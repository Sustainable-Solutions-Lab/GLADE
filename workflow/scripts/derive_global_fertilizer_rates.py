# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Derive global fertilizer N application rates for high-input agriculture.

This script processes country-level fertilizer application rates from the IFA FUBC
dataset and derives global "high-input" rates for each crop using percentiles.
The percentile approach captures typical rates in intensive agricultural systems
without being influenced by low-input or subsistence agriculture.

Input:
    - processing/{name}/fertilizer_application_rates.csv: Country-level N rates
        Columns: country (ISO3), crop, n_rate_kg_ha, crop_area_k_ha, n_fubc_crops

Output:
    - processing/{name}/global_fertilizer_n_rates.csv: Global high-input N rates
        Columns: crop, n_rate_kg_per_ha
        Units: kg of elemental nitrogen per hectare per year

Configuration:
    - primary.fertilizer.n_percentile: Percentile to use (0-100)
        Common values: 75 (upper quartile), 80 (default), 90 (very intensive)

Example:
    For wheat with n_percentile=80, if the 80th percentile of global wheat
    N application rates is 120 kg/ha, the output will be:
        crop,n_rate_kg_per_ha
        wheat,120.0
"""

import pandas as pd
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def calculate_percentile_rates(df, percentile, crops):
    """
    Calculate percentile-based N application rates for each crop.

    Parameters
    ----------
    df : pd.DataFrame
        Input dataframe with columns: country, crop, n_rate_kg_ha, crop_area_k_ha
    percentile : float
        Percentile to calculate (0-100)
    crops : list
        List of expected crops

    Returns
    -------
    pd.DataFrame
        Dataframe with columns: crop, n_rate_kg_per_ha
    """
    logger.info(f"Calculating {percentile}th percentile N application rates")

    # Calculate percentile for each crop
    # Use all data points (each country is one observation)
    percentile_rates = (
        df.groupby("crop")["n_rate_kg_ha"].quantile(percentile / 100.0).reset_index()
    )
    percentile_rates.columns = ["crop", "n_rate_kg_per_ha"]

    # Round to 2 decimal places
    percentile_rates["n_rate_kg_per_ha"] = percentile_rates["n_rate_kg_per_ha"].round(2)

    logger.info(f"Calculated rates for {len(percentile_rates)} crops")

    # Check for missing crops
    crops_with_data = set(percentile_rates["crop"])
    expected_crops = set(crops)
    missing_crops = expected_crops - crops_with_data

    if missing_crops:
        logger.warning(f"Missing data for crops: {', '.join(sorted(missing_crops))}")
        logger.warning("These crops will have no fertilizer application in the model")

    # Log statistics
    logger.info("\nN application rate statistics (kg N/ha/year):")
    logger.info(f"  Mean: {percentile_rates['n_rate_kg_per_ha'].mean():.1f}")
    logger.info(f"  Median: {percentile_rates['n_rate_kg_per_ha'].median():.1f}")
    logger.info(f"  Min: {percentile_rates['n_rate_kg_per_ha'].min():.1f}")
    logger.info(f"  Max: {percentile_rates['n_rate_kg_per_ha'].max():.1f}")

    # Log top 5 and bottom 5 crops
    sorted_rates = percentile_rates.sort_values("n_rate_kg_per_ha", ascending=False)
    logger.info("\nTop 5 crops by N rate (kg/ha/year):")
    for _, row in sorted_rates.head(5).iterrows():
        logger.info(f"  {row['crop']}: {row['n_rate_kg_per_ha']:.1f}")

    logger.info("\nBottom 5 crops by N rate (kg/ha/year):")
    for _, row in sorted_rates.tail(5).iterrows():
        logger.info(f"  {row['crop']}: {row['n_rate_kg_per_ha']:.1f}")

    return percentile_rates


if __name__ == "__main__":
    # Load input data
    logger.info(
        f"Loading fertilizer application rates from {snakemake.input['fertilizer_rates']}"
    )
    df = pd.read_csv(snakemake.input["fertilizer_rates"])

    logger.info(f"Loaded {len(df)} country-crop combinations")
    logger.info(f"Countries: {df['country'].nunique()}")
    logger.info(f"Crops: {df['crop'].nunique()}")

    # Get percentile from config
    percentile = snakemake.params["n_percentile"]
    logger.info(f"Using {percentile}th percentile for high-input agriculture")

    if not 0 <= percentile <= 100:
        raise ValueError(f"Percentile must be between 0 and 100, got {percentile}")

    # Calculate percentile rates
    output_df = calculate_percentile_rates(df, percentile, snakemake.params["crops"])

    # Save output
    logger.info(f"\nWriting output to {snakemake.output[0]}")
    output_df.to_csv(snakemake.output[0], index=False)

    logger.info("Done!")
