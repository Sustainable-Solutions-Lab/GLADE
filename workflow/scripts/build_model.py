# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import functools
import logging
from typing import Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from sklearn.cluster import KMeans

KM3_PER_M3 = 1e-9  # convert cubic metres to cubic kilometres
TONNE_TO_MEGATONNE = 1e-6  # convert tonnes to megatonnes
KCAL_TO_MCAL = 1e-6  # convert kilocalories to megacalories
KCAL_PER_100G_TO_MCAL_PER_TONNE = 1e-2  # kcal/100g to Mcal per tonne of food
DAYS_PER_YEAR = 365

SUPPORTED_NUTRITION_UNITS = {
    "g/100g": {"kind": "mass", "efficiency_factor": TONNE_TO_MEGATONNE},
    "kcal/100g": {
        "kind": "energy",
        "efficiency_factor": KCAL_PER_100G_TO_MCAL_PER_TONNE,
    },
}


def _nutrient_kind(unit: str) -> str:
    try:
        return SUPPORTED_NUTRITION_UNITS[unit]["kind"]
    except KeyError as exc:
        raise ValueError(f"Unsupported nutrition unit '{unit}'") from exc


def _nutrition_efficiency_factor(unit: str) -> float:
    try:
        return SUPPORTED_NUTRITION_UNITS[unit]["efficiency_factor"]
    except KeyError as exc:
        raise ValueError(f"Unsupported nutrition unit '{unit}'") from exc


def _per_capita_to_bus_units(
    value_per_person_per_day: float,
    population: float,
    unit: str,
) -> float:
    kind = _nutrient_kind(unit)
    if kind == "mass":
        # g/person/day → Mt/year (1e-12 = 1e-6 g→t × 1e-6 t→Mt)
        return value_per_person_per_day * population * DAYS_PER_YEAR * 1e-12
    if kind == "energy":
        return value_per_person_per_day * population * DAYS_PER_YEAR * KCAL_TO_MCAL
    raise ValueError(f"Unsupported nutrient kind '{kind}' for unit '{unit}'")


def _per_capita_food_group_to_mt(
    value_per_person_per_day: float, population: float
) -> float:
    """Convert g/person/day to Mt/year for food group buses."""

    return value_per_person_per_day * population * DAYS_PER_YEAR * 1e-12


def _carrier_unit_for_nutrient(unit: str) -> str:
    kind = _nutrient_kind(unit)
    if kind == "mass":
        return "Mt"
    if kind == "energy":
        return "Mcal"
    raise ValueError(f"Unsupported nutrient kind '{kind}'")


def _load_crop_yield_table(path: str) -> tuple[pd.DataFrame, dict[str, str | float]]:
    df = pd.read_csv(path)

    grouped_units = (
        df.groupby("variable")["unit"].agg(lambda s: s.dropna().unique()).to_dict()
    )
    units: dict[str, str | float] = {}
    for var, vals in grouped_units.items():
        if len(vals) == 1:
            units[var] = vals[0]
        else:
            units[var] = np.nan

    pivot = (
        df.pivot(index=["region", "resource_class"], columns="variable", values="value")
        .rename_axis(index=("region", "resource_class"), columns=None)
        .sort_index()
    )

    # Ensure resource_class level is integer
    pivot.index = pivot.index.set_levels(
        pivot.index.levels[1].astype(int), level="resource_class"
    )

    # Ensure numeric columns
    for column in pivot.columns:
        pivot[column] = pd.to_numeric(pivot[column], errors="coerce")

    return pivot, units


def _gaez_code_to_crop_map(mapping_df: pd.DataFrame) -> dict[str, str]:
    code_columns = [c for c in mapping_df.columns if c.endswith("_code")]
    mapping: dict[str, str] = {}
    for _, row in mapping_df.iterrows():
        crop_name = str(row.get("crop_name", "")).strip()
        if not crop_name:
            continue
        for col in code_columns:
            code = row.get(col)
            if pd.isna(code):
                continue
            code_str = str(code).strip().lower()
            if not code_str:
                continue
            mapping[code_str] = crop_name
    return mapping


def _exception_crops_from_unit_table(
    unit_df: pd.DataFrame, code_map: dict[str, str]
) -> set[str]:
    if "code" not in unit_df.columns:
        raise ValueError(
            "yield_unit_conversions.csv must contain a 'code' column listing GAEZ crop codes"
        )
    codes = unit_df["code"].dropna().astype(str).str.strip().str.lower()
    missing_codes = sorted(code for code in codes if code and code not in code_map)
    if missing_codes:
        logging.getLogger(__name__).warning(
            "yield_unit_conversions.csv references GAEZ codes with no crop mapping: %s",
            ", ".join(missing_codes),
        )
    return {code_map[code] for code in codes if code in code_map}


