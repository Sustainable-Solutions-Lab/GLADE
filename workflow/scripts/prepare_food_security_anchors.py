#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare per-country dietary-energy anchors from FAOSTAT.

Extracts three indicators from the FAOSTAT Suite of Food Security
Indicators (dataset code FS) for the configured reference year:

- Average Dietary Energy Requirement (ADER, item 21057): the population
  energy *need* in kcal/cap/day, derived by FAO from national
  age/sex/height/weight + activity-level data.
- Minimum Dietary Energy Requirement (MDER, item 21056): the lower
  physiological floor; sustained intake below this implies population
  weight loss.
- Dietary Energy Supply (DES, item 220001): kcal/cap/day made available
  by the food system, derived from FAOSTAT Food Balance Sheets. This is
  the FBS-derived availability against which surveys typically
  under-report.

These are independent reference points used by ``validate_baseline_diet``
to flag countries whose GDD-derived baseline-diet kcal totals are
implausible (below MDER, above DES, or far from ADER).

Input:
    - FAOSTAT FS bulk parquet
    - data/curated/M49-codes.csv

Output:
    - CSV with columns: iso3, ader_kcal, mder_kcal, des_kcal
"""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# FAOSTAT FS item codes
ADER_ITEM = 21057  # Average Dietary Energy Requirement (kcal/cap/day)
MDER_ITEM = 21056  # Minimum Dietary Energy Requirement (kcal/cap/day)
DES_ITEM = 220001  # Dietary energy supply, annual (kcal/cap/day)

# FAOSTAT FS uses a single Element Code 6128 ("Value") for all annual indicators
# we extract here; multi-year averages live under different element codes but
# the items we use (ADER/MDER/DES annual) are all 6128.
FS_ELEMENT_CODE = 6128


def main():
    countries = [str(c).upper() for c in snakemake.params.countries]
    reference_year = int(snakemake.params.reference_year)
    fs_path = snakemake.input.fs
    m49_codes = snakemake.input.m49_codes
    output_file = snakemake.output.anchors

    logger.info("Loading FAOSTAT Food Security bulk data")
    bulk = load_bulk(fs_path)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    df = filter_bulk(
        bulk,
        element_codes=[FS_ELEMENT_CODE],
        item_codes=[ADER_ITEM, MDER_ITEM, DES_ITEM],
        years=[reference_year],
        iso3_codes=countries,
    )

    if df.empty:
        raise ValueError(
            f"FAOSTAT FS returned no data for reference_year={reference_year} "
            f"and the configured country list."
        )

    item_to_col = {
        ADER_ITEM: "ader_kcal",
        MDER_ITEM: "mder_kcal",
        DES_ITEM: "des_kcal",
    }
    df = df[["iso3", "Item Code", "Value"]].rename(
        columns={"iso3": "country", "Item Code": "item_code", "Value": "value"}
    )
    df["column"] = df["item_code"].map(item_to_col)

    wide = df.pivot_table(
        index="country", columns="column", values="value", aggfunc="first"
    ).reset_index()

    # Ensure all three columns exist even when a country lacks one
    for col in item_to_col.values():
        if col not in wide.columns:
            wide[col] = pd.NA

    wide = wide[["country", "ader_kcal", "mder_kcal", "des_kcal"]].sort_values(
        "country"
    )

    missing_ader = sorted(
        set(countries) - set(wide.loc[wide["ader_kcal"].notna(), "country"])
    )
    if missing_ader:
        logger.warning(
            "No ADER value for %d configured countries: %s",
            len(missing_ader),
            ", ".join(missing_ader),
        )

    wide.to_csv(output_file, index=False)
    logger.info(
        "Wrote anchors for %d countries to %s",
        len(wide),
        output_file,
    )


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
