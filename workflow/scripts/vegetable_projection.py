"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

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
