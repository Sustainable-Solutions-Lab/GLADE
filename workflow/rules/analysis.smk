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


rule analyze_model:
    """Analyze solved network: extract statistics, emissions, GHG attribution, and health impacts.

    Loads the network once and runs all analysis steps in a single pass,
    passing intermediate DataFrames in memory rather than writing/re-reading them.
    """
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        food_groups="data/curated/food_groups.csv",
        risk_breakpoints="<processing>/{name}/health/scen-{scenario}/risk_breakpoints.csv",
        health_cluster_cause="<processing>/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        health_cause_log="<processing>/{name}/health/scen-{scenario}/cause_log_breakpoints.csv",
        health_clusters="<processing>/{name}/health/scen-{scenario}/country_clusters.csv",
        population="<processing>/{name}/population.csv",
    params:
        ghg_price=lambda w: get_effective_config(w.scenario)["emissions"]["ghg_price"],
        ch4_gwp=config["emissions"]["ch4_to_co2_factor"],
        n2o_gwp=config["emissions"]["n2o_to_co2_factor"],
        value_per_yll=lambda w: get_effective_config(w.scenario)["health"][
            "value_per_yll"
        ],
        health_risk_factors=config["health"]["risk_factors"],
    output:
        crop_production="<results>/{name}/analysis/scen-{scenario}/crop_production.csv",
        land_use="<results>/{name}/analysis/scen-{scenario}/land_use.csv",
        animal_production="<results>/{name}/analysis/scen-{scenario}/animal_production.csv",
        food_consumption="<results>/{name}/analysis/scen-{scenario}/food_consumption.csv",
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
        net_emissions="<results>/{name}/analysis/scen-{scenario}/net_emissions.csv",
        objective_breakdown="<results>/{name}/analysis/scen-{scenario}/objective_breakdown.csv",
        ghg_attribution="<results>/{name}/analysis/scen-{scenario}/ghg_attribution.csv",
        ghg_attribution_totals="<results>/{name}/analysis/scen-{scenario}/ghg_attribution_totals.csv",
        health_marginals="<results>/{name}/analysis/scen-{scenario}/health_marginals.csv",
        health_totals="<results>/{name}/analysis/scen-{scenario}/health_totals.csv",
    group:
        "model_core"
    resources:
        runtime="5m",
        mem_mb=1500,
    log:
        "<logs>/{name}/analyze_model_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/analyze_model_scen-{scenario}.tsv"
    script:
        "../scripts/analysis/analyze_model.py"


def _sensitivity_generator(wildcards):
    """Extract the single sensitivity generator from config scenarios."""
    raw_defs = config.get("scenarios") or {}

    generators = [
        gen
        for gen in raw_defs.get("_generators", [])
        if gen.get("mode") == "sensitivity"
    ]
    if not generators:
        raise ValueError("No sensitivity generator found in scenarios")
    if len(generators) > 1:
        raise ValueError(
            "Multiple sensitivity generators found in scenarios. "
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
                f"<results>/{wildcards.name}/analysis/scen-{scenario}/net_emissions.csv",
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
