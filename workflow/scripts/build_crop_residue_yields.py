# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compute gross dry-matter yields of crop residues used as livestock feed.

The residue supply model -- which crop produces which residue feed item, the
above-ground residue ratio (slope/intercept), and the field utilization
efficiency (FUE) -- lives entirely in ``data/curated/crop_residue_specs.csv``.
This script just applies it to the per-region crop yield tables.

For a given crop, gross above-ground residue is

    gross_residue_kg_per_ha = slope * grain_yield_t_per_ha * 1000 + intercept

with the slope/intercept on a DM-grain basis (GAEZ yields entering here are
already DM; see build_crop_yields.py). The full gross mass is exported so the
downstream soil-incorporation N2O accounting sees all of it; ``fue`` is exported
alongside and applied downstream as the cap on the feed-usable fraction.

Output
------
processing/{name}/crop_residue_yields/{crop}.csv with columns:
    water_supply, crop, feed_item, region, resource_class, country,
    residue_yield_t_per_ha (gross DM), fue
"""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "water_supply",
    "crop",
    "feed_item",
    "region",
    "resource_class",
    "country",
    "residue_yield_t_per_ha",
    "fue",
]


def _load_yield_table(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    pivot = (
        df.pivot(index=["region", "resource_class"], columns="variable", values="value")
        .rename_axis(index=("region", "resource_class"), columns=None)
        .sort_index()
    )
    pivot.index = pivot.index.set_levels(
        pivot.index.levels[1].astype(int), level="resource_class"
    )
    for column in pivot.columns:
        pivot[column] = pd.to_numeric(pivot[column], errors="coerce")
    return pivot


def main() -> None:
    crop = str(snakemake.wildcards.crop)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[index]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_files = dict(snakemake.input.items())  # type: ignore[attr-defined]

    specs = pd.read_csv(input_files["residue_specs"], comment="#")
    crop_specs = specs[specs["crop"] == crop]
    if crop_specs.empty:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.warning("No residue spec for crop '%s'; wrote empty table.", crop)
        return

    regions_gdf = gpd.read_file(input_files["regions"])
    region_to_country = regions_gdf.set_index("region")["country"].to_dict()

    yield_tables: dict[str, pd.DataFrame] = {}
    for ws, key in (("r", "yield_r"), ("i", "yield_i")):
        path = input_files.get(key)
        if path:
            yield_tables[ws] = _load_yield_table(str(path))

    if not yield_tables:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.warning("No yield tables for crop '%s'; wrote empty table.", crop)
        return

    rows: list[dict[str, object]] = []
    for spec in crop_specs.itertuples(index=False):
        for water_supply, yields in yield_tables.items():
            if "yield" not in yields.columns:
                continue
            valid = yields[yields["yield"] > 0].reset_index()
            for _, row in valid.iterrows():
                country = region_to_country.get(str(row["region"]))
                if not country:
                    continue
                gross_kg_per_ha = (
                    spec.slope * float(row["yield"]) * 1000.0 + spec.intercept_kg_ha
                )
                if gross_kg_per_ha <= 0:
                    continue
                rows.append(
                    {
                        "water_supply": water_supply,
                        "crop": crop,
                        "feed_item": spec.feed_item,
                        "region": str(row["region"]),
                        "resource_class": int(row["resource_class"]),
                        "country": country,
                        "residue_yield_t_per_ha": gross_kg_per_ha / 1000.0,
                        "fue": float(spec.fue),
                    }
                )

    if not rows:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.warning("Residue yields for crop '%s' are empty.", crop)
        return

    residue_df = pd.DataFrame(rows).sort_values(
        ["feed_item", "water_supply", "region", "resource_class"]
    )
    residue_df.to_csv(output_path, index=False)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
