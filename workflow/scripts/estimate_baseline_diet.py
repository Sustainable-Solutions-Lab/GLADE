#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Estimate per-food, per-country baseline diet from multiple data sources.

Combines food group totals (from GDD + FAOSTAT dietary intake) with GBD
dietary risk exposure data and FAOSTAT item-level food supply to produce
per-food consumption estimates (g/person/day).

Algorithm:
    1. Load food group totals from dietary_intake.csv (GDD + FAOSTAT supplements)
    2. For overlapping groups, average GDD and GBD estimates
    3. Build within-group food shares from FAOSTAT FBS item-level supply
    4. Resolve shared FBS items using QCL production data
    5. Compute per-food consumption = group_total x within_group_share

Input:
    - dietary_intake.csv: Food group totals (GDD + FAOSTAT, waste-corrected)
    - gbd_dietary_risk_exposure.csv: GBD estimates for averaging/cross-validation
    - faostat_fbs_items.csv: Item-level food supply for within-group shares
    - faostat_crop_production.csv: Crop production for QCL-based resolution
    - faostat_animal_production.csv: Animal production (dairy vs buffalo)
    - faostat_food_item_map.csv: Food → FBS item mapping
    - faostat_food_qcl_resolution.csv: Food → QCL item mapping for tie-breaking
    - food_groups.csv: Food group definitions

Output:
    - baseline_diet.csv: Per-food, per-country consumption (g/person/day)
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Food groups where GDD and GBD are averaged
GDD_GBD_AVERAGED_GROUPS = {
    "fruits",
    "vegetables",
    "whole_grains",
    "legumes",
    "nuts_seeds",
    "red_meat",
}

# Default pearl millet share of total millet production.
# Pearl millet (Pennisetum glaucum) dominates global millet output;
# foxtail millet (Setaria italica) accounts for the remainder.
# Source: FAO global millet production breakdowns are not available at
# species level, so we use a literature-based estimate.
DEFAULT_PEARL_MILLET_SHARE = 0.8


def load_group_totals(
    dietary_intake_path: str,
    gbd_exposure_path: str,
    baseline_age: str,
    reference_year: int,
    food_groups_included: list[str],
) -> pd.DataFrame:
    """Load food group totals, averaging GDD and GBD where applicable.

    Returns DataFrame with columns: country, food_group, group_total_g_per_day
    """
    # Load GDD + FAOSTAT dietary intake (waste-corrected)
    intake = pd.read_csv(dietary_intake_path)
    # Filter to baseline age group
    intake = intake[intake["age"] == baseline_age].copy()
    if intake.empty:
        raise ValueError(f"No dietary intake data for age group '{baseline_age}'")
    intake = intake.rename(columns={"item": "food_group"})
    # Keep only included food groups
    intake = intake[intake["food_group"].isin(food_groups_included)]
    gdd_totals = intake.set_index(["country", "food_group"])["value"]

    # Load GBD dietary risk exposure (aggregate duplicates if any)
    gbd = pd.read_csv(gbd_exposure_path)
    gbd_totals = gbd.groupby(["country", "food_group"])["consumption_g_per_day"].mean()

    # Build combined group totals
    results = []
    for country in sorted(gdd_totals.index.get_level_values("country").unique()):
        for fg in food_groups_included:
            gdd_val = gdd_totals.get((country, fg))
            if gdd_val is None:
                continue

            if fg in GDD_GBD_AVERAGED_GROUPS:
                gbd_val = gbd_totals.get((country, fg))
                if gbd_val is not None and pd.notna(gbd_val):
                    # Average of GDD and GBD
                    combined = (gdd_val + gbd_val) / 2.0
                    logger.debug(
                        "%s/%s: GDD=%.1f, GBD=%.1f, avg=%.1f",
                        country,
                        fg,
                        gdd_val,
                        gbd_val,
                        combined,
                    )
                    results.append(
                        {
                            "country": country,
                            "food_group": fg,
                            "group_total_g_per_day": combined,
                        }
                    )
                else:
                    # GBD missing for this country; use GDD only
                    logger.debug(
                        "%s/%s: GBD missing, using GDD=%.1f",
                        country,
                        fg,
                        gdd_val,
                    )
                    results.append(
                        {
                            "country": country,
                            "food_group": fg,
                            "group_total_g_per_day": gdd_val,
                        }
                    )
            else:
                # GDD-only or FAOSTAT-only groups
                results.append(
                    {
                        "country": country,
                        "food_group": fg,
                        "group_total_g_per_day": gdd_val,
                    }
                )

    result_df = pd.DataFrame(results)

    # Log cross-validation: GDD vs GBD agreement
    log_gdd_gbd_agreement(gdd_totals, gbd_totals)

    return result_df


