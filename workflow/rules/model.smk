# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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
    """Get all crop yield files for model building."""
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = config["crops"]
    else:
        irrigated_crops = list(irr_cfg)

    return {
        f"{crop}_yield_{water_supply}": f"<processing>/{{name}}/crop_yields/{crop}_{water_supply}.csv"
        for crop, water_supply in (
            list(zip(config["crops"], itertools.repeat("r")))  # Rainfed
            + list(zip(irrigated_crops, itertools.repeat("i")))
        )
    }


def harvested_area_model_inputs(wildcards):
    """Return harvested area files for all crops.

    Harvested area comes from the same GAEZ source as yields and is always
    loaded so that ``baseline_area_mha`` can be computed on every crop
    production link.
    """
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = config["crops"]
    else:
        irrigated_crops = list(irr_cfg)

    inputs = {
        f"{crop}_harvested_r": f"<processing>/{{name}}/harvested_area/gaez/{crop}_r.csv"
        for crop in config["crops"]
    }
    for crop in irrigated_crops:
        inputs[f"{crop}_harvested_i"] = (
            f"<processing>/{{name}}/harvested_area/gaez/{crop}_i.csv"
        )
    return inputs


def build_model_biofuel_baseline_input(wildcards):
    """Conditionally include biofuel baseline and biogas demand data."""
    eff = get_effective_config(wildcards.scenario)
    inputs = {}
    if eff["biomass"]["enforce_baseline_demand"]:
        inputs["biofuel_baseline"] = "<processing>/{name}/biofuel_baseline.csv"
        biogas_path = eff["biomass"]["biogas_crop_demand"]
        if biogas_path:
            inputs["biogas_demand"] = biogas_path
    return inputs


def build_model_fiber_baseline_input(wildcards):
    """Conditionally include fiber baseline data when enforce_fiber_demand is true."""
    eff = get_effective_config(wildcards.scenario)
    if eff["biomass"]["enforce_fiber_demand"]:
        return {"fiber_baseline": "<processing>/{name}/fiber_baseline.csv"}
    return {}


def build_model_grassland_calibration_input(wildcards):
    """Conditionally include grassland forage calibration CSV."""
    cal_cfg = config["grazing"]["grassland_forage_calibration"]
    if cal_cfg["generate"]:
        if wildcards.scenario == cal_cfg["scenario"]:
            return {}  # Source scenario: don't include its own calibration
        # Check effective config to avoid circular DAG dependencies
        eff = get_effective_config(wildcards.scenario)
        if not eff["grazing"]["grassland_forage_calibration"]["enabled"]:
            return {}
        return {"grassland_calibration": cal_cfg["source"]}
    elif cal_cfg["enabled"]:
        return {"grassland_calibration": cal_cfg["source"]}
    return {}


def build_model_fodder_yield_correction_input(wildcards):
    """Conditionally include fodder yield correction CSV."""
    if config["fodder_decomposition"]["yield_corrections"]["enabled"]:
        return {
            "fodder_yield_corrections": "<processing>/{name}/fodder_yield_corrections.csv"
        }
    return {}


