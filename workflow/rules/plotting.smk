# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


gaez = config["data"]["gaez"]
plotting_cfg = config.get("plotting", {})
food_group_colors = plotting_cfg.get("colors", {}).get("food_groups", {})
_param_groups_cfg = plotting_cfg.get("colors", {}).get("parameter_groups", {})
parameter_colors = {
    param: color
    for group in _param_groups_cfg.values()
    for param, color in group.get("parameters", {}).items()
}
parameter_group_order = [
    param
    for group in _param_groups_cfg.values()
    for param in group.get("parameters", {})
]
comparison_scenarios = plotting_cfg["comparison_scenarios"]

# Expand "all" to all scenario names from config
if comparison_scenarios == "all":
    scenario_names = list_scenarios()
    if not scenario_names:
        raise ValueError(
            "Cannot use comparison_scenarios='all' without scenarios defined in config"
        )
    comparison_scenarios = [f"scen-{name}" for name in scenario_names]


def _sobol_sensitivity_generators():
    """Return all sensitivity generators, or an empty list if none configured."""
    raw_defs = config["scenarios"]
    if raw_defs is None:
        return []
    generators_raw = raw_defs["_generators"] if "_generators" in raw_defs else []
    return [gen for gen in generators_raw if gen.get("mode") == "sensitivity"]


def _sobol_non_slice_parameters():
    """Return sensitivity parameters excluding configured slice parameters."""
    generators = _sobol_sensitivity_generators()
    if not generators:
        return []
    # All generators share the same parameter set; use the first.
    generator = generators[0]
    slice_parameters = set(generator["slice_parameters"])
    if len(slice_parameters) < 2:
        return []
    return [
        param_name
        for param_name in generator["parameters"]
        if param_name not in slice_parameters
    ]


sobol_non_slice_parameters = _sobol_non_slice_parameters()


def _sobol_parameter_groups():
    """Return parameter grouping dict from the sensitivity generators."""
    generators = _sobol_sensitivity_generators()
    if not generators:
        return {}
    # All generators share the same parameter groups; use the first.
    return dict(generators[0].get("parameter_groups", {}))


sobol_parameter_groups = _sobol_parameter_groups()


def _sobol_sensitivity_prefixes():
    """Return all scenario name prefixes for sensitivity generators."""
    generators = _sobol_sensitivity_generators()
    # Extract prefix from name pattern like "pce_{sample_id}" -> "pce"
    return [gen["name"].split("_{")[0] for gen in generators]


def _sobol_plot_targets():
    """Build all Sobol sensitivity plot targets for the collection rule."""
    prefixes = _sobol_sensitivity_prefixes()
    if not prefixes:
        return []

    non_slice = _sobol_non_slice_parameters()
    targets = []
    for prefix in prefixes:
        targets += [
            # Base conditional plots
            f"<results>/{{name}}/plots/sobol_conditional_s1_vs_value_per_yll_{prefix}.pdf",
            f"<results>/{{name}}/plots/sobol_conditional_s1_vs_ghg_price_{prefix}.pdf",
            # Phase diagram
            f"<results>/{{name}}/plots/sobol_conditional_dominant_factor_{prefix}.pdf",
        ]
        # Per-parameter contour surfaces
        for param in non_slice:
            targets.append(
                f"<results>/{{name}}/plots/sobol_conditional_s1_surface_{param}_{prefix}.pdf"
            )
    return targets


SOBOL_PLOT_TARGETS = _sobol_plot_targets()


def _gaez_actual_yield_raster_path(crop_name: str, water_supply: str) -> str:
    # Wrap helper to provide clearer error message for plotting context.
    try:
        return gaez_path("actual_yield", water_supply, crop_name)
    except ValueError as exc:
        raise ValueError(
            f"Missing RES06 actual yield data for crop '{crop_name}'."
        ) from exc


