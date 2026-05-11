"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

OVG_CROPS: tuple[str, ...] = ("onion", "cabbage", "carrot")
VEGETABLE_RESIDUAL_ITEM_CODE = 2605
OVG_COUNTRY_SHARE_BLEND = 0.7
STARCHY_PROJECTION_FOODS: tuple[str, ...] = (
    "potato",
    "sweet-potato",
    "yam",
    "cassava",
)
STARCHY_RESIDUAL_ITEM_CODE = 2534
STARCHY_COUNTRY_SHARE_BLEND = 0.7
NUTS_PROJECTION_FOODS: tuple[str, ...] = (
    "groundnut",
    "sesame-seed",
    "coconut",
    "sunflower-seed",
)
NUTS_RESIDUAL_ITEM_CODE = 2551
NUTS_COUNTRY_SHARE_BLEND = 0.7

# Modeled fruits absorb FBS supply from unmodeled fruit items so the
# GBD-anchored fruits group total stays consistent with the model. The
# projection is split along GAEZ RES06 module boundaries so that supply
# (which is allocated per module) and demand (this projection) attribute
# the same unmodeled fruits in parallel, avoiding the "banana competes
# with apples on the demand side but not on the supply side" asymmetry:
#
#  * BAN module — banana and plantain share the same GAEZ raster. Map
#    plantain FBS supply (FBS 2616) exclusively onto banana, matching
#    the supply side where banana's area already absorbs plantain area.
#  * FRT module — citrus, mango, watermelon share the GAEZ FRT raster,
#    and apple has its own CROPGRIDS-based supply. They jointly absorb
#    the unmodeled fruit basket (pineapples, dates, the FBS 2625
#    "Fruits, other" residual): pool those FBS items and project across
#    the four FRT-/CROPGRIDS-modeled crops by per-country / global
#    crop-production share.
#
# Apples (FBS 2617) used to be a residual member, but with apple a
# directly-modeled crop (via CROPGRIDS) it now has an explicit
# faostat_food_item_map row (food → 2617) and so is consumed via the
# normal explicit-supply path, not via the residual pool.
#
# Grapes (FBS 2620) are intentionally excluded: although the FBS "Food"
# element is in principle net of wine processing, most of the world's
# grape harvest enters the alcohol industry which the model does not
# treat as fruit consumption and which GBD's fruits risk factor
# excludes.
FRUITS_BAN_PROJECTION_FOODS: tuple[str, ...] = ("banana",)
FRUITS_BAN_RESIDUAL_ITEM_CODES: tuple[int, ...] = (2616,)  # Plantains
FRUITS_FRT_PROJECTION_FOODS: tuple[str, ...] = (
    "citrus",
    "mango",
    "watermelon",
    "apple",
)
FRUITS_FRT_RESIDUAL_ITEM_CODES: tuple[int, ...] = (
    2618,  # Pineapples
    2619,  # Dates
    2625,  # Fruits, other
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
