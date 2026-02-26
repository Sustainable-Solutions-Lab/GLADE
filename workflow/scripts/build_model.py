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
    sensitivity,
    trade,
    utils,
)
from workflow.scripts.constants import FEED_CATEGORIES, HA_PER_MHA, USD_TO_BNUSD
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

    validation_cfg = snakemake.params.validation  # type: ignore[attr-defined]
    use_actual_production = bool(validation_cfg["use_actual_production"])
    enforce_baseline = bool(validation_cfg["enforce_baseline_diet"])
    enforce_baseline_feed = bool(validation_cfg["enforce_baseline_feed"])
    # Enable land slack if explicitly requested or when using actual production
    enable_land_slack = bool(validation_cfg["land_slack"]) or use_actual_production
    validation_slack_cost = float(
        validation_cfg["slack_marginal_cost"]
    )  # Already in bn USD
    grassland_yield_multiplier = float(validation_cfg["grassland_yield_multiplier"])

    grassland_cal_cfg = snakemake.params.grazing["grassland_forage_calibration"]
    grassland_cal_path = (
        snakemake.input.get("grassland_calibration")
        if grassland_cal_cfg["enabled"]
        else None
    )

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
        valid = residue_yields.dropna(subset=["feed_item"])
        valid = valid[valid["feed_item"].astype(str).str.len() > 0]
        valid = valid.assign(
            crop=valid["crop"].astype(str),
            water_supply=valid["water_supply"].astype(str),
            region=valid["region"].astype(str),
            resource_class=valid["resource_class"].astype(int),
            residue_yield_t_per_ha=pd.to_numeric(
                valid["residue_yield_t_per_ha"], errors="coerce"
            ),
        )
        valid = valid.dropna(subset=["residue_yield_t_per_ha"])
        residue_lookup: dict[tuple[str, str, str, int], dict[str, float]] = {}
        for (
            crop_name,
            water_supply,
            region,
            resource_class,
            feed_item,
            residue_yield,
        ) in valid[
            [
                "crop",
                "water_supply",
                "region",
                "resource_class",
                "feed_item",
                "residue_yield_t_per_ha",
            ]
        ].itertuples(index=False, name=None):
            key = (crop_name, water_supply, region, int(resource_class))
            if key not in residue_lookup:
                residue_lookup[key] = {}
            residue_lookup[key][str(feed_item)] = float(residue_yield)
    else:
        residue_feed_items = []
        residue_lookup = {}

    # Read feed baseline (per-country, per-product, per-feed-category)
    feed_baseline = read_csv(snakemake.input.feed_baseline)

    # Read feed requirements for animal products (feed pools -> foods)
    feed_to_products = read_csv(snakemake.input.feed_to_products)
    feed_to_products["efficiency"] = pd.to_numeric(
        feed_to_products["efficiency"], errors="coerce"
    )
    if feed_to_products["efficiency"].isna().any():
        raise ValueError("feed_to_animal_products.csv contains non-numeric efficiency")

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

    # Read yields data and harvested area for each crop and water supply.
    # Harvested area is joined into the yields table at load time so that
    # downstream code can always assume a ``harvested_area`` column exists.
    # Crops without GAEZ harvested-area data (e.g. biomass-sorghum,
    # silage-maize) genuinely have zero recorded harvest; the column is
    # filled with 0.0 for those crops.
    yields_data: dict[str, pd.DataFrame] = {}
    for crop in snakemake.params.crops:
        expected_supplies = ["r"]
        if crop in expected_irrigated_crops:
            expected_supplies.append("i")

        for ws in expected_supplies:
            yields_key = f"{crop}_yield_{ws}"
            yields_df, _ = utils._load_crop_yield_table(snakemake.input[yields_key])

            harvest_key = f"{crop}_harvested_{ws}"
            harvest_df, _ = utils._load_crop_yield_table(snakemake.input[harvest_key])

            if not harvest_df.empty and "harvested_area" in harvest_df.columns:
                yields_df = yields_df.join(harvest_df["harvested_area"], how="left")
                yields_df["harvested_area"] = yields_df["harvested_area"].fillna(0.0)
            else:
                yields_df["harvested_area"] = 0.0

            yields_data[yields_key] = yields_df

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
    baseline_land_df = cropland_baseline_df.reindex(
        combined_index, fill_value=0.0
    ).astype(float)

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
    current_grassland_area_series: pd.Series | None = None
    marginal_grassland_area_series: pd.Series | None = None
    convertible_grassland_area_series: pd.Series | None = None
    if snakemake.params.grazing["enabled"]:
        grassland_df = read_csv(
            snakemake.input.grassland_yields, index_col=["region", "resource_class"]
        ).sort_index()
        grassland_df["yield"] = pd.to_numeric(grassland_df["yield"], errors="coerce")
        if grassland_yield_multiplier != 1.0:
            grassland_df["yield"] = grassland_df["yield"] * grassland_yield_multiplier
            logger.info(
                "Applied validation grassland yield multiplier: %.3f",
                grassland_yield_multiplier,
            )
        if grassland_cal_path:
            grassland_cal = read_csv(grassland_cal_path)
            gc_region = grassland_df.reset_index()
            _r2c = regions_df.set_index("region")["country"]
            gc_region["country"] = gc_region["region"].map(_r2c)
            gc_merged = gc_region.merge(
                grassland_cal[["country", "yield_correction"]],
                on="country",
                how="left",
            )
            gc_merged["yield_correction"] = gc_merged["yield_correction"].fillna(1.0)
            grassland_df["yield"] = (
                gc_merged.set_index(["region", "resource_class"])["yield_correction"]
                * grassland_df["yield"]
            ).values
            n_corrected = int((gc_merged["yield_correction"] < 1.0).sum())
            logger.info(
                "Applied grassland yield corrections: %d/%d regions adjusted",
                n_corrected,
                len(gc_merged),
            )
        current_grassland_area_df = read_csv(snakemake.input.current_grassland_area)
        if not current_grassland_area_df.empty:
            current_grassland_area_series = (
                current_grassland_area_df.set_index(["region", "resource_class"])[
                    "area_ha"
                ]
                .astype(float)
                .sort_index()
            )
        grazing_only_area_df = read_csv(snakemake.input.grazing_only_land)
        if not grazing_only_area_df.empty:
            marginal_grassland_area_series = (
                grazing_only_area_df.set_index(["region", "resource_class"])["area_ha"]
                .astype(float)
                .sort_index()
            )

        if current_grassland_area_series is not None:
            current_aligned = current_grassland_area_series.copy()
            if marginal_grassland_area_series is not None:
                combined_index = current_aligned.index.union(
                    marginal_grassland_area_series.index
                )
                current_aligned = current_aligned.reindex(
                    combined_index, fill_value=0.0
                )
                marginal_aligned = marginal_grassland_area_series.reindex(
                    combined_index, fill_value=0.0
                )
                overshoot_mask = marginal_aligned > current_aligned
                if overshoot_mask.any():
                    logger.warning(
                        "Clipping %d (region, class) entries where grazing-only area "
                        "exceeds current grassland area",
                        int(overshoot_mask.sum()),
                    )
                marginal_aligned = marginal_aligned.clip(upper=current_aligned)
                marginal_grassland_area_series = marginal_aligned[
                    marginal_aligned > 0.0
                ].sort_index()
                convertible = current_aligned - marginal_aligned
            else:
                convertible = current_aligned

            convertible = convertible[convertible > 0.0].sort_index()
            if not convertible.empty:
                convertible_grassland_area_series = convertible
        elif marginal_grassland_area_series is not None:
            raise ValueError(
                "Grazing-only land data is available but current grassland area is empty"
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
    per_tonne_cost_fraction = float(
        snakemake.config["crop_costs"]["per_tonne_cost_fraction"]
    )

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
    enforce_biofuel_baseline = bool(biomass_cfg["enforce_baseline_demand"])

    food_crops = set(foods.loc[foods["crop"].isin(crop_list), "crop"])
    crop_to_fresh_factor = utils._fresh_mass_conversion_factors(
        edible_portion_df, moisture_df, food_crops
    )

    base_food_list = foods.loc[foods["crop"].isin(crop_list), "food"].unique().tolist()
    biofuel_baseline_df = None
    if enforce_biofuel_baseline:
        biofuel_baseline_df = read_csv(snakemake.input.biofuel_baseline)
        logger.info("Biofuel baseline: %d rows", len(biofuel_baseline_df))
    enforce_fiber_demand = bool(biomass_cfg["enforce_fiber_demand"])
    fiber_baseline_df = None
    fiber_items: set[str] = set()
    if enforce_fiber_demand:
        fiber_baseline_df = read_csv(snakemake.input.fiber_baseline)
        fiber_items = set(fiber_baseline_df["source_item"].unique())
        logger.info("Fiber baseline: %d rows", len(fiber_baseline_df))
    food_list = sorted(set(base_food_list).union(animal_product_list))
    byproduct_list = list(snakemake.params.byproducts)
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
    # wheat-germ, rice-bran). Set biomass.marginal_values_usd_per_tonne to 0 to
    # make biomass export free.
    biomass.add_biomass_infrastructure(n, cfg_countries, biomass_cfg)
    # Filter fiber-managed items out of biomass byproduct routing: when fiber
    # demand is enforced, these items must flow to fiber stores instead.
    biomass_byproducts = [b for b in byproduct_list if b not in fiber_items]
    biomass.add_biomass_byproduct_links(n, cfg_countries, biomass_byproducts)
    biomass.add_biomass_crop_links(n, cfg_countries, biomass_crop_targets)
    if enforce_biofuel_baseline:
        biomass.add_biofuel_links(n, biofuel_baseline_df)
    if enforce_fiber_demand:
        biomass.add_fiber_demand_infrastructure(n, fiber_baseline_df, cfg_countries)

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
    disable_new_cropland = bool(validation_cfg["disable_new_cropland"])
    disable_new_pasture = bool(validation_cfg["disable_new_pasture"])
    disable_spared_cropland = bool(validation_cfg["disable_spared_cropland"])
    disable_spared_grassland = bool(validation_cfg["disable_spared_grassland"])
    if use_actual_production:
        # Validation mode should reproduce today's system: no new land conversion.
        if not disable_new_cropland:
            logger.info(
                "Validation mode active: forcing disable_new_cropland=true "
                "to prevent expansion during baseline replication"
            )
        if not disable_new_pasture:
            logger.info(
                "Validation mode active: forcing disable_new_pasture=true "
                "to prevent expansion during baseline replication"
            )
        disable_new_cropland = True
        disable_new_pasture = True
        if not disable_spared_cropland:
            logger.info(
                "Validation mode active: forcing disable_spared_cropland=true "
                "to prevent sequestration credits during baseline replication"
            )
        if not disable_spared_grassland:
            logger.info(
                "Validation mode active: forcing disable_spared_grassland=true "
                "to prevent sequestration credits during baseline replication"
            )
        disable_spared_cropland = True
        disable_spared_grassland = True

    reg_limit = float(land_cfg["regional_limit"])
    land_use_cost_usd_per_ha = float(land_cfg["land_use_cost_usd_per_ha"])
    land_use_cost_bnusd_per_mha = land_use_cost_usd_per_ha * HA_PER_MHA * USD_TO_BNUSD
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
        land_use_cost_bnusd_per_mha=land_use_cost_bnusd_per_mha,
        disable_new_cropland=disable_new_cropland,
        disable_new_pasture=disable_new_pasture,
        disable_spared_grassland=disable_spared_grassland,
        existing_grassland_convertible_area=convertible_grassland_area_series,
        existing_grassland_marginal_area=marginal_grassland_area_series,
    )

    # Apply the same min_area_ha filter to land_rainfed_df so that grassland
    # links are only created for regions that have corresponding land pool buses.
    if min_area_ha > 0:
        land_rainfed_df = land_rainfed_df[land_rainfed_df["area_ha"] >= min_area_ha]

    # Rice methane factor and scaling factor for rainfed wetland rice
    rice_cfg = snakemake.params.emissions["rice"]
    rice_methane_factor = float(rice_cfg["methane_emission_factor_kg_per_ha"])
    rainfed_wetland_rice_ch4_scaling_factor = float(
        rice_cfg["rainfed_wetland_rice_ch4_scaling_factor"]
    )

    # Crop production
    crops.add_spared_land_links(
        n,
        baseline_land_df,
        luc_lef_lookup,
        disable_spared_cropland=disable_spared_cropland,
    )
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
        use_actual_production=use_actual_production,
        per_tonne_cost_fraction=per_tonne_cost_fraction,
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
            per_tonne_cost_fraction=per_tonne_cost_fraction,
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
            marginal_grassland_area=marginal_grassland_area_series,
            use_actual_production=use_actual_production,
            fix_current_production=use_actual_production,
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
        byproduct_list,
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

    # Compute trade hub positions once (shared across crop, food, and feed trade)
    hub_centers = trade.compute_trade_hubs(
        regions_df, int(snakemake.params.trade["hubs"])
    )

    # Feed trade networks (between countries via hubs)
    trade.add_feed_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        FEED_CATEGORIES,
        hub_centers=hub_centers,
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
        feed_baseline=feed_baseline,
        enforce_baseline_feed=enforce_baseline_feed,
    )

    # Add exogenous feed generators (leaves/browse, swill)
    animals.add_exogenous_feed_generators(
        n,
        feed_baseline,
        enforce_baseline_feed=enforce_baseline_feed,
    )

    # Add exogenous forage from grassland calibration (deficit countries)
    if grassland_cal_path:
        grassland_cal = read_csv(grassland_cal_path)
        exog = grassland_cal[grassland_cal["exogenous_forage_mt_dm"] > 0].copy()
        if not exog.empty:
            exog_buses = "feed:ruminant_forage:" + exog["country"]
            bus_exists = exog_buses.isin(n.buses.static.index)
            exog = exog[bus_exists.values]
            exog_buses = exog_buses[bus_exists.values]
            if not exog.empty:
                if "exogenous_forage_cal" not in n.carriers.static.index:
                    n.carriers.add("exogenous_forage_cal", unit="Mt")
                gen_names = pd.Index(
                    "supply:exogenous_forage:" + exog["country"].values,
                    dtype="object",
                )
                if enforce_baseline_feed:
                    n.generators.add(
                        gen_names,
                        bus=exog_buses.values,
                        carrier="exogenous_forage_cal",
                        p_nom=exog["exogenous_forage_mt_dm"].values,
                        p_nom_extendable=False,
                        p_min_pu=1.0,
                        p_max_pu=1.0,
                        country=exog["country"].values,
                    )
                else:
                    n.generators.add(
                        gen_names,
                        bus=exog_buses.values,
                        carrier="exogenous_forage_cal",
                        p_nom_extendable=True,
                        p_nom_max=exog["exogenous_forage_mt_dm"].values,
                        marginal_cost=0.0,
                        country=exog["country"].values,
                    )
                logger.info(
                    "Added %d exogenous forage generators (%.1f Mt DM total)",
                    len(gen_names),
                    exog["exogenous_forage_mt_dm"].sum(),
                )

    # Add feed slack generators for validation mode feasibility
    if use_actual_production or enforce_baseline_feed:
        feed_slack_cost = validation_slack_cost * float(
            validation_cfg["feed_slack_cost_factor"]
        )
        animals.add_feed_slack_generators(
            n,
            marginal_cost=feed_slack_cost,
        )

    # Nutrition constraints
    nutrition.add_food_group_buses_and_loads(
        n,
        food_group_list,
        cfg_countries,
        population,
        max_per_capita=snakemake.params.food_group_max_per_capita,
        add_slack_for_fixed_consumption=False,  # Slack handled in solve_model via linopy variables
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
        byproduct_list,
    )

    # Trade networks
    trade.add_crop_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        list(crop_list),
        hub_centers=hub_centers,
    )
    trade.add_food_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        food_list,
        hub_centers=hub_centers,
    )

    health.add_health_stores(
        n,
        snakemake.input.health_cluster_summary,
        snakemake.input.health_cluster_cause,
        snakemake.config["health"],
    )

    # Compute and store health cluster populations from country populations
    cluster_map_df = read_csv(snakemake.input.health_clusters)
    cluster_lookup = (
        cluster_map_df.set_index("country_iso3")["health_cluster"].astype(int).to_dict()
    )
    pop_df = population.reset_index()
    pop_df.columns = ["iso3", "population"]
    pop_df["cluster"] = pop_df["iso3"].str.upper().map(cluster_lookup)
    pop_df = pop_df.dropna(subset=["cluster"])
    pop_df["cluster"] = pop_df["cluster"].astype(int)
    cluster_pop = pop_df.groupby("cluster")["population"].sum().to_dict()
    n.meta["population"]["health_cluster"] = cluster_pop

    # ═══════════════════════════════════════════════════════════════
    # SENSITIVITY ADJUSTMENTS
    # ═══════════════════════════════════════════════════════════════

    sensitivity_cfg = snakemake.params.sensitivity
    if sensitivity_cfg:
        logger.info("Applying sensitivity adjustments...")
        sensitivity.apply_sensitivity_factors(n, sensitivity_cfg)

    # ═══════════════════════════════════════════════════════════════
    # EXPORT
    # ═══════════════════════════════════════════════════════════════

    logger.info("Network summary:")
    logger.info("Carriers: %d", len(n.carriers.static))
    logger.info("Buses: %d", len(n.buses.static))
    logger.info("Stores: %d", len(n.stores.static))
    logger.info("Links: %d", len(n.links.static))

    # PyPSA may keep unused optional link bus slots as empty strings.
    # Normalize these placeholders to missing values before export.
    link_bus_cols = [c for c in n.links.static.columns if c.startswith("bus")]
    for col in link_bus_cols:
        n.links.static[col] = n.links.static[col].replace("", pd.NA)

    netcdf_config = snakemake.params.netcdf
    n.export_to_netcdf(
        snakemake.output.network,
        compression=netcdf_config["compression"],
        float32=netcdf_config["float32"],
    )
