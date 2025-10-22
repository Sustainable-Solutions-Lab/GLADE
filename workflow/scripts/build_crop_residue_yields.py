# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compute net dry-matter yields of crop residues used as livestock feed.

Inputs
------
- Crop yield tables from the preprocessing pipeline (rainfed + irrigated).
- GLEAM Supplement S1 (for residue slope/intercept parameters).
- GLEAM feed tables (for FUE factors per feed code).
- Region geometries (mapping regions to ISO3 countries).

Output
------
processing/{name}/crop_residue_yields.csv with columns:
    water_supply  (r / i)
    crop          (model crop producing the residue)
    feed_item     (residue feed item name)
    region
    resource_class
    country       (ISO3)
    residue_yield_t_per_ha (net dry-matter yield)
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Mapping of GLEAM residue feed codes to model metadata.
# Notes:
#   - Multiple crops can produce the same feed item (e.g., both rice crops → rice_straw).
#   - Fallback FUE values correspond to the "all production systems" figures in GLEAM.
RESIDUE_SPECS: dict[str, dict] = {
    "RSTRAW": {
        "feed_item": "rice_straw",
        "gleam_crop": "Rice",
        "crops": ["dryland-rice", "wetland-rice"],
        "fue_fallback": 0.70,
    },
    "WSTRAW": {
        "feed_item": "wheat_straw",
        "gleam_crop": "Wheat",
        "crops": ["wheat"],
        "fue_fallback": 0.70,
    },
    "BSTRAW": {
        "feed_item": "barley_straw",
        "gleam_crop": "Barley",
        "crops": ["barley", "oat", "rye"],
        "fue_fallback": 0.70,
    },
    "ZSTOVER": {
        "feed_item": "maize_stover",
        "gleam_crop": "Maize",
        "crops": ["maize"],
        "fue_fallback": 0.70,
    },
    "MSTOVER": {
        "feed_item": "millet_stover",
        "gleam_crop": "Millet",
        "crops": ["pearl-millet", "foxtail-millet"],
        "fue_fallback": 0.70,
    },
    "SSTOVER": {
        "feed_item": "sorghum_stover",
        "gleam_crop": "Sorghum",
        "crops": ["sorghum"],
        "fue_fallback": 0.70,
    },
    "TOPS": {
        "feed_item": "sugarcane_tops",
        "gleam_crop": "Sugarcane",
        "crops": ["sugarcane"],
        "fue_fallback": 0.70,
    },
    "PSTRAW": {
        "feed_item": "pulse_straw",
        "gleam_crop": "Pulses",
        "crops": [
            "dry-pea",
            "chickpea",
            "cowpea",
            "gram",
            "phaseolus-bean",
            "pigeonpea",
        ],
        "fue_fallback": 0.90,
    },
    "BNSTEM": {
        "feed_item": "banana_stem",
        "gleam_crop": "Banana fruits",
        "crops": ["banana"],
        "fue_fallback": 0.50,
    },
}


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


def _parse_slope_intercept(excel_path: str) -> dict[str, dict[str, float]]:
    """Return mapping from GLEAM crop name to slope/intercept coefficients."""
    table = pd.read_excel(excel_path, sheet_name="Tab. S.3.1", header=None)
    table = table.dropna(how="all")
    header_idx = table[1].astype(str).str.lower().eq("crop")
    if not header_idx.any():
        raise ValueError("Unable to locate header row in Table S.3.1")
    header_pos = int(header_idx.idxmax())

    data = table.loc[header_pos + 1 :, [1, 3, 4]].copy()
    data.columns = ["gleam_crop", "slope", "intercept"]
    data["gleam_crop"] = data["gleam_crop"].astype(str).str.strip()
    data["slope"] = pd.to_numeric(data["slope"], errors="coerce")
    data["intercept"] = pd.to_numeric(data["intercept"], errors="coerce")

    mapping: dict[str, dict[str, float]] = {}
    for row in data.itertuples(index=False):
        if np.isfinite(row.slope) and np.isfinite(row.intercept):
            mapping[row.gleam_crop] = {
                "slope": float(row.slope),
                "intercept": float(row.intercept),
            }
    return mapping


