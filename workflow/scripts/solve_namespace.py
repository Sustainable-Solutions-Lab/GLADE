# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reusable helpers for constructing snakemake-shaped namespaces for solves.

Centralises the manifest-entry / namespace machinery used by
``tools/export-solve-manifest``, ``tools/cluster-solve``, and the in-process
iterative calibration drivers (e.g. ``calibrate_deviation_penalty.py``).

The functions here are pure: they translate an effective scenario config into
the inputs/params/outputs structure expected by ``run_solve`` /
``run_analysis``. They mirror the rule definitions in ``workflow/rules`` —
when those rules change, update here too.
"""

import copy
import os
from pathlib import Path
from types import SimpleNamespace

import yaml

from workflow.scripts.snakemake_utils import _recursive_update

# Config key prefixes that are safe to vary per-scenario (solve-time only).
# Any scenario override whose dotted key path does NOT start with one of these
# prefixes is considered structural and must be set at the base config level.
# Mirrored (and validated) by workflow/rules/common.smk for DAG-time errors.
SOLVE_TIME_CONFIG_PREFIXES = {
    "emissions.ghg_price",
    "emissions.ghg_pricing_enabled",
    "health.enabled",
    "health.value_per_yll",
    "validation.enforce_baseline_diet",
    "validation.animal_growth_cap",
    "deviation_penalty",
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
    "land.reforestation_cap",
    "grazing.grassland_forage_calibration.enabled",
    "exogenous_feed_calibration.enabled",
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


def validate_scenario_overrides(scenario_defs: dict) -> None:
    """Raise if any scenario in scenario_defs overrides a structural config key.

    Structural keys are anything not under SOLVE_TIME_CONFIG_PREFIXES. The
    cluster manifest exporter and the iterative calibration drivers all share
    the same built network across scenarios, so allowing structural overrides
    would solve scenarios against a mismatched topology.
    """
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


# Deviation-penalty calibration components and their config-block paths
# within the deviation_penalty dict. Cropland and grassland carry independent
# L1 costs but live under the shared ``land`` block; feed and diet are
# top-level. Single source of truth shared by the solve-time resolver
# (solve_model/production_stability), the solve-input builders
# (workflow/rules/model.smk and build_scenario_entry below), and the config
# validator (workflow/validation/calibration.py).
DEVIATION_PENALTY_COMPONENT_PATHS = {
    "cropland": ("land", "crops"),
    "grassland": ("land", "grassland"),
    "feed": ("feed",),
    "diet": ("diet",),
}

CALIBRATED_SENTINEL = "calibrated"


def deviation_penalty_component_block(dp_cfg: dict, component: str) -> dict:
    """Return the config sub-dict carrying ``component``'s l1_cost knobs."""
    block = dp_cfg
    for key in DEVIATION_PENALTY_COMPONENT_PATHS[component]:
        block = block[key]
    return block


def deviation_penalty_uses_calibrated(dp_cfg: dict) -> bool:
    """Return True if any component's l1_cost is the calibrated sentinel."""
    return any(
        deviation_penalty_component_block(dp_cfg, c)["l1_cost"] == CALIBRATED_SENTINEL
        for c in DEVIATION_PENALTY_COMPONENT_PATHS
    )


def validate_scenario_config_schemas(
    base_config: dict, scenario_defs: dict, project_root
) -> None:
    """Schema-validate the merged config of structurally distinct scenarios.

    Scenario overrides are deep-merged into the base config with no key
    checking, so an outdated or misspelled override (e.g. a pre-split
    ``deviation_penalty`` layout) merges in silently and is ignored at
    solve time. The config schema rejects unknown keys
    (``additionalProperties: false``), so validating the merged config
    catches such drift. Expanded samples from one generator template share
    an identical override key structure; only one representative per
    distinct structure is validated, keeping this cheap for GSA-sized
    scenario sets.
    """
    # Lazy import: pulls in snakemake.utils, which is unavailable in the
    # cluster shim context (tools/cluster-solve) that imports this module
    # but never calls this function.
    from workflow.validation.config_schema import validate_config_schema

    seen_structures: set[tuple] = set()
    for name, overrides in scenario_defs.items():
        structure = tuple(sorted(_leaf_keys(overrides)))
        if not structure or structure in seen_structures:
            continue
        seen_structures.add(structure)
        merged = copy.deepcopy(dict(base_config))
        _recursive_update(merged, overrides)
        try:
            validate_config_schema(merged, Path(project_root))
        except Exception as exc:
            raise ValueError(
                f"Scenario '{name}' produces an invalid merged config; check "
                "the override structure against config/default.yaml and "
                "config/schemas/config.schema.yaml.\n"
                f"{str(exc)[:1500]}"
            ) from exc


