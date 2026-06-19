#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate SYNTHETIC data for the Carbon Price Dial widget during UI dev.

Real region geometries, fabricated scenario trends, in the same two-mode schema
as export_data.py (fixed vs flexible diet, each with diet + feed decomposition)
so the front end is developed against this and swapped to the real export with
no code changes.

    pixi run python web/carbon-dial/make_synthetic.py
"""

import json
from pathlib import Path

import geopandas as gpd
import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "config" / "default.yaml"
OUT_DIR = REPO / "docs" / "_static" / "carbon-dial" / "data"

PRICES = [
    0,
    10,
    12,
    15,
    19,
    23,
    28,
    34,
    42,
    52,
    64,
    78,
    96,
    118,
    145,
    179,
    219,
    270,
    331,
    407,
    500,
]
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
]
FEED_CATS = [
    ("Grass & leaves", "#6BA368"),
    ("Crop residues", "#C8B273"),
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
BASE_DIET = {
    "grain": 1100,
    "whole_grains": 320,
    "starchy_vegetable": 520,
    "vegetables": 1080,
    "fruits": 900,
    "legumes": 130,
    "nuts_seeds": 45,
    "oil": 205,
    "sugar": 180,
    "stimulants": 60,
    "dairy": 900,
    "eggs": 95,
    "poultry": 140,
    "red_meat": 350,
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    group_colors = {g: d["color"] for g, d in cfg["plotting"]["crop_groups"].items()}
    map_groups = [g for g in group_colors if g not in EXCLUDED_MAP_GROUPS]

    gdf = (
        gpd.read_file(
            REPO / "processing" / "ghg_sensitivity_fixed_diet" / "regions.geojson"
        )
        .to_crs(4326)
        .reset_index(drop=True)
    )
    gdf["id"] = range(len(gdf))
    gdf["geometry"] = gdf.geometry.simplify(0.08, preserve_topology=True)
    geo = json.loads(gdf[["id", "region", "country", "geometry"]].to_json())

    def _round(o, n=2):
        if isinstance(o, list):
            return [_round(x, n) for x in o]
        return round(o, n) if isinstance(o, float) else o

    for feat in geo["features"]:
        feat["geometry"]["coordinates"] = _round(feat["geometry"]["coordinates"])
    (OUT_DIR / "regions.geojson").write_text(json.dumps(geo, separators=(",", ":")))

    n = len(gdf)
    base_group = [i % len(map_groups) for i in range(n)]
    base_area = [0.2 + ((i * 37) % 100) / 100.0 for i in range(n)]
    lat = [gdf.geometry.iloc[i].representative_point().y for i in range(n)]

    def scenario(price, flexible):
        t = price / 500.0
        emissions = {
            "Land-use change": round(0.4 * (1 - 0.6 * t), 3),
            "Enteric & manure (CH4)": round(
                3.6 * (1 - (0.55 if flexible else 0.4) * t), 3
            ),
            "Fertilizer & residues (N2O)": round(2.1 * (1 - 0.4 * t), 3),
            "Sequestration": round(-16.0 * (t**1.3), 3),
        }
        net = round(sum(emissions.values()), 3)
        cost = round(2300 + (1700 if flexible else 1400) * (t**1.15), 1)
        # Diet: static under fixed; shifts plant-ward under flexible.
        diet = {}
        for k, _lbl, _c, animal in FOOD_GROUPS:
            b = BASE_DIET[k]
            if flexible:
                f = (1 - 0.6 * t) if animal else (1 + 0.35 * t)
                diet[k] = round(b * f, 1)
            else:
                diet[k] = round(b, 1)
        # Feed: grass/residues up, grains/cakes down as price rises (both modes).
        feed = {
            "Grass & leaves": round(3900 * (1 + 0.10 * t), 1),
            "Crop residues": round(680 * (1 + 0.30 * t), 1),
            "Grains": round(1220 * (1 - 0.45 * t), 1),
            "Oilseed cakes": round(450 * (1 - 0.40 * t), 1),
            "By-products": round(280 * (1 + 0.05 * t), 1),
        }
        grp = list(base_group)
        inten = []
        for i in range(n):
            spare = t * (0.4 + 0.6 * min(abs(lat[i]) / 60.0, 1.0))
            inten.append(round(min(max(0.0, base_area[i] * (1 - 0.8 * spare)), 1.0), 2))
        return {
            "price": price,
            "emissions": emissions,
            "netEmissions": net,
            "cost": cost,
            "diet": diet,
            "feed": feed,
            "regionGroup": grp,
            "regionIntensity": inten,
        }

    modes = {
        m: {
            "prices": PRICES,
            "scenarios": [scenario(p, m == "flexible") for p in PRICES],
        }
        for m in ("fixed", "flexible")
    }
    out = {
        "meta": {
            "synthetic": True,
            "modes": ["fixed", "flexible"],
            "modeLabels": {"fixed": "Fixed diet", "flexible": "Flexible diet"},
            "mapGroups": [{"name": g, "color": group_colors[g]} for g in map_groups],
            "foodGroups": [
                {"key": k, "label": lbl, "color": c, "animal": a}
                for k, lbl, c, a in FOOD_GROUPS
            ],
            "feedCats": [{"key": k, "color": c} for k, c in FEED_CATS],
            "emissionCategories": EMISSION_CATEGORIES,
        },
        "modes": modes,
    }
    (OUT_DIR / "data.json").write_text(json.dumps(out, separators=(",", ":")))
    print(
        f"Wrote synthetic data.json (2 modes x {len(PRICES)} prices), "
        f"regions.geojson ({n} regions)"
    )


if __name__ == "__main__":
    main()
