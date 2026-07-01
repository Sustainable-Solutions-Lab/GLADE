# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
    commodity_costs,
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
from workflow.scripts.constants import FEED_CATEGORIES, HA_PER_MHA, USD_TO_BNUSD
from workflow.scripts.logging_config import setup_script_logging

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

    # Grassland forage calibration is applied at solve time (not build time)
    # to allow calibrated and uncalibrated scenarios to share a single build.

    # ═══════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════════

    # Read fertilizer N application rates (kg N/ha/year for high-input agriculture)
    fertilizer_n_rates = read_csv(snakemake.input.fertilizer_n_rates, index_col="crop")[
        "n_rate_kg_per_ha"
    ].to_dict()

    # Read food conversion data
    foods = read_csv(snakemake.input.foods)
    edible_portion_df = read_csv(snakemake.input.edible_portion)
    moisture_df = read_csv(snakemake.input.moisture_content).set_index("crop")

    # Read food groups data
    food_groups = read_csv(snakemake.input.food_groups)

    # Read per-food native mass basis (used to annotate food buses)
    food_basis = (
        read_csv(snakemake.input.food_basis).set_index("food")["basis"].to_dict()
    )

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
            fue=pd.to_numeric(valid["fue"], errors="coerce"),
        )
        valid = valid.dropna(subset=["residue_yield_t_per_ha", "fue"])
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

        # Per-residue field utilization efficiency: caps the fraction of
        # the gross residue bus that can be routed to feed conversion. The
        # data prep guarantees a single FUE per feed_item across all rows;
        # raise if that invariant is broken.
        fue_per_item = (
            valid.groupby("feed_item")["fue"].agg(["min", "max"]).reset_index()
        )
        mismatched = fue_per_item[
            (fue_per_item["max"] - fue_per_item["min"]).abs() > 1e-9
        ]
        if not mismatched.empty:
            raise ValueError(
                "Residue feed items have inconsistent FUE values: "
                f"{mismatched['feed_item'].tolist()}"
            )
        residue_fue_lookup: dict[str, float] = dict(
            zip(fue_per_item["feed_item"], fue_per_item["min"].astype(float))
        )
    else:
        residue_feed_items = []
        residue_lookup = {}
        residue_fue_lookup = {}

    # Build per-residue soil-N2O coefficient (t N2O / Mt DM) once here so
    # both crop_production (mandatory (1-FUE) N2O on bus6) and
    # residue_incorporation (LP-controlled N2O on the net residue bus)
    # use the same numbers. Requires the IPCC residue-decomposition
    # factors from emissions.residues / emissions.fertilizer.
    _emissions_params = snakemake.params.emissions
    residue_n2o_eff_lookup = crops.compute_residue_n2o_efficiency_per_dm(
        residue_feed_items,
        ruminant_feed_mapping,
        ruminant_feed_categories,
        monogastric_feed_mapping,
        monogastric_feed_categories,
        float(_emissions_params["residues"]["incorporation_n2o_factor"]),
        float(_emissions_params["fertilizer"]["indirect_ef5"]),
        float(_emissions_params["fertilizer"]["frac_leach"]),
    )

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
    # CROPGRIDS-backed crops are rainfed-only by construction.
    cropgrids_crops = set(snakemake.config.get("cropgrids_crops") or [])  # type: ignore[index]
    expected_irrigated_crops -= cropgrids_crops

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

            # Floor crop-production capacity at the observed harvested area.
            # ``suitable_area`` is a GAEZ suitability raster that, in semi-arid
            # systems, rates far less land suitable than is actually cultivated.
            # Capping production at GAEZ suitability relocates baseline
            # production off real cropland (via _redistribute_excess_baseline)
            # and frees the marginal land, which p_min_pu=1 then forces into the
            # spared-land (reforestation) sink. Real harvested area is hard data
            # and wins: a crop may always be grown on at least its observed
            # footprint. Mirrors the CROPGRIDS treatment, where suitable_area is
            # harvested_area * suitable_area_expansion (build_crop_yields_cropgrids.py).
            yields_df["suitable_area"] = yields_df[
                ["suitable_area", "harvested_area"]
            ].max(axis=1)

            yields_data[yields_key] = yields_df

    # Per-crop coverage check: a crop is viable only if at least one of its
    # configured water supplies has a positive yield in some configured
    # region. Failing here means the build cannot place any production link
    # for the crop, so downstream rules would silently drop it and any
    # baseline-diet entry derived from it would saturate validation slack.
    unviable_crops: list[str] = []
    for crop in snakemake.params.crops:
        any_yield = False
        for ws in ("r", "i"):
            key = f"{crop}_yield_{ws}"
            df = yields_data.get(key)
            if df is None or df.empty:
                continue
            if "yield" in df.columns and (df["yield"].fillna(0.0) > 0).any():
                any_yield = True
                break
        if not any_yield:
            unviable_crops.append(crop)
    if unviable_crops:
        raise ValueError(
            "No configured region has positive yield (rainfed or irrigated) "
            f"for crop(s): {sorted(unviable_crops)}. Either remove them from "
            "`crops` in the config, or extend `countries` to include regions "
            "where they can be grown."
        )

    # Apply per-(country, crop) yield corrections. Two sources, applied
    # multiplicatively through the same per-cell rescaling:
    #   * fodder_yield_corrections: Eurostat-anchored FDD-crop corrections,
    #     always applied (calibrates GAEZ yields to observed European patterns).
    #   * yield_calibration: FBS-corrected-FAOSTAT-anchored corrections for
    #     crops where GAEZ relies on a proxy raster (e.g. plantain via banana).
    #     Only wired in when validation.use_actual_yields=true (see
    #     workflow/rules/model.smk).
    def _apply_yield_corrections(corr_df: pd.DataFrame, source_label: str) -> None:
        if corr_df.empty:
            return
        _r2c_corr = gpd.read_file(snakemake.input.regions)[
            ["region", "country"]
        ].set_index("region")["country"]
        n_adjusted = 0
        for _, row in corr_df.iterrows():
            country = str(row["country"])
            crop = str(row["crop"])
            factor = float(row["yield_correction_factor"])
            corr_regions = set(_r2c_corr[_r2c_corr == country].index)
            for ws in ("r", "i"):
                key = f"{crop}_yield_{ws}"
                if key not in yields_data:
                    continue
                df = yields_data[key]
                mask = df.index.get_level_values("region").isin(corr_regions)
                if mask.any():
                    df.loc[mask, "yield"] = df.loc[mask, "yield"] * factor
                    n_adjusted += int(mask.sum())
        logger.info(
            "Applied %s yield corrections: %d region-class entries adjusted "
            "across %d (country, crop) pairs",
            source_label,
            n_adjusted,
            len(corr_df),
        )

    fodder_corr_path = snakemake.input.get("fodder_yield_corrections")
    if fodder_corr_path:
        _apply_yield_corrections(read_csv(fodder_corr_path), "fodder")

    yield_cal_path = snakemake.input.get("yield_calibration")
    if yield_cal_path:
        _apply_yield_corrections(read_csv(yield_cal_path), "FAOSTAT-target")

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

        # Apply FAOSTAT pasture area cap: scale satellite grassland area
        # down to match FAOSTAT "permanent meadows and pastures" per country.
        # This replaces the old forage overlap subtraction with an external
        # ground-truth area constraint.  The uniform per-country factor never
        # increases area beyond the satellite estimate.
        faostat_pasture = read_csv(snakemake.input.faostat_pasture_area)
        if not faostat_pasture.empty and current_grassland_area_series is not None:
            faostat_area_ha = faostat_pasture.set_index("country")["area_kha"] * 1000
            _r2c = regions_df.set_index("region")["country"]
            _area_df = current_grassland_area_df.copy()
            _area_df["country"] = _area_df["region"].map(_r2c)
            satellite_by_country = _area_df.groupby("country")["area_ha"].sum()
            correction = (
                (faostat_area_ha / satellite_by_country).clip(upper=1.0).fillna(1.0)
            )
            _area_df["factor"] = (
                _area_df["country"].map(correction).fillna(1.0).to_numpy()
            )
            n_capped = int((_area_df["factor"] < 1.0).sum())
            current_grassland_area_df["area_ha"] = (
                current_grassland_area_df["area_ha"] * _area_df["factor"].values
            )
            current_grassland_area_series = (
                current_grassland_area_df.set_index(["region", "resource_class"])[
                    "area_ha"
                ]
                .astype(float)
                .sort_index()
            )
            total_satellite = satellite_by_country.sum() / 1e6
            total_faostat = (
                faostat_area_ha.reindex(
                    satellite_by_country.index, fill_value=0.0
                ).sum()
                / 1e6
            )
            logger.info(
                "Applied FAOSTAT pasture area cap: %d/%d entries capped "
                "(satellite %.1f Mha → FAOSTAT %.1f Mha)",
                n_capped,
                len(_area_df),
                total_satellite,
                total_faostat,
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
                logger.info(
                    "Total convertible grassland area: %.1f Mha",
                    convertible.sum() / 1e6,
                )
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

    baseline_year = int(snakemake.params.baseline_year)

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

    # Read FAOSTAT-based crop production costs (USD/ha per crop, country)
    costs_df = read_csv(snakemake.input.costs)
    base_year = int(snakemake.config["currency_base_year"])
    cost_col = f"cost_usd_{base_year}_per_ha"
    crop_costs = costs_df.set_index(["crop", "country"])[cost_col].astype(float)
    global_median_cost = costs_df.groupby("crop")[cost_col].median()

    # Per-class farm-to-wholesale marketing markups (USD per tonne). One
    # lookup per commodity domain; missing assignments are caught upstream
    # by ``workflow.validation.commodities``.
    commodities_cfg = snakemake.params.commodities
    crop_marketing_usd_per_t = commodity_costs.marketing_costs_per_t(
        commodities_cfg["crops"], snakemake.params.crops
    )
    feed_marketing_usd_per_t = commodity_costs.marketing_costs_per_t(
        commodities_cfg["feeds"], FEED_CATEGORIES
    )

    # Per-crop sowing rates (kg seed per ha per year). Source values in
    # data/curated/seed_rates.csv are fresh / as-planted mass to match the
    # citation literature (e.g. 2200 kg/ha seed tubers for potato). The
    # crop-link builders compare seed against yield in *dry-matter* units
    # (the basis of yields_data), so the seed series is converted to DM
    # here via (1 - moisture_fraction) before being passed downstream.
    # Without this conversion, high-moisture crops (potato, yam, sugarcane)
    # see their seed share massively inflated and clip against the 50% cap,
    # halving their effective yield.
    seed_rates_df = read_csv(snakemake.input.seed_rates, comment="#")
    seed_kg_dm_per_ha = seed_rates_df.set_index("crop")["seed_kg_per_ha"].astype(
        float
    ) * (1.0 - moisture_df["moisture_fraction"])

    # Optional cost calibration corrections (crops, grassland, animals)
    crop_cost_calibration = None
    grassland_cost_calibration = None
    animal_cost_calibration = None
    if hasattr(snakemake.input, "crop_cost_calibration"):
        cal_df = read_csv(snakemake.input.crop_cost_calibration)
        crop_cost_calibration = cal_df.set_index(["crop", "country"])[
            "correction_bnusd_per_mha"
        ]
        logger.info(
            "Loaded crop cost calibration: %d entries", len(crop_cost_calibration)
        )
    if hasattr(snakemake.input, "grassland_cost_calibration"):
        cal_df = read_csv(snakemake.input.grassland_cost_calibration)
        grassland_cost_calibration = cal_df.set_index("country")[
            "correction_bnusd_per_mha"
        ]
        logger.info(
            "Loaded grassland cost calibration: %d entries",
            len(grassland_cost_calibration),
        )
    if hasattr(snakemake.input, "animal_cost_calibration"):
        cal_df = read_csv(snakemake.input.animal_cost_calibration)
        animal_cost_calibration = cal_df.set_index(["product", "country"])[
            "correction_bnusd_per_mt_feed"
        ]
        logger.info(
            "Loaded animal cost calibration: %d entries",
            len(animal_cost_calibration),
        )

    # Read animal production costs (USD per tonne of product). animals.py
    # applies the tonne -> Mt scaling on the marginal_cost coefficient.
    animal_costs_df = read_csv(snakemake.input.animal_costs)
    cost_per_t_column = f"cost_per_t_usd_{base_year}"
    animal_costs_per_t = animal_costs_df.set_index("product")[cost_per_t_column].astype(
        float
    )

    grazing_cost_per_tonne_dm = grassland.calculate_grazing_cost_per_tonne_dm(
        animal_costs_df, feed_to_products, base_year
    )

    # ═══════════════════════════════════════════════════════════════
    # NETWORK BUILDING
    # ═══════════════════════════════════════════════════════════════

    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.name = "GLADE"

    # Store population in network metadata for consistent access in solve and analysis
    n.meta["population"] = {
        "country": population.to_dict(),
        "year": planning_year,
        "baseline_year": baseline_year,
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
        biofuel_baseline_df["bus_type"] = "food"
        logger.info("Biofuel baseline: %d rows", len(biofuel_baseline_df))
        if hasattr(snakemake.input, "biogas_demand"):
            biogas_df = read_csv(snakemake.input.biogas_demand)
            biogas_df["bus_type"] = "crop"
            logger.info("Biogas crop demand: %d rows", len(biogas_df))
            biofuel_baseline_df = pd.concat(
                [biofuel_baseline_df, biogas_df], ignore_index=True
            )
    enforce_fiber_demand = bool(biomass_cfg["enforce_fiber_demand"])
    fiber_baseline_df = None
    fiber_items: set[str] = set()
    if enforce_fiber_demand:
        fiber_baseline_df = read_csv(snakemake.input.fiber_baseline)
        fiber_items = set(fiber_baseline_df["source_item"].unique())
        logger.info("Fiber baseline: %d rows", len(fiber_baseline_df))
    animal_co_product_list = list(animal_products_cfg["co_products"].keys())
    food_list = sorted(
        set(base_food_list).union(animal_product_list).union(animal_co_product_list)
    )
    byproduct_list = list(snakemake.params.byproducts)
    food_marketing_usd_per_t = commodity_costs.marketing_costs_per_t(
        commodities_cfg["foods"], food_list
    )
    food_to_group = food_groups.set_index("food")["group"].to_dict()
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
        food_basis=food_basis,
    )

    # Biomass infrastructure and routing.
    # Creates country-level biomass buses and sinks (negative generators) that
    # allow crops and byproducts to be exported to the energy sector. This also
    # provides a disposal route for byproducts that lack feed mappings (e.g.
    # wheat-germ, rice-bran). Set biomass.marginal_values_usd_per_tonne to 0 to
    # make biomass export free.
    biomass.add_biomass_infrastructure(n, cfg_countries, biomass_cfg)
    # Build a per-food (1 - moisture) factor to convert food bus mass
    # (fresh) into biomass bus mass (Mt DM). Without this, the biomass
    # bus would account fresh food as DM, over-crediting moisture-heavy
    # foods. Uses the source crop's moisture as a first-order
    # approximation; foods with multiple pathways take the first crop.
    moisture_by_crop = moisture_df["moisture_fraction"].to_dict()
    food_dm_factor: dict[str, float] = {}
    for food_name, grp in foods.groupby("food"):
        source_crops = grp["crop"].dropna().astype(str).tolist()
        if not source_crops or source_crops[0] not in moisture_by_crop:
            food_dm_factor[food_name] = 1.0
        else:
            food_dm_factor[food_name] = 1.0 - moisture_by_crop[source_crops[0]]

    # Filter fiber-managed items out of biomass byproduct routing: when fiber
    # demand is enforced, these items must flow to fiber stores instead.
    biomass_byproducts = [b for b in byproduct_list if b not in fiber_items]
    biomass.add_biomass_byproduct_links(
        n, cfg_countries, biomass_byproducts, food_dm_factor=food_dm_factor
    )
    biomass.add_biomass_crop_links(n, cfg_countries, biomass_crop_targets)
    biomass_disposal_foods = list(biomass_cfg["disposal_foods"])
    biomass.add_biomass_disposal_links(
        n, cfg_countries, biomass_disposal_foods, food_dm_factor=food_dm_factor
    )
    if biofuel_baseline_df is not None:
        biomass.add_biofuel_links(
            n, biofuel_baseline_df, crop_moisture=moisture_by_crop
        )
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

    # Land conversion costs: annualize overnight investment using capital recovery factor
    investment_horizon = int(land_cfg["investment_horizon"])
    discount_rate = float(land_cfg["discount_rate"])
    if discount_rate > 0:
        crf = discount_rate / (1 - (1 + discount_rate) ** (-investment_horizon))
    else:
        crf = 1.0 / investment_horizon
    conv_cost_forest = (
        float(land_cfg["conversion_cost_forest_usd_per_ha"])
        * crf
        * HA_PER_MHA
        * USD_TO_BNUSD
    )
    conv_cost_nonforest = (
        float(land_cfg["conversion_cost_nonforest_usd_per_ha"])
        * crf
        * HA_PER_MHA
        * USD_TO_BNUSD
    )

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
        disable_spared_cropland=disable_spared_cropland,
        disable_spared_grassland=disable_spared_grassland,
        existing_grassland_convertible_area=convertible_grassland_area_series,
        existing_grassland_marginal_area=marginal_grassland_area_series,
        conversion_cost_forest_bnusd_per_mha=conv_cost_forest,
        conversion_cost_nonforest_bnusd_per_mha=conv_cost_nonforest,
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

    # Tag spared-land links (cropland + existing-grassland sparing) with their
    # country so the solve-time per-country reforestation cap
    # (land.reforestation_cap) can group by it. These links are
    # created without a country; every other geographic link already carries one.
    # An unmapped region is a hard error: a spare link without a country would
    # silently escape the cap (the constraint is an upper bound, so dropping
    # links fails open).
    _spare_r2c = regions_df.set_index("region")["country"]
    _spare_mask = n.links.static["carrier"].isin(
        ["spare_land", "spare_existing_grassland"]
    )
    _spare_country = n.links.static.loc[_spare_mask, "region"].map(_spare_r2c)
    if _spare_country.isna().any():
        _missing = sorted(
            n.links.static.loc[_spare_mask, "region"][_spare_country.isna()].unique()
        )
        raise ValueError(
            f"Spared-land links reference {len(_missing)} region(s) missing "
            f"from the region-to-country table: {_missing[:10]}"
        )
    n.links.static.loc[_spare_mask, "country"] = _spare_country
    # Per-(crop, country) supply-chain loss multiplier = 1 - loss_fraction
    # applied to crop_production efficiency. The loss rate is sourced from
    # the crop's *primary* food group (the food output with the highest
    # pathway factor): for almost all crops this is unambiguous because
    # the primary output and any byproducts share one SDG product type
    # (CRL_PUL / FRT_VGT / RT_TBR). Applying loss at production puts the
    # producer's supply-chain loss inside the crop bus mass, so trade and
    # downstream processing are loss-neutral and the LP cannot route
    # processing through low-loss countries to extract food efficiency.
    foods_with_group = foods.copy()
    foods_with_group["group"] = foods_with_group["food"].map(food_to_group)
    primary_food_group = (
        foods_with_group.dropna(subset=["group"])
        .sort_values(["crop", "factor"], ascending=[True, False])
        .drop_duplicates(subset=["crop"])
        .set_index("crop")["group"]
        .to_dict()
    )
    flw_loss = (
        food_loss_waste.set_index(["country", "food_group"])["loss_fraction"]
        .astype(float)
        .clip(0.0, 1.0)
    )
    crop_loss_pairs = [(c, ct) for c in primary_food_group for ct in cfg_countries]
    crop_loss_index = pd.MultiIndex.from_tuples(
        crop_loss_pairs, names=["crop", "country"]
    )
    loss_values = [
        flw_loss.get((ct, primary_food_group[c]), 0.0) for c, ct in crop_loss_pairs
    ]
    crop_loss_multiplier = (
        1.0 - pd.Series(loss_values, index=crop_loss_index, dtype=float)
    ).clip(lower=0.01)

    crops.add_regional_crop_production_links(
        n,
        crop_list,
        yields_data,
        region_to_country,
        set(cfg_countries),
        crop_costs,
        global_median_cost,
        fertilizer_n_rates,
        rice_methane_factor=rice_methane_factor,
        rainfed_wetland_rice_ch4_scaling_factor=rainfed_wetland_rice_ch4_scaling_factor,
        residue_lookup=residue_lookup,
        residue_fue_lookup=residue_fue_lookup,
        residue_n2o_eff_lookup=residue_n2o_eff_lookup,
        use_actual_production=use_actual_production,
        cost_calibration=crop_cost_calibration,
        min_yield_t_per_ha=min_crop_yield,
        seed_kg_dm_per_ha=seed_kg_dm_per_ha,
        crop_loss_multiplier=crop_loss_multiplier,
        crop_marketing_cost_usd_per_t=crop_marketing_usd_per_t,
    )
    land.add_multi_cropping_land_correction(
        n,
        land_use_cost_bnusd_per_mha=land_use_cost_bnusd_per_mha,
    )

    # Multi-cropping is disabled when running with actual production or when
    # the land deviation penalty is active (penalty anchors land area to
    # baseline; extra harvested area from multi-cropping would bias the
    # deviation accounting).
    dp_cfg = snakemake.params.deviation_penalty
    land_deviation_active = dp_cfg["enabled"] and dp_cfg["land"]["enabled"]
    enable_multiple_cropping = bool(snakemake.params.multiple_cropping) and (
        not use_actual_production and not land_deviation_active
    )
    if enable_multiple_cropping:
        crops.add_multi_cropping_links(
            n,
            multi_cropping_area_df,
            multi_cropping_cycle_df,
            region_to_country,
            set(cfg_countries),
            crop_costs,
            global_median_cost,
            fertilizer_n_rates,
            residue_lookup,
            residue_fue_lookup=residue_fue_lookup,
            residue_n2o_eff_lookup=residue_n2o_eff_lookup,
            min_yield_t_per_ha=min_crop_yield,
            seed_kg_dm_per_ha=seed_kg_dm_per_ha,
            crop_loss_multiplier=crop_loss_multiplier,
            crop_marketing_cost_usd_per_t=crop_marketing_usd_per_t,
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
            cost_calibration=grassland_cost_calibration,
        )

    # Food conversion. food_processing is country-neutral: producer-side
    # loss is applied earlier on crop_production (per crop's primary food
    # group), and consumer-side waste is applied later on the
    # food_consumption link via ``add_food_nutrition_links``. Animal
    # products go through ``add_feed_to_animal_product_links`` where
    # loss is applied on the animal_production link directly.
    food.add_food_conversion_links(
        n,
        food_list,
        foods,
        cfg_countries,
        crop_to_fresh_factor,
        food_to_group,
        snakemake.params.crops,
        byproduct_list,
        food_marketing_cost_usd_per_t=food_marketing_usd_per_t,
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
        feed_marketing_cost_usd_per_t=feed_marketing_usd_per_t,
    )

    # Compute trade hub positions once (shared across crop, food, and feed trade)
    hub_centers = trade.compute_trade_hubs(
        regions_df, int(snakemake.params.commodities["hubs"])
    )

    # Feed trade networks (between countries via hubs)
    trade.add_feed_trade_hubs_and_links(
        n,
        snakemake.params.commodities,
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

    # Animal production. Per-country food *loss* (pre-retail) is applied
    # here; consumer-side *waste* is applied later on the food_consumption
    # link via ``add_food_nutrition_links``.
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
        animal_costs_per_t,
        feed_baseline=feed_baseline,
        enforce_baseline_feed=enforce_baseline_feed,
        cost_calibration=animal_cost_calibration,
        co_products=animal_products_cfg["co_products"],
        animal_marketing_cost_usd_per_t=food_marketing_usd_per_t,
    )

    # Add exogenous feed generators (leaves/browse, swill), priced at the
    # grassland grazing cost in optimisation mode so the landless backstop
    # does not undercut endogenous grassland/fodder/residue feed.
    animals.add_exogenous_feed_generators(
        n,
        feed_baseline,
        enforce_baseline_feed=enforce_baseline_feed,
        grazing_cost_usd_per_t=grazing_cost_per_tonne_dm,
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
        food_loss_waste,
    )

    # Trade networks
    trade.add_crop_trade_hubs_and_links(
        n,
        snakemake.params.commodities,
        regions_df,
        cfg_countries,
        list(crop_list),
        hub_centers=hub_centers,
    )
    trade.add_food_trade_hubs_and_links(
        n,
        snakemake.params.commodities,
        regions_df,
        cfg_countries,
        food_list,
        hub_centers=hub_centers,
    )

    # Health-cluster stores are only added when health is enabled (in the base
    # config or any scenario). When disabled, the health processing inputs are
    # absent and no health data is needed on disk.
    if snakemake.params.health_enabled:
        health.add_health_stores(
            n,
            snakemake.input.health_cluster_summary,
            snakemake.input.health_cluster_cause,
            snakemake.config["health"],
        )

        # Compute and store health cluster populations from country populations
        cluster_map_df = read_csv(snakemake.input.health_clusters)
        cluster_lookup = (
            cluster_map_df.set_index("country_iso3")["health_cluster"]
            .astype(int)
            .to_dict()
        )
        pop_df = population.reset_index()
        pop_df.columns = ["iso3", "population"]
        pop_df["cluster"] = pop_df["iso3"].str.upper().map(cluster_lookup)
        pop_df = pop_df.dropna(subset=["cluster"])
        pop_df["cluster"] = pop_df["cluster"].astype(int)
        cluster_pop = pop_df.groupby("cluster")["population"].sum().to_dict()
        n.meta["population"]["health_cluster"] = cluster_pop

    # Store build-time regional_limit in metadata so solve_model can rescale
    n.meta["land_regional_limit"] = float(land_cfg["regional_limit"])

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
