# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Categorize feeds into quality classes and compute average nutritional values.

Groups individual feeds into categories based on digestibility and energy content,
then computes category-level average nutritional properties for use in the model.
"""

import logging

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def categorize_ruminant_feeds(
    feed_properties: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Categorize ruminant feeds based on digestibility.

    Categories align with IPCC CH4 emission factors:
    - roughage: low digestibility (< 0.55), high CH4
    - forage: medium digestibility (0.55-0.70), medium CH4
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
    df = (
        feed_properties.groupby(["feed_item", "source_type"])
        .agg(
            {
                "GE_MJ_per_kg_DM": "mean",
                "N_g_per_kg_DM": "mean",
                "digestibility": "mean",
            }
        )
        .reset_index()
    )

    # Calculate ME from GE * digestibility * 0.82 (standard conversion)
    df["ME_MJ_per_kg_DM"] = df["GE_MJ_per_kg_DM"] * df["digestibility"] * 0.82

    # Assign categories based on digestibility
    def assign_category(di):
        if di < 0.55:
            return "roughage"
        elif di < 0.70:
            return "forage"
        elif di < 0.90:
            return "grain"
        else:
            return "protein"

    df["category"] = df["digestibility"].apply(assign_category)

    # Create feed mapping table
    feed_mapping = df[["feed_item", "source_type", "category"]].copy()

    # Compute category-level averages
    categories = (
        df.groupby("category")
        .agg(
            {
                "ME_MJ_per_kg_DM": "mean",
                "GE_MJ_per_kg_DM": "mean",
                "N_g_per_kg_DM": "mean",
                "digestibility": "mean",
            }
        )
        .reset_index()
    )

    # Count feeds per category
    categories["n_feeds"] = df.groupby("category").size().values

    logger.info("Ruminant feed categories:")
    for _, row in categories.iterrows():
        logger.info(
            "  %s: %d feeds, ME=%.1f MJ/kg, N=%.1f g/kg, DI=%.2f",
            row["category"],
            row["n_feeds"],
            row["ME_MJ_per_kg_DM"],
            row["N_g_per_kg_DM"],
            row["digestibility"],
        )

    return categories, feed_mapping


def categorize_monogastric_feeds(
    feed_properties: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Categorize monogastric feeds based on ME content and protein level.

    Categories:
    - low_quality: ME < 11 MJ/kg (residues, bran)
    - grain: ME 11-15.5 MJ/kg, N < 35 g/kg (cereals)
    - energy: ME > 15.5 MJ/kg, N < 35 g/kg (high-energy feeds)
    - protein: N > 35 g/kg (legumes, meals)

    Returns
    -------
    categories : pd.DataFrame
        Category definitions with average nutritional values
    feed_mapping : pd.DataFrame
        Mapping from individual feeds to categories
    """
    # Deduplicate by averaging
    df = (
        feed_properties.groupby(["feed_item", "source_type"])
        .agg(
            {
                "GE_MJ_per_kg_DM": "mean",
                "ME_MJ_per_kg_DM": "mean",
                "N_g_per_kg_DM": "mean",
                "digestibility": "mean",
            }
        )
        .reset_index()
    )

    # Assign categories
    def assign_category(row):
        me = row["ME_MJ_per_kg_DM"]
        n = row["N_g_per_kg_DM"]

        # Protein category takes precedence
        if n > 35:
            return "protein"
        elif me < 11:
            return "low_quality"
        elif me < 15.5:
            return "grain"
        else:
            return "energy"

    df["category"] = df.apply(assign_category, axis=1)

    # Create feed mapping table
    feed_mapping = df[["feed_item", "source_type", "category"]].copy()

    # Compute category-level averages
    categories = (
        df.groupby("category")
        .agg(
            {
                "ME_MJ_per_kg_DM": "mean",
                "GE_MJ_per_kg_DM": "mean",
                "N_g_per_kg_DM": "mean",
                "digestibility": "mean",
            }
        )
        .reset_index()
    )

    # Count feeds per category
    categories["n_feeds"] = df.groupby("category").size().values

    logger.info("Monogastric feed categories:")
    for _, row in categories.iterrows():
        logger.info(
            "  %s: %d feeds, ME=%.1f MJ/kg, N=%.1f g/kg, DI=%.2f",
            row["category"],
            row["n_feeds"],
            row["ME_MJ_per_kg_DM"],
            row["N_g_per_kg_DM"],
            row["digestibility"],
        )

    return categories, feed_mapping


def add_methane_yields(
    ruminant_categories: pd.DataFrame,
    methane_yields: pd.DataFrame,
) -> pd.DataFrame:
    """Add CH4 emission factors to ruminant categories.

    Maps category names to IPCC-based CH4 yields from enteric_methane_yields.csv.
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
    # Read inputs
    ruminant_props = pd.read_csv(snakemake.input.ruminant_feed_properties, comment="#")
    monogastric_props = pd.read_csv(
        snakemake.input.monogastric_feed_properties, comment="#"
    )
    methane_yields = pd.read_csv(snakemake.input.enteric_methane_yields, comment="#")

    # Categorize feeds
    ruminant_categories, ruminant_mapping = categorize_ruminant_feeds(ruminant_props)
    monogastric_categories, monogastric_mapping = categorize_monogastric_feeds(
        monogastric_props
    )

    # Add CH4 yields to ruminant categories
    ruminant_categories = add_methane_yields(ruminant_categories, methane_yields)

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
