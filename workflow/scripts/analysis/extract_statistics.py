# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract production and consumption statistics from solved networks.

This script extracts key statistics from solved networks using PyPSA's
statistics module for efficient aggregation of dispatch flows:
- Crop production by crop, region, and country (Mt)
- Land use by crop, region, resource class, water supply, and country (Mha)
- Animal production by product and country (Mt)
- Food consumption by food and country (Mt, g/person/day)
- Food group consumption by food group and country (Mt, g/person/day)

Uses actual dispatch flows (p0, p1, etc.) rather than p_nom_opt * efficiency
for more accurate results that reflect actual model solutions.
"""

import re

import pandas as pd
import pypsa

from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE, PJ_TO_KCAL
from workflow.scripts.population import get_country_population

_FEED_CATEGORY_LABELS = {
    "ruminant_forage": "Grass & leaves",
    "ruminant_roughage": "Crop residues",
    "ruminant_grain": "Grains",
    "ruminant_protein": "Oilseed cakes",
    "monogastric_grain": "Grains",
    "monogastric_low_quality": "By-products",
    "monogastric_protein": "Oilseed cakes",
}

_PRODUCT_TO_ANIMAL = {
    "meat-cattle": "Cattle",
    "dairy": "Cattle",
    "meat-pig": "Pigs",
    "meat-chicken": "Chicken",
    "eggs": "Chicken",
    "meat-sheep": "Sheep",
    "meat-goat": "Goats",
    "meat-buffalo": "Buffalo",
    "dairy-buffalo": "Buffalo",
    "milk-sheep": "Sheep",
    "milk-goat": "Goats",
    "milk-buffalo": "Buffalo",
}


def _get_output_ports(n: pypsa.Network, carrier: str) -> list[str]:
    """Get list of output port indices for links with the given carrier.

    Detects ports dynamically from the link schema (bus1, bus2, ...).

    Parameters
    ----------
    n : pypsa.Network
        Network to query
    carrier : str
        Link carrier to check

    Returns
    -------
    list[str]
        List of port index strings (e.g., ["1", "2", "3"])
    """
    links = n.links.static
    sample_link = links[links["carrier"] == carrier].iloc[0]

    ports = []
    for col in sample_link.index:
        match = re.match(r"^bus(\d+)$", col)
        if match and int(match.group(1)) >= 1 and pd.notna(sample_link[col]):
            ports.append(match.group(1))
    return ports


def _extract_multi_crop_production(n: pypsa.Network) -> pd.DataFrame:
    """Extract production from multicropping links.

    Multicropping links have multiple output buses (bus1, bus2, ...) each
    connecting to a different crop bus. The crop must be looked up from
    the output bus's crop column.

    Parameters
    ----------
    n : pypsa.Network
        Network to query

    Returns
    -------
    pd.DataFrame
        Columns: crop, region, country, production_mt
    """
    links = n.links.static
    multi_mask = links["carrier"] == "crop_production_multi"
    multi_links = links[multi_mask]

    if multi_links.empty:
        return pd.DataFrame(columns=["crop", "region", "country", "production_mt"])

    output_ports = _get_output_ports(n, "crop_production_multi")

    # Build a dataframe per port, then concatenate
    port_dfs = []
    for port in output_ports:
        bus_col = f"bus{port}"
        p_col = f"p{port}"

        # Get bus names and check if they're crop buses
        bus_names = multi_links[bus_col]
        is_crop_bus = bus_names.str.startswith("crop:", na=False)

        # Skip ports with no crop buses
        if not is_crop_bus.any():
            continue

        # Check which links have dynamic data (PyPSA filters out zero-dispatch links)
        p_df = n.links.dynamic[p_col]
        valid_links = multi_links.index.intersection(p_df.columns)
        if valid_links.empty:
            continue

        # Link output ports are negative in PyPSA; flip sign to get positive production.
        production = (
            -p_df[valid_links].sum(axis=0).reindex(multi_links.index, fill_value=0.0)
        )

        # Map bus names to crops via buses.static
        crop = bus_names.map(n.buses.static["crop"])

        # Build dataframe for this port
        port_df = pd.DataFrame(
            {
                "crop": crop.values,
                "region": multi_links["region"].values,
                "country": multi_links["country"].values,
                "production_mt": production.values,
            }
        )

        # Filter to valid crop buses with positive production
        port_df = port_df[is_crop_bus.values & (port_df["production_mt"] > 0)]
        port_dfs.append(port_df)

    # Filter out empty DataFrames to avoid FutureWarning on concat
    port_dfs = [df for df in port_dfs if not df.empty]
    if not port_dfs:
        return pd.DataFrame(columns=["crop", "region", "country", "production_mt"])
    df = pd.concat(port_dfs, ignore_index=True)

    return df.groupby(["crop", "region", "country"], as_index=False)[
        "production_mt"
    ].sum()


def extract_crop_production(n: pypsa.Network) -> pd.DataFrame:
    """Extract crop production by crop, region, and country.

    Uses PyPSA statistics.supply() with bus_carrier and carrier filtering to
    extract actual dispatch flows to crop buses from production links only,
    which is more accurate than p_nom_opt * efficiency.

    Sources:
    - Single-crop links (produce_{crop}): p1 flow to crop buses
    - Multicropping links (crop_production_multi): p1, p2, etc. flows to crop buses
    - Grassland links (produce_grassland): p1 flow to feed buses

    Returns
    -------
    pd.DataFrame
        Columns: crop, region, country, production_mt
    """
    results = []

    # Get all crop bus carriers (crop_wheat, crop_maize, etc.)
    crop_bus_carriers = [
        c for c in n.buses.static["carrier"].unique() if c.startswith("crop_")
    ]

    # Single-crop production
    production = n.statistics.supply(
        components="Link",
        carrier=["crop_production"],
        bus_carrier=crop_bus_carriers,
        groupby=["crop", "region", "country"],
        nice_names=False,
    )
    if not production.empty:
        df = production.to_frame("production_mt").reset_index()
        df = df.dropna(subset=["crop", "region", "country"])
        results.append(df)

    # Grassland production: output to feed bus (feed_ruminant_forage)
    feed_bus_carriers = [
        c for c in n.buses.static["carrier"].unique() if c.startswith("feed_")
    ]
    grassland = n.statistics.supply(
        components="Link",
        carrier="grassland_production",
        bus_carrier=feed_bus_carriers,
        groupby=["region", "country"],
        nice_names=False,
    )
    if not grassland.empty:
        df = grassland.to_frame("production_mt").reset_index()
        df = df.dropna(subset=["region", "country"])
        df["crop"] = "grassland"
        results.append(df[["crop", "region", "country", "production_mt"]])

    # Multicropping: needs custom logic to lookup crop from output bus
    multi_production = _extract_multi_crop_production(n)
    results.append(multi_production)

    # Filter out empty DataFrames to avoid FutureWarning on concat
    results = [r for r in results if not r.empty]
    if not results:
        return pd.DataFrame(columns=["crop", "region", "country", "production_mt"])
    df = pd.concat(results, ignore_index=True)

    # Aggregate by crop, region, country (in case of duplicates)
    df = df.groupby(["crop", "region", "country"], as_index=False)[
        "production_mt"
    ].sum()

    return df.sort_values(["country", "crop", "region"]).reset_index(drop=True)


def _extract_multi_crop_land_use(n: pypsa.Network) -> pd.DataFrame:
    """Extract land use from multicropping links with yield-ratio attribution.

    For multicropping links, the total land use (withdrawal at port 0) is
    attributed to individual crops proportionally by their yield (efficiency).

    Parameters
    ----------
    n : pypsa.Network
        Network to query

    Returns
    -------
    pd.DataFrame
        Columns: crop, region, resource_class, water_supply, country, area_mha
    """
    columns = [
        "crop",
        "region",
        "resource_class",
        "water_supply",
        "country",
        "area_mha",
    ]

    links = n.links.static
    multi_mask = links["carrier"] == "crop_production_multi"
    multi_links = links[multi_mask]

    if multi_links.empty:
        return pd.DataFrame(columns=columns)

    output_ports = _get_output_ports(n, "crop_production_multi")

    # Get land use per link (sum of p0 over snapshots)
    # Handle case where some links may be filtered from dynamic data (zero dispatch)
    p0_df = n.links.dynamic["p0"]
    valid_links = multi_links.index.intersection(p0_df.columns)
    if valid_links.empty:
        return pd.DataFrame(columns=columns)
    land_use = p0_df[valid_links].sum(axis=0).reindex(multi_links.index, fill_value=0.0)

    # Build dataframe of (link, crop, efficiency) for each port, then stack
    port_dfs = []
    for port in output_ports:
        bus_col = f"bus{port}"
        eff_col = "efficiency" if port == "1" else f"efficiency{port}"

        bus_names = multi_links[bus_col]
        is_crop_bus = bus_names.str.startswith("crop:", na=False)
        efficiency = multi_links[eff_col].fillna(0.0)
        crop = bus_names.map(n.buses.static["crop"])

        port_df = pd.DataFrame(
            {
                "link": multi_links.index,
                "crop": crop.values,
                "efficiency": efficiency.values,
                "is_crop_bus": is_crop_bus.values,
            }
        )
        port_dfs.append(port_df)

    # Stack all ports; filter out empty DataFrames to avoid FutureWarning
    port_dfs = [df for df in port_dfs if not df.empty]
    if not port_dfs:
        return pd.DataFrame(columns=columns)
    all_ports = pd.concat(port_dfs, ignore_index=True)

    # Filter to valid crop buses with positive efficiency
    all_ports = all_ports[all_ports["is_crop_bus"] & (all_ports["efficiency"] > 0)]

    # Compute total yield per link and yield ratio
    all_ports["total_yield"] = all_ports.groupby("link")["efficiency"].transform("sum")
    all_ports["yield_ratio"] = all_ports["efficiency"] / all_ports["total_yield"]

    # Map land use and compute attributed area
    all_ports["land_use"] = all_ports["link"].map(land_use)
    all_ports["area_mha"] = all_ports["land_use"] * all_ports["yield_ratio"]

    # Filter to positive land use
    all_ports = all_ports[all_ports["land_use"] > 0]

    # Add link metadata
    all_ports["region"] = all_ports["link"].map(multi_links["region"])
    all_ports["resource_class"] = all_ports["link"].map(multi_links["resource_class"])
    all_ports["water_supply"] = all_ports["link"].map(multi_links["water_supply"])
    all_ports["country"] = all_ports["link"].map(multi_links["country"])

    return all_ports.groupby(
        ["crop", "region", "resource_class", "water_supply", "country"], as_index=False
    )["area_mha"].sum()


def extract_land_use(n: pypsa.Network) -> pd.DataFrame:
    """Extract land use by crop, region, resource class, water supply, and country.

    Uses direct dispatch data extraction (p0 flows) to get actual land utilization
    from production links. The statistics API's bus_carrier filtering doesn't work
    well with link-level groupby columns, so we extract dispatch directly.

    For multicropping, land is attributed to individual crops by yield ratio.

    Returns
    -------
    pd.DataFrame
        Columns: crop, region, resource_class, water_supply, country, area_mha
    """
    links = n.links.static
    columns = [
        "crop",
        "region",
        "resource_class",
        "water_supply",
        "country",
        "area_mha",
    ]

    # Get all production links (excluding multi, handled separately)
    produce_mask = links["carrier"].isin(["crop_production", "grassland_production"])
    produce_links = links[produce_mask]

    results = []

    p0 = n.links.dynamic["p0"]

    # Get p0 values for produce links (sum over snapshots)
    valid_links = produce_links.index.intersection(p0.columns)
    land_use = p0[valid_links].sum(axis=0)

    # Build DataFrame with link metadata and land use
    df = produce_links.loc[
        valid_links,
        ["crop", "region", "resource_class", "water_supply", "country", "carrier"],
    ]
    df = df.assign(area_mha=land_use.values)

    # Handle grassland: fill crop column and default water_supply
    grassland_mask = df["carrier"] == "grassland_production"
    df.loc[grassland_mask, "crop"] = "grassland"
    df.loc[grassland_mask & df["water_supply"].isna(), "water_supply"] = "rainfed"

    # Filter to positive land use only
    df = df[df["area_mha"] > 0]
    results.append(df[columns])

    # Multicropping: custom logic for yield-ratio attribution
    multi_land = _extract_multi_crop_land_use(n)
    results.append(multi_land)

    # Filter out empty DataFrames to avoid FutureWarning on concat
    results = [r for r in results if not r.empty]
    if not results:
        return pd.DataFrame(columns=columns)
    df = pd.concat(results, ignore_index=True)

    # Aggregate by all dimensions
    df = df.groupby(
        ["crop", "region", "resource_class", "water_supply", "country"], as_index=False
    )["area_mha"].sum()

    return df.sort_values(
        ["country", "crop", "region", "resource_class", "water_supply"]
    ).reset_index(drop=True)


def extract_animal_production(n: pypsa.Network) -> pd.DataFrame:
    """Extract animal production by product and country.

    Uses PyPSA statistics.supply() with bus_carrier filtering to extract
    actual dispatch flows to product buses from links with the `product` column set.

    Returns
    -------
    pd.DataFrame
        Columns: product, country, production_mt
    """
    links = n.links.static

    # Filter to links with product column set (exclude empty strings and 'nan' strings)
    product_mask = (
        links["product"].notna()
        & (links["product"] != "")
        & (links["product"] != "nan")
    )
    animal_links = links[product_mask]

    # Get unique carriers for animal products
    animal_carriers = animal_links["carrier"].unique().tolist()

    # Get product bus carriers (food_dairy, food_meat-cattle, etc.)
    # The product column contains values like 'dairy', 'meat-cattle', etc.
    # These link to food buses with carrier like food_dairy, food_meat-cattle
    products = animal_links["product"].unique()
    product_bus_carriers = [f"food_{p}" for p in products]

    production = n.statistics.supply(
        components="Link",
        carrier=animal_carriers,
        bus_carrier=product_bus_carriers,
        groupby=["product", "country"],
        nice_names=False,
    )

    if production.empty:
        return pd.DataFrame(columns=["product", "country", "production_mt"])

    df = production.to_frame("production_mt").reset_index()
    df = df.dropna(subset=["product", "country"])
    # Also filter out 'nan' string values that might come from groupby
    df = df[df["product"] != "nan"]

    # Aggregate by product and country (in case of duplicates)
    df = df.groupby(["product", "country"], as_index=False)["production_mt"].sum()

    return df.sort_values(["country", "product"]).reset_index(drop=True)


def _get_nutrient_ports(n: pypsa.Network) -> list[str]:
    """Get list of port indices that connect to nutrient buses.

    Parameters
    ----------
    n : pypsa.Network
        Network to query

    Returns
    -------
    list[str]
        List of port index strings (e.g., ["1", "2", "3", "4"])
    """
    links = n.links.static
    consume_links = links[links["carrier"] == "food_consumption"]
    sample_link = consume_links.iloc[0]

    nutrient_carriers = {"protein", "carb", "fat", "cal"}
    ports = []

    for col in sample_link.index:
        match = re.match(r"^bus(\d+)$", col)
        if match and int(match.group(1)) >= 1:
            bus_name = sample_link[col]
            if pd.notna(bus_name) and bus_name in n.buses.static.index:
                carrier = n.buses.static.at[bus_name, "carrier"]
                if carrier in nutrient_carriers:
                    ports.append(match.group(1))
    return ports


def _extract_nutrient_flows(
    n: pypsa.Network, consume_carriers: list[str], groupby: list[str]
) -> dict[str, pd.Series]:
    """Extract nutrient flows from consume links using statistics API.

    Parameters
    ----------
    n : pypsa.Network
        Network to query
    consume_carriers : list[str]
        List of consume link carriers
    groupby : list[str]
        Columns to group by

    Returns
    -------
    dict[str, pd.Series]
        Dict mapping nutrient names to grouped Series
    """
    nutrients = {}

    # Detect nutrient ports dynamically
    nutrient_ports = _get_nutrient_ports(n)

    # Nutrient bus carriers and their output column names
    nutrient_map = {
        "protein": "protein_mt",
        "carb": "carb_mt",
        "fat": "fat_mt",
        "cal": "cal_pj",
    }

    for nutrient, col_name in nutrient_map.items():
        flow = n.statistics.supply(
            components="Link",
            carrier=consume_carriers,
            at_port=nutrient_ports,
            bus_carrier=nutrient,
            groupby=groupby,
            nice_names=False,
        )
        nutrients[col_name] = flow

    return nutrients


def _extract_consumption(
    n: pypsa.Network, groupby: list[str], group_col: str
) -> pd.DataFrame:
    """Extract food consumption and macronutrients grouped by specified columns.

    Shared implementation for food and food group consumption extraction.

    Parameters
    ----------
    n : pypsa.Network
        Solved network
    groupby : list[str]
        Columns to group by (e.g., ["food", "country"] or ["food_group", "country"])
    group_col : str
        Primary grouping column name for output ("food" or "food_group")

    Returns
    -------
    pd.DataFrame
        Consumption data with mass, nutrient, and per-capita columns
    """
    consume_carriers = ["food_consumption"]

    # Get food bus carriers (food_wheat, food_bread, etc.)
    food_bus_carriers = [
        c for c in n.buses.static["carrier"].unique() if c.startswith("food_")
    ]

    # Food consumption = withdrawal from food buses
    consumption = n.statistics.withdrawal(
        components="Link",
        carrier=consume_carriers,
        bus_carrier=food_bus_carriers,
        groupby=groupby,
        nice_names=False,
    ).abs()

    if consumption.empty:
        columns = [
            *groupby,
            "consumption_mt",
            "protein_mt",
            "carb_mt",
            "fat_mt",
            "cal_pj",
            "consumption_g_per_person_day",
            "protein_g_per_person_day",
            "carb_g_per_person_day",
            "fat_g_per_person_day",
            "cal_kcal_per_person_day",
        ]
        return pd.DataFrame(columns=columns)

    df = consumption.to_frame("consumption_mt").reset_index()
    df = df.dropna(subset=groupby)

    # Extract nutrient flows
    nutrients = _extract_nutrient_flows(n, consume_carriers, groupby)

    # Merge nutrient columns
    for col_name, flow in nutrients.items():
        nutrient_df = flow.to_frame(col_name).reset_index()
        df = df.merge(nutrient_df, on=groupby, how="left")

    # Fill NaN nutrients with 0
    for col in ["protein_mt", "carb_mt", "fat_mt", "cal_pj"]:
        df[col] = df[col].fillna(0.0)

    # Aggregate by groupby columns (in case of duplicates)
    agg_cols = ["consumption_mt", "protein_mt", "carb_mt", "fat_mt", "cal_pj"]
    df = df.groupby(groupby, as_index=False)[agg_cols].sum()

    # Add per-capita values
    population = get_country_population(n)

    # Compute per-capita factor - will raise KeyError if country missing
    df["_per_capita_factor"] = df["country"].map(population) * DAYS_PER_YEAR

    df["consumption_g_per_person_day"] = (
        df["consumption_mt"] * GRAMS_PER_MEGATONNE / df["_per_capita_factor"]
    )
    df["protein_g_per_person_day"] = (
        df["protein_mt"] * GRAMS_PER_MEGATONNE / df["_per_capita_factor"]
    )
    df["carb_g_per_person_day"] = (
        df["carb_mt"] * GRAMS_PER_MEGATONNE / df["_per_capita_factor"]
    )
    df["fat_g_per_person_day"] = (
        df["fat_mt"] * GRAMS_PER_MEGATONNE / df["_per_capita_factor"]
    )
    df["cal_kcal_per_person_day"] = df["cal_pj"] * PJ_TO_KCAL / df["_per_capita_factor"]

    df = df.drop(columns=["_per_capita_factor"])

    return df.sort_values(["country", group_col]).reset_index(drop=True)


def extract_food_consumption(n: pypsa.Network) -> pd.DataFrame:
    """Extract food consumption and macronutrients by food and country.

    Uses PyPSA statistics with bus_carrier filtering to extract consumption
    (withdrawal from food buses) and nutrient flows (supply to nutrient buses).

    Returns
    -------
    pd.DataFrame
        Columns: food, country, consumption_mt, protein_mt, carb_mt, fat_mt, cal_pj,
                 consumption_g_per_person_day, protein_g_per_person_day,
                 carb_g_per_person_day, fat_g_per_person_day, cal_kcal_per_person_day
    """
    return _extract_consumption(n, groupby=["food", "country"], group_col="food")


def extract_feed_by_category(n: pypsa.Network) -> pd.DataFrame:
    """Extract total feed use grouped by feed category.

    Reads ``p0`` (feed input) of ``animal_production`` links and maps raw
    feed category names to human-readable labels.

    Returns
    -------
    pd.DataFrame
        Columns: category, mt_dm
    """
    links = n.links.static
    feed_links = links[
        (links["carrier"] == "animal_production") & links["feed_category"].notna()
    ]
    if feed_links.empty:
        return pd.DataFrame(columns=["category", "mt_dm"])

    snapshot = n.snapshots[-1]
    p0 = n.links.dynamic.p0.loc[snapshot]

    result: dict[str, float] = {}
    for link in feed_links.index:
        flow = abs(float(p0.get(link, 0.0)))
        if flow < 1e-12:
            continue
        raw = str(feed_links.at[link, "feed_category"])
        category = _FEED_CATEGORY_LABELS.get(raw, raw.replace("_", " ").title())
        result[category] = result.get(category, 0.0) + flow

    rows = [{"category": cat, "mt_dm": val} for cat, val in result.items()]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["category", "mt_dm"])


def extract_feed_by_animal(n: pypsa.Network) -> pd.DataFrame:
    """Extract total feed use grouped by animal type.

    Reads ``p0`` (feed input) of ``animal_production`` links and maps product
    names to animal type labels.

    Returns
    -------
    pd.DataFrame
        Columns: animal, mt_dm
    """
    links = n.links.static
    feed_links = links[
        (links["carrier"] == "animal_production") & links["product"].notna()
    ]
    if feed_links.empty:
        return pd.DataFrame(columns=["animal", "mt_dm"])

    snapshot = n.snapshots[-1]
    p0 = n.links.dynamic.p0.loc[snapshot]

    result: dict[str, float] = {}
    for link in feed_links.index:
        flow = abs(float(p0.get(link, 0.0)))
        if flow < 1e-12:
            continue
        product = str(feed_links.at[link, "product"])
        animal = _PRODUCT_TO_ANIMAL.get(product, product.replace("_", " ").title())
        result[animal] = result.get(animal, 0.0) + flow

    rows = [{"animal": a, "mt_dm": val} for a, val in result.items()]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["animal", "mt_dm"])


def extract_luc_breakdown(
    n: pypsa.Network,
    country_to_continent: dict[str, str],
) -> pd.DataFrame:
    """Extract land-use-change breakdown by continent and land type.

    For each LUC-related link, extracts both emissions (flow * efficiency2,
    in MtCO2) and area (flow in Mha, signed: positive for expansion, negative
    for sparing).

    Parameters
    ----------
    n : pypsa.Network
        Solved network.
    country_to_continent : dict[str, str]
        Mapping from ISO3 country code to continent name.

    Returns
    -------
    pd.DataFrame
        Columns: groupby, category, emissions_mtco2, area_mha.
        ``groupby`` is either "continent" or "land_type".
    """
    snapshot = n.snapshots[-1]
    links = n.links.static
    p0 = n.links.dynamic.p0.loc[snapshot]

    # Build region -> country mapping from crop production links
    crop = links[links["carrier"] == "crop_production"]
    region_to_country: dict[str, str] = {}
    for _, row in crop[["region", "country"]].drop_duplicates().iterrows():
        if pd.notna(row["region"]) and pd.notna(row["country"]) and row["country"]:
            region_to_country[str(row["region"])] = str(row["country"])

    # (carrier, land_type_label, split_by_land_type, area_sign)
    carrier_configs = [
        ("land_conversion", "Cropland expansion", False, 1),
        ("new_to_pasture", "Pasture expansion", False, 1),
        ("spare_land", "Cropland sparing", False, -1),
        ("spare_existing_grassland", None, True, -1),
    ]

    # Accumulators: {(groupby, category): {emissions_mtco2, area_mha}}
    data: dict[tuple[str, str], dict[str, float]] = {}

    def _add(groupby_val: str, category: str, emissions: float, area: float) -> None:
        key = (groupby_val, category)
        if key not in data:
            data[key] = {"emissions_mtco2": 0.0, "area_mha": 0.0}
        data[key]["emissions_mtco2"] += emissions
        data[key]["area_mha"] += area

    for carrier, base_label, split_by_land_type, area_sign in carrier_configs:
        mask = links["carrier"] == carrier
        carrier_links = links[mask]
        if carrier_links.empty:
            continue

        for link in carrier_links.index:
            flow = float(p0.get(link, 0.0))
            if abs(flow) < 1e-12:
                continue

            eff2 = float(carrier_links.at[link, "efficiency2"])
            emissions = flow * eff2
            area = flow * area_sign

            # Land type grouping
            if split_by_land_type:
                lt = carrier_links.at[link, "land_type"]
                if pd.notna(lt) and lt == "marginal":
                    type_label = "Grassland sparing (marginal)"
                else:
                    type_label = "Grassland sparing (convertible)"
            else:
                type_label = base_label
            _add("land_type", type_label, emissions, area)

            # Continent grouping
            region = carrier_links.at[link, "region"]
            if pd.isna(region):
                continue
            country = region_to_country.get(str(region))
            if not country:
                continue
            continent = country_to_continent.get(country, "Other")
            _add("continent", continent, emissions, area)

    rows = []
    for (groupby_val, category), vals in data.items():
        rows.append(
            {
                "groupby": groupby_val,
                "category": category,
                "emissions_mtco2": vals["emissions_mtco2"],
                "area_mha": vals["area_mha"],
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["groupby", "category", "emissions_mtco2", "area_mha"]
        )

    return pd.DataFrame(rows)


def extract_food_group_consumption(n: pypsa.Network) -> pd.DataFrame:
    """Extract food consumption and macronutrients by food group and country.

    Uses PyPSA statistics with bus_carrier filtering to group by the `food_group`
    column that already exists on consume links.

    Returns
    -------
    pd.DataFrame
        Columns: food_group, country, consumption_mt, protein_mt, carb_mt, fat_mt, cal_pj,
                 consumption_g_per_person_day, protein_g_per_person_day,
                 carb_g_per_person_day, fat_g_per_person_day, cal_kcal_per_person_day
    """
    return _extract_consumption(
        n, groupby=["food_group", "country"], group_col="food_group"
    )
