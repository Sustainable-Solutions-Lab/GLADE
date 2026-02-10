# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Water and fertilizer-related data preparation rules.

Includes fertilizer application rates, blue water availability,
and regional water resource calculations.

Water supply scenario:
- "sustainable": Uses Water Footprint Network blue water availability (Hoekstra & Mekonnen 2011)
- "current_use": Uses Huang et al. (2018) gridded irrigation water withdrawals
"""


rule prepare_fertilizer_application_rates:
    input:
        fubc_data="data/bundled/doi_10_5061_dryad_2rbnzs7qh__v20250311/FUBC_1_to_9_data.csv",
        mapping="data/curated/ifa_fubc_crop_mapping.csv",
    output:
        "processing/{name}/fertilizer_application_rates.csv",
    resources:
        runtime=1,
        mem_mb=200,
    log:
        "logs/{name}/prepare_fertilizer_application_rates.log",
    benchmark:
        "benchmarks/{name}/prepare_fertilizer_application_rates.tsv"
    script:
        "../scripts/prepare_fertilizer_application_rates.py"


rule derive_global_fertilizer_rates:
    input:
        fertilizer_rates="processing/{name}/fertilizer_application_rates.csv",
    params:
        n_percentile=config["fertilizer"]["n_percentile"],
        crops=config["crops"],
    output:
        "processing/{name}/global_fertilizer_n_rates.csv",
    resources:
        runtime=1,
        mem_mb=200,
    log:
        "logs/{name}/derive_global_fertilizer_rates.log",
    benchmark:
        "benchmarks/{name}/derive_global_fertilizer_rates.tsv"
    script:
        "../scripts/derive_global_fertilizer_rates.py"


rule extract_waterfootprint_appendix:
    input:
        zip_path="data/downloads/Report53_Appendix.zip",
    output:
        shapefile="data/downloads/Report53_Appendix/Report53-BlueWaterScarcity-ArcGIS-ShapeFile/Monthly_WS_GRDC_405_basins.shp",
        excel="data/downloads/Report53_Appendix/Report53-Appendices-VI-IX.xls",
    resources:
        runtime=1,
        mem_mb=200,
    log:
        "logs/shared/extract_waterfootprint_appendix.log",
    benchmark:
        "benchmarks/shared/extract_waterfootprint_appendix.tsv"
    shell:
        r"""
        unzip -o {input.zip_path} -d data/downloads > {log} 2>&1
        """


rule process_blue_water_availability:
    input:
        shapefile=rules.extract_waterfootprint_appendix.output.shapefile,
        excel=rules.extract_waterfootprint_appendix.output.excel,
    output:
        "processing/{name}/water/blue_water_availability.csv",
    resources:
        runtime=1,
        mem_mb=200,
    log:
        "logs/{name}/process_blue_water_availability.log",
    benchmark:
        "benchmarks/{name}/process_blue_water_availability.tsv"
    script:
        "../scripts/process_blue_water_availability.py"


def crop_yield_file_list(w):
    return list(yield_inputs(w).values())


# Rule for sustainable water availability (Water Footprint Network data)
rule build_region_water_sustainable:
    input:
        shapefile=rules.extract_waterfootprint_appendix.output.shapefile,
        regions="processing/{name}/regions.geojson",
        monthly="processing/{name}/water/blue_water_availability.csv",
        crop_yields=crop_yield_file_list,
    output:
        monthly_region="processing/{name}/water/sustainable/monthly_region_water.csv",
        region_growing="processing/{name}/water/sustainable/region_growing_season_water.csv",
    resources:
        runtime=1,
        mem_mb=300,
    log:
        "logs/{name}/build_region_water_sustainable.log",
    benchmark:
        "benchmarks/{name}/build_region_water_sustainable.tsv"
    script:
        "../scripts/build_region_water_availability.py"


# Rule for current water use (Huang et al. 2018 gridded irrigation data)
rule build_region_water_current_use:
    input:
        nc="data/downloads/huang_irrigation_water.nc",
        regions="processing/{name}/regions.geojson",
        crop_yields=crop_yield_file_list,
    params:
        reference_year=config["water"]["huang_reference_year"],
    output:
        monthly_region="processing/{name}/water/current_use/monthly_region_water.csv",
        region_growing="processing/{name}/water/current_use/region_growing_season_water.csv",
    resources:
        runtime=1,
        mem_mb=400,
    log:
        "logs/{name}/build_region_water_current_use.log",
    benchmark:
        "benchmarks/{name}/build_region_water_current_use.tsv"
    script:
        "../scripts/process_huang_irrigation_water.py"


def water_monthly_input(w):
    """Select monthly water input based on config."""
    scenario = config["water"]["supply_scenario"]
    return f"processing/{w.name}/water/{scenario}/monthly_region_water.csv"


def water_growing_input(w):
    """Select growing season water input based on config."""
    scenario = config["water"]["supply_scenario"]
    return f"processing/{w.name}/water/{scenario}/region_growing_season_water.csv"


# Unified rule that creates symlinks to the selected water data source
# This maintains backward compatibility with existing downstream rules
rule select_water_scenario:
    input:
        monthly=water_monthly_input,
        growing=water_growing_input,
    output:
        monthly_region="processing/{name}/water/monthly_region_water.csv",
        region_growing="processing/{name}/water/region_growing_season_water.csv",
    resources:
        runtime=1,
        mem_mb=400,
    log:
        "logs/{name}/select_water_scenario.log",
    benchmark:
        "benchmarks/{name}/select_water_scenario.tsv"
    run:
        import shutil
        from pathlib import Path

        Path(output.monthly_region).parent.mkdir(parents=True, exist_ok=True)

        # Copy files (Snakemake handles symlinks poorly across rules)
        shutil.copy(input.monthly, output.monthly_region)
        shutil.copy(input.growing, output.region_growing)

        with open(log[0], "w") as f:
            f.write(f"Water supply scenario: {config['water']['supply_scenario']}\n")
            f.write(f"Copied {input.monthly} -> {output.monthly_region}\n")
            f.write(f"Copied {input.growing} -> {output.region_growing}\n")
