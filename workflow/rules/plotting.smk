# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


gaez = config["data"]["gaez"]
plotting_cfg = config.get("plotting", {})
food_group_colors = plotting_cfg.get("colors", {}).get("food_groups", {})
comparison_scenarios = plotting_cfg["comparison_scenarios"]

# Expand "all" to all scenario names from scenario_defs
if comparison_scenarios == "all":
    scenario_names = list_scenarios()
    if not scenario_names:
        raise ValueError(
            "Cannot use comparison_scenarios='all' without scenario_defs defined in config"
        )
    comparison_scenarios = [f"scen-{name}" for name in scenario_names]


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
        pdf="results/{name}/plots/yield_gap_{crop}_{water_supply}.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_yield_gap_{crop}_{water_supply}.log",
    benchmark:
        "benchmarks/{name}/plot_yield_gap_{crop}_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_gap.py"


rule plot_regions_map:
    input:
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/regions_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "logs/{name}/plot_regions_map.log",
    benchmark:
        "benchmarks/{name}/plot_regions_map.tsv"
    script:
        "../scripts/plotting/plot_regions_map.py"


rule plot_resource_classes_map:
    input:
        classes="processing/{name}/resource_classes.nc",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/resource_classes_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1400,
    log:
        "logs/{name}/plot_resource_classes_map.log",
    benchmark:
        "benchmarks/{name}/plot_resource_classes_map.tsv"
    script:
        "../scripts/plotting/plot_resource_classes_map.py"


rule plot_objective_breakdown:
    """Plot objective function breakdown from pre-computed analysis."""
    input:
        objective_breakdown="results/{name}/analysis/scen-{scenario}/objective_breakdown.csv",
    output:
        breakdown_pdf="results/{name}/plots/scen-{scenario}/objective_breakdown.pdf",
        breakdown_csv="results/{name}/plots/scen-{scenario}/objective_breakdown.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_objective_breakdown_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_objective_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_objective_breakdown.py"


rule plot_yll_global_by_cause:
    input:
        cluster_cause="processing/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        cluster_summary="processing/{name}/health/scen-{scenario}/cluster_summary.csv",
    output:
        pdf="results/{name}/plots/scen-{scenario}/yll_global_by_cause.pdf",
        csv="results/{name}/plots/scen-{scenario}/yll_global_by_cause.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_yll_global_by_cause_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_yll_global_by_cause_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_yll_global_by_cause.py"


rule plot_health_impacts:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        regions="processing/{name}/regions.geojson",
        risk_breakpoints="processing/{name}/health/scen-{scenario}/risk_breakpoints.csv",
        health_cluster_cause="processing/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        health_cause_log="processing/{name}/health/scen-{scenario}/cause_log_breakpoints.csv",
        health_cluster_summary="processing/{name}/health/scen-{scenario}/cluster_summary.csv",
        health_clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
        health_cluster_risk_baseline="processing/{name}/health/scen-{scenario}/cluster_risk_baseline.csv",
        derived_tmrel="processing/{name}/health/scen-{scenario}/derived_tmrel.csv",
        population="processing/{name}/population.csv",
        food_groups="data/curated/food_groups.csv",
    params:
        health_risk_factors=config["health"]["risk_factors"],
        # Convert from USD/YLL -> bnUSD/YLL for objective consistency
        health_value_per_yll=lambda w: float(
            get_effective_config(w.scenario)["health"]["value_per_yll"]
        )
        * 1e-9,
    output:
        health_map_pdf="results/{name}/plots/scen-{scenario}/health_risk_map.pdf",
        health_map_csv="results/{name}/plots/scen-{scenario}/health_risk_by_region.csv",
        health_baseline_map_pdf="results/{name}/plots/scen-{scenario}/health_baseline_map.pdf",
        health_baseline_map_csv="results/{name}/plots/scen-{scenario}/health_baseline_by_region.csv",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_health_impacts_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_health_impacts_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_health_impacts.py"


rule plot_relative_risk_curves:
    input:
        relative_risks="processing/{name}/health/relative_risks.csv",
    output:
        pdf="results/{name}/plots/relative_risk_curves.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_relative_risk_curves.log",
    benchmark:
        "benchmarks/{name}/plot_relative_risk_curves.tsv"
    script:
        "../scripts/plotting/plot_relative_risk_curves.py"


rule plot_crop_production_map:
    input:
        land_use="results/{name}/analysis/scen-{scenario}/land_use.csv",
        regions="processing/{name}/regions.geojson",
        resource_classes="processing/{name}/resource_classes.nc",
        land_area_by_class="processing/{name}/land_area_by_class.csv",
        land_grazing_only="processing/{name}/land_grazing_only_by_class.csv",
    output:
        pdf="results/{name}/plots/scen-{scenario}/crop_production_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1400,
    log:
        "logs/{name}/plot_crop_production_map_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_crop_production_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_production_map.py"


