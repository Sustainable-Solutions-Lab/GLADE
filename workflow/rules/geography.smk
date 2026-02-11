# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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
        health_reference_year=config["health"]["reference_year"],
    output:
        population="processing/{name}/population.csv",
        population_age="processing/{name}/population_age.csv",
    resources:
        runtime="1m",
        mem_mb=1800,
    log:
        "logs/{name}/prepare_population.log",
    benchmark:
        "benchmarks/{name}/prepare_population.tsv"
    script:
        "../scripts/prepare_population.py"


rule simplify_gadm:
    input:
        "data/downloads/gadm.gpkg",
    params:
        simplify_min_area_km=config["aggregation"]["simplify_min_area_km"],
        simplify_tolerance_km=config["aggregation"]["simplify_tolerance_km"],
    output:
        "processing/shared/gadm-simplified.gpkg",
    resources:
        runtime="3m",
        mem_mb=8500,
    log:
        "logs/shared/simplify_gadm.log",
    benchmark:
        "benchmarks/shared/simplify_gadm.tsv"
    script:
        "../scripts/simplify_gadm.py"


rule build_regions:
    input:
        world="processing/shared/gadm-simplified.gpkg",
    params:
        n_regions=config["aggregation"]["regions"]["target_count"],
        allow_cross_border=config["aggregation"]["regions"]["allow_cross_border"],
        cluster_method=config["aggregation"]["regions"]["method"],
        countries=config["countries"],
    output:
        "processing/{name}/regions.geojson",
    resources:
        runtime="1m",
        mem_mb=400,
    log:
        "logs/{name}/build_regions.log",
    benchmark:
        "benchmarks/{name}/build_regions.tsv"
    script:
        "../scripts/build_regions.py"


rule compute_resource_classes:
    input:
        yields=(
            [gaez_path("yield", "r", crop) for crop in config["crops"]]
            + [gaez_path("yield", "i", crop) for crop in config["crops"]]
        ),
        regions="processing/{name}/regions.geojson",
    params:
        resource_class_quantiles=config["aggregation"]["resource_class_quantiles"],
    output:
        classes="processing/{name}/resource_classes.nc",
    resources:
        runtime="1m",
        mem_mb=1900,
    log:
        "logs/{name}/compute_resource_classes.log",
    benchmark:
        "benchmarks/{name}/compute_resource_classes.tsv"
    script:
        "../scripts/compute_resource_classes.py"


rule aggregate_class_areas:
    input:
        classes="processing/{name}/resource_classes.nc",
        sr=[gaez_path("suitability", "r", crop) for crop in config["crops"]],
        si=[gaez_path("suitability", "i", crop) for crop in config["crops"]],
        irrigated_share="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
        regions="processing/{name}/regions.geojson",
    params:
        irrigated_area_source=config["aggregation"]["irrigated_area_source"],
    output:
        "processing/{name}/land_area_by_class.csv",
    resources:
        runtime="1m",
        mem_mb=3000,
    log:
        "logs/{name}/aggregate_class_areas.log",
    benchmark:
        "benchmarks/{name}/aggregate_class_areas.tsv"
    script:
        "../scripts/aggregate_class_areas.py"
