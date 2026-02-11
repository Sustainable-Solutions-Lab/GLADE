# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import csv
from pathlib import Path

import yaml

shared_luc_dir = "<processing>/shared/luc"

# Use the default configuration (relative to the project root) to pick a canonical
# potential-yield raster for grid definition, keeping the shared grid invariant
# across scenario overrides.
_PROJECT_ROOT = Path(workflow.basedir).parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yaml"
with _DEFAULT_CONFIG_PATH.open(encoding="utf-8") as _cfg_file:
    _default_config = yaml.safe_load(_cfg_file)


def _get_unique_res06_rasters(crops: list[str]) -> dict[str, str]:
    """Get paths to unique GAEZ RES06-HAR rasters (one per res06_code × water supply).

    Returns a dict mapping "{res06_code}_{water}" to the raster path.
    """
    # Load the mapping to get RES06 codes
    mapping_path = _PROJECT_ROOT / "data" / "curated" / "gaez_crop_code_mapping.csv"
    with mapping_path.open(newline="") as f:
        crop_to_res06 = {
            row["crop_name"]: row["res06_code"].strip().upper()
            for row in csv.DictReader(f)
        }

    # Collect unique RES06 codes
    unique_res06_codes = set()
    for crop in crops:
        if crop in crop_to_res06:
            unique_res06_codes.add(crop_to_res06[crop])

    # Generate raster paths for each unique RES06 code and water supply
    # Use the first crop that maps to each code to get the raster path
    res06_to_crop = {}
    for crop in crops:
        if crop in crop_to_res06:
            res06_code = crop_to_res06[crop]
            if res06_code not in res06_to_crop:
                res06_to_crop[res06_code] = crop

    rasters = {}
    for res06_code, crop in res06_to_crop.items():
        for water in ["i", "r"]:
            key = f"{res06_code}_{water}"
            rasters[key] = f"data/downloads/gaez_harvested_area_{water}_{crop}.tif"

    return rasters


_first_crop = _default_config["crops"][0]
_default_gaez_cfg = _default_config["data"]["gaez"]
# Hardcoded: water supply is arbitrary for grid extraction (only resolution/extent matter)
_grid_yield_raster = (
    "data/downloads/gaez_yield"
    f"_{_default_gaez_cfg['climate_model']}"
    f"_{_default_gaez_cfg['period']}"
    f"_{_default_gaez_cfg['climate_scenario']}"
    f"_{_default_gaez_cfg['input_level']}"
    "_r"
    f"_{_first_crop}.tif"
)


# Provides the canonical model grid derived from a potential yield raster.
# The yield input is only used for grid resolution metadata.
rule build_luc_grid:
    input:
        yield_raster=_grid_yield_raster,
    output:
        grid=f"{shared_luc_dir}/grid.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "<logs>/shared/build_luc_grid.log",
    benchmark:
        "<benchmarks>/shared/build_luc_grid.tsv"
    script:
        "../scripts/build_luc_grid.py"


rule resample_luicube_grassland:
    input:
        grid=rules.build_luc_grid.output.grid,
        owl_area="data/downloads/luicube/GL-owl_area.tif",
        owl_hanpp="data/downloads/luicube/GL-owl_HANPPharv.tif",
        owl_nppeco="data/downloads/luicube/GL-owl_NPPeco.tif",
        notrees_area="data/downloads/luicube/GL-notrees_area.tif",
        notrees_hanpp="data/downloads/luicube/GL-notrees_HANPPharv.tif",
        notrees_nppeco="data/downloads/luicube/GL-notrees_NPPeco.tif",
    output:
        f"{shared_luc_dir}/luicube_grassland.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=4200,
    log:
        "<logs>/shared/resample_luicube_grassland.log",
    benchmark:
        "<benchmarks>/shared/resample_luicube_grassland.tsv"
    script:
        "../scripts/resample_luicube_grassland.py"


rule resample_land_cover:
    input:
        grid=rules.build_luc_grid.output.grid,
        land_cover="data/downloads/land_cover_lccs_class.nc",
    output:
        fractions=f"{shared_luc_dir}/land_cover_resampled.nc",
    group:
        "prep"
    resources:
        runtime="5m",
        mem_mb=700,
    log:
        "<logs>/shared/resample_land_cover.log",
    benchmark:
        "<benchmarks>/shared/resample_land_cover.tsv"
    script:
        "../scripts/resample_land_cover.py"


rule resample_regrowth:
    input:
        grid=rules.build_luc_grid.output.grid,
        regrowth_raw="data/downloads/forest_carbon_accumulation_griscom_1km.tif",
    output:
        regrowth=f"{shared_luc_dir}/regrowth_resampled.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1300,
    log:
        "<logs>/shared/resample_regrowth.log",
    benchmark:
        "<benchmarks>/shared/resample_regrowth.tsv"
    script:
        "../scripts/resample_forest_carbon_accumulation.py"