def _load_fue_lookup(
    monogastric_table: str, ruminant_table: str, default_fallback: float = 0.70
) -> dict[str, float]:
    """Build lookup of feed-code-specific FUE values."""
    lookup: dict[str, float] = {}

    mono = pd.read_csv(monogastric_table, comment="#")
    mono["FUE"] = pd.to_numeric(mono["FUE"], errors="coerce")
    for row in mono.itertuples(index=False):
        if np.isfinite(row.FUE):
            lookup[row.code] = float(row.FUE)

    rum = pd.read_csv(ruminant_table, comment="#")
    rum["FUE"] = pd.to_numeric(rum["FUE"], errors="coerce")
    for row in rum.itertuples(index=False):
        if np.isfinite(row.FUE):
            lookup.setdefault(row.code, float(row.FUE))

    for code, spec in RESIDUE_SPECS.items():
        lookup.setdefault(code, float(spec.get("fue_fallback", default_fallback)))

    return lookup


_CROP_TO_CODES: dict[str, list[str]] = {}
for _code, _spec in RESIDUE_SPECS.items():
    for _crop in _spec["crops"]:
        _CROP_TO_CODES.setdefault(_crop, []).append(_code)


OUTPUT_COLUMNS = [
    "water_supply",
    "crop",
    "feed_item",
    "gleam_code",
    "region",
    "resource_class",
    "country",
    "residue_yield_t_per_ha",
]


def main() -> None:
    crop = str(snakemake.wildcards.crop)  # type: ignore[attr-defined]
    residue_codes = _CROP_TO_CODES.get(crop, [])
    output_path = Path(snakemake.output[0])  # type: ignore[index]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not residue_codes:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.warning(
            "No residue mapping found for crop '%s'; wrote empty table.", crop
        )
        return

    input_files = dict(snakemake.input.items())  # type: ignore[attr-defined]

    slope_lookup = _parse_slope_intercept(input_files["gleam_supplement"])
    fue_lookup = _load_fue_lookup(
        input_files["monogastric_feed_table"],
        input_files["ruminant_feed_table"],
    )

    regions_gdf = gpd.read_file(input_files["regions"])
    region_to_country = regions_gdf.set_index("region")["country"].to_dict()

    yield_tables: dict[str, pd.DataFrame] = {}
    yield_r = input_files.get("yield_r")
    if yield_r:
        yield_tables["r"] = _load_yield_table(str(yield_r))
    yield_i = input_files.get("yield_i")
    if yield_i:
        yield_tables["i"] = _load_yield_table(str(yield_i))

    if not yield_tables:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.warning(
            "No yield tables available for crop '%s'; wrote empty table.", crop
        )
        return

    rows: list[dict[str, object]] = []
    missing_coeffs: list[str] = []

    for gleam_code in residue_codes:
        spec = RESIDUE_SPECS[gleam_code]
        gleam_crop = spec["gleam_crop"]
        coefficients = slope_lookup.get(gleam_crop)
        if coefficients is None:
            missing_coeffs.append(gleam_crop)
            continue

        slope = coefficients["slope"]
        intercept = coefficients["intercept"]
        fue = fue_lookup.get(gleam_code, spec.get("fue_fallback", 0.70))

        for water_supply, yields in yield_tables.items():
            if "yield" not in yields.columns:
                continue
            valid = yields[yields["yield"] > 0].copy()
            if valid.empty:
                continue
            valid = valid.reset_index()
            for _, row in valid.iterrows():
                region = str(row["region"])
                resource_class = int(row["resource_class"])
                country = region_to_country.get(region)
                if not country:
                    continue

                crop_yield_t_per_ha = float(row["yield"])
                gross_residue_kg_per_ha = (
                    slope * crop_yield_t_per_ha * 1000.0 + intercept
                )
                if gross_residue_kg_per_ha <= 0:
                    continue

                net_residue_t_per_ha = gross_residue_kg_per_ha * fue / 1000.0
                if net_residue_t_per_ha <= 0:
                    continue

                rows.append(
                    {
                        "water_supply": water_supply,
                        "crop": crop,
                        "feed_item": spec["feed_item"],
                        "gleam_code": gleam_code,
                        "region": region,
                        "resource_class": resource_class,
                        "country": country,
                        "residue_yield_t_per_ha": net_residue_t_per_ha,
                    }
                )

    if missing_coeffs:
        raise ValueError(
            "Missing slope/intercept coefficients for crops: "
            + ", ".join(sorted(set(missing_coeffs)))
        )

    if not rows:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(output_path, index=False)
        logger.info("Residue yields for crop '%s' are empty.", crop)
        return

    residue_df = pd.DataFrame(rows).sort_values(
        ["feed_item", "water_supply", "region", "resource_class"]
    )
    residue_df.to_csv(output_path, index=False)
    logger.info(
        "Computed %d residue entries for crop '%s' (%d feed items).",
        len(residue_df),
        crop,
        residue_df["feed_item"].nunique(),
    )


if __name__ == "__main__":
    main()
