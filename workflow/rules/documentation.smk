# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rules for generating documentation figures.

These figures are git-tracked and used in the Sphinx documentation.
They use a coarser resolution configuration for faster generation
and to showcase global coverage.
"""

from glob import glob

# Documentation figures are generated using the doc_figures config
DOC_FIG_NAME = "doc_figures"

# Validation figures use a separate config with top-level validation settings
# so that processing rules (which read config[...] directly) see them.
DOC_VAL_NAME = "doc_validation"

# Shared styling files tracked as inputs so Snakemake reruns figures when
# font sizes, colormaps, or other styling parameters change.
DOC_FIG_STYLE = [
    "workflow/scripts/doc_figures_config.py",
    "workflow/scripts/doc_figures_style.mplstyle",
]

# List of all documentation figures to generate
DOC_FIGURES = [
    # Introduction figures
    "intro_global_coverage",
    "model_topology",
    "land_flows",
    # Land use figures
    "land_resource_classes",
    "environment_luc_inputs",
    "environment_luc_lef",
    "grazing_only_land_fraction",
    # Crop production figures
    "crop_yield_wheat",
    "crop_yield_wetland-rice",
    "crop_yield_maize",
    "crop_yield_resource_class_wheat",
    "multi_cropping_potential_rainfed",
    "multi_cropping_potential_irrigated",
    # Water availability figures
    "water_basin_availability",
    "water_region_availability",
    "irrigated_land_fraction",
    # Livestock figures
    "grassland_yield",
    # Trade figures
    "trade_network",
    # Workflow figures
    "workflow_rulegraph",
    # Analysis figures
    "analysis_marginal_ghg",
    "analysis_marginal_yll",
    # Health figures
    "health_clusters",
    "health_burden",
    # Current diets figures
    "baseline_diet_by_region",
    "baseline_diet_by_food",
]

# Validation figures use the doc_validation config (separate from doc_figures)
DOC_VALIDATION_FIGURES = [
    "validation_crop_production",
    "validation_pasture",
    "validation_food_group_slack",
    "validation_slack_overview",
    "validation_feed_breakdown",
    "validation_grassland_calibration",
]


rule doc_fig_intro_global_coverage:
    """Generate global coverage map showing all modeled regions."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/intro_global_coverage.svg",
        png="docs/_static/figures/intro_global_coverage.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_intro_global_coverage.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_intro_global_coverage.tsv"
    script:
        "../scripts/doc_figures/intro_global_coverage.py"


rule doc_fig_model_topology:
    """Generate high-level model topology diagram showing material flows."""
    input:
        model=f"<results>/{DOC_FIG_NAME}/build/model.nc",
    output:
        svg="docs/_static/figures/model_topology.svg",
        png="docs/_static/figures/model_topology.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_model_topology.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_model_topology.tsv"
    script:
        "../scripts/visualize_model_topology.py"


rule doc_fig_land_flows:
    """Generate land flow diagram showing cropland and pasture pool structure."""
    input:
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/land_flows.svg",
        png="docs/_static/figures/land_flows.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_land_flows.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_land_flows.tsv"
    script:
        "../scripts/doc_figures/land_flows.py"


rule doc_fig_land_resource_classes:
    """Generate map showing resource class stratification."""
    input:
        classes=f"<processing>/{DOC_FIG_NAME}/resource_classes.nc",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/land_resource_classes.svg",
        png="docs/_static/figures/land_resource_classes.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_land_resource_classes.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_land_resource_classes.tsv"
    script:
        "../scripts/doc_figures/land_resource_classes.py"


rule doc_fig_environment_luc_inputs:
    """Visualise LUC carbon input datasets used in the model."""
    input:
        lc_masks=f"<processing>/{DOC_FIG_NAME}/luc/lc_masks.nc",
        agb=f"<processing>/{DOC_FIG_NAME}/luc/agb.nc",
        soc=f"<processing>/{DOC_FIG_NAME}/luc/soc.nc",
        regrowth="<processing>/shared/luc/regrowth_resampled.nc",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/environment_luc_inputs.svg",
        png="docs/_static/figures/environment_luc_inputs.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_environment_luc_inputs.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_environment_luc_inputs.tsv"
    script:
        "../scripts/doc_figures/luc_inputs_map.py"


