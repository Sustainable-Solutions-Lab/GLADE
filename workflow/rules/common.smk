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
    health_input_paths,
    resolve_gbd_anchoring,
    resolve_pathvars,
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


def health_required():
    """True if the health module is enabled in the base config or any scenario.

    The build step is scenario-independent, so health data-prep rules, health
    stores, and downstream health analysis/plots are needed whenever *any*
    configured scenario (or the base config) enables health. health.enabled is
    a solve-time-overridable key.
    """
    if config["health"]["enabled"]:
        return True
    return any(get_effective_config(s)["health"]["enabled"] for s in list_scenarios())


def gbd_anchoring_enabled():
    """Resolve diet.anchor_groups_to_gbd for this run (see resolve_gbd_anchoring)."""
    return resolve_gbd_anchoring(config)


def gbd_data_required():
    """True if the manually-downloaded IHME GBD data is needed by this run.

    The GBD intake/mortality prep (and thus the IHME source files) is required
    whenever the baseline diet is anchored to GBD or the health module is
    enabled in any scenario.
    """
    return gbd_anchoring_enabled() or health_required()


def assert_gbd_data_available():
    """Fail early with actionable guidance if GBD data is needed but absent.

    Snakemake would otherwise report a terse "missing input" deep in the DAG.
    Only enforced when gbd_data_required(); a health-off, anchoring-off run
    needs none of these files.
    """
    if not gbd_data_required():
        return
    year = config["baseline_year"]
    required = {
        f"data/manually_downloaded/IHME-GBD_2023-death-rates-{year}.csv": (
            "IHME GBD mortality / national-location list"
        ),
        "data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_1": (
            "IHME GBD dietary risk-exposure archive (part 1)"
        ),
        "data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_2": (
            "IHME GBD dietary risk-exposure archive (part 2)"
        ),
    }
    missing = [(p, desc) for p, desc in required.items() if not Path(p).exists()]
    if not missing:
        return
    reasons = []
    if health_required():
        reasons.append("health.enabled is true (base config or a scenario)")
    if gbd_anchoring_enabled():
        reasons.append("diet.anchor_groups_to_gbd resolves to true")
    listing = "\n".join(f"  - {p}  ({desc})" for p, desc in missing)
    raise FileNotFoundError(
        "This run needs the manually-downloaded IHME GBD data because "
        + " and ".join(reasons)
        + ", but the following are missing:\n"
        + listing
        + "\n\nEither place the files (see data/manually_downloaded/README.md "
        "for the GBD Results Tool queries), or run without GBD data by setting "
        "health.enabled: false and diet.anchor_groups_to_gbd: false in your "
        "config. Note that disabling anchoring changes the baseline diet "
        "(see docs/current_diets.rst)."
    )


# SOLVE_TIME_CONFIG_PREFIXES, _leaf_keys, _is_solve_time_key, and
# validate_scenario_overrides are imported from workflow.scripts.solve_namespace
# above so cluster manifest export, in-process calibration drivers, and this
# Snakemake-time guard share a single source of truth.


def assert_gdd_ia_data_available():
    """Fail early with actionable guidance if GDD-IA data is needed but absent.

    Only enforced when diet.source is "gdd_ia"; the default FBS-derived
    baseline diet needs no manually-downloaded dietary data.
    """
    if config["diet"]["source"] != "gdd_ia":
        return
    year = config["baseline_year"]
    missing = [
        p
        for p in (
            f"data/manually_downloaded/GDD-IA-intake_grams_{year}.csv",
            f"data/manually_downloaded/GDD-IA-intake_kcals_{year}.csv",
        )
        if not Path(p).exists()
    ]
    if not missing:
        return
    listing = "\n".join(f"  - {p}" for p in missing)
    raise FileNotFoundError(
        "This run needs the manually-downloaded GDD-IA dietary intake data "
        "because diet.source is 'gdd_ia', but the following are missing:\n"
        + listing
        + "\n\nEither place the files (see data/manually_downloaded/README.md)"
        " or set diet.source: fbs to use the FAOSTAT-FBS-derived baseline "
        "diet instead. Note that changing the diet source changes the "
        "baseline diet, which calibration artefact sets are fit against "
        "(see docs/calibration.rst)."
    )


validate_scenario_overrides(load_scenario_defs())
validate_scenario_config_schemas(config, load_scenario_defs(), Path.cwd())
assert_gbd_data_available()
assert_gdd_ia_data_available()


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
