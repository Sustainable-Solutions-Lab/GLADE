#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate SYNTHETIC data for the Carbon Price Dial widget during UI dev.

Uses the real region geometries (so the map looks right) but fabricates
plausible scenario trends as a smooth function of carbon price. The output
schema is identical to export_data.py, so the front end is developed against
this and later swapped to the real export with no code changes.

    pixi run python web/carbon-dial/make_synthetic.py
"""

import json
import math
from pathlib import Path

import geopandas as gpd
import yaml

REPO = Path(__file__).resolve().parents[2]
REGIONS_GEOJSON = REPO / "processing" / "doc_figures" / "regions.geojson"
DEFAULT_CONFIG = REPO / "config" / "default.yaml"
OUT_DIR = Path(__file__).resolve().parent / "data"

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
EXCLUDED_MAP_GROUPS = {"Feed crops"}

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
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    group_colors = {g: d["color"] for g, d in cfg["plotting"]["crop_groups"].items()}
    map_groups = [g for g in group_colors if g not in EXCLUDED_MAP_GROUPS]

    # --- regions: simplified geojson with integer ids ---
    gdf = gpd.read_file(REGIONS_GEOJSON).to_crs(4326).reset_index(drop=True)
    gdf["id"] = range(len(gdf))
    gdf["geometry"] = gdf.geometry.simplify(0.08, preserve_topology=True)
    geo = json.loads(gdf[["id", "region", "country", "geometry"]].to_json())

    def _round(o, n=2):
        if isinstance(o, list):
            return [_round(x, n) for x in o]
        if isinstance(o, float):
            return round(o, n)
        return o

    for feat in geo["features"]:
        feat["geometry"]["coordinates"] = _round(feat["geometry"]["coordinates"])
    (OUT_DIR / "regions.geojson").write_text(json.dumps(geo, separators=(",", ":")))

    n = len(gdf)
    # Stable per-region base dominant group + base cropland (deterministic).
    base_group = [i % len(map_groups) for i in range(n)]
    base_area = [0.2 + ((i * 37) % 100) / 100.0 for i in range(n)]
    # Latitude proxy for who spares land first (high-lat temperate spares more).
    lat = [gdf.geometry.iloc[i].representative_point().y for i in range(n)]

    scenarios = []
    for price in PRICES:
        t = price / 500.0  # 0..1
        # Emissions (GtCO2eq): livestock CH4 and N2O fall; sequestration grows
        # negative as carbon price rises (reforestation of spared land).
        luc = 0.4 * (1 - 0.6 * t)
        ch4 = 3.6 * (1 - 0.45 * t)
        n2o = 2.1 * (1 - 0.4 * t)
        seq = -16.0 * (t**1.3)
        emissions = {
            "Land-use change": round(luc, 3),
            "Enteric & manure (CH4)": round(ch4, 3),
            "Fertilizer & residues (N2O)": round(n2o, 3),
            "Sequestration": round(seq, 3),
        }
        net = round(sum(emissions.values()), 3)
        cost = round(2300 + 1400 * (t**1.15), 1)  # bn USD, rising

        # Diet (Mt, global): animal products fall, plants rise.
        diet = {
            "grain": round(1300 + 250 * t, 1),
            "vegetables": round(1100 + 400 * t, 1),
            "fruits": round(900 + 300 * t, 1),
            "legumes": round(120 + 220 * t, 1),
            "nuts_seeds": round(45 + 60 * t, 1),
            "roots": round(750 + 150 * t, 1),
            "oils": round(210 - 20 * t, 1),
            "sugar": round(180 - 40 * t, 1),
            "dairy": round(900 - 380 * t, 1),
            "eggs": round(95 - 20 * t, 1),
            "red_meat": round(360 - 250 * t, 1),
            "poultry": round(140 - 55 * t, 1),
        }
        # Land by crop group (Mha): total cropland shrinks (sparing).
        shrink = 1 - 0.32 * (t**1.1)
        land = {}
        for gi, g in enumerate(map_groups):
            base = 90 + 60 * math.cos(gi)
            land[g] = round(max(base, 5) * shrink, 1)

        # Per-region dominant group + intensity (fades, high-lat first).
        region_group = list(base_group)
        region_intensity = []
        for i in range(n):
            spare = t * (0.4 + 0.6 * min(abs(lat[i]) / 60.0, 1.0))
            inten = max(0.0, base_area[i] * (1 - 0.8 * spare))
            region_intensity.append(round(min(inten, 1.0), 2))

        scenarios.append(
            {
                "price": price,
                "emissions": emissions,
                "netEmissions": net,
                "cost": cost,
                "diet": diet,
                "land": land,
                "regionGroup": region_group,
                "regionIntensity": region_intensity,
            }
        )

    out = {
        "meta": {
            "synthetic": True,
            "prices": PRICES,
            "mapGroups": [{"name": g, "color": group_colors[g]} for g in map_groups],
            "foodGroupColors": FOOD_GROUP_COLORS,
            "emissionCategories": [
                "Land-use change",
                "Enteric & manure (CH4)",
                "Fertilizer & residues (N2O)",
                "Sequestration",
            ],
        },
        "scenarios": scenarios,
    }
    (OUT_DIR / "data.json").write_text(json.dumps(out, separators=(",", ":")))
    print(
        f"Wrote synthetic data.json ({len(scenarios)} scenarios) and "
        f"regions.geojson ({n} regions)"
    )


if __name__ == "__main__":
    main()
