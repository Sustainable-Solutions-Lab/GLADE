#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare FAOSTAT supply data to supplement GDD dietary intake.

The Global Dietary Database (GDD) lacks data for certain food groups. This
script reads supply data from a FAOSTAT FBS bulk CSV for:
- Dairy (milk, butter, cream - converted to milk equivalents)
- Poultry meat
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
    add_iso3_column,
    filter_bulk,
    get_element_map,
    load_bulk_csv,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# FAOSTAT Item Codes (FBS)
FAO_ITEMS = {
    "poultry": [2734],  # Poultry Meat
    "oil": [2586],  # Vegetable Oils
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

# Proxy mapping for missing countries
FALLBACK_MAPPING = {
    "ASM": ["WSM", "USA"],
    "BEN": ["TGO", "BFA", "NGA"],
    "BRN": ["MYS", "SGP"],
    "BTN": ["NPL", "IND"],
    "CAF": ["TCD", "CMR", "COG"],
    "ERI": ["ETH"],
    "GNQ": ["GAB", "CMR"],
    "GUF": ["GUY", "SUR", "FRA"],
    "PRI": ["USA", "DOM"],
    "PSE": ["JOR", "ISR"],
    "SDN": ["EGY", "ETH"],
    "SSD": ["SDN", "ETH"],
    "SOM": ["ETH"],
    "TWN": ["CHN"],
    "XKX": ["SRB", "ALB"],
    "ESH": ["MAR", "MRT"],
    "JPN": ["KOR", "CHN"],
    "MLI": ["SEN", "BFA", "NER"],
    "BDI": ["RWA", "TZA"],
    "COD": ["COG", "AGO"],
    "SYR": ["JOR", "LBN"],
    "TCD": ["SDN", "NER", "CMR"],
    "TGO": ["GHA", "BFA"],
    "VEN": ["COL", "BRA"],
    "YEM": ["OMN", "SAU"],
}


def main():
    countries = snakemake.params.countries
    reference_year = snakemake.params.reference_year
    fbs_csv = snakemake.input.fbs_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.supply

    # Load bulk CSV
    logger.info("Loading FAOSTAT FBS bulk CSV")
    bulk = load_bulk_csv(fbs_csv)

    # Find element code for food supply quantity
    element_map = get_element_map(bulk)
    elem_code = None
    for label, code in element_map.items():
        if "food supply quantity" in label.lower() and "kg" in label.lower():
            elem_code = code
            break
    if elem_code is None:
        logger.warning(
            "Element 'Food supply quantity (kg/capita/yr)' not found. Using 645."
        )
        elem_code = "645"

    # Collect all item codes
    item_codes = []
    for items in FAO_ITEMS.values():
        item_codes.extend(items)

    # Add ISO3 column
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Include proxy countries in the filter
    all_proxies = set()
    for proxies in FALLBACK_MAPPING.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    # Filter bulk data
    logger.info(
        f"Filtering FAOSTAT FBS data for {len(countries)} countries, year {reference_year}"
    )
    df = filter_bulk(
        bulk,
        element_codes=[elem_code],
        item_codes=[str(c) for c in item_codes],
        years=[reference_year],
        iso3_codes=filter_countries,
    )

    if df.empty:
        logger.warning("FAOSTAT FBS bulk data returned no data.")

    df["iso3"] = df["iso3"].astype(str).str.upper()
    df["Value"] = df["Value"].fillna(0.0)
    df["Item Code"] = (
        pd.to_numeric(df["Item Code"], errors="coerce").fillna(0).astype(int)
    )

    results = []

    # Process present countries
    present_countries = df["iso3"].unique()
    logger.info(f"Processing data for {len(present_countries)} countries...")

    for country, group_df in df.groupby("iso3"):
        supplies = {}

        # Poultry
        poultry_rows = group_df[group_df["Item Code"].isin(FAO_ITEMS["poultry"])]
        supplies["poultry"] = poultry_rows["Value"].sum()

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
            f"Attempting to fill {len(missing)} missing countries via proxies..."
        )

        # Build lookup
        data_by_country = {}
        for r in results:
            data_by_country.setdefault(r["country"], []).append(r)

        for iso in missing:
            proxies = FALLBACK_MAPPING.get(iso, [])
            filled = False
            for proxy in proxies:
                if proxy in data_by_country:
                    logger.info(f"Filling {iso} using proxy {proxy}")
                    for row in data_by_country[proxy]:
                        new_row = row.copy()
                        new_row["country"] = iso
                        results.append(new_row)
                    filled = True
                    break
            if not filled:
                logger.error(f"Could not fill {iso} - no proxy data available.")
                available_proxies = ", ".join(FALLBACK_MAPPING.get(iso, []))
                raise ValueError(
                    f"Missing FAOSTAT data for country {iso}. "
                    f"Attempted proxies ({available_proxies}) had no data. "
                    f"Please add valid proxy countries to FALLBACK_MAPPING or obtain data for {iso}."
                )

    pd.DataFrame(results).to_csv(output_file, index=False)
    logger.info(f"Wrote {len(results)} rows to {output_file}")


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