rule doc_fig_environment_luc_lef:
    """Visualise aggregated land-use change emission factors."""
    input:
        annualized=f"<processing>/{DOC_FIG_NAME}/luc/annualized.nc",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/environment_luc_lef.svg",
        png="docs/_static/figures/environment_luc_lef.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_environment_luc_lef.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_environment_luc_lef.tsv"
    script:
        "../scripts/doc_figures/luc_lef_map.py"


rule doc_fig_crop_yield:
    """Generate crop yield potential maps for selected crops."""
    input:
        yield_raster=lambda w: gaez_path("yield", "r", w.crop),
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        conversions="data/curated/yield_unit_conversions.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/crop_yield_{crop}.svg",
        png="docs/_static/figures/crop_yield_{crop}.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_crop_yield_{crop}.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_crop_yield_{crop}.tsv"
    script:
        "../scripts/doc_figures/crop_yield_map.py"


rule doc_fig_crop_yield_resource_class:
    """Generate resource class yield comparison maps."""
    input:
        crop_yields=f"<processing>/{DOC_FIG_NAME}/crop_yields/{{crop}}_r.csv",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/crop_yield_resource_class_{crop}.svg",
        png="docs/_static/figures/crop_yield_resource_class_{crop}.png",
    params:
        resource_class_1=1,
        resource_class_2=2,
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_crop_yield_resource_class_{crop}.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_crop_yield_resource_class_{crop}.tsv"
    script:
        "../scripts/doc_figures/crop_yield_resource_class.py"


rule doc_fig_multi_cropping_potential_rainfed:
    """Visualise rain-fed multi-cropping zones and regional potential."""
    input:
        zone_raster=lambda w: gaez_path("multiple_cropping_zone", "r", "all"),
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/multi_cropping_potential_rainfed.svg",
        png="docs/_static/figures/multi_cropping_potential_rainfed.png",
    params:
        water_supply="rainfed",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_multi_cropping_potential_rainfed.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_multi_cropping_potential_rainfed.tsv"
    script:
        "../scripts/doc_figures/multi_cropping_potential.py"


rule doc_fig_multi_cropping_potential_irrigated:
    """Visualise irrigated multi-cropping zones and regional potential."""
    input:
        zone_raster=lambda w: gaez_path("multiple_cropping_zone", "i", "all"),
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/multi_cropping_potential_irrigated.svg",
        png="docs/_static/figures/multi_cropping_potential_irrigated.png",
    params:
        water_supply="irrigated",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_multi_cropping_potential_irrigated.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_multi_cropping_potential_irrigated.tsv"
    script:
        "../scripts/doc_figures/multi_cropping_potential.py"


rule doc_fig_water_basin_availability:
    """Generate basin water availability map."""
    input:
        basin_shapefile="data/downloads/Report53_Appendix/Report53-BlueWaterScarcity-ArcGIS-ShapeFile/Monthly_WS_GRDC_405_basins.shp",
        water_data=f"<processing>/{DOC_FIG_NAME}/water/blue_water_availability.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/water_basin_availability.svg",
        png="docs/_static/figures/water_basin_availability.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_water_basin_availability.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_water_basin_availability.tsv"
    script:
        "../scripts/doc_figures/water_basin_availability.py"


