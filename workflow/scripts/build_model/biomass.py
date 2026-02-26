# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Biomass infrastructure and routing for the food systems model.

This module handles biomass exports to the energy sector, including
infrastructure setup and routing from crops and byproducts. Biomass
infrastructure is always present to provide a disposal route for
byproducts that lack feed mappings; set marginal_values_usd_per_tonne
to 0 for free disposal.
"""

from collections.abc import Iterable, Mapping
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

    marginal_cost = float(biomass_cfg["marginal_values_usd_per_tonne"])
    marginal_cost *= constants.USD_TO_BNUSD / constants.TONNE_TO_MEGATONNE
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
    """Add biofuel/industrial demand links from food buses to biomass.

    All biofuel demand is routed via food buses. For grain/sugar crops,
    the food processing pathways in foods.csv handle the crop→food
    conversion and byproduct generation; this function only creates the
    final food→biomass link. For oil crops the same pattern applies.

    Each link stores its baseline demand in the ``baseline_demand_mt``
    column for use by solve-time constraints.
    """
    carrier = "biofuel"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="Mt")

    bus_index = n.buses.static.index

    # Aggregate baseline demand by (source_item, crop, country)
    grouped = biofuel_baseline.groupby(
        ["source_item", "crop", "country"], as_index=False
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
        demand = float(row["demand_mt"])

        if demand <= 0:
            continue

        bus0 = f"food:{source_item}:{country}"
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

    n.links.add(
        names,
        bus0=bus0s,
        bus1=bus1s,
        carrier=carrier,
        efficiency=1.0,
        p_nom_extendable=True,
        country=countries,
        crop=crops,
        baseline_demand_mt=demands,
    )

    logger.info(
        "Added %d biofuel links (%.1f Mt total baseline demand)",
        len(names),
        sum(demands),
    )