def log_gdd_gbd_agreement(gdd_totals: pd.Series, gbd_totals: pd.Series) -> None:
    """Log cross-validation metrics between GDD and GBD estimates."""
    for fg in GDD_GBD_AVERAGED_GROUPS:
        gdd_fg = gdd_totals.xs(fg, level="food_group", drop_level=False)
        gbd_fg = (
            gbd_totals.xs(fg, level="food_group", drop_level=False)
            if fg in gbd_totals.index.get_level_values("food_group")
            else pd.Series(dtype=float)
        )

        if gbd_fg.empty:
            logger.info("Cross-validation: %s - no GBD data available", fg)
            continue

        # Align on common countries
        common = gdd_fg.index.intersection(gbd_fg.index)
        if len(common) == 0:
            logger.info("Cross-validation: %s - no common countries", fg)
            continue

        gdd_vals = gdd_fg.loc[common]
        gbd_vals = gbd_fg.loc[common]

        ratio = (gdd_vals / gbd_vals.replace(0, float("nan"))).dropna()
        if len(ratio) > 0:
            logger.info(
                "Cross-validation: %s - %d countries, median GDD/GBD ratio=%.2f "
                "(range: %.2f-%.2f)",
                fg,
                len(ratio),
                ratio.median(),
                ratio.min(),
                ratio.max(),
            )

    # Also log GBD milk as cross-validation for dairy
    if "milk" in gbd_totals.index.get_level_values("food_group"):
        milk = gbd_totals.xs("milk", level="food_group")
        logger.info(
            "Cross-validation: GBD milk (25+) - %d countries, mean=%.1f g/day "
            "(for reference against FAOSTAT dairy)",
            len(milk),
            milk.mean(),
        )


def build_within_group_shares(
    food_groups_df: pd.DataFrame,
    food_item_map_df: pd.DataFrame,
    fbs_items_df: pd.DataFrame,
    qcl_resolution_df: pd.DataFrame,
    crop_production_df: pd.DataFrame,
    animal_production_df: pd.DataFrame,
    food_groups_included: list[str],
    byproducts: list[str],
) -> pd.DataFrame:
    """Build within-group food shares per country from FAOSTAT data.

    Returns DataFrame with columns: country, food, food_group, share
    """
    # Build food → food_group mapping
    fg_map = food_groups_df.set_index("food")["group"].to_dict()

    # Build food → FBS item_code mapping
    fbs_map = food_item_map_df.set_index("food")["item_code"].to_dict()

    # Get foods to include (exclude byproducts)
    byproduct_set = set(byproducts)
    included_foods = [
        f
        for f in fg_map
        if fg_map[f] in food_groups_included and f not in byproduct_set
    ]

    # Identify foods sharing FBS items
    fbs_code_to_foods: dict[int, list[str]] = {}
    for food in included_foods:
        code = fbs_map.get(food)
        if code is not None and pd.notna(code):
            code = int(code)
            fbs_code_to_foods.setdefault(code, []).append(food)

    # Build QCL resolution lookup: food → (qcl_item_code, qcl_item_name)
    qcl_lookup: dict[str, int] = {}
    if not qcl_resolution_df.empty:
        for _, row in qcl_resolution_df.iterrows():
            qcl_lookup[row["food"]] = int(row["qcl_item_code"])

    # Get all countries from FBS data
    countries = sorted(fbs_items_df["country"].unique())

    # Build FBS supply lookup: (country, item_code) → supply_kg
    fbs_supply = fbs_items_df.set_index(["country", "item_code"])[
        "supply_kg_per_capita_year"
    ].to_dict()

    # Build QCL production lookups
    # Crop production: (country, qcl_item_code) → production_tonnes
    crop_prod_lookup = _build_crop_production_lookup(crop_production_df, qcl_lookup)
    # Animal production: (country, qcl_item_code) → production_mt
    animal_prod_lookup = _build_animal_production_lookup(
        animal_production_df, qcl_lookup
    )

    all_shares = []

    for country in countries:
        for fbs_code, foods in fbs_code_to_foods.items():
            supply = fbs_supply.get((country, fbs_code), 0.0)
            if supply <= 0:
                # No supply data; assign equal shares
                n = len(foods)
                for food in foods:
                    all_shares.append(
                        {
                            "country": country,
                            "food": food,
                            "food_group": fg_map[food],
                            "share": 1.0 / n,
                        }
                    )
                continue

            if len(foods) == 1:
                # Unique mapping: this food gets 100% of the FBS item
                all_shares.append(
                    {
                        "country": country,
                        "food": foods[0],
                        "food_group": fg_map[foods[0]],
                        "share": 1.0,
                    }
                )
                continue

            # Multiple foods share this FBS item → resolve via QCL production
            shares = _resolve_shared_fbs_item(
                country, foods, qcl_lookup, crop_prod_lookup, animal_prod_lookup
            )
            for food, share in shares.items():
                all_shares.append(
                    {
                        "country": country,
                        "food": food,
                        "food_group": fg_map[food],
                        "share": share,
                    }
                )

    shares_df = pd.DataFrame(all_shares)

    # Handle millet split (foxtail-millet vs pearl-millet)
    # Both map to FBS item 2517 "Millet" and QCL only has aggregate "Millet"
    # Use a fixed global split as proxy
    _apply_millet_split(shares_df)

    # Convert from per-FBS-item shares to per-food-group shares.
    # Currently each food's share sums to 1.0 within its FBS item, but food
    # groups span multiple FBS items. Weight by FBS supply to get the correct
    # proportion of each food within its food group.
    shares_df["fbs_item_code"] = shares_df["food"].map(fbs_map)
    shares_df["fbs_supply_kg"] = shares_df.apply(
        lambda r: fbs_supply.get((r["country"], int(r["fbs_item_code"])), 0.0)
        if pd.notna(r["fbs_item_code"])
        else 0.0,
        axis=1,
    )
    shares_df["supply_weight"] = shares_df["share"] * shares_df["fbs_supply_kg"]
    group_total_weight = shares_df.groupby(["country", "food_group"])[
        "supply_weight"
    ].transform("sum")
    nonzero = group_total_weight > 0
    shares_df.loc[nonzero, "share"] = (
        shares_df.loc[nonzero, "supply_weight"] / group_total_weight[nonzero]
    )
    # Where total weight is zero (no FBS supply), use equal shares within group
    if (~nonzero).any():
        group_counts = shares_df.groupby(["country", "food_group"])["food"].transform(
            "count"
        )
        shares_df.loc[~nonzero, "share"] = 1.0 / group_counts[~nonzero]

    return shares_df[["country", "food", "food_group", "share"]].copy()


