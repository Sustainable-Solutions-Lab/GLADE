# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Crop-related data preparation rules.

Includes crop yields, harvested areas, multi-cropping, grassland yields,
and crop residue processing.
"""


rule prepare_faostat_crop_production:
    input:
        mapping="data/curated/faostat_crop_item_map.csv",
        qcl_csv="data/downloads/faostat/QCL.parquet",
        m49_codes="data/curated/M49-codes.csv",
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


rule build_crop_yields:
    input:
        unpack(yield_and_suitability_for_crop),
        classes="<processing>/{name}/resource_classes.nc",
        regions="<processing>/{name}/regions.geojson",
        yield_unit_conversions="data/curated/yield_unit_conversions.csv",
        moisture_content="data/curated/crop_moisture_content.csv",
    params:
        use_actual_yields=config["validation"]["use_actual_yields"],
    output:
        "<processing>/{name}/crop_yields/{crop}_{water_supply}.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1300,
    log:
        "<logs>/{name}/build_crop_yields_{crop}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/build_crop_yields_{crop}_{water_supply}.tsv"
    script:
        "../scripts/build_crop_yields.py"


_fdd_crops_in_config = set(config["fodder_decomposition"]["fdd_crops"]) & set(
    config["crops"]
)


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
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = set(config["crops"])
    else:
        irrigated_crops = set(irr_cfg)

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
        if crop in irrigated_crops:
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


def _harvested_area_inputs(w):
    """Get inputs for build_harvested_area_gaez, including FDD shares when relevant."""
    inputs = {
        "harvested_area_raster": gaez_path("harvested_area", w.water_supply, w.crop),
        "classes": f"<processing>/{w.name}/resource_classes.nc",
        "regions": f"<processing>/{w.name}/regions.geojson",
        "crop_mapping": "data/curated/gaez_crop_code_mapping.csv",
        "faostat_production": f"<processing>/{w.name}/faostat_crop_production.csv",
    }
    if _fdd_crops_in_config:
        inputs["fdd_shares"] = f"<processing>/{w.name}/fdd_area_shares.csv"
    else:
        inputs["fdd_shares"] = []
    return inputs


rule build_harvested_area_gaez:
    input:
        unpack(_harvested_area_inputs),
    params:
        non_food_crops=config["non_food_crops"],
    output:
        "<processing>/{name}/harvested_area/gaez/{crop}_{water_supply}.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=700,
    log:
        "<logs>/{name}/build_harvested_area_gaez_{crop}_{water_supply}.log",
    benchmark:
        "<benchmarks>/{name}/build_harvested_area_gaez_{crop}_{water_supply}.tsv"
    script:
        "../scripts/build_harvested_area.py"


def multi_cropping_inputs(_wildcards):
    combos_cfg = config["multiple_cropping"]
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
    }
    for ws in ("r", "i"):
        for crop in sorted(crops_by_supply[ws]):
            prefix = f"{crop}_{ws}"
            inputs[f"{prefix}_yield_raster"] = gaez_path(yield_kind, ws, crop)
            inputs[f"{prefix}_suitability_raster"] = gaez_path("suitability", ws, crop)
            inputs[f"{prefix}_growing_season_start_raster"] = gaez_path(
                "growing_season_start", ws, crop
            )
            inputs[f"{prefix}_growing_season_length_raster"] = gaez_path(
                "growing_season_length", ws, crop
            )
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
        combinations=lambda wildcards: config["multiple_cropping"],
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


def grassland_forage_overlap_inputs(_wildcards):
    """Inputs needed to estimate forage-crop supply overlap by country."""
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = set(config["crops"])
    else:
        irrigated_crops = set(irr_cfg)

    inputs = {"regions": "<processing>/{name}/regions.geojson"}
    for crop in config["grazing"]["forage_overlap_crops"]:
        if crop not in config["crops"]:
            continue
        inputs[f"forage_yield_{crop}_r"] = (
            f"<processing>/{{name}}/crop_yields/{crop}_r.csv"
        )
        inputs[f"forage_harvested_{crop}_r"] = (
            f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"
        )
        if crop in irrigated_crops:
            inputs[f"forage_yield_{crop}_i"] = (
                f"<processing>/{{name}}/crop_yields/{crop}_i.csv"
            )
            inputs[f"forage_harvested_{crop}_i"] = (
                f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
            )
    return inputs


def merge_grassland_inputs(wildcards):
    """Get all inputs required for grassland yield merging."""
    inputs = {
        "luicube": f"<processing>/{wildcards.name}/luicube_grassland_yields.csv",
        "isimip": f"<processing>/{wildcards.name}/isimip_grassland_yields.csv",
    }
    inputs.update(grassland_forage_overlap_inputs(wildcards))
    if config["fodder_decomposition"]["yield_corrections"]["enabled"]:
        inputs["fodder_yield_corrections"] = (
            f"<processing>/{wildcards.name}/fodder_yield_corrections.csv"
        )
    return inputs


rule merge_grassland_yields:
    input:
        unpack(merge_grassland_inputs),
    params:
        isimip_utilization_rate=config["grazing"]["isimip_utilization_rate"],
        forage_overlap_subtraction_alpha=config["grazing"][
            "forage_overlap_subtraction_alpha"
        ],
        forage_overlap_crops=config["grazing"]["forage_overlap_crops"],
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
        gleam_supplement="data/downloads/gleam_3.0_supplement_s1.xlsx",
        ruminant_feed_table="data/bundled/gleam3/ruminants_feed_yield_fractions.csv",
        monogastric_feed_table="data/bundled/gleam3/monogastrics_feed_yeild_fractions.csv",
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
