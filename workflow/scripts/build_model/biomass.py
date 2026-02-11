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

import pandas as pd
import pypsa

from .. import constants


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