rule doc_fig_water_region_availability:
    """Generate regional water availability map."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        water_data=f"<processing>/{DOC_FIG_NAME}/water/region_growing_season_water.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/water_region_availability.svg",
        png="docs/_static/figures/water_region_availability.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_water_region_availability.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_water_region_availability.tsv"
    script:
        "../scripts/doc_figures/water_region_availability.py"


rule doc_fig_irrigated_land_fraction:
    """Generate irrigated land fraction map."""
    input:
        irrigated_fraction="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/irrigated_land_fraction.svg",
        png="docs/_static/figures/irrigated_land_fraction.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_irrigated_land_fraction.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_irrigated_land_fraction.tsv"
    script:
        "../scripts/doc_figures/irrigated_land_fraction.py"


rule doc_fig_grassland_yield:
    """Generate managed grassland yield map."""
    input:
        grassland_yield="data/downloads/grassland_yield_historical.nc4",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/grassland_yield.svg",
        png="docs/_static/figures/grassland_yield.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_grassland_yield.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_grassland_yield.tsv"
    script:
        "../scripts/doc_figures/grassland_yield_map.py"


rule doc_fig_grazing_only_land_fraction:
    """Visualise grazing-only land availability."""
    input:
        classes=f"<processing>/{DOC_FIG_NAME}/resource_classes.nc",
        lc_masks=f"<processing>/{DOC_FIG_NAME}/luc/lc_masks.nc",
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        suitability=[gaez_path("suitability", "r", crop) for crop in config["crops"]],
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/grazing_only_land_fraction.svg",
        png="docs/_static/figures/grazing_only_land_fraction.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_grazing_only_land_fraction.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_grazing_only_land_fraction.tsv"
    script:
        "../scripts/doc_figures/grazing_only_land_fraction.py"


rule doc_fig_trade_network:
    """Generate trade network map showing hubs and links."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/trade_network.svg",
        png="docs/_static/figures/trade_network.png",
    params:
        n_hubs=config["trade"]["hubs"],
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_trade_network.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_trade_network.tsv"
    script:
        "../scripts/doc_figures/trade_network_map.py"


rule doc_fig_workflow_rulegraph_dot:
    """Generate workflow dependency graph in DOT format from Snakemake."""
    output:
        dot="docs/_static/figures/workflow_rulegraph_raw.dot",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_workflow_rulegraph_dot.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_workflow_rulegraph_dot.tsv"
    shell:
        """
        snakemake --rulegraph --config name=test > {output.dot} 2> {log}
        """


rule doc_fig_workflow_rulegraph_styled:
    """Apply custom styling and text wrapping to DOT graph."""
    input:
        dot="docs/_static/figures/workflow_rulegraph_raw.dot",
    output:
        dot="docs/_static/figures/workflow_rulegraph.dot",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_workflow_rulegraph_styled.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_workflow_rulegraph_styled.tsv"
    script:
        "../scripts/doc_figures/style_workflow_graph.py"


rule doc_fig_workflow_rulegraph:
    """Render workflow dependency graph to SVG and PNG using Graphviz."""
    input:
        dot="docs/_static/figures/workflow_rulegraph.dot",
    output:
        svg="docs/_static/figures/workflow_rulegraph.svg",
        png="docs/_static/figures/workflow_rulegraph.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_workflow_rulegraph.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_workflow_rulegraph.tsv"
    script:
        "../scripts/doc_figures/render_graph.py"


rule doc_fig_analysis_ghg_health:
    """Generate GHG and health impact bar charts for documentation."""
    input:
        ghg_intensity=f"<results>/{DOC_FIG_NAME}/analysis/scen-default/ghg_attribution.csv",
        health_marginals=f"<results>/{DOC_FIG_NAME}/analysis/scen-default/health_marginals.csv",
        style=DOC_FIG_STYLE,
    output:
        ghg_svg="docs/_static/figures/analysis_marginal_ghg.svg",
        ghg_png="docs/_static/figures/analysis_marginal_ghg.png",
        yll_svg="docs/_static/figures/analysis_marginal_yll.svg",
        yll_png="docs/_static/figures/analysis_marginal_yll.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_analysis_ghg_health.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_analysis_ghg_health.tsv"
    script:
        "../scripts/doc_figures/analysis_ghg_health.py"


rule doc_fig_health_clusters:
    """Generate health cluster map showing country groupings."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        clusters=f"<processing>/{DOC_FIG_NAME}/health/country_clusters.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/health_clusters.svg",
        png="docs/_static/figures/health_clusters.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_health_clusters.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_health_clusters.tsv"
    script:
        "../scripts/doc_figures/health_clusters_map.py"


rule doc_fig_health_burden:
    """Generate choropleth of baseline diet-attributable disease burden."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        clusters=f"<processing>/{DOC_FIG_NAME}/health/country_clusters.csv",
        cluster_cause_baseline=f"<processing>/{DOC_FIG_NAME}/health/cluster_cause_baseline.csv",
        cluster_summary=f"<processing>/{DOC_FIG_NAME}/health/cluster_summary.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/health_burden.svg",
        png="docs/_static/figures/health_burden.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_health_burden.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_health_burden.tsv"
    script:
        "../scripts/doc_figures/health_burden_map.py"


