"""Within-group pooled-projection helpers for baseline-diet attribution.

Defines the per-(food_group, GAEZ-module) pools of FAOSTAT FBS item
codes that ``estimate_baseline_diet`` redistributes across the modelled
foods in each pool, weighted by a per-country/global blended crop-
production share. The same blend is used on the supply side by
``harvested_area_shares`` so demand and supply within-group splits stay
symmetric (see comment block below for the full rationale).

SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

# Demand-side projections that mirror the supply-side GAEZ-RES06 module
# attribution. Each (food_group, module) pool collects all FBS item codes
# whose food maps onto the module, and splits the pooled supply across the
# module's modelled foods by per-country/global crop-production share.
#
# Pooling explicit FBS codes alongside any FBS "Other" residual is what
# makes the demand split symmetric with the supply split. On the supply
# side, each modelled food in a module receives both its FAOSTAT direct
# area and a share of the module's residual raster area. If demand
# instead routed an explicit FBS supply (e.g. onion FBS 2602) entirely to
# one food, the within-module shares would diverge from supply: one food
# absorbs all explicit demand while supply spreads across the module,
# producing systematic per-food slack even when the group balances.
#
# Examples:
#   * OVG (vegetables): onion FBS 2602 + "Vegetables, other" FBS 2605
#     are pooled and split across {onion, cabbage, carrot} (cabbage and
#     carrot have no explicit FBS code; FAOSTAT lumps them under 2605).
#     Tomato is excluded — it has its own TOM raster and its own
#     FBS 2601, so its demand routes via the explicit-supply path.
#   * BAN (fruits): plantain FBS 2616 projects onto banana, matching
#     supply where banana's BAN raster already absorbs plantain area.
#     Banana FBS 2615 stays explicit because banana has its own raster
#     and own FBS code (same shape as tomato).
#   * FRT (fruits): citrus codes (2611-2614), apple FBS 2617, plus the
#     unmodelled-fruit residuals (FBS 2618 pineapples, 2619 dates, 2625
#     "Fruits, other") are pooled and split across {citrus, mango,
#     watermelon, apple}. Mango and watermelon have no explicit FBS
#     code; FAOSTAT lumps them under 2625.
#
# Grapes (FBS 2620) are intentionally excluded: although the FBS "Food"
# element is in principle net of wine processing, most of the world's
# grape harvest enters the alcohol industry which the model does not
# treat as fruit consumption and which GBD's fruits risk factor excludes.

OVG_CROPS: tuple[str, ...] = ("onion", "cabbage", "carrot")
OVG_POOL_ITEM_CODES: tuple[int, ...] = (
    2602,  # Onions (explicit; pooled so onion does not absorb 100% of demand)
    2605,  # Vegetables, Other (residual covering cabbage, carrot, etc.)
)
OVG_COUNTRY_SHARE_BLEND = 0.7

STARCHY_PROJECTION_FOODS: tuple[str, ...] = (
    "potato",
    "sweet-potato",
    "yam",
    "cassava",
)
STARCHY_POOL_ITEM_CODES: tuple[int, ...] = (2534,)  # Roots, Other
STARCHY_COUNTRY_SHARE_BLEND = 0.7

NUTS_PROJECTION_FOODS: tuple[str, ...] = (
    "groundnut",
    "sesame-seed",
    "coconut",
    "sunflower-seed",
)
NUTS_POOL_ITEM_CODES: tuple[int, ...] = (2551,)  # Nuts, Other
NUTS_COUNTRY_SHARE_BLEND = 0.7

FRUITS_BAN_PROJECTION_FOODS: tuple[str, ...] = ("banana",)
FRUITS_BAN_POOL_ITEM_CODES: tuple[int, ...] = (2616,)  # Plantains

FRUITS_FRT_PROJECTION_FOODS: tuple[str, ...] = (
    "citrus",
    "mango",
    "watermelon",
    "apple",
)
FRUITS_FRT_POOL_ITEM_CODES: tuple[int, ...] = (
    2611,  # Oranges, Mandarines (explicit; previously routed to citrus only)
    2612,  # Lemons, Limes (explicit; previously routed to citrus only)
    2613,  # Grapefruit (explicit; previously routed to citrus only)
    2614,  # Citrus, Other (explicit; previously routed to citrus only)
    2617,  # Apples (explicit; previously routed to apple only)
    2618,  # Pineapples (residual)
    2619,  # Dates (residual)
    2625,  # Fruits, Other (residual covering mango, watermelon, etc.)
)
FRUITS_COUNTRY_SHARE_BLEND = 0.7


def build_blended_crop_shares(
    crop_production_df: pd.DataFrame,
    crops: Sequence[str],
    blend_weight: float = OVG_COUNTRY_SHARE_BLEND,
) -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    """Build country-level blended shares for a set of crops.

    Shares blend country-specific production shares with global shares:
    share = blend_weight * country_share + (1 - blend_weight) * global_share
    """
    if not 0.0 <= blend_weight <= 1.0:
        raise ValueError(f"blend_weight must be in [0, 1], got {blend_weight}")

    crops = tuple(str(c).strip() for c in crops)
    if len(crops) == 0:
        raise ValueError("crops cannot be empty")
    crop_set = set(crops)

    df = crop_production_df.copy()
    if df.empty:
        uniform = 1.0 / len(crops)
        return {}, dict.fromkeys(crops, uniform)

    df["country"] = df["country"].astype(str).str.upper().str.strip()
    df["crop"] = df["crop"].astype(str).str.strip()
    df["production_tonnes"] = pd.to_numeric(
        df["production_tonnes"], errors="coerce"
    ).fillna(0.0)
    df = df[df["crop"].isin(crop_set)]

    if df.empty:
        uniform = 1.0 / len(crops)
        return {}, dict.fromkeys(crops, uniform)

    by_country_crop = (
        df.groupby(["country", "crop"], as_index=False)["production_tonnes"]
        .sum()
        .copy()
    )
    pivot = (
        by_country_crop.pivot(
            index="country", columns="crop", values="production_tonnes"
        )
        .fillna(0.0)
        .reindex(columns=list(crops), fill_value=0.0)
    )

    global_totals = pivot.sum(axis=0)
    global_total = float(global_totals.sum())
    if global_total > 0.0:
        global_share = (global_totals / global_total).to_dict()
    else:
        uniform = 1.0 / len(crops)
        global_share = dict.fromkeys(crops, uniform)

    global_series = pd.Series(global_share).reindex(list(crops), fill_value=0.0)
    lookup: dict[tuple[str, str], float] = {}

    for country, row in pivot.iterrows():
        country_total = float(row.sum())
        if country_total > 0.0:
            country_share = row / country_total
            blended = (
                blend_weight * country_share + (1.0 - blend_weight) * global_series
            )
        else:
            blended = global_series.copy()

        total = float(blended.sum())
        if total <= 0.0 or not np.isfinite(total):
            blended = pd.Series(
                {crop: 1.0 / len(crops) for crop in crops},
                index=list(crops),
            )
        else:
            blended = blended / total

        for crop in crops:
            lookup[(country, crop)] = float(blended[crop])

    # Keep global shares keyed by requested crop ordering
    return lookup, {crop: float(global_series[crop]) for crop in crops}
