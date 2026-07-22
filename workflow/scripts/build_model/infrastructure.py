# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Infrastructure setup for the food systems model.

This module handles the creation of carriers and buses that form the
foundation of the PyPSA network model.
"""

import numpy as np
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
    water_periods: int,
    food_basis: dict[str, str],
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
        crop_buses = pd.Index(
            "crop:" + df["item"] + ":" + df["country"], dtype="object"
        )
        crop_df = pd.DataFrame(index=crop_buses)
        crop_df["carrier"] = ("crop_" + df["item"]).to_numpy()
        crop_df["country"] = df["country"].to_numpy()
        crop_df["crop"] = df["item"].to_numpy()
        n.carriers.add(sorted({f"crop_{crop}" for crop in crop_list}), unit="Mt")
        n.buses.add(
            crop_df.index,
            carrier=crop_df["carrier"],
            country=crop_df["country"],
            crop=crop_df["crop"],
        )

    # Residues per country
    residue_items_sorted = sorted(dict.fromkeys(residue_feed_items))
    if residue_items_sorted:
        idx = pd.MultiIndex.from_product(
            [countries, residue_items_sorted], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        residue_buses = pd.Index(
            "residue:" + df["item"] + ":" + df["country"], dtype="object"
        )
        residue_df = pd.DataFrame(index=residue_buses)
        residue_df["carrier"] = ("residue_" + df["item"]).to_numpy()
        residue_df["country"] = df["country"].to_numpy()
        residue_df["residue"] = df["item"].to_numpy()
        n.carriers.add(sorted(set(residue_df["carrier"])), unit="Mt")
        n.buses.add(
            residue_df.index,
            carrier=residue_df["carrier"],
            country=residue_df["country"],
            residue=residue_df["residue"],
        )

    # Foods per country
    if food_list:
        idx = pd.MultiIndex.from_product(
            [countries, food_list], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        food_buses = pd.Index(
            "food:" + df["item"] + ":" + df["country"], dtype="object"
        )
        food_df = pd.DataFrame(index=food_buses)
        food_df["carrier"] = ("food_" + df["item"]).to_numpy()
        food_df["country"] = df["country"].to_numpy()
        food_df["food"] = df["item"].to_numpy()
        # Annotate each food bus with its native mass basis ("dry" or
        # "fresh") so downstream analysis can do consistency checks
        # without re-loading food_basis.csv.
        missing = set(food_list) - set(food_basis)
        if missing:
            raise ValueError(
                f"food_basis missing for foods {sorted(missing)}; "
                "extend data/curated/food_basis.csv."
            )
        food_df["basis"] = df["item"].map(food_basis).to_numpy()
        n.carriers.add(sorted({f"food_{food}" for food in food_list}), unit="Mt")
        n.buses.add(
            food_df.index,
            carrier=food_df["carrier"],
            country=food_df["country"],
            food=food_df["food"],
            basis=food_df["basis"],
        )

    # Food groups per country
    if food_group_list:
        idx = pd.MultiIndex.from_product(
            [countries, food_group_list], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        group_buses = pd.Index(
            "group:" + df["item"] + ":" + df["country"], dtype="object"
        )
        group_df = pd.DataFrame(index=group_buses)
        group_df["carrier"] = ("group_" + df["item"]).to_numpy()
        group_df["country"] = df["country"].to_numpy()
        n.carriers.add(
            sorted({f"group_{group}" for group in food_group_list}),
            unit="Mt",
        )
        n.buses.add(
            group_df.index,
            carrier=group_df["carrier"],
            country=group_df["country"],
        )

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
        nutrient_buses = pd.Index(
            "nutrient:" + df["item"] + ":" + df["country"], dtype="object"
        )
        nutrient_df = pd.DataFrame(index=nutrient_buses)
        nutrient_df["carrier"] = df["item"].to_numpy()
        nutrient_df["country"] = df["country"].to_numpy()
        n.buses.add(
            nutrient_df.index,
            carrier=nutrient_df["carrier"],
            country=nutrient_df["country"],
        )

    # Feed carriers per country (7 pools: 4 ruminant + 3 monogastric quality classes;
    # see constants.FEED_CATEGORIES for the canonical list).
    feed_categories = constants.FEED_CATEGORIES
    if feed_categories:
        idx = pd.MultiIndex.from_product(
            [countries, feed_categories], names=["country", "item"]
        )
        df = idx.to_frame(index=False)
        feed_buses = pd.Index(
            "feed:" + df["item"] + ":" + df["country"], dtype="object"
        )
        feed_df = pd.DataFrame(index=feed_buses)
        feed_df["carrier"] = ("feed_" + df["item"]).to_numpy()
        feed_df["country"] = df["country"].to_numpy()
        feed_df["feed_category"] = df["item"].to_numpy()
        n.carriers.add(sorted(set(feed_df["carrier"])), unit="Mt")
        n.buses.add(
            feed_df.index,
            carrier=feed_df["carrier"],
            country=feed_df["country"],
            feed_category=feed_df["feed_category"],
        )

    n.carriers.add("feed_conversion", unit="Mt")

    # Water carrier (buses added per region below). The global water source feeds
    # the tiered regional supply links; water scarcity accumulates on a global bus.
    n.carriers.add("water", unit="Mm^3")
    n.carriers.add("water_field", unit="Mm^3")
    n.carriers.add("water_source", unit="Mm^3")
    n.carriers.add("water_scarcity", unit="Mm^3 world-eq")
    n.carriers.add("groundwater_depletion", unit="Mm^3 mined")
    n.carriers.add("groundwater_renewable", unit="Mm^3")

    # Global emission and resource carriers with buses
    for carrier, unit in [
        ("fertilizer", "Mt"),
        ("co2", "MtCO2"),
        ("ch4", "ktCH4"),
        ("n2o", "ktN2O"),
        ("ghg", "MtCO2e"),
    ]:
        n.carriers.add(carrier, unit=unit)
    # Add global emission buses, fertilizer supply bus, water source and water
    # scarcity buses
    n.buses.add(
        [
            "emission:co2",
            "emission:ch4",
            "emission:n2o",
            "emission:ghg",
            "fertilizer:supply",
            "water:source",
            "impact:water_scarcity",
            "impact:groundwater_depletion",
            "impact:groundwater_renewable",
        ],
        carrier=[
            "co2",
            "ch4",
            "n2o",
            "ghg",
            "fertilizer",
            "water_source",
            "water_scarcity",
            "groundwater_depletion",
            "groundwater_renewable",
        ],
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

    # Per-region, per-period water buses: the consumption-basis pool (tiered
    # supply, scarcity, groundwater bands) and the field bus crops draw their
    # beneficial ET from, bridged by the irrigation delivery link. The year is
    # split into ``water_periods`` equal periods (water.temporal_resolution) so
    # a period's surface availability binds against the crop demand whose growing
    # season falls in it; a seasonal shortfall then draws groundwater.
    n_periods = int(water_periods)
    region_arr = np.array(list(water_regions), dtype=object)
    region_col = pd.Series(np.repeat(region_arr, n_periods))
    period_col = pd.Series(np.tile(np.arange(n_periods), len(region_arr))).astype(str)
    suffix = region_col.astype(str) + ":p" + period_col
    n.buses.add(
        ("water:" + suffix).to_numpy(), carrier="water", region=region_col.to_numpy()
    )
    n.buses.add(
        ("water_field:" + suffix).to_numpy(),
        carrier="water_field",
        region=region_col.to_numpy(),
    )
