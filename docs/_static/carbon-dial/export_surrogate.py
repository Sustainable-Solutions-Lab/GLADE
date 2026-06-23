#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Serialize the carbon-dial MLP surrogates to compact JSON for the browser.

The interactive "Carbon Price Dial" evaluates the GLADE MLP surrogate *directly*
in the browser -- no precomputed scenario grid, no interpolation. This script
extracts, from each fitted ``SurrogateBundle`` (built with the
``config/carbon_dial_surrogate.yaml`` overlay), everything a tiny JS forward
pass needs:

  * the shared MLP pipeline: log-transformed input indices, the StandardScaler
    mean/scale, and the ReLU network weights/biases (identity output);
  * per dial output: its column index in the MLP output vector and the
    per-output target mean/std used to invert standardization;
  * per spatial field: the PCA decoder (mean + components + region keys) and
    the score-column output indices, so the map can be reconstructed and the
    dominant crop group taken per region;
  * the nominal input vector (uncertainty factors at central values) and the
    live-slider parameters (ghg_price, and value_per_yll for the flexible-diet
    surrogate) with their ranges;
  * display meta: food->group map, group/feed/crop colours, per-capita unit
    factors, and per-mode axis ranges (computed by evaluating the surrogate on
    a coarse price x YLL grid, so the panels have stable scales).

Big float arrays are stored as base64-encoded little-endian float32. A numpy
reimplementation of the exact JS forward pass (``_js_forward``) is checked
against the bundle's own ``predict`` / ``predict_field`` so the browser math is
verified before it ships.

    pixi run -e dev python docs/_static/carbon-dial/export_surrogate.py
