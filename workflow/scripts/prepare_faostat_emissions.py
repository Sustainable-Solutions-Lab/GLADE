# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare FAOSTAT emissions data (Domain GT) for global comparison."""

from pathlib import Path

import pandas as pd

from workflow.scripts.faostat_bulk import load_bulk
from workflow.scripts.logging_config import setup_script_logging

if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    output_path = Path(snakemake.output[0])
    gt_csv = snakemake.input.gt_csv
    year = int(snakemake.params.year)

    # FAOSTAT items to fetch (mapped to model categories)
    # Note: We fetch individual gases to apply model GWPs later.
    items = {
        "Crop Residues": 5064,
        "Rice Cultivation": 5060,
        "Burning - Crop residues": 5066,
        "Synthetic Fertilizers": 5061,
        "Drained organic soils": 6729,
        "Enteric Fermentation": 5058,
        "Manure Management": 5059,
        "Manure applied to Soils": 5062,
        "Manure left on Pasture": 5063,
        "Net Forest conversion": 6750,
    }

    # Elements: Emissions in kt (CH4, N2O, CO2)
    elements = {
        "Emissions (CH4)": 7225,  # kt
        "Emissions (N2O)": 7230,  # kt
        "Emissions (CO2)": 7273,  # kt
    }

    logger.info("Loading FAOSTAT GT bulk data")
    bulk = load_bulk(gt_csv)

    # Filter for World (Area Code 5000), requested items, elements, and year
    item_codes = set(items.values())
    element_codes = set(elements.values())

    mask = (
        (bulk["Area Code"] == 5000)
        & bulk["Item Code"].isin(item_codes)
        & bulk["Element Code"].isin(element_codes)
        & (bulk["Year"] == year)
    )
    df = bulk.loc[mask].copy()

    logger.info("Filtered GT data for World, Year %s: %d rows", year, len(df))

    if df.empty:
        raise RuntimeError(
            "FAOSTAT GT bulk data returned no emissions data for the requested selection"
        )

    # Filter and clean
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
    df = df.dropna(subset=["Value"])

    # Create standardized output
    records = []

    for _, row in df.iterrows():
        item = str(row["Item"]).strip()
        element = str(row["Element"]).strip()
        value = float(row["Value"])
        unit = str(row["Unit"]).strip().lower()

        # Normalize unit to kilotonnes (kt).
        # Emission magnitudes span ~3 orders of magnitude across t/kt/Mt;
        # silently assuming kt for an unrecognised vintage could move totals
        # by 1000x, so refuse rather than warn.
        if unit in ("tonnes", "t"):
            factor = 1e-3
        elif unit in ("kilotonnes", "kt", "gigagrams", "gg"):
            factor = 1.0
        else:
            raise ValueError(
                f"Unrecognised FAOSTAT emissions unit '{unit}' for "
                f"item='{item}', element='{element}'; expected one of "
                f"tonnes/t/kilotonnes/kt/gigagrams/gg"
            )

        records.append(
            {"item": item, "element": element, "value_kt": value * factor, "year": year}
        )

    result = pd.DataFrame(records)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    logger.info("Saved %d emission records to %s", len(result), output_path)