def yield_gap_raster_inputs(wildcards):
    crop_name = wildcards.crop
    ws = wildcards.water_supply
    return {
        "potential_yield": gaez_path("yield", ws, crop_name),
        "actual_yield": _gaez_actual_yield_raster_path(crop_name, ws),
    }


rule plot_yield_gap:
    input:
        unpack(yield_gap_raster_inputs),
    output:
        pdf="<results>/{name}/plots/yield_gap_{crop}_{water_supply}.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_yield_gap_{crop}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/plot_yield_gap_{crop}_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_gap.py"


rule plot_regions_map:
    input:
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/regions_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "<logs>/{name}/plot_regions_map.log",
    benchmark:
        "<benchmarks>/{name}/plot_regions_map.tsv"
    script:
        "../scripts/plotting/plot_regions_map.py"


rule plot_resource_classes_map:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/resource_classes_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1400,
    log:
        "<logs>/{name}/plot_resource_classes_map.log",
    benchmark:
        "<benchmarks>/{name}/plot_resource_classes_map.tsv"
    script:
        "../scripts/plotting/plot_resource_classes_map.py"


rule plot_objective_breakdown:
    """Plot objective function breakdown from pre-computed analysis."""
    input:
        objective_breakdown="<results>/{name}/analysis/scen-{scenario}/objective_breakdown.parquet",
    output:
        breakdown_pdf="<results>/{name}/plots/scen-{scenario}/objective_breakdown.pdf",
        breakdown_csv="<results>/{name}/plots/scen-{scenario}/objective_breakdown.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_objective_breakdown_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_objective_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_objective_breakdown.py"


rule plot_yll_global_by_cause:
    input:
        cluster_cause="<processing>/{name}/health/cluster_cause_baseline.csv",
        cluster_summary="<processing>/{name}/health/cluster_summary.csv",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/yll_global_by_cause.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/yll_global_by_cause.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_yll_global_by_cause_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_yll_global_by_cause_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_yll_global_by_cause.py"


rule plot_health_impacts:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        regions="<processing>/{name}/regions.geojson",
        risk_breakpoints="<processing>/{name}/health/risk_breakpoints.csv",
        health_cluster_cause="<processing>/{name}/health/cluster_cause_baseline.csv",
        health_cause_log="<processing>/{name}/health/cause_log_breakpoints.csv",
        health_cluster_summary="<processing>/{name}/health/cluster_summary.csv",
        health_clusters="<processing>/{name}/health/country_clusters.csv",
        health_cluster_risk_baseline="<processing>/{name}/health/cluster_risk_baseline.csv",
        derived_tmrel="<processing>/{name}/health/derived_tmrel.csv",
        population="<processing>/{name}/population.csv",
        food_groups="data/curated/food_groups.csv",
    params:
        health_risk_factors=config["health"]["risk_factors"],
        # Convert from USD/YLL -> bnUSD/YLL for objective consistency
        health_value_per_yll=lambda w: float(
            get_effective_config(w.scenario)["health"]["value_per_yll"]
        )
        * 1e-9,
    output:
        health_map_pdf="<results>/{name}/plots/scen-{scenario}/health_risk_map.pdf",
        health_map_csv="<results>/{name}/plots/scen-{scenario}/health_risk_by_region.csv",
        health_baseline_map_pdf="<results>/{name}/plots/scen-{scenario}/health_baseline_map.pdf",
        health_baseline_map_csv="<results>/{name}/plots/scen-{scenario}/health_baseline_by_region.csv",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_health_impacts_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_health_impacts_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_health_impacts.py"


rule plot_relative_risk_curves:
    input:
        relative_risks="<processing>/{name}/health/relative_risks.csv",
    output:
        pdf="<results>/{name}/plots/relative_risk_curves.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_relative_risk_curves.log",
    benchmark:
        "<benchmarks>/{name}/plot_relative_risk_curves.tsv"
    script:
        "../scripts/plotting/plot_relative_risk_curves.py"


