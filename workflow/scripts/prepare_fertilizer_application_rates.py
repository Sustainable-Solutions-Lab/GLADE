# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Extract and map fertilizer application rates from IFA FUBC dataset.

This script processes the IFA Fertilizer Use by Crop (FUBC) dataset to extract
nitrogen (N) application rates for model crops by country. It:

1. Filters to Report 9 (2017-18 survey) - the most recent data
2. Calculates N application rates from total N and crop area when not directly provided
3. Maps FUBC crop names to model crops
4. Aggregates data across multiple FUBC crop names when needed (area-weighted average)
5. Applies fallbacks for crops without data (e.g., buckwheat uses rye/oat average)

Input files:
    - data/downloads/ifa_fubc_1_to_9_data.csv: Raw FUBC dataset
    - data/ifa_fubc_crop_mapping.csv: Mapping from model crops to FUBC crop names

Output:
    - processing/{name}/fertilizer_application_rates.csv: N application rates by crop and country
        Columns: country (ISO3 code), crop, n_rate_kg_ha, crop_area_k_ha, n_fubc_crops

Data source: Report 9 covers the 2017-18 period with data for 64 countries and 32 crops.
"""

import pandas as pd
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def load_fubc_data(path):
    """Load and clean FUBC dataset."""
    logger.info(f"Loading FUBC data from {path}")
    df = pd.read_csv(path)

    # Convert Year to string to handle mixed types
    df["Year"] = df["Year"].astype(str)

    # Calculate N_rate_kg_ha from N_k_t and Crop_area_k_ha when not provided
    # N_k_t = thousand tonnes, Crop_area_k_ha = thousand hectares
    # Rate = (N_k_t / Crop_area_k_ha) * 1000 kg/ha
    missing_rate = df["N_rate_kg_ha"].isna()
    has_data = (
        df["N_k_t"].notna() & df["Crop_area_k_ha"].notna() & (df["Crop_area_k_ha"] > 0)
    )
    can_calculate = missing_rate & has_data

    if can_calculate.sum() > 0:
        df.loc[can_calculate, "N_rate_kg_ha"] = (
            df.loc[can_calculate, "N_k_t"] / df.loc[can_calculate, "Crop_area_k_ha"]
        ) * 1000
        logger.info(f"Calculated N_rate_kg_ha for {can_calculate.sum()} rows")

    logger.info(f"Loaded {len(df)} rows from FUBC dataset")
    logger.info(f"Unique crops: {df['Crop'].nunique()}")
    logger.info(f"Countries covered: {df['Country'].nunique()}")
    logger.info(f"Report numbers: {sorted(df['FUBC_report_number'].unique())}")

    return df


def load_crop_mapping(path):
    """Load crop name mapping."""
    logger.info(f"Loading crop mapping from {path}")

    # Read CSV, skipping comment lines
    df = pd.read_csv(path, comment="#")

    logger.info(f"Loaded mapping for {df['model_crop'].nunique()} model crops")
    logger.info(f"Mapping to {df['fubc_crop_name'].nunique()} FUBC crop names")

    return df


def map_and_aggregate(fubc_df, mapping_df):
    """
    Map FUBC crops to model crops and aggregate.

    Uses area-weighted averaging when multiple FUBC crops map to a model crop.
    Prioritizes more recent data (higher FUBC_report_number) when available.
    """
    logger.info("Mapping FUBC crops to model crops...")

    # Merge FUBC data with mapping
    merged = fubc_df.merge(
        mapping_df[["model_crop", "fubc_crop_name"]],
        left_on="Crop",
        right_on="fubc_crop_name",
        how="inner",
    )

    logger.info(f"Matched {len(merged)} rows after mapping")
    logger.info(f"Model crops with data: {merged['model_crop'].nunique()}")

    # Filter to rows with valid N application rate data
    valid_data = merged[
        merged["N_rate_kg_ha"].notna()
        & (merged["N_rate_kg_ha"] > 0)
        & merged["Crop_area_k_ha"].notna()
        & (merged["Crop_area_k_ha"] > 0)
    ].copy()

    logger.info(f"Rows with valid N rate and area data: {len(valid_data)}")

    # Prioritize most recent data: for each country-crop, keep only the latest report
    latest_report = (
        valid_data.groupby(["ISO3_code", "model_crop"])["FUBC_report_number"]
        .max()
        .reset_index()
    )
    latest_report.columns = ["ISO3_code", "model_crop", "latest_report"]

    valid_data = valid_data.merge(latest_report, on=["ISO3_code", "model_crop"])
    valid_data = valid_data[
        valid_data["FUBC_report_number"] == valid_data["latest_report"]
    ]

    logger.info(f"After filtering to most recent data: {len(valid_data)} rows")

    # When multiple FUBC crops map to the same model crop, aggregate using area-weighted average
    # Group by ISO code and model crop
    aggregated = (
        valid_data.groupby(["ISO3_code", "model_crop"])
        .apply(
            lambda x: pd.Series(
                {
                    "n_rate_kg_ha": (x["N_rate_kg_ha"] * x["Crop_area_k_ha"]).sum()
                    / x["Crop_area_k_ha"].sum(),
                    "crop_area_k_ha": x["Crop_area_k_ha"].sum(),
                    "n_fubc_crops": len(x),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )

    # Rename columns for output
    aggregated.columns = [
        "country",
        "crop",
        "n_rate_kg_ha",
        "crop_area_k_ha",
        "n_fubc_crops",
    ]

    # Round numeric columns
    aggregated["n_rate_kg_ha"] = aggregated["n_rate_kg_ha"].round(2)
    aggregated["crop_area_k_ha"] = aggregated["crop_area_k_ha"].round(2)

    logger.info(f"Final aggregated data: {len(aggregated)} rows")
    logger.info(f"Countries with data: {aggregated['country'].nunique()}")
    logger.info(f"Model crops with data: {aggregated['crop'].nunique()}")

    # Log crops with data
    crops_with_data = sorted(aggregated["crop"].unique())
    logger.info(f"Crops with fertilizer data: {', '.join(crops_with_data)}")

    # Log summary statistics
    logger.info("\nN application rate statistics (kg/ha):")
    logger.info(f"  Mean: {aggregated['n_rate_kg_ha'].mean():.1f}")
    logger.info(f"  Median: {aggregated['n_rate_kg_ha'].median():.1f}")
    logger.info(f"  Min: {aggregated['n_rate_kg_ha'].min():.1f}")
    logger.info(f"  Max: {aggregated['n_rate_kg_ha'].max():.1f}")

    return aggregated


def apply_fallbacks(df):
    """
    Apply fallback values for crops without data.

    Report 9 aggregates some crops into broader categories. For crops missing
    from the dataset, we use data from similar crops as fallbacks.
    """
    logger.info("\nApplying fallbacks for missing crops...")

    crops_present = set(df["crop"].unique())

    # Define fallback mappings: missing_crop -> [source_crops]
    fallbacks = {
        "barley": ["wheat", "maize"],  # Small grain cereals
        "oat": ["wheat", "maize"],
        "rye": ["wheat", "maize"],
        "sorghum": ["maize", "wheat"],  # Coarse cereals
        "white-potato": ["sweet-potato", "cassava"],  # Root/tuber crops
        "banana": ["sugarcane", "citrus"],  # Perennial crops
        "coconut": ["oil-palm", "olive"],  # Tree crops
    }

    added_count = 0
    for target_crop, source_crop_list in fallbacks.items():
        if target_crop not in crops_present:
            # Find available source crops
            available_sources = [c for c in source_crop_list if c in crops_present]

            if available_sources:
                logger.info(
                    f"  {target_crop}: using average of {', '.join(available_sources)}"
                )

                # Get source crop data
                source_data = df[df["crop"].isin(available_sources)].copy()

                # Calculate country-level average
                fallback_data = (
                    source_data.groupby("country")
                    .apply(
                        lambda x: pd.Series(
                            {
                                "n_rate_kg_ha": x["n_rate_kg_ha"].mean(),
                                "crop_area_k_ha": 0.0,  # Unknown area
                                "n_fubc_crops": 0,
                            }
                        ),
                        include_groups=False,
                    )
                    .reset_index()
                )

                fallback_data["crop"] = target_crop
                fallback_data["n_rate_kg_ha"] = fallback_data["n_rate_kg_ha"].round(2)

                # Append to dataframe
                df = pd.concat([df, fallback_data], ignore_index=True)
                added_count += len(fallback_data)

                logger.info(
                    f"    Added {target_crop} data for {len(fallback_data)} countries"
                )
            else:
                logger.warning(
                    f"    Could not add {target_crop} fallback: no source crops available"
                )

    if added_count > 0:
        logger.info(f"  Total fallback entries added: {added_count}")
    else:
        logger.info("  No fallbacks needed")

    return df


if __name__ == "__main__":
    # Load data
    fubc_df = load_fubc_data(snakemake.input["fubc_data"])
    mapping_df = load_crop_mapping(snakemake.input["mapping"])

    # Map and aggregate
    output_df = map_and_aggregate(fubc_df, mapping_df)

    # Apply fallbacks for missing crops
    output_df = apply_fallbacks(output_df)

    # Save output
    logger.info(f"\nWriting output to {snakemake.output[0]}")
    output_df.to_csv(snakemake.output[0], index=False)

    logger.info("Done!")
