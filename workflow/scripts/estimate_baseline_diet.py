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
    2. For groups in ``health.risk_factors`` (GBD-anchored), the per-country
       group total is taken from GBD when GBD reports a value, else the
       GDD/FAOSTAT value. No averaging; GBD strictly takes precedence on
       these groups so the baseline aligns with the same intake basis the
       GBD relative-risk functions are calibrated against.
    3. Build within-group food shares from FAOSTAT FBS item-level supply
    4. Resolve shared FBS items using QCL production data
    5. Compute per-food consumption = group_total x within_group_share

Input:
    - dietary_intake.csv: Food group totals (GDD + FAOSTAT, waste-corrected)
    - gbd_food_group_intake.csv: GBD per-country group totals for risk groups
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
    conversion_factor,
    convert_intake,
    load_food_basis,
    load_source_basis_country_overrides,
)
from workflow.scripts.diet.food_group_projection import (
    FRUITS_BAN_POOL_ITEM_CODES,
    FRUITS_BAN_PROJECTION_FOODS,
    FRUITS_COUNTRY_SHARE_BLEND,
    FRUITS_FRT_POOL_ITEM_CODES,
    FRUITS_FRT_PROJECTION_FOODS,
    NUTS_COUNTRY_SHARE_BLEND,
    NUTS_POOL_ITEM_CODES,
    NUTS_PROJECTION_FOODS,
    OVG_COUNTRY_SHARE_BLEND,
    OVG_CROPS,
    OVG_POOL_ITEM_CODES,
    STARCHY_COUNTRY_SHARE_BLEND,
    STARCHY_POOL_ITEM_CODES,
    STARCHY_PROJECTION_FOODS,
    build_blended_crop_shares,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Per-food allocation overrides used when several modeled foods share the
# same QCL bucket and FBS item. Pearl millet (Pennisetum glaucum) dominates
# global millet output, foxtail millet (Setaria italica) accounts for the
# rest; FAO does not break millets down by species, so we use a
# literature-based global split. _resolve_shared_fbs_item looks up the
# absolute share when ALL same-QCL foods are listed here, replacing the
# default equal-split.
WITHIN_QCL_FOOD_SPLITS: dict[str, float] = {
    "pearl-millet": 0.8,
    "foxtail-millet": 0.2,
}


# Per-food-group pooled-projection table used by _project_pooled_supply.
#
# Each entry rebuilds the within-group FBS supply weights for a food
# group as follows. The entry defines one or more sub-projections; each
# sub-projection pools several FBS item codes (``pool_codes``) and
# distributes the pooled supply across a set of modelled foods
# (``projection_foods``) by a per-(country, food) share whose source is
# named by ``share_method`` (see ``_resolve_sub_spec_shares``):
#
#   * ``"blend"`` (default): country/global production-share blend built
#     from FAOSTAT crop production. ``blend_weight`` sets the country
#     weight; ``crop_to_food`` maps FAOSTAT crop names to model food
#     names where they differ.
#   * ``"frt_attribution"``: per-(country, crop) target_production_tonnes
#     read from the supply-side build_frt_area_attribution table, so the
#     demand-side within-pool split mirrors the supply-side attribution
#     exactly.
#
# The split is symmetric with the supply-side GAEZ-RES06 attribution:
# each modelled food's supply comes from FAOSTAT direct area plus a share
# of the module's residual raster area; pooling explicit FBS codes
# alongside the residual FBS code makes the demand-side within-module
# split match the supply-side split.
#
# Spec shape per entry:
#   - ``food_group``: name of the model food group to rebuild.
#   - ``projections``: list of sub-projections, each with the keys named
#     above plus ``share_method`` and its method-specific arguments.
#   For the common single-projection case, the sub-spec fields can be
#   inlined at the top level; ``_normalise_projection_spec`` wraps it
#   into a one-element ``projections`` list at runtime.
#
# Fruits uses two sub-projections so demand attribution mirrors the
# module-aligned supply:
#   * BAN module (blend): plantain FBS 2616 -> banana exclusively.
#     Banana FBS 2615 stays explicit (banana has its own BAN raster).
#   * FRT module + CROPGRIDS apple (frt_attribution): all citrus FBS
#     codes, apple FBS 2617, plus the unmodelled-fruit residual FBS
#     codes (pineapples, dates, "Fruits, other") are pooled and split
#     across citrus, mango, watermelon, and apple using the supply-side
#     target_production_tonnes. This closes the area-vs-production-share
#     asymmetry between FRT supply attribution and the blend-based
#     demand split that would otherwise leave per-fruit slack.
# Grapes (FBS 2620) are intentionally excluded; see diet/food_group_projection.py.
POOL_PROJECTIONS: list[dict[str, object]] = [
    {
        "food_group": "vegetables",
        "pool_codes": OVG_POOL_ITEM_CODES,
        "projection_foods": OVG_CROPS,
        "share_method": "blend",
        "blend_weight": OVG_COUNTRY_SHARE_BLEND,
        "crop_to_food": None,  # FAOSTAT crop names already match foods
    },
    {
        "food_group": "nuts_seeds",
        "pool_codes": NUTS_POOL_ITEM_CODES,
        "projection_foods": NUTS_PROJECTION_FOODS,
        "share_method": "blend",
        "blend_weight": NUTS_COUNTRY_SHARE_BLEND,
        "crop_to_food": {
            "groundnut": "groundnut",
            "sesame": "sesame-seed",
            "coconut": "coconut",
            "sunflower": "sunflower-seed",
        },
    },
    {
        "food_group": "starchy_vegetable",
        "pool_codes": STARCHY_POOL_ITEM_CODES,
        "projection_foods": STARCHY_PROJECTION_FOODS,
        "share_method": "blend",
        "blend_weight": STARCHY_COUNTRY_SHARE_BLEND,
        "crop_to_food": {
            "white-potato": "potato",
            "sweet-potato": "sweet-potato",
            "yam": "yam",
            "cassava": "cassava",
        },
    },
    {
        "food_group": "fruits",
        "projections": [
            {
                "pool_codes": FRUITS_BAN_POOL_ITEM_CODES,
                "projection_foods": FRUITS_BAN_PROJECTION_FOODS,
                "share_method": "blend",
                "blend_weight": FRUITS_COUNTRY_SHARE_BLEND,
                "crop_to_food": None,
            },
            {
                "pool_codes": FRUITS_FRT_POOL_ITEM_CODES,
                "projection_foods": FRUITS_FRT_PROJECTION_FOODS,
                "share_method": "frt_attribution",
            },
        ],
    },
]


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


def apply_cereal_residual_fix(
    group_totals: pd.DataFrame,
    kcal_target_df: pd.DataFrame,
    kcal_per_g_group: dict[str, float],
) -> pd.DataFrame:
    """Reallocate cereal kcal lost to the GBD whole_grain anchor into refined grain.

    GBD's ``whole_grains`` risk factor is defined narrowly (dry whole-
    grain flour). GDD-IA's ``whole_grains`` is broader (any product with
    substantial whole-grain content). When ``load_group_totals`` anchors
    ``whole_grains`` to GBD, ~250 kcal/d of cereal energy can disappear
    (India typical). To preserve the country's cereal energy budget,
    we reassign the deficit to refined ``grain``.

    The deficit is computed against IA's *actual* cereal kcal pool
    (whole_grains kcal + prc_grains kcal) carried via
    ``gdd_ia_kcal_target.csv``, not via the broader nutrition.csv per-
    group density — because IA's whole_grain mass is in a different
    basis (~2.5-3 kcal/g) than the model's dry-flour basis (3.3 kcal/g).

        deficit_kcal = (kcal_whole_grains_ia + kcal_grain_ia)
                       - g_whole_anchored x k_whole_model
        new_g_grain = max(0, deficit_kcal) / k_grain_model
    """
    k_whole = float(kcal_per_g_group.get(WHOLE_GRAINS_GROUP, 3.3))
    k_grain = float(kcal_per_g_group.get(GRAIN_GROUP, 3.6))

    targets = kcal_target_df.set_index("country")
    totals = group_totals.set_index(["country", "food_group"])[
        "group_total_g_per_day"
    ].copy()

    n_fixed = 0
    n_skipped = 0
    total_deficit_kcal = 0.0
    for country in totals.index.get_level_values("country").unique():
        if country not in targets.index:
            n_skipped += 1
            continue
        whole_anchored = float(totals.get((country, WHOLE_GRAINS_GROUP), 0.0) or 0.0)
        ia_cereal_kcal = float(targets.at[country, "kcal_whole_grains_ia"]) + float(
            targets.at[country, "kcal_grain_ia"]
        )
        deficit_kcal = ia_cereal_kcal - whole_anchored * k_whole
        if deficit_kcal <= 0:
            n_skipped += 1
            continue
        new_grain = deficit_kcal / k_grain
        totals.loc[(country, GRAIN_GROUP)] = new_grain
        n_fixed += 1
        total_deficit_kcal += deficit_kcal

    logger.info(
        "Cereal residual fix: set refined-grain to absorb IA cereal kcal "
        "minus anchored whole-grain kcal in %d countries (skipped %d). "
        "Mean refined-grain kcal per fixed country: %.0f kcal/d.",
        n_fixed,
        n_skipped,
        total_deficit_kcal / max(n_fixed, 1),
    )
    return (
        totals.reset_index()
        .sort_values(["country", "food_group"])
        .reset_index(drop=True)
    )


def apply_kcal_normalisation(
    group_totals: pd.DataFrame,
    kcal_target_df: pd.DataFrame,
    gbd_anchored_groups: set[str],
    kcal_per_g_group: dict[str, float],
) -> pd.DataFrame:
    """Anchor-aware kcal normalisation to GDD-IA's country-level target.

    For each country, scale unanchored groups so that total kcal hits
    ``kcal_target_modelled = all-fg - out-of-scope`` while leaving
    GBD-anchored groups (and the refined-grain residual) untouched.

    Anchored groups: those in ``gbd_anchored_groups`` plus ``grain``
    (which is set by the cereal residual fix and shouldn't be rescaled).
    """
    anchored = set(gbd_anchored_groups) | {GRAIN_GROUP}
    totals_idx = group_totals.set_index(["country", "food_group"])[
        "group_total_g_per_day"
    ]
    targets = kcal_target_df.set_index("country")["kcal_target_modelled"]

    rows = []
    skipped_countries = []
    factors = []
    for country in totals_idx.index.get_level_values("country").unique():
        target = float(targets.get(country, float("nan")))
        country_totals = totals_idx.xs(country, level="country")
        if pd.isna(target) or target <= 0:
            skipped_countries.append(country)
            for fg, g in country_totals.items():
                rows.append(
                    {
                        "country": country,
                        "food_group": fg,
                        "group_total_g_per_day": float(g),
                    }
                )
            continue

        kcal_anchored = 0.0
        kcal_unanchored = 0.0
        for fg, g in country_totals.items():
            kpg = float(kcal_per_g_group.get(fg, 0.0))
            kcal = float(g) * kpg
            if fg in anchored:
                kcal_anchored += kcal
            else:
                kcal_unanchored += kcal

        target_unanchored = target - kcal_anchored
        if kcal_unanchored <= 0 or target_unanchored <= 0:
            factor = 1.0
        else:
            factor = target_unanchored / kcal_unanchored
            factor = max(0.1, min(5.0, factor))
        factors.append(factor)

        for fg, g in country_totals.items():
            new_g = float(g) if fg in anchored else float(g) * factor
            rows.append(
                {"country": country, "food_group": fg, "group_total_g_per_day": new_g}
            )

    if skipped_countries:
        logger.warning(
            "kcal normalisation: %d countries had no kcal target; passed "
            "through unchanged: %s",
            len(skipped_countries),
            ", ".join(sorted(skipped_countries)[:8])
            + ("…" if len(skipped_countries) > 8 else ""),
        )

    if factors:
        ser = pd.Series(factors)
        logger.info(
            "kcal normalisation: unanchored scaling factor "
            "mean=%.3f std=%.3f range=[%.3f, %.3f] (n=%d)",
            ser.mean(),
            ser.std(),
            ser.min(),
            ser.max(),
            len(ser),
        )

    return (
        pd.DataFrame(rows).sort_values(["country", "food_group"]).reset_index(drop=True)
    )


def build_kcal_per_g_group(
    food_groups_df: pd.DataFrame,
    nutrition_df: pd.DataFrame,
) -> dict[str, float]:
    """Global per-group kcal/g from nutrition.csv averaged over the foods
    in each group. Used by the cereal residual fix and kcal normalisation.

    For dairy specifically we override to the cow-milk density (0.607
    kcal/g) so the mass is interpreted as strict milk-equivalent.
    """
    kcal_per_100g = nutrition_df[nutrition_df["nutrient"] == "cal"].set_index("food")[
        "value"
    ]
    fg = food_groups_df.merge(
        kcal_per_100g.rename("kcal_per_100g").reset_index(),
        on="food",
        how="left",
    )
    out = (fg.groupby("group")["kcal_per_100g"].mean() / 100.0).to_dict()
    out["dairy"] = 0.607  # cow-milk density for strict milk-equivalent
    return out


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


def cap_buffalo_share_at_production(
    shares_df: pd.DataFrame,
    group_totals: pd.DataFrame,
    animal_production_df: pd.DataFrame,
    population_df: pd.DataFrame,
    flw_df: pd.DataFrame,
) -> pd.DataFrame:
    """Cap dairy-buffalo within-group share at country's domestic buffalo production.

    Buffalo milk has very limited international trade — production is
    concentrated in South Asia and most output is consumed within the
    producing country. The default within-group split allocates a country's
    GBD-anchored dairy intake to cow vs buffalo by QCL production share,
    which over-allocates buffalo demand wherever total dairy intake exceeds
    domestic milk production (PAK is the textbook case): the gap shows up
    as buffalo shortage at solve, with no global buffalo to import.

    The fix is to assume that internationally-tradeable cow milk fills any
    marginal demand a country cannot meet from local buffalo. For each
    country, when the buffalo food-bus demand
    ``buffalo_share * dairy_intake / (1 - dairy_waste)`` exceeds domestic
    buffalo production, the buffalo share is capped so the food-bus
    demand exactly matches production, and the freed share is reassigned
    to dairy (cow). Countries whose buffalo demand already fits within
    production are untouched.

    The cap is conservative — it ignores the small (~few Mt globally)
    buffalo trade out of India / Nepal / Egypt — but the rounding error is
    much smaller than the shortage it closes.
    """
    pop_lookup = population_df.set_index("iso3")["population"].to_dict()
    ap = animal_production_df[animal_production_df["product"] == "dairy-buffalo"]
    buffalo_prod = ap.set_index("country")["production_mt_fresh_retail"].to_dict()
    group_total_lookup = (
        group_totals[group_totals["food_group"] == "dairy"]
        .set_index("country")["group_total_g_per_day"]
        .to_dict()
    )
    waste_lookup = (
        flw_df[flw_df["food_group"] == "dairy"]
        .set_index("country")["waste_fraction"]
        .to_dict()
    )

    out = shares_df.copy()
    log_rows: list[tuple] = []
    for country, group_total_g_per_day in group_total_lookup.items():
        mask_buf = (out["country"] == country) & (out["food"] == "dairy-buffalo")
        if not mask_buf.any():
            continue
        share_buf = float(out.loc[mask_buf, "share"].iloc[0])
        if share_buf <= 0.0:
            continue
        pop = float(pop_lookup.get(country, 0.0))
        if pop <= 0.0 or group_total_g_per_day <= 0:
            continue
        # Intake mass in Mt/year (g/day * persons * 365 / 1e12).
        intake_total_mt = group_total_g_per_day * pop * 365.0 / 1e12
        # Translate to food-bus demand (consume.p_set in the model):
        # the build step inflates intake by 1/(1-waste) so the
        # consumer-eaten share lands at the GBD-anchored intake.
        waste = float(waste_lookup.get(country, 0.0))
        if not 0.0 <= waste < 1.0:
            waste = 0.0
        demand_buf_mt = share_buf * intake_total_mt / (1.0 - waste)
        supply_buf_mt = float(buffalo_prod.get(country, 0.0))
        if demand_buf_mt <= supply_buf_mt:
            continue
        max_share_buf = supply_buf_mt * (1.0 - waste) / intake_total_mt
        shift = share_buf - max_share_buf
        out.loc[mask_buf, "share"] = max_share_buf
        mask_cow = (out["country"] == country) & (out["food"] == "dairy")
        if mask_cow.any():
            out.loc[mask_cow, "share"] += shift
        log_rows.append(
            (country, share_buf, max_share_buf, demand_buf_mt, supply_buf_mt)
        )

    if log_rows:
        logger.info(
            "Buffalo share capped in %d countries (food-bus demand > production); "
            "excess reassigned to dairy (cow). Top by absolute shift:",
            len(log_rows),
        )
        for c, old, new, d, s in sorted(
            log_rows, key=lambda r: r[3] - r[4], reverse=True
        )[:8]:
            logger.info(
                "  %s: %.2f→%.2f (food-bus demand %.1f Mt > buffalo prod %.1f Mt)",
                c,
                old,
                new,
                d,
                s,
            )
    return out


def build_within_group_shares(
    food_groups_df: pd.DataFrame,
    food_item_map_df: pd.DataFrame,
    fbs_items_df: pd.DataFrame,
    qcl_resolution_df: pd.DataFrame,
    crop_production_df: pd.DataFrame,
    animal_production_df: pd.DataFrame,
    food_groups_included: list[str],
    byproducts: list[str],
    weight_conversion: dict[str, dict[str, float]],
    frt_attribution_df: pd.DataFrame,
    edible_portion_by_food: dict[str, float],
) -> pd.DataFrame:
    """Build within-group food shares per country from FAOSTAT data.

    ``frt_attribution_df`` carries the supply-side per-(country, crop)
    FRT target_production_tonnes table from ``build_frt_area_attribution``;
    it is consumed by the fruits FRT sub-projection so demand attribution
    mirrors supply attribution exactly (see ``_project_pooled_supply``).

    ``edible_portion_by_food`` carries the edible-mass fraction of fresh
    commodity weight for each food (default 1.0 for foods absent from the
    mapping, e.g. processed products like flour and oil where the entire
    commodity is consumed). FBS food-supply is reported in commodity
    weight (whole fruit, post-supply-chain-loss) while GDD-anchored group
    totals are on edible-portion basis. The two are reconciled here by
    multiplying every per-FBS-item supply by the food's edible portion
    before aggregating into the within-group share, so the EDIBLE group
    total is split across foods on an EDIBLE-weighted basis. Without this
    rescaling, low-edible-portion foods (plantain at 0.59, watermelon at
    0.52, citrus at ~0.59) absorb a disproportionately large share of
    their group's intake total.

    Returns DataFrame with columns: country, food, food_group, share
    """
    # Build food → food_group mapping
    fg_map = food_groups_df.set_index("food")["group"].to_dict()

    # Build food → [FBS item_code, ...] mapping.
    # Some foods (e.g., citrus) are represented by multiple FAOSTAT items.
    fbs_codes_by_food = _build_food_to_fbs_codes(food_item_map_df)

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

    qcl_lookup = _build_qcl_lookup(qcl_resolution_df)

    # Get all countries from FBS data
    countries = sorted(fbs_items_df["country"].unique())

    # Build FBS supply lookup: (country, item_code) → supply_kg
    fbs_supply = fbs_items_df.set_index(["country", "item_code"])[
        "supply_kg_per_capita_year"
    ].to_dict()

    # Fail fast if any pooled projection references an FBS code that the
    # FBS fetch never pulled. This catches silent drift between
    # ``POOL_PROJECTIONS`` here and the fetch list in
    # ``prepare_faostat_fbs_items.py`` (which historically defaulted such
    # codes to 0 supply and made entire pools — plantain, apples,
    # pineapples, dates — silently vanish from the projection).
    fetched_codes = {int(code) for (_, code) in fbs_supply}
    referenced_codes: set[int] = set()
    for spec in POOL_PROJECTIONS:
        for sub in _normalise_projection_spec(spec):
            referenced_codes.update(int(c) for c in sub["pool_codes"])
    missing_codes = referenced_codes - fetched_codes
    if missing_codes:
        raise ValueError(
            "POOL_PROJECTIONS reference FBS codes that were not fetched "
            f"by prepare_faostat_fbs_items: {sorted(missing_codes)}. "
            "Either add them to data/curated/faostat_food_item_map.csv or to "
            "POOL_FETCH_CODES in prepare_faostat_fbs_items.py."
        )

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

            # Multiple foods share this FBS item → resolve via QCL production.
            # food_split_overrides handles same-QCL-bucket cases that
            # FAOSTAT cannot disambiguate (e.g. pearl- vs foxtail-millet).
            shares = _resolve_shared_fbs_item(
                country,
                foods,
                qcl_lookup,
                crop_prod_lookup,
                animal_prod_lookup,
                food_split_overrides=WITHIN_QCL_FOOD_SPLITS,
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

    # Convert from per-FBS-item shares to per-food-group shares.
    # Each row carries an FBS item code. Weight by item-level supply, then
    # aggregate back to (country, food, food_group).
    shares_df["fbs_supply_kg"] = shares_df.apply(
        lambda r: fbs_supply.get((r["country"], int(r["fbs_item_code"])), 0.0),
        axis=1,
    )
    # Convert FBS meat supply (carcass basis) to retail-equivalent mass so
    # within-group shares are consistent with model meat units. Foods not
    # listed in carcass_to_fresh pass through with factor 1.0.
    shares_df["carcass_to_retail_factor"] = shares_df["food"].map(
        lambda f: conversion_factor("carcass", "fresh", f, weight_conversion)
    )
    shares_df["fbs_supply_kg"] = (
        shares_df["fbs_supply_kg"] * shares_df["carcass_to_retail_factor"]
    )
    # Rescale to edible-mass basis so within-group shares match the
    # GDD-anchored group totals (see docstring).
    shares_df["edible_portion"] = (
        shares_df["food"].map(edible_portion_by_food).fillna(1.0).clip(lower=1e-6)
    )
    shares_df["fbs_supply_kg"] = (
        shares_df["fbs_supply_kg"] * shares_df["edible_portion"]
    )
    shares_df["supply_weight"] = shares_df["share"] * shares_df["fbs_supply_kg"]
    shares_df = (
        shares_df.groupby(["country", "food_group", "food"], as_index=False)[
            "supply_weight"
        ]
        .sum()
        .copy()
    )
    for spec in POOL_PROJECTIONS:
        sub_specs = _normalise_projection_spec(spec)
        shares_df = _project_pooled_supply(
            shares_df,
            food_group=spec["food_group"],
            sub_specs=sub_specs,
            included_foods=included_foods,
            fg_map=fg_map,
            fbs_codes_by_food=fbs_codes_by_food,
            fbs_supply=fbs_supply,
            countries=countries,
            crop_production_df=crop_production_df,
            frt_attribution_df=frt_attribution_df,
            edible_portion_by_food=edible_portion_by_food,
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


def _normalise_projection_spec(spec: dict) -> list[dict]:
    """Return the list of sub-projections for a POOL_PROJECTIONS entry.

    Accepts the long form (``projections`` is a list of sub-projection
    dicts) or the short form (a single sub-projection inlined at the top
    level). The short form is wrapped in a one-element list so callers
    can iterate uniformly.
    """
    if "projections" in spec:
        return list(spec["projections"])
    sub = {k: v for k, v in spec.items() if k != "food_group"}
    sub["pool_codes"] = tuple(int(c) for c in sub["pool_codes"])
    return [sub]


def _resolve_sub_spec_shares(
    sub: dict,
    active: list[str],
    crop_production_df: pd.DataFrame,
    frt_attribution_df: pd.DataFrame,
) -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    """Dispatch to the share-source named by ``sub["share_method"]``.

    Returns ``(per_country_share_lookup, global_share)`` in the shape
    used by ``_project_pooled_supply``. The default method "blend"
    computes a country/global production-share blend from FAOSTAT crop
    production. "frt_attribution" instead reads the supply-side
    target_production_tonnes table emitted by build_frt_area_attribution,
    so that the demand-side within-pool split exactly mirrors the
    supply-side attribution. Pass blend_weight=1.0 inside the attribution
    branch to keep per-country shares pure (global fallback only when a
    country reports no FRT target).
    """
    method = sub.get("share_method", "blend")
    if method == "blend":
        crop_to_food = sub.get("crop_to_food")
        if crop_to_food is None:
            df = crop_production_df
        else:
            df = crop_production_df.copy()
            df["crop"] = df["crop"].astype(str).str.strip().map(crop_to_food)
            df = df[df["crop"].notna()]
        return build_blended_crop_shares(
            df, active, blend_weight=float(sub["blend_weight"])
        )
    if method == "frt_attribution":
        return build_blended_crop_shares(
            frt_attribution_df,
            active,
            blend_weight=1.0,
            value_column="target_production_tonnes",
        )
    raise ValueError(
        f"Unknown share_method {method!r} in POOL_PROJECTIONS sub-spec; "
        "expected 'blend' or 'frt_attribution'."
    )


def _project_pooled_supply(
    shares_df: pd.DataFrame,
    *,
    food_group: str,
    sub_specs: list[dict],
    included_foods: list[str],
    fg_map: dict[str, str],
    fbs_codes_by_food: dict[str, list[int]],
    fbs_supply: dict[tuple[str, int], float],
    countries: list[str],
    crop_production_df: pd.DataFrame,
    frt_attribution_df: pd.DataFrame,
    edible_portion_by_food: dict[str, float],
) -> pd.DataFrame:
    """Rebuild within-group supply weights via pooled FBS projection.

    For each food in ``food_group``, ``supply_weight`` becomes the sum of:

      * **Explicit FBS supply** — supplies of the food's FBS codes that
        are *not* part of any sub-spec's pool. Foods whose codes all
        appear in a pool contribute zero here; their entire demand
        flows through the pool.
      * **One projected contribution per sub-spec** — for each sub-spec
        whose ``projection_foods`` includes this food, a share of the
        pooled FBS supply (sum of ``pool_codes`` supplies), weighted by
        the per-(country, food) share computed by the sub-spec's
        ``share_method``.

    Both contributions are then multiplied by the recipient food's
    edible portion. This keeps the within-group weights on the same
    edible-mass basis as the GDD-anchored group totals, matching the
    rescaling applied in the non-pooled branch of
    ``build_within_group_shares``. The edible portion follows the
    recipient food, not the source FBS item — in the BAN sub-projection,
    for instance, plantain FBS supply is redistributed to banana, and
    banana's edible portion (not plantain's) is applied.

    Splitting a group's pool across several sub-specs lets demand
    attribution mirror GAEZ-module-aligned supply attribution. For
    fruits we project plantain (FBS 2616) only onto banana (BAN raster)
    and the citrus / apple / pineapple / dates / fruits-other pool onto
    citrus, mango, watermelon, and apple (FRT raster + CROPGRIDS apple).

    Each sub-spec carries:
      - ``pool_codes`` (tuple[int]): FBS items whose supplies are pooled.
      - ``projection_foods`` (sequence[str]): modelled foods that absorb
        the pool.
      - ``share_method`` (str): "blend" (default) computes a
        country/global production-share blend from
        ``crop_production_df``; "frt_attribution" reads the supply-side
        target_production_tonnes from ``frt_attribution_df`` so demand
        attribution mirrors the supply attribution exactly.
      - ``blend_weight`` (float, ``share_method="blend"`` only):
        country/global blend weight.
      - ``crop_to_food`` (dict|None, ``share_method="blend"`` only):
        FAOSTAT-crop-name -> model-food rename (None when names match).
    """
    group_foods = [food for food in included_foods if fg_map.get(food) == food_group]
    if not group_foods:
        return shares_df

    # Pre-resolve each sub-spec: per-(country, food) share lookups and
    # the set-union of pool codes (used to filter the explicit-supply
    # path so a food whose FBS code lives in the pool doesn't
    # double-count).
    sub_resolved: list[dict] = []
    all_pool_codes: set[int] = set()
    for sub in sub_specs:
        codes = tuple(int(c) for c in sub["pool_codes"])
        all_pool_codes.update(codes)
        active = [f for f in sub["projection_foods"] if f in group_foods]
        if not active:
            sub_resolved.append(
                {"codes": codes, "active": [], "share": {}, "global": {}}
            )
            continue

        share_lookup, global_share = _resolve_sub_spec_shares(
            sub, active, crop_production_df, frt_attribution_df
        )
        sub_resolved.append(
            {
                "codes": codes,
                "active": active,
                "share": share_lookup,
                "global": global_share,
            }
        )

    if not any(sub["active"] for sub in sub_resolved):
        return shares_df

    rebuilt_rows: list[dict[str, object]] = []
    for country in countries:
        for food in group_foods:
            explicit_codes = [
                int(code)
                for code in fbs_codes_by_food.get(food, [])
                if int(code) not in all_pool_codes
            ]
            explicit_supply = sum(
                fbs_supply.get((country, code), 0.0) for code in explicit_codes
            )
            projected_supply = 0.0
            for sub in sub_resolved:
                if food not in sub["active"]:
                    continue
                pool_supply = sum(
                    fbs_supply.get((country, code), 0.0) for code in sub["codes"]
                )
                if pool_supply <= 0.0:
                    continue
                projected_supply += pool_supply * sub["share"].get(
                    (country, food), sub["global"].get(food, 0.0)
                )
            edible = float(edible_portion_by_food.get(food, 1.0))
            rebuilt_rows.append(
                {
                    "country": country,
                    "food_group": food_group,
                    "food": food,
                    "supply_weight": float(
                        (explicit_supply + projected_supply) * edible
                    ),
                }
            )

    rebuilt = pd.DataFrame(rebuilt_rows)
    other = shares_df[shares_df["food_group"] != food_group]
    return pd.concat([other, rebuilt], ignore_index=True)


def _build_qcl_lookup(qcl_resolution_df: pd.DataFrame) -> dict[str, int]:
    """Build food → QCL item code lookup from the resolution CSV."""
    if qcl_resolution_df.empty:
        return {}
    return {
        str(row["food"]): int(row["qcl_item_code"])
        for _, row in qcl_resolution_df.iterrows()
    }


def _build_food_to_fbs_codes(food_item_map_df: pd.DataFrame) -> dict[str, list[int]]:
    """Build food → sorted unique FBS item codes lookup."""
    df = food_item_map_df.copy()
    df["food"] = df["food"].astype(str)
    df["item_code"] = pd.to_numeric(df["item_code"], errors="coerce")
    df = df[df["item_code"].notna()]
    df["item_code"] = df["item_code"].astype(int)
    return (
        df.groupby("food")["item_code"]
        .apply(lambda s: sorted(set(s.tolist())))
        .to_dict()
    )


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
    food_split_overrides: dict[str, float] | None = None,
) -> dict[str, float]:
    """Resolve shares among foods sharing a single FBS item using QCL production.

    Foods are first grouped by their QCL item code. The shared-FBS supply is
    split between QCL buckets in proportion to their country-level
    production (crop production first, animal production as fallback);
    within a QCL bucket the default is an equal split.

    *food_split_overrides* lets callers pin the within-bucket split when
    several modeled foods land in the same QCL bucket and there is no
    country-level production data to separate them (e.g. pearl- vs
    foxtail-millet, both QCL "Millet"). It maps food name → absolute
    share of its bucket; the overrides are honoured only when ALL foods
    in the bucket are present in the map and their weights sum to 1.

    Falls back to equal split among all foods if no production data
    resolves the QCL buckets at all.
    """
    food_split_overrides = food_split_overrides or {}

    qcl_code_to_foods: dict[int, list[str]] = {}
    unresolved_foods: list[str] = []
    for food in foods:
        qcl_code = qcl_lookup.get(food)
        if qcl_code is not None:
            qcl_code_to_foods.setdefault(qcl_code, []).append(food)
        else:
            unresolved_foods.append(food)

    productions: dict[int, float] = {}
    for qcl_code in qcl_code_to_foods:
        prod = crop_prod_lookup.get((country, qcl_code), 0.0)
        if prod == 0.0:
            prod = animal_prod_lookup.get((country, qcl_code), 0.0)
        productions[qcl_code] = prod
    total_production = sum(productions.values())

    def _within_bucket_split(bucket_foods: list[str]) -> dict[str, float]:
        """Return weights summing to 1.0 across bucket_foods."""
        if all(f in food_split_overrides for f in bucket_foods):
            weights = {f: float(food_split_overrides[f]) for f in bucket_foods}
            total = sum(weights.values())
            if abs(total - 1.0) > 1e-9:
                # Normalize to be defensive; the table itself should sum to 1.
                weights = {f: w / total for f, w in weights.items()}
            return weights
        equal = 1.0 / len(bucket_foods)
        return {f: equal for f in bucket_foods}

    shares: dict[str, float] = {}
    if total_production > 0:
        for qcl_code, bucket in qcl_code_to_foods.items():
            bucket_share = productions[qcl_code] / total_production
            for food, w in _within_bucket_split(bucket).items():
                shares[food] = bucket_share * w
    else:
        # No production data: equal split across all QCL-mapped foods, then
        # unresolved foods absorb the remainder. Within-bucket overrides
        # still apply so pearl/foxtail land at 0.8/0.2 of their joint share.
        n_total = sum(len(b) for b in qcl_code_to_foods.values()) + len(
            unresolved_foods
        )
        if n_total == 0:
            return {}
        for bucket in qcl_code_to_foods.values():
            bucket_share = len(bucket) / n_total
            for food, w in _within_bucket_split(bucket).items():
                shares[food] = bucket_share * w

    if unresolved_foods:
        assigned = sum(shares.values())
        remainder = max(0.0, 1.0 - assigned)
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
    food_basis: dict[str, str],
    fbs_source_basis: dict[str, str],
    weight_conversion: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Override per-food consumption with FBS-supply-anchored intake.

    For each override food, computes per-country intake mass as:

        intake_g_day = FBS_supply_kg_per_capita_year
                       * within_FBS_item_share
                       * basis_factor
                       * (1 - waste_fraction)
                       * 1000 / 365

    The FAOSTAT FBS "Food supply" element is already net of supply-chain
    and post-harvest losses (production - feed - seed - processing - other
    - losses = food); only consumer-level waste needs to be deducted to
    land at consumer-eaten intake. ``basis_factor`` converts between the
    FBS item's native mass basis (declared in ``fbs_source_basis``) and
    the model's food basis (``food_basis``) — e.g. FBS meats are in
    carcass weight and convert to fresh/retail via
    ``weight_conversion.carcass_to_fresh``; FBS tea is in green-leaf and
    converts to dry via ``weight_conversion.fresh_to_dry``. Foods whose
    FBS basis is not declared pass through with factor 1.0.

    When several override foods share a single FBS item code (e.g.
    dairy/dairy-buffalo both map to 2848 "Milk - Excluding Butter"), the
    FBS supply is split between them by country-level QCL production
    weights (matching the within-FBS-item resolution used for non-override
    foods).
    """
    if not override_foods:
        return result

    result = result.copy()

    fbs_codes_by_food = _build_food_to_fbs_codes(food_item_map_df)

    # Reverse lookup: FBS item code → [override foods sharing it]
    code_to_override_foods: dict[int, list[str]] = {}
    for food in override_foods:
        for code in fbs_codes_by_food.get(food, []):
            code_to_override_foods.setdefault(int(code), []).append(food)

    # FBS supply lookup: (country, item_code) → kg/capita/year (carcass weight for meat)
    fbs_supply = fbs_items_df.set_index(["country", "item_code"])[
        "supply_kg_per_capita_year"
    ].to_dict()

    # FLW lookup: (country, food_group) → waste_fraction.
    # FBS-supply-anchored intake only needs the consumer-waste deduction;
    # loss is already absorbed in the FBS "Food supply" element.
    flw_lookup = flw_df.set_index(["country", "food_group"])["waste_fraction"]

    # QCL production lookups for splitting shared FBS items
    qcl_lookup = _build_qcl_lookup(qcl_resolution_df)
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
        src_basis = fbs_source_basis.get(food)
        tgt_basis = food_basis.get(food)
        if src_basis is None or tgt_basis is None or src_basis == tgt_basis:
            basis_factor = 1.0
        else:
            basis_factor = conversion_factor(
                src_basis, tgt_basis, food, weight_conversion
            )
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
                        food_split_overrides=WITHIN_QCL_FOOD_SPLITS,
                    )
                    fbs_share = shares.get(food, 1.0 / len(shared))
                else:
                    fbs_share = 1.0
                supply_kg += fbs_share * fbs_supply.get((country, int(code)), 0.0)

            # FBS supply is already post-loss; only deduct consumer waste.
            waste_frac = float(flw_lookup.get((country, food_group), 0.0))
            intake_g_day = (
                supply_kg * basis_factor * (1.0 - waste_frac) * 1000.0 / 365.0
            )
            new_intake.append(intake_g_day)

        result.loc[food_mask, "consumption_g_per_day"] = new_intake

        after_total = result.loc[food_mask, "consumption_g_per_day"].sum()
        logger.info(
            "FBS override: %s — before=%.0f g/day total, after=%.0f g/day total "
            "(basis=%s→%s factor=%.3f, %d countries)",
            food,
            before_total,
            after_total,
            src_basis or "—",
            tgt_basis or "—",
            basis_factor,
            int(food_mask.sum()),
        )

    return result


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
    kcal_target_path = snakemake.input.kcal_target
    nutrition_path = snakemake.input.nutrition
    crop_production_path = snakemake.input.crop_production
    animal_production_path = snakemake.input.animal_production
    frt_attribution_path = snakemake.input.frt_attribution
    population_path = snakemake.input.population
    food_item_map_path = snakemake.input.food_item_map
    qcl_resolution_path = snakemake.input.qcl_resolution
    food_groups_path = snakemake.input.food_groups
    food_loss_waste_path = snakemake.input.food_loss_waste
    edible_portion_path = snakemake.input.edible_portion
    foods_path = snakemake.input.foods
    output_path = snakemake.output.baseline_diet

    reference_year = int(snakemake.params.reference_year)
    baseline_age = str(snakemake.params.baseline_age)
    food_groups_included = list(snakemake.params.food_groups_included)
    byproducts = list(snakemake.params.byproducts)
    fbs_override_foods = list(snakemake.params.fbs_override_foods)
    # Food groups for which GBD provides intake exposure data; the
    # baseline-diet anchors to GBD for these. Sourced from
    # health.risk_factors so the diet anchor and the health-impact RR
    # machinery never drift on which groups they cover.
    gbd_anchored_groups = {str(g) for g in snakemake.params.gbd_anchored_groups}
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
    frt_attribution_df = pd.read_csv(frt_attribution_path)
    population_df = pd.read_csv(population_path)
    flw_df = pd.read_csv(food_loss_waste_path)

    # Build food -> edible-portion mapping by tracing each food back to its
    # source crop via foods.csv (the food_processing pathway table). FBS
    # food-supply is reported in fresh whole-commodity weight; the GDD-based
    # group totals are on edible-portion basis. Within-group shares must
    # therefore weight FBS supply by the food's edible portion so the
    # split of an edible group total is itself edible-weighted. Foods
    # without a crop link or without an edible-portion entry default to 1.0
    # (processed foods like flour and oil consume the whole commodity by
    # convention, and meat is already on retail basis after the carcass-
    # to-retail conversion applied earlier).
    edible_portion_df = pd.read_csv(edible_portion_path)
    edible_by_crop = dict(
        zip(
            edible_portion_df["crop"].astype(str),
            edible_portion_df["edible_portion_coefficient"].astype(float),
        )
    )
    foods_df = pd.read_csv(foods_path, comment="#")
    food_to_crop = dict(zip(foods_df["food"].astype(str), foods_df["crop"].astype(str)))
    edible_portion_by_food: dict[str, float] = {}
    for food in food_groups_df["food"].astype(str):
        crop = food_to_crop.get(food)
        if crop is None:
            continue
        if crop in edible_by_crop:
            edible_portion_by_food[food] = float(edible_by_crop[crop])

    # Build group-basis mapping from food_basis + food_groups
    food_to_group = food_groups_df.set_index("food")["group"].to_dict()
    group_basis_map = build_group_basis(food_basis, food_to_group)

    # Step 1: Food group totals (GBD-anchored for risk groups, GDD-IA
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

    # Step 1b: Cereal residual fix — when GBD's narrow whole_grain anchor
    # discards cereal kcal from the GDD-IA source, reassign that deficit
    # to refined grain. Uses IA's actual cereal kcal pool carried in
    # gdd_ia_kcal_target.csv.
    kcal_target_df = pd.read_csv(kcal_target_path)
    nutrition_df = pd.read_csv(nutrition_path)
    kcal_per_g_group = build_kcal_per_g_group(food_groups_df, nutrition_df)
    group_totals = apply_cereal_residual_fix(
        group_totals,
        kcal_target_df,
        kcal_per_g_group,
    )

    # Step 1c: Anchor-aware kcal normalisation to GDD-IA's country-level
    # target (all-fg minus out-of-scope categories). Scales unanchored
    # groups so total kcal lands on target; GBD-anchored values and the
    # refined-grain residual are preserved.
    group_totals = apply_kcal_normalisation(
        group_totals,
        kcal_target_df,
        gbd_anchored_groups=gbd_anchored_groups,
        kcal_per_g_group=kcal_per_g_group,
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
        weight_conversion,
        frt_attribution_df,
        edible_portion_by_food,
    )
    # Cap dairy-buffalo shares at domestic production so countries
    # whose total milk intake exceeds production (PAK is the headline
    # case) don't accumulate unrelievable buffalo shortage at solve.
    shares = cap_buffalo_share_at_production(
        shares, group_totals, animal_production_df, population_df, flw_df
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

    # Insert placeholder rows for FBS override foods missing from result for
    # any country. The check is per-(food, country): an override food may be
    # partially present (e.g. meat-chicken via NHANES for USA) while other
    # countries lack the underlying group total in dietary_intake (GDD has
    # no poultry variable). Without per-country placeholders the FBS override
    # loop, which iterates only existing rows, would silently leave those
    # countries at zero intake despite valid FBS Poultry Meat supply.
    fg_map = food_groups_df.set_index("food")["group"].to_dict()
    all_countries = result["country"].unique()
    for food in fbs_override_foods:
        food_group = fg_map.get(food)
        if food_group is None:
            continue
        existing = set(result.loc[result["food"] == food, "country"])
        missing = [c for c in all_countries if c not in existing]
        if not missing:
            continue
        placeholders = pd.DataFrame(
            {
                "country": missing,
                "food": food,
                "food_group": food_group,
                "consumption_g_per_day": 0.0,
            }
        )
        result = pd.concat([result, placeholders], ignore_index=True)

    result = result.sort_values(["country", "food_group", "food"]).reset_index(
        drop=True
    )

    # Validation: shares x group totals should reconstruct the group totals.
    # Run BEFORE the FBS override so that override-driven deviations from
    # the share-based estimate don't drown the signal.
    _validate_group_sums(result, group_totals, exclude_foods=set(fbs_override_foods))

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
            food_basis,
            source_basis.get("faostat_fbs_supply", {}),
            weight_conversion,
        )

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


def _validate_group_sums(
    result: pd.DataFrame,
    group_totals: pd.DataFrame,
    *,
    exclude_foods: set[str] | None = None,
) -> None:
    """Validate that within-group sums match group totals.

    *exclude_foods* lets callers drop foods that are about to be replaced
    by an FBS-supply-anchored override (those rows still carry the
    share-based value at validation time, but the FBS override will
    overwrite them and is not expected to mass-balance against the
    survey-derived group total).
    """
    df = result
    if exclude_foods:
        df = df[~df["food"].isin(exclude_foods)]
    computed_sums = (
        df.groupby(["country", "food_group"])["consumption_g_per_day"]
        .sum()
        .reset_index()
        .rename(columns={"consumption_g_per_day": "computed_sum"})
    )
    # Drop groups whose foods are entirely override foods (computed_sum=0
    # is meaningless once the override fires).
    if exclude_foods:
        kept = df.groupby(["country", "food_group"])["food"].count().reset_index()
        kept = kept[kept["food"] > 0][["country", "food_group"]]
        computed_sums = computed_sums.merge(kept, on=["country", "food_group"])
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