def _fresh_mass_conversion_factors(
    edible_portion_df: pd.DataFrame,
    crops: set[str],
    exceptions: set[str],
) -> dict[str, float]:
    df = edible_portion_df.copy()
    df["crop"] = df["crop"].astype(str).str.strip()

    df = df.set_index("crop")
    for col in ["edible_portion_coefficient", "water_content_g_per_100g"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    factors: dict[str, float] = {}
    missing_data: list[str] = []
    for crop in sorted(crops):
        if crop in exceptions:
            continue
        if crop not in df.index:
            missing_data.append(crop)
            continue
        edible_coeff = df.at[crop, "edible_portion_coefficient"]
        water_pct = df.at[crop, "water_content_g_per_100g"]
        if pd.isna(edible_coeff) or pd.isna(water_pct):
            missing_data.append(crop)
            continue
        if not (0 < edible_coeff <= 1):
            raise ValueError(
                f"Invalid edible portion coefficient {edible_coeff} for crop '{crop}'"
            )
        if water_pct < 0 or water_pct >= 100:
            raise ValueError(
                f"Water content for crop '{crop}' must be in [0, 100); found {water_pct}"
            )
        water_fraction = water_pct / 100.0
        factor = edible_coeff / (1 - water_fraction)
        if not np.isfinite(factor) or factor <= 0:
            raise ValueError(
                f"Computed non-positive fresh mass factor {factor} for crop '{crop}'"
            )
        factors[crop] = factor

    if missing_data:
        raise ValueError(
            "Missing edible portion or water content data for crops: "
            + ", ".join(sorted(missing_data))
        )

    return factors


def _build_luc_lef_lookup(
    df: pd.DataFrame,
) -> dict[tuple[str, int, str, str], float]:
    """Return LEF (tCO2/ha/yr) lookup keyed by (region, class, water, use)."""

    if df.empty:
        return {}

    lookup: dict[tuple[str, int, str, str], float] = {}
    for row in df.itertuples(index=False):
        lef = getattr(row, "LEF_tCO2_per_ha_yr", np.nan)
        if not np.isfinite(lef):
            continue
        key = (
            str(row.region),
            int(row.resource_class),
            str(row.water),
            str(row.use),
        )
        lookup[key] = float(lef)
    return lookup


logger = logging.getLogger(__name__)


def _calculate_ch4_per_product(
    feed_pool: str, efficiency: float, my_lookup: dict[str, float]
) -> float | None:
    """Calculate CH4 emissions (tCH4/t product) for ruminant feed.

    Parameters
    ----------
    feed_pool : str
        Feed category name (e.g., "ruminant_roughage", "monogastric_grain")
    efficiency : float
        Feed conversion efficiency (t product / t feed DM)
    my_lookup : dict[str, float]
        Methane yields by feed category (g CH4 / kg DMI)

    Returns
    -------
    float | None
        CH4 emissions in tCH4/t product, or None for non-ruminant feeds
    """
    if not feed_pool.startswith("ruminant_"):
        return None

    # Extract category (roughage/forage/grain/protein)
    category = feed_pool.split("_", 1)[1]

    # Look up methane yield for this category
    if category not in my_lookup:
        logger.warning(
            "No CH4 yield for ruminant feed category '%s', assuming 0", category
        )
        return None

    # DMI = dry matter intake per tonne product
    dmi = 1.0 / efficiency  # t DM / t product

    # Convert methane yield from g CH4/kg DM to t CH4/t DM
    my_t_per_t = my_lookup[category] / 1000.0

    return dmi * my_t_per_t  # t CH4 / t product


def add_carriers_and_buses(
    n: pypsa.Network,
    crop_list: list,
    food_list: list,
    residue_feed_items: list,
    food_group_list: list,
    nutrient_list: list,
    nutrient_units: dict[str, str],
    countries: list,
    regions: list,
    water_regions: list,
) -> None:
    """Add all carriers and their corresponding buses to the network.

    - Regional land buses remain per-region.
    - Crops, residues, foods, food groups, and macronutrients are created per-country.
    - Primary resources (water, fertilizer) and emissions (co2, ch4) stay global.
    """
    # Land carrier (class-level buses are added later)
    n.add("Carrier", "land", unit="Mha")

    # Crops per country
    crop_buses = [
        f"crop_{crop}_{country}" for country in countries for crop in crop_list
    ]
    crop_carriers = [f"crop_{crop}" for country in countries for crop in crop_list]
    if crop_buses:
        n.add("Carrier", sorted(set(f"crop_{crop}" for crop in crop_list)), unit="t")
        n.add("Bus", crop_buses, carrier=crop_carriers)

    # Residues per country
    residue_items_sorted = sorted(dict.fromkeys(residue_feed_items))
    if residue_items_sorted:
        residue_buses = [
            f"residue_{item}_{country}"
            for country in countries
            for item in residue_items_sorted
        ]
        residue_carriers = [
            f"residue_{item}" for country in countries for item in residue_items_sorted
        ]
        n.add("Carrier", sorted(set(residue_carriers)), unit="t")
        n.add("Bus", residue_buses, carrier=residue_carriers)

    # Foods per country
    food_buses = [
        f"food_{food}_{country}" for country in countries for food in food_list
    ]
    food_carriers = [f"food_{food}" for country in countries for food in food_list]
    if food_buses:
        n.add("Carrier", sorted(set(f"food_{food}" for food in food_list)), unit="t")
        n.add("Bus", food_buses, carrier=food_carriers)

    # Food groups per country
    group_buses = [
        f"group_{group}_{country}" for country in countries for group in food_group_list
    ]
    group_carriers = [
        f"group_{group}" for country in countries for group in food_group_list
    ]
    if group_buses:
        n.add(
            "Carrier",
            sorted(set(f"group_{group}" for group in food_group_list)),
            unit="Mt",
        )
        n.add("Bus", group_buses, carrier=group_carriers)
        scale_meta = n.meta.setdefault("carrier_unit_scale", {})
        scale_meta["food_group_t_to_Mt"] = TONNE_TO_MEGATONNE

    # Macronutrients per country
    nutrient_list_sorted = sorted(dict.fromkeys(nutrient_list))
    for nutrient in nutrient_list_sorted:
        unit = nutrient_units[nutrient]
        carrier_unit = _carrier_unit_for_nutrient(unit)
        if nutrient not in n.carriers.index:
            n.add("Carrier", nutrient, unit=carrier_unit)

    if nutrient_list_sorted:
        nutrient_buses = [
            f"{nut}_{country}" for country in countries for nut in nutrient_list_sorted
        ]
        nutrient_carriers = [
            nut for country in countries for nut in nutrient_list_sorted
        ]
        n.add("Bus", nutrient_buses, carrier=nutrient_carriers)

        scale_meta = n.meta.setdefault("carrier_unit_scale", {})
        if any(
            _nutrient_kind(nutrient_units[nut]) == "mass"
            for nut in nutrient_list_sorted
        ):
            scale_meta["macronutrient_t_to_Mt"] = TONNE_TO_MEGATONNE
        if any(
            _nutrient_kind(nutrient_units[nut]) == "energy"
            for nut in nutrient_list_sorted
        ):
            scale_meta["macronutrient_kcal_to_Mcal"] = KCAL_TO_MCAL

    # Feed carriers per country (8 pools: 4 ruminant + 4 monogastric quality classes)
    feed_categories = [
        "ruminant_roughage",
        "ruminant_forage",
        "ruminant_grain",
        "ruminant_protein",
        "monogastric_low_quality",
        "monogastric_grain",
        "monogastric_energy",
        "monogastric_protein",
    ]
    feed_buses = [
        f"feed_{fc}_{country}" for country in countries for fc in feed_categories
    ]
    feed_carriers = [f"feed_{fc}" for country in countries for fc in feed_categories]
    if feed_buses:
        n.add("Carrier", sorted(set(feed_carriers)), unit="t")
        n.add("Bus", feed_buses, carrier=feed_carriers)

    # Water carrier (buses added per region below)
    n.add("Carrier", "water", unit="km^3")

    # Global emission and resource carriers with buses
    for carrier, unit in [
        ("fertilizer", "kg"),
        ("co2", "MtCO2"),
        ("ch4", "MtCH4"),
        ("ghg", "MtCO2e"),
    ]:
        n.add("Carrier", carrier, unit=unit)
        n.add("Bus", carrier, carrier=carrier)

    scale_meta = n.meta.setdefault("carrier_unit_scale", {})
    scale_meta["co2_t_to_Mt"] = TONNE_TO_MEGATONNE
    scale_meta["ch4_t_to_Mt"] = TONNE_TO_MEGATONNE
    scale_meta["ghg_t_to_Mt"] = TONNE_TO_MEGATONNE

    for region in water_regions:
        bus_name = f"water_{region}"
        n.add("Bus", bus_name, carrier="water")


def add_primary_resources(
    n: pypsa.Network,
    primary_config: dict,
    region_water_limits: pd.Series,
    co2_price: float,
    ch4_to_co2_factor: float,
) -> None:
    """Add primary resource components and emissions bookkeeping."""
    # Filter to positive water limits and build stores
    df = region_water_limits[region_water_limits > 0].to_frame("limit_m3")
    df["limit_km3"] = df["limit_m3"] * KM3_PER_M3
    store_names = [f"water_store_{region}" for region in df.index]
    bus_names = [f"water_{region}" for region in df.index]
    limit_km3 = df["limit_km3"].tolist()

    n.add(
        "Store",
        store_names,
        bus=bus_names,
        carrier="water",
        e_nom=limit_km3,
        e_initial=limit_km3,
        e_nom_extendable=False,
        e_cyclic=False,
        p_nom=limit_km3,
        p_nom_extendable=False,
    )

    scale_meta = n.meta.setdefault("carrier_unit_scale", {})
    scale_meta["water_km3_per_m3"] = KM3_PER_M3

    co2_price_per_mt = co2_price / TONNE_TO_MEGATONNE

    # Fertilizer remains global (no regionalization yet)
    n.add(
        "Generator",
        "fertilizer",
        bus="fertilizer",
        carrier="fertilizer",
        p_nom_extendable=True,
        p_nom_max=float(primary_config["fertilizer"]["limit"]),
    )

    # Add GHG aggregation store and links from individual gases
    n.add(
        "Store",
        "ghg",
        bus="ghg",
        carrier="ghg",
        e_nom_extendable=True,
        e_nom_min=-np.inf,
        e_min_pu=-1.0,
        marginal_cost_storage=co2_price_per_mt,
    )
    n.add(
        "Link",
        "convert_co2_to_ghg",
        bus0="co2",
        bus1="ghg",
        carrier="co2",
        efficiency=1.0,
        p_min_pu=-1.0,  # allow negative emissions flow
        p_nom_extendable=True,
    )
    n.add(
        "Link",
        "convert_ch4_to_ghg",
        bus0="ch4",
        bus1="ghg",
        carrier="ch4",
        efficiency=ch4_to_co2_factor,
        p_nom_extendable=True,
    )


def add_regional_crop_production_links(
    n: pypsa.Network,
    crop_list: list,
    crops: pd.DataFrame,
    yields_data: dict,
    region_to_country: pd.Series,
    allowed_countries: set,
    crop_prices_usd_per_t: pd.Series,
    luc_lef_lookup: Mapping[tuple[str, int, str, str], float] | None = None,
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
) -> None:
    """Add crop production links per region/resource class and water supply.

    Rainfed yields must be present for every crop; irrigated yields are used when
    provided by the preprocessing pipeline. Output links produce into the same
    crop bus per country; link names encode supply type (i/r) and resource class.
    """
    luc_lef_lookup = luc_lef_lookup or {}
    residue_lookup = residue_lookup or {}

    for crop in crop_list:
        if crop not in crops.index.get_level_values(0):
            logger.warning("Crop '%s' not found in crops data, skipping", crop)
            continue

        crop_data = crops.loc[crop]

        # Get global production coefficients
        fert_use = pd.to_numeric(
            crop_data.loc["fertilizer", "value"], errors="coerce"
        )  # kg/t
        fert_use = float(fert_use) if np.isfinite(fert_use) else 0.0

        # Get emission coefficients (if they exist)
        ch4_emission = 0.0
        if "ch4" in crop_data.index:
            ch4_emission = crop_data.loc["ch4", "value"] / 1000.0  # kg/t → t/t

        available_supplies = [
            ws for ws in ("r", "i") if f"{crop}_yield_{ws}" in yields_data
        ]

        if "r" not in available_supplies:
            raise ValueError(
                "Rainfed yield data missing for crop '%s'; ensure build_crop_yields ran"
                % crop
            )

        # Process available water supplies (rainfed always first for stability)
        for ws in available_supplies:
            key = f"{crop}_yield_{ws}"
            crop_yields = yields_data[key].copy()

            # Add a unique name per link including water supply and class
            crop_yields["name"] = crop_yields.index.map(
                lambda x: f"produce_{crop}_{'irrigated' if ws == 'i' else 'rainfed'}_{x[0]}_class{x[1]}"
            )

            # Make index levels columns
            df = crop_yields.reset_index()

            # Set index to "name"
            df.set_index("name", inplace=True)
            df.index.name = None

            # Filter out rows with zero suitable area or zero yield
            df = df[(df["suitable_area"] > 0) & (df["yield"] > 0)]

            # Map regions to countries and filter to allowed countries
            df["country"] = df["region"].map(region_to_country)
            df = df[df["country"].isin(allowed_countries)]

            if df.empty:
                continue

            # Price for this crop (USD/tonne); if missing, warn and use 0
            price = float(crop_prices_usd_per_t.get(crop, float("nan")))
            if not np.isfinite(price):
                logger.warning(
                    "No FAOSTAT price for crop '%s'; defaulting marginal_cost to 0",
                    crop,
                )
                price = 0.0

            # Add links
            # Connect to class-level land bus per region/resource class and water supply
            # Land is now tracked in Mha, so scale yields and areas accordingly
            resource_classes = df["resource_class"].astype(int).to_numpy()
            regions = df["region"].astype(str).to_numpy()
            water_code = "i" if ws == "i" else "r"
            luc_lefs = np.array(
                [
                    luc_lef_lookup.get(
                        (region, int(resource_class), water_code, "cropland"), 0.0
                    )
                    for region, resource_class in zip(regions, resource_classes)
                ],
                dtype=float,
            )  # tCO2/ha/yr
            base_cost = (price * df["yield"] * 1e6).to_numpy()

            link_params = {
                "name": df.index,
                # Use the crop's own carrier so no extra carrier is needed
                "carrier": f"crop_{crop}",
                "bus0": df.apply(
                    lambda r: f"land_{r['region']}_class{int(r['resource_class'])}_{'i' if ws == 'i' else 'r'}",
                    axis=1,
                ),
                "bus1": df["country"].apply(lambda c: f"crop_{crop}_{c}"),
                "efficiency": df["yield"] * 1e6,  # t/ha → t/Mha
                "bus3": "fertilizer",
                "efficiency3": -fert_use / df["yield"],  # kg/t remains kg/t
                # Link marginal_cost is per unit of bus0 flow (now Mha).
                "marginal_cost": base_cost,
                "p_nom_max": df["suitable_area"] / 1e6,  # ha → Mha
                "p_nom_extendable": True,
            }

            if ws == "i":
                if "water_requirement_m3_per_ha" not in df.columns:
                    raise ValueError(
                        "Missing GAEZ water requirement column for irrigated crop "
                        f"'{crop}'"
                    )

                water_requirement = pd.to_numeric(
                    df["water_requirement_m3_per_ha"], errors="coerce"
                )
                invalid = (~np.isfinite(water_requirement)) | (water_requirement < 0)
                if invalid.any():
                    invalid_rows = df.loc[invalid, ["region", "resource_class"]]
                    sample = (
                        invalid_rows.head(5)
                        .apply(
                            lambda r: f"{r['region']}#class{int(r['resource_class'])}",
                            axis=1,
                        )
                        .tolist()
                    )
                    raise ValueError(
                        "Invalid irrigated water requirement for crop "
                        f"'{crop}' in {invalid_rows.shape[0]} rows (examples: "
                        + ", ".join(sample)
                        + ")"
                    )

                link_params["bus2"] = df["region"].apply(lambda r: f"water_{r}")
                # Convert m³/ha to km³/Mha for compatibility with scaled water units
                link_params["efficiency2"] = -water_requirement * 1e-3

            next_bus_idx = 4
            if residue_lookup:
                residue_feed_items = sorted(
                    {
                        feed_item
                        for region, resource_class in zip(regions, resource_classes)
                        for feed_item in residue_lookup.get(
                            (crop, water_code, region, int(resource_class)), {}
                        ).keys()
                    }
                )
                if residue_feed_items:
                    countries_for_rows = df["country"].astype(str).tolist()
                    for feed_item in residue_feed_items:
                        efficiencies = np.zeros(len(df), dtype=float)
                        for idx_row, (region, resource_class) in enumerate(
                            zip(regions, resource_classes)
                        ):
                            residue_dict = residue_lookup.get(
                                (crop, water_code, region, int(resource_class))
                            )
                            if not residue_dict:
                                continue
                            residue_yield = residue_dict.get(feed_item)
                            if residue_yield is None:
                                continue
                            efficiencies[idx_row] = residue_yield * 1e6  # t/ha → t/Mha
                        if np.allclose(efficiencies, 0.0):
                            continue
                        bus_key = f"bus{next_bus_idx}"
                        eff_key = f"efficiency{next_bus_idx}"
                        link_params[bus_key] = [
                            f"residue_{feed_item}_{country}"
                            for country in countries_for_rows
                        ]
                        link_params[eff_key] = efficiencies
                        next_bus_idx += 1

            emission_outputs: dict[str, np.ndarray] = {}

            if ch4_emission > 0:
                arr = (ch4_emission / df["yield"]).to_numpy(
                    dtype=float
                ) * TONNE_TO_MEGATONNE
                emission_outputs["ch4"] = emission_outputs.get(
                    "ch4", np.zeros(len(arr), dtype=float)
                )
                emission_outputs["ch4"] += arr

            luc_emissions = (
                luc_lefs * 1e6 * TONNE_TO_MEGATONNE
            )  # tCO2/ha/yr → MtCO2/Mha/yr
            if not np.allclose(luc_emissions, 0.0):
                emission_outputs["co2"] = emission_outputs.get(
                    "co2", np.zeros(len(luc_emissions), dtype=float)
                )
                emission_outputs["co2"] += luc_emissions

            for bus_name in sorted(emission_outputs.keys()):
                values = emission_outputs[bus_name]
                key_bus = f"bus{next_bus_idx}"
                key_eff = f"efficiency{next_bus_idx}"
                link_params[key_bus] = [bus_name] * len(values)
                link_params[key_eff] = values
                next_bus_idx += 1

            n.add("Link", **link_params)


def add_grassland_feed_links(
    n: pypsa.Network,
    grassland: pd.DataFrame,
    land_rainfed: pd.DataFrame,
    region_to_country: pd.Series,
    allowed_countries: set,
    luc_lef_lookup: Mapping[tuple[str, int, str, str], float] | None = None,
) -> None:
    """Add links supplying ruminant feed directly from rainfed land."""

    luc_lef_lookup = luc_lef_lookup or {}

    df = grassland.copy()
    df = df[np.isfinite(df["yield"]) & (df["yield"] > 0)]
    if df.empty:
        logger.info("Grassland yields contain no positive entries; skipping")
        return

    df = df.reset_index()
    df["resource_class"] = df["resource_class"].astype(int)
    df = df.set_index(["region", "resource_class"])

    merged = df.join(
        land_rainfed[["area_ha"]].rename(columns={"area_ha": "land_area"}),
        how="inner",
    )
    if merged.empty:
        logger.info(
            "No overlap between grassland yields and rainfed land areas; skipping"
        )
        return

    candidate_area = merged["suitable_area"].fillna(merged["land_area"])
    available_area = np.minimum(
        candidate_area.to_numpy(), merged["land_area"].to_numpy()
    )
    merged["available_area"] = available_area
    merged = merged[merged["available_area"] > 0]
    if merged.empty:
        logger.info("Grassland entries have zero available area; skipping")
        return

    merged = merged.reset_index()
    merged["country"] = merged["region"].map(region_to_country)
    merged = merged[merged["country"].isin(allowed_countries)]
    merged = merged.dropna(subset=["country"])
    if merged.empty:
        logger.info("No grassland regions map to configured countries; skipping")
        return

    merged["name"] = merged.apply(
        lambda r: f"graze_{r['region']}_class{int(r['resource_class'])}", axis=1
    )
    merged["bus0"] = merged.apply(
        lambda r: f"land_{r['region']}_class{int(r['resource_class'])}_r", axis=1
    )
    merged["bus1"] = merged["country"].apply(lambda c: f"feed_ruminant_forage_{c}")

    luc_emissions = (
        np.array(
            [
                luc_lef_lookup.get(
                    (row["region"], int(row["resource_class"]), "r", "pasture"), 0.0
                )
                for _, row in merged.iterrows()
            ],
            dtype=float,
        )
        * 1e6
        * TONNE_TO_MEGATONNE
    )  # tCO2/ha/yr → MtCO2/Mha/yr

    params = {
        "carrier": "feed_ruminant_forage",
        "bus0": merged["bus0"].tolist(),
        "bus1": merged["bus1"].tolist(),
        "efficiency": merged["yield"].to_numpy() * 1e6,  # t/ha → t/Mha
        "p_nom_max": merged["available_area"].to_numpy() / 1e6,  # ha → Mha
        "p_nom_extendable": True,
        "marginal_cost": 0.0,
    }

    if not np.allclose(luc_emissions, 0.0):
        params["bus2"] = "co2"
        params["efficiency2"] = luc_emissions

    n.add("Link", merged["name"].tolist(), **params)


def add_spared_land_links(
    n: pypsa.Network,
    land_class_df: pd.DataFrame,
    luc_lef_lookup: Mapping[tuple[str, int, str, str], float],
) -> None:
    """Add optional links to allocate spared land and credit CO2 sinks.

    The AGB threshold filtering is now applied directly in the LEF calculation,
    so this function simply uses the LEF values as provided.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    land_class_df : pd.DataFrame
        Land area by region/water_supply/resource_class.
    luc_lef_lookup : Mapping
        Land-use change emission factors (tCO2/ha/yr) by (region, class, water, use).
        The spared land LEFs already incorporate AGB threshold filtering.
    """

    if not luc_lef_lookup:
        logger.info("No LUC LEF entries available for spared land; skipping")
        return

    # Build DataFrame with LEF values
    df = land_class_df.reset_index()
    df["resource_class"] = df["resource_class"].astype(int)
    df["lef"] = df.apply(
        lambda r: luc_lef_lookup.get(
            (r["region"], int(r["resource_class"]), r["water_supply"], "spared"), 0.0
        ),
        axis=1,
    )

    # Filter to entries with positive LEF and area
    filtered_count = (df["lef"] == 0.0).sum()
    df = df[(df["lef"] != 0.0) & (df["area_ha"] > 0)].copy()

    if filtered_count > 0:
        logger.debug("Filtered %d spared land entries with zero LEF", filtered_count)

    if df.empty:
        logger.info("No eligible spared land entries; skipping spared links")
        return

    # Build bus and link names
    df["link_name"] = df.apply(
        lambda r: f"spare_{r['region']}_class{r['resource_class']}_{r['water_supply']}",
        axis=1,
    )
    df["bus0"] = df.apply(
        lambda r: f"land_{r['region']}_class{r['resource_class']}_{r['water_supply']}",
        axis=1,
    )
    df["sink_bus"] = df.apply(
        lambda r: f"land_spared_{r['region']}_class{r['resource_class']}_{r['water_supply']}",
        axis=1,
    )
    df["area_mha"] = df["area_ha"] / 1e6

    # Add carrier and sink buses
    n.add("Carrier", "spared_land", unit="Mha")
    n.add("Bus", df["sink_bus"].tolist(), carrier="spared_land")

    # Add stores for sink buses
    store_names = [f"{bus}_store" for bus in df["sink_bus"]]
    n.add(
        "Store",
        store_names,
        bus=df["sink_bus"].tolist(),
        carrier="spared_land",
        e_nom_extendable=True,
    )

    # Add spared land links
    n.add(
        "Link",
        df["link_name"].tolist(),
        carrier="spared_land",
        bus0=df["bus0"].tolist(),
        bus1=df["sink_bus"].tolist(),
        efficiency=1.0,
        bus2="co2",
        efficiency2=(
            df["lef"] * 1e6 * TONNE_TO_MEGATONNE
        ).to_numpy(),  # tCO2/ha/yr → MtCO2/Mha/yr
        p_nom_extendable=True,
        p_nom_max=df["area_mha"].to_numpy(),
    )


def add_food_conversion_links(
    n: pypsa.Network,
    food_list: list,
    foods: pd.DataFrame,
    countries: list,
    crop_to_fresh_factor: dict[str, float],
    exception_crops: set[str],
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
) -> None:
    """Add links for converting crops to foods via processing pathways.

    Pathways can have multiple outputs (e.g., wheat → white flour + bran).
    Each pathway creates one multi-output Link per country.
    """

    # Validate that foods.csv has the new pathway column
    if "pathway" not in foods.columns:
        raise ValueError(
            "foods.csv must contain a 'pathway' column. "
            "See data/foods.csv for the expected format with pathway-based structure."
        )

    loss_waste_pairs: dict[tuple[str, str], tuple[float, float]] = {}
    if not loss_waste.empty:
        required_cols = {"country", "food_group", "loss_fraction", "waste_fraction"}
        missing_cols = required_cols - set(loss_waste.columns)
        if missing_cols:
            raise ValueError(
                "food_loss_waste data missing columns: "
                + ", ".join(sorted(missing_cols))
            )
        for column in ["loss_fraction", "waste_fraction"]:
            loss_waste[column] = pd.to_numeric(
                loss_waste[column], errors="coerce"
            ).fillna(0)
        for _, row in loss_waste.iterrows():
            key = (str(row["country"]), str(row["food_group"]))
            loss_waste_pairs[key] = (
                float(row["loss_fraction"]),
                float(row["waste_fraction"]),
            )

    missing_loss_waste: set[tuple[str, str]] = set()
    missing_group_foods: set[str] = set()
    excessive_losses: set[tuple[str, str]] = set()
    invalid_pathways: list[str] = []

    normalized_countries = [str(c).upper() for c in countries]

    # Group foods by pathway and crop
    pathway_groups = foods.groupby(["pathway", "crop"])

    for (pathway, crop), pathway_df in pathway_groups:
        pathway = str(pathway).strip()
        crop = str(crop).strip()

        # Filter to foods that are in the food_list
        pathway_df = pathway_df[pathway_df["food"].isin(food_list)].copy()
        if pathway_df.empty:
            continue

        # Get output foods and factors
        output_foods = []
        output_factors = []
        for _, row in pathway_df.iterrows():
            food = str(row["food"]).strip()
            if pd.isna(row["factor"]):
                raise ValueError(
                    f"Missing conversion factor in pathway '{pathway}' for crop '{crop}' to food '{food}'"
                )
            factor = float(row["factor"])
            if not np.isfinite(factor) or factor <= 0:
                raise ValueError(
                    f"Invalid conversion factor {factor} in pathway '{pathway}' for crop '{crop}' to food '{food}'"
                )
            output_foods.append(food)
            output_factors.append(factor)

        # Verify mass balance (sum of factors should be ≤ 1.0)
        total_factor = sum(output_factors)
        if total_factor > 1.01:  # Allow small rounding tolerance
            invalid_pathways.append(f"{pathway} ({crop}): sum={total_factor:.3f}")

        # Get conversion factor (dry matter → fresh edible)
        if crop in exception_crops:
            conversion_factor = 1.0
        else:
            try:
                conversion_factor = crop_to_fresh_factor[crop]
            except KeyError as exc:
                raise ValueError(
                    f"Missing moisture/edible conversion data for crop '{crop}' in pathway '{pathway}'"
                ) from exc

        # Create multi-output link names (one per country)
        safe_pathway_name = pathway.replace(" ", "_").replace("(", "").replace(")", "")
        names = [f"pathway_{safe_pathway_name}_{c}" for c in normalized_countries]
        bus0 = [f"crop_{crop}_{c}" for c in normalized_countries]

        # Build parameters for multi-output link
        link_params = {
            "bus0": bus0,
            "marginal_cost": 0.01,
            "p_nom_extendable": True,
        }

        # Add each output food as a separate bus with its efficiency
        for output_idx, (food, factor) in enumerate(
            zip(output_foods, output_factors), start=1
        ):
            bus_key = f"bus{output_idx}"
            eff_key = "efficiency" if output_idx == 1 else f"efficiency{output_idx}"

            link_params[bus_key] = [f"food_{food}_{c}" for c in normalized_countries]

            # Calculate efficiencies per country (including loss/waste adjustments)
            efficiencies: list[float] = []
            group = food_to_group.get(food)
            for country in normalized_countries:
                multiplier = 1.0
                if group is None:
                    missing_group_foods.add(food)
                else:
                    fractions = loss_waste_pairs.get((country, group))
                    if fractions is None:
                        missing_loss_waste.add((country, group))
                    else:
                        raw_loss, raw_waste = fractions
                        loss_fraction = max(0.0, float(raw_loss))
                        waste_fraction = max(0.0, float(raw_waste))
                        loss_clamped = False
                        waste_clamped = False
                        if loss_fraction > 1.0:
                            loss_fraction = 1.0
                            loss_clamped = True
                        if waste_fraction > 1.0:
                            waste_fraction = 1.0
                            waste_clamped = True
                        if loss_clamped or waste_clamped:
                            excessive_losses.add((country, group))
                        multiplier = (1.0 - loss_fraction) * (1.0 - waste_fraction)
                        if multiplier <= 0.0:
                            excessive_losses.add((country, group))
                            multiplier = 0.0
                        else:
                            multiplier = min(1.0, multiplier)
                efficiencies.append(factor * conversion_factor * multiplier)

            link_params[eff_key] = efficiencies

        n.add("Link", names, **link_params)

    # Warnings
    if invalid_pathways:
        logger.warning(
            "Pathways with mass balance issues (sum of factors > 1.0): %s",
            "; ".join(invalid_pathways[:5]),
        )

    if missing_group_foods:
        logger.warning(
            "Food items without food-group mapping (loss/waste ignored): %s",
            ", ".join(sorted(missing_group_foods)),
        )

    unresolved = {
        (country, group)
        for (country, group) in missing_loss_waste
        if (country, group) not in loss_waste_pairs
    }
    if unresolved:
        sample = ", ".join(
            f"{country}:{group}" for country, group in sorted(unresolved)[:10]
        )
        logger.warning(
            "Missing food loss/waste data for %d country-group pairs; defaulting to no loss. Examples: %s",
            len(unresolved),
            sample,
        )

    if excessive_losses:
        sample = ", ".join(
            f"{country}:{group}" for country, group in sorted(excessive_losses)[:10]
        )
        logger.warning(
            "Extreme food loss/waste values for %d country-group pairs (efficiency clamped to feasible range). Examples: %s",
            len(excessive_losses),
            sample,
        )


def add_feed_supply_links(
    n: pypsa.Network,
    ruminant_categories: pd.DataFrame,
    ruminant_mapping: pd.DataFrame,
    monogastric_categories: pd.DataFrame,
    monogastric_mapping: pd.DataFrame,
    crop_list: list,
    food_list: list,
    residue_items: list,
    countries: list,
) -> None:
    """Add links converting crops and foods into categorized feed pools.

    Uses pre-computed feed categories and mappings to route items to appropriate
    feed pools (4 ruminant + 4 monogastric quality classes).
    """
    # Process ruminant feeds
    ruminant_feeds = ruminant_mapping[
        (
            (ruminant_mapping["source_type"] == "crop")
            & ruminant_mapping["feed_item"].isin(crop_list)
        )
        | (
            (ruminant_mapping["source_type"] == "food")
            & ruminant_mapping["feed_item"].isin(food_list)
        )
        | (
            (ruminant_mapping["source_type"] == "residue")
            & ruminant_mapping["feed_item"].isin(residue_items)
        )
    ].copy()

    # Merge with category digestibility
    ruminant_feeds = ruminant_feeds.merge(
        ruminant_categories[["category", "digestibility"]],
        on="category",
        how="left",
    )

    # Process monogastric feeds
    monogastric_feeds = monogastric_mapping[
        (
            (monogastric_mapping["source_type"] == "crop")
            & monogastric_mapping["feed_item"].isin(crop_list)
        )
        | (
            (monogastric_mapping["source_type"] == "food")
            & monogastric_mapping["feed_item"].isin(food_list)
        )
        | (
            (monogastric_mapping["source_type"] == "residue")
            & monogastric_mapping["feed_item"].isin(residue_items)
        )
    ].copy()

    # Merge with category digestibility
    monogastric_feeds = monogastric_feeds.merge(
        monogastric_categories[["category", "digestibility"]],
        on="category",
        how="left",
    )

    # Build ruminant links
    all_names = []
    all_bus0 = []
    all_bus1 = []
    all_efficiency = []

    for _, row in ruminant_feeds.iterrows():
        item = row["feed_item"]
        category = row["category"]
        source_type = row["source_type"]
        digestibility = row["digestibility"]

        if source_type == "crop":
            bus_prefix = "crop"
            link_prefix = "convert"
        elif source_type == "food":
            bus_prefix = "food"
            link_prefix = "convert_food"
        else:
            bus_prefix = "residue"
            link_prefix = "convert_residue"

        for country in countries:
            all_names.append(f"{link_prefix}_{item}_to_ruminant_{category}_{country}")
            all_bus0.append(f"{bus_prefix}_{item}_{country}")
            all_bus1.append(f"feed_ruminant_{category}_{country}")
            all_efficiency.append(digestibility)

    # Build monogastric links
    for _, row in monogastric_feeds.iterrows():
        item = row["feed_item"]
        category = row["category"]
        source_type = row["source_type"]
        digestibility = row["digestibility"]

        if source_type == "crop":
            bus_prefix = "crop"
            link_prefix = "convert"
        elif source_type == "food":
            bus_prefix = "food"
            link_prefix = "convert_food"
        else:
            bus_prefix = "residue"
            link_prefix = "convert_residue"

        for country in countries:
            all_names.append(
                f"{link_prefix}_{item}_to_monogastric_{category}_{country}"
            )
            all_bus0.append(f"{bus_prefix}_{item}_{country}")
            all_bus1.append(f"feed_monogastric_{category}_{country}")
            all_efficiency.append(digestibility)

    if not all_names:
        logger.info("No feed supply links to create; check crop/food lists")
        return

    n.add(
        "Link",
        all_names,
        bus0=all_bus0,
        bus1=all_bus1,
        carrier="convert_to_feed",
        efficiency=all_efficiency,
        marginal_cost=0.01,
        p_nom_extendable=True,
    )

    logger.info(
        "Created %d feed supply links (%d ruminant, %d monogastric)",
        len(all_names),
        len(ruminant_feeds) * len(countries),
        len(monogastric_feeds) * len(countries),
    )


def add_feed_to_animal_product_links(
    n: pypsa.Network,
    animal_products: list,
    feed_requirements: pd.DataFrame,
    ruminant_feed_categories: pd.DataFrame,
    countries: list,
) -> None:
    """Add links that convert feed pools into animal products with CH4 emissions.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to
    animal_products : list
        List of animal product names
    feed_requirements : pd.DataFrame
        Feed requirements with columns: product, feed_category, efficiency
    ruminant_feed_categories : pd.DataFrame
        Ruminant feed categories with CH4 yields (columns: category, MY_g_CH4_per_kg_DMI)
    countries : list
        List of country codes
    """

    if not animal_products:
        logger.info("No animal products configured; skipping feed→animal links")
        return

    # Build methane yield lookup from ruminant feed categories; "my" = "methane yield"
    my_lookup = (
        ruminant_feed_categories.set_index("category")["MY_g_CH4_per_kg_DMI"]
        .astype(float)
        .to_dict()
    )

    df = feed_requirements.copy()
    df = df[df["product"].isin(animal_products)]

    if df.empty:
        return

    # Calculate CH4 emissions for each row
    df["efficiency"] = df["efficiency"].astype(float)
    df["ch4_emissions"] = df.apply(
        lambda r: _calculate_ch4_per_product(
            r["feed_category"], r["efficiency"], my_lookup
        ),
        axis=1,
    )

    # Build all link names and buses (expand each row for all countries)
    all_names = []
    all_bus0 = []
    all_bus1 = []
    all_carrier = []
    all_efficiency = []
    all_ch4 = []

    for _, row in df.iterrows():
        for country in countries:
            all_names.append(
                f"produce_{row['product']}_from_{row['feed_category']}_{country}"
            )
            all_bus0.append(f"feed_{row['feed_category']}_{country}")
            all_bus1.append(f"food_{row['product']}_{country}")
            all_carrier.append(f"produce_{row['product']}")
            all_efficiency.append(row["efficiency"])
            ch4_val = row["ch4_emissions"]
            all_ch4.append(None if ch4_val is None else ch4_val * TONNE_TO_MEGATONNE)

    # Split into links with and without CH4 emissions
    has_ch4 = [ch4 is not None for ch4 in all_ch4]

    if any(has_ch4):
        ch4_indices = [i for i, has in enumerate(has_ch4) if has]
        n.add(
            "Link",
            [all_names[i] for i in ch4_indices],
            bus0=[all_bus0[i] for i in ch4_indices],
            bus1=[all_bus1[i] for i in ch4_indices],
            carrier=[all_carrier[i] for i in ch4_indices],
            efficiency=[all_efficiency[i] for i in ch4_indices],
            marginal_cost=0.0,
            p_nom_extendable=True,
            bus2="ch4",
            efficiency2=[all_ch4[i] for i in ch4_indices],
        )

    if not all(has_ch4):
        no_ch4_indices = [i for i, has in enumerate(has_ch4) if not has]
        n.add(
            "Link",
            [all_names[i] for i in no_ch4_indices],
            bus0=[all_bus0[i] for i in no_ch4_indices],
            bus1=[all_bus1[i] for i in no_ch4_indices],
            carrier=[all_carrier[i] for i in no_ch4_indices],
            efficiency=[all_efficiency[i] for i in no_ch4_indices],
            marginal_cost=0.0,
            p_nom_extendable=True,
        )


def add_food_group_buses_and_loads(
    n: pypsa.Network,
    food_group_list: list,
    food_groups: pd.DataFrame,
    food_groups_config: dict,
    countries: list,
    population: pd.Series,
    *,
    per_country_equal: dict[str, dict[str, float]] | None = None,
) -> None:
    """Add carriers, buses, and loads for food groups defined in the CSV.

    Supports min/max/equal per-person-per-day targets per food group. Country-level
    equality overrides can be supplied via ``per_country_equal``.
    """

    if not food_groups_config:
        return

    per_country_equal = per_country_equal or {}

    logger.info("Adding food group loads based on nutrition requirements...")
    for group in food_group_list:
        group_config = food_groups_config.get(group, {}) or {}
        min_value = group_config.get("min")
        max_value = group_config.get("max")
        equal_value = group_config.get("equal")
        equal_overrides = per_country_equal.get(group, {})

        names = [f"{group}_{c}" for c in countries]
        buses = [f"group_{group}_{c}" for c in countries]
        carriers = [f"group_{group}"] * len(countries)

        def _value_list(
            base_value: float | None,
            overrides: dict[str, float] | None,
        ) -> list[float | None]:
            override_map = overrides or {}
            values: list[float | None] = []
            for country in countries:
                if country in override_map:
                    values.append(float(override_map[country]))
                elif base_value is not None:
                    values.append(float(base_value))
                else:
                    values.append(None)
            return values

        equal_values = _value_list(equal_value, equal_overrides)
        if all(v is not None for v in equal_values):
            equal_totals = [
                _per_capita_food_group_to_mt(value, float(population[country]))
                for value, country in zip(equal_values, countries)
            ]
            n.add("Load", names, bus=buses, carrier=carriers, p_set=equal_totals)
            # Equality constraint fixes consumption; no additional stores required
            continue

        min_values = _value_list(min_value, None)
        max_values = _value_list(max_value, None)

        min_totals: list[float] | None = None
        if any(v is not None and v > 0.0 for v in min_values):
            min_totals = [
                _per_capita_food_group_to_mt(v or 0.0, float(population[country]))
                for v, country in zip(min_values, countries)
            ]
            n.add("Load", names, bus=buses, carrier=carriers, p_set=min_totals)

        max_totals: list[float] | None = None
        if any(v is not None for v in max_values):
            max_totals = [
                _per_capita_food_group_to_mt(v or 0.0, float(population[country]))
                for v, country in zip(max_values, countries)
            ]

        store_names = [f"store_{group}_{c}" for c in countries]
        store_kwargs: dict[str, Iterable[float]] = {}
        if max_totals is not None:
            if min_totals is not None:
                e_nom_max = [
                    max(max_total - min_total, 0.0)
                    for max_total, min_total in zip(max_totals, min_totals)
                ]
            else:
                e_nom_max = max_totals
            store_kwargs["e_nom_max"] = e_nom_max

        n.add(
            "Store",
            store_names,
            bus=buses,
            carrier=carriers,
            e_nom_extendable=True,
            **store_kwargs,
        )


def _build_food_group_equals_from_baseline(
    diet_df: pd.DataFrame,
    countries: Iterable[str],
    groups: Iterable[str],
    *,
    baseline_age: str,
    reference_year: int | None,
) -> dict[str, dict[str, float]]:
    """Map baseline diet table to per-country equality targets for food groups."""

    df = diet_df.copy()
    df["country"] = df["country"].str.upper()
    if baseline_age:
        df = df[df["age"] == baseline_age]
    if reference_year is not None and "year" in df.columns:
        sel = df[df["year"] == reference_year]
        if sel.empty:
            raise ValueError(
                f"No baseline diet records for year {reference_year} and age '{baseline_age}'"
            )
        df = sel

    filtered = df[df["country"].isin(countries) & df["item"].isin(groups)]
    if filtered.empty:
        raise ValueError(
            "Baseline diet table is empty after filtering by countries/groups"
        )

    pivot = (
        filtered.groupby(["country", "item"])["value"].mean().unstack(fill_value=np.nan)
    )

    result: dict[str, dict[str, float]] = {}
    missing_entries: list[str] = []
    for group in groups:
        values = {}
        for country in countries:
            value = pivot.get(group, pd.Series(dtype=float)).get(country)
            if pd.isna(value):
                missing_entries.append(f"{country}:{group}")
                continue
            values[country] = float(value)
        if values:
            result[str(group)] = values

    if missing_entries:
        logger.warning(
            "Missing baseline diet values for %d country/group pairs (examples: %s)",
            len(missing_entries),
            ", ".join(sorted(missing_entries)[:5]),
        )

    return result


def add_macronutrient_loads(
    n: pypsa.Network,
    macronutrients_config: dict,
    countries: list,
    population: pd.Series,
    nutrient_units: dict[str, str],
) -> None:
    """Add per-country loads and stores for macronutrient bounds."""

    if not macronutrients_config:
        return

    logger.info("Adding macronutrient constraints per country...")

    for nutrient, nutrient_config in macronutrients_config.items():
        unit = nutrient_units[nutrient]
        equal_value = nutrient_config.get("equal")
        min_value = nutrient_config.get("min")
        max_value = nutrient_config.get("max")

        names = [f"{nutrient}_{c}" for c in countries]
        carriers = [nutrient] * len(countries)

        # Handle equality constraint
        if equal_value is not None:
            p_set = [
                _per_capita_to_bus_units(equal_value, float(population[c]), unit)
                for c in countries
            ]
            n.add("Load", names, bus=names, carrier=carriers, p_set=p_set)
            continue

        # Handle min constraint with Load
        min_totals = None
        if min_value is not None:
            min_totals = [
                _per_capita_to_bus_units(min_value, float(population[c]), unit)
                for c in countries
            ]
            n.add("Load", names, bus=names, carrier=carriers, p_set=min_totals)

        # Handle max constraint with Store
        if max_value is not None or min_value is not None:
            store_names = [f"store_{nutrient}_{c}" for c in countries]

            e_nom_max = None
            if max_value is not None:
                max_totals = [
                    _per_capita_to_bus_units(max_value, float(population[c]), unit)
                    for c in countries
                ]
                if min_totals is not None:
                    e_nom_max = [
                        max(max_t - min_t, 0.0)
                        for max_t, min_t in zip(max_totals, min_totals)
                    ]
                else:
                    e_nom_max = max_totals

            n.add(
                "Store",
                store_names,
                bus=names,
                carrier=carriers,
                e_nom_extendable=True,
                e_cyclic=False,
                **({"e_nom_max": e_nom_max} if e_nom_max is not None else {}),
            )


def add_food_nutrition_links(
    n: pypsa.Network,
    food_list: list,
    foods: pd.DataFrame,
    food_groups: pd.DataFrame,
    nutrition: pd.DataFrame,
    nutrient_units: dict[str, str],
    countries: list,
) -> None:
    """Add multilinks per country for converting foods to groups and macronutrients.

    Byproduct foods (those with group='byproduct') are excluded from human consumption.
    """
    # Pre-index food_groups for lookup
    food_to_group = food_groups.set_index("food")["group"].to_dict()

    # Filter out byproducts from human consumption
    byproduct_foods = set(food_groups.loc[food_groups["group"] == "byproduct", "food"])
    consumable_foods = [f for f in food_list if f not in byproduct_foods]

    if byproduct_foods:
        logger.info(
            "Excluding %d byproduct foods from human consumption: %s",
            len(byproduct_foods),
            ", ".join(sorted(byproduct_foods)),
        )

    nutrients = list(nutrition.index.get_level_values("nutrient").unique())
    for food in consumable_foods:
        group_val = food_to_group.get(food, None)
        names = [
            f"consume_{food.replace(' ', '_').replace('(', '').replace(')', '')}_{c}"
            for c in countries
        ]
        bus0 = [f"food_{food}_{c}" for c in countries]

        # macronutrient outputs
        out_bus_lists = []
        eff_lists = []
        for i, nutrient in enumerate(nutrients, start=1):
            unit = nutrient_units[nutrient]
            factor = _nutrition_efficiency_factor(unit)
            out_bus_lists.append([f"{nutrient}_{c}" for c in countries])
            eff_val = (
                float(nutrition.loc[(food, nutrient), "value"])
                if (food, nutrient) in nutrition.index
                else 0.0
            )
            eff_lists.append([eff_val * factor] * len(countries))

        params = {"bus0": bus0, "marginal_cost": 0.01}
        for i, (buses, effs) in enumerate(zip(out_bus_lists, eff_lists), start=1):
            params[f"bus{i}"] = buses
            params["efficiency" if i == 1 else f"efficiency{i}"] = effs

        # optional food group output as last leg
        if group_val is not None and pd.notna(group_val):
            idx = len(nutrients) + 1
            params[f"bus{idx}"] = [f"group_{group_val}_{c}" for c in countries]
            params[f"efficiency{idx}"] = TONNE_TO_MEGATONNE

        n.add("Link", names, p_nom_extendable=True, **params)


def _resolve_trade_costs(
    trade_config: dict,
    items: list,
    *,
    categories_key: str | None,
    default_cost_key: str | None,
    fallback_cost_key: str,
    category_item_key: str,
) -> tuple[dict[str, float], float]:
    """Map each item to its configured trade cost per kilometre."""

    # Get default cost from config hierarchy
    if default_cost_key is not None:
        default_cost = float(trade_config[default_cost_key])
    else:
        default_cost = float(trade_config[fallback_cost_key])

    item_costs = {str(item): default_cost for item in items}

    if categories_key is None:
        return item_costs, default_cost

    # Override with category-specific costs
    categories = trade_config.get(categories_key, {})
    for category, cfg in categories.items():
        category_cost = float(cfg.get("cost_per_km", default_cost))
        configured_items = cfg.get(category_item_key, [])

        for item in configured_items:
            item_label = str(item)
            if item_label in item_costs:
                item_costs[item_label] = category_cost

    return item_costs, default_cost


def _add_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    items: list,
    *,
    hub_count_key: str,
    marginal_cost_key: str,
    cost_categories_key: str | None,
    default_cost_key: str | None,
    category_item_key: str,
    non_tradable_key: str,
    bus_prefix: str,
    carrier_prefix: str,
    hub_name_prefix: str,
    link_name_prefix: str,
    log_label: str,
) -> None:
    """Shared implementation for adding trade hubs and links for a set of items."""

    n_hubs = int(trade_config[hub_count_key])
    item_costs, default_cost = _resolve_trade_costs(
        trade_config,
        items,
        categories_key=cost_categories_key,
        default_cost_key=default_cost_key,
        fallback_cost_key=marginal_cost_key,
        category_item_key=category_item_key,
    )

    if len(regions_gdf) == 0 or len(countries) == 0:
        logger.info("Skipping %s trade hubs: no regions/countries available", log_label)
        return

    items = list(dict.fromkeys(items))
    if len(items) == 0:
        logger.info("Skipping %s trade hubs: no items configured", log_label)
        return

    non_tradable = {
        str(item) for item in trade_config[non_tradable_key] if item in items
    }
    tradable_items = [item for item in items if item not in non_tradable]
    if non_tradable:
        logger.info(
            "Skipping %s trade network for configured non-tradable items: %s",
            log_label,
            ", ".join(sorted(non_tradable)),
        )

    if not tradable_items:
        logger.info("Skipping %s trade hubs: no tradable items available", log_label)
        return

    gdf = regions_gdf.copy()
    gdf_ee = gdf.to_crs(6933)

    cent = gdf_ee.geometry.centroid
    X = np.column_stack([cent.x.values, cent.y.values])
    k = min(max(1, n_hubs), len(X))
    if k < n_hubs:
        logger.info(
            "Reducing %s hub count from %d to %d (regions=%d)",
            log_label,
            n_hubs,
            k,
            len(X),
        )
        n_hubs = k

    km = KMeans(n_clusters=n_hubs, n_init=10, random_state=0)
    km.fit_predict(X)
    centers = km.cluster_centers_

    hub_ids = list(range(n_hubs))
    for item in tradable_items:
        item_label = str(item)
        hub_bus_names = [f"{hub_name_prefix}_{h}_{item_label}" for h in hub_ids]
        hub_carriers = [f"{carrier_prefix}{item_label}"] * n_hubs
        n.add("Bus", hub_bus_names, carrier=hub_carriers)

    gdf_countries = gdf_ee[gdf_ee["country"].isin(countries)].dissolve(
        by="country", as_index=True
    )
    ccent = gdf_countries.geometry.centroid
    C = np.column_stack([ccent.x.values, ccent.y.values])
    dch = ((C[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2) ** 0.5
    nearest_hub_idx = dch.argmin(axis=1)
    nearest_hub_dist_km = dch[np.arange(len(C)), nearest_hub_idx] / 1000.0

    country_index = gdf_countries.index.to_list()
    country_to_hub = {c: int(h) for c, h in zip(country_index, nearest_hub_idx)}
    country_to_dist_km = {
        c: float(d) for c, d in zip(country_index, nearest_hub_dist_km)
    }

    valid_countries = [c for c in countries if c in country_to_hub]
    for item in tradable_items:
        if not valid_countries:
            continue
        item_label = str(item)
        names_from_c = [
            f"{link_name_prefix}_{item_label}_{c}_hub{country_to_hub[c]}"
            for c in valid_countries
        ]
        names_from_hub = [
            f"{link_name_prefix}_{item_label}_hub{country_to_hub[c]}_{c}"
            for c in valid_countries
        ]
        bus0 = [f"{bus_prefix}{item_label}_{c}" for c in valid_countries]
        bus1 = [
            f"{hub_name_prefix}_{country_to_hub[c]}_{item_label}"
            for c in valid_countries
        ]
        item_cost = item_costs.get(item_label, default_cost)
        costs = [country_to_dist_km[c] * item_cost for c in valid_countries]
        n.add(
            "Link",
            names_from_c,
            bus0=bus0,
            bus1=bus1,
            marginal_cost=costs,
            p_nom_extendable=True,
        )
        n.add(
            "Link",
            names_from_hub,
            bus0=bus1,
            bus1=bus0,
            marginal_cost=costs,
            p_nom_extendable=True,
        )

    if n_hubs >= 2:
        H = centers
        D = np.sqrt(((H[:, None, :] - H[None, :, :]) ** 2).sum(axis=2)) / 1000.0
        ii, jj = np.where(~np.eye(n_hubs, dtype=bool))
        dists_km = D[ii, jj].tolist()

        for item in tradable_items:
            if len(ii) == 0:
                continue
            item_label = str(item)
            names = [
                f"{link_name_prefix}_{item_label}_hub{i}_to_hub{j}"
                for i, j in zip(ii, jj)
            ]
            bus0 = [f"{hub_name_prefix}_{i}_{item_label}" for i in ii]
            bus1 = [f"{hub_name_prefix}_{j}_{item_label}" for j in jj]
            item_cost = item_costs.get(item_label, default_cost)
            costs = [d * item_cost for d in dists_km]
            n.add(
                "Link",
                names,
                bus0=bus0,
                bus1=bus1,
                marginal_cost=costs,
                p_nom_extendable=True,
            )


def add_crop_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    crop_list: list,
) -> None:
    """Add crop trading hubs and connect crop buses via hubs."""

    _add_trade_hubs_and_links(
        n,
        trade_config,
        regions_gdf,
        countries,
        crop_list,
        hub_count_key="crop_hubs",
        marginal_cost_key="crop_trade_marginal_cost_per_km",
        cost_categories_key="crop_trade_cost_categories",
        default_cost_key="crop_default_trade_cost_per_km",
        category_item_key="crops",
        non_tradable_key="non_tradable_crops",
        bus_prefix="crop_",
        carrier_prefix="crop_",
        hub_name_prefix="hub",
        link_name_prefix="trade",
        log_label="crop",
    )


def add_animal_product_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    animal_product_list: list,
) -> None:
    """Add trading hubs and links for animal products."""

    _add_trade_hubs_and_links(
        n,
        trade_config,
        regions_gdf,
        countries,
        animal_product_list,
        hub_count_key="animal_product_hubs",
        marginal_cost_key="animal_product_trade_marginal_cost_per_km",
        cost_categories_key="animal_product_trade_cost_categories",
        default_cost_key="animal_product_default_trade_cost_per_km",
        category_item_key="products",
        non_tradable_key="non_tradable_animal_products",
        bus_prefix="food_",
        carrier_prefix="food_",
        hub_name_prefix="hub_food",
        link_name_prefix="trade_food",
        log_label="animal product",
    )


