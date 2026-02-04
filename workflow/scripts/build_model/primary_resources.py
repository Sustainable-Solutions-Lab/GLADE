# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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


def _add_land_slack_generators(
    n: pypsa.Network, bus_names: list[str], marginal_cost: float
) -> None:
    """Attach slack generators to the provided land buses."""

    if "land_slack" not in n.carriers.static.index:
        n.carriers.add("land_slack", unit="Mha")
    # Convention exception: parse bus names since only string identifiers are
    # available here (e.g., "land:cropland:usa_c1_r" -> "usa_c1_r")
    slack_names = [f"slack:land:{bus.split(':')[-1]}" for bus in bus_names]
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
    region_water_limits: pd.Series,
    ch4_to_co2_factor: float,
    n2o_to_co2_factor: float,
    use_actual_production: bool,
    water_slack_cost: float,
) -> None:
    """Add primary resource components and emissions bookkeeping.

    Note: GHG pricing is applied at solve time, not build time.
    """
    # Water stores use Mm^3, so convert m^3 limits accordingly.
    water_limits = region_water_limits * constants.MM3_PER_M3
    n.stores.add(
        "store:water:" + water_limits.index,
        bus="water:" + water_limits.index,
        carrier="water",
        e_nom=water_limits.values,
        e_initial=water_limits.values,
        e_nom_extendable=False,
        e_cyclic=False,
        region=water_limits.index,
    )

    # Slack in water limits when using actual (current) production
    if use_actual_production:
        n.generators.add(
            "slack:water:" + water_limits.index,
            bus="water:" + water_limits.index,
            carrier="water",
            marginal_cost=water_slack_cost,
            p_nom_extendable=True,
            region=water_limits.index,
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
    n.links.add(
        "aggregate:co2_to_ghg",
        bus0="emission:co2",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=1.0,
        p_min_pu=-1.0,  # allow negative emissions flow
        p_nom_extendable=True,
    )
    n.links.add(
        "aggregate:ch4_to_ghg",
        bus0="emission:ch4",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=ch4_to_co2_factor * constants.TONNE_TO_MEGATONNE,
        p_nom_extendable=True,
    )
    n.links.add(
        "aggregate:n2o_to_ghg",
        bus0="emission:n2o",
        bus1="emission:ghg",
        carrier="emission_aggregation",
        efficiency=n2o_to_co2_factor * constants.TONNE_TO_MEGATONNE,
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

    countries_idx = pd.Index(country_list)
    fertilizer_buses = ("fertilizer:" + countries_idx).tolist()
    names = ("distribute:fertilizer:" + countries_idx).tolist()
    params: dict[str, object] = {
        "bus0": "fertilizer:supply",
        "bus1": fertilizer_buses,
        "carrier": "fertilizer_distribution",
        "efficiency": 1.0,
        "p_nom_extendable": True,
        "country": country_list,
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
        emission_t_per_mt = emission_mt_per_mt * constants.MEGATONNE_TO_TONNE
        params["bus2"] = "emission:n2o"
        params["efficiency2"] = emission_t_per_mt

    n.links.add(names, **params)

    # Add extendable stores to absorb excess fertilizer (primarily manure nitrogen
    # from animal production when crop demand is insufficient)
    n.stores.add(
        ("store:fertilizer:" + countries_idx).tolist(),
        bus=fertilizer_buses,
        carrier="fertilizer",
        e_nom_extendable=True,
        country=country_list,
    )
