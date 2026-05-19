# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Biomass infrastructure and routing for the food systems model.

This module handles biomass exports to the energy sector, including
infrastructure setup and routing from crops and byproducts. Biomass
infrastructure is always present to provide a disposal route for
byproducts that lack feed mappings; set marginal_values_usd_per_tonne
to 0 for free disposal.
"""

from collections.abc import Iterable, Mapping, Sequence
import logging

import pandas as pd
import pypsa

from .. import constants

logger = logging.getLogger(__name__)


def add_biomass_infrastructure(
    n: pypsa.Network, countries: Iterable[str], biomass_cfg: Mapping[str, object]
) -> None:
    """Create biomass carrier, buses, and energy-sector sinks.

    Adds per-country biomass buses and "negative generators" that consume
    biomass at a configurable marginal cost. These sinks represent exports
    to the energy sector (e.g. biofuel production, power generation).

    This function only creates the base infrastructure; routing links from
    crops and byproducts to biomass buses are added by add_biomass_crop_links
    and add_biomass_byproduct_links.
    """

    # USD/tonne -> bnUSD/Mt: tonnes->Mt is 1e-6 (MEGATONNE_TO_TONNE), USD->bnUSD
    # is 1e-9, so per-Mt cost = USD_per_t * 1e6 * 1e-9 = 1e-3 * USD_per_t.
    marginal_cost = float(biomass_cfg["marginal_values_usd_per_tonne"])
    marginal_cost *= constants.MEGATONNE_TO_TONNE * constants.USD_TO_BNUSD
    # Biomass quantities are in Mt DM throughout this module.
    biomass_carrier = "biomass"
    n.carriers.add(biomass_carrier, unit="MtDM")

    country_list = list(countries)
    biomass_buses = [f"biomass:{country}" for country in country_list]
    n.buses.add(biomass_buses, carrier=biomass_carrier, country=country_list)

    n.generators.add(
        [f"sink:biomass:{country}" for country in country_list],
        bus=biomass_buses,
        carrier=biomass_carrier,
        p_nom_extendable=True,
        marginal_cost=marginal_cost,
        p_min_pu=-1,  # Allow consumption, not generation of biomass
        p_max_pu=0,
        country=country_list,
    )


def add_biomass_byproduct_links(
    n: pypsa.Network, countries: Iterable[str], byproducts: Iterable[str]
) -> None:
    """Allow food byproducts to be routed to biomass buses."""
    combos = pd.MultiIndex.from_product(
        [byproducts, countries], names=["item", "country"]
    ).to_frame(index=False)
    combos["bus0"] = "food:" + combos["item"] + ":" + combos["country"]
    combos["bus1"] = "biomass:" + combos["country"]
    bus_index = n.buses.static.index
    combos = combos[combos["bus0"].isin(bus_index) & combos["bus1"].isin(bus_index)]
    if combos.empty:
        return

    combos["name"] = "biomass:byproduct_" + combos["item"] + ":" + combos["country"]
    combos = combos.set_index("name")

    carrier = "biomass_byproduct"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")

    n.links.add(
        combos.index,
        bus0=combos["bus0"],
        bus1=combos["bus1"],
        carrier=carrier,
        p_nom_extendable=True,
        country=combos["country"],
        food=combos["item"],
    )


def add_biomass_disposal_links(
    n: pypsa.Network, countries: Iterable[str], foods: Iterable[str]
) -> None:
    """Allow human-consumed foods to be routed to biomass for disposal.

    Unlike ``add_biomass_byproduct_links`` (which routes items already excluded
    from human diets), this function targets foods that remain part of the diet
    but are jointly produced as forced co-products of other commodity demands
    (e.g. cottonseed-oil from cotton-fiber-driven cotton production). Without
    this route, the model can only dispose of surplus via food slack at the
    validation slack price, which both inflates the objective and biases the
    consumer-value duals on the diet-equality constraints.
    """
    combos = pd.MultiIndex.from_product(
        [foods, countries], names=["item", "country"]
    ).to_frame(index=False)
    combos["bus0"] = "food:" + combos["item"] + ":" + combos["country"]
    combos["bus1"] = "biomass:" + combos["country"]
    bus_index = n.buses.static.index
    combos = combos[combos["bus0"].isin(bus_index) & combos["bus1"].isin(bus_index)]
    if combos.empty:
        return

    combos["name"] = "biomass:disposal_" + combos["item"] + ":" + combos["country"]
    combos = combos.set_index("name")

    carrier = "biomass_disposal"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")

    n.links.add(
        combos.index,
        bus0=combos["bus0"],
        bus1=combos["bus1"],
        carrier=carrier,
        p_nom_extendable=True,
        country=combos["country"],
        food=combos["item"],
    )


def add_biomass_crop_links(
    n: pypsa.Network, countries: Iterable[str], crops: Iterable[str]
) -> None:
    """Route configured crops to biomass buses (dry-matter accounting)."""
    combos = pd.MultiIndex.from_product(
        [crops, countries], names=["crop", "country"]
    ).to_frame(index=False)
    combos["bus0"] = "crop:" + combos["crop"] + ":" + combos["country"]
    combos["bus1"] = "biomass:" + combos["country"]
    bus_index = n.buses.static.index
    combos = combos[combos["bus0"].isin(bus_index) & combos["bus1"].isin(bus_index)]
    if combos.empty:
        return

    combos["name"] = "biomass:crop_" + combos["crop"] + ":" + combos["country"]
    combos = combos.set_index("name")

    carrier = "biomass_crop"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")
    n.links.add(
        combos.index,
        bus0=combos["bus0"],
        bus1=combos["bus1"],
        carrier=carrier,
        p_nom_extendable=True,
        country=combos["country"],
        crop=combos["crop"],
    )


def add_biofuel_links(
    n: pypsa.Network,
    biofuel_baseline: pd.DataFrame,
) -> None:
    """Add biofuel/industrial demand links from food or crop buses to biomass.

    Most biofuel demand is routed via food buses. For grain/sugar crops,
    the food processing pathways in foods.csv handle the crop→food
    conversion and byproduct generation; this function only creates the
    final food→biomass link. For oil crops the same pattern applies.

    Biogas crop demand (e.g. silage maize) is routed directly from crop
    buses when the ``bus_type`` column is set to ``"crop"``.

    Each link is fixed at its baseline demand level: ``p_nom`` is set to
    the demand and ``p_min_pu = 1.0`` forces the flow to equal ``p_nom``.
    """
    carrier = "biofuel"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="Mt")

    bus_index = n.buses.static.index

    # Ensure bus_type column exists (default "food" for backward compatibility)
    if "bus_type" not in biofuel_baseline.columns:
        biofuel_baseline = biofuel_baseline.assign(bus_type="food")

    # Aggregate baseline demand by (source_item, crop, country, bus_type)
    grouped = biofuel_baseline.groupby(
        ["source_item", "crop", "country", "bus_type"], as_index=False
    )["demand_mt"].sum()

    names = []
    bus0s = []
    bus1s = []
    demands = []
    countries = []
    crops = []
    skipped = 0

    for _, row in grouped.iterrows():
        source_item = str(row["source_item"])
        crop = str(row["crop"])
        country = str(row["country"])
        bus_type = str(row["bus_type"])
        demand = float(row["demand_mt"])

        if demand <= 0:
            continue

        bus0 = f"{bus_type}:{source_item}:{country}"
        bus1 = f"biomass:{country}"

        if bus0 not in bus_index or bus1 not in bus_index:
            skipped += 1
            continue

        names.append(f"biofuel:{source_item}:{country}")
        bus0s.append(bus0)
        bus1s.append(bus1)
        demands.append(demand)
        countries.append(country)
        crops.append(crop)

    if not names:
        logger.warning("No biofuel links created (all buses missing)")
        return

    if skipped:
        logger.info("Skipped %d biofuel links due to missing buses", skipped)

    # Fix each link at its baseline demand: p_nom = demand, p_min_pu = 1.0
    # forces p == p_nom == demand. No solve-time constraint needed.
    n.links.add(
        names,
        bus0=bus0s,
        bus1=bus1s,
        carrier=carrier,
        efficiency=1.0,
        p_nom=demands,
        p_min_pu=1.0,
        country=countries,
        crop=crops,
    )

    logger.info(
        "Added %d biofuel links (%.1f Mt total baseline demand)",
        len(names),
        sum(demands),
    )


def add_fiber_demand_infrastructure(
    n: pypsa.Network,
    fiber_baseline: pd.DataFrame,
    countries: Sequence[str],
) -> None:
    """Add fiber demand buses, stores, and routing links.

    Creates per-country fiber infrastructure:
    - Fiber buses: ``fiber:{country}``
    - Extendable stores: ``store:fiber:{source_item}:{country}``
      with ``e_nom_min = demand``, ``e_min_pu = 1.0`` (enforces >= demand)
    - Routing links: ``fiber:{source_item}:{country}``
      from ``food:{source_item}:{country}`` to ``fiber:{country}``

    Stores are extendable with ``e_nom_min`` set to baseline demand so the
    optimizer must absorb at least ``demand`` Mt of each fiber item, but can
    freely absorb excess production (e.g. cotton grown for cottonseed oil).
    """
    carrier = "fiber_demand"
    n.carriers.add(carrier, unit="Mt")

    # Aggregate demand by (source_item, country), drop non-positive
    grouped = (
        fiber_baseline.groupby(["source_item", "country"], as_index=False)["demand_mt"]
        .sum()
        .query("demand_mt > 0")
        .copy()
    )

    # Filter to entries where the food bus exists
    bus_index = n.buses.static.index
    grouped["bus0"] = "food:" + grouped["source_item"] + ":" + grouped["country"]
    grouped = grouped[grouped["bus0"].isin(bus_index)]
    if grouped.empty:
        logger.warning("No fiber demand links created (all buses missing)")
        return

    # Use source_item + country as the natural index for aligned addition
    idx = pd.Index(
        "fiber:" + grouped["source_item"] + ":" + grouped["country"],
        name="name",
    )
    grouped = grouped.set_index(idx)

    # 1. Buses — one per country with positive demand
    fiber_countries = sorted(grouped["country"].unique())
    fiber_buses = pd.Index([f"fiber:{c}" for c in fiber_countries], name="name")
    n.buses.add(
        fiber_buses,
        carrier=carrier,
        country=fiber_countries,
    )

    # 2. Links — route food:{source_item}:{country} -> fiber:{country}
    fiber_bus1 = "fiber:" + grouped["country"]
    fiber_bus1.index = grouped.index
    n.links.add(
        grouped.index,
        bus0=grouped["bus0"],
        bus1=fiber_bus1,
        carrier=carrier,
        efficiency=1.0,
        p_nom_extendable=True,
        country=grouped["country"],
    )

    # 3. Stores — extendable with e_nom_min = demand enforces >= demand.
    #    e_min_pu=1.0 and e_max_pu=1.0 (default) force e == e_nom, and
    #    e_nom_min ensures e_nom >= demand. Excess is absorbed for free.
    stores = grouped[["source_item", "country", "demand_mt"]].copy()
    stores.index = pd.Index(
        "store:fiber:" + stores["source_item"] + ":" + stores["country"],
        name="name",
    )
    stores["bus"] = "fiber:" + stores["country"]
    n.stores.add(
        stores.index,
        bus=stores["bus"],
        carrier=carrier,
        e_nom_extendable=True,
        e_nom_min=stores["demand_mt"],
        e_min_pu=1.0,
        country=stores["country"],
    )

    logger.info(
        "Added %d fiber demand stores (%.1f Mt minimum demand, %d countries)",
        len(grouped),
        grouped["demand_mt"].sum(),
        len(fiber_countries),
    )