rule build_model:
    input:
        unpack(yield_inputs),
        unpack(residue_yield_inputs),
        unpack(harvested_area_model_inputs),
        unpack(build_model_grassland_calibration_input),
        unpack(build_model_fodder_yield_correction_input),
        unpack(build_model_biofuel_baseline_input),
        unpack(build_model_fiber_baseline_input),
        feed_baseline="<processing>/{name}/feed_baseline.csv",
        feed_to_products="<processing>/{name}/feed_to_animal_products.csv",
        fertilizer_n_rates="<processing>/{name}/global_fertilizer_n_rates.csv",
        foods="data/curated/foods.csv",
        moisture_content="data/curated/crop_moisture_content.csv",
        ruminant_feed_categories="<processing>/{name}/ruminant_feed_categories.csv",
        ruminant_feed_mapping="<processing>/{name}/ruminant_feed_mapping.csv",
        monogastric_feed_categories="<processing>/{name}/monogastric_feed_categories.csv",
        monogastric_feed_mapping="<processing>/{name}/monogastric_feed_mapping.csv",
        manure_emissions="<processing>/{name}/manure_emission_factors.csv",
        food_groups="data/curated/food_groups.csv",
        nutrition="data/curated/nutrition.csv",
        regions="<processing>/{name}/regions.geojson",
        land_area_by_class="<processing>/{name}/land_area_by_class.csv",
        cropland_baseline="<processing>/{name}/cropland_baseline_by_class.csv",
        multi_cropping_area="<processing>/{name}/multi_cropping/eligible_area.csv",
        multi_cropping_yields="<processing>/{name}/multi_cropping/cycle_yields.csv",
        edible_portion="<processing>/{name}/fao_edible_portion.csv",
        population="<processing>/{name}/population.csv",
        baseline_diet="<processing>/{name}/dietary_intake.csv",
        food_loss_waste="<processing>/{name}/food_loss_waste.csv",
        costs="<processing>/{name}/crop_costs.csv",
        animal_costs="<processing>/{name}/animal_costs.csv",
        grassland_yields="<processing>/{name}/grassland_yields.csv",
        monthly_region_water="<processing>/{name}/water/monthly_region_water.csv",
        growing_season_water="<processing>/{name}/water/region_growing_season_water.csv",
        blue_water_availability="<processing>/{name}/water/blue_water_availability.csv",
        luc_carbon_coefficients="<processing>/{name}/luc/luc_carbon_coefficients.csv",
        current_grassland_area="<processing>/{name}/luc/current_grassland_area_by_class.csv",
        grazing_only_land="<processing>/{name}/land_grazing_only_by_class.csv",
        health_cluster_summary="<processing>/{name}/health/cluster_summary.csv",
        health_cluster_cause="<processing>/{name}/health/cluster_cause_baseline.csv",
        health_clusters="<processing>/{name}/health/country_clusters.csv",
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
                "sensitivity.py",
                "trade.py",
                "utils.py",
            ],
        ),
        constants_script="workflow/scripts/constants.py",
    params:
        crops=lambda w: get_effective_config(w.scenario)["crops"],
        multiple_cropping=lambda w: get_effective_config(w.scenario)[
            "multiple_cropping"
        ],
        countries=lambda w: get_effective_config(w.scenario)["countries"],
        land=lambda w: get_effective_config(w.scenario)["land"],
        fertilizer=lambda w: get_effective_config(w.scenario)["fertilizer"],
        residues=lambda w: get_effective_config(w.scenario)["residues"],
        biomass=lambda w: get_effective_config(w.scenario)["biomass"],
        emissions=lambda w: get_effective_config(w.scenario)["emissions"],
        food_groups=lambda w: get_effective_config(w.scenario)["food_groups"][
            "included"
        ],
        food_group_constraints=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["constraints"],
        food_group_max_per_capita=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["max_per_capita"],
        macronutrients=lambda w: get_effective_config(w.scenario)["macronutrients"],
        diet=lambda w: get_effective_config(w.scenario)["diet"],
        byproducts=lambda w: get_effective_config(w.scenario)["byproducts"],
        animal_products=lambda w: get_effective_config(w.scenario)["animal_products"],
        trade=lambda w: get_effective_config(w.scenario)["trade"],
        grazing=lambda w: get_effective_config(w.scenario)["grazing"],
        baseline_year=lambda w: get_effective_config(w.scenario)["baseline_year"],
        validation=lambda w: get_effective_config(w.scenario)["validation"],
        production_stability=lambda w: get_effective_config(w.scenario)["validation"][
            "production_stability"
        ],
        netcdf=lambda w: get_effective_config(w.scenario)["netcdf"],
        sensitivity=lambda w: get_effective_config(w.scenario)["sensitivity"],
        # Only used to force correct reruns when scenario definitions change.
        scenario_hash=lambda w: scenario_override_hash(w.scenario),
    output:
        network="<results>/{name}/build/model_scen-{scenario}.nc",
    group:
        "build_model"
    resources:
        runtime="1m",
        mem_mb=900,
    log:
        "<logs>/{name}/build_model_scen-{scenario}.log",
    benchmark:
        "<benchmarks>/{name}/build_model_scen-{scenario}.tsv"
    script:
        "../scripts/build_model.py"


def solve_model_inputs(w):
    """Get input files for solve_model rule."""
    inputs = {
        "network": f"<results>/{w.name}/build/model_scen-{w.scenario}.nc",
        "m49": "data/curated/M49-codes.csv",
        "health_risk_breakpoints": f"<processing>/{w.name}/health/risk_breakpoints.csv",
        "health_cluster_cause": f"<processing>/{w.name}/health/cluster_cause_baseline.csv",
        "health_cause_log": f"<processing>/{w.name}/health/cause_log_breakpoints.csv",
        "health_cluster_summary": f"<processing>/{w.name}/health/cluster_summary.csv",
        "health_clusters": f"<processing>/{w.name}/health/country_clusters.csv",
        "health_derived_tmrel": f"<processing>/{w.name}/health/derived_tmrel.csv",
        "health_cluster_risk_baseline": f"<processing>/{w.name}/health/cluster_risk_baseline.csv",
        "food_groups": "data/curated/food_groups.csv",
        "baseline_diet": f"<processing>/{w.name}/baseline_diet.csv",
    }

    # Add food incentives input if enabled for this scenario
    eff_cfg = get_effective_config(w.scenario)
    if eff_cfg["food_incentives"]["enabled"]:
        sources = eff_cfg["food_incentives"]["sources"]
        if not sources:
            raise ValueError("food_incentives enabled but sources is empty")
        inputs["food_incentives"] = [
            source.format(name=w.name, scenario=w.scenario) for source in sources
        ]
    utility_cfg = eff_cfg["food_utility_piecewise"]
    if utility_cfg["enabled"]:
        inputs["food_utility_piecewise"] = (
            f"<results>/{w.name}/consumer_values/utility_blocks.csv"
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
        production_stability=lambda w: get_effective_config(w.scenario)["validation"][
            "production_stability"
        ],
        animal_growth_cap=lambda w: get_effective_config(w.scenario)["validation"][
            "animal_growth_cap"
        ],
        food_utility_piecewise=lambda w: get_effective_config(w.scenario)[
            "food_utility_piecewise"
        ],
        fix_within_group_ratios=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["fix_within_group_ratios"],
        sensitivity=lambda w: get_effective_config(w.scenario).get("sensitivity", {}),
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
