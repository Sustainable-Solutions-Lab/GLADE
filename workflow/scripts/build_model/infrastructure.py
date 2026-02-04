# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Infrastructure setup for the food systems model.

This module handles the creation of carriers and buses that form the
foundation of the PyPSA network model.
"""

import pandas as pd
import pypsa

from .. import constants
from .utils import _carrier_unit_for_nutrient, _nutrient_kind


def add_carriers_and_buses(
    n: pypsa.Network,
    crop_list: list,
    food_list: list,
    residue_feed_items: list,
    food_group_list: list,
    nutrient_list: list,
    nutrient_units: dict[str, str],
    countries: list,
    regions: list,
    water_regions: list,
) -> None:
    """Add all carriers and their corresponding buses to the network.

    - Regional land buses remain per-region.
    - Crops, residues, foods, food groups, and macronutrients are created per-country.
    - Primary resources (water) and emissions (co2, ch4, n2o) use global buses.
    - Fertilizer has a global supply bus with per-country delivery buses.

    Bus names use ":" as delimiter: {type}:{specifier}:{scope}
    All buses have "country" and "region" columns (NaN when not applicable).
    """
    # Land carrier (class-level buses are added later)
    n.carriers.add("land", unit="Mha")

    # Crops per country
    if crop_list:
        idx = pd.MultiIndex.from_product(
            [countries, crop_list], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        crop_buses = ("crop:" + df["item"] + ":" + df["country"]).tolist()
        crop_carriers = ("crop_" + df["item"]).tolist()
        crop_countries = df["country"].tolist()
        crop_names = df["item"].tolist()
        n.carriers.add(sorted({f"crop_{crop}" for crop in crop_list}), unit="Mt")
        n.buses.add(
            crop_buses, carrier=crop_carriers, country=crop_countries, crop=crop_names
        )

    # Residues per country
    residue_items_sorted = sorted(dict.fromkeys(residue_feed_items))
    if residue_items_sorted:
        idx = pd.MultiIndex.from_product(
            [countries, residue_items_sorted], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        residue_buses = ("residue:" + df["item"] + ":" + df["country"]).tolist()
        residue_carriers = ("residue_" + df["item"]).tolist()
        residue_countries = df["country"].tolist()
        residue_names = df["item"].tolist()
        n.carriers.add(sorted(set(residue_carriers)), unit="Mt")
        n.buses.add(
            residue_buses,
            carrier=residue_carriers,
            country=residue_countries,
            residue=residue_names,
        )

    # Foods per country
    if food_list:
        idx = pd.MultiIndex.from_product(
            [countries, food_list], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        food_buses = ("food:" + df["item"] + ":" + df["country"]).tolist()
        food_carriers = ("food_" + df["item"]).tolist()
        food_countries = df["country"].tolist()
        food_names = df["item"].tolist()
        n.carriers.add(sorted({f"food_{food}" for food in food_list}), unit="Mt")
        n.buses.add(
            food_buses, carrier=food_carriers, country=food_countries, food=food_names
        )

    # Food groups per country
    if food_group_list:
        idx = pd.MultiIndex.from_product(
            [countries, food_group_list], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        group_buses = ("group:" + df["item"] + ":" + df["country"]).tolist()
        group_carriers = ("group_" + df["item"]).tolist()
        group_countries = df["country"].tolist()
        n.carriers.add(
            sorted({f"group_{group}" for group in food_group_list}),
            unit="Mt",
        )
        n.buses.add(group_buses, carrier=group_carriers, country=group_countries)

    # Macronutrients per country
    nutrient_list_sorted = sorted(dict.fromkeys(nutrient_list))
    new_nutrients = [
        nut for nut in nutrient_list_sorted if nut not in n.carriers.static.index
    ]
    if new_nutrients:
        carrier_units = [
            _carrier_unit_for_nutrient(nutrient_units[nut]) for nut in new_nutrients
        ]
        n.carriers.add(new_nutrients, unit=carrier_units)

    if nutrient_list_sorted:
        idx = pd.MultiIndex.from_product(
            [countries, nutrient_list_sorted], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        nutrient_buses = ("nutrient:" + df["item"] + ":" + df["country"]).tolist()
        nutrient_carriers = df["item"].tolist()
        nutrient_countries = df["country"].tolist()
        n.buses.add(
            nutrient_buses, carrier=nutrient_carriers, country=nutrient_countries
        )

    # Feed carriers per country (9 pools: 5 ruminant + 4 monogastric quality classes)
    feed_categories = constants.FEED_CATEGORIES
    if feed_categories:
        idx = pd.MultiIndex.from_product(
            [countries, feed_categories], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        feed_buses = ("feed:" + df["item"] + ":" + df["country"]).tolist()
        feed_carriers = ("feed_" + df["item"]).tolist()
        feed_countries = df["country"].tolist()
        n.carriers.add(sorted(set(feed_carriers)), unit="Mt")
        n.buses.add(feed_buses, carrier=feed_carriers, country=feed_countries)

    n.carriers.add("feed_conversion", unit="Mt")

    # Water carrier (buses added per region below)
    n.carriers.add("water", unit="Mm^3")

    # Global emission and resource carriers with buses
    for carrier, unit in [
        ("fertilizer", "Mt"),
        ("co2", "MtCO2"),
        ("ch4", "tCH4"),
        ("n2o", "tN2O"),
        ("ghg", "MtCO2e"),
    ]:
        n.carriers.add(carrier, unit=unit)
    # Add global emission buses and fertilizer supply bus
    n.buses.add(
        [
            "emission:co2",
            "emission:ch4",
            "emission:n2o",
            "emission:ghg",
            "fertilizer:supply",
        ],
        carrier=["co2", "ch4", "n2o", "ghg", "fertilizer"],
    )

    # Per-country fertilizer buses
    fert_country_buses = [f"fertilizer:{country}" for country in countries]
    n.buses.add(
        fert_country_buses,
        carrier="fertilizer",
        country=countries,
    )

    # Consolidate all carrier_unit_scale assignments
    scale_meta = n.meta.setdefault("carrier_unit_scale", {})
    for key in (
        "co2_t_to_Mt",
        "ch4_t_to_Mt",
        "ghg_t_to_Mt",
        "n2o_t_to_Mt",
        "fertilizer_t_to_Mt",
    ):
        scale_meta[key] = constants.TONNE_TO_MEGATONNE
    scale_meta["water_mm3_per_m3"] = constants.MM3_PER_M3
    if nutrient_list_sorted and any(
        _nutrient_kind(nutrient_units[nut]) == "energy" for nut in nutrient_list_sorted
    ):
        scale_meta["macronutrient_kcal_to_PJ"] = constants.KCAL_TO_PJ

    # Per-region water buses
    water_bus_names = [f"water:{region}" for region in water_regions]
    n.buses.add(water_bus_names, carrier="water", region=list(water_regions))
