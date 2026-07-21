# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Primary resources management for the food systems model.

This module handles land, water, and fertilizer resources, including
emissions bookkeeping for GHG, CO2, CH4, and N2O.
"""

from collections.abc import Iterable

import numpy as np
import pandas as pd
import pypsa

from .. import constants

# Merit-order regularizer for the tiered water supply (bn USD per Mm^3, per unit
# characterisation factor). Breaks the unpriced tier-choice degeneracy by making
# low-CF water marginally cheaper to draw. Sized to be economically negligible
# (equivalent to a ~1e-5 USD/m3-world-eq water price, orders of magnitude below
# the swept water-scarcity prices) yet above solver optimality tolerance.
WATER_MERIT_ORDER_EPSILON = 1e-8


def _retain_material_water_capacities(
    capacities: pd.DataFrame, min_capacity_mm3: float
) -> pd.DataFrame:
    """Drop supply bands smaller than the configured physical capacity floor."""
    if min_capacity_mm3 == 0:
        return capacities
    return capacities[capacities["capacity_mm3"] >= min_capacity_mm3].copy()


def _add_land_slack_generators(
    n: pypsa.Network, bus_names: list[str], marginal_cost: float
) -> None:
    """Attach slack generators to the provided land buses."""

    if "land_slack" not in n.carriers.static.index:
        n.carriers.add("land_slack", unit="Mha")
    # Use bus carrier attribute to namespace slack names (avoids collisions between
    # e.g. land:pasture:X and land:existing_grassland_{type}:X). Still parse suffix
    # since buses don't have a dedicated attribute for the region/class identifier.
    bus_carriers = n.buses.static.loc[bus_names, "carrier"]
    slack_names = [
        f"slack:{carrier}:{bus.split(':')[-1]}"
        for bus, carrier in zip(bus_names, bus_carriers)
    ]
    n.generators.add(
        slack_names,
        bus=bus_names,
        carrier="land_slack",
        p_nom_extendable=True,
        marginal_cost=marginal_cost,
    )


def add_primary_resources(
    n: pypsa.Network,
    fertilizer_config: dict,
    water_tiers: pd.DataFrame,
    groundwater_bands: pd.DataFrame,
    water_regions: Iterable[str],
    water_periods: int,
    ch4_to_co2_factor: float,
    n2o_to_co2_factor: float,
    use_actual_production: bool,
    water_slack_cost: float,
    groundwater_pumping_cost_usd_per_m3: float,
    min_water_capacity_mm3: float,
) -> None:
    """Add primary resource components and emissions bookkeeping.

    **Surface water** is supplied through a convex tiered curve: a single free
    global source (``water:source``) feeds per-region, per-period, per-tier links
    that deliver water to ``water:{region}:p{period}`` (efficiency 1) and
    accumulate water scarcity on ``impact:water_scarcity`` at each tier's marginal
    characterisation factor (``efficiency2``). The sum of a region-period's tier
    capacities is its per-period availability cap (a period's surface draw cannot
    exceed it, so a seasonal shortfall must draw groundwater). Capacities are
    already in Mm^3.

    **Groundwater** (``groundwater_bands``, groundwater mode only) is instead an
    *annual* per-region resource: an aquifer integrates recharge over the year
    and can be pumped in any period. Each region gets a ``groundwater:{region}``
    bus fed by two supply links from ``water:source`` -- renewable (WaterGAP
    volume, scarcity-priced at the region's scarcest surface CF, tallied on
    ``impact:groundwater_renewable``) and non-renewable (a generous ceiling, mined
    volume on ``impact:groundwater_depletion``, ordered last by its pumping cost)
    -- and free ``groundwater_delivery`` links distribute it to every period bus.
    The annual cap therefore lets a dry period draw the whole year's recharge,
    unlike surface which is period-bound.

    Because the water itself is free, the unpriced LP is indifferent to which
    tier it draws from within a region and lands on an arbitrary (high-CF) mix.
    A negligible merit-order regularizer (``marginal_cost = WATER_MERIT_ORDER_
    EPSILON * marginal_cf``) breaks this degeneracy so the LP always draws
    low-scarcity water first within each region, independent of any solve-time
    water-scarcity price. The cost is economically negligible (equivalent to a
    water price ~1e-5 USD/m3-world-eq, far below the swept prices) but well above
    solver tolerance, so it only resolves the tie and does not distort trade-offs.

    Note: GHG and water-scarcity pricing are applied at solve time, not build time.
    """
    water_region_list = list(water_regions)

    # Global free water source feeding the tiered regional supply links.
    n.carriers.add("water_supply", unit="Mm^3")
    n.generators.add(
        "supply:water_source",
        bus="water:source",
        carrier="water_source",
        p_nom_extendable=True,
    )

    tiers = water_tiers[water_tiers["region"].isin(water_region_list)]
    tiers = _retain_material_water_capacities(tiers, min_water_capacity_mm3)
    period_str = tiers["period"].astype(int).astype(str)
    tier_names = (
        "supply:water:"
        + tiers["region"]
        + ":p"
        + period_str
        + ":t"
        + tiers["tier"].astype(str)
    ).to_numpy()
    # Each tier is surface water, renewable groundwater, or non-renewable
    # groundwater. Surface and renewable groundwater both accumulate AWARE
    # scarcity on impact:water_scarcity at their marginal CF (renewable
    # groundwater is part of the renewable blue-water system AWARE's AMD
    # counts) and carry the negligible merit-order regularizer (cost
    # proportional to CF) so the unpriced LP draws low-CF water first;
    # renewable groundwater additionally tallies its drawn volume 1:1 on
    # impact:groundwater_renewable (bus3) as a hook for future policy.
    # Non-renewable groundwater accumulates mined volume 1:1 on
    # impact:groundwater_depletion instead of a CF, and carries a small real
    # pumping cost that both adds realism and orders it last in the merit
    # order (drawn only once renewable water in the region is exhausted).
    source = tiers["source"].to_numpy()
    is_nonrenewable = source == "groundwater_nonrenewable"
    is_renewable_gw = source == "groundwater_renewable"
    marginal_cf = tiers["marginal_cf"].to_numpy()
    pumping_cost_bnusd_per_mm3 = (
        groundwater_pumping_cost_usd_per_m3
        / constants.MM3_PER_M3
        * constants.USD_TO_BNUSD
    )
    n.links.add(
        tier_names,
        bus0="water:source",
        bus1=("water:" + tiers["region"] + ":p" + period_str).to_numpy(),
        bus2=np.where(
            is_nonrenewable, "impact:groundwater_depletion", "impact:water_scarcity"
        ),
        bus3=np.where(is_renewable_gw, "impact:groundwater_renewable", ""),
        carrier="water_supply",
        efficiency=1.0,
        efficiency2=np.where(is_nonrenewable, 1.0, marginal_cf),
        efficiency3=np.where(is_renewable_gw, 1.0, 0.0),
        marginal_cost=np.where(
            is_nonrenewable,
            pumping_cost_bnusd_per_mm3,
            WATER_MERIT_ORDER_EPSILON * marginal_cf,
        ),
        p_nom=tiers["capacity_mm3"].to_numpy(),
        p_nom_extendable=False,
        region=tiers["region"].to_numpy(),
        period=tiers["period"].astype(int).to_numpy(),
        source=source,
    )

    # Accumulating impact stores: renewable water scarcity and non-renewable
    # groundwater depletion (both priced/capped at solve time), and the
    # renewable-groundwater volume tally (reporting/future policy only).
    n.stores.add(
        "store:impact:water_scarcity",
        bus="impact:water_scarcity",
        carrier="water_scarcity",
        e_nom_extendable=True,
    )
    n.stores.add(
        "store:impact:groundwater_depletion",
        bus="impact:groundwater_depletion",
        carrier="groundwater_depletion",
        e_nom_extendable=True,
    )
    n.stores.add(
        "store:impact:groundwater_renewable",
        bus="impact:groundwater_renewable",
        carrier="groundwater_renewable",
        e_nom_extendable=True,
    )

    # Annual per-region groundwater (aquifer). Each region's groundwater:{region}
    # bus is fed by renewable and non-renewable supply links (annual caps, same
    # impact wiring as the surface tiers) and distributed to every period water
    # bus by free delivery links, so the year's recharge is shared across periods
    # (a dry period can draw all of it). Empty bands (non-groundwater modes) skip
    # this entirely.
    gw = groundwater_bands[groundwater_bands["region"].isin(water_region_list)]
    gw = _retain_material_water_capacities(gw, min_water_capacity_mm3)
    if not gw.empty:
        n.carriers.add("groundwater", unit="Mm^3")
        n.carriers.add("groundwater_delivery", unit="Mm^3")
        gw_regions = sorted(gw["region"].unique())
        n.buses.add(
            ["groundwater:" + r for r in gw_regions],
            carrier="groundwater",
            region=gw_regions,
        )

        gw_nonrenew = (gw["source"] == "groundwater_nonrenewable").to_numpy()
        gw_cf = gw["marginal_cf"].to_numpy()
        n.links.add(
            ("supply:groundwater:" + gw["region"] + ":" + gw["source"]).to_numpy(),
            bus0="water:source",
            bus1=("groundwater:" + gw["region"]).to_numpy(),
            bus2=np.where(
                gw_nonrenew, "impact:groundwater_depletion", "impact:water_scarcity"
            ),
            bus3=np.where(gw_nonrenew, "", "impact:groundwater_renewable"),
            carrier="water_supply",
            efficiency=1.0,
            efficiency2=np.where(gw_nonrenew, 1.0, gw_cf),
            efficiency3=np.where(gw_nonrenew, 0.0, 1.0),
            marginal_cost=np.where(
                gw_nonrenew,
                pumping_cost_bnusd_per_mm3,
                WATER_MERIT_ORDER_EPSILON * gw_cf,
            ),
            p_nom=gw["capacity_mm3"].to_numpy(),
            p_nom_extendable=False,
            region=gw["region"].to_numpy(),
            period=-1,
            source=gw["source"].to_numpy(),
        )

        n_periods = int(water_periods)
        deliver_region = pd.Series(np.repeat(gw_regions, n_periods))
        deliver_period = pd.Series(
            np.tile(np.arange(n_periods), len(gw_regions))
        ).astype(str)
        deliver_suffix = deliver_region.astype(str) + ":p" + deliver_period
        n.links.add(
            ("deliver:groundwater:" + deliver_suffix).to_numpy(),
            bus0=("groundwater:" + deliver_region.astype(str)).to_numpy(),
            bus1=("water:" + deliver_suffix).to_numpy(),
            carrier="groundwater_delivery",
            efficiency=1.0,
            p_nom_extendable=True,
            region=deliver_region.to_numpy(),
        )

    # Slack in water limits when using actual (current) production. One slack
    # generator per region-period water bus, so a fixed-area baseline stays
    # feasible even where a single period's supply cannot meet its demand.
    if use_actual_production and water_region_list:
        n_periods = int(water_periods)
        slack_regions = pd.Series(np.repeat(water_region_list, n_periods))
        slack_period = pd.Series(
            np.tile(np.arange(n_periods), len(water_region_list))
        ).astype(str)
        slack_bus = pd.Index(
            "water:" + slack_regions.astype(str) + ":p" + slack_period, dtype="object"
        )
        n.generators.add(
            "slack:" + slack_bus,
            bus=slack_bus,
            carrier="water",
            marginal_cost=water_slack_cost,
            p_nom_extendable=True,
            region=slack_regions.to_numpy(),
        )

    scale_meta = n.meta.setdefault("carrier_unit_scale", {})
    scale_meta["water_mm3_per_m3"] = constants.MM3_PER_M3

    # Fertilizer remains global (no regionalization yet)
    limit_mt = float(fertilizer_config["limit"]) * constants.TONNE_TO_MEGATONNE
    marginal_cost_bnusd_per_mt = (
        float(fertilizer_config["marginal_cost_usd_per_tonne"])
        * constants.MEGATONNE_TO_TONNE
        * constants.USD_TO_BNUSD
    )
    n.generators.add(
        "supply:fertilizer",
        bus="fertilizer:supply",
        carrier="fertilizer",
        p_nom_extendable=True,
        p_nom_max=limit_mt,
        marginal_cost=marginal_cost_bnusd_per_mt,
    )

    # Add GHG aggregation store and links from individual gases
    # Note: GHG pricing is applied at solve time, not build time
    n.carriers.add("emission_aggregation", unit="MtCO2e")
    n.stores.add(
        "store:emission:ghg",
        bus="emission:ghg",
        carrier="ghg",
        e_nom_extendable=True,
        e_nom_min=-np.inf,
        e_min_pu=-1.0,
    )
    # CO2 aggregator allows negative flow so spare-land sequestration
    # credits (negative efficiency on emission:co2 from spare_land links)
    # can propagate to emission:ghg. CH4 and N2O have no sequestration
    # mechanism in this model - all source links write non-negative
    # efficiencies (manure CH4, enteric CH4, manure / synthetic N2O) -
    # so the default p_min_pu=0 is correct for the gas-specific
    # aggregators.
    n.links.add(
        "aggregate:co2_to_ghg",
        bus0="emission:co2",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=1.0,
        p_min_pu=-1.0,
        p_nom_extendable=True,
    )
    n.links.add(
        "aggregate:ch4_to_ghg",
        bus0="emission:ch4",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=ch4_to_co2_factor * constants.KILOTONNE_TO_MEGATONNE,
        p_nom_extendable=True,
    )
    n.links.add(
        "aggregate:n2o_to_ghg",
        bus0="emission:n2o",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=n2o_to_co2_factor * constants.KILOTONNE_TO_MEGATONNE,
        p_nom_extendable=True,
    )


def add_fertilizer_distribution_links(
    n: pypsa.Network,
    countries: Iterable[str],
    synthetic_n2o_factor: float,
    indirect_ef4: float,
    indirect_ef5: float,
    frac_gasf: float,
    frac_leach: float,
) -> None:
    """Connect the global fertilizer supply bus to country-level fertilizer buses.

    Includes direct and indirect N₂O emissions from synthetic fertilizer following
    IPCC 2019 Refinement methodology (Chapter 11, Equations 11.1, 11.9, 11.10).

    Also adds extendable stores at each country's fertilizer bus to absorb excess
    manure nitrogen when crop demand is insufficient.
    """

    country_list = list(countries)
    if not country_list:
        return

    n.carriers.add("fertilizer_distribution", unit="Mt")

    countries_idx = pd.Index(country_list, dtype="object")
    names = pd.Index("distribute:fertilizer:" + countries_idx, dtype="object")
    link_df = pd.DataFrame(index=names)
    link_df["country"] = countries_idx.to_numpy()
    link_df["bus1"] = ("fertilizer:" + countries_idx).to_numpy()
    params: dict[str, object] = {
        "bus0": "fertilizer:supply",
        "bus1": link_df["bus1"],
        "carrier": "fertilizer_distribution",
        "efficiency": 1.0,
        "p_nom_extendable": True,
        "country": link_df["country"],
    }

    # Calculate total N2O emissions (direct + indirect)
    # Direct N2O (Equation 11.1)
    direct_n2o_n = float(synthetic_n2o_factor)

    # Indirect N2O from volatilization (Equation 11.9)
    indirect_vol_n2o_n = frac_gasf * indirect_ef4

    # Indirect N2O from leaching (Equation 11.10)
    indirect_leach_n2o_n = frac_leach * indirect_ef5

    # Total N2O-N per kg N applied, converted to N2O
    total_n2o_n = direct_n2o_n + indirect_vol_n2o_n + indirect_leach_n2o_n
    emission_mt_per_mt = total_n2o_n * constants.N2O_N_TO_N2O

    if emission_mt_per_mt > 0.0:
        emission_kt_per_mt = emission_mt_per_mt * constants.MEGATONNE_TO_KILOTONNE
        params["bus2"] = "emission:n2o"
        params["efficiency2"] = emission_kt_per_mt

    n.links.add(link_df.index, **params)

    # Add extendable stores to absorb excess fertilizer (primarily manure nitrogen
    # from animal production when crop demand is insufficient)
    store_names = pd.Index("store:fertilizer:" + countries_idx, dtype="object")
    store_df = pd.DataFrame(index=store_names)
    store_df["bus"] = ("fertilizer:" + countries_idx).to_numpy()
    store_df["country"] = countries_idx.to_numpy()
    n.stores.add(
        store_df.index,
        bus=store_df["bus"],
        carrier="fertilizer",
        e_nom_extendable=True,
        country=store_df["country"],
    )
