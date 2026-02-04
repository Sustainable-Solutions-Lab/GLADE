#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare raw food supply data from FAOSTAT Food Balance Sheets (FBS).

Reads item-level supply data (kg/capita/year) for all items mapped in
the food item mapping file from a FAOSTAT FBS bulk CSV. This raw data
is used for calculating within-group food consumption ratios.

Input:
    - data/faostat_food_item_map.csv: Mapping from model foods to FBS items
    - FAOSTAT FBS bulk CSV

Output:
    - CSV with columns: item_code, item_name, country, supply_kg_per_capita_year
      Raw per-capita food supply from FBS (not adjusted for waste)
"""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
    FBS_COUNTRY_FALLBACKS,
    add_iso3_column,
    filter_bulk,
    load_bulk_csv,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def main():
    countries = [str(c).upper() for c in snakemake.params.countries]
    reference_year = int(snakemake.params.reference_year)
    food_item_map_path = snakemake.input.food_item_map
    fbs_csv = snakemake.input.fbs_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.fbs_items

    # Load food-to-FBS-item mapping
    food_map_df = pd.read_csv(food_item_map_path, comment="#")
    unique_item_codes = food_map_df["item_code"].dropna().astype(int).unique().tolist()

    if not unique_item_codes:
        raise ValueError("No item codes found in food item mapping file")

    logger.info("Found %d unique FBS item codes to fetch", len(unique_item_codes))

    # Load bulk CSV
    logger.info("Loading FAOSTAT FBS bulk CSV")
    bulk = load_bulk_csv(fbs_csv)

    elem_code = str(snakemake.params.fbs_element_code)

    # Add ISO3 column
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Include proxy countries in the filter so we can use them as fallbacks
    all_proxies = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    # Filter bulk data
    logger.info(
        "Filtering FAOSTAT FBS data for %d items, %d countries, year %d",
        len(unique_item_codes),
        len(countries),
        reference_year,
    )
    df = filter_bulk(
        bulk,
        element_codes=[elem_code],
        item_codes=[str(c) for c in unique_item_codes],
        years=[reference_year],
        iso3_codes=filter_countries,
    )

    if df.empty:
        raise ValueError("FAOSTAT FBS bulk data returned no data")

    df["country"] = df["iso3"].astype(str).str.upper()
    df["supply_kg_per_capita_year"] = df["Value"].fillna(0.0)
    df["item_code"] = pd.to_numeric(df["Item Code"], errors="coerce")
    df = df.dropna(subset=["item_code"])
    df["item_code"] = df["item_code"].astype(int)
    df["item_name"] = df["Item"].astype(str)

    # Build result DataFrame
    results = df[
        ["item_code", "item_name", "country", "supply_kg_per_capita_year"]
    ].copy()

    # Filter to target countries first, then handle missing via proxies
    target_results = results[results["country"].isin(countries)]
    present_countries = set(target_results["country"].unique())
    missing = set(countries) - present_countries

    if missing:
        logger.info(
            "Attempting to fill %d missing countries via proxies...", len(missing)
        )

        proxy_rows = []
        for iso in missing:
            proxies = FBS_COUNTRY_FALLBACKS.get(iso, [])
            filled = False
            for proxy in proxies:
                proxy_data = results[results["country"] == proxy]
                if not proxy_data.empty:
                    logger.info("Filling %s using proxy %s", iso, proxy)
                    proxy_copy = proxy_data.copy()
                    proxy_copy["country"] = iso
                    proxy_rows.append(proxy_copy)
                    filled = True
                    break
            if not filled:
                available_proxies = ", ".join(FBS_COUNTRY_FALLBACKS.get(iso, []))
                raise ValueError(
                    f"Missing FAOSTAT FBS data for country {iso}. "
                    f"Attempted proxies ({available_proxies}) had no data. "
                    f"Please add valid proxy countries to FBS_COUNTRY_FALLBACKS."
                )

        if proxy_rows:
            target_results = pd.concat([target_results, *proxy_rows], ignore_index=True)

    target_results.to_csv(output_file, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %d items) to %s",
        len(target_results),
        target_results["country"].nunique(),
        target_results["item_code"].nunique(),
        output_file,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
