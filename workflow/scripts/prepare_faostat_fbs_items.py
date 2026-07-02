#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare raw food supply data from FAOSTAT Food Balance Sheets.

Reads item-level food supply for all items mapped in the food item
mapping file, in two elements: mass supply (kg/capita/year, consumed by
the within-group share machinery and the FBS overrides) and energy
supply (kcal/capita/day, consumed by the FBS baseline-diet source in
``prepare_fbs_dietary_intake``). Both use a layered fallback (see
:func:`build_layered_fbs_supply` in ``faostat_bulk.py``):

    1. New FBS at the reference year
    2. New FBS at the latest available year for that (country, item)
    3. Historic FBSH (covers Japan, Chad, Mali, Benin, Togo, Burundi,
       Eritrea, Somalia, CAR, ... -- countries not in the new FBS
       dataset, which only covers 179 countries)
    4. The same cascade applied to a proxy country from
       ``FBS_COUNTRY_FALLBACKS``
    5. Skipped (zero supply downstream)

Outputs ``faostat_fbs_items.csv`` (mass) and
``faostat_fbs_items_kcal.csv`` (energy), plus a provenance CSV
summarising which fallback tier each country lands on (mass element).
"""

import logging

import pandas as pd

from workflow.scripts.diet.fbs_intake import EXTRA_FETCH_ITEM_CODES
from workflow.scripts.diet.food_group_projection import (
    FRUITS_BAN_POOL_ITEM_CODES,
    FRUITS_FRT_POOL_ITEM_CODES,
    NUTS_POOL_ITEM_CODES,
    OVG_POOL_ITEM_CODES,
    STARCHY_POOL_ITEM_CODES,
)
from workflow.scripts.faostat_bulk import (
    FBS_COUNTRY_FALLBACKS,
    add_iso3_column,
    build_layered_fbs_supply,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


# FBS item codes that drive the pooled-supply projections in
# estimate_baseline_diet.py. Importing them here keeps the fetch list in
# sync with the projection logic: if the projection references a code,
# this script fetches it. Missing fetches would otherwise silently
# default the pool to 0 (e.g. dropping plantain or pineapples from the
# fruits projection).
POOL_FETCH_CODES: tuple[int, ...] = (
    *OVG_POOL_ITEM_CODES,
    *STARCHY_POOL_ITEM_CODES,
    *NUTS_POOL_ITEM_CODES,
    *FRUITS_BAN_POOL_ITEM_CODES,
    *FRUITS_FRT_POOL_ITEM_CODES,
)


def main():
    countries = [str(c).upper() for c in snakemake.params.countries]
    reference_year = int(snakemake.params.reference_year)
    food_item_map_path = snakemake.input.food_item_map
    fbs_csv = snakemake.input.fbs_csv
    fbsh_csv = snakemake.input.fbsh_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.fbs_items
    provenance_file = snakemake.output.fbs_provenance

    # Load food-to-FBS-item mapping. EXTRA_FETCH_ITEM_CODES covers items
    # only the FBS diet source needs (butter, cream, vegetable-oils
    # aggregate); fetching them unconditionally keeps this rule
    # source-independent.
    food_map_df = pd.read_csv(food_item_map_path, comment="#")
    explicit_codes = food_map_df["item_code"].dropna().astype(int).unique().tolist()
    unique_item_codes = sorted(
        set(explicit_codes) | set(POOL_FETCH_CODES) | set(EXTRA_FETCH_ITEM_CODES)
    )

    if not unique_item_codes:
        raise ValueError("No item codes found in food item mapping file")

    extra_codes = sorted(set(POOL_FETCH_CODES) - set(explicit_codes))
    if extra_codes:
        logger.info(
            "Adding %d pooled FBS codes referenced by POOL_PROJECTIONS "
            "but absent from food_item_map.csv: %s",
            len(extra_codes),
            extra_codes,
        )

    logger.info("Found %d unique FBS item codes to fetch", len(unique_item_codes))

    mass_elem_code = int(snakemake.params.fbs_element_code)
    kcal_elem_code = int(snakemake.params.fbs_kcal_element_code)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)

    # Include proxy countries in the filter so we can use them as fallbacks
    all_proxies: set[str] = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    # Load and filter both bulks once for both elements, then resolve the
    # layered fallback per element.
    logger.info("Loading FAOSTAT new FBS bulk")
    fbs_bulk = add_iso3_column(load_bulk(fbs_csv), m49_to_iso3)
    fbs_df = filter_bulk(
        fbs_bulk,
        element_codes=[mass_elem_code, kcal_elem_code],
        item_codes=unique_item_codes,
        iso3_codes=filter_countries,
    )

    logger.info("Loading FAOSTAT historic FBSH bulk")
    fbsh_bulk = add_iso3_column(load_bulk(fbsh_csv), m49_to_iso3)
    fbsh_df = filter_bulk(
        fbsh_bulk,
        element_codes=[mass_elem_code, kcal_elem_code],
        item_codes=unique_item_codes,
        iso3_codes=filter_countries,
    )

    def layered(elem_code: int) -> pd.DataFrame:
        return build_layered_fbs_supply(
            fbs_df=fbs_df[fbs_df["Element Code"] == elem_code],
            fbsh_df=fbsh_df[fbsh_df["Element Code"] == elem_code],
            countries=countries,
            item_codes=unique_item_codes,
            reference_year=reference_year,
        )

    result = layered(mass_elem_code)
    if result.empty:
        raise ValueError(
            "Layered FBS/FBSH fallback produced zero rows for all "
            f"{len(countries)} target countries"
        )

    kcal_result = layered(kcal_elem_code).rename(
        columns={"supply_kg_per_capita_year": "kcal_per_capita_day"}
    )
    if kcal_result.empty:
        raise ValueError(
            "Layered FBS/FBSH fallback produced zero kcal-supply rows for "
            f"all {len(countries)} target countries"
        )
    kcal_result.to_csv(snakemake.output.fbs_items_kcal, index=False)
    logger.info(
        "Wrote %d kcal-supply rows (%d countries, %d items) to %s",
        len(kcal_result),
        kcal_result["country"].nunique(),
        kcal_result["item_code"].nunique(),
        snakemake.output.fbs_items_kcal,
    )

    # Downstream consumers expect at least country, item_code, item_name,
    # supply_kg_per_capita_year. The added source/year columns are the
    # provenance trail; ignored by readers that don't know about them.
    result.to_csv(output_file, index=False)

    # Summary: how many cells per source family
    fam = result["source"].str.split(":").str[0]
    logger.info("Source distribution (per cell):\n%s", fam.value_counts().to_string())

    # Per-country provenance summary (one row per country, columns per family)
    summary = result.assign(family=fam)
    by_country = (
        summary.groupby(["country", "family"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    by_country.to_csv(provenance_file, index=False)
    logger.info("Wrote provenance summary to %s", provenance_file)

    # Log the countries that needed any fallback beyond direct new-FBS
    fam_pivot = summary.groupby(["country", "family"]).size().unstack(fill_value=0)
    fbs_col = "FBS"
    if fbs_col in fam_pivot.columns:
        non_fbs_cells = fam_pivot.drop(columns=[fbs_col]).sum(axis=1)
    else:
        non_fbs_cells = fam_pivot.sum(axis=1)
    needs_fallback = non_fbs_cells[non_fbs_cells > 0].sort_values(ascending=False)
    if len(needs_fallback) > 0:
        logger.info(
            "%d countries used a fallback for at least one item " "(top: %s)",
            len(needs_fallback),
            ", ".join(needs_fallback.head(10).index),
        )

    n_total = len(countries) * len(unique_item_codes)
    n_resolved = len(result)
    n_missing = n_total - n_resolved
    if n_missing:
        logger.warning(
            "Layered fallback left %d/%d (country,item) cells unresolved; "
            "downstream treats these as 0 supply",
            n_missing,
            n_total,
        )

    logger.info(
        "Wrote %d rows (%d countries, %d items) to %s",
        n_resolved,
        result["country"].nunique(),
        result["item_code"].nunique(),
        output_file,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
