# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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

from scenario_generators import expand_scenario_defs

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


# Config key prefixes that are safe to vary per-scenario (solve-time only).
# Any scenario override whose dotted key path does NOT start with one of these
# prefixes is considered structural and will be rejected at DAG construction
# time. This allowlist approach ensures new config keys are structural by
# default until explicitly verified as solve-time safe.
SOLVE_TIME_CONFIG_PREFIXES = {
    "emissions.ghg_price",
    "emissions.ghg_pricing_enabled",
    "health.enabled",
    "health.value_per_yll",
    "validation.enforce_baseline_diet",
    "validation.production_stability",
    "validation.animal_growth_cap",
    "macronutrients",
    "food_utility_piecewise",
    "food_incentives",
    "food_groups.constraints",
    "food_groups.fix_within_group_ratios",
    "food_groups.equal_by_country_source",
    "food_groups.max_per_capita",
    "biomass.marginal_values_usd_per_tonne",
    "biomass.biofuel_demand_scale",
    "land.regional_limit",
    "grazing.grassland_forage_calibration.enabled",
    "consumer_values",
    "sensitivity",
    "solving",
    "plotting",
    "remote_solve",
    "netcdf",
}


def _leaf_keys(d, prefix=""):
    """Yield dotted key paths for leaf (non-dict) values in a nested dict."""
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            yield from _leaf_keys(v, full)
        else:
            yield full


def _is_solve_time_key(key):
    """Return True if the key matches any allowed solve-time prefix."""
    return any(key == p or key.startswith(p + ".") for p in SOLVE_TIME_CONFIG_PREFIXES)


def _validate_scenario_overrides():
    """Raise an error if any scenario overrides a structural config key."""
    scenario_defs = load_scenario_defs()
    errors = []
    for scenario_name, overrides in scenario_defs.items():
        for key in _leaf_keys(overrides):
            if not _is_solve_time_key(key):
                errors.append(
                    f"  scenario '{scenario_name}' overrides structural key '{key}'"
                )
    if errors:
        raise ValueError(
            "Scenario overrides must not change model topology.\n"
            "The following overrides affect build-time structure and must be\n"
            "set at the base config level instead:\n" + "\n".join(errors)
        )


_validate_scenario_overrides()


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


def gaez_path(kind: str, water_supply: str, crop: str) -> str:
    """Return GAEZ v5 raster path for a given kind and water supply.

    kind: one of {"yield", "suitability", "water_requirement", "growing_season_start", "growing_season_length", "actual_yield", "harvested_area"}
    water_supply: "i" (irrigated) or "r" (rainfed)
    crop: crop name (e.g., "wheat")
    """
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
