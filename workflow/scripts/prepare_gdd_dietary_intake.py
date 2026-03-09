#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Process Global Dietary Database (GDD) country-level dietary intake data.

Reads multiple v*_cnty.csv files from GDD, extracts national-level baseline
intake estimates by age group (aggregated across sex/urban/education strata),
maps GDD food variables to model food groups, and outputs a consolidated
baseline diet file with age stratification.

Input:
    - GDD directory with Country-level estimates/*.csv files
    - Reference year from config
    - Food groups from config

Output:
    - CSV with columns: unit,item,country,age,year,value
    - Age groups: 0-1, 1-2, 2-5, 6-10, 11-74, 75+ years, plus "All ages" aggregate
"""

import logging
from pathlib import Path
import sys

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger("prepare_gdd_dietary_intake")


def main():
    gdd_dir = Path(snakemake.input["gdd_dir"])
    reference_year = snakemake.params["reference_year"]
    food_groups = snakemake.params["food_groups"]
    output_file = snakemake.output["diet"]
    stimulant_brewed_to_dry = {
        "v18": snakemake.params["stimulant_brewed_to_dry"]["tea"],
    }
    ssb_sugar_g_per_100g = float(snakemake.params["ssb_sugar_g_per_100g"])
    if ssb_sugar_g_per_100g <= 0:
        logger.error(
            "health.ssb_sugar_g_per_100g must be positive for sugar conversions"
        )
        sys.exit(1)
    ssb_sugar_per_gram = ssb_sugar_g_per_100g / 100.0

    # Map GDD variable codes (vXX) to model food groups
    # Based on GDD codebook and canonical food_groups.csv
    gdd_to_model_items = {
        "v01": "fruits",  # Fruits (all types, excluding juices)
        "v02": "vegetables",  # Non-starchy vegetables
        "v03": "starchy_vegetable",  # Potatoes
        "v04": "starchy_vegetable",  # Other starchy vegetables (yam, cassava, etc.)
        "v05": "legumes",  # Beans and legumes
        "v06": "nuts_seeds",  # Nuts and seeds
        "v07": "grain",  # Refined grains (white flour, white rice)
        "v08": "whole_grains",  # Whole grains
        "v09": "prc_meat",  # Total processed meats
        "v10": "red_meat",  # Unprocessed red meats (cattle, pig)
        # v11 (Total seafoods) not mapped — fish/seafood not modelled
        "v12": "eggs",  # Eggs
        # "v57": "dairy",  # Total Milk - Excluded: Sourced from FAOSTAT
        "v15": "sugar",  # Sugar-sweetened beverages → refined sugar equivalent
        "v35": "sugar",  # Added sugars (g/day already reported)
        "v16": None,  # Fruit juices (excluded - not part of GBD fruit risk factor)
        "v17": None,  # Coffee — excluded: GDD v17 is unreliable for many countries
        # (e.g. 42x overestimate for India vs FAOSTAT). coffee-green is sourced
        # from FAOSTAT FBS via fbs_override_foods instead.
        "v18": "tea-dried",  # Tea (brewed → dry weight conversion applied)
        # Note: We use v57 "Total Milk" for dairy, which aligns with the GBD dairy
        # risk factor definition and includes milk equivalents from all dairy products.
        # Individual components (v13 cheese, v14 yogurt) are not used separately
        # to avoid double-counting.
        # Fruit juices (v16) are excluded from fruits to match GBD fruit risk factor,
        # which only includes whole fruits, not juices.
    }

    # Filter to only food groups that are in the config
    # Multiple GDD variables may map to the same food group
    # Per-food items (e.g. coffee-green, tea-dried) bypass the food group filter
    # since they represent direct food-level values, not group aggregates.
    DIRECT_FOOD_ITEMS = {"tea-dried"}
    requested_food_groups = set(food_groups)
    food_group_vars = {}
    for varcode, item in gdd_to_model_items.items():
        if item is not None and (
            item in requested_food_groups or item in DIRECT_FOOD_ITEMS
        ):
            if item not in food_group_vars:
                food_group_vars[item] = []
            food_group_vars[item].append(varcode)

    logger.info("Processing GDD data for year %d", reference_year)
    logger.info("Food groups: %s", sorted(food_group_vars.keys()))
    for item, varcodes in sorted(food_group_vars.items()):
        logger.info("  %s: %s", item, varcodes)

    country_estimates_dir = gdd_dir / "Country-level estimates"
    if not country_estimates_dir.exists():
        logger.error(
            "Country-level estimates directory not found: %s", country_estimates_dir
        )
        sys.exit(1)

    all_data = []

    # Process each food group (which may aggregate multiple GDD variables)
    for model_item, varcodes in food_group_vars.items():
        item_data = []

        for varcode in varcodes:
            csv_file = country_estimates_dir / f"{varcode}_cnty.csv"
            if not csv_file.exists():
                logger.warning("File not found: %s", csv_file)
                continue

            logger.info("Reading %s (%s)...", csv_file.name, model_item)
            df = pd.read_csv(csv_file)

            # Filter to reference year
            df_year = df[df["year"] == reference_year].copy()

            if df_year.empty:
                logger.warning(
                    "No data for year %d in %s. Trying nearest year...",
                    reference_year,
                    csv_file.name,
                )
                # Find nearest year
                available_years = sorted(df["year"].unique())
                nearest_year = min(
                    available_years, key=lambda y: abs(y - reference_year)
                )
                logger.warning("  Using nearest year: %d", nearest_year)
                df_year = df[df["year"] == nearest_year].copy()
                if df_year.empty:
                    logger.error("Still no data for %s", csv_file.name)
                    continue

            if model_item == "sugar":
                if varcode == "v15":
                    beverage_grams = pd.to_numeric(
                        df_year["median"], errors="coerce"
                    ).fillna(0.0)
                    df_year["median"] = beverage_grams * ssb_sugar_per_gram
                elif varcode == "v35":
                    # Added sugars in %kcal.
                    # Assume 2000 kcal/day diet for conversion (v35 / 100 * 2000 / 4)
                    # 1 g sugar = 4 kcal.
                    median_val = pd.to_numeric(
                        df_year["median"], errors="coerce"
                    ).fillna(0.0)
                    df_year["median"] = median_val * 2000.0 / 100.0 / 4.0
                else:
                    df_year["median"] = pd.to_numeric(
                        df_year["median"], errors="coerce"
                    ).fillna(0.0)
            elif varcode in stimulant_brewed_to_dry:
                brewed_to_dry = stimulant_brewed_to_dry[varcode]
                brewed_grams = pd.to_numeric(df_year["median"], errors="coerce").fillna(
                    0.0
                )
                df_year["median"] = brewed_grams * brewed_to_dry

            # Aggregate to country-age level (weighted mean across sex/urban/education strata)
            # GDD stratifies by age, sex, urban, education
            # We want population-weighted national averages by age group
            # The 'median' column is the mean intake (50th percentile of modeled simulations)

            def map_age_bucket(age_val):
                if age_val == 999:
                    return "All ages"
                elif age_val <= 1:
                    return "0-1 years"
                elif age_val <= 2:
                    return "1-2 years"
                elif age_val <= 5:
                    return "2-5 years"
                elif age_val <= 10:
                    return "6-10 years"
                elif age_val <= 74:
                    return "11-74 years"
                else:
                    return "75+ years"

            # Prefer GDD's pre-computed population-weighted national aggregate
            # rows (female==999, urban==999, edu==999) over a simple mean across
            # all 36 demographic strata, which would ignore stratum sizes.
            strat_cols = [c for c in ["female", "urban", "edu"] if c in df_year.columns]
            if strat_cols:
                mask = pd.Series(True, index=df_year.index)
                for col in strat_cols:
                    mask &= df_year[col] == 999
                df_agg = df_year[mask]
                if not df_agg.empty:
                    df_year = df_agg

            if "age" in df_year.columns:
                # Add age bucket column
                df_year["age_bucket"] = df_year["age"].apply(map_age_bucket)

                # Aggregate to country-age level (mean across remaining rows)
                natl = (
                    df_year.groupby(["iso3", "age_bucket"])["median"]
                    .mean()
                    .reset_index()
                    .rename(columns={"median": "value"})
                )
            else:
                # Simpler structure: already aggregated, no age info
                natl = (
                    df_year.groupby("iso3")["median"]
                    .mean()
                    .reset_index()
                    .rename(columns={"median": "value"})
                )
                natl["age_bucket"] = "All ages"

            natl["varcode"] = varcode
            item_data.append(natl)

        # Aggregate multiple GDD variables into the same food group
        # For example, fruits = fruits + fruit juices, starchy_vegetable = potatoes + other starchy veg
        if item_data:
            if len(item_data) == 1:
                # Single variable maps to this food group
                combined = item_data[0].copy()
                combined["item"] = model_item
                combined = combined[["iso3", "age_bucket", "value", "item"]]
            else:
                # Multiple variables map to this food group - sum them by country-age
                logger.info(
                    "Aggregating %d GDD variables for %s", len(item_data), model_item
                )
                combined = pd.concat(item_data, ignore_index=True)
                combined = (
                    combined.groupby(["iso3", "age_bucket"])["value"]
                    .sum()
                    .reset_index()
                )
                combined["item"] = model_item

            combined["year"] = reference_year
            all_data.append(combined)

    if not all_data:
        logger.error("No data collected from GDD files")
        sys.exit(1)

    # Concatenate all food groups
    result = pd.concat(all_data, ignore_index=True)

    # Add unit column with item-specific descriptions
    # GDD reports foods in "as consumed" weights (fresh/cooked, not dry)
    # See Miller et al. (2017) Global Dietary Database methodology
    def get_unit(item):
        if item == "dairy":
            return (
                "g/day (milk equiv)"  # Total milk equivalents from all dairy products
            )
        if item == "sugar":
            return "g/day (refined sugar eq)"
        if item in DIRECT_FOOD_ITEMS:
            return "g/day (dry wt)"  # Dry commodity weight (converted from brewed)
        return "g/day (fresh wt)"  # Fresh/cooked "as consumed" weight

    result["unit"] = result["item"].apply(get_unit)
    result = result.rename(columns={"iso3": "country", "age_bucket": "age"})

    # Reorder columns to standard format with age stratification
    result = result[["unit", "item", "country", "age", "year", "value"]]

    # Sort by country, item, and age for readability
    result = result.sort_values(["country", "item", "age"]).reset_index(drop=True)

    logger.info(
        "Processed %d country-item-age combinations for %d countries and %d age groups",
        len(result),
        result["country"].nunique(),
        result["age"].nunique(),
    )
    logger.info("Food groups: %s", sorted(result["item"].unique()))
    logger.info("Age groups: %s", sorted(result["age"].unique()))

    # Fill in missing countries using proxy data from similar countries
    # This is for territories/dependencies that don't have separate GDD data
    COUNTRY_PROXIES = {
        "ASM": "WSM",  # American Samoa -> Samoa
        "GUF": "FRA",  # French Guiana -> France
        "PRI": "USA",  # Puerto Rico -> USA
        "SOM": "ETH",  # Somalia -> Ethiopia (similar region, data available)
    }

    required_countries = set(snakemake.params["countries"])
    requested_food_groups = set(snakemake.params["food_groups"])
    output_countries = set(result["country"].unique())
    output_food_groups = set(result["item"].unique())

    # Only validate food groups that we actually tried to extract from GDD
    # Some food groups (like oil, poultry) are not tracked in dietary surveys
    expected_food_groups = set(food_group_vars.keys())

    missing_countries = required_countries - output_countries
    if missing_countries:
        filled = []
        still_missing = []
        for missing in sorted(missing_countries):
            if missing in COUNTRY_PROXIES:
                proxy = COUNTRY_PROXIES[missing]
                if proxy in output_countries:
                    # Duplicate proxy country's data for the missing country (all age groups)
                    proxy_data = result[result["country"] == proxy].copy()
                    proxy_data["country"] = missing
                    result = pd.concat([result, proxy_data], ignore_index=True)
                    filled.append(f"{missing} (using {proxy} data)")
                else:
                    still_missing.append(missing)
            else:
                still_missing.append(missing)

        if filled:
            logger.info("Filled %d missing countries using proxies:", len(filled))
            for entry in filled:
                logger.info("  - %s", entry)

        # Update missing list after filling
        output_countries = set(result["country"].unique())
        missing_countries = required_countries - output_countries

    # Validate that we have all required countries and food groups
    if missing_countries:
        raise ValueError(
            f"[prepare_gdd_dietary_intake] ERROR: GDD dietary data is missing {len(missing_countries)} required countries: "
            f"{sorted(missing_countries)[:20]}{'...' if len(missing_countries) > 20 else ''}. "
            f"Please ensure the GDD download includes all countries listed in config."
        )

    missing_food_groups = expected_food_groups - output_food_groups
    if missing_food_groups:
        raise ValueError(
            f"[prepare_gdd_dietary_intake] ERROR: GDD dietary data is missing {len(missing_food_groups)} expected food groups: "
            f"{sorted(missing_food_groups)}. Available: {sorted(output_food_groups)}. "
            f"Please ensure the GDD download includes all necessary data."
        )

    # Report food groups not available in GDD
    unavailable_in_gdd = requested_food_groups - expected_food_groups
    if unavailable_in_gdd:
        for missing_item in sorted(unavailable_in_gdd):
            logger.warning(
                "No GDD intake data for '%s'. Skipping.",
                missing_item,
            )

    logger.info(
        "✓ Validation passed: all required countries and %d GDD-tracked food groups present",
        len(expected_food_groups),
    )

    # Write output
    result.to_csv(output_file, index=False)


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
