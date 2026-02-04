# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Health-related data preparation rules.

Includes dietary intake, mortality rates, relative risks, life tables,
and health cost calculations.
"""


rule retrieve_gdp_per_capita:
    """Retrieve GDP per capita data from IMF World Economic Outlook API.

    Missing data is imputed using UN M49 sub-regional means.
    """
    input:
        m49="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        year=config["health"]["clustering"]["gdp_reference_year"],
    output:
        gdp="data/downloads/gdp_per_capita.csv",
    log:
        "logs/retrieve_gdp_per_capita.log",
    script:
        "../scripts/retrieve_gdp_per_capita.py"


rule prepare_gbd_mortality:
    input:
        gbd_mortality="data/manually_downloaded/IHME-GBD_2023-dealth-rates.csv",
    params:
        countries=config["countries"],
        causes=config["health"]["causes"],
        reference_year=config["health"]["reference_year"],
    output:
        mortality="processing/{name}/health/gbd_mortality_rates.csv",
    log:
        "logs/{name}/prepare_gbd_mortality.log",
    script:
        "../scripts/prepare_gbd_mortality.py"


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
        reference_year=config["food_groups"]["fix_within_group_ratios"][
            "reference_year"
        ],
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


rule prepare_relative_risks:
    input:
        gbd_rr="data/manually_downloaded/IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX",
    params:
        risk_factors=config["health"]["risk_factors"],
        causes=config["health"]["causes"],
        omega3_per_100g=config["health"]["omega3_per_100g_fish"],
        ssb_sugar_g_per_100g=config["health"]["ssb_sugar_g_per_100g"],
    output:
        relative_risks="processing/{name}/health/relative_risks.csv",
    log:
        "logs/{name}/prepare_relative_risks.log",
    script:
        "../scripts/prepare_relative_risks.py"


rule prepare_life_table:
    input:
        wpp_life_table="data/downloads/WPP_life_table.csv.gz",
    params:
        reference_year=config["health"]["reference_year"],
    output:
        life_table="processing/{name}/health/life_table.csv",
    log:
        "logs/{name}/prepare_life_table.log",
    script:
        "../scripts/prepare_life_table.py"


rule prepare_health_costs:
    """Prepare health cost data for SOS2 linearization.

    This rule is scenario-specific because the breakpoint tables (risk_breakpoints,
    cause_log) depend on intake_grid_points and log_rr_points parameters which
    can vary by scenario.
    """
    input:
        regions="processing/{name}/regions.geojson",
        diet="processing/{name}/dietary_intake.csv",
        relative_risks="processing/{name}/health/relative_risks.csv",
        dr="processing/{name}/health/gbd_mortality_rates.csv",
        population="processing/{name}/population_age.csv",
        life_table="processing/{name}/health/life_table.csv",
        food_groups="data/curated/food_groups.csv",
        gdp="data/downloads/gdp_per_capita.csv",
    params:
        countries=lambda w: get_effective_config(w.scenario)["countries"],
        health=lambda w: get_effective_config(w.scenario)["health"],
    output:
        risk_breakpoints="processing/{name}/health/scen-{scenario}/risk_breakpoints.csv",
        cluster_cause="processing/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        cause_log="processing/{name}/health/scen-{scenario}/cause_log_breakpoints.csv",
        cluster_summary="processing/{name}/health/scen-{scenario}/cluster_summary.csv",
        clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
        cluster_risk_baseline="processing/{name}/health/scen-{scenario}/cluster_risk_baseline.csv",
        derived_tmrel="processing/{name}/health/scen-{scenario}/derived_tmrel.csv",
    log:
        "logs/{name}/prepare_health_costs_scen-{scenario}.log",
    script:
        "../scripts/prepare_health_costs.py"
