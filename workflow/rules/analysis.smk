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
                "extract_baseline_deviation.py",
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
        crop_production="<results>/{name}/analysis/scen-{scenario}/crop_production.parquet",
        land_use="<results>/{name}/analysis/scen-{scenario}/land_use.parquet",
        animal_production="<results>/{name}/analysis/scen-{scenario}/animal_production.parquet",
        food_consumption="<results>/{name}/analysis/scen-{scenario}/food_consumption.parquet",
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.parquet",
        net_emissions="<results>/{name}/analysis/scen-{scenario}/net_emissions.parquet",
        objective_breakdown="<results>/{name}/analysis/scen-{scenario}/objective_breakdown.parquet",
        ghg_attribution="<results>/{name}/analysis/scen-{scenario}/ghg_attribution.parquet",
        ghg_attribution_totals="<results>/{name}/analysis/scen-{scenario}/ghg_attribution_totals.parquet",
        health_marginals="<results>/{name}/analysis/scen-{scenario}/health_marginals.parquet",
        health_totals="<results>/{name}/analysis/scen-{scenario}/health_totals.parquet",
        health_attribution="<results>/{name}/analysis/scen-{scenario}/health_attribution.parquet",
        feed_by_category="<results>/{name}/analysis/scen-{scenario}/feed_by_category.parquet",
        feed_by_animal="<results>/{name}/analysis/scen-{scenario}/feed_by_animal.parquet",
        luc_breakdown="<results>/{name}/analysis/scen-{scenario}/luc_breakdown.parquet",
        baseline_deviation="<results>/{name}/analysis/scen-{scenario}/baseline_deviation.parquet",
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


def _sensitivity_generator_group(gen):
    """Extract the group name from a sensitivity generator name pattern.

    E.g. "gsa_{sample_id}" -> "gsa", "gsa-l1-0p05_{sample_id}" -> "gsa-l1-0p05".
    """
    return gen["name"].split("_{")[0]


def _sensitivity_generator(wildcards):
    """Return the sensitivity generator whose group matches the wildcard."""
    raw_defs = config.get("scenarios") or {}
    group = wildcards.group

    generators = [
        gen
        for gen in raw_defs.get("_generators", [])
        if gen.get("mode") == "sensitivity"
    ]
    if not generators:
        raise ValueError("No sensitivity generator found in scenarios")

    for gen in generators:
        if _sensitivity_generator_group(gen) == group:
            return gen

    available = [_sensitivity_generator_group(gen) for gen in generators]
    raise ValueError(
        f"No sensitivity generator matches group '{group}'. "
        f"Available groups: {available}"
    )


def _sensitivity_scenario_names(wildcards):
    """Return all sensitivity scenario names matching the group in expansion order."""
    import re

    generator = _sensitivity_generator(wildcards)
    pattern = re.escape(generator["name"]).replace(r"\{sample_id\}", r"\d+") + "$"

    all_scenarios = list_scenarios()
    matching = [s for s in all_scenarios if re.match(pattern, s)]
    if not matching:
        raise ValueError(
            f"No scenarios found matching pattern '{pattern}'. "
            f"Available scenarios: {all_scenarios}"
        )
    return matching


_ANALYSIS_FILES = (
    "objective_breakdown.parquet",
    "net_emissions.parquet",
    "land_use.parquet",
    "health_totals.parquet",
)


def _sensitivity_scenario_inputs(wildcards):
    """Generate input files for all sensitivity scenarios.

    Lists all expected scenarios so Snakemake can build the full dependency
    chain (solve → analyze → compute_sobol).  The compute script receives
    only the available subset via params.scenario_names.
    """
    all_scenarios = _sensitivity_scenario_names(wildcards)
    return [
        f"<results>/{wildcards.name}/analysis/scen-{s}/{f}"
        for s in all_scenarios
        for f in _ANALYSIS_FILES
    ]


def _sensitivity_method_config(wildcards):
    """Return the method-specific config dict from sensitivity_analysis.methods."""
    method = wildcards.method
    sa_cfg = config.get("sensitivity_analysis", {})
    methods = sa_cfg.get("methods", {})
    if method not in methods:
        raise ValueError(
            f"Unknown sensitivity method '{method}'. "
            f"Available methods: {list(methods.keys())}"
        )
    return dict(methods[method])


def _sensitivity_slice_grid(wildcards):
    """Build a conditioning grid for slice parameters.

    Returns a dict mapping each slice parameter name to a list of
    linearly-spaced values between its min and max.  Grid resolution
    is read from the method config under ``sensitivity_analysis.methods``.
    """
    import numpy as _np

    from scenario_generators import build_chaospy_distribution

    generator = _sensitivity_generator(wildcards)
    method_cfg = _sensitivity_method_config(wildcards)
    n_grid = method_cfg.get("grid_resolution", 100)
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

    Dispatches to either PCE or Random Forest based on the ``{method}``
    wildcard, reading method-specific settings from
    ``sensitivity_analysis.methods.<method>`` in config.

    The {group} wildcard identifies the scenario sampling group (e.g., "gsa"),
    while {method} selects the surrogate fitting approach ("pce" or "rf").
    """
    input:
        _sensitivity_scenario_inputs,
    params:
        scenario_names=_sensitivity_scenario_names,
        generator_spec=lambda w: _sensitivity_generator(w),
        method=lambda w: w.method,
        method_config=_sensitivity_method_config,
        holdout_fraction=lambda w: config["sensitivity_analysis"]["holdout_fraction"],
        slice_grid=_sensitivity_slice_grid,
    output:
        global_indices="<results>/{name}/analysis/sobol_global_indices_{group}_{method}.parquet",
        conditional_indices="<results>/{name}/analysis/sobol_conditional_indices_{group}_{method}.parquet",
        conditional_joint_indices="<results>/{name}/analysis/sobol_conditional_joint_indices_{group}_{method}.parquet",
        validation="<results>/{name}/analysis/sobol_validation_{group}_{method}.parquet",
    group:
        "analysis_plot"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/compute_sobol_sensitivity_{group}_{method}.log",
    benchmark:
        "<benchmarks>/{name}/compute_sobol_sensitivity_{group}_{method}.tsv"
    script:
        "../scripts/analysis/compute_sobol_sensitivity.py"
