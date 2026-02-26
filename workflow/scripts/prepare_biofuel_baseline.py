#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepare baseline biofuel/industrial demand from FAOSTAT Food Balance Sheets.

Reads FBS element 5154 "Other uses (non-food)" for crops mapped in the
biofuel crop mapping file and converts to food-bus units (Mt).

For grain/sugar crops, the FBS reports demand in crop fresh-weight units.
The script converts to dry matter and then applies the pathway factor
from the biofuel mapping to express demand in food-bus units (matching
the ethanol-equivalent items produced by foods.csv pathways).

For oil crops, the FBS already reports demand in oil units, so the
pathway factor is 1.0.

Input:
    - data/curated/faostat_biofuel_crop_map.csv: Biofuel-to-model entity map
    - FAOSTAT FBS bulk CSV
    - Crop moisture content CSV
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
    fbs_element_code = str(snakemake.params.fbs_element_code)

    biofuel_map_path = snakemake.input.biofuel_crop_map
    fbs_csv = snakemake.input.fbs_csv
    m49_codes = snakemake.input.m49_codes
    moisture_path = snakemake.input.moisture_content
    output_file = snakemake.output[0]

    # Load biofuel crop mapping
    biofuel_map = pd.read_csv(biofuel_map_path, comment="#")
    item_codes = biofuel_map["fbs_item_code"].dropna().astype(int).unique().tolist()

    if not item_codes:
        raise ValueError("No FBS item codes found in biofuel crop mapping")

    logger.info("Found %d biofuel FBS item codes to fetch", len(item_codes))

    # Load moisture content for dry-matter conversion
    moisture_df = pd.read_csv(moisture_path, comment="#")
    moisture_lookup = moisture_df.set_index("crop")["moisture_fraction"].to_dict()

    # Load and filter FBS bulk data
    logger.info("Loading FAOSTAT FBS bulk CSV")
    bulk = load_bulk_csv(fbs_csv)

    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    logger.info(
        "Filtering FBS data for %d items, %d countries, year %d",
        len(item_codes),
        len(countries),
        reference_year,
    )
    df = filter_bulk(
        bulk,
        element_codes=[fbs_element_code],
        item_codes=[str(c) for c in item_codes],
        years=[reference_year],
        iso3_codes=countries,
    )

    if df.empty:
        raise ValueError(
            f"FAOSTAT FBS bulk data returned no rows for element {fbs_element_code}"
        )

    df["country"] = df["iso3"].astype(str).str.upper()
    df["value_1000t"] = df["Value"].fillna(0.0)
    df["item_code"] = pd.to_numeric(df["Item Code"], errors="coerce")
    df = df.dropna(subset=["item_code"])
    df["item_code"] = df["item_code"].astype(int)

    # Join with biofuel mapping to get model entities
    code_to_map = biofuel_map.set_index("fbs_item_code")
    results = []
    for _, row in df.iterrows():
        code = int(row["item_code"])
        if code not in code_to_map.index:
            continue
        map_row = code_to_map.loc[code]
        source_item = str(map_row["source_item"])
        crop = str(map_row["crop"])
        pathway_factor = float(map_row["pathway_factor"])
        fbs_is_processed = str(map_row["fbs_is_processed"]).strip().lower() == "true"

        value_1000t = float(row["value_1000t"])
        if value_1000t <= 0:
            continue

        # Convert units: 1000 tonnes → Mt on the food bus.
        # For grain/sugar crops (fbs_is_processed=false): FBS is in crop
        # fresh weight, so convert to DM first, then multiply by
        # pathway_factor to get food-bus units.
        # For oil crops (fbs_is_processed=true): FBS is already in
        # processed (oil) units, so skip the DM conversion.
        if fbs_is_processed:
            demand_mt = value_1000t * pathway_factor / 1000.0
        else:
            moisture = moisture_lookup.get(crop, 0.0)
            demand_mt = value_1000t * (1.0 - moisture) * pathway_factor / 1000.0

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
        raise ValueError("No biofuel baseline data produced after filtering")

    # Filter to target countries. Unlike per-capita food supply data,
    # biofuel demand is in absolute units (Mt). Countries without FBS
    # "Other uses" data have no significant biofuel/industrial demand,
    # so we assign them zero rather than copying a proxy country's
    # absolute demand.
    target_results = results_df[results_df["country"].isin(countries)]
    present_countries = set(target_results["country"].unique())
    missing = set(countries) - present_countries

    if missing:
        logger.info(
            "%d countries have no FBS biofuel data (zero demand): %s",
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
