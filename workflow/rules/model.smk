# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Model building and solving rules.

Includes the main optimization model construction and solution rules,
along with helper functions for gathering input files.
"""

import itertools


def yield_inputs(wildcards):
    """Get all crop yield files for model building."""
    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = config["crops"]
    else:
        irrigated_crops = list(irr_cfg)

    return {
        f"{crop}_yield_{water_supply}": f"processing/{{name}}/crop_yields/{crop}_{water_supply}.csv"
        for crop, water_supply in (
            list(zip(config["crops"], itertools.repeat("r")))  # Rainfed
            + list(zip(irrigated_crops, itertools.repeat("i")))
        )
    }


def harvested_area_model_inputs(_wildcards):
    """Return harvested area files when actual production mode is enabled."""

    if not config["validation"]["use_actual_production"]:
        return {}

    irr_cfg = config["irrigation"]["irrigated_crops"]
    if irr_cfg == "all":
        irrigated_crops = config["crops"]
    else:
        irrigated_crops = list(irr_cfg)

    inputs = {
        f"{crop}_harvested_r": f"processing/{{name}}/harvested_area/gaez/{crop}_r.csv"
        for crop in config["crops"]
    }
    for crop in irrigated_crops:
        inputs[f"{crop}_harvested_i"] = (
            f"processing/{{name}}/harvested_area/gaez/{crop}_i.csv"
        )
    return inputs


def production_stability_build_inputs(wildcards):
    """Return production stability baseline inputs when penalty mode is l1/quadratic."""
    stability_cfg = get_effective_config(wildcards.scenario)["validation"][
        "production_stability"
    ]
    if not stability_cfg["enabled"]:
        return {}
    if stability_cfg["penalty_mode"] not in ("l1", "quadratic"):
        return {}

    inputs = {}
    if stability_cfg["crops"]["enabled"]:
        inputs["crop_production_baseline"] = (
            f"processing/{wildcards.name}/faostat_crop_production.csv"
        )
        inputs["faostat_crop_item_map"] = "data/curated/faostat_crop_item_map.csv"
    if stability_cfg["animals"]["enabled"]:
        inputs["animal_production_baseline"] = (
            f"processing/{wildcards.name}/faostat_animal_production.csv"
        )
    return inputs


rule build_model:
    input:
        unpack(yield_inputs),
        unpack(residue_yield_inputs),
        unpack(harvested_area_model_inputs),
        unpack(production_stability_build_inputs),
        fertilizer_n_rates="processing/{name}/global_fertilizer_n_rates.csv",
        foods="data/curated/foods.csv",
        moisture_content="data/curated/crop_moisture_content.csv",
        ruminant_feed_categories="processing/{name}/ruminant_feed_categories.csv",
        ruminant_feed_mapping="processing/{name}/ruminant_feed_mapping.csv",
        monogastric_feed_categories="processing/{name}/monogastric_feed_categories.csv",
        monogastric_feed_mapping="processing/{name}/monogastric_feed_mapping.csv",
        feed_to_products="processing/{name}/feed_to_animal_products.csv",
        manure_emissions="processing/{name}/manure_emission_factors.csv",
        food_groups="data/curated/food_groups.csv",
        nutrition="data/curated/nutrition.csv",
        regions="processing/{name}/regions.geojson",
        land_area_by_class="processing/{name}/land_area_by_class.csv",
        cropland_baseline="processing/{name}/cropland_baseline_by_class.csv",
        multi_cropping_area="processing/{name}/multi_cropping/eligible_area.csv",
        multi_cropping_yields="processing/{name}/multi_cropping/cycle_yields.csv",
        edible_portion="processing/{name}/fao_edible_portion.csv",
        population="processing/{name}/population.csv",
        baseline_diet="processing/{name}/dietary_intake.csv",
        food_loss_waste="processing/{name}/food_loss_waste.csv",
        costs="processing/{name}/crop_costs.csv",
        animal_costs="processing/{name}/animal_costs.csv",
        grassland_yields="processing/{name}/grassland_yields.csv",
        monthly_region_water="processing/{name}/water/monthly_region_water.csv",
        growing_season_water="processing/{name}/water/region_growing_season_water.csv",
        blue_water_availability="processing/{name}/water/blue_water_availability.csv",
        luc_carbon_coefficients="processing/{name}/luc/luc_carbon_coefficients.csv",
        current_grassland_area="processing/{name}/luc/current_grassland_area_by_class.csv",
        grazing_only_land="processing/{name}/land_grazing_only_by_class.csv",
        health_cluster_summary="processing/{name}/health/scen-{scenario}/cluster_summary.csv",
        health_cluster_cause="processing/{name}/health/scen-{scenario}/cluster_cause_baseline.csv",
        health_clusters="processing/{name}/health/scen-{scenario}/country_clusters.csv",
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
                "production_stability.py",
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
        health_reference_year=lambda w: get_effective_config(w.scenario)["health"][
            "reference_year"
        ],
        validation=lambda w: get_effective_config(w.scenario)["validation"],
        production_stability=lambda w: get_effective_config(w.scenario)["validation"][
            "production_stability"
        ],
        netcdf=lambda w: get_effective_config(w.scenario)["netcdf"],
        sensitivity=lambda w: get_effective_config(w.scenario)["sensitivity"],
        # Only used to force correct reruns when scenario_defs changes.
        scenario_hash=lambda w: scenario_override_hash(w.scenario),
    output:
        network="results/{name}/build/model_scen-{scenario}.nc",
    log:
        "logs/{name}/build_model_scen-{scenario}.log",
    script:
        "../scripts/build_model.py"


def solve_model_inputs(w):
    """Get input files for solve_model rule.

    Includes validation-specific inputs (e.g., FAO animal production data)
    only when validation mode is enabled.
    """
    inputs = {
        "network": f"results/{w.name}/build/model_scen-{w.scenario}.nc",
        "m49": "data/curated/M49-codes.csv",
        "health_risk_breakpoints": f"processing/{w.name}/health/scen-{w.scenario}/risk_breakpoints.csv",
        "health_cluster_cause": f"processing/{w.name}/health/scen-{w.scenario}/cluster_cause_baseline.csv",
        "health_cause_log": f"processing/{w.name}/health/scen-{w.scenario}/cause_log_breakpoints.csv",
        "health_cluster_summary": f"processing/{w.name}/health/scen-{w.scenario}/cluster_summary.csv",
        "health_clusters": f"processing/{w.name}/health/scen-{w.scenario}/country_clusters.csv",
        "health_derived_tmrel": f"processing/{w.name}/health/scen-{w.scenario}/derived_tmrel.csv",
        "food_groups": "data/curated/food_groups.csv",
        "baseline_diet": f"processing/{w.name}/baseline_diet.csv",
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
            f"results/{w.name}/consumer_values/utility_blocks.csv"
        )
    equal_source = eff_cfg["food_groups"]["equal_by_country_source"]
    if equal_source:
        inputs["food_group_equal"] = equal_source.format(
            name=w.name,
            scenario=w.scenario,
        )

    # Add validation-specific inputs
    if config.get("validation", {}).get("use_actual_production", False):
        inputs["animal_production"] = (
            f"processing/{w.name}/faostat_animal_production.csv"
        )
        inputs["food_loss_waste"] = f"processing/{w.name}/food_loss_waste.csv"

    # Add production stability inputs
    stability_cfg = eff_cfg["validation"]["production_stability"]
    if stability_cfg["enabled"]:
        if stability_cfg["crops"]["enabled"]:
            inputs["crop_production_baseline"] = (
                f"processing/{w.name}/faostat_crop_production.csv"
            )
            # Include FAO item mapping to aggregate crops sharing an FAO item
            inputs["faostat_crop_item_map"] = "data/curated/faostat_crop_item_map.csv"
        if stability_cfg["animals"]["enabled"]:
            inputs["animal_production_baseline"] = (
                f"processing/{w.name}/faostat_animal_production.csv"
            )
            inputs["food_loss_waste"] = f"processing/{w.name}/food_loss_waste.csv"

    return inputs


def get_solver_threads(cfg: dict) -> int:
    """Return configured solver threads as an int."""

    return int(cfg["solving"]["threads"])


def solver_options_with_threads(cfg: dict) -> dict:
    """Return solver options with a threads override applied when configured."""

    solver_name = cfg["solving"]["solver"]
    options = cfg["solving"].get(f"options_{solver_name}", {}) or {}
    threads = get_solver_threads(cfg)

    options = dict(options)
    solver_key = solver_name.lower()
    if solver_key == "gurobi":
        options["Threads"] = threads
    elif solver_key == "highs":
        options["threads"] = threads

    return options


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
        solver_threads=lambda w: get_solver_threads(get_effective_config(w.scenario)),
        solver_options=lambda w: solver_options_with_threads(
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
        food_utility_piecewise=lambda w: get_effective_config(w.scenario)[
            "food_utility_piecewise"
        ],
        fix_within_group_ratios=lambda w: get_effective_config(w.scenario)[
            "food_groups"
        ]["fix_within_group_ratios"],
        sensitivity=lambda w: get_effective_config(w.scenario).get("sensitivity", {}),
        # Only used to force correct reruns when scenario_defs changes.
        scenario_hash=lambda w: scenario_override_hash(w.scenario),
    output:
        network="results/{name}/solved/model_scen-{scenario}.nc",
    log:
        "logs/{name}/solve_model_scen-{scenario}.log",
    script:
        "../scripts/solve_model.py"
