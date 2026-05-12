# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Food conversion and feed supply for the food systems model.

This module handles the conversion of crops to food items through
processing pathways, and the routing of crops and foods to animal
feed categories.
"""

import logging

import pandas as pd
import pypsa

from .. import constants

logger = logging.getLogger(__name__)

_LOW_PROCESSING_COST = 0.01 * constants.USD_TO_BNUSD / constants.TONNE_TO_MEGATONNE


def add_food_conversion_links(
    n: pypsa.Network,
    food_list: list,
    foods: pd.DataFrame,
    countries: list,
    crop_to_fresh_factor: dict[str, float],
    food_to_group: dict[str, str],
    crop_list: list,
    byproduct_list: list,
) -> None:
    """Add links for converting crops to foods via processing pathways.

    Pathways can have multiple outputs (e.g., wheat → white flour + bran).
    Each pathway creates one multi-output Link per country.
    Only processes crops that are in the configured crop_list.
    Foods flagged as byproducts are ignored when checking for food-group mappings.

    Food loss and waste is *not* applied here; it lives on the consumer
    side (``add_food_nutrition_links``). The food bus therefore carries
    supply-basis mass (edible-portion fresh equivalent of the crop input),
    pre-consumer-FLW.
    """

    # Filter foods DataFrame to only include configured crops and foods.
    foods = foods[foods["crop"].isin(crop_list) & foods["food"].isin(food_list)].copy()

    # Add food_processing carrier
    if "food_processing" not in n.carriers.static.index:
        n.carriers.add("food_processing", unit="Mt")

    missing_group_foods: set[str] = set()
    byproduct_foods: set[str] = set(byproduct_list or [])
    invalid_pathways: list[str] = []

    normalized_countries = [str(c).upper() for c in countries]
    countries_index = pd.Index(normalized_countries, dtype="object")

    batched_frames = {}

    # Group foods by pathway and crop
    pathway_groups = foods.groupby(["pathway", "crop"])

    for (pathway, crop), pathway_df in pathway_groups:
        pathway = str(pathway).strip()
        crop = str(crop).strip()

        output_rows = pathway_df[["food", "factor"]].copy()
        output_rows["food"] = output_rows["food"].astype(str)
        output_rows["factor"] = output_rows["factor"].astype(float)
        n_outputs = len(output_rows.index)
        if n_outputs == 0:
            continue

        # Verify mass balance (sum of factors should be ≤ 1.0)
        total_factor = output_rows["factor"].sum()
        if total_factor > 1.01:  # Allow small rounding tolerance
            invalid_pathways.append(f"{pathway} ({crop}): sum={total_factor:.3f}")

        # Per-crop factor that translates dry-matter crop bus into food bus
        # mass. ``inverse_moisture`` crops apply 1/(1-moisture) so the food
        # bus is in commercial commodity weight; ``identity`` crops (only
        # tea today) leave it as dry matter because their moisture entry
        # refers to the as-harvested form. The policy is encoded in
        # crop_moisture_content.csv and baked into ``crop_to_fresh_factor``
        # by ``utils._fresh_mass_conversion_factors`` so this loop does not
        # need to special-case any crop.
        conversion_factor = crop_to_fresh_factor[crop]

        names = pd.Index("pathway:" + pathway + ":" + countries_index, dtype="object")
        link_df = pd.DataFrame(index=names)
        link_df["bus0"] = "crop:" + crop + ":" + countries_index
        link_df["country"] = countries_index
        link_df["crop"] = crop
        link_df["pathway"] = pathway

        # Add each output food as a separate bus with its efficiency.
        # No country-specific factor on processing: FLW is applied on the
        # consumption side.
        for output_idx, row in enumerate(output_rows.itertuples(index=False), start=1):
            food = row.food
            factor = row.factor
            bus_key = f"bus{output_idx}"
            eff_key = "efficiency" if output_idx == 1 else f"efficiency{output_idx}"

            link_df[bus_key] = "food:" + food + ":" + countries_index

            group = food_to_group.get(food)
            if (group is None or pd.isna(group)) and food not in byproduct_foods:
                missing_group_foods.add(food)
            link_df[eff_key] = factor * conversion_factor

        batched_frames.setdefault(n_outputs, []).append(link_df)

    for n_outputs, frames in batched_frames.items():
        all_df = pd.concat(frames, axis=0)
        link_params = {
            "bus0": all_df["bus0"],
            "carrier": "food_processing",
            "marginal_cost": _LOW_PROCESSING_COST,
            "p_nom_extendable": True,
            "country": all_df["country"],
            "crop": all_df["crop"],
            "pathway": all_df["pathway"],
        }
        for output_idx in range(1, n_outputs + 1):
            bus_key = f"bus{output_idx}"
            eff_key = "efficiency" if output_idx == 1 else f"efficiency{output_idx}"
            link_params[bus_key] = all_df[bus_key]
            link_params[eff_key] = all_df[eff_key]

        n.links.add(all_df.index, **link_params)

    # Warnings
    if invalid_pathways:
        logger.warning(
            "Pathways with mass balance issues (sum of factors > 1.0): %s",
            "; ".join(invalid_pathways[:5]),
        )

    if missing_group_foods:
        logger.warning(
            "Food items without food-group mapping (consumer FLW will not apply): %s",
            ", ".join(sorted(missing_group_foods)),
        )


def _filter_feed_mapping(mapping, crop_list, food_list, residue_items):
    return mapping[
        ((mapping["source_type"] == "crop") & mapping["feed_item"].isin(crop_list))
        | ((mapping["source_type"] == "food") & mapping["feed_item"].isin(food_list))
        | (
            (mapping["source_type"] == "residue")
            & mapping["feed_item"].isin(residue_items)
        )
    ].copy()


def add_feed_supply_links(
    n: pypsa.Network,
    ruminant_categories: pd.DataFrame,
    ruminant_mapping: pd.DataFrame,
    monogastric_categories: pd.DataFrame,
    monogastric_mapping: pd.DataFrame,
    crop_list: list,
    food_list: list,
    residue_items: list,
    countries: list,
) -> None:
    """Add links converting crops and foods into categorized feed pools.

    Uses pre-computed feed categories and mappings to route items to appropriate
    feed pools (4 ruminant + 4 monogastric quality classes).

    Each row of the feed mapping creates one feed_conversion link with
    ``efficiency = share`` (default 1.0). Items with multi-category
    splits (e.g. DDGS configured to act as 70% grain + 30% protein for
    monogastric) appear as multiple rows whose shares sum to 1.0; the
    resulting set of links carries the input mass through to the right
    feed buses with mass conserved in aggregate.
    """
    # Process ruminant feeds
    ruminant_feeds = _filter_feed_mapping(
        ruminant_mapping, crop_list, food_list, residue_items
    )

    # Process monogastric feeds
    monogastric_feeds = _filter_feed_mapping(
        monogastric_mapping, crop_list, food_list, residue_items
    )

    # Feed buses are expressed in tonnes of dry matter intake (tDM).
    # Conversion links therefore use efficiency=1.0; digestibility is accounted
    # for downstream in feed-to-animal efficiencies and emissions.

    # Concatenate ruminant + monogastric feeds with animal_type column
    ruminant_feeds["animal_type"] = "ruminant"
    monogastric_feeds["animal_type"] = "monogastric"
    all_feeds = pd.concat([ruminant_feeds, monogastric_feeds], ignore_index=True)

    if all_feeds.empty:
        logger.info("No feed supply links to create; check crop/food lists")
        return

    # Derive bus/link prefixes and crop values vectorized
    source_type_map = {
        "crop": ("crop", "convert"),
        "food": ("food", "convert_food"),
        "residue": ("residue", "convert_residue"),
    }
    all_feeds["bus_prefix"] = all_feeds["source_type"].map(
        lambda s: source_type_map[s][0]
    )
    all_feeds["link_prefix"] = all_feeds["source_type"].map(
        lambda s: source_type_map[s][1]
    )
    all_feeds["crop_value"] = all_feeds.apply(
        lambda r: r["feed_item"] if r["source_type"] == "crop" else pd.NA, axis=1
    )

    # Cross-merge with countries
    countries_df = pd.DataFrame({"country": countries})
    expanded = all_feeds.merge(countries_df, how="cross")
    if expanded.empty:
        logger.info("No feed supply links to create; check crop/food lists")
        return

    # Build all name/bus columns with vectorized string ops
    feed_cat = expanded["animal_type"] + "_" + expanded["category"]
    names = pd.Index(
        expanded["link_prefix"]
        + ":"
        + expanded["feed_item"]
        + "_to_"
        + feed_cat
        + ":"
        + expanded["country"],
        dtype="object",
    )
    expanded = expanded.set_index(names, drop=False)
    expanded["bus0"] = (
        expanded["bus_prefix"] + ":" + expanded["feed_item"] + ":" + expanded["country"]
    )
    expanded["feed_category_value"] = (
        expanded["animal_type"] + "_" + expanded["category"]
    )
    expanded["bus1"] = (
        "feed:" + expanded["feed_category_value"] + ":" + expanded["country"]
    )

    # Add feed_conversion carrier
    if "feed_conversion" not in n.carriers.static.index:
        n.carriers.add("feed_conversion", unit="Mt")

    # Link efficiency = mass share (default 1.0). Multi-category items have
    # share < 1 on each row; mass balance holds because shares per
    # (item, source_type, animal_type) sum to 1.0 (validated upstream in
    # categorize_feeds.apply_category_overrides).
    efficiency = expanded["share"].astype(float) if "share" in expanded.columns else 1.0

    n.links.add(
        expanded.index,
        bus0=expanded["bus0"],
        bus1=expanded["bus1"],
        carrier="feed_conversion",
        efficiency=efficiency,
        marginal_cost=_LOW_PROCESSING_COST,
        p_nom_extendable=True,
        country=expanded["country"],
        feed_category=expanded["feed_category_value"],
        crop=expanded["crop_value"],
    )

    logger.info(
        "Created %d feed supply links (%d ruminant, %d monogastric)",
        len(expanded),
        len(ruminant_feeds) * len(countries),
        len(monogastric_feeds) * len(countries),
    )
