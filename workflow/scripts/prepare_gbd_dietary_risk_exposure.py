#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Process IHME GBD 2019 dietary risk exposure data.

Reads per-risk-factor CSV files from the GBD dietary risk dataset, extracts
country-level intake estimates for adults 25+, maps GBD risk factor names
to model food groups, and outputs a consolidated exposure file.

Input:
    - Directory of GBD dietary risk CSVs (one per risk factor)
    - Reference year from config

Output:
    - CSV with columns: food_group, country, consumption_g_per_day, lower, upper

GBD CSV columns:
    measure_id, measure_name, location_set, location_id, location_name,
    sex_id, sex_name, age_group_id, age_group_name, year_id, metric_id,
    metric_name, unit, val, upper, lower
"""

import logging
from pathlib import Path

import pandas as pd
import pycountry

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Reuse country name overrides from prepare_gbd_mortality.py
COUNTRY_NAME_OVERRIDES = {
    "Bolivia (Plurinational State of)": "BOL",
    "Bonaire, Saint Eustatius and Saba": "BES",
    "Cabo Verde": "CPV",
    "Côte d'Ivoire": "CIV",
    "Democratic People's Republic of Korea": "PRK",
    "Democratic Republic of the Congo": "COD",
    "French Guiana": "GUF",
    "Iran (Islamic Republic of)": "IRN",
    "Lao People's Democratic Republic": "LAO",
    "Micronesia (Federated States of)": "FSM",
    "Niger": "NER",
    "Republic of Korea": "KOR",
    "Republic of Moldova": "MDA",
    "Republic of the Congo": "COG",
    "Saint Barthélemy": "BLM",
    "Saint Martin (French part)": "MAF",
    "Sint Maarten (Dutch part)": "SXM",
    "The former Yugoslav Republic of Macedonia": "MKD",
    "Türkiye": "TUR",
    "United Kingdom of Great Britain and Northern Ireland": "GBR",
    "United Republic of Tanzania": "TZA",
    "United States of America": "USA",
    "United States Virgin Islands": "VIR",
    "Venezuela (Bolivarian Republic of)": "VEN",
    "Viet Nam": "VNM",
}

# Map GBD risk factor file suffixes to model food group names.
# Only risk factors relevant for food group total estimation are included.
GBD_RISK_TO_FOOD_GROUP = {
    "FRUIT": "fruits",
    "VEG": "vegetables",
    "WHOLEGRAINS": "whole_grains",
    "LEGUMES": "legumes",
    "NUTS": "nuts_seeds",
    "REDMEAT": "red_meat",
    "MILK": "milk",  # Cross-validation only; not used for dairy group total
}


def map_country_name_to_iso3(name: str) -> str | None:
    """Map GBD location name to ISO3 code using pycountry + manual overrides."""
    if name in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[name]
    try:
        matches = pycountry.countries.search_fuzzy(name)
        if matches:
            return matches[0].alpha_3
    except LookupError:
        pass
    return None


def main():
    gbd_dir = Path(snakemake.input.gbd_dir)
    reference_year = int(snakemake.params.reference_year)
    output_file = snakemake.output.exposure

    logger.info("Processing GBD dietary risk exposure data from %s", gbd_dir)
    logger.info("Reference year: %d", reference_year)

    # Find all dietary risk CSV files in the directory
    csv_files = sorted(gbd_dir.glob("IHME_GBD_2019_DIET_RISK_*.CSV"))
    if not csv_files:
        raise FileNotFoundError(f"No GBD dietary risk CSV files found in {gbd_dir}")
    logger.info("Found %d GBD dietary risk CSV files", len(csv_files))

    # Build country name -> ISO3 cache (populated on first encounter)
    country_cache: dict[str, str | None] = {}

    all_results = []

    for csv_path in csv_files:
        # Extract risk factor name from filename
        # Pattern: IHME_GBD_2019_DIET_RISK_1990_2019_{RISK}_Y2021M09D27.CSV
        stem = csv_path.stem
        parts = stem.split("_")
        # Risk factor name is between "2019" (second occurrence) and "Y2021..."
        # Find the risk factor: everything after the 8th underscore-separated part
        # up to the date suffix
        risk_name = None
        for i, part in enumerate(parts):
            if part.startswith("Y20"):
                risk_name = "_".join(parts[7:i])
                break
        if risk_name is None:
            logger.warning(
                "Could not parse risk factor from filename: %s", csv_path.name
            )
            continue

        if risk_name not in GBD_RISK_TO_FOOD_GROUP:
            logger.info("Skipping risk factor %s (not mapped to food group)", risk_name)
            continue

        food_group = GBD_RISK_TO_FOOD_GROUP[risk_name]
        logger.info("Processing %s -> %s", risk_name, food_group)

        df = pd.read_csv(csv_path)

        # Filter: sex=Both, age=25 plus, country-level only
        df = df[
            (df["sex_name"] == "Both")
            & (df["age_group_name"] == "25 plus")
            & (df["location_set"] == "GBD")
        ].copy()

        if df.empty:
            logger.warning(
                "No data for sex=Both, age=25+ in %s",
                csv_path.name,
            )
            continue

        # Filter to reference year, with fallback to nearest available
        df_year = df[df["year_id"] == reference_year]
        if df_year.empty:
            available_years = sorted(df["year_id"].unique())
            nearest_year = min(available_years, key=lambda y: abs(y - reference_year))
            logger.warning(
                "No data for year %d in %s; using nearest year %d",
                reference_year,
                csv_path.name,
                nearest_year,
            )
            df_year = df[df["year_id"] == nearest_year]
        df = df_year

        # Map location names to ISO3
        for name in df["location_name"].unique():
            if name not in country_cache:
                country_cache[name] = map_country_name_to_iso3(name)

        df["country"] = df["location_name"].map(country_cache)

        # Drop locations that don't map to valid ISO3 (regional aggregates, etc.)
        unmapped = df[df["country"].isna()]["location_name"].unique()
        if len(unmapped) > 0:
            logger.info(
                "Dropped %d locations without ISO3 mapping for %s (regional aggregates)",
                len(unmapped),
                risk_name,
            )
        df = df[df["country"].notna()].copy()

        if df.empty:
            logger.warning("No valid country data for %s", risk_name)
            continue

        # Extract values
        result = pd.DataFrame(
            {
                "food_group": food_group,
                "country": df["country"].values,
                "consumption_g_per_day": pd.to_numeric(
                    df["val"], errors="coerce"
                ).values,
                "lower": pd.to_numeric(df["lower"], errors="coerce").values,
                "upper": pd.to_numeric(df["upper"], errors="coerce").values,
            }
        )
        all_results.append(result)
        logger.info(
            "  %s: %d countries, mean=%.1f g/day",
            food_group,
            result["country"].nunique(),
            result["consumption_g_per_day"].mean(),
        )

    if not all_results:
        raise ValueError("No GBD dietary risk exposure data extracted")

    combined = pd.concat(all_results, ignore_index=True)
    combined = combined.sort_values(["food_group", "country"]).reset_index(drop=True)

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    combined.to_csv(output_file, index=False)
    logger.info(
        "Wrote %d rows (%d food groups, %d countries) to %s",
        len(combined),
        combined["food_group"].nunique(),
        combined["country"].nunique(),
        output_file,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