rule prepare_luc_inputs:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        land_cover=rules.resample_land_cover.output.fractions,
        regrowth=rules.resample_regrowth.output.regrowth,
        agb="data/downloads/esa_biomass_cci_v6_0.nc",
        soc="data/downloads/soilgrids_ocs_0-30cm_mean.tif",
    params:
        forest_fraction_threshold=config["luc"]["forest_fraction_threshold"],
    output:
        lc_masks="<processing>/{name}/luc/lc_masks.nc",
        agb="<processing>/{name}/luc/agb.nc",
        soc="<processing>/{name}/luc/soc.nc",
        regrowth="<processing>/{name}/luc/regrowth.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1200,
    log:
        "<logs>/{name}/prepare_luc_inputs.log",
    benchmark:
        "<benchmarks>/{name}/prepare_luc_inputs.tsv"
    script:
        "../scripts/prepare_luc_inputs.py"


rule build_current_grassland_area:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        luicube=rules.resample_luicube_grassland.output[0],
        regions="<processing>/{name}/regions.geojson",
    output:
        current_area="<processing>/{name}/luc/current_grassland_area_by_class.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=800,
    log:
        "<logs>/{name}/build_current_grassland_area.log",
    benchmark:
        "<benchmarks>/{name}/build_current_grassland_area.tsv"
    script:
        "../scripts/build_current_grassland_area.py"


def _gaez_cropland_baseline_inputs(_wildcards):
    """Return input dict for GAEZ-based cropland baseline rule."""
    return _get_unique_res06_rasters(config["crops"])


if config["luc"]["cropland_source"] == "gaez":

    # GAEZ-based cropland baseline: sum harvested area from RES06-HAR rasters
    rule build_current_cropland_area:
        input:
            unpack(_gaez_cropland_baseline_inputs),
            classes="<processing>/{name}/resource_classes.nc",
            regions="<processing>/{name}/regions.geojson",
            crop_mapping="data/curated/gaez_crop_code_mapping.csv",
            irrigated_share="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
        output:
            cropland_area="<processing>/{name}/cropland_baseline_by_class.csv",
        group:
            "prep"
        resources:
            runtime="1m",
            mem_mb=850,
        log:
            "<logs>/{name}/build_current_cropland_area.log",
        benchmark:
            "<benchmarks>/{name}/build_current_cropland_area.tsv"
        script:
            "../scripts/build_cropland_baseline_from_gaez.py"

else:

    # ESA-based cropland baseline: use satellite land cover classification
    rule build_current_cropland_area:
        input:
            classes="<processing>/{name}/resource_classes.nc",
            lc_masks=rules.prepare_luc_inputs.output.lc_masks,
            irrigated_share="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
            regions="<processing>/{name}/regions.geojson",
        output:
            cropland_area="<processing>/{name}/cropland_baseline_by_class.csv",
        group:
            "prep"
        resources:
            runtime="1m",
            mem_mb=850,
        log:
            "<logs>/{name}/build_current_cropland_area.log",
        benchmark:
            "<benchmarks>/{name}/build_current_cropland_area.tsv"
        script:
            "../scripts/build_current_cropland_area.py"


rule build_grazing_only_land:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
        lc_masks=rules.prepare_luc_inputs.output.lc_masks,
        luicube=rules.resample_luicube_grassland.output[0],
        suitability=[gaez_path("suitability", "r", crop) for crop in config["crops"]],
    output:
        grazing_area="<processing>/{name}/land_grazing_only_by_class.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1800,
    log:
        "<logs>/{name}/build_grazing_only_land.log",
    benchmark:
        "<benchmarks>/{name}/build_grazing_only_land.tsv"
    script:
        "../scripts/build_grazing_only_land.py"


rule build_luc_carbon_coefficients:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
        agb=rules.prepare_luc_inputs.output.agb,
        soc=rules.prepare_luc_inputs.output.soc,
        regrowth=rules.prepare_luc_inputs.output.regrowth,
        zone_parameters="data/curated/luc_zone_parameters.csv",
    params:
        horizon_years=config["luc"]["horizon_years"],
        managed_flux_mode=config["luc"]["managed_flux_mode"],
        agb_threshold=config["luc"]["spared_land_agb_threshold_tc_per_ha"],
    output:
        pulses="<processing>/{name}/luc/pulses.nc",
        annualized="<processing>/{name}/luc/annualized.nc",
        coefficients="<processing>/{name}/luc/luc_carbon_coefficients.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2400,
    log:
        "<logs>/{name}/build_luc_carbon_coefficients.log",
    benchmark:
        "<benchmarks>/{name}/build_luc_carbon_coefficients.tsv"
    script:
        "../scripts/build_luc_carbon_coefficients.py"
