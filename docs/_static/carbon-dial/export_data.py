#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export compact JSON for the interactive "Carbon Price Dial" web widget.

Reads the paper's published GHG carbon-price sweep directly from the solved
networks of the data reproduction package (Zenodo DOI 10.5281/zenodo.20617942),
extracted under ``ARCHIVE_ROOT`` below, and computes every quantity with the
current GLADE ``extract_*`` analysis functions so the widget matches the paper
figures exactly.

Per carbon-price scenario the output carries: net emissions by category,
food-system cost, diet by food group (Mt), animal feed by the paper's six
source categories (Mt DM, summed over the accounted/unaccounted split), and a
per-region dominant-crop-group index plus cropland and pasture land-use
intensities so the two maps fade as land is spared.

Each solved network (~60k buses, ~140 MB) is processed in its own subprocess so
memory is fully released between scenarios; the orchestrator only assembles the
small per-scenario results, the geometry and the normalisation.

    pixi run python web/carbon-dial/export_data.py
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np
import yaml

# Networks are processed one per subprocess (memory is fully released between
# them); several run concurrently. Each peaks at ~1.5 GB.
MAX_PARALLEL = 4

REPO = Path(__file__).resolve().parents[3]
ARCHIVE_ROOT = REPO / ".cache" / "zenodo" / "extract" / "GLADE-paper-data"
DEFAULT_CONFIG = REPO / "config" / "default.yaml"
# This script sits next to the published widget assets; write the data the
# front end fetches into the sibling data/ directory.
OUT_DIR = Path(__file__).resolve().parent / "data"

MODE_TREES = {
    "fixed": "ghg_sensitivity_fixed_diet",
    "flexible": "ghg_sensitivity_flexible_diet",
}
MODE_LABELS = {"fixed": "Fixed diet", "flexible": "Flexible diet"}
REGIONS_GEOJSON = ARCHIVE_ROOT / "processing" / "central" / "regions.geojson"

EXCLUDED_MAP_GROUPS = {"Feed crops"}

FOOD_GROUPS = [
    ("grain", "Grains", "#E6AB02", False),
    ("whole_grains", "Whole grains", "#BF9B30", False),
    ("starchy_vegetable", "Starchy veg", "#A6761D", False),
    ("vegetables", "Vegetables", "#1B9E77", False),
    ("fruits", "Fruits", "#D95F02", False),
    ("legumes", "Legumes", "#5F8C5A", False),
    ("nuts_seeds", "Nuts & seeds", "#8C6D31", False),
    ("oil", "Oils", "#7570B3", False),
    ("sugar", "Sugar", "#E7298A", False),
    ("stimulants", "Stimulants", "#B15928", False),
    ("dairy", "Dairy", "#80B1D3", True),
    ("eggs", "Eggs", "#E8C547", True),
    ("poultry", "Poultry", "#FB8072", True),
    ("red_meat", "Red meat", "#A6191E", True),
    ("animal_fat", "Animal fat", "#D9A441", True),
]

# source_key -> display (mirrors _SOURCE_KEY_TO_DISPLAY in
# paper/scripts/figure_03_data.py); summed over the accounted/unaccounted split.
FEED_SOURCE_MAP = {
    "grassland": "Grazed grass",
    "exog_forage_cal": "Grazed grass",
    "fodder_crop": "Fodder crops",
    "residue": "Crop residues & roughage",
    "exog_roughage_cal": "Crop residues & roughage",
    "exog_browse": "Crop residues & roughage",
    "grain_crop": "Grains",
    "protein_crop": "Oilseed cakes",
    "exog_protein_cal": "Oilseed cakes",
    "food_byproduct": "By-products",
    "exog_swill": "By-products",
    "exog_other": "By-products",
}
FEED_CATS = [
    ("Grazed grass", "#6a994e"),
    ("Fodder crops", "#a7c957"),
    ("Crop residues & roughage", "#c8b273"),
    ("Grains", "#E6AB02"),
    ("Oilseed cakes", "#7570B3"),
    ("By-products", "#9AA8A2"),
]