def _build_crop_production_lookup(
    crop_production_df: pd.DataFrame,
    qcl_lookup: dict[str, int],
) -> dict[tuple[str, int], float]:
    """Build (country, qcl_item_code) → production_tonnes lookup from crop production data.

    The crop production data uses crop names, not QCL item codes directly.
    We need to map from foods in qcl_lookup to crop names to production values.
    """
    # The QCL resolution CSV maps food names to QCL item codes, but crop_production.csv
    # uses crop names that may differ. We use the QCL item code as the key since
    # we aggregate by it.
    result: dict[tuple[str, int], float] = {}

    if crop_production_df.empty:
        return result

    # The crop production file has columns: country, crop, year, production_tonnes
    # The crop names match model crop names (same as food names in some cases)
    # Build a mapping from food name to crop production
    for _, row in crop_production_df.iterrows():
        crop_name = row["crop"]
        country = row["country"]
        production = row["production_tonnes"]

        # Check if this crop name is in qcl_lookup (as a food name)
        if crop_name in qcl_lookup:
            qcl_code = qcl_lookup[crop_name]
            key = (country, qcl_code)
            result[key] = result.get(key, 0.0) + production

    return result


def _build_animal_production_lookup(
    animal_production_df: pd.DataFrame,
    qcl_lookup: dict[str, int],
) -> dict[tuple[str, int], float]:
    """Build (country, qcl_item_code) → production_mt lookup from animal production data."""
    result: dict[tuple[str, int], float] = {}

    if animal_production_df.empty:
        return result

    # Animal production has columns: country, product, year, production_mt
    # Map product names to QCL codes via qcl_lookup
    for _, row in animal_production_df.iterrows():
        product_name = row["product"]
        country = row["country"]
        production = row["production_mt"]

        if product_name in qcl_lookup:
            qcl_code = qcl_lookup[product_name]
            key = (country, qcl_code)
            result[key] = result.get(key, 0.0) + production

    return result


