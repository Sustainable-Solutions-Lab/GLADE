# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import yaml

shared_luc_dir = "processing/shared/luc"

# Use the default configuration (relative to the project root) to pick a canonical
# potential-yield raster for grid definition, keeping the shared grid invariant
# across scenario overrides.
_PROJECT_ROOT = Path(workflow.basedir).parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yaml"
with _DEFAULT_CONFIG_PATH.open(encoding="utf-8") as _cfg_file:
    _default_config = yaml.safe_load(_cfg_file)

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
    log:
        "logs/shared/build_luc_grid.log",
    script:
        "../scripts/build_luc_grid.py"


rule resample_land_cover:
    input:
        grid=rules.build_luc_grid.output.grid,
        land_cover="data/downloads/land_cover_lccs_class.nc",
    output:
        fractions=f"{shared_luc_dir}/land_cover_resampled.nc",
    log:
        "logs/shared/resample_land_cover.log",
    script:
        "../scripts/resample_land_cover.py"


rule resample_regrowth:
    input:
        grid=rules.build_luc_grid.output.grid,
        regrowth_raw="data/downloads/forest_carbon_accumulation_griscom_1km.tif",
    output:
        regrowth=f"{shared_luc_dir}/regrowth_resampled.nc",
    log:
        "logs/shared/resample_regrowth.log",
    script:
        "../scripts/resample_forest_carbon_accumulation.py"


rule prepare_luc_inputs:
    input:
        classes="processing/{name}/resource_classes.nc",
        land_cover=rules.resample_land_cover.output.fractions,
        regrowth=rules.resample_regrowth.output.regrowth,
        agb="data/downloads/esa_biomass_cci_v6_0.nc",
        soc="data/downloads/soilgrids_ocs_0-30cm_mean.tif",
    params:
        forest_fraction_threshold=config["luc"]["forest_fraction_threshold"],
    output:
        lc_masks="processing/{name}/luc/lc_masks.nc",
        agb="processing/{name}/luc/agb.nc",
        soc="processing/{name}/luc/soc.nc",
        regrowth="processing/{name}/luc/regrowth.nc",
    log:
        "logs/{name}/prepare_luc_inputs.log",
    script:
        "../scripts/prepare_luc_inputs.py"


rule build_current_grassland_area:
    input:
        classes="processing/{name}/resource_classes.nc",
        lc_masks=rules.prepare_luc_inputs.output.lc_masks,
        regions="processing/{name}/regions.geojson",
    output:
        current_area="processing/{name}/luc/current_grassland_area_by_class.csv",
    log:
        "logs/{name}/build_current_grassland_area.log",
    script:
        "../scripts/build_current_grassland_area.py"


rule build_current_cropland_area:
    input:
        classes="processing/{name}/resource_classes.nc",
        lc_masks=rules.prepare_luc_inputs.output.lc_masks,
        irrigated_share="data/downloads/gaez_land_equipped_for_irrigation_share.tif",
        regions="processing/{name}/regions.geojson",
    output:
        cropland_area="processing/{name}/cropland_baseline_by_class.csv",
    log:
        "logs/{name}/build_current_cropland_area.log",
    script:
        "../scripts/build_current_cropland_area.py"


rule build_grazing_only_land:
    input:
        classes="processing/{name}/resource_classes.nc",
        regions="processing/{name}/regions.geojson",
        lc_masks=rules.prepare_luc_inputs.output.lc_masks,
        suitability=[gaez_path("suitability", "r", crop) for crop in config["crops"]],
    output:
        grazing_area="processing/{name}/land_grazing_only_by_class.csv",
    log:
        "logs/{name}/build_grazing_only_land.log",
    script:
        "../scripts/build_grazing_only_land.py"


rule build_luc_carbon_coefficients:
    input:
        classes="processing/{name}/resource_classes.nc",
        regions="processing/{name}/regions.geojson",
        agb=rules.prepare_luc_inputs.output.agb,
        soc=rules.prepare_luc_inputs.output.soc,
        regrowth=rules.prepare_luc_inputs.output.regrowth,
        zone_parameters="data/luc_zone_parameters.csv",
    params:
        horizon_years=config["luc"]["horizon_years"],
        managed_flux_mode=config["luc"]["managed_flux_mode"],
        agb_threshold=config["luc"]["spared_land_agb_threshold_tc_per_ha"],
    output:
        pulses="processing/{name}/luc/pulses.nc",
        annualized="processing/{name}/luc/annualized.nc",
        coefficients="processing/{name}/luc/luc_carbon_coefficients.csv",
    log:
        "logs/{name}/build_luc_carbon_coefficients.log",
    script:
        "../scripts/build_luc_carbon_coefficients.py"
