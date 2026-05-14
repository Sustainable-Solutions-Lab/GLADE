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
    build_layered_fbs_supply,
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
    # Rendered animal fats (lard/tallow). GDD-IA reports fat_ani for only
    # ~146/175 countries; merge_dietary_sources uses this as a fallback
    # for the remaining countries so every consume:rendered-fat:* link
    # gets a baseline value.
    "animal_fat": [2737],  # Fats, Animals, Raw
}

# Milk→product extraction rates from FAO dairy commodity tree
DAIRY_MILK_EQUIV_FACTORS = {
    2848: 1.0,  # Milk - Excluding Butter (already milk-equivalent)
    2740: 21.3,  # Butter/Ghee (cow milk commodity tree No. 57)
    2743: 6.7,  # Cream (fresh) milk-equivalent
}


def main():
    countries = [str(c).upper() for c in snakemake.params.countries]
    reference_year = int(snakemake.params.reference_year)
    baseline_age = str(snakemake.params.baseline_age)
    fbs_csv = snakemake.input.fbs_csv
    fbsh_csv = snakemake.input.fbsh_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.supply

    elem_code = int(snakemake.params.fbs_element_code)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)

    # Collect all item codes
    item_codes: list[int] = []
    for items in FAO_ITEMS.values():
        item_codes.extend(items)
    item_codes = sorted(set(item_codes))

    # Include proxy countries in the filter
    all_proxies: set[str] = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    logger.info("Loading FAOSTAT new FBS bulk")
    fbs_bulk = add_iso3_column(load_bulk(fbs_csv), m49_to_iso3)
    fbs_df = filter_bulk(
        fbs_bulk,
        element_codes=[elem_code],
        item_codes=item_codes,
        iso3_codes=filter_countries,
    )

    logger.info("Loading FAOSTAT historic FBSH bulk")
    fbsh_bulk = add_iso3_column(load_bulk(fbsh_csv), m49_to_iso3)
    fbsh_df = filter_bulk(
        fbsh_bulk,
        element_codes=[elem_code],
        item_codes=item_codes,
        iso3_codes=filter_countries,
    )

    supplies = build_layered_fbs_supply(
        fbs_df=fbs_df,
        fbsh_df=fbsh_df,
        countries=countries,
        item_codes=item_codes,
        reference_year=reference_year,
    )

    if supplies.empty:
        logger.warning("Layered FBS/FBSH fallback produced no rows")

    # Pivot to per-(country, item_code) supply (kg/cap/yr)
    lookup = supplies.set_index(["country", "item_code"])["supply_kg_per_capita_year"]

    results = []
    for country in countries:
        group_supplies: dict[str, float] = {}
        # Oil
        group_supplies["oil"] = sum(
            float(lookup.get((country, code), 0.0)) for code in FAO_ITEMS["oil"]
        )
        # Sugar (raw equivalent)
        group_supplies["sugar"] = sum(
            float(lookup.get((country, code), 0.0)) for code in FAO_ITEMS["sugar"]
        )
        # Dairy: convert butter/ghee and cream to milk equivalents
        group_supplies["dairy"] = sum(
            float(lookup.get((country, code), 0.0))
            * DAIRY_MILK_EQUIV_FACTORS.get(code, 1.0)
            for code in FAO_ITEMS["dairy"]
        )
        # Animal fat (rendered fat, lard/tallow)
        group_supplies["animal_fat"] = sum(
            float(lookup.get((country, code), 0.0)) for code in FAO_ITEMS["animal_fat"]
        )

        for item, supply_kg in group_supplies.items():
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

    pd.DataFrame(results).to_csv(output_file, index=False)
    logger.info("Wrote %d rows to %s", len(results), output_file)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
