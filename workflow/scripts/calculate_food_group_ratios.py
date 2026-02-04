#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Calculate within-group food consumption ratios from FAOSTAT FBS supply data.

For each (country, food_group), calculates the fraction of total group
supply contributed by each food. These ratios are used to constrain
relative food contributions within each group during optimization.

When multiple foods map to the same FBS item, the FBS supply is split
equally among them.

Input:
    - FBS item-level supply data (from prepare_faostat_fbs_items.py)
    - Food-to-FBS-item mapping (data/faostat_food_item_map.csv)
    - Food groups (data/food_groups.csv)

Output:
    - CSV with columns: country, food_group, food, ratio
      where ratio = food_supply / total_group_supply for that country
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def calculate_ratios(
    fbs_items: pd.DataFrame,
    food_item_map: pd.DataFrame,
    food_groups: pd.DataFrame,
    byproducts: list[str],
) -> pd.DataFrame:
    """Calculate within-group food ratios from FBS supply data.

    Parameters
    ----------
    fbs_items : pd.DataFrame
        FBS supply data with columns: item_code, item_name, country,
        supply_kg_per_capita_year
    food_item_map : pd.DataFrame
        Mapping with columns: food, faostat_item, item_code
    food_groups : pd.DataFrame
        Food group assignments with columns: food, group
    byproducts : list[str]
        Foods to exclude from ratio calculation (e.g., wheat-bran, rice-bran)

    Returns
    -------
    pd.DataFrame
        Ratios with columns: country, food_group, food, ratio
    """
    # Merge food item mapping with food groups
    food_map = food_item_map.merge(food_groups, on="food", how="inner")
    food_map = food_map.rename(columns={"group": "food_group"})

    # Exclude byproducts
    if byproducts:
        excluded = food_map[food_map["food"].isin(byproducts)]
        if not excluded.empty:
            logger.info(
                "Excluding %d byproduct foods from ratio calculation: %s",
                len(excluded),
                ", ".join(excluded["food"].unique()),
            )
        food_map = food_map[~food_map["food"].isin(byproducts)]

    if food_map.empty:
        raise ValueError("No foods remaining after excluding byproducts")

    # Count how many foods share each item_code (for equal-split logic)
    foods_per_item = food_map.groupby("item_code").size().reset_index(name="n_foods")
    food_map = food_map.merge(foods_per_item, on="item_code")

    # Merge FBS supply data
    food_map["item_code"] = food_map["item_code"].astype(int)
    fbs_items["item_code"] = fbs_items["item_code"].astype(int)

    # Cross-join foods with countries to ensure we have all combinations
    countries = fbs_items["country"].unique()
    food_country = (
        food_map.assign(key=1)
        .merge(pd.DataFrame({"country": countries, "key": 1}), on="key")
        .drop("key", axis=1)
    )

    # Merge with FBS supply
    merged = food_country.merge(
        fbs_items[["item_code", "country", "supply_kg_per_capita_year"]],
        on=["item_code", "country"],
        how="left",
    )

    # Fill missing supply with 0
    merged["supply_kg_per_capita_year"] = merged["supply_kg_per_capita_year"].fillna(
        0.0
    )

    # Split supply equally among foods sharing the same FBS item
    merged["food_supply"] = merged["supply_kg_per_capita_year"] / merged["n_foods"]

    # Calculate total group supply per country
    group_totals = (
        merged.groupby(["country", "food_group"])["food_supply"]
        .sum()
        .reset_index()
        .rename(columns={"food_supply": "group_total"})
    )

    # Merge back and calculate ratios
    result = merged.merge(group_totals, on=["country", "food_group"])

    # Calculate ratio, handling zero-total groups
    result["ratio"] = 0.0
    nonzero_mask = result["group_total"] > 0
    result.loc[nonzero_mask, "ratio"] = (
        result.loc[nonzero_mask, "food_supply"]
        / result.loc[nonzero_mask, "group_total"]
    )

    # Select output columns
    output = result[["country", "food_group", "food", "ratio"]].copy()
    output = output.sort_values(["country", "food_group", "food"]).reset_index(
        drop=True
    )

    return output


def main():
    fbs_items_path = snakemake.input.fbs_items
    food_item_map_path = snakemake.input.food_item_map
    food_groups_path = snakemake.input.food_groups
    output_file = snakemake.output.ratios
    byproducts = list(snakemake.params.byproducts)

    # Load data
    fbs_items = pd.read_csv(fbs_items_path)
    food_item_map = pd.read_csv(food_item_map_path, comment="#")
    food_groups = pd.read_csv(food_groups_path)

    logger.info(
        "Loaded %d FBS items, %d food mappings, %d food groups",
        len(fbs_items),
        len(food_item_map),
        food_groups["group"].nunique(),
    )

    # Calculate ratios
    ratios = calculate_ratios(fbs_items, food_item_map, food_groups, byproducts)

    # Log summary statistics
    n_countries = ratios["country"].nunique()
    n_groups = ratios["food_group"].nunique()
    n_foods = ratios["food"].nunique()

    logger.info(
        "Calculated ratios for %d foods in %d groups across %d countries",
        n_foods,
        n_groups,
        n_countries,
    )

    # Check for groups with zero total supply
    zero_groups = ratios.groupby(["country", "food_group"])["ratio"].sum()
    zero_groups = zero_groups[zero_groups == 0]
    if not zero_groups.empty:
        examples = [f"{c}/{g}" for (c, g) in zero_groups.index[:5]]
        logger.warning(
            "%d (country, group) pairs have zero total supply (examples: %s)",
            len(zero_groups),
            ", ".join(examples),
        )

    ratios.to_csv(output_file, index=False)
    logger.info("Wrote %d rows to %s", len(ratios), output_file)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