# Canonical list of per-scenario parquet outputs that analyze_model
# writes. Single source of truth shared by three consumers, all of
# which import from here:
#
#   - workflow/rules/analysis.smk derives the {name -> path-template}
#     dict for Snakemake rule outputs.
#   - workflow/scripts/analysis/analyze_model.py asserts the `results`
#     dict it writes covers exactly this set (catches local drift in
#     the producer).
#   - workflow/scripts/solve_namespace.build_run_solve_namespace_for_scenario
#     uses it to populate the cluster shim's `outputs` dict (the
#     manifest-driven cluster path that bypasses Snakemake).
#
# A new output added to analyze_model MUST be appended here; otherwise
# the cluster path raises KeyError on write and the Snakemake rule is
# missing the file from its declared outputs.
ANALYSIS_OUTPUT_NAMES = (
    "crop_production",
    "land_use",
    "animal_production",
    "food_consumption",
    "food_group_consumption",
    "net_emissions",
    "objective_breakdown",
    "ghg_attribution",
    "ghg_attribution_totals",
    "health_marginals",
    "health_totals",
    "health_attribution",
    "feed_by_category",
    "feed_by_animal",
    "feed_by_source",
    "luc_breakdown",
    "baseline_deviation",
    "food_prices",
)


def resolve_calibration_source_paths(config: dict) -> dict:
    """Substitute ``{calibration_source}`` in all string config values.

    Calibration artefact paths in config/default.yaml carry the
    ``{calibration_source}`` placeholder so that a single key,
    ``calibration.source``, selects which artefact set under
    ``data/curated/calibration/<source>/`` a config reads (and, for
    generation runs, writes). Mutates ``config`` in place and returns it.
    """
    source = config["calibration"]["source"]

    def _substitute(node):
        if isinstance(node, dict):
            return {k: _substitute(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_substitute(v) for v in node]
        if isinstance(node, str):
            return node.replace("{calibration_source}", source)
        return node

    for key in list(config.keys()):
        config[key] = _substitute(config[key])
    return config


def load_merged_config(*configfiles) -> dict:
    """Load and merge YAML config files (later files override earlier ones)."""
    merged: dict = {}
    for path in configfiles:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        _recursive_update(merged, data)
    return resolve_calibration_source_paths(merged)


def get_effective_config(
    base_config: dict, scenario_name: str, scenario_defs: dict
) -> dict:
    """Return the configuration with scenario overrides applied."""
    eff = copy.deepcopy(base_config)
    if not scenario_name:
        return eff
    if scenario_name in scenario_defs:
        _recursive_update(eff, scenario_defs[scenario_name])
    return eff


def resolve_path_root(raw_path: str) -> str:
    """Resolve environment variables and user-home markers in a path root."""
    resolved = os.path.expanduser(os.path.expandvars(raw_path))
    if resolved != "/":
        resolved = resolved.rstrip("/")
    return resolved


def resolve_pathvars(path: str, path_roots: dict[str, str]) -> str:
    """Replace <results>, <processing>, etc. with the configured paths."""
    for key, root in path_roots.items():
        path = path.replace(f"<{key}>", root)
    return path


def default_path_roots(config: dict) -> dict[str, str]:
    """Resolve the standard four path roots from a config dict."""
    paths_cfg = config.get("paths", {}) or {}
    return {
        "results": resolve_path_root(paths_cfg.get("results_root", "results")),
        "processing": resolve_path_root(paths_cfg.get("processing_root", "processing")),
        "logs": resolve_path_root(paths_cfg.get("logs_root", "logs")),
        "benchmarks": resolve_path_root(paths_cfg.get("benchmarks_root", "benchmarks")),
    }


def solver_options_with_overrides(cfg: dict) -> dict:
    """Return solver options with threads and time-limit overrides applied."""
    solver_name = cfg["solving"]["solver"]
    options = cfg["solving"].get(f"options_{solver_name}", {}) or {}
    threads = int(cfg["solving"]["threads"])
    time_limit = cfg["solving"]["time_limit"]

    options = dict(options)
    solver_key = solver_name.lower()
    if solver_key == "gurobi":
        options["Threads"] = threads
        if time_limit is not None:
            options["TimeLimit"] = time_limit * 60
    elif solver_key == "highs":
        options["threads"] = threads
        if time_limit is not None:
            options["time_limit"] = time_limit * 60
    return options


def build_scenario_entry(
    base_config: dict,
    scenario: str,
    name: str,
    path_roots: dict[str, str],
    inline_analysis: bool,
    scenario_defs: dict,
) -> dict:
    """Build a manifest-style entry for one scenario.

    Mirrors the inputs/params lists in workflow/rules/model.smk (solve_model)
    and workflow/rules/analysis.smk (solve_and_analyze_model) — keep in sync.
    """
    eff = get_effective_config(base_config, scenario, scenario_defs)

    def rp(path: str) -> str:
        return resolve_pathvars(path.format(name=name, scenario=scenario), path_roots)

    inputs: dict = {
        "network": rp("<results>/{name}/build/model.nc"),
        "m49": "data/curated/M49-codes.csv",
        "food_groups": "data/curated/food_groups.csv",
        "baseline_diet": rp("<processing>/{name}/baseline_diet.csv"),
    }

    # Health processing inputs only when this scenario enables health (mirrors
    # solve_model_inputs in workflow/rules/model.smk).
    if eff["health"]["enabled"]:
        inputs.update(
            {
                "health_risk_breakpoints": rp(
                    "<processing>/{name}/health/risk_breakpoints.csv"
                ),
                "health_cluster_cause": rp(
                    "<processing>/{name}/health/cluster_cause_baseline.csv"
                ),
                "health_cause_log": rp(
                    "<processing>/{name}/health/cause_log_breakpoints.csv"
                ),
                "health_cluster_summary": rp(
                    "<processing>/{name}/health/cluster_summary.csv"
                ),
                "health_clusters": rp(
                    "<processing>/{name}/health/country_clusters.csv"
                ),
                "health_tmrel": rp("<processing>/{name}/health/tmrel.csv"),
                "health_cluster_risk_baseline": rp(
                    "<processing>/{name}/health/cluster_risk_baseline.csv"
                ),
            }
        )

    if eff["food_incentives"]["enabled"]:
        sources = eff["food_incentives"]["sources"]
        if not sources:
            raise ValueError(
                f"Scenario {scenario}: food_incentives enabled but sources is empty"
            )
        inputs["food_incentives"] = [
            source.format(name=name, scenario=scenario) for source in sources
        ]

    utility_cfg = eff["food_utility_piecewise"]
    if utility_cfg["enabled"]:
        baseline_name = eff["consumer_values"]["baseline_scenario"]
        inputs["food_utility_piecewise"] = rp(
            f"<results>/{{name}}/consumer_values/{baseline_name}/utility_blocks.csv"
        )

    equal_source = eff["food_groups"]["equal_by_country_source"]
    if equal_source:
        inputs["food_group_equal"] = equal_source.format(name=name, scenario=scenario)

    macronutrient_cfg = eff["macronutrients"]
    if any(
        isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        for bounds in macronutrient_cfg.values()
    ):
        inputs["nutrition"] = "data/curated/nutrition.csv"

    cal_cfg = eff["grazing"]["grassland_forage_calibration"]
    if cal_cfg["enabled"]:
        inputs["grassland_yield_correction"] = cal_cfg["grassland_yield_correction"]
        inputs["fodder_conversion_correction"] = cal_cfg["fodder_conversion_correction"]
        inputs["exogenous_forage"] = cal_cfg["exogenous_forage"]

    exo_feed_cal_cfg = eff["exogenous_feed_calibration"]
    if exo_feed_cal_cfg["enabled"]:
        inputs["exogenous_feed"] = exo_feed_cal_cfg["exogenous_feed"]

    fd_cal_cfg = eff["food_demand_calibration"]
    if fd_cal_cfg["enabled"]:
        inputs["food_demand_calibration"] = fd_cal_cfg["calibration_file"]

    dp_cfg = eff["deviation_penalty"]
    dp_cal_cfg = dp_cfg["calibration"]
    if dp_cal_cfg["enabled"] and deviation_penalty_uses_calibrated(dp_cfg):
        inputs["deviation_penalty_calibration"] = dp_cal_cfg["calibrated_yaml"]

    if inline_analysis:
        inputs["population"] = rp("<processing>/{name}/population.csv")

    params: dict = {
        "health_enabled": eff["health"]["enabled"],
        "health_risk_factors": eff["health"]["risk_factors"],
        "health_risk_cause_map": eff["health"]["risk_cause_map"],
        "health_value_per_yll": eff["health"]["value_per_yll"],
        "ghg_price": eff["emissions"]["ghg_price"],
        "solver": eff["solving"]["solver"],
        "solver_options": solver_options_with_overrides(eff),
        "io_api": eff["solving"]["io_api"],
        "calculate_fixed_duals": eff["solving"]["calculate_fixed_duals"],
        "netcdf": eff["netcdf"],
        "macronutrients": macronutrient_cfg,
        "food_group_constraints": eff["food_groups"]["constraints"],
        "enforce_baseline": eff["validation"]["enforce_baseline_diet"],
        "deviation_penalty": eff["deviation_penalty"],
        "animal_growth_cap": eff["validation"]["animal_growth_cap"],
        "crop_growth_cap": eff["validation"]["crop_growth_cap"],
        "food_utility_piecewise": utility_cfg,
        "fix_within_group_ratios": eff["food_groups"]["fix_within_group_ratios"],
        "sensitivity": eff["sensitivity"],
        "reforestation_cap": eff["land"]["reforestation_cap"],
        "forage_calibration_enabled": cal_cfg["enabled"],
        "forage_overlap_crops": eff["grazing"]["forage_overlap_crops"],
        "exogenous_feed_calibration_enabled": exo_feed_cal_cfg["enabled"],
        "enforce_baseline_feed": eff["validation"]["enforce_baseline_feed"],
        "regional_limit": eff["land"]["regional_limit"],
        "biofuel_demand_scale": eff["biomass"]["biofuel_demand_scale"],
        "ghg_pricing_enabled": eff["emissions"]["ghg_pricing_enabled"],
        "food_incentives_enabled": eff["food_incentives"]["enabled"],
        "equal_by_country_source": equal_source,
        "slack_marginal_cost": eff["validation"]["slack_marginal_cost"],
        "residue_max_feed_fraction": eff["residues"]["max_feed_fraction"],
        "residue_max_feed_fraction_by_region": eff["residues"][
            "max_feed_fraction_by_region"
        ],
        "countries": eff["countries"],
        "export_for_tuning": eff["solving"].get("export_for_tuning", False),
    }

    if inline_analysis:
        params["ch4_gwp"] = eff["emissions"]["ch4_to_co2_factor"]
        params["n2o_gwp"] = eff["emissions"]["n2o_to_co2_factor"]

    if inline_analysis:
        outputs = {
            out_name: rp(
                f"<results>/{{name}}/analysis/scen-{{scenario}}/{out_name}.parquet"
            )
            for out_name in ANALYSIS_OUTPUT_NAMES
        }
    else:
        outputs = {
            "network": rp("<results>/{name}/solved/model_scen-{scenario}.nc"),
        }

    if inline_analysis:
        log = rp("<logs>/{name}/solve_and_analyze_model_scen-{scenario}.log")
    else:
        log = rp("<logs>/{name}/solve_model_scen-{scenario}.log")

    return {
        "scenario": scenario,
        "inputs": inputs,
        "params": params,
        "outputs": outputs,
        "log": log,
    }


def build_namespace(entry: dict, shared_params: dict | None = None) -> SimpleNamespace:
    """Build a snakemake-shaped namespace from a manifest entry."""
    inputs = entry["inputs"]
    input_ns = SimpleNamespace(**inputs)

    all_params = dict(shared_params or {})
    all_params.update(entry["params"])
    params_ns = SimpleNamespace(**all_params)

    output_ns = SimpleNamespace(**entry["outputs"])
    wildcards_ns = SimpleNamespace(scenario=entry["scenario"])

    return SimpleNamespace(
        input=input_ns,
        params=params_ns,
        output=output_ns,
        wildcards=wildcards_ns,
        log=[entry["log"]],
    )