EMISSION_CATEGORIES = [
    "Land-use change",
    "Enteric & manure (CH4)",
    "Fertilizer & residues (N2O)",
    "Sequestration",
]
COST_COMPONENTS = [
    "crop_production",
    "animal_production",
    "feed_conversion",
    "fertilizer",
    "land_use",
    "processing",
    "trade",
    "resource_supply",
]

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def crop_group_mapping():
    with open(DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    crop_to_group, group_colors = {}, {}
    for name, gdef in cfg["plotting"]["crop_groups"].items():
        group_colors[name] = gdef["color"]
        for crop in gdef["crops"]:
            crop_to_group[crop] = name
    return crop_to_group, group_colors


# --------------------------------------------------------------------------
# Worker: process ONE solved network and write a small JSON. Runs in its own
# process (heavy imports + the 60k-bus network live only here).
# --------------------------------------------------------------------------
def run_worker(nc_path, analysis_dir, out_path):
    sys.path.insert(0, str(REPO))
    import pandas as pd
    import pypsa

    from workflow.scripts.analysis.extract_objective_breakdown import (
        extract_objective_breakdown,
    )
    from workflow.scripts.analysis.extract_statistics import (
        extract_feed_by_source,
        extract_land_use,
    )

    crop_to_group, _ = crop_group_mapping()
    n = pypsa.Network(str(nc_path))

    emis = {c: 0.0 for c in EMISSION_CATEGORIES}
    ne = pd.read_parquet(Path(analysis_dir) / "net_emissions.parquet")
    for _, row in ne.iterrows():
        gas, source, val = row["gas"], row["source"], float(row["mtco2eq"])
        if gas == "co2" and "sequestr" in source.lower():
            emis["Sequestration"] += val
        elif gas == "co2":
            emis["Land-use change"] += val
        elif gas == "ch4":
            emis["Enteric & manure (CH4)"] += val
        elif gas == "n2o":
            emis["Fertilizer & residues (N2O)"] += val

    ob = extract_objective_breakdown(n).iloc[0]
    cost = float(sum(float(ob[c]) for c in COST_COMPONENTS if c in ob.index))

    # Diet by food group (global Mt): the consume-link p0 (food withdrawn),
    # grouped by the food_group column. Equivalent to
    # extract_food_group_consumption's consumption_mt but ~400x faster, since it
    # skips the heavy n.statistics call and the per-capita / nutrient flows.
    links = n.links.static
    consume = links[links["carrier"] == "food_consumption"]
    snapshot = n.snapshots[-1]
    flow = n.links.dynamic.p0.loc[snapshot].reindex(consume.index).abs()
    diet = {
        k: float(v) for k, v in flow.groupby(consume["food_group"].values).sum().items()
    }

    fbs = extract_feed_by_source(n)
    fbs["disp"] = fbs["source_key"].map(FEED_SOURCE_MAP).fillna("By-products")
    feed = {k: float(v) for k, v in fbs.groupby("disp")["mt_dm"].sum().items()}

    lu = extract_land_use(n)
    lu["group"] = lu["crop"].map(crop_to_group).fillna("Other")
    food_lu = lu[~lu["group"].isin(EXCLUDED_MAP_GROUPS)]
    area_by_region, dom_by_region = {}, {}
    if not food_lu.empty:
        byrg = food_lu.groupby(["region", "group"])["area_mha"].sum().reset_index()
        area_by_region = {
            r: float(a) for r, a in byrg.groupby("region")["area_mha"].sum().items()
        }
        dom = byrg.loc[byrg.groupby("region")["area_mha"].idxmax()]
        dom_by_region = dict(zip(dom["region"], dom["group"]))
    gl = lu[lu["crop"] == "grassland"]
    pasture_by_region = {
        r: float(a) for r, a in gl.groupby("region")["area_mha"].sum().items()
    }

    Path(out_path).write_text(
        json.dumps(
            {
                "emissions": emis,
                "cost": cost,
                "diet": diet,
                "feed": feed,
                "areaByRegion": area_by_region,
                "domByRegion": dom_by_region,
                "pastureByRegion": pasture_by_region,
            }
        )
    )


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------
def discover_scenarios(tree):
    solved = tree / "solved"
    out = []
    if not solved.is_dir():
        return out
    for nc in solved.glob("model_scen-ghg_*.nc"):
        m = re.fullmatch(r"model_scen-ghg_(\d+)\.nc", nc.name)
        if m:
            out.append(
                (int(m.group(1)), nc, tree / "analysis" / f"scen-ghg_{m.group(1)}")
            )
    return sorted(out)


def main():
    import geopandas as gpd
    import pandas as pd

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _, group_colors = crop_group_mapping()
    map_groups = [g for g in group_colors if g not in EXCLUDED_MAP_GROUPS]
    group_index = {g: i for i, g in enumerate(map_groups)}

    gdf = gpd.read_file(REGIONS_GEOJSON).to_crs(4326).reset_index(drop=True)
    region_to_id = {r: i for i, r in enumerate(gdf["region"])}
    n_regions = len(gdf)
    gdf["id"] = range(n_regions)
    land_area_mha = gdf.to_crs("+proj=cea").area.to_numpy() / 1e10
    land_area_mha[land_area_mha <= 0] = np.inf
    simp = gdf.copy()
    simp["geometry"] = simp.geometry.simplify(0.06, preserve_topology=True)
    geo = json.loads(simp[["id", "region", "country", "geometry"]].to_json())

    def _round(o, n=2):
        if isinstance(o, list):
            return [_round(x, n) for x in o]
        return round(o, n) if isinstance(o, float) else o

    for feat in geo["features"]:
        feat["geometry"]["coordinates"] = _round(feat["geometry"]["coordinates"])
    (OUT_DIR / "regions.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    logger.info(
        "Wrote regions.geojson (%d regions, %.0f KB)",
        n_regions,
        (OUT_DIR / "regions.geojson").stat().st_size / 1024,
    )

    feed_keys = [c for c, _ in FEED_CATS]
    max_frac = max_frac_p = 0.0
    raw = {}

    # Vectorised scatter of a {region_name: value} dict onto a region-id array.
    def region_array(d, mapper=None, fill=0.0, dtype=float):
        arr = np.full(n_regions, fill, dtype=dtype)
        if d:
            s = pd.Series(d)
            ids = s.index.map(region_to_id)
            vals = s.map(mapper) if mapper is not None else s
            mask = ids.notna() & vals.notna()
            arr[np.asarray(ids[mask], dtype=int)] = np.asarray(vals[mask], dtype=dtype)
        return arr

    with tempfile.TemporaryDirectory() as tmp:
        jobs = []  # (mode, price, nc, adir, out_path)
        for mode, tname in MODE_TREES.items():
            scens = discover_scenarios(ARCHIVE_ROOT / "results" / tname)
            if not scens:
                logger.warning(
                    "mode %s: no solved networks under results/%s", mode, tname
                )
                continue
            for price, nc, adir in scens:
                jobs.append((mode, price, nc, adir, Path(tmp) / f"{mode}_{price}.json"))

        if not jobs:
            raise SystemExit("No solved networks found; extract the archive first.")

        def _run(job):
            mode, price, nc, adir, out = job
            subprocess.run(
                [sys.executable, __file__, "--worker", str(nc), str(adir), str(out)],
                check=True,
            )
            logger.info("  %s ghg_%s done", mode, price)

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            list(ex.map(_run, jobs))

        # Assemble (fast, sequential). Jobs are in mode-then-sorted-price order.
        for mode, price, _nc, _adir, out in jobs:
            w = json.loads(out.read_text())
            area = region_array(w["areaByRegion"])
            grp = region_array(w["domByRegion"], mapper=group_index, fill=-1, dtype=int)
            pasture = region_array(w["pastureByRegion"])
            max_frac = max(max_frac, float(np.max(area / land_area_mha)))
            max_frac_p = max(max_frac_p, float(np.max(pasture / land_area_mha)))
            raw.setdefault(mode, []).append(
                {
                    "price": price,
                    "emissions": {
                        k: round(v / 1000.0, 4) for k, v in w["emissions"].items()
                    },
                    "netEmissions": round(sum(w["emissions"].values()) / 1000.0, 4),
                    "cost": round(w["cost"], 2),
                    "diet": {k: round(v, 2) for k, v in w["diet"].items()},
                    "feed": {
                        k: round(float(w["feed"].get(k, 0.0)), 2) for k in feed_keys
                    },
                    "_area": area,
                    "_grp": grp,
                    "_pasture": pasture,
                }
            )
        for mode in raw:
            logger.info("mode %s: %d scenarios", mode, len(raw[mode]))

    denom = (max_frac * land_area_mha) if max_frac > 0 else land_area_mha
    denom_p = (max_frac_p * land_area_mha) if max_frac_p > 0 else land_area_mha
    modes = {}
    for mode, recs in raw.items():
        scenarios = []
        for r in recs:
            inten = np.clip(r["_area"] / denom, 0.0, 1.0)
            inten_p = np.clip(r["_pasture"] / denom_p, 0.0, 1.0)
            scenarios.append(
                {
                    "price": r["price"],
                    "emissions": r["emissions"],
                    "netEmissions": r["netEmissions"],
                    "cost": r["cost"],
                    "diet": r["diet"],
                    "feed": r["feed"],
                    "regionGroup": r["_grp"].tolist(),
                    "regionIntensity": [round(float(x), 2) for x in inten],
                    "regionPasture": [round(float(x), 2) for x in inten_p],
                }
            )
        modes[mode] = {"prices": [r["price"] for r in recs], "scenarios": scenarios}

    out = {
        "meta": {
            "modes": list(modes.keys()),
            "modeLabels": {m: MODE_LABELS[m] for m in modes},
            "mapGroups": [{"name": g, "color": group_colors[g]} for g in map_groups],
            "foodGroups": [
                {"key": k, "label": lbl, "color": c, "animal": a}
                for k, lbl, c, a in FOOD_GROUPS
            ],
            "feedCats": [{"key": k, "color": c} for k, c in FEED_CATS],
            "emissionCategories": EMISSION_CATEGORIES,
            "source": "GLADE paper data (Zenodo 10.5281/zenodo.20617942)",
        },
        "modes": modes,
    }
    (OUT_DIR / "data.json").write_text(json.dumps(out, separators=(",", ":")))
    logger.info(
        "Wrote data.json (%.0f KB)", (OUT_DIR / "data.json").stat().st_size / 1024
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main()
