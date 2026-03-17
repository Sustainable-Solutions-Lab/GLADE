# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


rule prepare_faostat_emissions:
    input:
        gt_csv="data/downloads/faostat/GT.parquet",
    output:
        "<processing>/{name}/faostat_emissions.csv",
    params:
        year=config["baseline_year"],
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
        m49_codes="data/curated/M49-codes.csv",
        risk_breakpoints="<processing>/{name}/health/risk_breakpoints.csv",
        health_cluster_cause="<processing>/{name}/health/cluster_cause_baseline.csv",
        health_cause_log="<processing>/{name}/health/cause_log_breakpoints.csv",
        health_clusters="<processing>/{name}/health/country_clusters.csv",
        population="<processing>/{name}/population.csv",
        derived_tmrel="<processing>/{name}/health/derived_tmrel.csv",
        analysis_scripts=expand(
            "workflow/scripts/analysis/{script}",
            script=[
                "extract_statistics.py",
                "extract_net_emissions.py",
                "extract_objective_breakdown.py",
                "extract_ghg_attribution.py",
                "extract_health_impacts.py",
            ],
        ),
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
        health_attribution="<results>/{name}/analysis/scen-{scenario}/health_attribution.csv",
        feed_by_category="<results>/{name}/analysis/scen-{scenario}/feed_by_category.csv",
        feed_by_animal="<results>/{name}/analysis/scen-{scenario}/feed_by_animal.csv",
        luc_breakdown="<results>/{name}/analysis/scen-{scenario}/luc_breakdown.csv",
    group:
        "analyze_model"
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
    """Return all sensitivity scenario names matching the prefix in expansion order."""
    prefix = wildcards.prefix
    all_scenarios = list_scenarios()
    matching = [s for s in all_scenarios if s.startswith(prefix)]
    if not matching:
        raise ValueError(
            f"No scenarios found with prefix '{prefix}'. "
            f"Available scenarios: {all_scenarios}"
        )
    return matching


_ANALYSIS_CSVS = (
    "objective_breakdown.csv",
    "net_emissions.csv",
    "land_use.csv",
    "health_totals.csv",
)


def _available_sensitivity_scenarios(wildcards):
    """Return sensitivity scenarios whose analysis directories exist on disk.

    This allows the GSA rule to run with an incomplete set of solved
    scenarios (e.g. when some solves failed or results were partially
    copied back from a cluster).
    """
    from pathlib import Path

    all_scenarios = _sensitivity_scenario_names(wildcards)
    base = Path(f"results/{wildcards.name}/analysis")
    available = [
        s
        for s in all_scenarios
        if all((base / f"scen-{s}" / csv).exists() for csv in _ANALYSIS_CSVS)
    ]
    if not available:
        raise ValueError(
            f"No completed scenarios with prefix '{wildcards.prefix}' "
            f"found in {base}"
        )
    n_total = len(all_scenarios)
    n_avail = len(available)
    if n_avail < n_total * 0.5:
        print(
            f"WARNING: Only {n_avail}/{n_total} sensitivity scenarios available "
            f"({100*n_avail/ n_total:.0f}%). GSA results may be unreliable."
        )
    return available


def _sensitivity_scenario_inputs(wildcards):
    """Generate input files for sensitivity scenarios that completed successfully."""
    available = _available_sensitivity_scenarios(wildcards)
    return [
        f"<results>/{wildcards.name}/analysis/scen-{s}/{csv}"
        for s in available
        for csv in _ANALYSIS_CSVS
    ]


def _sensitivity_slice_grid(wildcards):
    """Build a conditioning grid for slice parameters.

    Returns a dict mapping each slice parameter name to a list of
    linearly-spaced values between its min and max.  Resolution
    defaults to 100 but can be overridden via ``grid_resolution``
    in the generator spec.
    """
    import numpy as _np

    from scenario_generators import build_chaospy_distribution

    generator = _sensitivity_generator(wildcards)
    n_grid = generator.get("grid_resolution", 100)
    slice_params = generator.get("slice_parameters", [])
    grid = {}
    for sp in slice_params:
        spec = generator["parameters"][sp]
        dist = build_chaospy_distribution(spec)
        lo, hi = float(dist.lower[0]), float(dist.upper[0])
        grid[sp] = [float(v) for v in _np.linspace(lo, hi, n_grid)]
    return grid


rule compute_sobol_sensitivity:
    """Compute global sensitivity indices from ensemble scenario runs.

    Dispatches to either PCE or Random Forest based on the ``method``
    key in the generator spec (default: ``"pce"``).

    To use, ensure your scenarios file has a generator with mode: sensitivity,
    then run:
        tools/smk --configfile config/pce_sensitivity.yaml -- <results>/{name}/analysis/sobol_global_indices_{prefix}.csv

    The {prefix} wildcard matches the scenario name prefix (e.g., "pce_" for
    scenarios pce_0, pce_1, ...).
    """
    input:
        _sensitivity_scenario_inputs,
    params:
        scenario_names=_available_sensitivity_scenarios,
        generator_spec=lambda w: _sensitivity_generator(w),
        slice_grid=_sensitivity_slice_grid,
    output:
        global_indices="<results>/{name}/analysis/sobol_global_indices_{prefix}.csv",
        conditional_indices="<results>/{name}/analysis/sobol_conditional_indices_{prefix}.csv",
        conditional_joint_indices="<results>/{name}/analysis/sobol_conditional_joint_indices_{prefix}.csv",
        validation="<results>/{name}/analysis/sobol_validation_{prefix}.csv",
    group:
        "analysis_plot"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/compute_sobol_sensitivity_{prefix}.log",
    benchmark:
        "<benchmarks>/{name}/compute_sobol_sensitivity_{prefix}.tsv"
    script:
        "../scripts/analysis/compute_sobol_sensitivity.py"
