#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare baseline fiber demand from FAOSTAT production data (QCL domain).

Cotton lint is not tracked in FAOSTAT Food Balance Sheets (it's not food),
so we use QCL production data for "Cotton lint, ginned" (item 767) as a
proxy for fiber demand: each country's baseline fiber demand equals its
lint production.

Input:
    - data/curated/faostat_fiber_demand_map.csv: Fiber-to-model entity map
    - FAOSTAT QCL bulk CSV
    - M49 codes CSV

Output:
    - CSV with columns: source_item, crop, country, demand_mt
"""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
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

    fiber_map_path = snakemake.input.fiber_demand_map
    qcl_csv = snakemake.input.qcl_csv
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output[0]

    # Load fiber demand mapping
    fiber_map = pd.read_csv(fiber_map_path, comment="#")

    item_codes = fiber_map["qcl_item_code"].dropna().astype(int).unique().tolist()
    element_codes = fiber_map["qcl_element_code"].dropna().astype(int).unique().tolist()

    if not item_codes:
        raise ValueError("No QCL item codes found in fiber demand mapping")

    logger.info(
        "Found %d fiber QCL item codes, %d element codes",
        len(item_codes),
        len(element_codes),
    )

    # Load and filter QCL bulk data
    logger.info("Loading FAOSTAT QCL bulk CSV")
    bulk = load_bulk_csv(qcl_csv)

    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    logger.info(
        "Filtering QCL data for %d items, %d countries, year %d",
        len(item_codes),
        len(countries),
        reference_year,
    )
    df = filter_bulk(
        bulk,
        element_codes=[str(c) for c in element_codes],
        item_codes=[str(c) for c in item_codes],
        years=[reference_year],
        iso3_codes=countries,
    )

    if df.empty:
        raise ValueError(
            f"FAOSTAT QCL bulk data returned no rows for items {item_codes}"
        )

    df["country"] = df["iso3"].astype(str).str.upper()
    df["value_tonnes"] = df["Value"].fillna(0.0)
    df["item_code"] = pd.to_numeric(df["Item Code"], errors="coerce")
    df = df.dropna(subset=["item_code"])
    df["item_code"] = df["item_code"].astype(int)

    # Join with fiber mapping to get model entities
    code_to_map = fiber_map.set_index("qcl_item_code")
    results = []
    for _, row in df.iterrows():
        code = int(row["item_code"])
        if code not in code_to_map.index:
            continue
        map_row = code_to_map.loc[code]
        source_item = str(map_row["source_item"])
        crop = str(map_row["crop"])

        value_tonnes = float(row["value_tonnes"])
        if value_tonnes <= 0:
            continue

        # QCL production is in tonnes; convert to Mt
        demand_mt = value_tonnes / 1e6

        results.append(
            {
                "source_item": source_item,
                "crop": crop,
                "country": str(row["country"]),
                "demand_mt": demand_mt,
            }
        )

    results_df = pd.DataFrame(results)

    if results_df.empty:
        raise ValueError("No fiber baseline data produced after filtering")

    # Filter to target countries. Countries without QCL production data
    # have no significant fiber demand, so we assign them zero.
    target_results = results_df[results_df["country"].isin(countries)]
    present_countries = set(target_results["country"].unique())
    missing = set(countries) - present_countries

    if missing:
        logger.info(
            "%d countries have no QCL fiber production data (zero demand): %s",
            len(missing),
            ", ".join(sorted(missing)[:10]) + ("..." if len(missing) > 10 else ""),
        )

    target_results.to_csv(output_file, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %.1f Mt total demand) to %s",
        len(target_results),
        target_results["country"].nunique(),
        target_results["demand_mt"].sum(),
        output_file,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
