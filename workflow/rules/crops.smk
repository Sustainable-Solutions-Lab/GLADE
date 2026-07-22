# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Crop-related data preparation rules.

Includes crop yields, harvested areas, multi-cropping, grassland yields,
and crop residue processing.
"""


rule prepare_faostat_crop_costs:
    input:
        pp_parquet="data/downloads/faostat/PP.parquet",
        qcl_parquet="data/downloads/faostat/QCL.parquet",
        mapping="data/curated/faostat_crop_item_map.csv",
        m49_codes="data/curated/M49-codes.csv",
        cpi="<processing>/shared/cpi_annual.csv",
        proxies="data/curated/faostat_cost_proxies.yaml",
    params:
        countries=config["countries"],
        crops=config["crops"],
        currency_base_year=config["currency_base_year"],
        averaging_period=config["costs"]["averaging_period"],
        non_endogenous_cost_share=config["crop_costs"]["non_endogenous_cost_share"],
        outlier_cap_quantile=config["crop_costs"]["outlier_cap_quantile"],
        price_element_code=config["crop_costs"]["faostat"]["price_element_code"],
        yield_element_code=config["crop_costs"]["faostat"]["yield_element_code"],
    output:
        "<processing>/{name}/faostat_crop_costs.csv",
    group:
        "prep"
    resources:
        runtime="2m",
        mem_mb=5000,
    log:
        "<logs>/{name}/prepare_faostat_crop_costs.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_crop_costs.tsv"
    script:
        "../scripts/prepare_faostat_crop_costs.py"


rule prepare_faostat_crop_production:
    input:
        mapping="data/curated/faostat_crop_item_map.csv",
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
        banana_plantain_override="<processing>/{name}/banana_plantain_production.csv",
    params:
        countries=config["countries"],
        production_year=config["baseline_year"],
        qcl_element_code=config["data"]["faostat"]["qcl_production_element_code"],
    output:
        "<processing>/{name}/faostat_crop_production.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/prepare_faostat_crop_production.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_crop_production.tsv"
    script:
        "../scripts/prepare_faostat_crop_production.py"


rule prepare_banana_plantain_production:
    """Per-country banana/plantain production split from FAOSTAT FBS.

    FAOSTAT QCL inconsistently classifies cooking bananas: several large
    plantain producers (Nigeria, Burundi, Rwanda, ...) report all output
    under "Bananas". The FBS dataset performs its own per-country
    reconciliation between items 2615 (Bananas) and 2616 (Plantains),
    which is more aligned with dietary reality. This rule writes a
    production override file consumed by ``prepare_faostat_crop_production``.
    """
    input:
        fbs_csv="data/downloads/faostat/FBS.parquet",
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        production_year=config["baseline_year"],
        qcl_production_element_code=config["data"]["faostat"][
            "qcl_production_element_code"
        ],
        fbs_production_element_code=config["data"]["faostat"][
            "fbs_production_element_code"
        ],
    output:
        "<processing>/{name}/banana_plantain_production.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/prepare_banana_plantain_production.log",
    benchmark:
        "<benchmarks>/{name}/prepare_banana_plantain_production.tsv"
    script:
        "../scripts/prepare_banana_plantain_production.py"


rule prepare_fao_edible_portion:
    input:
        table="data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx",
        mapping="data/curated/faostat_crop_item_map.csv",
    params:
        crops=config["crops"],
    output:
        edible_portion="<processing>/{name}/fao_edible_portion.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/prepare_fao_edible_portion.log",
    benchmark:
        "<benchmarks>/{name}/prepare_fao_edible_portion.tsv"
    script:
        "../scripts/prepare_fao_edible_portion.py"


def yield_and_suitability_for_crop(w):
    """Get input files for build_crop_yields rule.

    w.crop is the crop name (e.g., 'wheat')
    w.water_supply is 'i' or 'r'
    """
    crop = w.crop
    ws = w.water_supply
    yield_kind = (
        "actual_yield" if config["validation"]["use_actual_yields"] else "yield"
    )

    inputs = {
        "yield_raster": gaez_path(yield_kind, ws, crop),
        "suitability_raster": gaez_path("suitability", ws, crop),
        "growing_season_start_raster": gaez_path("growing_season_start", ws, crop),
        "growing_season_length_raster": gaez_path("growing_season_length", ws, crop),
    }
    if ws == "i":
        inputs["water_requirement_raster"] = gaez_path("water_requirement", ws, crop)
    return inputs


rule build_region_class_cell_mapping:
    input:
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        mapping="<processing>/{name}/region_class_cell_mapping.npz",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=600,
    log:
        "<logs>/{name}/build_region_class_cell_mapping.log",
    benchmark:
        "<benchmarks>/{name}/build_region_class_cell_mapping.tsv"
    script:
        "../scripts/build_region_class_cell_mapping.py"


rule build_crop_yields:
    input:
        unpack(yield_and_suitability_for_crop),
        cell_mapping="<processing>/{name}/region_class_cell_mapping.npz",
        yield_unit_conversions="data/curated/yield_unit_conversions.csv",
        moisture_content="data/curated/crop_moisture_content.csv",
    params:
        use_actual_yields=config["validation"]["use_actual_yields"],
    output:
        "<processing>/{name}/crop_yields/{crop}_{water_supply}.csv",
    # Crops sourced from CROPGRIDS are routed to build_crop_yields_cropgrids
    # below; this rule only matches GAEZ-backed crops.
    wildcard_constraints:
        crop="|".join(gaez_crops()) if gaez_crops() else "__never__",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=700,
    log:
        "<logs>/{name}/build_crop_yields_{crop}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/build_crop_yields_{crop}_{water_supply}.tsv"
    script:
        "../scripts/build_crop_yields.py"


rule build_crop_yields_cropgrids:
    """Build yield and harvested-area CSVs for cropgrids-backed crops.

    Produces both ``crop_yields/{crop}_r.csv`` and
    ``harvested_area/gaez/{crop}_r.csv`` in one go, matching the schema of
    the GAEZ pipeline. Rainfed only.
    """
    input:
        cropgrids_nc="<processing>/shared/cropgrids_nc/CROPGRIDSv1.08_{crop}.nc",
        cropgrids_mapping="data/curated/cropgrids_crop_mapping.csv",
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
        moisture_content="data/curated/crop_moisture_content.csv",
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
        # CROPGRIDS-backed fruits absorb a share of the non-modelled FRT
        # residual via build_frt_area_attribution (distributed proportional
        # to the crop's CROPGRIDS density).
        frt_attribution="<processing>/{name}/frt_area_attribution.csv",
    params:
        countries=config["countries"],
        averaging_period=config["costs"]["averaging_period"],
        suitable_area_expansion=config["cropgrids"]["suitable_area_expansion"],
    output:
        crop_yields="<processing>/{name}/crop_yields/{crop}_r.csv",
        harvested_area="<processing>/{name}/harvested_area/gaez/{crop}_r.csv",
    wildcard_constraints:
        crop=(
            "|".join(config["cropgrids_crops"])
            if config.get("cropgrids_crops")
            else "__never__"
        ),
    group:
        "prep"
    resources:
        runtime="5m",
        mem_mb=3500,
    log:
        "<logs>/{name}/build_crop_yields_cropgrids_{crop}.log",
    benchmark:
        "<benchmarks>/{name}/build_crop_yields_cropgrids_{crop}.tsv"
    script:
        "../scripts/build_crop_yields_cropgrids.py"


_fdd_crops_in_config = set(config["fodder_decomposition"]["fdd_crops"]) & set(
    config["crops"]
)


rule build_ooc_olive_area_share:
    input:
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        baseline_year=config["baseline_year"],
    output:
        "<processing>/{name}/ooc_olive_area_share.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/build_ooc_olive_area_share.log",
    benchmark:
        "<benchmarks>/{name}/build_ooc_olive_area_share.tsv"
    script:
        "../scripts/build_ooc_olive_area_share.py"


rule build_frt_area_attribution:
    """Per-(country, modelled-fruit) target harvested area for the FRT pool.

    Combines FAOSTAT direct area for each modelled FRT-pool fruit (citrus,
    mango, watermelon, apple) with a projection of the non-modelled FRT
    residual (pears, peaches, plums, pineapples, papayas, kiwi, ...) onto
    all 5 modelled fruits (banana included) by per-country FAOSTAT-area
    share. Replaces build_frt_kept_area_share, which only deflated the
    trio's area scalar and could not enforce agroecological consistency
    at the cell level.
    """
    input:
        qcl_csv="data/downloads/faostat/QCL.parquet",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        baseline_year=config["baseline_year"],
    output:
        "<processing>/{name}/frt_area_attribution.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/build_frt_area_attribution.log",
    benchmark:
        "<benchmarks>/{name}/build_frt_area_attribution.tsv"
    script:
        "../scripts/build_frt_area_attribution.py"


rule build_fdd_area_shares:
    input:
        eurostat_fodder="data/downloads/eurostat_fodder_production.csv",
        regions="<processing>/{name}/regions.geojson",
        yield_alfalfa=lambda w: gaez_path("yield", "r", "alfalfa"),
        yield_silage_maize=lambda w: gaez_path("yield", "r", "silage-maize"),
    params:
        fdd_crops=config["fodder_decomposition"]["fdd_crops"],
        suitability_blend_weight=config["fodder_decomposition"][
            "suitability_blend_weight"
        ],
    output:
        "<processing>/{name}/fdd_area_shares.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1300,
    log:
        "<logs>/{name}/build_fdd_area_shares.log",
    benchmark:
        "<benchmarks>/{name}/build_fdd_area_shares.tsv"
    script:
        "../scripts/build_fdd_area_shares.py"


def _fodder_yield_correction_inputs(_wildcards):
    """Get GAEZ yield and area files for all FDD crops (both water supplies)."""
    irrigated = set(irrigated_crops())

    inputs = {"regions": "<processing>/{name}/regions.geojson"}
    for crop in config["fodder_decomposition"]["fdd_crops"]:
        if crop not in config["crops"]:
            continue
        inputs[f"gaez_yield_{crop}_r"] = (
            f"<processing>/{{name}}/crop_yields/{crop}_r.csv"
        )
        inputs[f"gaez_harvested_{crop}_r"] = (
            f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"
        )
        if crop in irrigated:
            inputs[f"gaez_yield_{crop}_i"] = (
                f"<processing>/{{name}}/crop_yields/{crop}_i.csv"
            )
            inputs[f"gaez_harvested_{crop}_i"] = (
                f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
            )
    return inputs


rule build_fodder_yield_corrections:
    input:
        unpack(_fodder_yield_correction_inputs),
        eurostat_fodder="data/downloads/eurostat_fodder_production.csv",
    params:
        fdd_crops=config["fodder_decomposition"]["fdd_crops"],
        eurostat_moisture=config["fodder_decomposition"]["yield_corrections"][
            "eurostat_moisture"
        ],
        floor=config["fodder_decomposition"]["yield_corrections"]["floor"],
        ceiling=config["fodder_decomposition"]["yield_corrections"]["ceiling"],
    output:
        "<processing>/{name}/fodder_yield_corrections.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=300,
    log:
        "<logs>/{name}/build_fodder_yield_corrections.log",
    benchmark:
        "<benchmarks>/{name}/build_fodder_yield_corrections.tsv"
    script:
        "../scripts/build_fodder_yield_corrections.py"


def _yield_calibration_inputs(_wildcards):
    """GAEZ yield + harvested-area inputs for each calibrated crop."""
    irrigated = set(irrigated_crops())
    inputs = {
        "regions": "<processing>/{name}/regions.geojson",
        "faostat_production": "<processing>/{name}/faostat_crop_production.csv",
    }
    for crop in config["yield_calibration"]["crops"]:
        if crop not in config["crops"]:
            continue
        inputs[f"gaez_yield_{crop}_r"] = (
            f"<processing>/{{name}}/crop_yields/{crop}_r.csv"
        )
        inputs[f"gaez_harvested_{crop}_r"] = (
            f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"
        )
        if crop in irrigated:
            inputs[f"gaez_yield_{crop}_i"] = (
                f"<processing>/{{name}}/crop_yields/{crop}_i.csv"
            )
            inputs[f"gaez_harvested_{crop}_i"] = (
                f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
            )
    return inputs


def _yield_calibration_moisture(_wildcards):
    """Per-crop moisture fractions needed to convert GAEZ DM yields to fresh."""
    import pandas as pd

    moisture = pd.read_csv(
        "data/curated/crop_moisture_content.csv", comment="#"
    ).set_index("crop")["moisture_fraction"]
    return {
        crop: float(moisture.loc[crop]) for crop in config["yield_calibration"]["crops"]
    }


rule build_yield_calibration:
    """Per-(country, crop) yield calibration anchored on FBS-corrected FAOSTAT
    production for crops listed in config[yield_calibration].crops. Output
    schema matches fodder_yield_corrections.csv and is applied through the
    same per-cell yield-rescaling path in build_model.py, only when
    validation.use_actual_yields=true. Used for crops where GAEZ relies on
    a proxy raster (e.g. plantain via banana)."""
    input:
        unpack(_yield_calibration_inputs),
    params:
        crops=config["yield_calibration"]["crops"],
        multiplier_min=config["yield_calibration"]["multiplier_min"],
        multiplier_max=config["yield_calibration"]["multiplier_max"],
        moisture_by_crop=_yield_calibration_moisture,
    output:
        "<processing>/{name}/yield_calibration.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=300,
    log:
        "<logs>/{name}/build_yield_calibration.log",
    benchmark:
        "<benchmarks>/{name}/build_yield_calibration.tsv"
    script:
        "../scripts/build_yield_calibration.py"


# Modelled fruits whose harvested area is sourced via FAOSTAT-attribution +
# yield-weighted cell distribution (instead of GAEZ-FRT-raster scaling).
# Apple goes via CROPGRIDS; banana absorbs FRT residual on top of its BAN
# raster output. These four constants are mirrored on the demand side by
# FRUITS_FRT_PROJECTION_FOODS in diet/food_group_projection.py.
_FRT_YIELD_WEIGHTED_TRIO = ("citrus", "mango", "watermelon")


def _harvested_area_inputs(w):
    """Get inputs for build_harvested_area_gaez, including FDD shares when relevant."""
    inputs = {
        "harvested_area_raster": gaez_path("harvested_area", w.water_supply, w.crop),
        "cell_mapping": f"<processing>/{w.name}/region_class_cell_mapping.npz",
        "regions": f"<processing>/{w.name}/regions.geojson",
        "crop_mapping": "data/curated/gaez_crop_code_mapping.csv",
        "faostat_production": f"<processing>/{w.name}/faostat_crop_production.csv",
    }
    if _fdd_crops_in_config:
        inputs["fdd_shares"] = f"<processing>/{w.name}/fdd_area_shares.csv"
    else:
        inputs["fdd_shares"] = []
    if w.crop == "olive":
        # The GAEZ Module VI OOC raster pools olive area with linseed, mustard,
        # safflower and other minor oilseed land. Deflate per-country to the
        # FAOSTAT olive share so we don't attribute non-olive OOC land to olive.
        inputs["ooc_olive_share"] = f"<processing>/{w.name}/ooc_olive_area_share.csv"
    else:
        inputs["ooc_olive_share"] = []
    if w.crop == "banana":
        # Banana absorbs a per-country share of the non-modelled FRT-pool
        # residual on top of its BAN-raster area; the addition is
        # distributed across cells by GAEZ banana yield x suitable_area.
        inputs["frt_attribution"] = f"<processing>/{w.name}/frt_area_attribution.csv"
        inputs["crop_yields"] = f"<processing>/{w.name}/crop_yields/{w.crop}_r.csv"
    return inputs


rule build_harvested_area_gaez:
    input:
        unpack(_harvested_area_inputs),
    params:
        non_food_crops=config["non_food_crops"],
    output:
        "<processing>/{name}/harvested_area/gaez/{crop}_{water_supply}.csv",
    # Cropgrids-backed crops produce this output via build_crop_yields_cropgrids;
    # FRT-trio crops via build_harvested_area_yield_weighted (below).
    wildcard_constraints:
        crop=(
            "|".join(c for c in gaez_crops() if c not in _FRT_YIELD_WEIGHTED_TRIO)
            if [c for c in gaez_crops() if c not in _FRT_YIELD_WEIGHTED_TRIO]
            else "__never__"
        ),
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=400,
    log:
        "<logs>/{name}/build_harvested_area_gaez_{crop}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/build_harvested_area_gaez_{crop}_{water_supply}.tsv"
    script:
        "../scripts/build_harvested_area.py"


def _yield_weighted_trio_in_config():
    return [c for c in _FRT_YIELD_WEIGHTED_TRIO if c in config["crops"]]


def _yield_weighted_inputs(w):
    """Inputs for the yield-weighted rule (jointly produces _r and _i)."""
    inputs = {
        "yields_r": f"<processing>/{w.name}/crop_yields/{w.crop}_r.csv",
        "attribution": f"<processing>/{w.name}/frt_area_attribution.csv",
        "regions": f"<processing>/{w.name}/regions.geojson",
    }
    if w.crop in irrigated_crops():
        inputs["yields_i"] = f"<processing>/{w.name}/crop_yields/{w.crop}_i.csv"
    return inputs


def _yield_weighted_outputs(crop: str) -> dict[str, str]:
    out = {"rainfed": f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"}
    if crop in irrigated_crops():
        out["irrigated"] = f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
    return out


def _crop_moisture(crop: str) -> float:
    import pandas as pd

    moisture = pd.read_csv(
        "data/curated/crop_moisture_content.csv", comment="#"
    ).set_index("crop")["moisture_fraction"]
    return float(moisture.loc[crop])


rule build_harvested_area_yield_weighted:
    """Cell-level area distribution for FRT-trio fruits, weighted by GAEZ
    yield x suitable_area, target-production-rescaled per country so the
    area x yield total matches the FAOSTAT-derived production target
    (replaces the misallocating FRT-raster scaling).

    Jointly emits the rainfed and (when the crop is irrigated) irrigated
    harvested-area CSVs from a single per-country target so the r/i split
    is set endogenously by the GAEZ yield-weight ratio.
    """
    input:
        unpack(_yield_weighted_inputs),
    params:
        moisture_fraction=lambda w: _crop_moisture(w.crop),
    output:
        rainfed="<processing>/{name}/harvested_area/gaez/{crop}_r.csv",
        irrigated="<processing>/{name}/harvested_area/gaez/{crop}_i.csv",
    wildcard_constraints:
        crop=(
            "|".join(_yield_weighted_trio_in_config())
            if _yield_weighted_trio_in_config()
            else "__never__"
        ),
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=700,
    log:
        "<logs>/{name}/build_harvested_area_yield_weighted_{crop}.log",
    benchmark:
        "<benchmarks>/{name}/build_harvested_area_yield_weighted_{crop}.tsv"
    script:
        "../scripts/build_harvested_area_yield_weighted.py"


MIRCA_MULTICROPPING_CATALOG = "data/curated/mirca_os_multicropping_combinations.yaml"
MIRCA_MULTICROPPING_YEAR = closest_mirca_multicropping_year(config["baseline_year"])
if MIRCA_MULTICROPPING_YEAR != config["baseline_year"]:
    logger.warning(
        "MIRCA-OS has no release for baseline_year=%d; using closest release %d "
        "for the multiple-cropping baseline",
        config["baseline_year"],
        MIRCA_MULTICROPPING_YEAR,
    )


def mirca_multicropping_inputs(_wildcards):
    """Inputs for the config-specific observed multi-cropping derivation.

    All 23 MIRCA crops' annual harvested grids (for the all-crop M_total),
    the ir/rf footprint layers, the rice subcrop monthly grids (repeated-cycle
    detection), the GAEZ multiple-cropping-zone rasters (cycle-count gate), and the
    crop concordance and fixed combination catalog. The active config's region,
    resource-class, and GAEZ grids make the resulting aggregate config-specific.
    """
    grids = "data/downloads/mirca_os/grids"
    year = MIRCA_MULTICROPPING_YEAR
    inputs = {
        "concordance": "data/curated/mirca_os_crop_mapping.csv",
        "catalog": MIRCA_MULTICROPPING_CATALOG,
        "classes": "<processing>/{name}/resource_classes.nc",
        "regions": "<processing>/{name}/regions.geojson",
        "footprint_ir": f"{grids}/footprint/MIRCA-OS_{year}_ir_v2.tif",
        "footprint_rf": f"{grids}/footprint/MIRCA-OS_{year}_rf_v2.tif",
        "rice2_ir": f"{grids}/monthly/MIRCA-OS_Rice2_{year}_ir.nc",
        "rice2_rf": f"{grids}/monthly/MIRCA-OS_Rice2_{year}_rf.nc",
        "rice3_ir": f"{grids}/monthly/MIRCA-OS_Rice3_{year}_ir.nc",
        "rice3_rf": f"{grids}/monthly/MIRCA-OS_Rice3_{year}_rf.nc",
        "zone_i": gaez_path("multiple_cropping_zone", "i", "all"),
        "zone_r": gaez_path("multiple_cropping_zone", "r", "all"),
    }
    for mirca_crop in MIRCA_OS_BASE_CROPS:
        for mws in ("ir", "rf"):
            key = f"annual_{mirca_crop.replace(' ', '_')}_{mws}"
            inputs[key] = f"{grids}/annual/MIRCA-OS_{mirca_crop}_{year}_{mws}_v2.tif"
    return inputs


rule derive_mirca_multicropping:
    """Derive and aggregate the observed multi-cropping baseline.

    Attributes MIRCA's extra-cycle harvested area to the fixed curated crop-
    sequence catalog, gating on MIRCA co-occurrence and the active config's GAEZ
    multiple-cropping zones. The physical link areas are aggregated directly to
    the active region and resource-class grids.
    """
    input:
        unpack(mirca_multicropping_inputs),
    output:
        baseline="<processing>/{name}/multi_cropping/baseline_area.csv",
        residual="<processing>/{name}/multi_cropping/residual_multicrop.tif",
        stats="<processing>/{name}/multi_cropping/attribution_stats.csv",
    params:
        source_year=MIRCA_MULTICROPPING_YEAR,
    resources:
        runtime="15m",
        mem_mb=8000,
    log:
        "<logs>/{name}/derive_mirca_multicropping.log",
    benchmark:
        "<benchmarks>/{name}/derive_mirca_multicropping.tsv"
    script:
        "../scripts/derive_mirca_multicropping.py"


def multicropping_combinations_yaml():
    """Path of the authoritative observed combination catalog."""
    return MIRCA_MULTICROPPING_CATALOG


def _effective_multicropping():
    """The effective observed and greenfield combination set, DAG-side."""
    return effective_combinations(config, multicropping_combinations_yaml())


def multi_cropping_inputs(_wildcards):
    combos_cfg = _effective_multicropping()
    crops_by_supply: dict[str, set[str]] = {"r": set(), "i": set()}
    for combo_name, entry in combos_cfg.items():
        if entry is None:
            continue
        water_supplies = entry.get("water_supplies", ["r"])
        if isinstance(water_supplies, str):
            water_supplies = [water_supplies]
        for ws in water_supplies:
            crops_by_supply[ws].update(entry["crops"])
    yield_kind = (
        "actual_yield" if config["validation"]["use_actual_yields"] else "yield"
    )
    inputs = {
        "classes": "<processing>/{name}/resource_classes.nc",
        "regions": "<processing>/{name}/regions.geojson",
        "yield_unit_conversions": "data/curated/yield_unit_conversions.csv",
        "combinations": multicropping_combinations_yaml(),
    }
    for ws in ("r", "i"):
        for crop in sorted(crops_by_supply[ws]):
            prefix = f"{crop}_{ws}"
            inputs[f"{prefix}_yield_raster"] = gaez_path(yield_kind, ws, crop)
            inputs[f"{prefix}_suitability_raster"] = gaez_path("suitability", ws, crop)
            if ws == "i":
                inputs[f"{prefix}_water_requirement_raster"] = gaez_path(
                    "water_requirement", ws, crop
                )
        if crops_by_supply[ws]:
            inputs[f"multiple_cropping_zone_{ws}"] = gaez_path(
                "multiple_cropping_zone", ws, "all"
            )
    return inputs


rule build_multi_cropping:
    input:
        unpack(multi_cropping_inputs),
        moisture_content="data/curated/crop_moisture_content.csv",
    params:
        use_actual_yields=config["validation"]["use_actual_yields"],
    output:
        eligible="<processing>/{name}/multi_cropping/eligible_area.csv",
        yields="<processing>/{name}/multi_cropping/cycle_yields.csv",
    group:
        "prep"
    resources:
        runtime="2m",
        mem_mb=5500,
    log:
        "<logs>/{name}/build_multi_cropping.log",
    benchmark:
        "<benchmarks>/{name}/build_multi_cropping.tsv"
    script:
        "../scripts/build_multi_cropping.py"


rule build_grassland_yields:
    input:
        grassland="data/downloads/grassland_yield_historical.nc4",
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        "<processing>/{name}/isimip_grassland_yields.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1500,
    log:
        "<logs>/{name}/build_grassland_yields.log",
    benchmark:
        "<benchmarks>/{name}/build_grassland_yields.tsv"
    script:
        "../scripts/build_grassland_yields.py"


rule build_luicube_grassland_yields:
    input:
        luicube="<processing>/shared/luc/luicube_grassland.nc",
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
    output:
        "<processing>/{name}/luicube_grassland_yields.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2600,
    log:
        "<logs>/{name}/build_luicube_grassland_yields.log",
    benchmark:
        "<benchmarks>/{name}/build_luicube_grassland_yields.tsv"
    script:
        "../scripts/build_luicube_grassland_yields.py"


rule prepare_faostat_pasture_area:
    input:
        rl_parquet="data/downloads/faostat/RL.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        baseline_year=config["baseline_year"],
    output:
        "<processing>/{name}/faostat_pasture_area.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2500,
    log:
        "<logs>/{name}/prepare_faostat_pasture_area.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_pasture_area.tsv"
    script:
        "../scripts/prepare_faostat_pasture_area.py"


rule merge_grassland_yields:
    input:
        luicube="<processing>/{name}/luicube_grassland_yields.csv",
        isimip="<processing>/{name}/isimip_grassland_yields.csv",
        regions="<processing>/{name}/regions.geojson",
    params:
        isimip_utilization_rate=config["grazing"]["isimip_utilization_rate"],
    output:
        "<processing>/{name}/grassland_yields.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/merge_grassland_yields.log",
    benchmark:
        "<benchmarks>/{name}/merge_grassland_yields.tsv"
    script:
        "../scripts/merge_grassland_yields.py"


rule build_crop_residue_yields:
    input:
        yield_r=lambda wildcards: f"<processing>/{wildcards.name}/crop_yields/{wildcards.crop}_r.csv",
        yield_i=lambda wildcards: (
            f"<processing>/{wildcards.name}/crop_yields/{wildcards.crop}_i.csv"
            if config["irrigation"]["irrigated_crops"] == "all"
            or wildcards.crop in config["irrigation"]["irrigated_crops"]
            else []
        ),
        residue_specs="data/curated/crop_residue_specs.csv",
        regions="<processing>/{name}/regions.geojson",
    output:
        "<processing>/{name}/crop_residue_yields/{crop}.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=250,
    log:
        "<logs>/{name}/build_crop_residue_yields_{crop}.log",
    benchmark:
        "<benchmarks>/{name}/build_crop_residue_yields_{crop}.tsv"
    script:
        "../scripts/build_crop_residue_yields.py"


def residue_yield_inputs(_wildcards):
    return {
        f"residue_{crop}": f"<processing>/{{name}}/crop_residue_yields/{crop}.csv"
        for crop in (
            set(config["animal_products"]["residue_crops"]) & set(config["crops"])
        )
    }


rule prepare_biofuel_baseline:
    input:
        biofuel_crop_map="data/curated/faostat_biofuel_crop_map.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        moisture_content="data/curated/crop_moisture_content.csv",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_other_uses_element_code"],
    output:
        "<processing>/{name}/biofuel_baseline.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/prepare_biofuel_baseline.log",
    benchmark:
        "<benchmarks>/{name}/prepare_biofuel_baseline.tsv"
    script:
        "../scripts/prepare_biofuel_baseline.py"


rule prepare_fiber_baseline:
    input:
        fiber_demand_map="data/curated/faostat_fiber_demand_map.csv",
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
    output:
        "<processing>/{name}/fiber_baseline.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2900,
    log:
        "<logs>/{name}/prepare_fiber_baseline.log",
    benchmark:
        "<benchmarks>/{name}/prepare_fiber_baseline.tsv"
    script:
        "../scripts/prepare_fiber_baseline.py"


if config["cost_calibration"]["generate"]:

    _cal_scenario = config["cost_calibration"]["scenario"]
    _cal_name = config.get("name", "default")

    rule extract_cost_calibration:
        input:
            network=f"<results>/{_cal_name}/solved/model_scen-{_cal_scenario}.nc",
        output:
            crop_correction=config["cost_calibration"]["crop_correction_csv"],
            multi_crop_correction=config["cost_calibration"][
                "multi_crop_correction_csv"
            ],
            grassland_correction=config["cost_calibration"]["grassland_correction_csv"],
            animal_correction=config["cost_calibration"]["animal_correction_csv"],
        resources:
            runtime="2m",
            mem_mb=2000,
        log:
            f"<logs>/{_cal_name}/extract_cost_calibration.log",
        benchmark:
            f"<benchmarks>/{_cal_name}/extract_cost_calibration.tsv"
        script:
            "../scripts/extract_cost_calibration.py"
