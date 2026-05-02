# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Categorize feeds into quality classes and compute average nutritional values.

Groups individual feeds into categories based on digestibility and energy content,
then computes category-level average nutritional properties for use in the model.

By default each (feed_item, source_type) is assigned to a single category with
share = 1.0. A feed_category_overrides.csv may specify multi-category splits
for individual items (e.g. DDGS = 70% grain + 30% protein in monogastric
diets); see ``apply_category_overrides`` and the override file's header for
details. Multi-row entries are propagated through to the feed_conversion link
construction (where ``share`` becomes the link efficiency, conserving mass)
and through to the GLEAM-derived feed baseline split.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def categorize_ruminant_feeds(
    feed_properties: pd.DataFrame,
    ash_content: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Categorize ruminant feeds based on digestibility.

    Categories align with IPCC CH4 emission factors:
    - roughage: low digestibility (< 0.55), high CH4
    - forage: medium digestibility (0.55-0.70), medium CH4 (includes grassland)
    - grain: high digestibility (0.70-0.90), low CH4
    - protein: very high digestibility (> 0.90), low CH4

    Returns
    -------
    categories : pd.DataFrame
        Category definitions with average nutritional values
    feed_mapping : pd.DataFrame
        Mapping from individual feeds to categories
    """
    # Deduplicate by averaging (same feed may have multiple GLEAM codes)
    agg_dict = {
        "GE_MJ_per_kg_DM": "mean",
        "N_g_per_kg_DM": "mean",
        "digestibility": "mean",
    }

    # Merge with ash content data if available
    if "ash_content_pct_dm" in feed_properties.columns:
        agg_dict["ash_content_pct_dm"] = "mean"

    df = (
        feed_properties.groupby(["feed_item", "source_type"])
        .agg(agg_dict)
        .reset_index()
    )

    # Calculate ME from GE * digestibility * 0.82 (standard conversion)
    df["ME_MJ_per_kg_DM"] = df["GE_MJ_per_kg_DM"] * df["digestibility"] * 0.82

    # Assign categories based on nitrogen content and digestibility
    # Grassland gets its own category for special N management
    def assign_category(row):
        if row["feed_item"] == "grassland":
            return "forage"

        # All crop residues are roughage regardless of digestibility.
        # GLEAM groups all residues together under "crop residues" in the
        # roughage decomposition (SI Tables 4-5), and they share the same
        # role in the feed system.
        if row["source_type"] == "residue":
            return "roughage"

        # Protein category takes precedence for high-N feeds (like monogastrics)
        # Threshold of 50 g N/kg DM captures protein meals (rapeseed, soybean, etc.)
        if row["N_g_per_kg_DM"] > 50:
            return "protein"

        # Otherwise categorize by digestibility
        di = row["digestibility"]
        if di < 0.55:
            return "roughage"
        elif di < 0.70:
            return "forage"
        elif di < 0.90:
            return "grain"
        else:
            return "protein"

    df["category"] = df.apply(assign_category, axis=1)

    # Create feed mapping table
    feed_mapping = df[["feed_item", "source_type", "category"]].copy()

    # Compute category-level averages
    agg_dict_cat = {
        "ME_MJ_per_kg_DM": "mean",
        "GE_MJ_per_kg_DM": "mean",
        "N_g_per_kg_DM": "mean",
        "digestibility": "mean",
    }

    if "ash_content_pct_dm" in df.columns:
        agg_dict_cat["ash_content_pct_dm"] = "mean"

    categories = df.groupby("category").agg(agg_dict_cat).reset_index()

    # Count feeds per category
    categories["n_feeds"] = df.groupby("category").size().values

    logger.info("Ruminant feed categories:")
    for _, row in categories.iterrows():
        ash_info = (
            f", Ash={row['ash_content_pct_dm']:.1f}%"
            if "ash_content_pct_dm" in row.index
            else ""
        )
        logger.info(
            "  %s: %d feeds, ME=%.1f MJ/kg, N=%.1f g/kg, DI=%.2f%s",
            row["category"],
            row["n_feeds"],
            row["ME_MJ_per_kg_DM"],
            row["N_g_per_kg_DM"],
            row["digestibility"],
            ash_info,
        )

    return categories, feed_mapping


def categorize_monogastric_feeds(
    feed_properties: pd.DataFrame,
    ash_content: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Categorize monogastric feeds based on ME content and protein level.

    Categories:
    - low_quality: ME < 11 MJ/kg (residues, bran)
    - grain: ME >= 11 MJ/kg, N < 35 g/kg (cereals, energy crops)
    - protein: N > 35 g/kg (legumes, meals)

    Returns
    -------
    categories : pd.DataFrame
        Category definitions with average nutritional values
    feed_mapping : pd.DataFrame
        Mapping from individual feeds to categories
    """
    # Deduplicate by averaging
    agg_dict = {
        "GE_MJ_per_kg_DM": "mean",
        "ME_MJ_per_kg_DM": "mean",
        "N_g_per_kg_DM": "mean",
        "digestibility": "mean",
    }

    if "ash_content_pct_dm" in feed_properties.columns:
        agg_dict["ash_content_pct_dm"] = "mean"

    df = (
        feed_properties.groupby(["feed_item", "source_type"])
        .agg(agg_dict)
        .reset_index()
    )

    # Assign categories
    def assign_category(row):
        n = row["N_g_per_kg_DM"]
        me = row["ME_MJ_per_kg_DM"]

        # Protein category takes precedence
        if n > 35:
            return "protein"
        elif me < 11:
            return "low_quality"
        else:
            return "grain"

    df["category"] = df.apply(assign_category, axis=1)

    # Create feed mapping table
    feed_mapping = df[["feed_item", "source_type", "category"]].copy()

    # Compute category-level averages
    agg_dict_cat = {
        "ME_MJ_per_kg_DM": "mean",
        "GE_MJ_per_kg_DM": "mean",
        "N_g_per_kg_DM": "mean",
        "digestibility": "mean",
    }

    if "ash_content_pct_dm" in df.columns:
        agg_dict_cat["ash_content_pct_dm"] = "mean"

    categories = df.groupby("category").agg(agg_dict_cat).reset_index()

    # Count feeds per category
    categories["n_feeds"] = df.groupby("category").size().values

    logger.info("Monogastric feed categories:")
    for _, row in categories.iterrows():
        ash_info = (
            f", Ash={row['ash_content_pct_dm']:.1f}%"
            if "ash_content_pct_dm" in row.index
            else ""
        )
        logger.info(
            "  %s: %d feeds, ME=%.1f MJ/kg, N=%.1f g/kg, DI=%.2f%s",
            row["category"],
            row["n_feeds"],
            row["ME_MJ_per_kg_DM"],
            row["N_g_per_kg_DM"],
            row["digestibility"],
            ash_info,
        )

    return categories, feed_mapping


def apply_category_overrides(
    mapping: pd.DataFrame,
    animal_type: str,
    overrides_path: str | Path | None,
) -> pd.DataFrame:
    """Apply multi-category overrides to an auto-categorized feed mapping.

    The default mapping has one row per (feed_item, source_type) with the
    auto-determined category. Overrides allow a single item to be split
    across multiple categories with explicit shares (e.g. DDGS as 70%
    grain + 30% protein for monogastric pigs). Items not listed in the
    overrides file keep their auto-categorized assignment with share=1.0.

    Parameters
    ----------
    mapping : pd.DataFrame
        Auto-categorized mapping with columns
        ``[feed_item, source_type, category]``.
    animal_type : str
        ``ruminant`` or ``monogastric``; selects which override rows
        apply.
    overrides_path : str | Path | None
        Path to the override CSV, or None to skip applying overrides.

    Returns
    -------
    pd.DataFrame
        Mapping with columns ``[feed_item, source_type, category, share]``.
        Items not overridden have share=1.0; overridden items have one
        row per (item, category) with the configured share.
    """
    mapping = mapping.copy()
    mapping["share"] = 1.0

    if overrides_path is None or not Path(overrides_path).exists():
        return mapping

    overrides = pd.read_csv(overrides_path, comment="#")
    overrides = overrides[overrides["animal_type"] == animal_type]
    if overrides.empty:
        return mapping

    required_cols = {"feed_item", "source_type", "animal_type", "category", "share"}
    missing = required_cols - set(overrides.columns)
    if missing:
        raise ValueError(
            f"feed_category_overrides.csv missing columns: {sorted(missing)}"
        )

    # Validate per-(item, source) shares sum to 1
    sums = overrides.groupby(["feed_item", "source_type"])["share"].sum()
    bad = sums[~np.isclose(sums.values, 1.0, atol=1e-6)]
    if not bad.empty:
        raise ValueError(
            f"feed_category_overrides shares must sum to 1.0 per "
            f"(feed_item, source_type) for {animal_type}; got:\n{bad}"
        )

    overridden = set(zip(overrides["feed_item"], overrides["source_type"]))
    keep_mask = ~mapping[["feed_item", "source_type"]].apply(tuple, axis=1).isin(
        overridden
    )
    base = mapping.loc[keep_mask, ["feed_item", "source_type", "category", "share"]]
    extra = overrides[["feed_item", "source_type", "category", "share"]]

    logger.info(
        "%s: applied category overrides to %d items (%d total override rows)",
        animal_type,
        len(overridden),
        len(extra),
    )
    return (
        pd.concat([base, extra], ignore_index=True)
        .sort_values(["feed_item", "source_type", "category"])
        .reset_index(drop=True)
    )


def add_methane_yields(
    ruminant_categories: pd.DataFrame,
    methane_yields: pd.DataFrame,
) -> pd.DataFrame:
    """Add CH4 emission factors to ruminant categories.

    Maps category names to IPCC-based CH4 yields from ipcc_enteric_methane_yields.csv.
    """
    # Create mapping from our categories to methane yield categories
    category_mapping = {
        "roughage": "roughage",
        "forage": "forage",
        "grain": "concentrate",
        "protein": "concentrate",
    }

    # Merge with methane yields
    ruminant_categories["ch4_category"] = ruminant_categories["category"].map(
        category_mapping
    )

    result = ruminant_categories.merge(
        methane_yields[["feed_category", "MY_g_CH4_per_kg_DMI"]],
        left_on="ch4_category",
        right_on="feed_category",
        how="left",
    )

    # Drop temporary columns
    result = result.drop(columns=["ch4_category", "feed_category"])

    logger.info("Added CH4 yields to ruminant categories")

    return result


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    # Read inputs
    ruminant_props = pd.read_csv(snakemake.input.ruminant_feed_properties)
    monogastric_props = pd.read_csv(snakemake.input.monogastric_feed_properties)
    methane_yields = pd.read_csv(snakemake.input.enteric_methane_yields, comment="#")
    ash_content = pd.read_csv(snakemake.input.ash_content, comment="#")

    # Merge ash content data with feed properties
    ruminant_props = ruminant_props.merge(
        ash_content[["feed", "ash_content_pct_dm"]],
        left_on="feed_item",
        right_on="feed",
        how="left",
    ).drop(columns=["feed"])

    monogastric_props = monogastric_props.merge(
        ash_content[["feed", "ash_content_pct_dm"]],
        left_on="feed_item",
        right_on="feed",
        how="left",
    ).drop(columns=["feed"])

    # Categorize feeds
    ruminant_categories, ruminant_mapping = categorize_ruminant_feeds(
        ruminant_props, ash_content
    )
    monogastric_categories, monogastric_mapping = categorize_monogastric_feeds(
        monogastric_props, ash_content
    )

    # Add CH4 yields to ruminant categories
    ruminant_categories = add_methane_yields(ruminant_categories, methane_yields)

    # Apply optional multi-category overrides (adds ``share`` column).
    overrides_path = getattr(snakemake.input, "category_overrides", None)
    ruminant_mapping = apply_category_overrides(
        ruminant_mapping, "ruminant", overrides_path
    )
    monogastric_mapping = apply_category_overrides(
        monogastric_mapping, "monogastric", overrides_path
    )

    # Write outputs
    ruminant_categories.to_csv(snakemake.output.ruminant_categories, index=False)
    monogastric_categories.to_csv(snakemake.output.monogastric_categories, index=False)
    ruminant_mapping.to_csv(snakemake.output.ruminant_mapping, index=False)
    monogastric_mapping.to_csv(snakemake.output.monogastric_mapping, index=False)

    logger.info("Feed categorization complete")
    logger.info(
        "  Ruminant: %d categories, %d feeds",
        len(ruminant_categories),
        len(ruminant_mapping),
    )
    logger.info(
        "  Monogastric: %d categories, %d feeds",
        len(monogastric_categories),
        len(monogastric_mapping),
    )