if __name__ == "__main__":
    read_csv = functools.partial(pd.read_csv, comment="#")

    # Read crop data
    crops = read_csv(snakemake.input.crops, index_col=["crop", "param"])

    # Read food conversion data
    foods = read_csv(snakemake.input.foods)
    if not foods.empty:
        foods["food"] = foods["food"].astype(str).str.strip()
        foods["crop"] = foods["crop"].astype(str).str.strip()
        foods["factor"] = pd.to_numeric(foods["factor"], errors="coerce")
    edible_portion_df = read_csv(snakemake.input.edible_portion)
    yield_unit_conversion_df = read_csv(snakemake.input.yield_unit_conversions)
    gaez_code_mapping_df = read_csv(snakemake.input.gaez_crop_mapping)
    gaez_code_map = _gaez_code_to_crop_map(gaez_code_mapping_df)

    # Read food groups data
    food_groups = read_csv(snakemake.input.food_groups)

    # Read nutrition data
    nutrition = read_csv(snakemake.input.nutrition, index_col=["food", "nutrient"])

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
                str(getattr(row, "crop")),
                str(getattr(row, "water_supply")),
                str(getattr(row, "region")),
                int(getattr(row, "resource_class")),
            )
            residue_lookup.setdefault(key, {})[feed_item] = float(
                getattr(row, "residue_yield_t_per_ha", 0.0)
            )
    else:
        residue_feed_items = []
        residue_lookup = {}

    # Read feed requirements for animal products (feed pools -> foods)
    feed_to_products = read_csv(snakemake.input.feed_to_products)

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
            try:
                path = snakemake.input[yields_key]
            except AttributeError as exc:
                supply_label = "irrigated" if ws == "i" else "rainfed"
                raise ValueError(
                    "Missing %s yield input for crop '%s'. Ensure the crop yield preprocessing "
                    "step produced '%s'." % (supply_label, crop, yields_key)
                ) from exc

            yields_df, var_units = _load_crop_yield_table(path)
            yield_unit = var_units.get("yield")
            if yield_unit != "t/ha (DM)":
                raise ValueError(
                    f"Unexpected unit for 'yield' in '{path}': expected 't/ha (DM)', found '{yield_unit}'"
                )
            area_unit = var_units.get("suitable_area")
            if area_unit != "ha":
                raise ValueError(
                    f"Unexpected unit for 'suitable_area' in '{path}': expected 'ha', found '{area_unit}'"
                )
            if ws == "i":
                water_unit = var_units.get("water_requirement_m3_per_ha")
                if water_unit not in {None, np.nan, "m^3/ha"}:
                    raise ValueError(
                        f"Unexpected unit for 'water_requirement_m3_per_ha' in '{path}': "
                        f"expected 'm^3/ha', found '{water_unit}'"
                    )
            yields_data[yields_key] = yields_df
            logger.info(
                "Loaded yields for %s (%s): %d rows",
                crop,
                "irrigated" if ws == "i" else "rainfed",
                len(yields_df),
            )

    # Read regions
    regions_df = gpd.read_file(snakemake.input.regions)

    # Load class-level land areas
    land_class_df = read_csv(snakemake.input.land_area_by_class)
    # Expect columns: region, water_supply, resource_class, area_ha
    land_class_df = land_class_df.set_index(
        ["region", "water_supply", "resource_class"]
    ).sort_index()

    luc_lef_lookup: dict[tuple[str, int, str, str], float] = {}
    carbon_price = float(snakemake.params.emissions["ghg_price"])
    ch4_to_co2_factor = float(snakemake.params.emissions["ch4_to_co2_factor"])
    if ch4_to_co2_factor <= 0.0:
        raise ValueError("`emissions.ch4_to_co2_factor` must be positive.")
    try:
        luc_coefficients_path = snakemake.input.luc_carbon_coefficients
        luc_coeff_df = read_csv(luc_coefficients_path)
        if not luc_coeff_df.empty:
            luc_lef_lookup = _build_luc_lef_lookup(luc_coeff_df)
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
    if snakemake.params.grazing["enabled"]:
        grassland_df = read_csv(
            snakemake.input.grassland_yields, index_col=["region", "resource_class"]
        ).sort_index()

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
    pop_map = population_df.set_index("iso3")["population"].reindex(cfg_countries)
    missing = pop_map[pop_map.isna()].index.tolist()
    if missing:
        raise ValueError("Missing population for countries: " + ", ".join(missing))
    # population series indexed by country code (ISO3)
    population = pop_map.astype(float)

    diet_cfg = snakemake.params.get("diet", {})
    health_reference_year = int(snakemake.params.health_reference_year)
    enforce_baseline = bool(diet_cfg.get("enforce_gdd_baseline", False))
    baseline_equals: dict[str, dict[str, float]] = {}
    if enforce_baseline:
        baseline_age = str(diet_cfg.get("baseline_age", "All ages"))
        baseline_year = diet_cfg.get("baseline_reference_year", health_reference_year)
        if baseline_year is not None:
            baseline_year = int(baseline_year)
        diet_table_path = snakemake.input.get("baseline_diet")
        if diet_table_path is None:
            raise ValueError(
                "Baseline diet enforcement requested but no GDD intake table provided"
            )
        diet_table = read_csv(diet_table_path)
        baseline_equals = _build_food_group_equals_from_baseline(
            diet_table,
            cfg_countries,
            food_groups["group"].unique(),
            baseline_age=baseline_age,
            reference_year=baseline_year,
        )
        logger.info(
            "Enforcing baseline diet using GDD data (age=%s, year=%s)",
            baseline_age,
            baseline_year,
        )

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

    positive_water_limits = region_water_limits[region_water_limits > 0].copy()

    irrigated_regions: set[str] = set()
    for key, df in yields_data.items():
        if key.endswith("_yield_i"):
            irrigated_regions.update(df.index.get_level_values("region"))

    land_regions = set(land_class_df.index.get_level_values("region"))
    water_bus_regions = sorted(
        set(positive_water_limits.index)
        .union(irrigated_regions)
        .intersection(land_regions)
    )

    missing_water_regions = [r for r in regions if region_water_limits.loc[r] <= 0]
    if missing_water_regions:
        logger.warning(
            "Regions without growing-season water availability data: %s",
            ", ".join(missing_water_regions[:10])
            + ("..." if len(missing_water_regions) > 10 else ""),
        )

    logger.debug("Crops data:\n%s", crops.head(10))
    logger.debug("Foods data:\n%s", foods.head())
    logger.debug("Food groups data:\n%s", food_groups.head())
    logger.debug("Nutrition data:\n%s", nutrition.head())

    # Read FAOSTAT prices (USD/tonne) and build crop->price mapping
    prices_df = read_csv(snakemake.input.prices)
    # Expect columns: crop, faostat_item, n_obs, price_usd_per_tonne
    crop_prices = prices_df.set_index("crop")["price_usd_per_tonne"].astype(float)

    # Build the network (inlined)
    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.name = "food-opt"

    crop_list = snakemake.params.crops
    exception_crops = _exception_crops_from_unit_table(
        yield_unit_conversion_df, gaez_code_map
    ).intersection(set(crop_list))
    animal_products_cfg = snakemake.params.animal_products
    animal_product_list = list(animal_products_cfg["include"])

    # Validate foods.csv structure
    if "pathway" not in foods.columns:
        raise ValueError(
            "foods.csv must contain a 'pathway' column. "
            "Update data/foods.csv to use the pathway-based format."
        )

    food_crops = set(foods.loc[foods["crop"].isin(crop_list), "crop"])
    crop_to_fresh_factor = _fresh_mass_conversion_factors(
        edible_portion_df, food_crops, exception_crops
    )

    base_food_list = foods.loc[foods["crop"].isin(crop_list), "food"].unique().tolist()
    food_list = sorted(set(base_food_list).union(animal_product_list))
    food_groups_clean = food_groups.dropna(subset=["food", "group"]).copy()
    food_groups_clean["food"] = food_groups_clean["food"].astype(str).str.strip()
    food_groups_clean["group"] = food_groups_clean["group"].astype(str).str.strip()
    duplicate_groups = (
        food_groups_clean.groupby("food")["group"].nunique().loc[lambda s: s > 1]
    )
    if not duplicate_groups.empty:
        raise ValueError(
            "Each food must map to a single food group. Conflicts for: "
            + ", ".join(duplicate_groups.index.tolist())
        )
    food_to_group = (
        food_groups_clean.drop_duplicates(subset=["food"])
        .set_index("food")["group"]
        .to_dict()
    )
    food_group_list = food_groups_clean.loc[
        food_groups_clean["food"].isin(food_list), "group"
    ].unique()

    if enforce_baseline:
        missing_pairs = [
            f"{country}:{group}"
            for group in food_group_list
            for country in cfg_countries
            if baseline_equals.get(str(group), {}).get(country) is None
        ]
        if missing_pairs:
            raise ValueError(
                "Baseline diet enforcement missing values for: "
                + ", ".join(sorted(missing_pairs)[:10])
            )

    macronutrient_cfg = snakemake.params.macronutrients
    nutrient_units = (
        nutrition.reset_index()
        .drop_duplicates(subset=["nutrient"])
        .set_index("nutrient")["unit"]
        .to_dict()
    )
    macronutrient_names = list(macronutrient_cfg.keys()) if macronutrient_cfg else []

    add_carriers_and_buses(
        n,
        crop_list,
        food_list,
        residue_feed_items,
        food_group_list,
        macronutrient_names,
        nutrient_units,
        cfg_countries,
        regions,
        water_bus_regions,
    )
    add_primary_resources(
        n,
        snakemake.params.primary,
        positive_water_limits,
        carbon_price,
        ch4_to_co2_factor,
    )

    # Add class-level land buses and generators (shared pools), replacing region-level caps
    # Apply same regional_limit factor per class pool
    reg_limit = float(snakemake.params.primary["land"]["regional_limit"])
    # Build all unique class buses
    bus_names = [f"land_{r}_class{int(k)}_{ws}" for (r, ws, k) in land_class_df.index]
    n.add("Bus", bus_names, carrier=["land"] * len(bus_names))
    n.add(
        "Generator",
        bus_names,
        bus=bus_names,
        carrier=["land"] * len(bus_names),
        p_nom_extendable=[True] * len(bus_names),
        p_nom_max=(reg_limit * land_class_df["area_ha"] / 1e6).values,  # ha → Mha
    )
    add_spared_land_links(n, land_class_df, luc_lef_lookup)
    add_regional_crop_production_links(
        n,
        crop_list,
        crops,
        yields_data,
        region_to_country,
        set(cfg_countries),
        crop_prices,
        luc_lef_lookup,
        residue_lookup,
    )
    if snakemake.params.grazing["enabled"]:
        add_grassland_feed_links(
            n,
            grassland_df,
            land_rainfed_df,
            region_to_country,
            set(cfg_countries),
            luc_lef_lookup,
        )
    add_food_conversion_links(
        n,
        food_list,
        foods,
        cfg_countries,
        crop_to_fresh_factor,
        exception_crops,
        food_to_group,
        food_loss_waste,
    )
    add_feed_supply_links(
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
    add_feed_to_animal_product_links(
        n,
        animal_product_list,
        feed_to_products,
        ruminant_feed_categories,
        cfg_countries,
    )
    add_food_group_buses_and_loads(
        n,
        food_group_list,
        food_groups,
        snakemake.params.food_groups,
        cfg_countries,
        population,
        per_country_equal=baseline_equals,
    )
    add_macronutrient_loads(
        n,
        macronutrient_cfg,
        cfg_countries,
        population,
        nutrient_units,
    )
    add_food_nutrition_links(
        n,
        food_list,
        foods,
        food_groups,
        nutrition,
        nutrient_units,
        cfg_countries,
    )

    # Add crop trading hubs and links (hierarchical trade network)
    add_crop_trade_hubs_and_links(
        n, snakemake.params.trade, regions_df, cfg_countries, list(crop_list)
    )
    add_animal_product_trade_hubs_and_links(
        n,
        snakemake.params.trade,
        regions_df,
        cfg_countries,
        animal_product_list,
    )

    logger.info("Network summary:")
    logger.info("Carriers: %d", len(n.carriers))
    logger.info("Buses: %d", len(n.buses))
    logger.info("Stores: %d", len(n.stores))
    logger.info("Links: %d", len(n.links))

    n.export_to_netcdf(snakemake.output.network)