def _resolve_shared_fbs_item(
    country: str,
    foods: list[str],
    qcl_lookup: dict[str, int],
    crop_prod_lookup: dict[tuple[str, int], float],
    animal_prod_lookup: dict[tuple[str, int], float],
) -> dict[str, float]:
    """Resolve shares among foods sharing a single FBS item using QCL production.

    Falls back to equal split if no production data is available.
    """
    # Group foods by their QCL item code
    qcl_code_to_foods: dict[int, list[str]] = {}
    unresolved_foods: list[str] = []

    for food in foods:
        qcl_code = qcl_lookup.get(food)
        if qcl_code is not None:
            qcl_code_to_foods.setdefault(qcl_code, []).append(food)
        else:
            unresolved_foods.append(food)

    # Get production for each QCL code
    productions: dict[int, float] = {}
    for qcl_code in qcl_code_to_foods:
        prod = crop_prod_lookup.get((country, qcl_code), 0.0)
        if prod == 0.0:
            prod = animal_prod_lookup.get((country, qcl_code), 0.0)
        productions[qcl_code] = prod

    total_production = sum(productions.values())

    shares: dict[str, float] = {}

    if total_production > 0:
        # Production-based split among QCL groups
        for qcl_code, qcl_foods in qcl_code_to_foods.items():
            group_share = productions[qcl_code] / total_production
            # Equal split within foods sharing the same QCL code
            per_food_share = group_share / len(qcl_foods)
            for food in qcl_foods:
                shares[food] = per_food_share
    else:
        # No production data; equal split among all QCL-mapped foods
        n_mapped = sum(len(fl) for fl in qcl_code_to_foods.values())
        n_total = n_mapped + len(unresolved_foods)
        equal_share = 1.0 / n_total if n_total > 0 else 0.0
        for qcl_foods in qcl_code_to_foods.values():
            for food in qcl_foods:
                shares[food] = equal_share

    # Unresolved foods (no QCL mapping) get equal share of remainder
    if unresolved_foods:
        assigned = sum(shares.values())
        remainder = 1.0 - assigned
        per_food = remainder / len(unresolved_foods) if remainder > 0 else 0.0
        for food in unresolved_foods:
            shares[food] = per_food

    return shares


def _apply_millet_split(shares_df: pd.DataFrame) -> None:
    """Apply fixed global split for foxtail-millet vs pearl-millet.

    Both map to FBS item 2517 (Millet) and QCL only has aggregate "Millet",
    so we use a literature-based global production split.
    Modifies shares_df in place.
    """
    millet_mask = shares_df["food"].isin(["foxtail-millet", "pearl-millet"])
    if not millet_mask.any():
        return

    pearl_share = DEFAULT_PEARL_MILLET_SHARE
    foxtail_share = 1.0 - pearl_share

    for idx in shares_df[millet_mask].index:
        food = shares_df.loc[idx, "food"]
        current_share = shares_df.loc[idx, "share"]
        if food == "pearl-millet":
            shares_df.loc[idx, "share"] = current_share * pearl_share / 0.5
        elif food == "foxtail-millet":
            shares_df.loc[idx, "share"] = current_share * foxtail_share / 0.5

    # Verify the millet shares still sum correctly per country
    for country in shares_df.loc[millet_mask, "country"].unique():
        country_millet = shares_df[(shares_df["country"] == country) & millet_mask]
        total = country_millet["share"].sum()
        if abs(total - 1.0) > 0.01:
            logger.warning(
                "Millet shares for %s sum to %.3f (expected 1.0)", country, total
            )


