# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Dietary intake and baseline diet estimation rules.

Includes GDD survey processing, FAOSTAT supplements, food loss/waste,
GBD dietary risk exposure, and per-food baseline diet estimation.
"""


rule prepare_gdd_dietary_intake:
    input:
        gdd_dir="data/manually_downloaded/GDD-dietary-intake",
    params:
        countries=config["countries"],
        food_groups=config["food_groups"]["included"],
        reference_year=config["health"]["reference_year"],
        ssb_sugar_g_per_100g=config["health"]["ssb_sugar_g_per_100g"],
    output:
        diet="processing/{name}/gdd_dietary_intake.csv",
    log:
        "logs/{name}/prepare_gdd_dietary_intake.log",
    script:
        "../scripts/prepare_gdd_dietary_intake.py"


rule prepare_faostat_fbs_items:
    """Prepare raw item-level supply data from FAOSTAT Food Balance Sheets.

    Reads supply data (kg/capita/year) for all items in the food item mapping
    from a bulk FBS CSV, used for calculating within-group food consumption ratios.
    """
    input:
        food_item_map="data/curated/faostat_food_item_map.csv",
        fbs_csv="data/downloads/faostat/FBS.csv",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["diet"]["baseline_reference_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        fbs_items="processing/{name}/faostat_fbs_items.csv",
    log:
        "logs/{name}/prepare_faostat_fbs_items.log",
    script:
        "../scripts/prepare_faostat_fbs_items.py"


rule prepare_faostat_gdd_supplements:
    """Prepare FAOSTAT supply data to supplement GDD dietary intake.

    Reads dairy, poultry, and oil supply data from FAOSTAT FBS bulk CSV to
    fill gaps in the Global Dietary Database (GDD).
    """
    input:
        fbs_csv="data/downloads/faostat/FBS.csv",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["health"]["reference_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
        poultry_carcass_to_retail=config["animal_products"]["carcass_to_retail_meat"][
            "meat-chicken"
        ],
    output:
        supply="processing/{name}/faostat_gdd_supplements.csv",
    log:
        "logs/{name}/prepare_faostat_gdd_supplements.log",
    script:
        "../scripts/prepare_faostat_gdd_supplements.py"


rule merge_dietary_sources:
    input:
        gdd="processing/{name}/gdd_dietary_intake.csv",
        faostat="processing/{name}/faostat_gdd_supplements.csv",
        food_loss_waste="processing/{name}/food_loss_waste.csv",
    output:
        diet="processing/{name}/dietary_intake.csv",
    log:
        "logs/{name}/merge_dietary_sources.log",
    script:
        "../scripts/merge_dietary_sources.py"


rule prepare_food_loss_waste:
    input:
        m49="data/curated/M49-codes.csv",
        animal_production="processing/{name}/faostat_animal_production.csv",
        faostat_gdd_supplements="processing/{name}/faostat_gdd_supplements.csv",
        population="processing/{name}/population.csv",
        fbs_csv="data/downloads/faostat/FBS.csv",
        sdg_csv="data/downloads/unsd/SDG_12_3_1.csv",
    params:
        countries=config["countries"],
        food_groups=config["food_groups"]["included"],
        health_reference_year=config["health"]["reference_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        food_loss_waste="processing/{name}/food_loss_waste.csv",
    log:
        "logs/{name}/prepare_food_loss_waste.log",
    script:
        "../scripts/prepare_food_loss_waste.py"


rule prepare_gbd_dietary_risk_exposure:
    """Process GBD 2019 dietary risk exposure data for food group intake estimates.

    Extracts country-level dietary intake (g/day) for adults 25+ from GBD risk
    factor CSVs. Used to average with GDD estimates and for cross-validation.
    """
    input:
        gbd_dir="data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA",
    params:
        reference_year=config["diet"]["baseline_reference_year"],
    output:
        exposure="processing/{name}/gbd_dietary_risk_exposure.csv",
    log:
        "logs/{name}/prepare_gbd_dietary_risk_exposure.log",
    script:
        "../scripts/prepare_gbd_dietary_risk_exposure.py"


rule estimate_baseline_diet:
    """Estimate per-food, per-country baseline diet from multiple sources.

    Combines food group totals (GDD + GBD averaged) with FAOSTAT item-level
    supply data to disaggregate group totals into per-food consumption estimates.
    """
    input:
        dietary_intake="processing/{name}/dietary_intake.csv",
        gbd_exposure="processing/{name}/gbd_dietary_risk_exposure.csv",
        fbs_items="processing/{name}/faostat_fbs_items.csv",
        crop_production="processing/{name}/faostat_crop_production.csv",
        animal_production="processing/{name}/faostat_animal_production.csv",
        food_item_map="data/curated/faostat_food_item_map.csv",
        qcl_resolution="data/curated/faostat_food_qcl_resolution.csv",
        food_groups="data/curated/food_groups.csv",
        food_loss_waste="processing/{name}/food_loss_waste.csv",
    params:
        reference_year=config["diet"]["baseline_reference_year"],
        baseline_age=config["diet"]["baseline_age"],
        food_groups_included=config["food_groups"]["included"],
        byproducts=config["byproducts"],
        fbs_override_foods=config["diet"]["fbs_override_foods"],
    output:
        baseline_diet="processing/{name}/baseline_diet.csv",
    log:
        "logs/{name}/estimate_baseline_diet.log",
    script:
        "../scripts/estimate_baseline_diet.py"
