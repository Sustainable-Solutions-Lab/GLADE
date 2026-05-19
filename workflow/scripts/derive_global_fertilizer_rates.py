# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
        Columns: country (ISO3), crop, n_rate_kg_per_ha, crop_area_k_ha, n_fubc_crops

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

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Setup logging
# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def calculate_percentile_rates(df, percentile, crops, proxy_rates):
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
    proxy_rates : dict[str, str]
        Map of model crop -> source crop for crops absent from FUBC. The
        source crop's derived rate is copied onto the target crop so every
        expected model crop is present in the output.

    Returns
    -------
    pd.DataFrame
        Dataframe with columns: crop, n_rate_kg_per_ha
    """
    logger.info(f"Calculating {percentile}th percentile N application rates")

    # Calculate percentile for each crop
    # Use all data points (each country is one observation)
    percentile_rates = (
        df.groupby("crop")["n_rate_kg_per_ha"]
        .quantile(percentile / 100.0)
        .reset_index()
    )

    # Apply proxy mappings: every target crop in proxy_rates inherits the
    # source crop's rate. Source crops must themselves be present in the
    # FUBC-derived table.
    if proxy_rates:
        source_lookup = percentile_rates.set_index("crop")["n_rate_kg_per_ha"].to_dict()
        missing_sources = sorted(
            target
            for target, source in proxy_rates.items()
            if source not in source_lookup
        )
        if missing_sources:
            raise ValueError(
                "fertilizer.proxy_rates references source crops that are "
                f"absent from the derived rates table: {missing_sources}"
            )
        proxy_rows = pd.DataFrame(
            {
                "crop": list(proxy_rates.keys()),
                "n_rate_kg_per_ha": [
                    source_lookup[source] for source in proxy_rates.values()
                ],
            }
        )
        percentile_rates = pd.concat([percentile_rates, proxy_rows], ignore_index=True)
        logger.info(
            "Applied %d proxy mappings: %s",
            len(proxy_rates),
            ", ".join(f"{t}<-{s}" for t, s in proxy_rates.items()),
        )

    # Round to 2 decimal places
    percentile_rates["n_rate_kg_per_ha"] = percentile_rates["n_rate_kg_per_ha"].round(2)

    logger.info(f"Calculated rates for {len(percentile_rates)} crops")

    # Every model crop must have a rate. Silent zeros would let crops avoid
    # synthetic-N draw and the associated N2O emissions.
    crops_with_data = set(percentile_rates["crop"])
    expected_crops = set(crops)
    missing_crops = expected_crops - crops_with_data
    if missing_crops:
        raise ValueError(
            "Missing N application rates for model crops: "
            f"{sorted(missing_crops)}. Add a fertilizer.proxy_rates entry "
            "pointing each missing crop to a similar FUBC-covered crop."
        )

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
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

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
    output_df = calculate_percentile_rates(
        df,
        percentile,
        snakemake.params["crops"],
        dict(snakemake.params["proxy_rates"]),
    )

    # Save output
    logger.info(f"\nWriting output to {snakemake.output[0]}")
    output_df.to_csv(snakemake.output[0], index=False)

    logger.info("Done!")
