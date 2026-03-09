#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare FAOSTAT supply data to supplement or override GDD dietary intake.

The Global Dietary Database (GDD) is structurally missing or can be
substantially biased low for some commodity groups in validation settings.
This script reads supply data from a FAOSTAT FBS bulk CSV for:
- Dairy (milk, butter, cream - converted to milk equivalents)
- Eggs
- Poultry meat (converted to model retail-meat basis)
- Vegetable oils

Values are converted to g/day per capita. These supplement GDD data in
merge_dietary_sources.py to create complete baseline dietary intake estimates.

Input:
    - FAOSTAT FBS bulk CSV

Output:
    - CSV with columns: unit, item, country, age, year, value
      Values are raw food supply in g/day (not adjusted for waste)
"""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
    FBS_COUNTRY_FALLBACKS,
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# FAOSTAT Item Codes (FBS)
FAO_ITEMS = {
    "poultry": [2734],  # Poultry Meat
    "eggs": [2744],  # Eggs
    # Aggregate vegetable oils intake; used as group total that is later
    # distributed across modeled oil foods.
    "oil": [2914],  # Vegetable Oils
    "dairy": [2848, 2740, 2743],  # Milk (excl butter), Butter/Ghee, Cream
}

# Milk→product extraction rates from FAO dairy commodity tree
DAIRY_MILK_EQUIV_FACTORS = {
    2848: 1.0,  # Milk - Excluding Butter (already milk-equivalent)
    2740: 21.3,  # Butter/Ghee (cow milk commodity tree No. 57)
    2743: 6.7,  # Cream (fresh) milk-equivalent
}

# Standard Age Groups
AGE_GROUPS = [
    "0-1 years",
    "1-2 years",
    "2-5 years",
    "6-10 years",
    "11-74 years",
    "75+ years",
    "All ages",
]


def main():
    countries = snakemake.params.countries
    reference_year = snakemake.params.reference_year
    poultry_carcass_to_retail = float(snakemake.params.poultry_carcass_to_retail)
    fbs_csv = snakemake.input.fbs_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.supply

    if not 0 < poultry_carcass_to_retail <= 1:
        raise ValueError(
            "poultry_carcass_to_retail must be in (0, 1], "
            f"got {poultry_carcass_to_retail}"
        )
    logger.info(
        "Using poultry carcass-to-retail factor: %.3f",
        poultry_carcass_to_retail,
    )

    # Load bulk data
    logger.info("Loading FAOSTAT FBS bulk data")
    bulk = load_bulk(fbs_csv)

    elem_code = int(snakemake.params.fbs_element_code)

    # Collect all item codes
    item_codes = []
    for items in FAO_ITEMS.values():
        item_codes.extend(items)

    # Add ISO3 column
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Include proxy countries in the filter
    all_proxies = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    # Filter bulk data
    logger.info(
        "Filtering FAOSTAT FBS data for %d countries, year %s",
        len(countries),
        reference_year,
    )
    df = filter_bulk(
        bulk,
        element_codes=[elem_code],
        item_codes=item_codes,
        years=[reference_year],
        iso3_codes=filter_countries,
    )

    if df.empty:
        logger.warning("FAOSTAT FBS bulk data returned no data.")

    df["iso3"] = df["iso3"].astype(str).str.upper()
    df["Value"] = df["Value"].fillna(0.0)
    df["Item Code"] = df["Item Code"].fillna(0).astype(int)

    results = []

    # Process present countries
    present_countries = df["iso3"].unique()
    logger.info("Processing data for %d countries...", len(present_countries))

    for country, group_df in df.groupby("iso3"):
        supplies = {}

        # Poultry
        poultry_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["poultry"])]
        # Convert FBS carcass-equivalent poultry mass to model retail-meat basis.
        supplies["poultry"] = poultry_rows["Value"].sum() * poultry_carcass_to_retail

        # Eggs
        egg_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["eggs"])]
        supplies["eggs"] = egg_rows["Value"].sum()

        # Oil
        oil_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["oil"])]
        supplies["oil"] = oil_rows["Value"].sum()

        # Dairy: convert butter/ghee and cream to milk equivalents
        dairy_sum = 0.0
        for item_code in FAO_ITEMS["dairy"]:
            val = group_df[group_df["Item Code"] == item_code]["Value"].sum()
            factor = DAIRY_MILK_EQUIV_FACTORS.get(item_code, 1.0)
            dairy_sum += val * factor
        supplies["dairy"] = dairy_sum

        for item, supply_kg in supplies.items():
            supply_g = (supply_kg * 1000.0) / 365.0
            unit = "g/day (milk equiv)" if item == "dairy" else "g/day (fresh wt)"

            for age in AGE_GROUPS:
                results.append(
                    {
                        "unit": unit,
                        "item": item,
                        "country": country,
                        "age": age,
                        "year": reference_year,
                        "value": supply_g,
                    }
                )

    # Handle missing countries
    missing = set(countries) - set(present_countries)
    if missing:
        logger.info(
            "Attempting to fill %d missing countries via proxies...", len(missing)
        )

        # Build lookup
        data_by_country = {}
        for r in results:
            data_by_country.setdefault(r["country"], []).append(r)

        for iso in missing:
            proxies = FBS_COUNTRY_FALLBACKS.get(iso, [])
            filled = False
            for proxy in proxies:
                if proxy in data_by_country:
                    logger.info("Filling %s using proxy %s", iso, proxy)
                    for row in data_by_country[proxy]:
                        new_row = row.copy()
                        new_row["country"] = iso
                        results.append(new_row)
                    filled = True
                    break
            if not filled:
                logger.error("Could not fill %s - no proxy data available.", iso)
                available_proxies = ", ".join(FBS_COUNTRY_FALLBACKS.get(iso, []))
                raise ValueError(
                    f"Missing FAOSTAT data for country {iso}. "
                    f"Attempted proxies ({available_proxies}) had no data. "
                    f"Please add valid proxy countries to FBS_COUNTRY_FALLBACKS."
                )

    pd.DataFrame(results).to_csv(output_file, index=False)
    logger.info("Wrote %d rows to %s", len(results), output_file)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
