# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build PyPSA network model for global food systems optimization.

This script orchestrates the construction of a complete food systems model
by loading data and calling functions from the build_model package modules.
"""

import functools
import logging
from pathlib import Path
import sys

import geopandas as gpd
from logging_config import setup_script_logging
import pandas as pd
import pypsa

# Ensure the project root is on sys.path so we can import the package, even when
# Snakemake runs a temporary copy of this script from .snakemake/scripts.
_script_path = Path(__file__).resolve()
try:
    _project_root = _script_path.parents[2]
except IndexError:
    _project_root = _script_path.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Import build_model package modules
from workflow.scripts.build_model import (  # noqa: E402
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
    trade,
    utils,
)
from workflow.scripts.snakemake_utils import apply_scenario_config  # noqa: E402

# Enable new PyPSA components API
pypsa.options.api.new_components_api = True


def _compute_inferred_multi_cropping_areas(
    harvested_area_data: dict[str, pd.DataFrame],
    combinations: dict[str, dict],
) -> pd.DataFrame:
    """Compute multi-cropping areas inferred from CROPGRIDS cropping intensity.

    For crops with cropping_intensity > 1, we infer that (CI - 1) * crop_area
    is being multi-cropped. This function allocates that excess area to
    configured multi-cropping combinations.

    Returns a DataFrame with columns:
        combination, region, resource_class, water_supply, inferred_area_ha
    """
    from collections import defaultdict

    # Build lookup: crop -> list of combinations containing that crop
    crop_to_combos: dict[str, list[str]] = defaultdict(list)
    combo_crops: dict[str, list[str]] = {}

    for name, entry in combinations.items():
        crops_list = [str(c) for c in entry["crops"]]
        combo_crops[name] = crops_list
        for crop in set(crops_list):
            crop_to_combos[crop].append(name)

    # Extract excess area per crop/region/class/water_supply
    excess_data: dict[tuple[str, str, int, str], float] = {}
    for key, df in harvested_area_data.items():
        # Key format: "{crop}_harvested_{ws}"
        parts = key.replace("_harvested_", "_").rsplit("_", 1)
        if len(parts) != 2:
            continue
        crop, ws = parts

        if "cropping_intensity" not in df.columns or "crop_area" not in df.columns:
            continue

        df = df.reset_index()
        for _, row in df.iterrows():
            ci = row.get("cropping_intensity", 1.0)
            crop_area = row.get("crop_area", 0.0)
            if pd.isna(ci) or pd.isna(crop_area) or ci <= 1.0 or crop_area <= 0:
                continue
            excess = (ci - 1.0) * crop_area
            region = str(row["region"])
            rc = int(row["resource_class"])
            excess_data[(crop, region, rc, ws)] = excess

    if not excess_data:
        return pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "inferred_area_ha",
            ]
        )

    # Allocate excess to combinations
    # Priority: double-crop same crop > mixed combinations
    records = []
    allocated: dict[tuple[str, str, int, str], float] = defaultdict(float)

    # First pass: allocate to same-crop combinations (e.g., double_rice)
    for combo_name, crops_list in combo_crops.items():
        unique_crops = set(crops_list)
        if len(unique_crops) != 1:
            continue  # Not a same-crop combination

        crop = next(iter(unique_crops))
        for (c, region, rc, ws), excess in excess_data.items():
            if c != crop:
                continue
            remaining = excess - allocated[(crop, region, rc, ws)]
            if remaining <= 0:
                continue
            # Allocate all remaining excess to this same-crop combination
            allocated[(crop, region, rc, ws)] += remaining
            records.append(
                {
                    "combination": combo_name,
                    "region": region,
                    "resource_class": rc,
                    "water_supply": ws,
                    "inferred_area_ha": remaining,
                }
            )

    # Second pass: allocate to mixed-crop combinations (e.g., rice_wheat)
    for combo_name, crops_list in combo_crops.items():
        unique_crops = set(crops_list)
        if len(unique_crops) == 1:
            continue  # Already handled

        # For mixed combinations, allocate min(excess of all crops)
        # Group by region/rc/ws
        location_excess: dict[tuple[str, int, str], dict[str, float]] = defaultdict(
            dict
        )
        for crop in unique_crops:
            for (c, region, rc, ws), excess in excess_data.items():
                if c != crop:
                    continue
                remaining = excess - allocated[(crop, region, rc, ws)]
                if remaining > 0:
                    location_excess[(region, rc, ws)][crop] = remaining

        for (region, rc, ws), crop_excess in location_excess.items():
            if set(crop_excess.keys()) != unique_crops:
                continue  # Not all crops have excess at this location
            alloc = min(crop_excess.values())
            if alloc <= 0:
                continue
            for crop in unique_crops:
                allocated[(crop, region, rc, ws)] += alloc
            records.append(
                {
                    "combination": combo_name,
                    "region": region,
                    "resource_class": rc,
                    "water_supply": ws,
                    "inferred_area_ha": alloc,
                }
            )

    return pd.DataFrame(records)


def _cap_multi_cropping_areas(
    gaez_areas: pd.DataFrame,
    inferred_areas: pd.DataFrame,
) -> pd.DataFrame:
    """Replace GAEZ eligible areas with CROPGRIDS-inferred multi-cropping areas.

    For combinations in both GAEZ and inferred: use inferred area
    For combinations only in inferred: add them (e.g., double_rice not in GAEZ)
    For combinations only in GAEZ: remove them (no actual multi-cropping)

    Returns a DataFrame with structure matching gaez_areas.
    """
    if inferred_areas.empty:
        # No inferred data, return empty
        return gaez_areas.iloc[:0].copy()

    # Aggregate inferred areas by combination/region/resource_class/water_supply
    inferred_agg = (
        inferred_areas.groupby(
            ["combination", "region", "resource_class", "water_supply"]
        )["inferred_area_ha"]
        .sum()
        .reset_index()
        .rename(columns={"inferred_area_ha": "eligible_area_ha"})
    )

    # Merge with GAEZ to get water_requirement_m3_per_ha where available
    key_cols = ["combination", "region", "resource_class", "water_supply"]
    if "water_requirement_m3_per_ha" in gaez_areas.columns:
        water_req = gaez_areas[*key_cols, "water_requirement_m3_per_ha"].copy()
        result = inferred_agg.merge(water_req, on=key_cols, how="left")
        result["water_requirement_m3_per_ha"] = result[
            "water_requirement_m3_per_ha"
        ].fillna(0.0)
    else:
        result = inferred_agg.copy()
        result["water_requirement_m3_per_ha"] = 0.0

    # Ensure same column order as gaez_areas
    result = result[gaez_areas.columns.intersection(result.columns)]

    return result[result["eligible_area_ha"] > 0]


def _subtract_multi_cropping_from_harvested(
    harvested_area_data: dict[str, pd.DataFrame],
    inferred_areas: pd.DataFrame,
    combinations: dict[str, dict],
) -> dict[str, pd.DataFrame]:
    """Subtract inferred multi-cropping areas from single-crop harvested areas.

    This prevents double-counting: the multi-cropping links handle the extra
    production from CI > 1, so we must reduce the single-crop harvested areas
    by the corresponding inferred multi-cropping areas.

    Returns a modified copy of harvested_area_data with reduced harvested_area values.
    """
    if inferred_areas.empty:
        return harvested_area_data

    # Build lookup: combination -> unique crops
    combo_crops: dict[str, set[str]] = {}
    for name, entry in combinations.items():
        combo_crops[name] = {str(c) for c in entry["crops"]}

    # Build reduction lookup: (crop, region, resource_class, water_supply) -> reduction_ha
    reductions: dict[tuple[str, str, int, str], float] = {}
    for _, row in inferred_areas.iterrows():
        combo = row["combination"]
        region = str(row["region"])
        rc = int(row["resource_class"])
        ws = row["water_supply"]
        area = row["inferred_area_ha"]

        # Subtract from each crop in the combination
        for crop in combo_crops.get(combo, set()):
            key = (crop, region, rc, ws)
            reductions[key] = reductions.get(key, 0.0) + area

    # Apply reductions to harvested_area_data
    result = {}
    for key, df in harvested_area_data.items():
        # Key format: "{crop}_harvested_{ws}"
        parts = key.replace("_harvested_", "_").rsplit("_", 1)
        if len(parts) != 2:
            result[key] = df.copy()
            continue
        crop, ws = parts

        df = df.copy()
        if "harvested_area" in df.columns:
            df = df.reset_index()
            for i, row in df.iterrows():
                region = str(row["region"])
                rc = int(row["resource_class"])
                red_key = (crop, region, rc, ws)
                reduction = reductions.get(red_key, 0.0)
                if reduction > 0:
                    old_val = df.at[i, "harvested_area"]
                    df.at[i, "harvested_area"] = max(0.0, old_val - reduction)
            df = df.set_index(["region", "resource_class"])
        result[key] = df

    return result


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
    harvest_area_source = str(validation_cfg["harvest_area_source"])
    enable_inferred_multi_cropping = bool(
        validation_cfg.get("enable_inferred_multi_cropping", False)
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

    # When using inferred multi-cropping in validation mode, cap GAEZ eligible areas
    # by the actual multi-cropped area implied by CROPGRIDS cropping intensity.
    if enable_inferred_multi_cropping and use_actual_production and harvested_area_data:
        inferred_areas = _compute_inferred_multi_cropping_areas(
            harvested_area_data,
            dict(snakemake.params.multiple_cropping),
        )
        if not inferred_areas.empty and not multi_cropping_area_df.empty:
            # Filter inferred areas to only combinations with cycle_yields data
            # (e.g., double_rice may be inferred but has no yields in GAEZ)
            combos_with_yields = set(multi_cropping_cycle_df["combination"].unique())
            inferred_before = len(inferred_areas)
            inferred_areas = inferred_areas[
                inferred_areas["combination"].isin(combos_with_yields)
            ]
            if len(inferred_areas) < inferred_before:
                skipped_combos = (
                    set(
                        _compute_inferred_multi_cropping_areas(
                            harvested_area_data,
                            dict(snakemake.params.multiple_cropping),
                        )["combination"].unique()
                    )
                    - combos_with_yields
                )
                logger.warning(
                    "Skipping inferred multi-cropping for combinations without cycle yields: %s",
                    ", ".join(sorted(skipped_combos)),
                )

            if not inferred_areas.empty:
                # Cap GAEZ eligible areas by inferred areas from CROPGRIDS
                multi_cropping_area_df = _cap_multi_cropping_areas(
                    multi_cropping_area_df, inferred_areas
                )
                logger.info(
                    "Capped multi-cropping areas to CROPGRIDS-inferred values: %.2f Mha total",
                    multi_cropping_area_df["eligible_area_ha"].sum() / 1e6,
                )
                # NOTE: We don't subtract inferred areas from harvested areas because:
                # - Single-crop yields already skip CI multiplier (via skip_cropping_intensity_multiplier)
                # - Multi-crop links provide the extra capacity from CI > 1
                # - Subtracting would create production deficits that can't be filled

    luc_lef_lookup: dict[tuple[str, int, str, str], float] = {}
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

    diet_cfg = snakemake.params.diet
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

    crop_list = snakemake.params.crops
    animal_products_cfg = snakemake.params.animal_products
    animal_product_list = list(animal_products_cfg["include"])
    biomass_cfg = snakemake.params.biomass
    biomass_enabled = bool(biomass_cfg["enabled"])
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
    # Only configured macronutrients get constraints applied
    macronutrient_names = list(macronutrient_cfg.keys()) if macronutrient_cfg else []

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

    # Biomass infrastructure (optional)
    if biomass_cfg["enabled"]:
        biomass.add_biomass_infrastructure(n, cfg_countries, biomass_cfg)

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
    land.add_land_components(
        n,
        land_class_df,
        baseline_land_df,
        luc_lef_lookup,
        reg_limit=reg_limit,
        land_slack_cost=validation_slack_cost,  # Use unified validation slack cost
        enable_land_slack=enable_land_slack,
    )

    # Marginal land buses (grazing-only)
    marginal_bus_names: list[str] = []
    if grazing_only_area_series is not None and not grazing_only_area_series.empty:
        marginal_bus_names = [
            f"land_marginal_{region}_class{int(cls)}"
            for region, cls in grazing_only_area_series.index
        ]
        n.buses.add(marginal_bus_names, carrier=["land"] * len(marginal_bus_names))
        n.generators.add(
            marginal_bus_names,
            bus=marginal_bus_names,
            carrier=["land"] * len(marginal_bus_names),
            p_nom_extendable=[True] * len(marginal_bus_names),
            p_nom_max=(reg_limit * grazing_only_area_series.values / 1e6),
        )
        if enable_land_slack:
            primary_resources._add_land_slack_generators(
                n, marginal_bus_names, validation_slack_cost
            )

    # Rice methane factor and scaling factor for rainfed wetland rice
    rice_cfg = snakemake.params.emissions["rice"]
    rice_methane_factor = float(rice_cfg["methane_emission_factor_kg_per_ha"])
    rainfed_wetland_rice_ch4_scaling_factor = float(
        rice_cfg["rainfed_wetland_rice_ch4_scaling_factor"]
    )

    # Multi-cropping can be enabled in two modes:
    # 1. Standard mode: when not using actual production (optimization)
    # 2. Inferred mode: in validation with CROPGRIDS data (experimental)
    enable_multiple_cropping = bool(snakemake.params.multiple_cropping) and (
        (
            not use_actual_production
            and not validation_cfg["production_stability"]["enabled"]
        )
        or enable_inferred_multi_cropping
    )
    # In validation mode with inferred multi-cropping, the multi-crop links handle
    # the extra production from cropping_intensity > 1, so we skip the CI multiplier
    # on single-crop yields to avoid double-counting.
    use_fixed_multi_cropping = (
        enable_multiple_cropping
        and enable_inferred_multi_cropping
        and use_actual_production
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
        harvest_area_source=harvest_area_source,
        residue_lookup=residue_lookup,
        harvested_area_data=harvested_area_data if use_actual_production else None,
        use_actual_production=use_actual_production,
        skip_cropping_intensity_multiplier=use_fixed_multi_cropping,
    )
    if enable_multiple_cropping:
        if use_fixed_multi_cropping:
            logger.info(
                "Enabling inferred multi-cropping in validation mode with fixed areas"
            )
            if harvest_area_source != "cropgrids":
                logger.warning(
                    "Inferred multi-cropping works best with harvest_area_source=cropgrids"
                )
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
            use_fixed_areas=use_fixed_multi_cropping,
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

    # Biomass routing (optional)
    if biomass_cfg["enabled"]:
        biomass.add_biomass_byproduct_links(
            n, cfg_countries, snakemake.params.byproducts
        )
        biomass.add_biomass_crop_links(n, cfg_countries, biomass_crop_targets)

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
        add_slack_for_fixed_consumption=enforce_baseline,
        slack_marginal_cost=validation_slack_cost,
    )
    nutrition.add_macronutrient_loads(
        n,
        all_nutrient_names,
        macronutrient_cfg,
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

    # ═══════════════════════════════════════════════════════════════
    # EXPORT
    # ═══════════════════════════════════════════════════════════════

    logger.info("Network summary:")
    logger.info("Carriers: %d", len(n.carriers.static))
    logger.info("Buses: %d", len(n.buses.static))
    logger.info("Stores: %d", len(n.stores.static))
    logger.info("Links: %d", len(n.links.static))

    n.export_to_netcdf(snakemake.output.network)
