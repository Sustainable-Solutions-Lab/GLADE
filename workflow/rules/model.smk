# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Model building and solving rules.

Includes the main optimization model construction and solution rules,
along with helper functions for gathering input files.
"""

import itertools
import math


def yield_inputs(wildcards):
    """Get all crop yield files for model building.

    Yield CSVs share one schema regardless of source: GAEZ-backed crops get
    them from build_crop_yields, CROPGRIDS-backed crops (config["cropgrids_crops"])
    from build_crop_yields_cropgrids. Cropgrids-backed crops are rainfed-only.
    """
    return {
        f"{crop}_yield_{water_supply}": f"<processing>/{{name}}/crop_yields/{crop}_{water_supply}.csv"
        for crop, water_supply in (
            list(zip(config["crops"], itertools.repeat("r")))  # Rainfed
            + list(zip(irrigated_crops(), itertools.repeat("i")))
        )
    }


def harvested_area_model_inputs(wildcards):
    """Return harvested area files for all crops.

    Both GAEZ and CROPGRIDS-backed crops write into the same
    ``harvested_area/gaez/{crop}_{water_supply}.csv`` path so downstream
    consumers see a uniform layout. Cropgrids-backed crops are rainfed-only.
    """
    inputs = {
        f"{crop}_harvested_r": f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"
        for crop in config["crops"]
    }
    for crop in irrigated_crops():
        inputs[f"{crop}_harvested_i"] = (
            f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
        )
    return inputs


def build_model_biofuel_baseline_input(wildcards):
    """Conditionally include biofuel baseline and biogas demand data."""
    inputs = {}
    if config["biomass"]["enforce_baseline_demand"]:
        inputs["biofuel_baseline"] = "<processing>/{name}/biofuel_baseline.csv"
        biogas_path = config["biomass"]["biogas_crop_demand"]
        if biogas_path:
            inputs["biogas_demand"] = biogas_path
    return inputs


def build_model_fiber_baseline_input(wildcards):
    """Conditionally include fiber baseline data when enforce_fiber_demand is true."""
    if config["biomass"]["enforce_fiber_demand"]:
        return {"fiber_baseline": "<processing>/{name}/fiber_baseline.csv"}
    return {}


def build_model_grassland_calibration_input(wildcards):
    """Grassland forage calibration is now applied at solve time.

    This stub remains to avoid breaking unpack() calls in build_model inputs
    while the transition settles.
    """
    return {}


def build_model_fodder_yield_correction_input(wildcards):
    """Conditionally include fodder yield correction CSV."""
    if config["fodder_decomposition"]["yield_corrections"]["enabled"]:
        return {
            "fodder_yield_corrections": "<processing>/{name}/fodder_yield_corrections.csv"
        }
    return {}


def build_model_yield_calibration_input(wildcards):
    """Conditionally include the FBS-corrected FAOSTAT yield calibration CSV.

    Only required when the calibration is enabled AND
    ``validation.use_actual_yields`` is true; in optimisation mode the
    GAEZ potential yields are used as-is.
    """
    if not config["yield_calibration"]["enabled"]:
        return {}
    if not config["validation"]["use_actual_yields"]:
        return {}
    return {"yield_calibration": "<processing>/{name}/yield_calibration.csv"}


def build_model_cost_calibration_input(wildcards):
    """Conditionally include cost calibration CSVs (crops, grassland, animals).

    When ``generate`` is true, calibration is produced from a solved model
    that depends on this build, so we exclude it to break the DAG cycle.
    """
    cal_cfg = config["cost_calibration"]
    if cal_cfg["generate"]:
        return {}
    if cal_cfg["enabled"]:
        return {
            "crop_cost_calibration": cal_cfg["crop_correction_csv"],
            "grassland_cost_calibration": cal_cfg["grassland_correction_csv"],
            "animal_cost_calibration": cal_cfg["animal_correction_csv"],
        }
    return {}


rule build_model:
    input:
        unpack(yield_inputs),
        unpack(residue_yield_inputs),
        unpack(harvested_area_model_inputs),
        unpack(build_model_grassland_calibration_input),
        unpack(build_model_fodder_yield_correction_input),
        unpack(build_model_yield_calibration_input),
        unpack(build_model_cost_calibration_input),
        unpack(build_model_biofuel_baseline_input),
        unpack(build_model_fiber_baseline_input),
        feed_baseline="<processing>/{name}/feed_baseline.csv",
        feed_to_products="<processing>/{name}/feed_to_animal_products.csv",
        fertilizer_n_rates="<processing>/{name}/global_fertilizer_n_rates.csv",
        foods="data/curated/foods.csv",
        moisture_content="data/curated/crop_moisture_content.csv",
        seed_rates="data/curated/seed_rates.csv",
        ruminant_feed_categories="<processing>/{name}/ruminant_feed_categories.csv",
        ruminant_feed_mapping="<processing>/{name}/ruminant_feed_mapping.csv",
        monogastric_feed_categories="<processing>/{name}/monogastric_feed_categories.csv",
        monogastric_feed_mapping="<processing>/{name}/monogastric_feed_mapping.csv",
        manure_emissions="<processing>/{name}/manure_emission_factors.csv",
        food_groups="data/curated/food_groups.csv",
        food_basis="data/curated/food_basis.csv",
        nutrition="data/curated/nutrition.csv",
        regions="<processing>/{name}/regions.geojson",
        land_area_by_class="<processing>/{name}/land_area_by_class.csv",
        cropland_baseline="<processing>/{name}/cropland_baseline_by_class.csv",
        multi_cropping_area="<processing>/{name}/multi_cropping/eligible_area.csv",
        multi_cropping_yields="<processing>/{name}/multi_cropping/cycle_yields.csv",
        edible_portion="<processing>/{name}/fao_edible_portion.csv",
        population="<processing>/{name}/population.csv",
        baseline_diet="<processing>/{name}/dietary_intake.csv",
        baseline_diet_validation="<processing>/{name}/baseline_diet_validation.csv",
        # GBD baseline-diet consistency check; only built when health is on.
        baseline_diet_risk_comparison=(
            "<processing>/{name}/baseline_diet_risk_comparison.csv"
            if health_required()
            else []
        ),
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
        costs="<processing>/{name}/faostat_crop_costs.csv",
        animal_costs="<processing>/{name}/animal_costs.csv",
        grassland_yields="<processing>/{name}/grassland_yields.csv",
        monthly_region_water="<processing>/{name}/water/monthly_region_water.csv",
        growing_season_water="<processing>/{name}/water/region_growing_season_water.csv",
        blue_water_availability="<processing>/{name}/water/blue_water_availability.csv",
        luc_carbon_coefficients="<processing>/{name}/luc/luc_carbon_coefficients.csv",
        faostat_pasture_area="<processing>/{name}/faostat_pasture_area.csv",
        current_grassland_area="<processing>/{name}/luc/current_grassland_area_by_class.csv",
        grazing_only_land="<processing>/{name}/land_grazing_only_by_class.csv",
        # Health-cluster stores are only added when health is enabled (in the
        # base config or any scenario, since the build is scenario-independent).
        health_cluster_summary=(
            "<processing>/{name}/health/cluster_summary.csv"
            if health_required()
            else []
        ),
        health_cluster_cause=(
            "<processing>/{name}/health/cluster_cause_baseline.csv"
            if health_required()
            else []
        ),
        health_clusters=(
            "<processing>/{name}/health/country_clusters.csv"
            if health_required()
            else []
        ),
        build_scripts=expand(
            "workflow/scripts/build_model/{script}",
            script=[
                "animals.py",
                "biomass.py",
                "health.py",
                "crops.py",
                "food.py",
                "grassland.py",
                "infrastructure.py",
                "land.py",
                "nutrition.py",
                "primary_resources.py",
                "trade.py",
                "utils.py",
            ],
        ),
        constants_script="workflow/scripts/constants.py",
    params:
        crops=config["crops"],
        multiple_cropping=config["multiple_cropping"],
        countries=config["countries"],
        land=config["land"],
        fertilizer=config["fertilizer"],
        residues=config["residues"],
        biomass=config["biomass"],
        emissions=config["emissions"],
        food_groups=config["food_groups"]["included"],
        food_group_constraints=config["food_groups"]["constraints"],
        food_group_max_per_capita=config["food_groups"]["max_per_capita"],
        macronutrients=config["macronutrients"],
        diet=config["diet"],
        byproducts=config["byproducts"],
        animal_products=config["animal_products"],
        commodities=config["commodities"],
        grazing=config["grazing"],
        baseline_year=config["baseline_year"],
        validation=config["validation"],
        deviation_penalty=config["deviation_penalty"],
        netcdf=config["netcdf"],
        # Add health-cluster stores when health is enabled in the base config or
        # any scenario (the build is shared across scenarios).
        health_enabled=health_required(),
    output:
        network="<results>/{name}/build/model.nc",
    group:
        "build_model"
    resources:
        runtime="1m",
        mem_mb=900,
    log:
        "<logs>/{name}/build_model.log",
    benchmark:
        "<benchmarks>/{name}/build_model.tsv"
    script:
        "../scripts/build_model.py"


def solve_model_inputs(w):
    """Get input files for solve_model rule.

    NOTE: Also update tools/export-solve-manifest when changing these inputs.
    """
    inputs = {
        "network": f"<results>/{w.name}/build/model.nc",
        "m49": "data/curated/M49-codes.csv",
        "food_groups": "data/curated/food_groups.csv",
        "baseline_diet": f"<processing>/{w.name}/baseline_diet.csv",
    }

    eff_cfg = get_effective_config(w.scenario)

    # Health processing inputs are only consumed when this scenario enables
    # health (add_health_objective / post-hoc evaluation). Omitted otherwise so
    # the solve needs none of the GBD-derived health artefacts.
    if eff_cfg["health"]["enabled"]:
        inputs.update(
            {
                "health_risk_breakpoints": f"<processing>/{w.name}/health/risk_breakpoints.csv",
                "health_cluster_cause": f"<processing>/{w.name}/health/cluster_cause_baseline.csv",
                "health_cause_log": f"<processing>/{w.name}/health/cause_log_breakpoints.csv",
                "health_cluster_summary": f"<processing>/{w.name}/health/cluster_summary.csv",
                "health_clusters": f"<processing>/{w.name}/health/country_clusters.csv",
                "health_tmrel": f"<processing>/{w.name}/health/tmrel.csv",
                "health_cluster_risk_baseline": f"<processing>/{w.name}/health/cluster_risk_baseline.csv",
            }
        )

    # Add food incentives input if enabled for this scenario
    if eff_cfg["food_incentives"]["enabled"]:
        sources = eff_cfg["food_incentives"]["sources"]
        if not sources:
            raise ValueError("food_incentives enabled but sources is empty")
        inputs["food_incentives"] = [
            source.format(name=w.name, scenario=w.scenario) for source in sources
        ]
    utility_cfg = eff_cfg["food_utility_piecewise"]
    if utility_cfg["enabled"]:
        baseline_name = eff_cfg["consumer_values"]["baseline_scenario"]
        inputs["food_utility_piecewise"] = (
            f"<results>/{w.name}/consumer_values/{baseline_name}/utility_blocks.csv"
        )
    equal_source = eff_cfg["food_groups"]["equal_by_country_source"]
    if equal_source:
        inputs["food_group_equal"] = equal_source.format(
            name=w.name,
            scenario=w.scenario,
        )
    macronutrient_cfg = eff_cfg["macronutrients"]
    if any(
        isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        for bounds in macronutrient_cfg.values()
    ):
        inputs["nutrition"] = "data/curated/nutrition.csv"

    # Grassland forage calibration: include CSVs when enabled for this scenario.
    # When generate=true, the calibration CSVs are produced from the source
    # scenario's solve, so non-source scenarios depend on them via the DAG.
    # The source scenario has enabled=false and skips this block.
    cal_cfg = eff_cfg["grazing"]["grassland_forage_calibration"]
    if cal_cfg["enabled"]:
        inputs["grassland_yield_correction"] = cal_cfg["grassland_yield_correction"]
        inputs["fodder_conversion_correction"] = cal_cfg["fodder_conversion_correction"]
        inputs["exogenous_forage"] = cal_cfg["exogenous_forage"]

    # Protein-feed calibration: per-country exogenous protein supply CSV.
    # Same gating pattern as the forage calibration.
    exo_feed_cal_cfg = eff_cfg["exogenous_feed_calibration"]
    if exo_feed_cal_cfg["enabled"]:
        inputs["exogenous_feed"] = exo_feed_cal_cfg["exogenous_feed"]

    # Food demand calibration: include the per-food multiplier CSV when
    # enabled. The generation config sets enabled=false to break the DAG
    # cycle on the source scenario; solve_model then treats the calibration
    # as absent (multiplier=1).
    fd_cal_cfg = eff_cfg["food_demand_calibration"]
    if fd_cal_cfg["enabled"]:
        inputs["food_demand_calibration"] = fd_cal_cfg["calibration_file"]

    # Deviation-penalty L1 calibration: include the calibrated YAML when
    # any component's l1_cost is set to the "calibrated" sentinel. The
    # solve resolves the sentinel via
    # solve_model/production_stability.resolve_calibrated_l1_costs.
    dp_cfg = eff_cfg["deviation_penalty"]
    dp_cal_cfg = dp_cfg["calibration"]
    if dp_cal_cfg["enabled"] and deviation_penalty_uses_calibrated(dp_cfg):
        inputs["deviation_penalty_calibration"] = dp_cal_cfg["calibrated_yaml"]

    return inputs


def get_solver_threads(cfg: dict) -> int:
    """Return configured solver threads as an int."""

    return int(cfg["solving"]["threads"])


def solver_options_with_overrides(cfg: dict) -> dict:
    """Return solver options with threads and time-limit overrides applied."""

    solver_name = cfg["solving"]["solver"]
    options = cfg["solving"].get(f"options_{solver_name}", {}) or {}
    threads = get_solver_threads(cfg)
    time_limit = cfg["solving"]["time_limit"]

    options = dict(options)
    solver_key = solver_name.lower()
    if solver_key == "gurobi":
        options["Threads"] = threads
        if time_limit is not None:
            options["TimeLimit"] = time_limit * 60
    elif solver_key == "highs":
        options["threads"] = threads
        if time_limit is not None:
            options["time_limit"] = time_limit * 60

    return options


def solve_model_runtime(wildcards, attempt: int) -> int:
    """Scale solve runtime in minutes aggressively on retries (x5 per retry).

    When a solver time_limit is configured, cap the escalated runtime at
    time_limit + 10 minutes (overhead for model I/O and export) so retries
    don't balloon far beyond the solver's own cutoff.
    """

    cfg = get_effective_config(wildcards.scenario)["solving"]
    base_runtime = cfg["runtime"]
    escalated = base_runtime * (5 ** (attempt - 1))
    time_limit = cfg["time_limit"]
    if time_limit is not None:
        return min(escalated, time_limit + 10)
    return escalated


def solve_model_mem_mb(wildcards, attempt: int) -> int:
    """Scale solve memory moderately on retries (~30% per retry)."""

    base_mem_mb = get_effective_config(wildcards.scenario)["solving"]["mem_mb"]
    return math.ceil(base_mem_mb * (1.3 ** (attempt - 1)))


# NOTE: When changing inputs or params on solve_model, also update
# tools/export-solve-manifest which mirrors these for the HPC manifest.
rule solve_model:
    input:
        unpack(solve_model_inputs),
    threads: lambda w: get_solver_threads(get_effective_config(w.scenario))
    params:
        health_enabled=lambda w: get_effective_config(w.scenario)["health"]["enabled"],
        health_risk_factors=lambda w: get_effective_config(w.scenario)["health"][
            "risk_factors"
        ],
        health_risk_cause_map=lambda w: get_effective_config(w.scenario)["health"][
            "risk_cause_map"
        ],
        health_value_per_yll=lambda w: get_effective_config(w.scenario)["health"][
            "value_per_yll"
        ],
        ghg_price=lambda w: get_effective_config(w.scenario)["emissions"]["ghg_price"],
        solver=lambda w: get_effective_config(w.scenario)["solving"]["solver"],
        solver_options=lambda w: solver_options_with_overrides(
            get_effective_config(w.scenario)
        ),
        io_api=lambda w: get_effective_config(w.scenario)["solving"]["io_api"],
        calculate_fixed_duals=lambda w: get_effective_config(w.scenario)["solving"][
            "calculate_fixed_duals"
        ],
        netcdf=lambda w: get_effective_config(w.scenario)["netcdf"],
        macronutrients=lambda w: get_effective_config(w.scenario)["macronutrients"],
        food_group_constraints=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["constraints"],
        enforce_baseline=lambda w: get_effective_config(w.scenario)["validation"][
            "enforce_baseline_diet"
        ],
        deviation_penalty=lambda w: get_effective_config(w.scenario)[
            "deviation_penalty"
        ],
        animal_growth_cap=lambda w: get_effective_config(w.scenario)["validation"][
            "animal_growth_cap"
        ],
        crop_growth_cap=lambda w: get_effective_config(w.scenario)["validation"][
            "crop_growth_cap"
        ],
        food_utility_piecewise=lambda w: get_effective_config(w.scenario)[
            "food_utility_piecewise"
        ],
        fix_within_group_ratios=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["fix_within_group_ratios"],
        sensitivity=lambda w: get_effective_config(w.scenario)["sensitivity"],
        reforestation_cap=lambda w: get_effective_config(w.scenario)["land"][
            "reforestation_cap"
        ],
        forage_calibration_enabled=lambda w: get_effective_config(w.scenario)[
            "grazing"
        ]["grassland_forage_calibration"]["enabled"],
        forage_overlap_crops=config["grazing"]["forage_overlap_crops"],
        exogenous_feed_calibration_enabled=lambda w: get_effective_config(w.scenario)[
            "exogenous_feed_calibration"
        ]["enabled"],
        enforce_baseline_feed=config["validation"]["enforce_baseline_feed"],
        regional_limit=lambda w: get_effective_config(w.scenario)["land"][
            "regional_limit"
        ],
        biofuel_demand_scale=lambda w: get_effective_config(w.scenario)["biomass"][
            "biofuel_demand_scale"
        ],
        ghg_pricing_enabled=lambda w: get_effective_config(w.scenario)["emissions"][
            "ghg_pricing_enabled"
        ],
        food_incentives_enabled=lambda w: get_effective_config(w.scenario)[
            "food_incentives"
        ]["enabled"],
        equal_by_country_source=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["equal_by_country_source"],
        slack_marginal_cost=config["validation"]["slack_marginal_cost"],
        residue_max_feed_fraction=config["residues"]["max_feed_fraction"],
        residue_max_feed_fraction_by_region=config["residues"][
            "max_feed_fraction_by_region"
        ],
        countries=config["countries"],
        export_for_tuning=lambda w: get_effective_config(w.scenario)["solving"].get(
            "export_for_tuning", False
        ),
        # Only used to force correct reruns when scenario definitions change.
        scenario_hash=lambda w: scenario_override_hash(w.scenario),
    output:
        network="<results>/{name}/solved/model_scen-{scenario}.nc",
    retries: 2
    resources:
        runtime=solve_model_runtime,
        mem_mb=solve_model_mem_mb,
    log:
        "<logs>/{name}/solve_model_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/solve_model_scen-{scenario}.tsv"
    script:
        "../scripts/solve_model.py"
