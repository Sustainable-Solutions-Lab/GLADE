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
        production_year=config["validation"]["production_year"],
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
        averaging_period=config["animal_costs"]["averaging_period"],
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
        gleam_mapping="data/curated/gleam_feed_mapping.csv",
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


rule build_feed_to_animal_products:
    input:
        wirsenius="data/curated/wirsenius_feed_energy_requirements.csv",
        ruminant_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_categories="<processing>/{name}/monogastric_feed_categories.csv",
        country_region_map="data/curated/country_wirsenius_region.csv",
    output:
        "<processing>/{name}/feed_to_animal_products.csv",
    params:
        feed_efficiency_regions=config["animal_products"]["feed_efficiency_regions"],
        countries=config["countries"],
        net_to_me_conversion=config["animal_products"][
            "net_to_metabolizable_energy_conversion"
        ],
        carcass_to_retail=config["animal_products"]["carcass_to_retail_meat"],
        feed_proxy_map=config["animal_products"]["feed_proxy_map"],
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


rule prepare_gleam_feed_baseline:
    input:
        si_table_2="data/curated/gleam_tables/gleam_2_0_si2_global_livestock_feed_intake.csv",
        si_table_4="data/curated/gleam_tables/gleam_2_0_si4_dairy_cattle_composition.csv",
        si_table_5="data/curated/gleam_tables/gleam_2_0_si5_beef_cattle_composition.csv",
        oecd_status="data/curated/country_oecd_status.csv",
        gleam_regions="data/curated/country_gleam_region.csv",
        wirsenius="data/curated/wirsenius_feed_energy_requirements.csv",
        country_wirsenius_region="data/curated/country_wirsenius_region.csv",
        qcl_csv="data/downloads/faostat/QCL.csv",
        m49_codes="data/curated/M49-codes.csv",
    params:
        reference_year=config["validation"]["production_year"],
        countries=config["countries"],
        net_to_me_conversion=config["animal_products"][
            "net_to_metabolizable_energy_conversion"
        ],
        feed_proxy_map=config["animal_products"]["feed_proxy_map"],
        faostat_items=config["animal_products"]["faostat_items"],
    output:
        "<processing>/{name}/gleam_feed_baseline.csv",
    group:
        "prep"
    resources:
        runtime="2m",
        mem_mb=2800,
    log:
        "<logs>/{name}/prepare_gleam_feed_baseline.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gleam_feed_baseline.tsv"
    script:
        "../scripts/prepare_gleam_feed_baseline.py"


rule calculate_manure_emissions:
    input:
        ruminant_feed_categories="<processing>/{name}/ruminant_feed_categories.csv",
        monogastric_feed_categories="<processing>/{name}/monogastric_feed_categories.csv",
        b0_data="data/curated/ipcc_manure_methane_producing_capacity.csv",
        mcf_data="data/curated/ipcc_manure_methane_conversion_factors.csv",
        mms_fractions="data/curated/gleam_tables/manure_management_systems_fraction.csv",
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
