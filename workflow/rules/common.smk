# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Common configuration variables and helper functions shared across Snakemake rules.

This file should be included first in the main Snakefile, before any other rule files.
"""
import copy
import csv
import hashlib
import json
from pathlib import Path

from scenario_generators import expand_scenario_defs

from workflow.scripts.solve_namespace import (
    SOLVE_TIME_CONFIG_PREFIXES,
    _is_solve_time_key,
    _leaf_keys,
    deviation_penalty_uses_calibrated,
    validate_scenario_config_schemas,
    validate_scenario_overrides,
)

_SCENARIO_CACHE = None


def _recursive_update(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            _recursive_update(target[key], value)
        else:
            target[key] = value
    return target


def load_scenario_defs():
    """Load scenario definitions from the config's `scenarios` key."""
    global _SCENARIO_CACHE
    if _SCENARIO_CACHE is None:
        raw_defs = config.get("scenarios") or {}
        _SCENARIO_CACHE = expand_scenario_defs(raw_defs)
    return _SCENARIO_CACHE


def list_scenarios():
    """Return the scenario names from the `scenarios` config key."""
    return list(load_scenario_defs().keys())


def get_effective_config(scenario_name):
    """Return the configuration with scenario overrides applied."""
    scenario_defs = load_scenario_defs()

    # Start with a deep copy of the global config to avoid mutating it
    # We convert config to dict because it might be a Config object
    eff_config = copy.deepcopy(dict(config))

    if scenario_name:
        if scenario_name not in scenario_defs:
            # If scenario is not found, maybe raise warning or error?
            # For now, we assume if it's not in cache, no overrides (or invalid scenario handled elsewhere)
            pass
        else:
            overrides = scenario_defs[scenario_name]
            _recursive_update(eff_config, overrides)

    return eff_config


# SOLVE_TIME_CONFIG_PREFIXES, _leaf_keys, _is_solve_time_key, and
# validate_scenario_overrides are imported from workflow.scripts.solve_namespace
# above so cluster manifest export, in-process calibration drivers, and this
# Snakemake-time guard share a single source of truth.


validate_scenario_overrides(load_scenario_defs())
validate_scenario_config_schemas(config, load_scenario_defs(), Path.cwd())


def scenario_override_hash(scenario_name):
    """Return a stable hash of scenario overrides."""
    # Snakemake does not track scenario changes; this hash exists only to force
    # correct reruns when scenario definitions are edited.
    if not scenario_name:
        overrides = {}
    else:
        scenario_defs = load_scenario_defs()
        overrides = scenario_defs.get(scenario_name, {})

    payload = json.dumps(
        overrides,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Extract configuration name and relevant config sections
name = config["name"]
gaez_cfg = config["data"]["gaez"]
grazing_cfg = config.get("grazing", {})

# Load GAEZ crop code mapping from CSV
with open("data/curated/gaez_crop_code_mapping.csv", newline="") as _gaez_mapping_file:
    _GAEZ_CODE_MAPPING = {
        row["crop_name"]: {
            "res02": row["res02_code"],
            "res05": row["res05_code"],
            "res06": row["res06_code"],
            "res02_fallback_crop": row["res02_fallback_crop"],
        }
        for row in csv.DictReader(_gaez_mapping_file)
    }


def get_gaez_code(crop_name: str, module: str) -> str:
    """Look up the GAEZ RES code for a given crop and module."""

    module_key = module.lower()
    if module_key not in {"res02", "res05", "res06"}:
        raise ValueError(f"Unknown GAEZ module '{module}'")

    try:
        code = _GAEZ_CODE_MAPPING[crop_name][module_key]
    except KeyError as exc:
        raise ValueError(f"Crop '{crop_name}' not found in mapping") from exc

    if not code:
        raise ValueError(f"Crop '{crop_name}' has no {module_key} code")

    return code.strip().upper()


def get_gaez_res02_source_crop(crop_name: str) -> str:
    """Return the crop whose RES02 calendar rasters should be used."""

    try:
        fallback_crop = _GAEZ_CODE_MAPPING[crop_name]["res02_fallback_crop"]
    except KeyError as exc:
        raise ValueError(f"Crop '{crop_name}' not found in mapping") from exc

    if fallback_crop:
        return fallback_crop.strip()

    return crop_name


def get_gaez_res02_code(crop_name: str) -> str:
    """Look up the RES02 code, following an explicit calendar fallback if set."""

    return get_gaez_code(get_gaez_res02_source_crop(crop_name), "res02")


def gaez_crops(crops=None):
    """Return crops sourced from GAEZ (config["crops"] minus cropgrids_crops).

    Used by rules that build per-crop GAEZ raster inputs. CROPGRIDS-backed
    crops bypass GAEZ entirely and must not appear in those input lists.
    """
    base = list(crops) if crops is not None else list(config["crops"])
    cropgrids_set = set(config.get("cropgrids_crops") or [])
    return [c for c in base if c not in cropgrids_set]


def irrigated_crops():
    """Return the list of crops with irrigated production.

    Resolves ``config["irrigation"]["irrigated_crops"]`` ("all" → every model
    crop) and strips out ``cropgrids_crops`` (rainfed-only by construction,
    enforced by validate_cropgrids_crops).
    """
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        base = list(config["crops"])
    else:
        base = list(irr_cfg)
    cropgrids_set = set(config.get("cropgrids_crops") or [])
    return [c for c in base if c not in cropgrids_set]


def gaez_path(kind: str, water_supply: str, crop: str) -> str:
    """Return GAEZ v5 raster path for a given kind and water supply.

    kind: one of {"yield", "suitability", "water_requirement", "growing_season_start", "growing_season_length", "actual_yield", "harvested_area"}
    water_supply: "i" (irrigated) or "r" (rainfed)
    crop: crop name (e.g., "wheat")
    """
    cropgrids_set = set(config.get("cropgrids_crops") or [])
    if crop in cropgrids_set:
        raise ValueError(
            f"gaez_path() called for CROPGRIDS-backed crop '{crop}'; this "
            "crop has no GAEZ raster. Use gaez_crops() to filter the crop "
            "list before iterating."
        )
    ws = water_supply.lower()
    if ws not in {"i", "r"}:
        raise ValueError(f"Unsupported water supply '{water_supply}'")

    if kind == "actual_yield":
        return f"data/downloads/gaez_actual_yield_{ws}_{crop}.tif"
    if kind == "harvested_area":
        return f"data/downloads/gaez_harvested_area_{ws}_{crop}.tif"

    climate = gaez_cfg["climate_model"]
    period = gaez_cfg["period"]
    climate_scenario = gaez_cfg["climate_scenario"]
    input_level = gaez_cfg["input_level"]

    prefix_by_kind = {
        "yield": "data/downloads/gaez_yield",
        "water_requirement": "data/downloads/gaez_water",
        "suitability": "data/downloads/gaez_suitability",
        "multiple_cropping_zone": "data/downloads/gaez_multiple_cropping",
        "growing_season_start": "data/downloads/gaez_growing_season_start",
        "growing_season_length": "data/downloads/gaez_growing_season_length",
    }

    try:
        prefix = prefix_by_kind[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown kind for gaez_path: {kind}") from exc

    if kind == "multiple_cropping_zone":
        return f"{prefix}_{climate}_{period}_{climate_scenario}_{input_level}_{ws}.tif"

    return (
        f"{prefix}_{climate}_{period}_{climate_scenario}_{input_level}_{ws}_{crop}.tif"
    )
