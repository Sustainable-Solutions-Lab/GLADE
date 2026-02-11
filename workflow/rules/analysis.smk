# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


rule prepare_faostat_emissions:
    input:
        gt_csv="data/downloads/faostat/GT.csv",
    output:
        "<processing>/{name}/faostat_emissions.csv",
    params:
        year=config["validation"]["production_year"],
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1400,
    log:
        "<logs>/{name}/prepare_faostat_emissions.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_emissions.tsv"
    script:
        "../scripts/prepare_faostat_emissions.py"


rule extract_ghg_intensity:
    """Extract GHG intensity and totals by food and country."""
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        food_groups="data/curated/food_groups.csv",
        food_consumption="<results>/{name}/analysis/scen-{scenario}/food_consumption.csv",
    params:
        ghg_price=lambda w: get_effective_config(w.scenario)["emissions"]["ghg_price"],
        ch4_gwp=config["emissions"]["ch4_to_co2_factor"],
        n2o_gwp=config["emissions"]["n2o_to_co2_factor"],
    output:
        csv="<results>/{name}/analysis/scen-{scenario}/ghg_intensity.csv",
        totals="<results>/{name}/analysis/scen-{scenario}/ghg_totals.csv",
    group:
        "model_core"
    resources:
        runtime="1m",
        mem_mb=950,
    log:
        "<logs>/{name}/extract_ghg_intensity_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/extract_ghg_intensity_scen-{scenario}.tsv"
    script:
        "../scripts/analysis/extract_ghg_intensity.py"


rule extract_health_impacts:
    """Extract marginal health impacts and totals by food group and country."""
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
        risk_breakpoints="<processing>/{name}/health/scen-{scenario}/risk_breakpoints.csv",
        health_cluster_cause="<processing>/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        health_cause_log="<processing>/{name}/health/scen-{scenario}/cause_log_breakpoints.csv",
        health_clusters="<processing>/{name}/health/scen-{scenario}/country_clusters.csv",
        population="<processing>/{name}/population.csv",
    params:
        value_per_yll=lambda w: get_effective_config(w.scenario)["health"][
            "value_per_yll"
        ],
        health_risk_factors=config["health"]["risk_factors"],
    output:
        marginals="<results>/{name}/analysis/scen-{scenario}/health_marginals.csv",
        totals="<results>/{name}/analysis/scen-{scenario}/health_totals.csv",
    group:
        "model_core"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/extract_health_impacts_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/extract_health_impacts_scen-{scenario}.tsv"
    script:
        "../scripts/analysis/extract_health_impacts.py"


rule extract_statistics:
    """Extract production and consumption statistics."""
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        crop_production="<results>/{name}/analysis/scen-{scenario}/crop_production.csv",
        land_use="<results>/{name}/analysis/scen-{scenario}/land_use.csv",
        animal_production="<results>/{name}/analysis/scen-{scenario}/animal_production.csv",
        food_consumption="<results>/{name}/analysis/scen-{scenario}/food_consumption.csv",
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
    group:
        "model_core"
    resources:
        runtime="1m",
        mem_mb=950,
    log:
        "<logs>/{name}/extract_statistics_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/extract_statistics_scen-{scenario}.tsv"
    script:
        "../scripts/analysis/extract_statistics.py"


rule extract_objective_breakdown:
    """Extract objective function breakdown by cost category."""
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        objective_breakdown="<results>/{name}/analysis/scen-{scenario}/objective_breakdown.csv",
    group:
        "model_core"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/extract_objective_breakdown_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/extract_objective_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/analysis/extract_objective_breakdown.py"


def _sensitivity_generator(wildcards):
    """Extract the single sensitivity generator from scenario_defs."""
    import yaml

    scenario_defs_path = config.get("scenario_defs")
    if not scenario_defs_path:
        raise ValueError(
            "scenario_defs not configured; cannot compute sensitivity indices"
        )

    with open(scenario_defs_path, "r", encoding="utf-8") as f:
        raw_defs = yaml.safe_load(f) or {}

    generators = [
        gen
        for gen in raw_defs.get("_generators", [])
        if gen.get("mode") == "sensitivity"
    ]
    if not generators:
        raise ValueError("No sensitivity generator found in scenario_defs")
    if len(generators) > 1:
        raise ValueError(
            "Multiple sensitivity generators found in scenario_defs. "
            "Only one sensitivity generator per config is currently supported."
        )
    return generators[0]


def _sensitivity_scenario_names(wildcards):
    """Return sensitivity scenario names matching the prefix in expansion order."""
    prefix = wildcards.prefix
    all_scenarios = list_scenarios()
    matching = [s for s in all_scenarios if s.startswith(prefix)]
    if not matching:
        raise ValueError(
            f"No scenarios found with prefix '{prefix}'. "
            f"Available scenarios: {all_scenarios}"
        )
    return matching


def _sensitivity_scenario_inputs(wildcards):
    """Generate input files for all sensitivity-sampled scenarios."""
    matching = _sensitivity_scenario_names(wildcards)
    inputs = []
    for scenario in matching:
        inputs.extend(
            [
                f"<results>/{wildcards.name}/analysis/scen-{scenario}/objective_breakdown.csv",
                f"<results>/{wildcards.name}/analysis/scen-{scenario}/ghg_totals.csv",
                f"<results>/{wildcards.name}/analysis/scen-{scenario}/land_use.csv",
                f"<results>/{wildcards.name}/analysis/scen-{scenario}/health_totals.csv",
            ]
        )
    return inputs


def _sensitivity_slice_grid(wildcards):
    """Build a conditioning grid for slice parameters.

    Returns a dict mapping each slice parameter name to a list of 25
    linearly-spaced values between its min and max.
    """
    import numpy as _np

    from scenario_generators import build_chaospy_distribution

    generator = _sensitivity_generator(wildcards)
    slice_params = generator.get("slice_parameters", [])
    grid = {}
    for sp in slice_params:
        spec = generator["parameters"][sp]
        dist = build_chaospy_distribution(spec)
        lo, hi = float(dist.lower[0]), float(dist.upper[0])
        grid[sp] = [float(v) for v in _np.linspace(lo, hi, 25)]
    return grid


rule compute_pce_sensitivity:
    """Compute PCE-based global sensitivity indices from ensemble scenario runs.

    Fits Polynomial Chaos Expansions to model outputs and computes Sobol
    indices analytically. Supports conditional analysis on slice parameters.

    To use, ensure your scenarios file has a generator with mode: sensitivity,
    then run:
        tools/smk --configfile config/pce_sensitivity.yaml -- <results>/{name}/analysis/pce_global_indices_{prefix}.csv

    The {prefix} wildcard matches the scenario name prefix (e.g., "pce_" for
    scenarios pce_0, pce_1, ...).
    """
    input:
        _sensitivity_scenario_inputs,
    params:
        analysis_dir=lambda w: f"<results>/{w.name}/analysis",
        scenario_names=_sensitivity_scenario_names,
        generator_spec=lambda w: _sensitivity_generator(w),
        slice_grid=_sensitivity_slice_grid,
    output:
        global_indices="<results>/{name}/analysis/pce_global_indices_{prefix}.csv",
        conditional_indices="<results>/{name}/analysis/pce_conditional_indices_{prefix}.csv",
        validation="<results>/{name}/analysis/pce_validation_{prefix}.csv",
    group:
        "analysis_plot"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/compute_pce_sensitivity_{prefix}.log",
    benchmark:
        "<benchmarks>/{name}/compute_pce_sensitivity_{prefix}.tsv"
    script:
        "../scripts/analysis/compute_pce_sensitivity.py"