def main():
    dietary_intake_path = snakemake.input.dietary_intake
    gbd_exposure_path = snakemake.input.gbd_exposure
    fbs_items_path = snakemake.input.fbs_items
    crop_production_path = snakemake.input.crop_production
    animal_production_path = snakemake.input.animal_production
    food_item_map_path = snakemake.input.food_item_map
    qcl_resolution_path = snakemake.input.qcl_resolution
    food_groups_path = snakemake.input.food_groups
    output_path = snakemake.output.baseline_diet

    reference_year = int(snakemake.params.reference_year)
    baseline_age = str(snakemake.params.baseline_age)
    food_groups_included = list(snakemake.params.food_groups_included)
    byproducts = list(snakemake.params.byproducts)

    logger.info("Estimating baseline diet for reference year %d", reference_year)
    logger.info("Baseline age group: %s", baseline_age)
    logger.info("Food groups: %s", food_groups_included)

    # Load input data
    food_groups_df = pd.read_csv(food_groups_path)
    food_item_map_df = pd.read_csv(food_item_map_path, comment="#")
    fbs_items_df = pd.read_csv(fbs_items_path)
    qcl_resolution_df = pd.read_csv(qcl_resolution_path, comment="#")
    crop_production_df = pd.read_csv(crop_production_path)
    animal_production_df = pd.read_csv(animal_production_path)

    # Step 1: Food group totals (GDD+GBD averaged where applicable)
    logger.info("Step 1: Computing food group totals...")
    group_totals = load_group_totals(
        dietary_intake_path,
        gbd_exposure_path,
        baseline_age,
        reference_year,
        food_groups_included,
    )
    logger.info(
        "Group totals: %d countries, %d food groups",
        group_totals["country"].nunique(),
        group_totals["food_group"].nunique(),
    )

    # Step 2: Within-group food shares
    logger.info("Step 2: Building within-group food shares...")
    shares = build_within_group_shares(
        food_groups_df,
        food_item_map_df,
        fbs_items_df,
        qcl_resolution_df,
        crop_production_df,
        animal_production_df,
        food_groups_included,
        byproducts,
    )
    logger.info(
        "Food shares: %d countries, %d foods",
        shares["country"].nunique(),
        shares["food"].nunique(),
    )

    # Log which countries needed global-average fallback for QCL shares
    _log_qcl_fallback_stats(shares, qcl_resolution_df)

    # Step 3: Per-food consumption = group_total * share
    logger.info("Step 3: Computing per-food consumption estimates...")
    baseline_diet = shares.merge(
        group_totals,
        on=["country", "food_group"],
        how="inner",
    )
    baseline_diet["consumption_g_per_day"] = (
        baseline_diet["group_total_g_per_day"] * baseline_diet["share"]
    )

    # Select output columns
    result = baseline_diet[
        ["country", "food", "food_group", "consumption_g_per_day"]
    ].copy()
    result = result.sort_values(["country", "food_group", "food"]).reset_index(
        drop=True
    )

    # Validation: group sums should match group totals
    _validate_group_sums(result, group_totals)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    result.to_csv(output_path, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %d foods) to %s",
        len(result),
        result["country"].nunique(),
        result["food"].nunique(),
        output_path,
    )

    # Summary statistics
    _log_summary_stats(result)


def _log_qcl_fallback_stats(
    shares: pd.DataFrame, qcl_resolution_df: pd.DataFrame
) -> None:
    """Log which countries needed global-average fallback for QCL shares."""
    if qcl_resolution_df.empty:
        return

    qcl_foods = set(qcl_resolution_df["food"].unique())
    qcl_shares = shares[shares["food"].isin(qcl_foods)]
    if qcl_shares.empty:
        return

    # Check for countries where all QCL-resolved foods in a group have equal shares
    # (indicating fallback to global average)
    for fbs_code in qcl_resolution_df["fbs_item_code"].unique():
        foods_for_code = qcl_resolution_df[
            qcl_resolution_df["fbs_item_code"] == fbs_code
        ]["food"].tolist()
        subset = qcl_shares[qcl_shares["food"].isin(foods_for_code)]

        for country, grp in subset.groupby("country"):
            if len(grp) > 1:
                share_range = grp["share"].max() - grp["share"].min()
                if share_range < 1e-6:
                    logger.debug(
                        "QCL fallback (equal split) for FBS %d in %s",
                        fbs_code,
                        country,
                    )


def _validate_group_sums(result: pd.DataFrame, group_totals: pd.DataFrame) -> None:
    """Validate that within-group sums match group totals."""
    computed_sums = (
        result.groupby(["country", "food_group"])["consumption_g_per_day"]
        .sum()
        .reset_index()
        .rename(columns={"consumption_g_per_day": "computed_sum"})
    )
    merged = computed_sums.merge(group_totals, on=["country", "food_group"])

    if merged.empty:
        logger.warning("No group sums to validate")
        return

    merged["diff"] = abs(merged["computed_sum"] - merged["group_total_g_per_day"])
    large_diffs = merged[merged["diff"] > 0.1]
    if len(large_diffs) > 0:
        logger.warning(
            "Group sum validation: %d country-group pairs differ by >0.1 g/day",
            len(large_diffs),
        )
        for _, row in large_diffs.head(10).iterrows():
            logger.warning(
                "  %s/%s: computed=%.1f, expected=%.1f",
                row["country"],
                row["food_group"],
                row["computed_sum"],
                row["group_total_g_per_day"],
            )
    else:
        logger.info("Group sum validation: all groups within tolerance")


def _log_summary_stats(result: pd.DataFrame) -> None:
    """Log summary statistics for the baseline diet."""
    # Per-country totals
    country_totals = result.groupby("country")["consumption_g_per_day"].sum()
    logger.info(
        "Total consumption: mean=%.0f g/day, range=[%.0f, %.0f]",
        country_totals.mean(),
        country_totals.min(),
        country_totals.max(),
    )

    # Per-food-group averages
    group_means = result.groupby("food_group")["consumption_g_per_day"].mean()
    logger.info("Mean consumption by food group:")
    for fg, val in group_means.sort_values(ascending=False).items():
        logger.info("  %s: %.1f g/day", fg, val)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
