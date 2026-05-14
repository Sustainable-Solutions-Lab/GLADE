# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Dietary intake and baseline diet estimation rules.

Includes GDD survey processing, FAOSTAT supplements, food loss/waste,
GBD dietary risk exposure, and per-food baseline diet estimation.
"""


rule prepare_gdd_ia_dietary_intake:
    """Process Global Dietary Database — Integrated Assessment (GDD-IA) dataset.

    Reads the parallel grams and kcal CSVs, maps prim/prcd categories to
    food-opt food groups (butter+cream folded into dairy as
    milk-equivalent; fat_ani, fruits_starch, seafood, alcohol etc. left
    out-of-scope), derives mass in model basis from energy
    (``g_model = kcal_ia / kcal_per_g_model_basis``), and applies a
    cooked-to-raw inflation for red_meat. Emits:

      - gdd_ia_dietary_intake.csv: per-(country, group) intake (g/d)
      - gdd_ia_kcal_target.csv: per-country all-fg / OOS / target
        kcal/d for the anchor-aware normalisation in
        ``estimate_baseline_diet``.
    """
    input:
        grams=f"data/manually_downloaded/GDD-IA-intake_grams_{config['baseline_year']}.csv",
        kcal=f"data/manually_downloaded/GDD-IA-intake_kcals_{config['baseline_year']}.csv",
        food_groups="data/curated/food_groups.csv",
        nutrition="data/curated/nutrition.csv",
    params:
        countries=config["countries"],
        food_groups=config["food_groups"]["included"],
        reference_year=config["baseline_year"],
        cooked_to_raw=config["diet"]["gdd_ia"]["cooked_to_raw"],
        country_proxies=config["diet"]["gdd_ia"].get("country_proxies", {}),
    output:
        diet="<processing>/{name}/gdd_ia_dietary_intake.csv",
        kcal_target="<processing>/{name}/gdd_ia_kcal_target.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=1500,
    log:
        "<logs>/{name}/prepare_gdd_ia_dietary_intake.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gdd_ia_dietary_intake.tsv"
    script:
        "../scripts/prepare_gdd_ia_dietary_intake.py"


rule prepare_faostat_food_group_supply:
    """Prepare FAOSTAT supply data for downstream waste accounting.

    Previously also supplemented GDD dietary intake; now retained only
    as an input to ``prepare_food_loss_waste`` (dairy/oil/sugar/eggs/
    poultry supply totals used for FLW fractions). The diet pipeline
    is fully GDD-IA-driven and no longer uses this file. Uses the same
    layered FBS/FBSH/proxy fallback as ``prepare_faostat_fbs_items``.
    """
    input:
        fbs_csv="data/downloads/faostat/FBS.parquet",
        fbsh_csv="data/downloads/faostat/FBSH.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        baseline_age=config["diet"]["baseline_age"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        supply="<processing>/{name}/faostat_food_group_supply.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3300,
    log:
        "<logs>/{name}/prepare_faostat_food_group_supply.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_food_group_supply.tsv"
    script:
        "../scripts/prepare_faostat_food_group_supply.py"


rule prepare_faostat_fbs_items:
    """Prepare raw item-level supply data from FAOSTAT Food Balance Sheets.

    Reads supply data (kg/capita/year) for all items in the food item mapping,
    used for calculating within-group food consumption ratios. Uses a layered
    fallback: new FBS at the reference year -> latest available year in new
    FBS -> latest available year in historic FBSH (covers Japan, Chad, Mali,
    Benin, Togo, Burundi, etc., which are not in new FBS) -> country proxy.
    """
    input:
        food_item_map="data/curated/faostat_food_item_map.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        fbsh_csv="data/downloads/faostat/FBSH.parquet",
        m49_codes="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        reference_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
    output:
        fbs_items="<processing>/{name}/faostat_fbs_items.csv",
        fbs_provenance="<processing>/{name}/faostat_fbs_items_provenance.csv",
    group:
        "prep"
    resources:
        runtime="2m",
        mem_mb=6000,
    log:
        "<logs>/{name}/prepare_faostat_fbs_items.log",
    benchmark:
        "<benchmarks>/{name}/prepare_faostat_fbs_items.tsv"
    script:
        "../scripts/prepare_faostat_fbs_items.py"


rule prepare_nhanes_dietary_intake:
    """Parse the FPED demographic-table PDF and emit per-food-group intake
    for the United States.

    Output schema matches `gdd_dietary_intake.csv` and
    `faostat_food_group_supply.csv` so the merge step can treat NHANES as a
    drop-in source. The single "Males and females / 2 and over"
    population-mean is emitted at the configured baseline_age. The script
    also augments FPED's skim-equivalent Total Dairy with butter (FAOSTAT
    FBS item 2740 in milk-equivalent grams) so total dairy mass reflects
    all dairy products consumed, not only the low-fat fraction. Only USA
    is produced; other countries fall back to GDD/FAOSTAT.
    """
    input:
        fped_pdf=lambda wc: f"data/downloads/usda_fped/Table_1_FPED_MaleFemale_{config['diet']['nhanes']['cycle']}.pdf",
        mapping="data/curated/nhanes_fped_mapping.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        m49="data/curated/M49-codes.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
    params:
        reference_year=config["diet"]["nhanes"]["reference_year"],
        baseline_age=config["diet"]["baseline_age"],
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
    """Merge GDD-IA dietary intake with NHANES USA override.

    GDD-IA mass is already in model basis (kcal-derived). NHANES is
    already in model basis. The FAOSTAT supply file is used only as
    a fallback for the ``animal_fat`` group on countries that GDD-IA
    does not cover. Output is the merged ``dietary_intake.csv``;
    GBD anchoring and kcal normalisation happen in
    ``estimate_baseline_diet``.
    """
    input:
        gdd_ia="<processing>/{name}/gdd_ia_dietary_intake.csv",
        nhanes="<processing>/{name}/nhanes_dietary_intake.csv",
        faostat_supply="<processing>/{name}/faostat_food_group_supply.csv",
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


def _food_waste_calibration_input(_w):
    cfg = config["food_loss_waste_calibration"]
    if cfg["enabled"] and not cfg["generate"]:
        return cfg["calibration_file"]
    return []


rule prepare_food_loss_waste:
    input:
        m49="data/curated/M49-codes.csv",
        animal_production="<processing>/{name}/faostat_animal_production.csv",
        faostat_food_group_supply="<processing>/{name}/faostat_food_group_supply.csv",
        faostat_fbs_items="<processing>/{name}/faostat_fbs_items.csv",
        population="<processing>/{name}/population.csv",
        fbs_csv="data/downloads/faostat/FBS.parquet",
        sdg_csv="data/downloads/unsd/SDG_12_3_1.csv",
        overrides="data/curated/food_loss_waste_overrides.csv",
        waste_calibration=_food_waste_calibration_input,
    params:
        countries=config["countries"],
        food_groups=config["food_groups"]["included"],
        baseline_year=config["baseline_year"],
        fbs_element_code=config["data"]["faostat"]["fbs_food_supply_element_code"],
        weight_conversion=config["weight_conversion"],
        waste_calibration_food_groups=config["food_loss_waste_calibration"][
            "food_groups"
        ],
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


_food_waste_cal_cfg = config["food_loss_waste_calibration"]

if _food_waste_cal_cfg["generate"]:
    _food_waste_cal_scenario = _food_waste_cal_cfg["scenario"]

    rule compute_food_waste_calibration:
        input:
            network=f"<results>/{name}/solved/model_scen-{_food_waste_cal_scenario}.nc",
        params:
            food_groups=_food_waste_cal_cfg["food_groups"],
        output:
            calibration_file=_food_waste_cal_cfg["calibration_file"],
        resources:
            runtime="2m",
            mem_mb=2000,
        log:
            f"<logs>/{name}/compute_food_waste_calibration_scen-{_food_waste_cal_scenario}.log",
        benchmark:
            f"<benchmarks>/{name}/compute_food_waste_calibration_scen-{_food_waste_cal_scenario}.tsv"
        script:
            "../scripts/compute_food_waste_calibration.py"


rule prepare_gbd_food_group_intake:
    """Process GBD 2019 dietary risk exposure data for food group intake estimates.

    Extracts country-level dietary intake (g/day) for adults 25+ from GBD risk
    factor CSVs. Used to average with GDD estimates and for cross-validation.
    """
    input:
        gbd_dir="data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA",
    params:
        reference_year=config["baseline_year"],
    output:
        exposure="<processing>/{name}/gbd_food_group_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=500,
    log:
        "<logs>/{name}/prepare_gbd_food_group_intake.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gbd_food_group_intake.tsv"
    script:
        "../scripts/prepare_gbd_food_group_intake.py"


rule estimate_baseline_diet:
    """Estimate per-food, per-country baseline diet from multiple sources.

    Combines food group totals (GDD + GBD averaged) with FAOSTAT item-level
    supply data to disaggregate group totals into per-food consumption estimates.
    """
    input:
        dietary_intake="<processing>/{name}/dietary_intake.csv",
        gbd_exposure="<processing>/{name}/gbd_food_group_intake.csv",
        kcal_target="<processing>/{name}/gdd_ia_kcal_target.csv",
        nutrition="data/curated/nutrition.csv",
        fbs_items="<processing>/{name}/faostat_fbs_items.csv",
        crop_production="<processing>/{name}/faostat_crop_production.csv",
        animal_production="<processing>/{name}/faostat_animal_production.csv",
        # Supply-side per-(country, crop) FRT target_production_tonnes;
        # consumed by the fruits FRT sub-projection so demand attribution
        # mirrors supply within the FRT pool (citrus/mango/watermelon/apple).
        frt_attribution="<processing>/{name}/frt_area_attribution.csv",
        # Population by country for the dairy-buffalo cap (converts per-
        # capita intake to mass for comparison against domestic production).
        population="<processing>/{name}/population.csv",
        food_item_map="data/curated/faostat_food_item_map.csv",
        qcl_resolution="data/curated/faostat_food_qcl_resolution.csv",
        food_groups="data/curated/food_groups.csv",
        food_basis="data/curated/food_basis.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
        source_basis_country_overrides="data/curated/diet_source_basis_overrides.csv",
    params:
        reference_year=config["baseline_year"],
        baseline_age=config["diet"]["baseline_age"],
        food_groups_included=config["food_groups"]["included"],
        byproducts=config["byproducts"],
        fbs_override_foods=config["diet"]["fbs_override_foods"],
        gbd_anchored_groups=config["health"]["risk_factors"],
        source_basis=config["diet"]["source_basis"],
        weight_conversion=config["weight_conversion"],
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
        gbd_exposure="<processing>/{name}/gbd_food_group_intake.csv",
        source_basis_country_overrides="data/curated/diet_source_basis_overrides.csv",
    params:
        countries=config["countries"],
        risk_factors=config["health"]["risk_factors"],
        source_basis=config["diet"]["source_basis"],
        weight_conversion=config["weight_conversion"],
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
    """Compare baseline-diet kcal totals against FAOSTAT
    dietary-energy anchors and emit a per-country status report.

    Status categories (evaluated in order — first match wins):
    - no-anchor: ADER missing from FAOSTAT FS (small territories).
    - below-MDER: baseline < 0.85 x MDER (physically implausible at
                  population scale; investigate the diet pipeline).
    - above-DES:  baseline > 1.05 x DES (exceeds food-system supply).
    - low:  baseline < 0.70 x ADER (severe survey under-reporting but
            not physically impossible — already cleared the MDER floor).
    - high: baseline > 1.40 x ADER (already cleared the DES ceiling).
    - ok:   baseline within [0.70, 1.40] x ADER.
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
