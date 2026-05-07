# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
        reference_year=config["baseline_year"],
        stimulant_brewed_to_dry=config["diet"]["stimulant_brewed_to_dry"],
    output:
        diet="<processing>/{name}/gdd_dietary_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1000,
    log:
        "<logs>/{name}/prepare_gdd_dietary_intake.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gdd_dietary_intake.tsv"
    script:
        "../scripts/prepare_gdd_dietary_intake.py"


rule prepare_faostat_fbs_items:
    """Prepare raw item-level supply data from FAOSTAT Food Balance Sheets.

    Reads supply data (kg/capita/year) for all items in the food item mapping
    from a bulk FBS CSV, used for calculating within-group food consumption ratios.
    """
    input:
        food_item_map="data/curated/faostat_food_item_map.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        fbs_items="<processing>/{name}/faostat_fbs_items.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3200,
    log:
        "<logs>/{name}/prepare_faostat_fbs_items.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_fbs_items.tsv"
    script:
        "../scripts/prepare_faostat_fbs_items.py"


rule prepare_faostat_gdd_supplements:
    """Prepare FAOSTAT supply data to supplement or override GDD dietary intake.

    Reads dairy, eggs, poultry, and oil supply data from FAOSTAT FBS bulk CSV
    to fill gaps in the Global Dietary Database (GDD) or replace survey-based
    values where FAOSTAT provides a more suitable validation anchor.
    """
    input:
        fbs_csv="data/downloads/faostat/FBS.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
        poultry_carcass_to_retail=config["animal_products"]["carcass_to_retail_meat"][
            "meat-chicken"
        ],
    output:
        supply="<processing>/{name}/faostat_gdd_supplements.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3300,
    log:
        "<logs>/{name}/prepare_faostat_gdd_supplements.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_gdd_supplements.tsv"
    script:
        "../scripts/prepare_faostat_gdd_supplements.py"


rule prepare_fbs_cereal_intake:
    """Aggregate FAOSTAT FBS cereal supply to per-country intake (g/day).

    Used by ``estimate_baseline_diet`` when
    ``diet.fbs_grain_supplement.enabled`` is true: refined ``grain``
    intake is anchored as ``max(0, fbs_cereal_intake - whole_grains)``,
    closing the GDD data hole on refined grain in HICs without
    disturbing the GBD-anchored ``whole_grains`` total.
    """
    input:
        fbs_csv="data/downloads/faostat/FBS.parquet",
        food_item_map="data/curated/faostat_food_item_map.csv",
        food_groups="data/curated/food_groups.csv",
        m49_codes="data/curated/M49-codes.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        cereal_intake="<processing>/{name}/fbs_cereal_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3300,
    log:
        "<logs>/{name}/prepare_fbs_cereal_intake.log",
    benchmark:
        "<benchmarks>/{name}/prepare_fbs_cereal_intake.tsv"
    script:
        "../scripts/prepare_fbs_cereal_intake.py"


