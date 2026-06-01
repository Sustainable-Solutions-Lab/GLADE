# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Health-related data preparation rules.

Includes mortality rates, relative risks, life tables,
and health cost calculations.
"""


rule prepare_gbd_mortality:
    input:
        gbd_mortality=f"data/manually_downloaded/IHME-GBD_2023-death-rates-{config['baseline_year']}.csv",
    params:
        countries=config["countries"],
        causes=config["health"]["causes"],
        reference_year=config["baseline_year"],
    output:
        mortality="<processing>/{name}/health/gbd_mortality_rates.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/prepare_gbd_mortality.log",
    benchmark:
        "<benchmarks>/{name}/prepare_gbd_mortality.tsv"
    script:
        "../scripts/prepare_gbd_mortality.py"


rule prepare_relative_risks:
    """Build dietary RR curves from GBD 2023 Burden-of-Proof curves.

    Applies the GBD->model basis conversion, clips each curve at the curated
    TMREL, and age-expands the all-ages BoP curve with the curated
    age-attenuation table. Risks in alternative_rr use a literature log-linear
    curve instead. Emits the canonical model-basis TMREL alongside the curves.
    """
    input:
        **{f"alt_rr_{k}": v for k, v in config["health"]["alternative_rr"].items() if v},
        bop_curves="data/downloads/burden_of_proof/bop_rr_curves.csv",
        beta="data/curated/health/rr_age_attenuation.csv",
        tmrel="data/curated/health/rr_tmrel.csv",
        food_basis="data/curated/food_basis.csv",
        food_groups="data/curated/food_groups.csv",
    params:
        risk_factors=config["health"]["risk_factors"],
        risk_cause_map=config["health"]["risk_cause_map"],
        alternative_rr=config["health"]["alternative_rr"],
        source_basis=config["diet"]["source_basis"],
        weight_conversion=config["weight_conversion"],
    output:
        relative_risks="<processing>/{name}/health/relative_risks.csv",
        tmrel="<processing>/{name}/health/tmrel.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/prepare_relative_risks.log",
    benchmark:
        "<benchmarks>/{name}/prepare_relative_risks.tsv"
    script:
        "../scripts/prepare_relative_risks.py"


rule prepare_life_table:
    input:
        wpp_life_table="data/downloads/WPP_life_table.csv.gz",
    params:
        reference_year=config["baseline_year"],
    output:
        life_table="<processing>/{name}/health/life_table.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=3800,
    log:
        "<logs>/{name}/prepare_life_table.log",
    benchmark:
        "<benchmarks>/{name}/prepare_life_table.tsv"
    script:
        "../scripts/prepare_life_table.py"


rule prepare_health_costs:
    """Prepare health cost data for SOS2 linearization.

    This rule is scenario-independent: grid resolution parameters
    (intake_grid_points, log_rr_points) and clustering are config-level
    settings. Scenario-specific adjustments (rr_quantiles, value_per_yll)
    are applied downstream in build_model/solve_model.
    """
    input:
        regions="<processing>/{name}/regions.geojson",
        diet="<processing>/{name}/dietary_intake.csv",
        relative_risks="<processing>/{name}/health/relative_risks.csv",
        tmrel="<processing>/{name}/health/tmrel.csv",
        dr="<processing>/{name}/health/gbd_mortality_rates.csv",
        population="<processing>/{name}/population_age.csv",
        life_table="<processing>/{name}/health/life_table.csv",
        food_groups="data/curated/food_groups.csv",
        gdp="<processing>/{name}/gdp_per_capita.csv",
    params:
        countries=config["countries"],
        health=config["health"],
        baseline_year=config["baseline_year"],
    output:
        risk_breakpoints="<processing>/{name}/health/risk_breakpoints.csv",
        cluster_cause="<processing>/{name}/health/cluster_cause_baseline.csv",
        cause_log="<processing>/{name}/health/cause_log_breakpoints.csv",
        cluster_summary="<processing>/{name}/health/cluster_summary.csv",
        clusters="<processing>/{name}/health/country_clusters.csv",
        cluster_risk_baseline="<processing>/{name}/health/cluster_risk_baseline.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=400,
    log:
        "<logs>/{name}/prepare_health_costs.log",
    benchmark:
        "<benchmarks>/{name}/prepare_health_costs.tsv"
    script:
        "../scripts/prepare_health_costs.py"
