# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build PyPSA network model for global food systems optimization.

This script orchestrates the construction of a complete food systems model
by loading data and calling functions from the build_model package modules.
"""

import functools
import logging

import geopandas as gpd
import pandas as pd
import pypsa

from workflow.scripts.build_model import (
    animals,
    biomass,
    crops,
    food,
    grassland,
    health,
    infrastructure,
    land,
    nutrition,
    primary_resources,
    production_stability,
    trade,
    utils,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.snakemake_utils import apply_scenario_config

if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    class _CarrierUnitWarningFilter(logging.Filter):
        """Drop noisy PyPSA carrier unit warnings."""

        _prefix = (
            "The attribute 'unit' is a standard attribute for other components "
            "but not for carriers."
        )

        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            return not (
                record.name == "pypsa.network.transform"
                and isinstance(message, str)
                and message.startswith(self._prefix)
            )

    logging.getLogger("pypsa.network.transform").addFilter(_CarrierUnitWarningFilter())

    # Apply scenario config overrides based on wildcard
    apply_scenario_config(snakemake.config, snakemake.wildcards.scenario)

    read_csv = functools.partial(pd.read_csv, comment="#")

    validation_cfg = snakemake.config["validation"]  # type: ignore[attr-defined]
    use_actual_production = bool(validation_cfg["use_actual_production"])
    enforce_baseline = bool(validation_cfg["enforce_gdd_baseline"])
    # Enable land slack if explicitly requested or when using actual production
    enable_land_slack = bool(validation_cfg["land_slack"]) or use_actual_production
    validation_slack_cost = float(
        validation_cfg["slack_marginal_cost"]
    )  # Already in bn USD

    # ═══════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════════

    # Read fertilizer N application rates (kg N/ha/year for high-input agriculture)
    fertilizer_n_rates = read_csv(snakemake.input.fertilizer_n_rates, index_col="crop")[
        "n_rate_kg_per_ha"
    ].to_dict()

    # Read food conversion data
    foods = read_csv(snakemake.input.foods)
    if not foods.empty:
        foods["food"] = foods["food"].astype(str).str.strip()
        foods["crop"] = foods["crop"].astype(str).str.strip()
        foods["factor"] = pd.to_numeric(foods["factor"], errors="coerce")
    edible_portion_df = read_csv(snakemake.input.edible_portion)
    moisture_df = read_csv(snakemake.input.moisture_content)

    # Read food groups data
    food_groups = read_csv(snakemake.input.food_groups)

    # Read nutrition data
    nutrition_data = read_csv(snakemake.input.nutrition)
    nutrition_data["nutrient"] = nutrition_data["nutrient"].replace("kcal", "cal")
    nutrition_data = nutrition_data.set_index(["food", "nutrient"])

    # Read categorized feed data
    ruminant_feed_categories = read_csv(snakemake.input.ruminant_feed_categories)
    ruminant_feed_mapping = read_csv(snakemake.input.ruminant_feed_mapping)
    monogastric_feed_categories = read_csv(snakemake.input.monogastric_feed_categories)
    monogastric_feed_mapping = read_csv(snakemake.input.monogastric_feed_mapping)

    # Read crop residue yields (may be empty if no residues available)
    residue_tables = {
        str(key): path
        for key, path in snakemake.input.items()
        if str(key).startswith("residue_")
    }
    residue_frames: list[pd.DataFrame] = []
    for path in residue_tables.values():
        df = read_csv(path)
        if not df.empty:
            residue_frames.append(df)

    if residue_frames:
        residue_yields = pd.concat(residue_frames, ignore_index=True)
        residue_feed_items = (
            residue_yields["feed_item"]
            .dropna()
            .astype(str)
            .sort_values()
            .unique()
            .tolist()
        )
        residue_lookup = {}
        for row in residue_yields.itertuples(index=False):
            feed_item = getattr(row, "feed_item", "")
            if not isinstance(feed_item, str) or not feed_item:
                continue
            key = (
                str(row.crop),
                str(row.water_supply),
                str(row.region),
                int(row.resource_class),
            )
            residue_lookup.setdefault(key, {})[feed_item] = float(
                getattr(row, "residue_yield_t_per_ha", 0.0)
            )
    else:
        residue_feed_items = []
        residue_lookup = {}

    # Read feed requirements for animal products (feed pools -> foods)
    feed_to_products = read_csv(snakemake.input.feed_to_products)

    # Read manure emission factors (CH4 and N2O)
    manure_emissions = read_csv(snakemake.input.manure_emissions)

    # Read food loss & waste fractions per country and food group
    food_loss_waste = read_csv(snakemake.input.food_loss_waste)
    if not food_loss_waste.empty:
        food_loss_waste["country"] = food_loss_waste["country"].astype(str).str.upper()
        food_loss_waste["food_group"] = food_loss_waste["food_group"].astype(str)

    irrigation_cfg = snakemake.config["irrigation"]["irrigated_crops"]  # type: ignore[index]
    if irrigation_cfg == "all":
        expected_irrigated_crops = set(snakemake.params.crops)
    else:
        expected_irrigated_crops = set(map(str, irrigation_cfg))

    # Read yields data for each configured crop and water supply
    yields_data: dict[str, pd.DataFrame] = {}
    for crop in snakemake.params.crops:
        expected_supplies = ["r"]
        if crop in expected_irrigated_crops:
            expected_supplies.append("i")

        for ws in expected_supplies:
            yields_key = f"{crop}_yield_{ws}"
            yields_df, _ = utils._load_crop_yield_table(snakemake.input[yields_key])
            yields_data[yields_key] = yields_df

    harvested_area_data: dict[str, pd.DataFrame] = {}
    if use_actual_production:
        for crop in snakemake.params.crops:
            expected_supplies = ["r"]
            if crop in expected_irrigated_crops:
                expected_supplies.append("i")
            for ws in expected_supplies:
                harvest_key = f"{crop}_harvested_{ws}"
                path = snakemake.input[harvest_key]
                harvest_df, _ = utils._load_crop_yield_table(path)
                harvested_area_data[harvest_key] = harvest_df

    # Read regions
    regions_df = gpd.read_file(snakemake.input.regions)

    # Load class-level land areas
    land_class_df = read_csv(snakemake.input.land_area_by_class)
    # Expect columns: region, water_supply, resource_class, area_ha
    land_class_df = land_class_df.set_index(
        ["region", "water_supply", "resource_class"]
    ).sort_index()

    cropland_baseline_df = read_csv(snakemake.input.cropland_baseline)
    if cropland_baseline_df.empty:
        cropland_baseline_df = pd.DataFrame(
            columns=["region", "water_supply", "resource_class", "area_ha"]
        )
    cropland_baseline_df = cropland_baseline_df.set_index(
        ["region", "water_supply", "resource_class"]
    ).sort_index()

    combined_index = land_class_df.index.union(cropland_baseline_df.index)
    land_class_df = land_class_df.reindex(combined_index, fill_value=0.0)
    baseline_land_df = (
        cropland_baseline_df.reindex(combined_index, fill_value=0.0)
        .astype(float)
        .rename(columns={"area_ha": "area_ha"})
    )

    multi_cropping_area_df = read_csv(snakemake.input.multi_cropping_area)
    multi_cropping_cycle_df = read_csv(snakemake.input.multi_cropping_yields)

    luc_lef_lookup = pd.DataFrame(
        columns=["region", "resource_class", "water_supply", "use", "lef"]
    )
    ch4_to_co2_factor = float(snakemake.params.emissions["ch4_to_co2_factor"])
    n2o_to_co2_factor = float(snakemake.params.emissions["n2o_to_co2_factor"])
    try:
        luc_coefficients_path = snakemake.input.luc_carbon_coefficients
        luc_coeff_df = read_csv(luc_coefficients_path)
        if not luc_coeff_df.empty:
            luc_lef_lookup = utils._build_luc_lef_lookup(luc_coeff_df)
            logger.info(
                "Loaded LUC LEFs for %d (region, class, water, use) combinations",
                len(luc_lef_lookup),
            )
        else:
            logger.warning(
                "LUC carbon coefficients file is empty; skipping LUC emission adjustments"
            )
    except (AttributeError, FileNotFoundError) as e:
        logger.info(
            "LUC carbon coefficients not available (%s); skipping LUC emission adjustments",
            type(e).__name__,
        )

    land_rainfed_df = land_class_df.xs("r", level="water_supply").copy()
    grassland_df = pd.DataFrame()
    current_grassland_area_df: pd.DataFrame | None = None
    grazing_only_area_series: pd.Series | None = None
    if snakemake.params.grazing["enabled"]:
        grassland_df = read_csv(
            snakemake.input.grassland_yields, index_col=["region", "resource_class"]
        ).sort_index()
        if use_actual_production:
            current_grassland_area_df = read_csv(snakemake.input.current_grassland_area)
        grazing_only_area_df = read_csv(snakemake.input.grazing_only_land)
        if not grazing_only_area_df.empty:
            grazing_only_area_series = (
                grazing_only_area_df.set_index(["region", "resource_class"])["area_ha"]
                .astype(float)
                .sort_index()
            )

    blue_water_availability_df = read_csv(snakemake.input.blue_water_availability)
    monthly_region_water_df = read_csv(snakemake.input.monthly_region_water)
    region_growing_water_df = read_csv(snakemake.input.growing_season_water)

    logger.info(
        "Loaded blue water availability data: %d basin-month pairs",
        len(blue_water_availability_df),
    )
    logger.info(
        "Loaded monthly region water availability: %d rows",
        len(monthly_region_water_df),
    )
    logger.info(
        "Loaded region growing-season water availability: %d regions",
        region_growing_water_df.shape[0],
    )

    # Load population per country for planning horizon
    population_df = read_csv(snakemake.input.population)
    # Expect columns: iso3, country, year, population
    # Select only configured countries and validate coverage
    cfg_countries = list(snakemake.params.countries)
    population = (
        population_df.set_index("iso3")["population"]
        .reindex(cfg_countries)
        .astype(float)
    )
    planning_year = int(population_df["year"].iloc[0])

    health_reference_year = int(snakemake.params.health_reference_year)

    region_to_country = regions_df.set_index("region")["country"]
    # Warn if any configured countries are missing from regions
    present_countries = set(region_to_country.unique())
    missing_in_regions = [c for c in cfg_countries if c not in present_countries]
    if missing_in_regions:
        logger.warning(
            "Configured countries missing from regions and may be disconnected: %s",
            ", ".join(sorted(missing_in_regions)),
        )
    # Keep only regions whose country is in configured countries
    region_to_country = region_to_country[region_to_country.isin(cfg_countries)]

    regions = sorted(region_to_country.index.unique())

    region_water_limits = (
        region_growing_water_df.set_index("region")["growing_season_water_available_m3"]
        .reindex(regions)
        .fillna(0.0)
    )

    irrigated_regions: set[str] = set()
    for key, df in yields_data.items():
        if key.endswith("_yield_i"):
            irrigated_regions.update(df.index.get_level_values("region"))

    land_regions = set(land_class_df.index.get_level_values("region"))
    water_bus_regions = sorted(
        set(region_water_limits.index)
        .union(irrigated_regions)
        .intersection(land_regions)
    )

    logger.debug("Foods data:\n%s", foods.head())
    logger.debug("Food groups data:\n%s", food_groups.head())
    logger.debug("Nutrition data:\n%s", nutrition_data.head())

    # Read USDA production costs (USD/ha in base year dollars)
    costs_df = read_csv(snakemake.input.costs)
    base_year = int(snakemake.config["currency_base_year"])
    cost_per_year_column = f"cost_per_year_usd_{base_year}_per_ha"
    cost_per_planting_column = f"cost_per_planting_usd_{base_year}_per_ha"

    crop_costs_per_year = costs_df.set_index("crop")[cost_per_year_column].astype(float)
    crop_costs_per_planting = costs_df.set_index("crop")[
        cost_per_planting_column
    ].astype(float)

    # Read animal production costs (USD/Mt in base year dollars)
    animal_costs_df = read_csv(snakemake.input.animal_costs)
    cost_per_mt_column = f"cost_per_mt_usd_{base_year}"
    animal_costs_per_mt = animal_costs_df.set_index("product")[
        cost_per_mt_column
    ].astype(float)

    grazing_cost_per_tonne_dm = grassland.calculate_grazing_cost_per_tonne_dm(
        animal_costs_df, feed_to_products, base_year
    )

    # ═══════════════════════════════════════════════════════════════
    # NETWORK BUILDING
    # ═══════════════════════════════════════════════════════════════

    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.name = "food-opt"

    # Store population in network metadata for consistent access in solve and analysis
    n.meta["population"] = {
        "country": population.to_dict(),
        "year": planning_year,
    }

    crop_list = snakemake.params.crops
    animal_products_cfg = snakemake.params.animal_products
    animal_product_list = list(animal_products_cfg["include"])
    biomass_cfg = snakemake.params.biomass
    biomass_crop_targets_cfg = [str(crop).strip() for crop in biomass_cfg["crops"]]
    biomass_crop_targets = sorted(
        {crop for crop in biomass_crop_targets_cfg if crop in crop_list}
    )

    food_crops = set(foods.loc[foods["crop"].isin(crop_list), "crop"])
    crop_to_fresh_factor = utils._fresh_mass_conversion_factors(
        edible_portion_df, moisture_df, food_crops
    )

    base_food_list = foods.loc[foods["crop"].isin(crop_list), "food"].unique().tolist()
    food_list = sorted(set(base_food_list).union(animal_product_list))
    food_groups_clean = food_groups.dropna(subset=["food", "group"]).copy()
    food_groups_clean["food"] = food_groups_clean["food"].astype(str).str.strip()
    food_groups_clean["group"] = food_groups_clean["group"].astype(str).str.strip()
    food_to_group = (
        food_groups_clean.drop_duplicates(subset=["food"])
        .set_index("food")["group"]
        .to_dict()
    )
    food_group_list = list(snakemake.params.food_groups)

    macronutrient_cfg = snakemake.params.macronutrients
    nutrient_units = (
        nutrition_data.reset_index()
        .drop_duplicates(subset=["nutrient"])
        .set_index("nutrient")["unit"]
        .to_dict()
    )
    # All nutrients from nutrition data get buses (tracked but not necessarily constrained)
    all_nutrient_names = list(nutrient_units.keys())

    # Infrastructure: carriers and buses
    infrastructure.add_carriers_and_buses(
        n,
        crop_list,
        food_list,
        residue_feed_items,
        food_group_list,
        all_nutrient_names,
        nutrient_units,
        cfg_countries,
        regions,
        water_bus_regions,
    )

    # Biomass infrastructure and routing.
    # Creates country-level biomass buses and sinks (negative generators) that
    # allow crops and byproducts to be exported to the energy sector. This also
    # provides a disposal route for byproducts that lack feed mappings (e.g.
    # wheat-germ, rice-bran). Set marginal_cost to 0 to make biomass export free.
    biomass.add_biomass_infrastructure(n, cfg_countries, biomass_cfg)
    biomass.add_biomass_byproduct_links(n, cfg_countries, snakemake.params.byproducts)
    biomass.add_biomass_crop_links(n, cfg_countries, biomass_crop_targets)

    # Primary resources: water, fertilizer, emissions
    water_slack_cost = validation_slack_cost / 1e3

    primary_resources.add_primary_resources(
        n,
        snakemake.params.fertilizer,
        region_water_limits,
        ch4_to_co2_factor,
        n2o_to_co2_factor,
        use_actual_production=use_actual_production,
        water_slack_cost=water_slack_cost,
    )
    synthetic_n2o_factor = float(
        snakemake.params.emissions["fertilizer"]["synthetic_n2o_factor"]
    )
    indirect_ef4 = float(snakemake.params.emissions["fertilizer"]["indirect_ef4"])
    indirect_ef5 = float(snakemake.params.emissions["fertilizer"]["indirect_ef5"])
    frac_gasf = float(snakemake.params.emissions["fertilizer"]["frac_gasf"])
    frac_leach = float(snakemake.params.emissions["fertilizer"]["frac_leach"])
    primary_resources.add_fertilizer_distribution_links(
        n,
        cfg_countries,
        synthetic_n2o_factor,
        indirect_ef4,
        indirect_ef5,
        frac_gasf,
        frac_leach,
    )

    land_cfg = snakemake.params.land
    reg_limit = float(land_cfg["regional_limit"])
    filtering_cfg = land_cfg["filtering"]
    min_crop_yield = float(filtering_cfg["min_crop_yield_t_per_ha"])
    min_grassland_yield = float(filtering_cfg["min_grassland_yield_t_per_ha"])
    min_area_ha = float(filtering_cfg["min_area_ha"])
    land.add_land_components(
        n,
        land_class_df,
        baseline_land_df,
        luc_lef_lookup,
        reg_limit=reg_limit,
        land_slack_cost=validation_slack_cost,  # Use unified validation slack cost
        enable_land_slack=enable_land_slack,
        min_area_ha=min_area_ha,
        disable_new_cropland=bool(validation_cfg["disable_new_cropland"]),
        disable_new_pasture=bool(validation_cfg["disable_new_pasture"]),
    )

    # Apply the same min_area_ha filter to land_rainfed_df so that grassland
    # links are only created for regions that have corresponding land pool buses.
    if min_area_ha > 0:
        land_rainfed_df = land_rainfed_df[land_rainfed_df["area_ha"] >= min_area_ha]

    # Existing pasture buses (grazing-only land)
    existing_pasture_bus_names: list[str] = []
    if grazing_only_area_series is not None and not grazing_only_area_series.empty:
        existing_pasture_bus_names = [
            f"land:existing_pasture:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        pasture_regions = [region for region, _cls in grazing_only_area_series.index]
        n.carriers.add("land_existing_pasture", unit="Mha")
        n.buses.add(
            existing_pasture_bus_names,
            carrier="land_existing_pasture",
            region=pasture_regions,
        )
        pasture_gen_names = [
            f"supply:land_existing_pasture:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        n.generators.add(
            pasture_gen_names,
            bus=existing_pasture_bus_names,
            carrier="land_existing_pasture",
            p_nom_extendable=True,
            p_nom_max=(reg_limit * grazing_only_area_series.values / 1e6),
            region=pasture_regions,
        )
        if enable_land_slack:
            primary_resources._add_land_slack_generators(
                n, existing_pasture_bus_names, validation_slack_cost
            )

        # Add marginal_to_pasture links: existing_pasture → pasture pool
        # Ensure pasture pool buses exist for regions that only have marginal land
        n.carriers.add("marginal_to_pasture", unit="Mha")
        pasture_pool_buses = [
            f"land:pasture:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        missing_pasture = [
            (bus, region)
            for bus, region in zip(pasture_pool_buses, pasture_regions)
            if bus not in n.buses.static.index
        ]
        if missing_pasture:
            missing_names, missing_regions = zip(*missing_pasture)
            if "land_pasture" not in n.carriers.static.index:
                n.carriers.add("land_pasture", unit="Mha")
            n.buses.add(
                list(missing_names),
                carrier="land_pasture",
                region=list(missing_regions),
            )
        marginal_to_pasture_names = [
            f"use:marginal_to_pasture:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        marginal_area_mha = reg_limit * grazing_only_area_series.values / 1e6
        n.links.add(
            marginal_to_pasture_names,
            carrier="marginal_to_pasture",
            bus0=existing_pasture_bus_names,
            bus1=pasture_pool_buses,
            efficiency=1.0,
            p_nom_extendable=True,
            p_nom_max=marginal_area_mha,
            region=pasture_regions,
        )

        # Add spare_marginal links: existing_pasture → spared (with sequestration)
        n.carriers.add("spare_marginal", unit="Mha")
        n.carriers.add("spared_marginal", unit="Mha")
        spare_marginal_names = [
            f"spare:marginal:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        spared_marginal_buses = [
            f"land:spared_marginal:{region}_c{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        n.buses.add(
            spared_marginal_buses, carrier="spared_marginal", region=pasture_regions
        )
        # Get spared LEF for each region/class (use rainfed water supply)
        spare_df = pd.DataFrame(
            {
                "region": [r for r, _ in grazing_only_area_series.index],
                "resource_class": [int(c) for _, c in grazing_only_area_series.index],
                "water_supply": "r",
            }
        )
        spare_lefs = utils.merge_lef(
            spare_df, luc_lef_lookup, "spared", allow_missing=True
        ).to_numpy()
        n.links.add(
            spare_marginal_names,
            carrier="spare_marginal",
            bus0=existing_pasture_bus_names,
            bus1=spared_marginal_buses,
            efficiency=1.0,
            bus2="emission:co2",
            # tCO2/ha = MtCO2/Mha numerically; spared LEFs are negative
            efficiency2=spare_lefs,
            p_nom_extendable=True,
            p_nom_max=marginal_area_mha,
            region=pasture_regions,
        )
        n.stores.add(
            spared_marginal_buses,
            bus=spared_marginal_buses,
            carrier="spared_marginal",
            e_nom_extendable=True,
            region=pasture_regions,
        )

    # Rice methane factor and scaling factor for rainfed wetland rice
    rice_cfg = snakemake.params.emissions["rice"]
    rice_methane_factor = float(rice_cfg["methane_emission_factor_kg_per_ha"])
    rainfed_wetland_rice_ch4_scaling_factor = float(
        rice_cfg["rainfed_wetland_rice_ch4_scaling_factor"]
    )

    # Crop production
    crops.add_spared_land_links(n, baseline_land_df, luc_lef_lookup)
    crops.add_regional_crop_production_links(
        n,
        crop_list,
        yields_data,
        region_to_country,
        set(cfg_countries),
        crop_costs_per_year,
        crop_costs_per_planting,
        fertilizer_n_rates,
        rice_methane_factor=rice_methane_factor,
        rainfed_wetland_rice_ch4_scaling_factor=rainfed_wetland_rice_ch4_scaling_factor,
        residue_lookup=residue_lookup,
        harvested_area_data=harvested_area_data if use_actual_production else None,
        use_actual_production=use_actual_production,
        min_yield_t_per_ha=min_crop_yield,
    )
    # Multi-cropping is disabled when running with actual production
    enable_multiple_cropping = bool(snakemake.params.multiple_cropping) and (
        not use_actual_production
        and not validation_cfg["production_stability"]["enabled"]
    )
    if enable_multiple_cropping:
        crops.add_multi_cropping_links(
            n,
            multi_cropping_area_df,
            multi_cropping_cycle_df,
            region_to_country,
            set(cfg_countries),
            crop_costs_per_year,
            crop_costs_per_planting,
            fertilizer_n_rates,
            residue_lookup,
            min_yield_t_per_ha=min_crop_yield,
        )
    elif use_actual_production:
        logger.info("Skipping multiple cropping links under actual production mode")
    if snakemake.params.grazing["enabled"]:
        grassland.add_grassland_feed_links(
            n,
            grassland_df,
            land_rainfed_df,
            region_to_country,
            set(cfg_countries),
            marginal_cost=grazing_cost_per_tonne_dm,
            current_grassland_area=current_grassland_area_df,
            pasture_land_area=grazing_only_area_series,
            use_actual_production=use_actual_production,
            pasture_utilization_rate=float(
                snakemake.params.grazing["pasture_utilization_rate"]
            ),
            min_yield_t_per_ha=min_grassland_yield,
        )

    # Food conversion
    food.add_food_conversion_links(
        n,
        food_list,
        foods,
        cfg_countries,
        crop_to_fresh_factor,
        food_to_group,
        food_loss_waste,
        snakemake.params.crops,
        snakemake.params.byproducts,
    )

    # Feed supply
    food.add_feed_supply_links(
        n,
        ruminant_feed_categories,
        ruminant_feed_mapping,
        monogastric_feed_categories,
        monogastric_feed_mapping,
        crop_list,
        food_list,
        residue_feed_items,
        cfg_countries,
    )

    # Feed trade networks (between countries via hubs)
    # Feed categories must match infrastructure.py lines 110-120
    feed_category_list = [
        "ruminant_grassland",
        "ruminant_roughage",
        "ruminant_forage",
        "ruminant_grain",
        "ruminant_protein",
        "monogastric_low_quality",
        "monogastric_grain",
        "monogastric_energy",
        "monogastric_protein",
    ]
    trade.add_feed_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        feed_category_list,
    )

    # Crop residue soil incorporation (with N₂O emissions)
    # Process ALL residues regardless of animal type; N content from feed data
    incorporation_n2o_factor = float(
        snakemake.params.emissions["residues"]["incorporation_n2o_factor"]
    )
    crops.add_residue_soil_incorporation_links(
        n,
        residue_feed_items,
        ruminant_feed_mapping,
        ruminant_feed_categories,
        monogastric_feed_mapping,
        monogastric_feed_categories,
        cfg_countries,
        incorporation_n2o_factor,
        indirect_ef5,
        frac_leach,
    )

    # Animal production
    animals.add_feed_to_animal_product_links(
        n,
        animal_product_list,
        feed_to_products,
        ruminant_feed_categories,
        monogastric_feed_categories,
        manure_emissions,
        nutrition_data,
        snakemake.params.fertilizer,
        snakemake.params.emissions,
        cfg_countries,
        food_to_group,
        food_loss_waste,
        animal_costs_per_mt,
    )

    # Add feed slack generators for validation mode feasibility
    if use_actual_production:
        animals.add_feed_slack_generators(n, marginal_cost=validation_slack_cost)

    # Nutrition constraints
    nutrition.add_food_group_buses_and_loads(
        n,
        food_group_list,
        cfg_countries,
        population,
        max_per_capita=snakemake.params.food_group_max_per_capita,
        add_slack_for_fixed_consumption=enforce_baseline,
        slack_marginal_cost=validation_slack_cost,
    )
    nutrition.add_macronutrient_loads(
        n,
        all_nutrient_names,
        cfg_countries,
        population,
        nutrient_units,
    )
    nutrition.add_food_nutrition_links(
        n,
        food_list,
        foods,
        food_groups,
        nutrition_data,
        nutrient_units,
        cfg_countries,
        snakemake.params.byproducts,
    )

    # Trade networks
    trade.add_crop_trade_hubs_and_links(
        n, snakemake.params.trade, regions_df, cfg_countries, list(crop_list)
    )
    trade.add_food_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        food_list,
    )

    health.add_health_stores(
        n,
        snakemake.input.health_cluster_summary,
        snakemake.input.health_cluster_cause,
        snakemake.config["health"],
    )

    # Production stability stores (for l1/quadratic penalty modes)
    stability_cfg = snakemake.params.production_stability
    if stability_cfg["enabled"] and stability_cfg["penalty_mode"] in (
        "l1",
        "quadratic",
    ):
        crop_baseline = None
        crop_to_fao_item: dict[str, str] = {}
        animal_baseline = None

        if stability_cfg["crops"]["enabled"]:
            crop_baseline = read_csv(snakemake.input.crop_production_baseline)
            fao_map_df = read_csv(snakemake.input.faostat_crop_item_map)
            crop_to_fao_item = dict(
                zip(
                    fao_map_df["crop"].astype(str),
                    fao_map_df["faostat_item"].astype(str),
                )
            )

        if stability_cfg["animals"]["enabled"]:
            animal_baseline = read_csv(snakemake.input.animal_production_baseline)

        production_stability.add_production_stability_stores(
            n,
            stability_cfg,
            crop_baseline,
            animal_baseline,
            crop_to_fao_item,
            food_to_group,
            food_loss_waste,
        )

    # Compute and store health cluster populations from country populations
    cluster_map_df = read_csv(snakemake.input.health_clusters)
    cluster_lookup = (
        cluster_map_df.set_index("country_iso3")["health_cluster"].astype(int).to_dict()
    )
    cluster_pop: dict[int, float] = {}
    for iso3, pop in population.items():
        cluster = cluster_lookup.get(iso3.upper())
        if cluster is not None:
            cluster_pop[cluster] = cluster_pop.get(cluster, 0.0) + pop
    n.meta["population"]["health_cluster"] = cluster_pop

    # ═══════════════════════════════════════════════════════════════
    # EXPORT
    # ═══════════════════════════════════════════════════════════════

    logger.info("Network summary:")
    logger.info("Carriers: %d", len(n.carriers.static))
    logger.info("Buses: %d", len(n.buses.static))
    logger.info("Stores: %d", len(n.stores.static))
    logger.info("Links: %d", len(n.links.static))

    netcdf_config = snakemake.params.netcdf
    n.export_to_netcdf(
        snakemake.output.network,
        compression=netcdf_config["compression"],
        float32=netcdf_config["float32"],
    )