rule doc_fig_baseline_diet_by_region:
    """Generate baseline diet by-region stacked bar chart."""
    input:
        baseline_diet=f"<processing>/{DOC_FIG_NAME}/baseline_diet.csv",
        population=f"<processing>/{DOC_FIG_NAME}/population.csv",
        m49_codes="data/curated/M49-codes.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/baseline_diet_by_region.svg",
        png="docs/_static/figures/baseline_diet_by_region.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_baseline_diet_by_region.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_baseline_diet_by_region.tsv"
    script:
        "../scripts/doc_figures/baseline_diet_by_region.py"


rule doc_fig_baseline_diet_by_food:
    """Generate baseline diet by-food stacked bar chart."""
    input:
        baseline_diet=f"<processing>/{DOC_FIG_NAME}/baseline_diet.csv",
        population=f"<processing>/{DOC_FIG_NAME}/population.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/baseline_diet_by_food.svg",
        png="docs/_static/figures/baseline_diet_by_food.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_baseline_diet_by_food.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_baseline_diet_by_food.tsv"
    script:
        "../scripts/doc_figures/baseline_diet_by_food.py"


# --- Validation figures ---


rule doc_fig_validation_crop_production:
    """Generate validation crop production map (excluding pasture)."""
    input:
        land_use=f"<results>/{DOC_VAL_NAME}/analysis/scen-default/land_use.csv",
        regions=f"<processing>/{DOC_VAL_NAME}/regions.geojson",
        resource_classes=f"<processing>/{DOC_VAL_NAME}/resource_classes.nc",
        land_area_by_class=f"<processing>/{DOC_VAL_NAME}/land_area_by_class.csv",
        land_grazing_only=f"<processing>/{DOC_VAL_NAME}/land_grazing_only_by_class.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_crop_production.svg",
        png="docs/_static/figures/validation_crop_production.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_crop_production.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_crop_production.tsv"
    script:
        "../scripts/doc_figures/validation_crop_production_map.py"


rule doc_fig_validation_pasture:
    """Generate validation pasture/grassland intensity map."""
    input:
        land_use=f"<results>/{DOC_VAL_NAME}/analysis/scen-default/land_use.csv",
        regions=f"<processing>/{DOC_VAL_NAME}/regions.geojson",
        resource_classes=f"<processing>/{DOC_VAL_NAME}/resource_classes.nc",
        land_area_by_class=f"<processing>/{DOC_VAL_NAME}/land_area_by_class.csv",
        land_grazing_only=f"<processing>/{DOC_VAL_NAME}/land_grazing_only_by_class.csv",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_pasture.svg",
        png="docs/_static/figures/validation_pasture.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_pasture.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_pasture.tsv"
    script:
        "../scripts/doc_figures/validation_pasture_map.py"


rule doc_fig_validation_food_group_slack:
    """Generate two-panel food group slack figure for validation."""
    input:
        network=f"<results>/{DOC_VAL_NAME}/solved/model_scen-default.nc",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_food_group_slack.svg",
        png="docs/_static/figures/validation_food_group_slack.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_food_group_slack.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_food_group_slack.tsv"
    script:
        "../scripts/doc_figures/validation_food_group_slack.py"


rule doc_fig_validation_slack_overview:
    """Generate overall slack overview figure for validation."""
    input:
        network=f"<results>/{DOC_VAL_NAME}/solved/model_scen-default.nc",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_slack_overview.svg",
        png="docs/_static/figures/validation_slack_overview.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_slack_overview.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_slack_overview.tsv"
    script:
        "../scripts/doc_figures/validation_slack_overview.py"


rule doc_fig_validation_feed_breakdown:
    """Generate feed breakdown figure for validation."""
    input:
        network=f"<results>/{DOC_VAL_NAME}/solved/model_scen-default.nc",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_feed_breakdown.svg",
        png="docs/_static/figures/validation_feed_breakdown.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_feed_breakdown.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_feed_breakdown.tsv"
    script:
        "../scripts/doc_figures/validation_feed_breakdown.py"


