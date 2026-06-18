#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export compact JSON for the interactive "Carbon Price Dial" web widget.

Reads the per-scenario analysis tables produced by the GHG price sweep
(``config/web_dial.yaml``, results tree ``results/doc_figures``) plus the
region geometries, and writes a single ``data.json`` consumed by the static
front end in this directory.

The output is intentionally small: per-scenario global aggregates (emissions
by category, food-system cost, diet by food group, land use by crop group)
plus a per-region dominant-crop-group index and a land-use intensity that lets
the map fade as cropland is spared at high carbon prices. Region geometries are
simplified and quantized and written once to ``regions.geojson``.

Run from the repository root:

    pixi run python web/carbon-dial/export_data.py
"""

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = REPO / "results" / "web_dial" / "analysis"
REGIONS_GEOJSON = REPO / "processing" / "web_dial" / "regions.geojson"
DEFAULT_CONFIG = REPO / "config" / "default.yaml"
OUT_DIR = Path(__file__).resolve().parent / "data"

# Carbon prices in the sweep (must match config/web_dial.yaml scenario names).
PRICES = [
    0,
    5,
    10,
    15,
    20,
    25,
    30,
    40,
    50,
    60,
    75,
    90,
    100,
    120,
    140,
    160,
    180,
    200,
    225,
    250,
    300,
    350,
    400,
    450,
    500,
]

# Crop groups excluded from the dominant-group map (feed/fodder is not a human
# food-crop signal). Mirrors EXCLUDED_MAP_GROUPS in the doc figure scripts.
EXCLUDED_MAP_GROUPS = {"Feed crops"}

# Food-group display colours for the diet strip (food_group_consumption uses
# its own categories, distinct from the crop groups used on the map).
FOOD_GROUP_COLORS = {
    "grain": "#E6AB02",
    "roots": "#A6761D",
    "vegetables": "#1B9E77",
    "fruits": "#D95F02",
    "legumes": "#666666",
    "nuts_seeds": "#8C6D31",
    "oils": "#7570B3",
    "sugar": "#E7298A",
    "dairy": "#80B1D3",
    "eggs": "#FFED6F",
    "red_meat": "#A6191E",
    "poultry": "#FB8072",
    "fish": "#4EB3D3",
    "other": "#BBBBBB",
}


def crop_group_mapping() -> tuple[dict, dict]:
    """Return (crop -> group, group -> color) from default.yaml plotting config."""
    with open(DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    crop_to_group: dict[str, str] = {}
    group_colors: dict[str, str] = {}
    for group_name, group_def in cfg["plotting"]["crop_groups"].items():
        group_colors[group_name] = group_def["color"]
        for crop in group_def["crops"]:
            crop_to_group[crop] = group_name
    return crop_to_group, group_colors


def aggregate_emissions(df: pd.DataFrame) -> dict:
    """Aggregate net_emissions rows into a few display categories (MtCO2eq)."""
    out = {
        "Land-use change": 0.0,
        "Enteric & manure (CH4)": 0.0,
        "Fertilizer & residues (N2O)": 0.0,
        "Sequestration": 0.0,
    }
    for _, row in df.iterrows():
        gas, source, val = row["gas"], row["source"], float(row["mtco2eq"])
        if gas == "co2" and "sequestr" in source.lower():
            out["Sequestration"] += val
        elif gas == "co2":
            out["Land-use change"] += val
        elif gas == "ch4":
            out["Enteric & manure (CH4)"] += val
        elif gas == "n2o":
            out["Fertilizer & residues (N2O)"] += val
    return out


def food_system_cost(df: pd.DataFrame) -> float:
    """Physical food-system cost (bn USD): production + processing + trade.

    Excludes the endogenous GHG-price term, the production-stability penalty
    and the consumer-value (utility) term, which are not 'resource' costs.
    """
    row = df.iloc[0]
    components = [
        "crop_production",
        "animal_production",
        "feed_conversion",
        "fertilizer",
        "land_use",
        "processing",
        "trade",
    ]
    return float(sum(float(row[c]) for c in components if c in row))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crop_to_group, group_colors = crop_group_mapping()
    # Stable ordering of map groups (exclude feed crops).
    map_groups = [g for g in group_colors if g not in EXCLUDED_MAP_GROUPS]
    group_index = {g: i for i, g in enumerate(map_groups)}

    # --- regions geometry -> simplified geojson with integer ids ---
    gdf = gpd.read_file(REGIONS_GEOJSON).to_crs(4326)
    gdf = gdf.reset_index(drop=True)
    region_to_id = {r: i for i, r in enumerate(gdf["region"])}
    gdf["id"] = range(len(gdf))
    # Simplify + round coordinates to shrink payload.
    simplified = gdf.copy()
    simplified["geometry"] = simplified.geometry.simplify(0.08, preserve_topology=True)
    geo = json.loads(simplified[["id", "region", "country", "geometry"]].to_json())

    def _round_coords(obj, ndigits=2):
        if isinstance(obj, list):
            return [_round_coords(x, ndigits) for x in obj]
        if isinstance(obj, float):
            return round(obj, ndigits)
        return obj

    for feat in geo["features"]:
        feat["geometry"]["coordinates"] = _round_coords(feat["geometry"]["coordinates"])
    (OUT_DIR / "regions.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    logger.info(
        "Wrote regions.geojson (%d regions, %.0f KB)",
        len(gdf),
        (OUT_DIR / "regions.geojson").stat().st_size / 1024,
    )

    n_regions = len(gdf)

    # First pass: per-scenario per-region food-crop area, to normalise intensity
    # per region across the whole sweep (so the map fades as land is spared).
    region_area = {}  # price -> np.array(n_regions)
    region_group = {}  # price -> np.array(n_regions) dominant group index (-1 none)
    scen_records = []
    available_prices = []

    for price in PRICES:
        adir = ANALYSIS_DIR / f"scen-ghg_{price}"
        if not (adir / "net_emissions.parquet").exists():
            logger.warning("skip price %s (no analysis yet) at %s", price, adir)
            continue
        available_prices.append(price)

        emissions = aggregate_emissions(pd.read_parquet(adir / "net_emissions.parquet"))
        net = sum(emissions.values())
        cost = food_system_cost(pd.read_parquet(adir / "objective_breakdown.parquet"))

        # Diet by food group (global totals, Mt).
        fg = pd.read_parquet(adir / "food_group_consumption.parquet")
        diet = fg.groupby("food_group")["consumption_mt"].sum().round(3).to_dict()

        # Land use by crop group (global, Mha) and per-region dominant group.
        lu = pd.read_parquet(adir / "land_use.parquet")
        lu = lu.copy()
        lu["group"] = lu["crop"].map(crop_to_group).fillna("Other")
        land = (
            lu[~lu["group"].isin(EXCLUDED_MAP_GROUPS)]
            .groupby("group")["area_mha"]
            .sum()
            .round(3)
            .to_dict()
        )

        # Per-region dominant food-crop group + total food-crop area.
        food_lu = lu[~lu["group"].isin(EXCLUDED_MAP_GROUPS)]
        by_region_group = (
            food_lu.groupby(["region", "group"])["area_mha"].sum().reset_index()
        )
        area_arr = np.zeros(n_regions)
        grp_arr = np.full(n_regions, -1, dtype=int)
        if not by_region_group.empty:
            # total area per region
            tot = by_region_group.groupby("region")["area_mha"].sum()
            for region, a in tot.items():
                rid = region_to_id.get(region)
                if rid is not None:
                    area_arr[rid] = a
            # dominant group per region
            idx = by_region_group.groupby("region")["area_mha"].idxmax()
            dom = by_region_group.loc[idx]
            for _, row in dom.iterrows():
                rid = region_to_id.get(row["region"])
                if rid is not None:
                    grp_arr[rid] = group_index.get(row["group"], -1)
        region_area[price] = area_arr
        region_group[price] = grp_arr

        scen_records.append(
            {
                "price": price,
                "emissions": {k: round(v / 1000.0, 4) for k, v in emissions.items()},
                "netEmissions": round(net / 1000.0, 4),  # GtCO2eq
                "cost": round(cost, 3),  # bn USD
                "diet": diet,  # Mt by food group
                "land": land,  # Mha by crop group
            }
        )

    if not available_prices:
        raise SystemExit("No scenarios available yet; solve them first.")

    # Normalise intensity per region by its own max across the sweep.
    region_max = np.zeros(n_regions)
    for price in available_prices:
        region_max = np.maximum(region_max, region_area[price])
    region_max[region_max == 0] = 1.0

    for rec in scen_records:
        price = rec["price"]
        inten = np.clip(region_area[price] / region_max, 0.0, 1.0)
        grp = region_group[price]
        # Compact per-region arrays: group index and intensity (2 decimals).
        rec["regionGroup"] = grp.tolist()
        rec["regionIntensity"] = [round(float(x), 2) for x in inten]

    out = {
        "meta": {
            "prices": available_prices,
            "mapGroups": [{"name": g, "color": group_colors[g]} for g in map_groups],
            "foodGroupColors": FOOD_GROUP_COLORS,
            "emissionCategories": [
                "Land-use change",
                "Enteric & manure (CH4)",
                "Fertilizer & residues (N2O)",
                "Sequestration",
            ],
        },
        "scenarios": scen_records,
    }
    out_path = OUT_DIR / "data.json"
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    logger.info(
        "Wrote data.json (%d scenarios, %.0f KB)",
        len(scen_records),
        out_path.stat().st_size / 1024,
    )
    logger.info("Prices exported: %s", available_prices)


if __name__ == "__main__":
    main()