"""

import base64
import json
import logging
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from workflow.scripts.analysis.surrogate import (  # noqa: E402
    load_bundle,
    predict,
    predict_field,
)
from workflow.scripts.constants import (  # noqa: E402
    DAYS_PER_YEAR,
    GRAMS_PER_MEGATONNE,
    PJ_TO_KCAL,
)

OUT_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_CONFIG = REPO / "config" / "default.yaml"
FOOD_GROUPS_CSV = REPO / "data" / "curated" / "food_groups.csv"

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

BUNDLES = {
    "flexible": REPO / "results" / "gsa" / "surrogates" / "surrogate_gsa_mlp.pkl",
    "fixed": REPO
    / "results"
    / "gsa_fixed_diet"
    / "surrogates"
    / "surrogate_gsa-fd_mlp.pkl",
}
MODE_LABELS = {"fixed": "Fixed diet", "flexible": "Flexible diet"}

SLIDER_PARAMS = ["ghg_price", "value_per_yll"]

# Nominal values for the frozen uncertainty parameters: multiplicative factors
# at 1.0, health relative-risk quantiles at the median, reforestation cap at the
# central.yaml operating point.
NOMINAL = {
    "yield_factor": 1.0,
    "ch4_factor": 1.0,
    "n2o_factor": 1.0,
    "luc_factor": 1.0,
    "flw_factor": 1.0,
    "fcr_factor": 1.0,
    "rr_protective": 0.5,
    "rr_harmful": 0.5,
    "reforest_fraction": 0.5,
}

SCALAR_OUTPUTS = ["co2", "ch4", "n2o", "sequestration", "total_cost", "yll"]
OBJ_PARTS = [
    "ghg_cost",
    "production_stability",
    "diet_stability",
    "consumer_values",
    "health_burden",
]
VECTOR_PREFIXES = ["foods", "foods_energy", "feed_categories"]

# Per-crop-group cropland fields, in display order; the map colours each region
# by argmax over these (mapGroups carries the matching names/colours). The two
# total fields drive cropland intensity (opacity) and the pasture map.
CROP_GROUP_FIELDS = [
    "cropland_cereals_by_region",
    "cropland_legumes_by_region",
    "cropland_roots_tubers_by_region",
    "cropland_vegetables_by_region",
    "cropland_fruits_by_region",
    "cropland_oilseeds_by_region",
    "cropland_sugar_crops_by_region",
    "cropland_stimulants_by_region",
    "cropland_fiber_crops_by_region",
]
FIELD_OUTPUTS = ["cropland_by_region", "grazing_by_region", *CROP_GROUP_FIELDS]

# Food-group display (key, label, colour, is-animal); matches food_groups.csv
# group names. Animal groups are bold-labelled in the diet strip.
FOOD_GROUP_META = [
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
# Feed categories from feed_by_category.parquet (key, label, colour).
FEED_CAT_META = [
    ("Grass & leaves", "Grass & leaves", "#6a994e"),
    ("Crop residues", "Crop residues", "#c8b273"),
    ("Grains", "Grains", "#E6AB02"),
    ("Oilseed cakes", "Oilseed cakes", "#7570B3"),
    ("By-products", "By-products", "#9AA8A2"),
]
EMISSION_CATEGORIES = [
    "Land-use change",
    "Enteric & manure (CH4)",
    "Fertilizer & residues (N2O)",
    "Sequestration",
]
COST_PARTS = [
    ("production", "Production cost", "#3b745f"),
    ("resistance", "Resistance to change", "#d08b3f"),
    ("scc", "Social cost of carbon", "#7570B3"),
    ("health", "Health burden", "#A6191E"),
]


def b64(arr):
    """Base64-encode a little-endian float32 array for compact JS transfer."""
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f4").tobytes()).decode()


def extract_mlp(bundle):
    """Pull the shared pipeline weights out of the MLP bundle."""
    if bundle.method != "mlp":
        raise ValueError(f"expected an mlp bundle, got method={bundle.method!r}")
    pipe = next(iter(bundle.models.values())).model
    mlp = pipe.named_steps["mlp"]
    if mlp.activation != "relu" or mlp.out_activation_ != "identity":
        raise ValueError(
            f"unexpected MLP activations: {mlp.activation}/{mlp.out_activation_}"
        )
    return {
        "log_indices": list(pipe.named_steps["log"].log_indices),
        "scaler_mean": np.asarray(pipe.named_steps["scaler"].mean_, float),
        "scaler_scale": np.asarray(pipe.named_steps["scaler"].scale_, float),
        "weights": [np.asarray(w, float) for w in mlp.coefs_],
        "biases": [np.asarray(b, float) for b in mlp.intercepts_],
    }


def _js_forward(mlp, x_row):
    """Reference for the JS forward pass: raw input row -> standardized MLP out."""
    x = np.array(x_row, float)
    for j in mlp["log_indices"]:
        x[j] = np.log(x[j])
    h = (x - mlp["scaler_mean"]) / mlp["scaler_scale"]
    n = len(mlp["weights"])
    for k in range(n):
        h = h @ mlp["weights"][k] + mlp["biases"][k]
        if k < n - 1:
            h = np.maximum(h, 0.0)
    return h


def output_entry(bundle, name):
    p = bundle.models[name]
    return {
        "i": int(p.output_index),
        "m": float(p.target_mean),
        "s": float(p.target_std),
    }


def nominal_vector(bundle):
    params = list(bundle.param_names)
    spec = bundle.generator_spec["parameters"]
    nominal = np.array(
        [NOMINAL[p] if p in NOMINAL else float("nan") for p in params], float
    )
    sliders = {}
    for sp in SLIDER_PARAMS:
        if sp in params:
            j = params.index(sp)
            sliders[sp] = {
                "index": j,
                "min": float(spec[sp]["lower"]),
                "max": float(spec[sp]["upper"]),
            }
            nominal[j] = float(spec[sp]["lower"])  # placeholder; overridden live
    if np.isnan(nominal).any():
        missing = [p for p, v in zip(params, nominal) if np.isnan(v)]
        raise ValueError(f"no nominal value for params {missing}")
    return params, nominal, sliders


def build_mode(bundle):
    """Assemble the JSON-serializable weights/decoders for one surrogate."""
    mlp = extract_mlp(bundle)
    params, nominal, sliders = nominal_vector(bundle)

    out_map = {}
    for name in SCALAR_OUTPUTS:
        if name in bundle.models:
            out_map[name] = output_entry(bundle, name)
    for part in OBJ_PARTS:
        col = f"objective_breakdown.{part}"
        if col in bundle.models:
            out_map[col] = output_entry(bundle, col)
    vectors = {pref: [] for pref in VECTOR_PREFIXES}
    for col in bundle.output_columns:
        for pref in VECTOR_PREFIXES:
            if col.startswith(pref + "."):
                out_map[col] = output_entry(bundle, col)
                vectors[pref].append(col[len(pref) + 1 :])

    fields = {}
    for fname in FIELD_OUTPUTS:
        if fname not in bundle.field_decoders:
            continue
        dec = bundle.field_decoders[fname]
        for sc in dec.score_columns:
            out_map[sc] = output_entry(bundle, sc)
        fields[fname] = {
            "scoreCols": list(dec.score_columns),
            "keys": list(dec.keys),
            "mean": b64(dec.mean),
            "components": b64(dec.components.reshape(-1)),
            "nComp": int(dec.components.shape[0]),
            "nKeys": int(dec.components.shape[1]),
        }

    payload = {
        "params": params,
        "nominal": nominal.tolist(),
        "sliders": sliders,
        "logIndices": mlp["log_indices"],
        "scalerMean": b64(mlp["scaler_mean"]),
        "scalerScale": b64(mlp["scaler_scale"]),
        "layers": [
            {
                "w": b64(w.reshape(-1)),
                "nIn": int(w.shape[0]),
                "nOut": int(w.shape[1]),
                "b": b64(b),
            }
            for w, b in zip(mlp["weights"], mlp["biases"])
        ],
        "nMlpOut": int(mlp["weights"][-1].shape[1]),
        "outMap": out_map,
        "vectors": vectors,
        "fields": fields,
    }
    return payload, mlp


def parity_check(bundle, mlp, n=8):
    rng = np.random.default_rng(0)
    params = list(bundle.param_names)
    spec = bundle.generator_spec["parameters"]
    x = np.array(
        [
            [
                rng.uniform(spec[p]["lower"], spec[p]["upper"])
                if p in spec
                else NOMINAL.get(p, 1.0)
                for p in params
            ]
            for _ in range(n)
        ]
    )
    std_out = np.array([_js_forward(mlp, row) for row in x])
    max_err = 0.0
    checks = (
        SCALAR_OUTPUTS
        + [c for c in bundle.output_columns if c.startswith("foods.")][:3]
    )
    for name in checks:
        if name not in bundle.models:
            continue
        p = bundle.models[name]
        js = std_out[:, p.output_index] * p.target_std + p.target_mean
        max_err = max(max_err, float(np.max(np.abs(js - predict(bundle, name, x)))))
    for fname, dec in list(bundle.field_decoders.items())[:2]:
        idx = [bundle.models[sc].output_index for sc in dec.score_columns]
        m = np.array([bundle.models[sc].target_mean for sc in dec.score_columns])
        s = np.array([bundle.models[sc].target_std for sc in dec.score_columns])
        js_field = (std_out[:, idx] * s + m) @ dec.components + dec.mean
        ref = predict_field(bundle, fname, x)
        max_err = max(max_err, float(np.max(np.abs(js_field - ref))))
    return max_err


def grid_design(bundle, n_price=24, n_yll=12):
    """Coarse slider grid (nominal elsewhere) for computing display ranges."""
    params, nominal, sliders = nominal_vector(bundle)
    rows = [nominal.copy()]

    def logspace(lo, hi, k):
        return np.exp(np.linspace(np.log(lo), np.log(hi), k))

    pj = sliders["ghg_price"]["index"]
    prices = logspace(sliders["ghg_price"]["min"], sliders["ghg_price"]["max"], n_price)
    if "value_per_yll" in sliders:
        yj = sliders["value_per_yll"]["index"]
        ylls = logspace(
            sliders["value_per_yll"]["min"], sliders["value_per_yll"]["max"], n_yll
        )
    else:
        yj, ylls = None, [None]
    rows = []
    for y in ylls:
        for p in prices:
            r = nominal.copy()
            r[pj] = p
            if yj is not None:
                r[yj] = y
            rows.append(r)
    return np.array(rows), prices, (ylls if yj is not None else None), pj, yj


def panel_ranges(bundle, food2group, grams_f, kcal_f):
    """Axis ranges for emissions/cost/diet/feed/YLL over the slider grid.

    Mirrors the JS panel math so the browser's scales stay fixed as the user
    drags. cv_ref (consumer-value deviation baseline) is taken at the lowest
    carbon price for each YLL level.
    """
    x, prices, ylls, pj, yj = grid_design(bundle)

    def col(name):
        return predict(bundle, name, x) if name in bundle.models else np.zeros(len(x))

    co2, ch4, n2o, seq = col("co2"), col("ch4"), col("n2o"), col("sequestration")
    luc = co2 - seq
    emi = np.vstack([luc, ch4, n2o, seq]) / 1000.0  # Gt

    total = col("total_cost")
    scc = col("objective_breakdown.ghg_cost")
    stab = col("objective_breakdown.production_stability") + col(
        "objective_breakdown.diet_stability"
    )
    cv = col("objective_breakdown.consumer_values")
    health = col("objective_breakdown.health_burden")
    production = total - scc - stab - cv - health
    # cv_ref at the lowest price per YLL block (grid is YLL-major, price-minor).
    npx = len(prices)
    cv_ref = np.repeat(cv.reshape(-1, npx)[:, 0], npx) if yj is not None else cv[0]
    resistance = stab + (cv - cv_ref)
    cost = np.vstack([production, resistance, scc, health, total - cv_ref])

    # Diet/feed totals (per-capita g & kcal/day; feed Mt DM).
    def group_total(prefix, factor):
        tot = np.zeros(len(x))
        for col_name in bundle.output_columns:
            if col_name.startswith(prefix + "."):
                tot = tot + np.maximum(predict(bundle, col_name, x), 0.0)
        return tot * factor

    diet_g = group_total("foods", grams_f)
    diet_kcal = group_total("foods_energy", kcal_f)
    feed = group_total("feed_categories", 1.0)
    yll = col("yll")

    return {
        "emiMin": float(emi.min()),
        "emiMax": float(emi.max()),
        "costMin": float(cost.min()),
        "costMax": float(cost.max()),
        "dietMaxG": float(diet_g.max()),
        "dietMaxKcal": float(diet_kcal.max()),
        "feedMax": float(feed.max()),
        "yllMin": float(yll.min()),
        "yllMax": float(yll.max()),
    }


def region_land_areas():
    """Per-region land area (Mha) from the widget's regions.geojson.

    Used to normalize cropland/pasture area into a 0-1 map opacity (a region
    fully under one use reads as fully saturated), matching the old dial.
    """
    import geopandas as gpd

    gdf = gpd.read_file(OUT_DIR / "regions.geojson").to_crs("+proj=cea")
    areas = gdf.geometry.area.to_numpy() / 1e10  # m^2 -> Mha
    areas[areas <= 0] = np.inf
    return dict(zip(gdf["region"], areas))


def map_max_fraction(bundle, field, region_area):
    """Max over the slider grid of per-region (area / land area) for ``field``."""
    x, *_ = grid_design(bundle)
    dec = bundle.field_decoders[field]
    fld = predict_field(bundle, field, x)  # (n_grid, n_keys)
    land = np.array([region_area.get(k, np.inf) for k in dec.keys])
    return float(np.max(fld / land))


def load_common_meta():
    pop = float(
        pd.read_csv(REPO / "processing" / "gsa" / "population.csv", comment="#")[
            "population"
        ].sum()
    )
    fg = pd.read_csv(FOOD_GROUPS_CSV, comment="#")
    food2group = dict(zip(fg["food"], fg["group"]))
    crop_groups = yaml.safe_load(DEFAULT_CONFIG.read_text())["plotting"]["crop_groups"]
    # mapGroups order MUST match CROP_GROUP_FIELDS so the JS argmax index aligns.
    field_to_group = {
        "cropland_cereals_by_region": "Cereals",
        "cropland_legumes_by_region": "Legumes",
        "cropland_roots_tubers_by_region": "Roots & tubers",
        "cropland_vegetables_by_region": "Vegetables",
        "cropland_fruits_by_region": "Fruits",
        "cropland_oilseeds_by_region": "Oilseeds",
        "cropland_sugar_crops_by_region": "Sugar crops",
        "cropland_stimulants_by_region": "Stimulants",
        "cropland_fiber_crops_by_region": "Fiber crops",
    }
    map_groups = [
        {"name": field_to_group[f], "color": crop_groups[field_to_group[f]]["color"]}
        for f in CROP_GROUP_FIELDS
    ]
    return {
        "pop": pop,
        "food2group": food2group,
        "gramsFactor": GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * pop),
        "kcalFactor": PJ_TO_KCAL / (DAYS_PER_YEAR * pop),
        "foodGroups": [
            {"key": k, "label": lbl, "color": c, "animal": a}
            for k, lbl, c, a in FOOD_GROUP_META
        ],
        "foodToGroup": food2group,
        "feedCats": [
            {"key": k, "label": lbl, "color": c} for k, lbl, c in FEED_CAT_META
        ],
        "mapGroups": map_groups,
        "cropGroupFields": CROP_GROUP_FIELDS,
        "emissionCategories": EMISSION_CATEGORIES,
        "costParts": [{"key": k, "label": lbl, "color": c} for k, lbl, c in COST_PARTS],
        "modeLabels": MODE_LABELS,
        "source": "GLADE MLP surrogate (gsa / gsa_fixed_diet)",
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    common = load_common_meta()
    grams_f, kcal_f = common["gramsFactor"], common["kcalFactor"]

    region_area = region_land_areas()
    crop_max_frac = pasture_max_frac = 0.0

    modes = {}
    for mode, path in BUNDLES.items():
        if not path.exists():
            logger.warning("mode %s: bundle missing at %s -- skipping", mode, path)
            continue
        bundle = load_bundle(path)
        payload, mlp = build_mode(bundle)
        err = parity_check(bundle, mlp)
        if err > 1e-2:
            raise SystemExit(f"parity check failed for {mode}: {err:.3e}")
        payload["ranges"] = panel_ranges(bundle, common["food2group"], grams_f, kcal_f)
        # Shared (cross-mode) map opacity normalization, so the maps don't
        # rescale when toggling fixed/flexible.
        crop_max_frac = max(
            crop_max_frac, map_max_fraction(bundle, "cropland_by_region", region_area)
        )
        pasture_max_frac = max(
            pasture_max_frac, map_max_fraction(bundle, "grazing_by_region", region_area)
        )
        modes[mode] = payload
        logger.info(
            "mode %-8s: %d params, %d mlp outputs, %d fields, sliders=%s; "
            "parity %.2e",
            mode,
            len(payload["params"]),
            payload["nMlpOut"],
            len(payload["fields"]),
            list(payload["sliders"]),
            err,
        )

    if not modes:
        raise SystemExit("no surrogate bundles found; run the cluster refit first")

    meta = {k: v for k, v in common.items() if k not in ("pop", "food2group")}
    meta["modes"] = list(modes.keys())
    meta["regionArea"] = {k: float(v) for k, v in region_area.items()}
    meta["cropMaxFrac"] = crop_max_frac
    meta["pastureMaxFrac"] = pasture_max_frac
    out = {"meta": meta, "modes": modes}
    dest = OUT_DIR / "surrogate.json"
    dest.write_text(json.dumps(out, separators=(",", ":")))
    logger.info("Wrote %s (%.0f KB)", dest, dest.stat().st_size / 1024)


if __name__ == "__main__":
    main()
