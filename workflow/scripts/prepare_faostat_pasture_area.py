# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract per-country permanent meadows & pastures area from FAOSTAT RL.

Reads the FAOSTAT Land Use (RL) bulk Parquet, filters for
Item Code 6655 ("Land under permanent meadows and pastures") and
Element Code 5110 ("Area"), and outputs per-country area in 1000 ha.
"""

import logging
from pathlib import Path

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def main() -> None:
    rl_path = snakemake.input.rl_parquet  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    baseline_year = int(snakemake.params.baseline_year)  # type: ignore[name-defined]

    item_code = 6655  # Land under permanent meadows and pastures
    element_code = 5110  # Area (1000 ha)

    logger.info("Loading FAOSTAT RL bulk data")
    bulk = load_bulk(rl_path)

    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    df = filter_bulk(
        bulk,
        element_codes=[element_code],
        item_codes=[item_code],
        years=[baseline_year],
        iso3_codes=countries,
    )

    logger.info(
        "Filtered FAOSTAT pasture area for year %d: %d rows",
        baseline_year,
        len(df),
    )

    if df.empty:
        raise RuntimeError(
            f"FAOSTAT returned no pasture area data for year {baseline_year}"
        )

    df = df.dropna(subset=["Value"])
    df["country"] = df["iso3"].astype(str).str.upper()

    # FAOSTAT RL reports area in 1000 ha
    result = (
        df.groupby("country", as_index=False)["Value"]
        .sum()
        .rename(columns={"Value": "area_kha"})
        .sort_values("country")
    )

    logger.info(
        "Pasture area for %d countries, total %.1f Mha",
        len(result),
        result["area_kha"].sum() / 1e3,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )
    main()
