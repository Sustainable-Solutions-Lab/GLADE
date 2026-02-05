# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


rule prepare_faostat_emissions:
    input:
        gt_csv="data/downloads/faostat/GT.csv",
    output:
        "processing/{name}/faostat_emissions.csv",
    params:
        year=config["validation"]["production_year"],
    log:
        "logs/{name}/prepare_faostat_emissions.log",
    script:
        "../scripts/prepare_faostat_emissions.py"


rule extract_ghg_intensity:
    """Extract GHG intensity and totals by food and country."""
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        food_groups="data/curated/food_groups.csv",
        food_consumption="results/{name}/analysis/scen-{scenario}/food_consumption.csv",
    params:
        ghg_price=lambda w: get_effective_config(w.scenario)["emissions"]["ghg_price"],
        ch4_gwp=config["emissions"]["ch4_to_co2_factor"],
        n2o_gwp=config["emissions"]["n2o_to_co2_factor"],
    output:
        csv="results/{name}/analysis/scen-{scenario}/ghg_intensity.csv",
        totals="results/{name}/analysis/scen-{scenario}/ghg_totals.csv",
    log:
        "logs/{name}/extract_ghg_intensity_scen-{scenario}.log",
    script:
        "../scripts/analysis/extract_ghg_intensity.py"


rule extract_health_impacts:
    """Extract marginal health impacts and totals by food group and country."""
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        food_group_consumption="results/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
        risk_breakpoints="processing/{name}/health/scen-{scenario}/risk_breakpoints.csv",
        health_cluster_cause="processing/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        health_cause_log="processing/{name}/health/scen-{scenario}/cause_log_breakpoints.csv",
        health_clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
        population="processing/{name}/population.csv",
    params:
        value_per_yll=lambda w: get_effective_config(w.scenario)["health"][
            "value_per_yll"
        ],
        health_risk_factors=config["health"]["risk_factors"],
    output:
        marginals="results/{name}/analysis/scen-{scenario}/health_marginals.csv",
        totals="results/{name}/analysis/scen-{scenario}/health_totals.csv",
    log:
        "logs/{name}/extract_health_impacts_scen-{scenario}.log",
    script:
        "../scripts/analysis/extract_health_impacts.py"


rule extract_statistics:
    """Extract production and consumption statistics."""
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        crop_production="results/{name}/analysis/scen-{scenario}/crop_production.csv",
        land_use="results/{name}/analysis/scen-{scenario}/land_use.csv",
        animal_production="results/{name}/analysis/scen-{scenario}/animal_production.csv",
        food_consumption="results/{name}/analysis/scen-{scenario}/food_consumption.csv",
        food_group_consumption="results/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
    log:
        "logs/{name}/extract_statistics_scen-{scenario}.log",
    script:
        "../scripts/analysis/extract_statistics.py"


rule extract_objective_breakdown:
    """Extract objective function breakdown by cost category."""
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        objective_breakdown="results/{name}/analysis/scen-{scenario}/objective_breakdown.csv",
    log:
        "logs/{name}/extract_objective_breakdown_scen-{scenario}.log",
    script:
        "../scripts/analysis/extract_objective_breakdown.py"


def _sobol_scenario_inputs(wildcards):
    """Generate input files for all Sobol-sampled scenarios.

    Looks for scenarios matching the prefix pattern in scenario_defs and
    returns the analysis CSVs needed for sensitivity index computation.
    """
    prefix = wildcards.prefix
    all_scenarios = list_scenarios()

    # Preserve scenario_defs expansion order; Sobol analysis needs this exact ordering.
    matching = [s for s in all_scenarios if s.startswith(prefix)]

    if not matching:
        raise ValueError(
            f"No scenarios found with prefix '{prefix}'. "
            f"Available scenarios: {all_scenarios}"
        )

    inputs = []
    for scenario in matching:
        inputs.extend(
            [
                f"results/{wildcards.name}/analysis/scen-{scenario}/objective_breakdown.csv",
                f"results/{wildcards.name}/analysis/scen-{scenario}/ghg_totals.csv",
                f"results/{wildcards.name}/analysis/scen-{scenario}/land_use.csv",
                f"results/{wildcards.name}/analysis/scen-{scenario}/health_totals.csv",
            ]
        )
    return inputs


def _sobol_scenario_names(wildcards):
    """Return Sobol scenario names matching the requested prefix in expansion order."""
    prefix = wildcards.prefix
    all_scenarios = list_scenarios()
    matching = [s for s in all_scenarios if s.startswith(prefix)]
    if not matching:
        raise ValueError(
            f"No scenarios found with prefix '{prefix}'. "
            f"Available scenarios: {all_scenarios}"
        )
    return matching


def _sobol_generator(wildcards):
    """Extract the single Sobol generator from scenario_defs."""
    import yaml

    scenario_defs_path = config.get("scenario_defs")
    if not scenario_defs_path:
        raise ValueError(
            "scenario_defs not configured; cannot compute sensitivity indices"
        )

    with open(scenario_defs_path, "r", encoding="utf-8") as f:
        raw_defs = yaml.safe_load(f) or {}

    sobol_generators = [
        gen for gen in raw_defs.get("_generators", []) if gen.get("mode") == "sobol"
    ]
    if not sobol_generators:
        raise ValueError("No Sobol generator found in scenario_defs")
    if len(sobol_generators) > 1:
        raise ValueError(
            "Multiple Sobol generators found in scenario_defs. "
            "Only one Sobol generator per config is currently supported."
        )
    return sobol_generators[0]


def _sobol_parameter_bounds(wildcards):
    """Extract parameter bounds from the Sobol generator in scenario_defs."""
    generator = _sobol_generator(wildcards)
    return {
        name: {"min": spec["min"], "max": spec["max"]}
        for name, spec in generator["parameters"].items()
    }


def _sobol_base_samples(wildcards):
    """Extract base sample count from the Sobol generator."""
    return _sobol_generator(wildcards)["samples"]


rule compute_sensitivity_indices:
    """Compute global Sobol sensitivity indices from ensemble scenario runs.

    This rule aggregates outputs from Sobol-sampled scenarios and computes
    first-order (S1) and total-order (ST) sensitivity indices.

    To use, ensure your scenarios file has a generator with mode: sobol,
    then run:
        tools/smk --configfile config/global_sensitivity.yaml -- results/{name}/analysis/sobol_indices_sa_.csv

    The {prefix} wildcard matches the scenario name prefix (e.g., "sa_" for scenarios sa_0, sa_1, ...).
    """
    input:
        _sobol_scenario_inputs,
    params:
        analysis_dir=lambda w: f"results/{w.name}/analysis",
        scenario_prefix=lambda w: w.prefix,
        scenario_names=_sobol_scenario_names,
        base_samples=_sobol_base_samples,
        parameter_bounds=_sobol_parameter_bounds,
    output:
        indices="results/{name}/analysis/sobol_indices_{prefix}.csv",
    log:
        "logs/{name}/compute_sensitivity_indices_{prefix}.log",
    script:
        "../scripts/analysis/compute_sensitivity_indices.py"
