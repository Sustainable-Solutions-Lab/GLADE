#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare FAOSTAT supply data to supplement or override GDD dietary intake.

The Global Dietary Database (GDD) is structurally missing or can be
substantially biased for some commodity groups in validation settings.
This script reads supply data from a FAOSTAT FBS bulk CSV for:
- Dairy (milk, butter, cream - converted to milk equivalents)
- Vegetable oils
- Refined sugar (FBS item 2542 "Sugar Raw Equivalent")

Eggs and poultry meat are intentionally NOT supplemented here: both are
in ``diet.fbs_override_foods`` and get a fully FBS-anchored value in
``estimate_baseline_diet`` from the raw ``faostat_fbs_items.csv``, with
a single carcass-to-retail conversion applied at that point. Emitting
group totals for them here would be dead on arrival (the override
overwrites them) and the c2r dance would have to live in two places.

Sugar is sourced from FAOSTAT here because GDD's v35 ("Added sugars")
variable is reported as %-of-total-energy and converted to g/day using
a 2000 kcal/day denominator that is too coarse. The resulting g/day
values are wildly inflated in some regions (India: 25.87% energy ->
129 g/day, against an FAOSTAT supply of 48 g/day raw / ~36 g/day intake
post-waste, and surveyed actual ~10-15 g/day refined sugar).

Values are converted to g/day per capita. These supplement GDD data in
merge_dietary_sources.py to create complete baseline dietary intake estimates.

Input:
    - FAOSTAT FBS bulk CSV

Output:
    - CSV with columns: unit, item, country, age, year, value
      Values are raw food supply in g/day (not adjusted for waste).
      Only the configured baseline_age row is emitted (consumers filter
      by age and the FAOSTAT supply has no age stratification anyway).
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
    # Aggregate vegetable oils intake; used as group total that is later
    # distributed across modeled oil foods.
    "oil": [2914],  # Vegetable Oils
    "dairy": [2848, 2740, 2743],  # Milk (excl butter), Butter/Ghee, Cream
    "sugar": [2542],  # Sugar (Raw Equivalent)
}

# Milk→product extraction rates from FAO dairy commodity tree
DAIRY_MILK_EQUIV_FACTORS = {
    2848: 1.0,  # Milk - Excluding Butter (already milk-equivalent)
    2740: 21.3,  # Butter/Ghee (cow milk commodity tree No. 57)
    2743: 6.7,  # Cream (fresh) milk-equivalent
}


def main():
    countries = snakemake.params.countries
    reference_year = snakemake.params.reference_year
    baseline_age = str(snakemake.params.baseline_age)
    fbs_csv = snakemake.input.fbs_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.supply

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

        # Oil
        oil_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["oil"])]
        supplies["oil"] = oil_rows["Value"].sum()

        # Sugar (raw equivalent)
        sugar_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["sugar"])]
        supplies["sugar"] = sugar_rows["Value"].sum()

        # Dairy: convert butter/ghee and cream to milk equivalents
        dairy_sum = 0.0
        for item_code in FAO_ITEMS["dairy"]:
            val = group_df[group_df["Item Code"] == item_code]["Value"].sum()
            factor = DAIRY_MILK_EQUIV_FACTORS.get(item_code, 1.0)
            dairy_sum += val * factor
        supplies["dairy"] = dairy_sum

        for item, supply_kg in supplies.items():
            supply_g = (supply_kg * 1000.0) / 365.0
            if item == "dairy":
                unit = "g/day (milk equiv)"
            elif item == "sugar":
                unit = "g/day (refined sugar eq)"
            else:
                unit = "g/day (fresh wt)"
            results.append(
                {
                    "unit": unit,
                    "item": item,
                    "country": country,
                    "age": baseline_age,
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