rule plot_crop_production_map:
    input:
        land_use="<results>/{name}/analysis/scen-{scenario}/land_use.parquet",
        regions="<processing>/{name}/regions.geojson",
        resource_classes="<processing>/{name}/resource_classes.nc",
        land_area_by_class="<processing>/{name}/land_area_by_class.csv",
        land_grazing_only="<processing>/{name}/land_grazing_only_by_class.csv",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/crop_production_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1400,
    log:
        "<logs>/{name}/plot_crop_production_map_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_crop_production_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_production_map.py"


rule plot_crop_trade_map:
    input:
        land_use="<results>/{name}/analysis/scen-{scenario}/land_use.parquet",
        regions="<processing>/{name}/regions.geojson",
        resource_classes="<processing>/{name}/resource_classes.nc",
        land_area_by_class="<processing>/{name}/land_area_by_class.csv",
        land_grazing_only="<processing>/{name}/land_grazing_only_by_class.csv",
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/crop_trade_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_crop_trade_map_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_crop_trade_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_trade_map.py"


rule plot_crop_use_breakdown:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/crop_use_breakdown.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/crop_use_breakdown.csv",
    params:
        animal_products=config["animal_products"]["include"],
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_crop_use_breakdown_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_crop_use_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_use_breakdown.py"


rule plot_feed_breakdown:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/feed_breakdown.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/feed_breakdown.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1100,
    log:
        "<logs>/{name}/plot_feed_breakdown_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_feed_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_feed_breakdown.py"


rule plot_food_group_slack:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/food_group_slack.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/food_group_slack.csv",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=950,
    log:
        "<logs>/{name}/plot_food_group_slack_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_food_group_slack_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_group_slack.py"


rule plot_feed_slack:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/feed_slack.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/feed_slack.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=950,
    log:
        "<logs>/{name}/plot_feed_slack_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_feed_slack_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_feed_slack.py"


rule plot_slack_overview:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/slack_overview.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/slack_overview.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_slack_overview_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_slack_overview_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_slack_overview.py"


rule plot_water_balance:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/water_balance.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/water_balance.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_water_balance_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_water_balance_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_balance.py"


rule plot_water_use_map:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/water_use_map.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/water_use_by_region.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_water_use_map_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_water_use_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_use_map.py"


rule plot_food_consumption:
    input:
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.parquet",
        population="<processing>/{name}/population.csv",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/food_consumption.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "<logs>/{name}/plot_food_consumption_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_food_consumption_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_consumption.py"


def food_consumption_comparison_inputs(wildcards):
    return [
        f"<results>/{wildcards.name}/analysis/{suffix}/food_group_consumption.parquet"
        for suffix in comparison_scenarios
    ]


rule plot_food_consumption_comparison:
    input:
        food_group_consumption=food_consumption_comparison_inputs,
    output:
        pdf="<results>/{name}/plots/food_consumption_comparison.pdf",
        csv="<results>/{name}/plots/food_consumption_comparison.csv",
    params:
        wildcards=comparison_scenarios,
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_food_consumption_comparison.log",
    benchmark:
        "<benchmarks>/{name}/plot_food_consumption_comparison.tsv"
    script:
        "../scripts/plotting/plot_food_consumption_comparison.py"


def system_cost_comparison_inputs(wildcards):
    return [
        f"<results>/{wildcards.name}/plots/{suffix}/objective_breakdown.csv"
        for suffix in comparison_scenarios
    ]


rule plot_system_cost_comparison:
    input:
        breakdowns=system_cost_comparison_inputs,
    output:
        pdf="<results>/{name}/plots/system_cost_comparison.pdf",
        csv="<results>/{name}/plots/system_cost_comparison.csv",
    params:
        wildcards=comparison_scenarios,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_system_cost_comparison.log",
    benchmark:
        "<benchmarks>/{name}/plot_system_cost_comparison.tsv"
    script:
        "../scripts/plotting/plot_system_cost_comparison.py"