rule plot_crop_trade_map:
    input:
        land_use="results/{name}/analysis/scen-{scenario}/land_use.csv",
        regions="processing/{name}/regions.geojson",
        resource_classes="processing/{name}/resource_classes.nc",
        land_area_by_class="processing/{name}/land_area_by_class.csv",
        land_grazing_only="processing/{name}/land_grazing_only_by_class.csv",
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/crop_trade_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_crop_trade_map_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_crop_trade_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_trade_map.py"


rule plot_crop_use_breakdown:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/crop_use_breakdown.pdf",
        csv="results/{name}/plots/scen-{scenario}/crop_use_breakdown.csv",
    params:
        animal_products=config["animal_products"]["include"],
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_crop_use_breakdown_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_crop_use_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_crop_use_breakdown.py"


rule plot_feed_breakdown:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/feed_breakdown.pdf",
        csv="results/{name}/plots/scen-{scenario}/feed_breakdown.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1100,
    log:
        "logs/{name}/plot_feed_breakdown_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_feed_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_feed_breakdown.py"


rule plot_food_group_slack:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/food_group_slack.pdf",
        csv="results/{name}/plots/scen-{scenario}/food_group_slack.csv",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=950,
    log:
        "logs/{name}/plot_food_group_slack_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_food_group_slack_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_group_slack.py"


rule plot_slack_overview:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/slack_overview.pdf",
        csv="results/{name}/plots/scen-{scenario}/slack_overview.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_slack_overview_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_slack_overview_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_slack_overview.py"


rule plot_water_balance:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    output:
        pdf="results/{name}/plots/scen-{scenario}/water_balance.pdf",
        csv="results/{name}/plots/scen-{scenario}/water_balance.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_water_balance_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_water_balance_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_balance.py"


rule plot_water_use_map:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/scen-{scenario}/water_use_map.pdf",
        csv="results/{name}/plots/scen-{scenario}/water_use_by_region.csv",
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_water_use_map_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_water_use_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_use_map.py"


rule plot_food_consumption:
    input:
        food_group_consumption="results/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
        population="processing/{name}/population.csv",
    output:
        pdf="results/{name}/plots/scen-{scenario}/food_consumption.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "logs/{name}/plot_food_consumption_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_food_consumption_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_consumption.py"


def food_consumption_comparison_inputs(wildcards):
    return [
        f"results/{wildcards.name}/analysis/{suffix}/food_group_consumption.csv"
        for suffix in comparison_scenarios
    ]


rule plot_food_consumption_comparison:
    input:
        food_group_consumption=food_consumption_comparison_inputs,
    output:
        pdf="results/{name}/plots/food_consumption_comparison.pdf",
        csv="results/{name}/plots/food_consumption_comparison.csv",
    params:
        wildcards=comparison_scenarios,
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_food_consumption_comparison.log",
    benchmark:
        "benchmarks/{name}/plot_food_consumption_comparison.tsv"
    script:
        "../scripts/plotting/plot_food_consumption_comparison.py"


def system_cost_comparison_inputs(wildcards):
    return [
        f"results/{wildcards.name}/plots/{suffix}/objective_breakdown.csv"
        for suffix in comparison_scenarios
    ]


rule plot_system_cost_comparison:
    input:
        breakdowns=system_cost_comparison_inputs,
    output:
        pdf="results/{name}/plots/system_cost_comparison.pdf",
        csv="results/{name}/plots/system_cost_comparison.csv",
    params:
        wildcards=comparison_scenarios,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_system_cost_comparison.log",
    benchmark:
        "benchmarks/{name}/plot_system_cost_comparison.tsv"
    script:
        "../scripts/plotting/plot_system_cost_comparison.py"


rule plot_food_consumption_map:
    input:
        food_group_consumption="results/{name}/analysis/scen-{scenario}/food_group_consumption.csv",
        population="processing/{name}/population.csv",
        clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/scen-{scenario}/food_consumption_map.pdf",
        csv="results/{name}/plots/scen-{scenario}/food_consumption_map.csv",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "logs/{name}/plot_food_consumption_map_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_food_consumption_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_food_consumption_map.py"


