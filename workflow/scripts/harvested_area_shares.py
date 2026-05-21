"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.diet.food_group_projection import (
    OVG_COUNTRY_SHARE_BLEND,
    build_blended_crop_shares,
)

RES06_HAR_SCALE_TO_HA = 1_000.0  # rasters store thousand hectares (kha)
logger = logging.getLogger(__name__)


def load_mapping(mapping_path: Path) -> pd.DataFrame:
    return pd.read_csv(mapping_path)


def shares_for_crop(
    crop: str,
    mapping_df: pd.DataFrame,
    production_df: pd.DataFrame,
    non_food_crops: set[str] | None = None,
) -> tuple[dict[str, float], float]:
    """Return country-specific shares and fallback share for the given crop."""

    row = mapping_df[mapping_df["crop_name"] == crop]
    if row.empty:
        raise ValueError(f"Crop '{crop}' missing from RES06 mapping table")
    module_code = row.iloc[0]["res06_code"]

    crops_in_module = sorted(
        set(mapping_df.loc[mapping_df["res06_code"] == module_code, "crop_name"])
    )
    if crop not in crops_in_module:
        crops_in_module.append(crop)

    if len(crops_in_module) == 1:
        return {}, 1.0

    production_df = production_df.copy()
    production_df["crop"] = production_df["crop"].astype(str).str.strip()
    production_df["country"] = production_df["country"].astype(str).str.upper()
    production_df = production_df[production_df["crop"].isin(crops_in_module)]

    if crop not in set(production_df["crop"].unique()) and len(crops_in_module) > 1:
        mapped_siblings = sorted(set(production_df["crop"].unique()))
        if mapped_siblings:
            logger.warning(
                "Crop '%s' has no FAOSTAT production mapping in RES06 module %s "
                "(mapped siblings: %s). Using harvested-area share 0.0.",
                crop,
                module_code,
                ", ".join(mapped_siblings),
            )
            return {}, 0.0

    if production_df.empty:
        uniform_share = 1.0 / len(crops_in_module)
        return {}, uniform_share

    non_food_set = set(non_food_crops or set())
    available_crops = set(production_df["crop"].unique())
    missing_crops = sorted(set(crops_in_module) - available_crops)
    missing_relevant = [c for c in missing_crops if c not in non_food_set]
    if missing_relevant:
        uniform_share = 1.0 / len(crops_in_module)
        logger.warning(
            "Missing FAOSTAT production for RES06 module siblings of '%s': %s. "
            "Using uniform harvested-area share %.3f across module crops %s.",
            crop,
            ", ".join(missing_relevant),
            uniform_share,
            ", ".join(crops_in_module),
        )
        return {}, uniform_share

    if module_code == "OVG":
        lookup, global_share = build_blended_crop_shares(
            production_df,
            crops_in_module,
            blend_weight=OVG_COUNTRY_SHARE_BLEND,
        )
        shares_lookup = {
            country: share
            for (country, crop_name), share in lookup.items()
            if crop_name == crop
        }
        fallback_share = float(global_share.get(crop, 1.0 / len(crops_in_module)))
        return shares_lookup, fallback_share

    production_df["production_tonnes"] = pd.to_numeric(
        production_df["production_tonnes"], errors="coerce"
    ).fillna(0.0)

    by_country = (
        production_df.groupby(["country", "crop"])["production_tonnes"]
        .sum()
        .rename("crop_total")
        .reset_index()
    )
    country_totals = (
        by_country.groupby("country")["crop_total"].sum().rename("country_total")
    )
    by_country = by_country.merge(country_totals, on="country", how="left")
    by_country["share"] = np.where(
        by_country["country_total"] > 0,
        by_country["crop_total"] / by_country["country_total"],
        np.nan,
    )

    shares_lookup = {
        country: float(share)
        for country, share in zip(
            by_country[by_country["crop"] == crop]["country"],
            by_country[by_country["crop"] == crop]["share"],
            strict=False,
        )
        if np.isfinite(share)
    }

    global_totals = (
        production_df.groupby("crop")["production_tonnes"].sum().rename("global_total")
    )
    global_denominator = float(global_totals.sum())
    if global_denominator > 0:
        fallback_share = float(global_totals.get(crop, 0.0) / global_denominator)
    else:
        fallback_share = 1.0 / len(crops_in_module)

    if fallback_share == 0.0:
        fallback_share = 1.0 / len(crops_in_module)

    return shares_lookup, fallback_share


def apply_country_shares(
    df: pd.DataFrame,
    shares_lookup: dict[str, float],
    fallback_share: float,
) -> pd.DataFrame:
    df = df.copy()
    df["share"] = df["country"].map(
        lambda country: shares_lookup.get(country, fallback_share)
    )
    df["value"] = df["value"] * df["share"].fillna(fallback_share)
    return df.drop(columns=["share"])


def shares_from_fdd(
    fdd_shares_path: Path,
    crop: str,
) -> tuple[dict[str, float], float] | None:
    """Load pre-computed FDD area shares for a crop, if available."""
    if not fdd_shares_path.exists():
        return None
    fdd_df = pd.read_csv(fdd_shares_path)
    fdd_df["country"] = fdd_df["country"].astype(str).str.upper()
    crop_shares = fdd_df[fdd_df["crop"] == crop]
    if crop_shares.empty:
        return None
    shares_lookup = dict(
        zip(crop_shares["country"], crop_shares["share"], strict=False)
    )
    fallback_share = float(crop_shares["share"].mean())
    return shares_lookup, fallback_share