rule doc_fig_validation_grassland_calibration:
    """Generate grassland forage calibration choropleth map."""
    input:
        calibration=config["grazing"]["grassland_forage_calibration"][
            "grassland_yield_correction"
        ],
        exogenous_forage=config["grazing"]["grassland_forage_calibration"][
            "exogenous_forage"
        ],
        regions=f"<processing>/{DOC_VAL_NAME}/regions.geojson",
        style=DOC_FIG_STYLE,
    output:
        svg="docs/_static/figures/validation_grassland_calibration.svg",
        png="docs/_static/figures/validation_grassland_calibration.png",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_validation_grassland_calibration.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_validation_grassland_calibration.tsv"
    script:
        "../scripts/doc_figures/validation_grassland_calibration.py"


# --- Trade friction production pattern GIF ---

TRADE_SCENARIOS = ["free_trade", "default_trade", "costly_trade", "autarky"]

TRADE_SCENARIO_LABELS = {
    "free_trade": "Free trade (0.25\u00d7 trade costs)",
    "default_trade": "Baseline trade (1\u00d7 trade costs)",
    "costly_trade": "Costly trade (4\u00d7 trade costs)",
    "autarky": "Near-autarky (100\u00d7 trade costs)",
}

# Fixed bar-chart x-axis scale (Mha) shared across all frames for comparability.
PRODUCTION_PATTERN_BAR_XMAX = 400


rule doc_fig_production_pattern_frame:
    """Generate one production-pattern PNG frame for a trade scenario."""
    input:
        regions=f"<processing>/{DOC_FIG_NAME}/regions.geojson",
        resource_classes=f"<processing>/{DOC_FIG_NAME}/resource_classes.nc",
        land_area_by_class=f"<processing>/{DOC_FIG_NAME}/land_area_by_class.csv",
        land_grazing_only=f"<processing>/{DOC_FIG_NAME}/land_grazing_only_by_class.csv",
        land_use=f"<results>/{DOC_FIG_NAME}/analysis/scen-{{trade_scenario}}/land_use.csv",
        style=DOC_FIG_STYLE,
    output:
        png="docs/_static/figures/production_pattern_{trade_scenario}.png",
    params:
        frame_label=lambda w: TRADE_SCENARIO_LABELS[w.trade_scenario],
        bar_xmax_mha=PRODUCTION_PATTERN_BAR_XMAX,
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_production_pattern_{trade_scenario}.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_production_pattern_{trade_scenario}.tsv"
    script:
        "../scripts/doc_figures/production_pattern.py"


rule doc_fig_production_pattern_gif:
    """Collate trade-friction frames into an animated GIF."""
    input:
        frames=expand(
            "docs/_static/figures/production_pattern_{ts}.png",
            ts=TRADE_SCENARIOS,
        ),
    output:
        gif="docs/_static/figures/production_pattern.gif",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/doc_fig_production_pattern_gif.log",
    benchmark:
        "<benchmarks>/shared/doc_fig_production_pattern_gif.tsv"
    script:
        "../scripts/doc_figures/collate_production_gif.py"


rule build_docs:
    """Build Sphinx documentation including all figures."""
    input:
        # Figures
        expand("docs/_static/figures/{fig}.svg", fig=DOC_FIGURES),
        expand("docs/_static/figures/{fig}.png", fig=DOC_FIGURES),
        # NOTE: Validation figures are NOT listed here. They are generated
        # separately via tools/build-docs using the doc_validation config.
        # Production pattern GIF (landing page)
        "docs/_static/figures/production_pattern.gif",
        expand(
            "docs/_static/figures/production_pattern_{ts}.png",
            ts=TRADE_SCENARIOS,
        ),
        # Documentation source files
        "docs/conf.py",
        glob("docs/**/*.rst", recursive=True),
    output:
        "docs/_build/html/index.html",
    group:
        "analysis_plot"
    resources:
        runtime="10m",
        mem_mb=2000,
    log:
        "<logs>/shared/build_docs.log",
    benchmark:
        "<benchmarks>/shared/build_docs.tsv"
    shell:
        """
        cd docs && make html > ../{log} 2>&1
        """