rule plot_food_consumption_map:
    input:
        food_group_consumption="<results>/{name}/analysis/scen-{scenario}/food_group_consumption.parquet",
        population="<processing>/{name}/population.csv",
        clusters="<processing>/{name}/health/country_clusters.csv",
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/food_consumption_map.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/food_consumption_map.csv",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "<logs>/{name}/plot_food_consumption_map_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_food_consumption_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_consumption_map.py"


rule plot_food_consumption_baseline_map:
    input:
        diet="<processing>/{name}/dietary_intake.csv",
        population="<processing>/{name}/population.csv",
        clusters="<processing>/{name}/health/country_clusters.csv",
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/food_consumption_baseline_map.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/food_consumption_baseline_map.csv",
    params:
        age=config.get("diet", {}).get("baseline_age", "All ages"),
        reference_year=config["baseline_year"],
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_food_consumption_baseline_map-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_food_consumption_baseline_map-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_baseline_food_consumption_map.py"


def yield_map_inputs(wildcards):
    if wildcards.item == "pasture":
        return {"raster": "data/downloads/grassland_yield_historical.nc4"}
    else:
        return {
            "raster": gaez_path("yield", wildcards.water_supply, wildcards.item),
            "conversions": "data/curated/yield_unit_conversions.csv",
        }


rule plot_yield_map:
    input:
        unpack(yield_map_inputs),
    output:
        pdf="<results>/{name}/plots/yield_map_{item}_{water_supply}.pdf",
    params:
        gaez=gaez,
        item=lambda wc: wc.item,
        supply=lambda wc: wc.water_supply,
        unit="t/ha",
        cmap="YlGn",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_yield_map_{item}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/plot_yield_map_{item}_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_map.py"


rule plot_average_yield_gap_by_country:
    input:
        regions="<processing>/{name}/regions.geojson",
        csv="<processing>/{name}/yield_gap_by_country_all_crops_{water_supply}.csv",
    output:
        pdf="<results>/{name}/plots/yield_gap_by_country_average_{water_supply}.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_average_yield_gap_by_country_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/plot_average_yield_gap_by_country_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_gap_by_country_average.py"


rule plot_water_value_map:
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/water_value_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_water_value_map_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_water_value_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_value_map.py"


rule plot_emissions_breakdown:
    input:
        net_emissions="<results>/{name}/analysis/scen-{scenario}/net_emissions.parquet",
        faostat_emissions="<processing>/{name}/faostat_emissions.csv",
        gleam_emissions="data/bundled/gleam3/livestock_emissions.csv",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/emissions_breakdown.pdf",
    params:
        ch4_gwp=lambda w: get_effective_config(w.scenario)["emissions"][
            "ch4_to_co2_factor"
        ],
        n2o_gwp=lambda w: get_effective_config(w.scenario)["emissions"][
            "n2o_to_co2_factor"
        ],
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_emissions_breakdown_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_emissions_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_emissions_breakdown.py"


rule plot_consumption_balance:
    input:
        food_consumption="<results>/{name}/analysis/scen-{scenario}/food_consumption.parquet",
        food_groups="data/curated/food_groups.csv",
        population="<processing>/{name}/population.csv",
        clusters="<processing>/{name}/health/country_clusters.csv",
    output:
        pdf="<results>/{name}/plots/scen-{scenario}/consumption_balance.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_consumption_balance_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_consumption_balance_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_consumption_balance.py"


rule plot_ghg_health_global:
    """Plot consumption-weighted global average GHG and YLL by food group."""
    input:
        ghg_intensity="<results>/{name}/analysis/scen-{scenario}/ghg_attribution.parquet",
        health_marginals="<results>/{name}/analysis/scen-{scenario}/health_marginals.parquet",
    output:
        ghg_pdf="<results>/{name}/plots/scen-{scenario}/marginal_ghg_global.pdf",
        yll_pdf="<results>/{name}/plots/scen-{scenario}/marginal_yll_global.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/plot_ghg_health_global_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_ghg_health_global_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_ghg_health_global.py"