rule prepare_nhanes_dietary_intake:
    """Parse the FPED demographic-table PDF and emit per-food-group intake
    for the United States.

    Output schema matches `gdd_dietary_intake.csv` and
    `faostat_gdd_supplements.csv` so the merge step can treat NHANES as a
    drop-in source. The single "Males and females / 2 and over"
    population-mean is replicated across the model's age groups (matching
    how FAOSTAT supply is propagated). The script also augments FPED's
    skim-equivalent Total Dairy with butter (FAOSTAT FBS item 2740 in
    milk-equivalent grams) so total dairy mass reflects all dairy products
    consumed, not only the low-fat fraction. Only USA is produced; other
    countries fall back to GDD/FAOSTAT.
    """
    input:
        fped_pdf=lambda wc: f"data/downloads/usda_fped/Table_1_FPED_MaleFemale_{config['diet']['nhanes']['cycle']}.pdf",
        mapping="data/curated/nhanes_fped_mapping.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        m49="data/curated/M49-codes.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
    params:
        reference_year=config["diet"]["nhanes"]["reference_year"],
        food_groups_included=config["food_groups"]["included"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
        country="USA",
    output:
        diet="<processing>/{name}/nhanes_dietary_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=300,
    log:
        "<logs>/{name}/prepare_nhanes_dietary_intake.log",
    benchmark:
        "<benchmarks>/{name}/prepare_nhanes_dietary_intake.tsv"
    script:
        "../scripts/prepare_nhanes_dietary_intake.py"


rule merge_dietary_sources:
    input:
        gdd="<processing>/{name}/gdd_dietary_intake.csv",
        faostat="<processing>/{name}/faostat_gdd_supplements.csv",
        nhanes="<processing>/{name}/nhanes_dietary_intake.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
        food_groups="data/curated/food_groups.csv",
        food_basis="data/curated/food_basis.csv",
    params:
        source_basis=config["diet"]["source_basis"],
        source_basis_country_overrides=config["diet"]["source_basis_country_overrides"],
        weight_conversion=config["diet"]["weight_conversion"],
    output:
        diet="<processing>/{name}/dietary_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/merge_dietary_sources.log",
    benchmark:
        "<benchmarks>/{name}/merge_dietary_sources.tsv"
    script:
        "../scripts/merge_dietary_sources.py"


rule prepare_food_loss_waste:
    input:
        m49="data/curated/M49-codes.csv",
        animal_production="<processing>/{name}/faostat_animal_production.csv",
        faostat_gdd_supplements="<processing>/{name}/faostat_gdd_supplements.csv",
        population="<processing>/{name}/population.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        sdg_csv="data/downloads/unsd/SDG_12_3_1.csv",
        overrides="data/curated/food_loss_waste_overrides.csv",
    params:
        countries=config["countries"],
        food_groups=config["food_groups"]["included"],
        baseline_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3300,
    log:
        "<logs>/{name}/prepare_food_loss_waste.log",
    benchmark:
        "<benchmarks>/{name}/prepare_food_loss_waste.tsv"
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
        reference_year=config["baseline_year"],
    output:
        exposure="<processing>/{name}/gbd_dietary_risk_exposure.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=500,
    log:
        "<logs>/{name}/prepare_gbd_dietary_risk_exposure.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gbd_dietary_risk_exposure.tsv"
    script:
        "../scripts/prepare_gbd_dietary_risk_exposure.py"


rule estimate_baseline_diet:
    """Estimate per-food, per-country baseline diet from multiple sources.

    Combines food group totals (GDD + GBD averaged) with FAOSTAT item-level
    supply data to disaggregate group totals into per-food consumption estimates.
    """
    input:
        dietary_intake="<processing>/{name}/dietary_intake.csv",
        gbd_exposure="<processing>/{name}/gbd_dietary_risk_exposure.csv",
        fbs_items="<processing>/{name}/faostat_fbs_items.csv",
        fbs_cereal_intake="<processing>/{name}/fbs_cereal_intake.csv",
        crop_production="<processing>/{name}/faostat_crop_production.csv",
        animal_production="<processing>/{name}/faostat_animal_production.csv",
        food_item_map="data/curated/faostat_food_item_map.csv",
        qcl_resolution="data/curated/faostat_food_qcl_resolution.csv",
        food_groups="data/curated/food_groups.csv",
        food_basis="data/curated/food_basis.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
    params:
        reference_year=config["baseline_year"],
        baseline_age=config["diet"]["baseline_age"],
        food_groups_included=config["food_groups"]["included"],
        byproducts=config["byproducts"],
        fbs_override_foods=config["diet"]["fbs_override_foods"],
        carcass_to_retail_meat=config["animal_products"]["carcass_to_retail_meat"],
        risk_group_anchor=config["diet"]["risk_group_anchor"],
        fbs_grain_supplement=config["diet"]["fbs_grain_supplement"],
        source_basis=config["diet"]["source_basis"],
        source_basis_country_overrides=config["diet"]["source_basis_country_overrides"],
        weight_conversion=config["diet"]["weight_conversion"],
    output:
        baseline_diet="<processing>/{name}/baseline_diet.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/estimate_baseline_diet.log",
    benchmark:
        "<benchmarks>/{name}/estimate_baseline_diet.tsv"
    script:
        "../scripts/estimate_baseline_diet.py"


rule prepare_food_security_anchors:
    """Per-country dietary energy anchors (ADER/MDER/DES) from FAOSTAT FS.

    Used by validate_baseline_diet to flag countries whose GDD-derived
    baseline-diet kcal totals are implausible relative to physiological
    requirements (MDER) or food-system supply (DES).
    """
    input:
        fs="data/downloads/faostat/FS.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
    output:
        anchors="<processing>/{name}/food_security_anchors.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=500,
    log:
        "<logs>/{name}/prepare_food_security_anchors.log",
    benchmark:
        "<benchmarks>/{name}/prepare_food_security_anchors.tsv"
    script:
        "../scripts/prepare_food_security_anchors.py"


rule compare_baseline_diet_to_gbd:
    """Compare per-country GBD-risk-factor consumption in the baseline
    diet against GBD's own intake estimates, after applying the same
    cooked-to-dry conversion the pipeline uses.

    This is a consistency check for the health module: if the model's
    baseline-diet intake of a risk factor differs dramatically from
    GBD's intake estimate for the same country, the attributable
    disease burden the model computes will diverge from what GBD
    itself estimates for that country.
    """
    input:
        baseline_diet="<processing>/{name}/baseline_diet.csv",
        food_groups="data/curated/food_groups.csv",
        food_basis="data/curated/food_basis.csv",
        gbd_exposure="<processing>/{name}/gbd_dietary_risk_exposure.csv",
    params:
        countries=config["countries"],
        risk_factors=config["health"]["risk_factors"],
        source_basis=config["diet"]["source_basis"],
        source_basis_country_overrides=config["diet"]["source_basis_country_overrides"],
        weight_conversion=config["diet"]["weight_conversion"],
    output:
        report="<processing>/{name}/baseline_diet_risk_comparison.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/compare_baseline_diet_to_gbd.log",
    benchmark:
        "<benchmarks>/{name}/compare_baseline_diet_to_gbd.tsv"
    script:
        "../scripts/compare_baseline_diet_to_gbd.py"


rule validate_baseline_diet:
    """Compare GDD-derived baseline-diet kcal totals against FAOSTAT
    dietary-energy anchors and emit a per-country status report.

    Status categories:
    - ok: GDD total within [0.7, 1.4] x ADER and consistent with MDER/DES.
    - low: 0.7 x ADER >= GDD > 0.85 x MDER (consistent with severe survey
           under-reporting but not physically impossible).
    - below-MDER: GDD < 0.85 x MDER (physically implausible at population
                  scale; investigate the GDD pipeline for that country).
    - high: GDD > 1.4 x ADER but <= 1.05 x DES (high but supportable).
    - above-DES: GDD > 1.05 x DES (exceeds food-system availability,
                 implies upstream over-projection).
    - no-anchor: country missing from FAOSTAT FS (small territories).
    """
    input:
        baseline_diet="<processing>/{name}/baseline_diet.csv",
        anchors="<processing>/{name}/food_security_anchors.csv",
        nutrition="data/curated/nutrition.csv",
    params:
        countries=config["countries"],
    output:
        report="<processing>/{name}/baseline_diet_validation.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/validate_baseline_diet.log",
    benchmark:
        "<benchmarks>/{name}/validate_baseline_diet.tsv"
    script:
        "../scripts/validate_baseline_diet.py"
