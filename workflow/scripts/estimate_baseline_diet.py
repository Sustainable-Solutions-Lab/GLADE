#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

from workflow.scripts.diet.basis import (
    build_group_basis,
    convert_intake,
    load_food_basis,
    load_source_basis_country_overrides,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.vegetable_projection import (
    NUTS_COUNTRY_SHARE_BLEND,
    NUTS_PROJECTION_FOODS,
    NUTS_RESIDUAL_ITEM_CODE,
    OVG_COUNTRY_SHARE_BLEND,
    OVG_CROPS,
    STARCHY_COUNTRY_SHARE_BLEND,
    STARCHY_PROJECTION_FOODS,
    STARCHY_RESIDUAL_ITEM_CODE,
    VEGETABLE_RESIDUAL_ITEM_CODE,
    build_blended_crop_shares,
)

logger = logging.getLogger(__name__)

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
    food_groups_included: list[str],
    gbd_anchored_groups: set[str],
    source_basis: dict[str, dict[str, str]],
    source_basis_country_overrides: dict[str, dict[str, dict[str, str]]],
    group_basis: dict[str, str],
    weight_conversion: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Load food group totals, anchoring risk groups to GBD when available.

    For groups in *gbd_anchored_groups*, the per-country total comes from
    GBD when GBD reports a value (so the baseline aligns with the same
    intake basis the GBD relative-risk functions are calibrated against);
    otherwise the GDD/FAOSTAT value from *dietary_intake_path* is used.
    For all other groups the GDD/FAOSTAT value is used directly.

    GBD's intake exposure values share the basis of the GBD/IHME RR
    dose-response curves. Per-(source, country, group) basis lookup
    via *source_basis* and *source_basis_country_overrides* drives
    cooked->dry / cooked->fresh conversions where the source basis
    differs from the model's.

    Returns DataFrame with columns: country, food_group, group_total_g_per_day
    """
    # Load GDD + FAOSTAT dietary intake (already in model basis after
    # merge_dietary_sources applied source_basis conversion).
    intake = pd.read_csv(dietary_intake_path)
    intake = intake[intake["age"] == baseline_age].copy()
    if intake.empty:
        raise ValueError(f"No dietary intake data for age group '{baseline_age}'")
    intake = intake.rename(columns={"item": "food_group"})
    intake = intake[intake["food_group"].isin(food_groups_included)]
    gdd_totals = intake.set_index(["country", "food_group"])["value"]

    # Load GBD dietary risk exposure (aggregate duplicates if any) and
    # convert to the model's basis where it differs.
    gbd = pd.read_csv(gbd_exposure_path)
    gbd = convert_intake(
        gbd,
        source="gbd",
        value_column="consumption_g_per_day",
        group_column="food_group",
        country_column="country",
        source_basis=source_basis,
        source_basis_country_overrides=source_basis_country_overrides,
        target_basis_by_key=group_basis,
        factors=weight_conversion,
    )
    gbd_totals = gbd.groupby(["country", "food_group"])["consumption_g_per_day"].mean()

    # Build combined group totals: GBD-anchored groups prefer GBD when
    # available, else fall back to GDD/FAOSTAT; non-anchored groups
    # use GDD/FAOSTAT directly.
    results = []
    for country in sorted(gdd_totals.index.get_level_values("country").unique()):
        for fg in food_groups_included:
            gdd_val = gdd_totals.get((country, fg))
            gbd_val = (
                gbd_totals.get((country, fg)) if fg in gbd_anchored_groups else None
            )
            if pd.notna(gbd_val):
                value = float(gbd_val)
            elif pd.notna(gdd_val):
                value = float(gdd_val)
            else:
                continue
            results.append(
                {
                    "country": country,
                    "food_group": fg,
                    "group_total_g_per_day": value,
                }
            )

    log_gdd_gbd_agreement(gdd_totals, gbd_totals, gbd_anchored_groups)
    return pd.DataFrame(results)


GRAIN_GROUP = "grain"
WHOLE_GRAINS_GROUP = "whole_grains"


def apply_fbs_grain_supplement(
    group_totals: pd.DataFrame,
    fbs_cereal_intake_path: str,
    enabled: bool,
    threshold: float,
) -> pd.DataFrame:
    """Backfill the refined-``grain`` total from FBS-derived cereal intake.

    GDD systematically under-reports refined grain in some HICs
    (Norway: 13.5 g/day GDD vs ~185 g/day inferred from FBS supply
    minus whole-grain consumption). The pipeline has no FAOSTAT
    supplement for cereals because GBD covers ``whole_grains`` as a
    risk factor and we don't want to disturb that anchor.

    For every country, define the FBS-implied refined-grain
    residual as

        residual = max(0, fbs_cereal_intake - whole_grains_total)

    Then apply

        new_grain = max(current_grain, threshold * residual)

    so that countries with plausible GDD coverage are left untouched
    while obvious holes are lifted to a defensible floor. *threshold*
    is the minimum fraction of the FBS residual we are willing to
    accept as refined-grain intake; with the historical FLW
    under-correction for HIC consumer waste, ``threshold = 0.5``
    matches the ~50% of FBS-supply level that household-survey
    studies typically hit in HICs. The current value is preserved
    when it is already at or above this floor.

    If ``residual <= 0`` (FBS thinks cereals are essentially entirely
    whole grain, common for some LICs where FBS coverage is sparse),
    we skip the supplement to avoid pushing grain to zero.
    """
    if not enabled:
        return group_totals

    fbs_cereal = pd.read_csv(fbs_cereal_intake_path).set_index("country")[
        "fbs_cereal_intake_g_per_day"
    ]

    df = group_totals.copy()
    wide = df.pivot(
        index="country", columns="food_group", values="group_total_g_per_day"
    )
    if GRAIN_GROUP not in wide.columns:
        wide[GRAIN_GROUP] = float("nan")
    n_supplemented = 0
    skipped_negative_residual = 0
    skipped_already_above_floor = 0
    skipped_no_fbs = 0
    for country in wide.index:
        if country not in fbs_cereal.index:
            skipped_no_fbs += 1
            continue
        wg = wide.loc[country].get(WHOLE_GRAINS_GROUP, float("nan"))
        wg = 0.0 if pd.isna(wg) else float(wg)
        residual = float(fbs_cereal.loc[country]) - wg
        if residual <= 0:
            skipped_negative_residual += 1
            continue
        floor = threshold * residual
        current = wide.loc[country].get(GRAIN_GROUP, float("nan"))
        current_val = 0.0 if pd.isna(current) else float(current)
        if current_val >= floor:
            skipped_already_above_floor += 1
            continue
        wide.loc[country, GRAIN_GROUP] = floor
        n_supplemented += 1

    long = wide.reset_index().melt(
        id_vars="country", var_name="food_group", value_name="group_total_g_per_day"
    )
    long = long.dropna(subset=["group_total_g_per_day"])
    logger.info(
        "FBS grain supplement: lifted refined-grain in %d countries to "
        "%.0f%% of (FBS cereal - whole_grains); skipped %d already at/above "
        "floor, %d with non-positive residual, %d with no FBS data",
        n_supplemented,
        threshold * 100,
        skipped_already_above_floor,
        skipped_negative_residual,
        skipped_no_fbs,
    )
    return long.sort_values(["country", "food_group"]).reset_index(drop=True)


def log_gdd_gbd_agreement(
    gdd_totals: pd.Series,
    gbd_totals: pd.Series,
    gbd_anchored_groups: set[str],
) -> None:
    """Log cross-validation metrics between GDD and GBD estimates."""
    for fg in gbd_anchored_groups:
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
    carcass_to_retail_meat: dict[str, float],
) -> pd.DataFrame:
    """Build within-group food shares per country from FAOSTAT data.

    Returns DataFrame with columns: country, food, food_group, share
    """
    # Build food → food_group mapping
    fg_map = food_groups_df.set_index("food")["group"].to_dict()

    # Build food → [FBS item_code, ...] mapping.
    # Some foods (e.g., citrus) are represented by multiple FAOSTAT items.
    map_df = food_item_map_df.copy()
    map_df["food"] = map_df["food"].astype(str)
    map_df["item_code"] = pd.to_numeric(map_df["item_code"], errors="coerce")
    map_df = map_df[map_df["item_code"].notna()].copy()
    map_df["item_code"] = map_df["item_code"].astype(int)
    fbs_codes_by_food = (
        map_df.groupby("food")["item_code"]
        .apply(lambda s: sorted(set(s.tolist())))
        .to_dict()
    )

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
        for code in fbs_codes_by_food.get(food, []):
            fbs_code_to_foods.setdefault(int(code), []).append(food)

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
    # Animal production: (country, qcl_item_code) → production_mt_fresh_retail
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
                            "fbs_item_code": fbs_code,
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
                        "fbs_item_code": fbs_code,
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
                        "fbs_item_code": fbs_code,
                        "share": share,
                    }
                )

    shares_df = pd.DataFrame(all_shares)

    # Handle millet split (foxtail-millet vs pearl-millet)
    # Both map to FBS item 2517 "Millet" and QCL only has aggregate "Millet"
    # Use a fixed global split as proxy
    _apply_millet_split(shares_df)

    # Convert from per-FBS-item shares to per-food-group shares.
    # Each row carries an FBS item code. Weight by item-level supply, then
    # aggregate back to (country, food, food_group).
    shares_df["fbs_supply_kg"] = shares_df.apply(
        lambda r: fbs_supply.get((r["country"], int(r["fbs_item_code"])), 0.0),
        axis=1,
    )
    # Convert FBS meat supply (carcass basis) to retail-equivalent mass so
    # within-group shares are consistent with model meat units.
    shares_df["carcass_to_retail_factor"] = shares_df["food"].map(
        carcass_to_retail_meat
    )
    meat_mask = shares_df["food"].astype(str).str.startswith("meat-")
    missing_factors = shares_df.loc[
        meat_mask & shares_df["carcass_to_retail_factor"].isna(), "food"
    ].unique()
    if len(missing_factors) > 0:
        logger.warning(
            "Missing carcass-to-retail factors for meats in baseline share estimation: %s",
            ", ".join(sorted(missing_factors)),
        )
    shares_df["carcass_to_retail_factor"] = shares_df[
        "carcass_to_retail_factor"
    ].fillna(1.0)
    shares_df["fbs_supply_kg"] = (
        shares_df["fbs_supply_kg"] * shares_df["carcass_to_retail_factor"]
    )
    shares_df["supply_weight"] = shares_df["share"] * shares_df["fbs_supply_kg"]
    shares_df = (
        shares_df.groupby(["country", "food_group", "food"], as_index=False)[
            "supply_weight"
        ]
        .sum()
        .copy()
    )
    shares_df = _project_vegetable_residual_supply(
        shares_df,
        included_foods=included_foods,
        fg_map=fg_map,
        fbs_codes_by_food=fbs_codes_by_food,
        fbs_supply=fbs_supply,
        countries=countries,
        crop_production_df=crop_production_df,
    )
    shares_df = _project_nuts_residual_supply(
        shares_df,
        included_foods=included_foods,
        fg_map=fg_map,
        fbs_codes_by_food=fbs_codes_by_food,
        fbs_supply=fbs_supply,
        countries=countries,
        crop_production_df=crop_production_df,
    )
    shares_df = _project_starchy_residual_supply(
        shares_df,
        included_foods=included_foods,
        fg_map=fg_map,
        fbs_codes_by_food=fbs_codes_by_food,
        fbs_supply=fbs_supply,
        countries=countries,
        crop_production_df=crop_production_df,
    )
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


def _project_vegetable_residual_supply(
    shares_df: pd.DataFrame,
    included_foods: list[str],
    fg_map: dict[str, str],
    fbs_codes_by_food: dict[str, list[int]],
    fbs_supply: dict[tuple[str, int], float],
    countries: list[str],
    crop_production_df: pd.DataFrame,
) -> pd.DataFrame:
    """Project FAOSTAT residual vegetables (item 2605) onto modeled vegetables.

    The residual item is distributed across OVG crops (onion/cabbage/carrot)
    using blended country/global production shares. Tomato keeps its explicit
    item-level supply only.
    """
    vegetable_foods = [
        food for food in included_foods if fg_map.get(food) == "vegetables"
    ]
    if len(vegetable_foods) == 0:
        return shares_df

    ovg_foods = [food for food in OVG_CROPS if food in vegetable_foods]
    if len(ovg_foods) == 0:
        return shares_df

    ovg_lookup, ovg_global = build_blended_crop_shares(
        crop_production_df,
        ovg_foods,
        blend_weight=OVG_COUNTRY_SHARE_BLEND,
    )

    residual_code = VEGETABLE_RESIDUAL_ITEM_CODE
    rebuilt_rows: list[dict[str, object]] = []
    for country in countries:
        residual_supply = float(fbs_supply.get((country, residual_code), 0.0))
        for food in vegetable_foods:
            explicit_codes = [
                int(code)
                for code in fbs_codes_by_food.get(food, [])
                if int(code) != residual_code
            ]
            explicit_supply = float(
                sum(fbs_supply.get((country, code), 0.0) for code in explicit_codes)
            )
            projected_supply = 0.0
            if food in ovg_foods and residual_supply > 0.0:
                projected_supply = residual_supply * ovg_lookup.get(
                    (country, food), ovg_global[food]
                )
            rebuilt_rows.append(
                {
                    "country": country,
                    "food_group": "vegetables",
                    "food": food,
                    "supply_weight": explicit_supply + projected_supply,
                }
            )

    if len(rebuilt_rows) == 0:
        return shares_df

    rebuilt = pd.DataFrame(rebuilt_rows)
    non_vegetables = shares_df[shares_df["food_group"] != "vegetables"]
    vegetables = pd.concat([rebuilt], ignore_index=True)
    return pd.concat([non_vegetables, vegetables], ignore_index=True)


def _project_nuts_residual_supply(
    shares_df: pd.DataFrame,
    included_foods: list[str],
    fg_map: dict[str, str],
    fbs_codes_by_food: dict[str, list[int]],
    fbs_supply: dict[tuple[str, int], float],
    countries: list[str],
    crop_production_df: pd.DataFrame,
) -> pd.DataFrame:
    """Project FAOSTAT residual nuts item (2551) onto modeled nuts/seeds foods."""
    nuts_foods = [food for food in included_foods if fg_map.get(food) == "nuts_seeds"]
    if len(nuts_foods) == 0:
        return shares_df

    projection_foods = [food for food in NUTS_PROJECTION_FOODS if food in nuts_foods]
    if len(projection_foods) == 0:
        return shares_df

    crop_to_food = {
        "groundnut": "groundnut",
        "sesame": "sesame-seed",
        "coconut": "coconut",
        "sunflower": "sunflower-seed",
    }
    projected_crop_prod = crop_production_df.copy()
    projected_crop_prod["crop"] = (
        projected_crop_prod["crop"].astype(str).str.strip().map(crop_to_food)
    )
    projected_crop_prod = projected_crop_prod[projected_crop_prod["crop"].notna()]

    share_lookup, global_share = build_blended_crop_shares(
        projected_crop_prod,
        projection_foods,
        blend_weight=NUTS_COUNTRY_SHARE_BLEND,
    )

    residual_code = NUTS_RESIDUAL_ITEM_CODE
    rebuilt_rows: list[dict[str, object]] = []
    for country in countries:
        residual_supply = float(fbs_supply.get((country, residual_code), 0.0))
        for food in nuts_foods:
            explicit_codes = [
                int(code)
                for code in fbs_codes_by_food.get(food, [])
                if int(code) != residual_code
            ]
            explicit_supply = float(
                sum(fbs_supply.get((country, code), 0.0) for code in explicit_codes)
            )
            projected_supply = 0.0
            if food in projection_foods and residual_supply > 0.0:
                projected_supply = residual_supply * share_lookup.get(
                    (country, food), global_share[food]
                )
            rebuilt_rows.append(
                {
                    "country": country,
                    "food_group": "nuts_seeds",
                    "food": food,
                    "supply_weight": explicit_supply + projected_supply,
                }
            )

    if len(rebuilt_rows) == 0:
        return shares_df

    rebuilt = pd.DataFrame(rebuilt_rows)
    non_nuts = shares_df[shares_df["food_group"] != "nuts_seeds"]
    return pd.concat([non_nuts, rebuilt], ignore_index=True)


def _project_starchy_residual_supply(
    shares_df: pd.DataFrame,
    included_foods: list[str],
    fg_map: dict[str, str],
    fbs_codes_by_food: dict[str, list[int]],
    fbs_supply: dict[tuple[str, int], float],
    countries: list[str],
    crop_production_df: pd.DataFrame,
) -> pd.DataFrame:
    """Project FAOSTAT residual starchy vegetables (item 2534) to modeled foods."""
    starchy_foods = [
        food for food in included_foods if fg_map.get(food) == "starchy_vegetable"
    ]
    if len(starchy_foods) == 0:
        return shares_df

    projection_foods = [
        food for food in STARCHY_PROJECTION_FOODS if food in starchy_foods
    ]
    if len(projection_foods) == 0:
        return shares_df

    crop_to_food = {
        "white-potato": "potato",
        "sweet-potato": "sweet-potato",
        "yam": "yam",
        "cassava": "cassava",
    }
    projected_crop_prod = crop_production_df.copy()
    projected_crop_prod["crop"] = (
        projected_crop_prod["crop"].astype(str).str.strip().map(crop_to_food)
    )
    projected_crop_prod = projected_crop_prod[projected_crop_prod["crop"].notna()]

    share_lookup, global_share = build_blended_crop_shares(
        projected_crop_prod,
        projection_foods,
        blend_weight=STARCHY_COUNTRY_SHARE_BLEND,
    )

    residual_code = STARCHY_RESIDUAL_ITEM_CODE
    rebuilt_rows: list[dict[str, object]] = []
    for country in countries:
        residual_supply = float(fbs_supply.get((country, residual_code), 0.0))
        for food in starchy_foods:
            explicit_codes = [
                int(code)
                for code in fbs_codes_by_food.get(food, [])
                if int(code) != residual_code
            ]
            explicit_supply = float(
                sum(fbs_supply.get((country, code), 0.0) for code in explicit_codes)
            )
            projected_supply = 0.0
            if food in projection_foods and residual_supply > 0.0:
                projected_supply = residual_supply * share_lookup.get(
                    (country, food), global_share[food]
                )
            rebuilt_rows.append(
                {
                    "country": country,
                    "food_group": "starchy_vegetable",
                    "food": food,
                    "supply_weight": explicit_supply + projected_supply,
                }
            )

    if len(rebuilt_rows) == 0:
        return shares_df

    rebuilt = pd.DataFrame(rebuilt_rows)
    non_starchy = shares_df[shares_df["food_group"] != "starchy_vegetable"]
    return pd.concat([non_starchy, rebuilt], ignore_index=True)


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
    """Build (country, qcl_item_code) → production_mt_fresh_retail lookup.

    Animal production CSV columns: country, product, year,
    production_mt_fresh_retail. The mass basis is fresh retail weight for
    meats (post-c2r) and raw fresh weight for milk/eggs.
    """
    result: dict[tuple[str, int], float] = {}

    if animal_production_df.empty:
        return result

    # Map product names to QCL codes via qcl_lookup
    for _, row in animal_production_df.iterrows():
        product_name = row["product"]
        country = row["country"]
        production = row["production_mt_fresh_retail"]

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


def _apply_fbs_overrides(
    result: pd.DataFrame,
    fbs_items_df: pd.DataFrame,
    food_item_map_df: pd.DataFrame,
    flw_df: pd.DataFrame,
    qcl_resolution_df: pd.DataFrame,
    crop_production_df: pd.DataFrame,
    animal_production_df: pd.DataFrame,
    override_foods: list[str],
    carcass_to_retail: dict[str, float],
) -> pd.DataFrame:
    """Override per-food consumption with FBS-supply-anchored intake.

    For each override food, computes per-country intake mass as:

        intake_g_day = FBS_supply_kg_per_capita_year
                       * within_FBS_item_share
                       * carcass_to_retail
                       * (1 - loss_fraction)
                       * (1 - waste_fraction)
                       * 1000 / 365

    Carcass_to_retail converts FAOSTAT FBS supply (carcass weight equivalent
    for meat) to retail mass; non-meat foods use 1.0. The (1-loss)(1-waste)
    multiplier mirrors the FLW correction that the build_model
    animal_production and food_processing links apply on the production
    side, so consumer-side intake is on the same post-FLW basis as what the
    food bus actually delivers.

    When several override foods share a single FBS item code (e.g.
    dairy/dairy-buffalo both map to 2848 "Milk - Excluding Butter"), the
    FBS supply is split between them by country-level QCL production
    weights (matching the within-FBS-item resolution used for non-override
    foods).
    """
    if not override_foods:
        return result

    result = result.copy()

    # Build food → [item_code, ...] lookup
    map_df = food_item_map_df.copy()
    map_df["item_code"] = pd.to_numeric(map_df["item_code"], errors="coerce")
    map_df = map_df[map_df["item_code"].notna()]
    map_df["item_code"] = map_df["item_code"].astype(int)
    fbs_codes_by_food = map_df.groupby("food")["item_code"].apply(list).to_dict()

    # Reverse lookup: FBS item code → [override foods sharing it]
    code_to_override_foods: dict[int, list[str]] = {}
    for food in override_foods:
        for code in fbs_codes_by_food.get(food, []):
            code_to_override_foods.setdefault(int(code), []).append(food)

    # FBS supply lookup: (country, item_code) → kg/capita/year (carcass weight for meat)
    fbs_supply = fbs_items_df.set_index(["country", "item_code"])[
        "supply_kg_per_capita_year"
    ].to_dict()

    # FLW lookup: (country, food_group) → (loss_fraction, waste_fraction)
    flw_lookup = flw_df.set_index(["country", "food_group"])[
        ["loss_fraction", "waste_fraction"]
    ]

    # QCL production lookups for splitting shared FBS items
    qcl_lookup: dict[str, int] = {}
    if not qcl_resolution_df.empty:
        for _, row in qcl_resolution_df.iterrows():
            qcl_lookup[row["food"]] = int(row["qcl_item_code"])
    crop_prod_lookup = _build_crop_production_lookup(crop_production_df, qcl_lookup)
    animal_prod_lookup = _build_animal_production_lookup(
        animal_production_df, qcl_lookup
    )

    for food in override_foods:
        codes = fbs_codes_by_food.get(food)
        if codes is None:
            logger.warning(
                "FBS override: no FBS item codes for food '%s', skipping", food
            )
            continue

        food_mask = result["food"] == food
        if not food_mask.any():
            logger.warning(
                "FBS override: food '%s' not in baseline diet, skipping", food
            )
            continue

        food_group = result.loc[food_mask, "food_group"].iloc[0]
        c2r = float(carcass_to_retail.get(food, 1.0))
        before_total = result.loc[food_mask, "consumption_g_per_day"].sum()

        countries = result.loc[food_mask, "country"].tolist()
        new_intake = []
        for country in countries:
            supply_kg = 0.0
            for code in codes:
                shared = code_to_override_foods.get(int(code), [food])
                if len(shared) > 1:
                    shares = _resolve_shared_fbs_item(
                        country,
                        shared,
                        qcl_lookup,
                        crop_prod_lookup,
                        animal_prod_lookup,
                    )
                    fbs_share = shares.get(food, 1.0 / len(shared))
                else:
                    fbs_share = 1.0
                supply_kg += fbs_share * fbs_supply.get((country, int(code)), 0.0)

            try:
                loss_frac, waste_frac = flw_lookup.loc[(country, food_group)]
            except KeyError:
                loss_frac, waste_frac = 0.0, 0.0
            flw_mult = (1.0 - float(loss_frac)) * (1.0 - float(waste_frac))

            intake_g_day = supply_kg * c2r * flw_mult * 1000.0 / 365.0
            new_intake.append(intake_g_day)

        result.loc[food_mask, "consumption_g_per_day"] = new_intake

        after_total = result.loc[food_mask, "consumption_g_per_day"].sum()
        logger.info(
            "FBS override: %s — before=%.0f g/day total, after=%.0f g/day total "
            "(c2r=%.2f, %d countries)",
            food,
            before_total,
            after_total,
            c2r,
            int(food_mask.sum()),
        )

    return result


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


def load_direct_food_items(
    dietary_intake_path: str,
    food_groups_df: pd.DataFrame,
    baseline_age: str,
) -> tuple[pd.DataFrame, set[str]]:
    """Extract per-food items from dietary intake data.

    Items in dietary_intake.csv whose name matches a food (not a food group)
    are treated as direct per-food consumption values, bypassing the
    group-total x within-group-share disaggregation.  This applies to foods
    like coffee-green and tea-dried, where GDD provides per-food data directly.

    Returns (direct_foods_df, direct_food_names) where direct_foods_df has
    columns: country, food, food_group, consumption_g_per_day.
    """
    intake = pd.read_csv(dietary_intake_path)
    intake = intake[intake["age"] == baseline_age].copy()

    food_names = set(food_groups_df["food"])
    group_names = set(food_groups_df["group"])
    food_to_group = food_groups_df.set_index("food")["group"].to_dict()

    # Items that are food names (not group names) are direct per-food values
    direct_items = intake[
        intake["item"].isin(food_names) & ~intake["item"].isin(group_names)
    ]

    if direct_items.empty:
        return (
            pd.DataFrame(
                columns=["country", "food", "food_group", "consumption_g_per_day"]
            ),
            set(),
        )

    direct_food_names = set(direct_items["item"].unique())

    result = direct_items.rename(
        columns={"item": "food", "value": "consumption_g_per_day"}
    )
    result["food_group"] = result["food"].map(food_to_group)
    result = result[["country", "food", "food_group", "consumption_g_per_day"]].copy()

    logger.info(
        "Direct per-food items from dietary intake: %s",
        sorted(direct_food_names),
    )

    return result, direct_food_names


def main():
    dietary_intake_path = snakemake.input.dietary_intake
    gbd_exposure_path = snakemake.input.gbd_exposure
    fbs_items_path = snakemake.input.fbs_items
    fbs_cereal_intake_path = snakemake.input.fbs_cereal_intake
    crop_production_path = snakemake.input.crop_production
    animal_production_path = snakemake.input.animal_production
    food_item_map_path = snakemake.input.food_item_map
    qcl_resolution_path = snakemake.input.qcl_resolution
    food_groups_path = snakemake.input.food_groups
    food_loss_waste_path = snakemake.input.food_loss_waste
    output_path = snakemake.output.baseline_diet

    reference_year = int(snakemake.params.reference_year)
    baseline_age = str(snakemake.params.baseline_age)
    food_groups_included = list(snakemake.params.food_groups_included)
    byproducts = list(snakemake.params.byproducts)
    fbs_override_foods = list(snakemake.params.fbs_override_foods)
    carcass_to_retail_meat = {
        str(food): float(factor)
        for food, factor in snakemake.params.carcass_to_retail_meat.items()
    }
    # Food groups for which GBD provides intake exposure data; the
    # baseline-diet anchors to GBD for these (with GDD/FAOSTAT as
    # fallback). Sourced from health.risk_factors so the diet anchor
    # and the health-impact RR machinery never drift on which groups
    # they cover.
    gbd_anchored_groups = {str(g) for g in snakemake.params.gbd_anchored_groups}
    fbs_grain_cfg = dict(snakemake.params.fbs_grain_supplement)
    source_basis = {
        src: {str(g): str(b) for g, b in groups.items()}
        for src, groups in dict(snakemake.params.source_basis).items()
    }
    source_basis_country_overrides = load_source_basis_country_overrides(
        snakemake.input.source_basis_country_overrides
    )
    weight_conversion = {
        str(table): {str(k): float(v) for k, v in entries.items()}
        for table, entries in dict(snakemake.params.weight_conversion).items()
    }

    food_basis = load_food_basis(snakemake.input.food_basis)

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
    flw_df = pd.read_csv(food_loss_waste_path)

    # Build group-basis mapping from food_basis + food_groups
    food_to_group = food_groups_df.set_index("food")["group"].to_dict()
    group_basis_map = build_group_basis(food_basis, food_to_group)

    # Step 1: Food group totals (GBD-anchored for risk groups, GDD/FAOSTAT
    # for everything else).
    logger.info("Step 1: Computing food group totals...")
    group_totals = load_group_totals(
        dietary_intake_path,
        gbd_exposure_path,
        baseline_age,
        food_groups_included,
        gbd_anchored_groups=gbd_anchored_groups,
        source_basis=source_basis,
        source_basis_country_overrides=source_basis_country_overrides,
        group_basis=group_basis_map,
        weight_conversion=weight_conversion,
    )
    logger.info(
        "Group totals: %d countries, %d food groups",
        group_totals["country"].nunique(),
        group_totals["food_group"].nunique(),
    )

    # Step 1b: Optionally backfill refined-grain hole from FBS cereal supply.
    group_totals = apply_fbs_grain_supplement(
        group_totals,
        fbs_cereal_intake_path,
        enabled=bool(fbs_grain_cfg["enabled"]),
        threshold=float(fbs_grain_cfg["threshold"]),
    )

    # Step 1a: Extract direct per-food items (e.g. coffee-green, tea-dried)
    direct_foods, direct_food_names = load_direct_food_items(
        dietary_intake_path, food_groups_df, baseline_age
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
        carcass_to_retail_meat,
    )
    # Exclude direct foods from shares — they don't participate in the
    # group_total x share disaggregation.
    if direct_food_names:
        shares = shares[~shares["food"].isin(direct_food_names)]
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

    # Append direct per-food items
    if not direct_foods.empty:
        result = pd.concat([result, direct_foods], ignore_index=True)

    # Insert placeholder rows for FBS override foods not yet in result
    # (e.g. cocoa-powder when its group has no group-level total)
    fg_map = food_groups_df.set_index("food")["group"].to_dict()
    for food in fbs_override_foods:
        if food not in result["food"].values:
            food_group = fg_map.get(food)
            if food_group is not None:
                countries = result["country"].unique()
                placeholders = pd.DataFrame(
                    {
                        "country": countries,
                        "food": food,
                        "food_group": food_group,
                        "consumption_g_per_day": 0.0,
                    }
                )
                result = pd.concat([result, placeholders], ignore_index=True)

    result = result.sort_values(["country", "food_group", "food"]).reset_index(
        drop=True
    )

    # Step 4: Override specific foods with FBS-supply-anchored intake
    if fbs_override_foods:
        logger.info("Step 4: Applying FBS overrides for %s...", fbs_override_foods)
        result = _apply_fbs_overrides(
            result,
            fbs_items_df,
            food_item_map_df,
            flw_df,
            qcl_resolution_df,
            crop_production_df,
            animal_production_df,
            fbs_override_foods,
            carcass_to_retail_meat,
        )

    # Validation: group sums should match group totals
    _validate_group_sums(result, group_totals)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Summary statistics (uses internal column name)
    _log_summary_stats(result)

    # Rename to make the weight basis explicit on disk: the value is
    # consumer-eaten intake mass (g/day), already net of supply-chain loss
    # and consumer waste — i.e. on the same basis as the food bus delivers
    # mass after the animal_production / food_processing FLW multiplier.
    result.rename(
        columns={"consumption_g_per_day": "consumption_g_per_day_intake"}
    ).to_csv(output_path, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %d foods) to %s",
        len(result),
        result["country"].nunique(),
        result["food"].nunique(),
        output_path,
    )


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
