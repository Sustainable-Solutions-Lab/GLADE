# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Geographic and spatial data preparation rules.

Includes population, administrative boundaries, regional aggregation,
and resource class computation.
"""


rule prepare_population:
    input:
        population_gz="data/downloads/WPP_population.csv.gz",
    params:
        planning_horizon=config["planning_horizon"],
        countries=config["countries"],
        baseline_year=config["baseline_year"],
    output:
        population="<processing>/{name}/population.csv",
        population_age="<processing>/{name}/population_age.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1800,
    log:
        "<logs>/{name}/prepare_population.log",
    benchmark:
        "<benchmarks>/{name}/prepare_population.tsv"
    script:
        "../scripts/prepare_population.py"


rule simplify_gadm:
    input:
        "data/downloads/gadm.gpkg",
    params:
        simplify_min_area_km=config["aggregation"]["simplify_min_area_km"],
        simplify_tolerance_km=config["aggregation"]["simplify_tolerance_km"],
    output:
        "<processing>/shared/gadm-simplified.gpkg",
    group:
        "prep"
    resources:
        runtime="3m",
        mem_mb=8500,
    log:
        "<logs>/shared/simplify_gadm.log",
    benchmark:
        "<benchmarks>/shared/simplify_gadm.tsv"
    script:
        "../scripts/simplify_gadm.py"


rule build_regions:
    input:
        world="<processing>/shared/gadm-simplified.gpkg",
        basins="data/downloads/aware2/AWARE20_Native_CFs_geospatial.gpkg",
    params:
        n_regions=config["aggregation"]["regions"]["target_count"],
        cluster_method=config["aggregation"]["regions"]["method"],
        basin_scarcity_weight=config["aggregation"]["regions"]["basin_scarcity_weight"],
        countries=config["countries"],
    output:
        "<processing>/{name}/regions.geojson",
    group:
        "prep"
    resources:
        runtime="15m",
        mem_mb=6000,
    log:
        "<logs>/{name}/build_regions.log",
    benchmark:
        "<benchmarks>/{name}/build_regions.tsv"
    script:
        "../scripts/build_regions.py"


_RESOURCE_CLASS_SCORE = config["aggregation"]["resource_class_score"]
_RESOURCE_CLASS_WATER_SUPPLIES = ["r", "i"]
_RESOURCE_CLASS_FDD_CROPS = set(config["fodder_decomposition"]["fdd_crops"]) & set(
    config["crops"]
)


def _resource_class_rasters(kind):
    return [
        gaez_path(kind, water_supply, crop)
        for water_supply in _RESOURCE_CLASS_WATER_SUPPLIES
        for crop in gaez_crops()
    ]


def _compute_resource_class_inputs(wildcards):
    yield_kind = (
        "actual_yield" if config["validation"]["use_actual_yields"] else "yield"
    )
    inputs = {
        "yields": _resource_class_rasters(yield_kind),
        "regions": f"<processing>/{wildcards.name}/regions.geojson",
        "yield_unit_conversions": "data/curated/yield_unit_conversions.csv",
        "moisture_content": "data/curated/crop_moisture_content.csv",
    }
    if _RESOURCE_CLASS_SCORE == "regional_crop_mix_actual_yield":
        inputs.update(
            {
                "harvested_area": _resource_class_rasters("harvested_area"),
                "crop_mapping": "data/curated/gaez_crop_code_mapping.csv",
                "faostat_production": f"<processing>/{wildcards.name}/faostat_crop_production.csv",
            }
        )
        if _RESOURCE_CLASS_FDD_CROPS:
            inputs["fdd_shares"] = f"<processing>/{wildcards.name}/fdd_area_shares.csv"
    return inputs


rule compute_resource_classes:
    input:
        unpack(_compute_resource_class_inputs),
    params:
        resource_class_quantiles=config["aggregation"]["resource_class_quantiles"],
        resource_class_score=_RESOURCE_CLASS_SCORE,
        use_actual_yields=config["validation"]["use_actual_yields"],
        crops=gaez_crops(),
        water_supplies=_RESOURCE_CLASS_WATER_SUPPLIES,
        non_food_crops=config["non_food_crops"],
    output:
        classes="<processing>/{name}/resource_classes.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=900,
    log:
        "<logs>/{name}/compute_resource_classes.log",
    benchmark:
        "<benchmarks>/{name}/compute_resource_classes.tsv"
    script:
        "../scripts/compute_resource_classes.py"


rule aggregate_class_areas:
    input:
        cell_mapping="<processing>/{name}/region_class_cell_mapping.npz",
        sr=[gaez_path("suitability", "r", crop) for crop in gaez_crops()],
        si=[gaez_path("suitability", "i", crop) for crop in gaez_crops()],
        irrigated_share="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
    params:
        irrigated_area_source=config["aggregation"]["irrigated_area_source"],
    output:
        "<processing>/{name}/land_area_by_class.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=800,
    log:
        "<logs>/{name}/aggregate_class_areas.log",
    benchmark:
        "<benchmarks>/{name}/aggregate_class_areas.tsv"
    script:
        "../scripts/aggregate_class_areas.py"
