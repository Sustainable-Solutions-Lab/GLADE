# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Animal product and feed-related data preparation rules.

Includes feed properties, feed categorization, feed-to-product conversions,
and manure emissions calculations.
"""


rule prepare_faostat_animal_production:
    input:
        qcl_csv="data/downloads/faostat/QCL.csv",
        m49_codes="data/curated/M49-codes.csv",
    params:
        production_year=config["baseline_year"],
        countries=config["countries"],
        carcass_to_retail_meat=config["animal_products"]["carcass_to_retail_meat"],
        qcl_element_code=config["data"]["faostat"]["qcl_production_element_code"],
        faostat_items=config["animal_products"]["faostat_items"],
    output:
        "<processing>/{name}/faostat_animal_production.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2800,
    log:
        "<logs>/{name}/prepare_faostat_animal_production.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_animal_production.tsv"
    script:
        "../scripts/prepare_faostat_animal_production.py"


rule prepare_faostat_yields:
    input:
        mapping="data/curated/faostat_animal_yield_mapping.yaml",
        qcl_csv="data/downloads/faostat/QCL.csv",
    params:
        cost_params=config["animal_costs"]["faostat"],
        averaging_period=config["costs"]["averaging_period"],
    output:
        "<processing>/{name}/faostat_animal_yields.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=2800,
    log:
        "<logs>/{name}/prepare_faostat_yields.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_yields.tsv"
    script:
        "../scripts/prepare_faostat_yields.py"


rule prepare_gleam_feed_properties:
    input:
        gleam_supplement="data/downloads/gleam_3.0_supplement_s1.xlsx",
        gleam_mapping="data/curated/gleam/feed_mapping.csv",
    output:
        ruminant="<processing>/{name}/ruminant_feed_properties.csv",
        monogastric="<processing>/{name}/monogastric_feed_properties.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/prepare_gleam_feed_properties.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gleam_feed_properties.tsv"
    script:
        "../scripts/prepare_gleam_feed_properties.py"


rule categorize_feeds:
    input:
        ruminant_feed_properties="<processing>/{name}/ruminant_feed_properties.csv",
        monogastric_feed_properties="<processing>/{name}/monogastric_feed_properties.csv",
        enteric_methane_yields="data/curated/ipcc_enteric_methane_yields.csv",
        ash_content="data/curated/feed_ash_content.csv",
    output:
        ruminant_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_categories="<processing>/{name}/monogastric_feed_categories.csv",
        ruminant_mapping="<processing>/{name}/ruminant_feed_mapping.csv",
        monogastric_mapping="<processing>/{name}/monogastric_feed_mapping.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/categorize_feeds.log",
    benchmark:
        "<benchmarks>/{name}/categorize_feeds.tsv"
    script:
        "../scripts/categorize_feeds.py"


rule compute_gleam3_me_requirements:
    input:
        gleam3_intakes="data/bundled/gleam3/intakes.csv",
        gleam3_production="data/bundled/gleam3/production.csv",
        ruminant_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_categories="<processing>/{name}/monogastric_feed_categories.csv",
        wirsenius="data/curated/wirsenius_feed_energy_requirements.csv",
        country_wirsenius_region="data/curated/country_wirsenius_region.csv",
    params:
        countries=config["countries"],
        net_to_me_conversion=config["animal_products"][
            "net_to_metabolizable_energy_conversion"
        ],
    output:
        "<processing>/{name}/gleam3_me_requirements.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=500,
    log:
        "<logs>/{name}/compute_gleam3_me_requirements.log",
    benchmark:
        "<benchmarks>/{name}/compute_gleam3_me_requirements.tsv"
    script:
        "../scripts/compute_gleam3_me_requirements.py"


rule build_feed_to_animal_products:
    input:
        me_requirements="<processing>/{name}/gleam3_me_requirements.csv",
        ruminant_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_categories="<processing>/{name}/monogastric_feed_categories.csv",
    output:
        "<processing>/{name}/feed_to_animal_products.csv",
    params:
        carcass_to_retail=config["animal_products"]["carcass_to_retail_meat"],
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/build_feed_to_animal_products.log",
    benchmark:
        "<benchmarks>/{name}/build_feed_to_animal_products.tsv"
    script:
        "../scripts/build_feed_to_animal_products.py"


rule compute_gleam3_feed_fractions:
    input:
        feed_items_categories="data/bundled/gleam3/feed_items_categories.xlsx",
        gleam_feed_mapping="data/curated/gleam/feed_mapping.csv",
        faostat_crop_production="<processing>/{name}/faostat_crop_production.csv",
        ruminant_feed_mapping="<processing>/{name}/ruminant_feed_mapping.csv",
        monogastric_feed_mapping="<processing>/{name}/monogastric_feed_mapping.csv",
    output:
        "<processing>/{name}/gleam3_feed_fractions.csv",
    params:
        countries=config["countries"],
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=500,
    log:
        "<logs>/{name}/compute_gleam3_feed_fractions.log",
    benchmark:
        "<benchmarks>/{name}/compute_gleam3_feed_fractions.tsv"
    script:
        "../scripts/compute_gleam3_feed_fractions.py"


rule prepare_feed_baseline:
    input:
        gleam3_intakes="data/bundled/gleam3/intakes.csv",
        gleam3_production="data/bundled/gleam3/production.csv",
        gleam3_feed_fractions="<processing>/{name}/gleam3_feed_fractions.csv",
        me_requirements="<processing>/{name}/gleam3_me_requirements.csv",
        qcl_csv="data/downloads/faostat/QCL.csv",
        m49_codes="data/curated/M49-codes.csv",
        ruminant_feed_mapping="<processing>/{name}/ruminant_feed_mapping.csv",
        monogastric_feed_mapping="<processing>/{name}/monogastric_feed_mapping.csv",
        feed_to_animal_products="<processing>/{name}/feed_to_animal_products.csv",
        faostat_animal_production="<processing>/{name}/faostat_animal_production.csv",
    params:
        reference_year=config["baseline_year"],
        countries=config["countries"],
        faostat_items=config["animal_products"]["faostat_items"],
        gleam3_system_product_map=config["animal_products"]["gleam3_system_product_map"],
    output:
        "<processing>/{name}/feed_baseline.csv",
    group:
        "prep"
    resources:
        runtime="2m",
        mem_mb=2800,
    log:
        "<logs>/{name}/prepare_feed_baseline.log",
    benchmark:
        "<benchmarks>/{name}/prepare_feed_baseline.tsv"
    script:
        "../scripts/prepare_feed_baseline.py"


_grassland_cal_cfg = config["grazing"]["grassland_forage_calibration"]

if _grassland_cal_cfg["generate"]:
    _grassland_cal_scenario = _grassland_cal_cfg["scenario"]

    rule compute_grassland_calibration:
        input:
            network=f"<results>/{name}/solved/model_scen-{_grassland_cal_scenario}.nc",
        output:
            _grassland_cal_cfg["source"],
        resources:
            runtime="2m",
            mem_mb=4000,
        log:
            f"<logs>/{name}/compute_grassland_calibration_scen-{_grassland_cal_scenario}.log",
        benchmark:
            f"<benchmarks>/{name}/compute_grassland_calibration_scen-{_grassland_cal_scenario}.tsv"
        script:
            "../scripts/compute_grassland_calibration.py"


rule calculate_manure_emissions:
    input:
        ruminant_feed_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_feed_categories="<processing>/{name}/monogastric_feed_categories.csv",
        b0_data="data/curated/ipcc_manure_methane_producing_capacity.csv",
        mcf_data="data/curated/ipcc_manure_methane_conversion_factors.csv",
        mms_fractions="data/bundled/gleam3/manure_management_systems_fraction.csv",
        n2o_efs="data/curated/ipcc_manure_n2o_emission_factors.csv",
    output:
        "<processing>/{name}/manure_emission_factors.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/calculate_manure_emissions.log",
    benchmark:
        "<benchmarks>/{name}/calculate_manure_emissions.tsv"
    script:
        "../scripts/calculate_manure_emissions.py"