rule plot_luc_emissions:
    """Plot land use change emissions by country (bar) and resource class (map)."""
    input:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
        regions="<processing>/{name}/regions.geojson",
        resource_classes="<processing>/{name}/resource_classes.nc",
    output:
        bar_pdf="<results>/{name}/plots/scen-{scenario}/luc_emissions_bar.pdf",
        map_pdf="<results>/{name}/plots/scen-{scenario}/luc_emissions_map.pdf",
        csv="<results>/{name}/plots/scen-{scenario}/luc_emissions.csv",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_luc_emissions_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/plot_luc_emissions_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_luc_emissions.py"


rule plot_sobol_conditional_sensitivity:
    """Plot stacked conditional Sobol shares vs policy slice parameters."""
    input:
        conditional_indices="<results>/{name}/analysis/sobol_conditional_indices_{prefix}.parquet",
        validation="<results>/{name}/analysis/sobol_validation_{prefix}.parquet",
    output:
        value_per_yll_pdf="<results>/{name}/plots/sobol_conditional_s1_vs_value_per_yll_{prefix}.pdf",
        ghg_price_pdf="<results>/{name}/plots/sobol_conditional_s1_vs_ghg_price_{prefix}.pdf",
    params:
        metric="S1_cond",
        parameter_colors=parameter_colors,
        parameter_group_order=parameter_group_order,
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "<logs>/{name}/plot_sobol_conditional_sensitivity_{prefix}.log",
    benchmark:
        "<benchmarks>/{name}/plot_sobol_conditional_sensitivity_{prefix}.tsv"
    script:
        "../scripts/plotting/plot_sobol_conditional_sensitivity.py"


rule plot_sobol_joint_conditional_contour:
    """Plot conditional Sobol surface for one non-slice parameter."""
    input:
        conditional_joint_indices="<results>/{name}/analysis/sobol_conditional_joint_indices_{prefix}.parquet",
        validation="<results>/{name}/analysis/sobol_validation_{prefix}.parquet",
    output:
        pdf="<results>/{name}/plots/sobol_conditional_s1_surface_{parameter}_{prefix}.pdf",
    params:
        metric="S1_cond",
        allowed_parameters=sobol_non_slice_parameters,
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1200,
    log:
        "<logs>/{name}/plot_sobol_joint_conditional_contour_{parameter}_{prefix}.log",
    benchmark:
        "<benchmarks>/{name}/plot_sobol_joint_conditional_contour_{parameter}_{prefix}.tsv"
    script:
        "../scripts/plotting/plot_sobol_joint_conditional_contour.py"


rule plot_sobol_joint_conditional_phase_diagram:
    """Plot dominant non-slice sensitivity parameter across 2D policy space."""
    input:
        conditional_joint_indices="<results>/{name}/analysis/sobol_conditional_joint_indices_{prefix}.parquet",
        validation="<results>/{name}/analysis/sobol_validation_{prefix}.parquet",
    output:
        pdf="<results>/{name}/plots/sobol_conditional_dominant_factor_{prefix}.pdf",
    params:
        metric="S1_cond",
        allowed_parameters=sobol_non_slice_parameters,
        parameter_colors=parameter_colors,
        parameter_group_order=parameter_group_order,
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1200,
    log:
        "<logs>/{name}/plot_sobol_joint_conditional_phase_diagram_{prefix}.log",
    benchmark:
        "<benchmarks>/{name}/plot_sobol_joint_conditional_phase_diagram_{prefix}.tsv"
    script:
        "../scripts/plotting/plot_sobol_joint_conditional_phase_diagram.py"


rule sobol_plots:
    """Generate all Sobol sensitivity analysis plots."""
    input:
        expand(SOBOL_PLOT_TARGETS, name=[name]),