rule plot_food_consumption_baseline_map:
    input:
        diet="processing/{name}/dietary_intake.csv",
        population="processing/{name}/population.csv",
        clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/scen-{scenario}/food_consumption_baseline_map.pdf",
        csv="results/{name}/plots/scen-{scenario}/food_consumption_baseline_map.csv",
    params:
        age=config.get("diet", {}).get("baseline_age", "All ages"),
        reference_year=config.get("diet", {}).get(
            "baseline_reference_year", config["health"]["reference_year"]
        ),
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_food_consumption_baseline_map-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_food_consumption_baseline_map-{scenario}.tsv"
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
        pdf="results/{name}/plots/yield_map_{item}_{water_supply}.pdf",
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
        "logs/{name}/plot_yield_map_{item}_{water_supply}.log",
    benchmark:
        "benchmarks/{name}/plot_yield_map_{item}_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_map.py"


rule plot_average_yield_gap_by_country:
    input:
        regions="processing/{name}/regions.geojson",
        csv="processing/{name}/yield_gap_by_country_all_crops_{water_supply}.csv",
    output:
        pdf="results/{name}/plots/yield_gap_by_country_average_{water_supply}.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_average_yield_gap_by_country_{water_supply}.log",
    benchmark:
        "benchmarks/{name}/plot_average_yield_gap_by_country_{water_supply}.tsv"
    script:
        "../scripts/plotting/plot_yield_gap_by_country_average.py"


rule plot_water_value_map:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/scen-{scenario}/water_value_map.pdf",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_water_value_map_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_water_value_map_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_water_value_map.py"


rule plot_emissions_breakdown:
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        faostat_emissions="processing/{name}/faostat_emissions.csv",
        gleam_emissions="data/curated/gleam_livestock_emissions.csv",
        regions="processing/{name}/regions.geojson",
    output:
        pdf="results/{name}/plots/scen-{scenario}/emissions_breakdown.pdf",
        csv="results/{name}/plots/scen-{scenario}/emissions_breakdown.csv",
        choropleth_pdf="results/{name}/plots/scen-{scenario}/emissions_choropleth.pdf",
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
        mem_mb=1200,
    log:
        "logs/{name}/plot_emissions_breakdown_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_emissions_breakdown_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_emissions_breakdown.py"


rule plot_consumption_balance:
    input:
        food_consumption="results/{name}/analysis/scen-{scenario}/food_consumption.csv",
        food_groups="data/curated/food_groups.csv",
        population="processing/{name}/population.csv",
        clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
    output:
        pdf="results/{name}/plots/scen-{scenario}/consumption_balance.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_consumption_balance_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_consumption_balance_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_consumption_balance.py"


rule plot_ghg_health_global:
    """Plot consumption-weighted global average GHG and YLL by food group."""
    input:
        ghg_intensity="results/{name}/analysis/scen-{scenario}/ghg_intensity.csv",
        health_marginals="results/{name}/analysis/scen-{scenario}/health_marginals.csv",
    output:
        ghg_pdf="results/{name}/plots/scen-{scenario}/marginal_ghg_global.pdf",
        yll_pdf="results/{name}/plots/scen-{scenario}/marginal_yll_global.pdf",
    params:
        group_colors=food_group_colors,
    group:
        "analysis_plot"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "logs/{name}/plot_ghg_health_global_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_ghg_health_global_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_ghg_health_global.py"


rule plot_luc_emissions:
    """Plot land use change emissions by country (bar) and resource class (map)."""
    input:
        network="results/{name}/solved/model_scen-{scenario}.nc",
        regions="processing/{name}/regions.geojson",
        resource_classes="processing/{name}/resource_classes.nc",
    output:
        bar_pdf="results/{name}/plots/scen-{scenario}/luc_emissions_bar.pdf",
        map_pdf="results/{name}/plots/scen-{scenario}/luc_emissions_map.pdf",
        csv="results/{name}/plots/scen-{scenario}/luc_emissions.csv",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_luc_emissions_scen-{scenario}.log",
    benchmark:
        "benchmarks/{name}/plot_luc_emissions_scen-{scenario}.tsv"
    script:
        "../scripts/plotting/plot_luc_emissions.py"


rule plot_pce_conditional_sensitivity:
    """Plot stacked conditional Sobol shares vs policy slice parameters."""
    input:
        conditional_indices="results/{name}/analysis/pce_conditional_indices_{prefix}.csv",
    output:
        value_per_yll_pdf="results/{name}/plots/pce_conditional_s1_vs_value_per_yll_{prefix}.pdf",
        ghg_price_pdf="results/{name}/plots/pce_conditional_s1_vs_ghg_price_{prefix}.pdf",
    params:
        metric="S1_cond",
    group:
        "analysis_plot"
    resources:
        runtime="2m",
        mem_mb=1000,
    log:
        "logs/{name}/plot_pce_conditional_sensitivity_{prefix}.log",
    benchmark:
        "benchmarks/{name}/plot_pce_conditional_sensitivity_{prefix}.tsv"
    script:
        "../scripts/plotting/plot_pce_conditional_sensitivity.py"
