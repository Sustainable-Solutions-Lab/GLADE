# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

import numpy as np
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


# Nutrient bus carriers and their output column names
_NUTRIENT_MAP = {
    "protein": "protein_mt",
    "carb": "carb_mt",
    "fat": "fat_mt",
    "cal": "cal_pj",
}

_CONSUMPTION_VALUE_COLS = ["consumption_mt", *_NUTRIENT_MAP.values()]


def _extract_consumption_detailed(n: pypsa.Network) -> pd.DataFrame:
    """Extract consumption and nutrient flows at (food, food_group, country) level.

    Uses one withdrawal call (mass from food buses) and one supply call (all
    four nutrient flows at once, grouped by bus_carrier); each statistics call
    scans every port of the full network, so minimizing the call count matters.

    Statistics are fetched unrounded and with zeros kept; rounding and
    zero-dropping happen after aggregation to the output level (in
    :func:`_finalize_consumption`) so results match a direct per-level
    statistics query.

    Returns
    -------
    pd.DataFrame
        Columns: food, food_group, country, consumption_mt, protein_mt,
        carb_mt, fat_mt, cal_pj. Empty (no columns) if the network has no
        consumption flows.
    """
    consume_carriers = ["food_consumption"]
    groupby = ["food", "food_group", "country"]

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
        round=False,
        drop_zero=False,
    ).abs()

    if consumption.empty:
        return pd.DataFrame()

    df = consumption.to_frame("consumption_mt").reset_index()
    df = df.dropna(subset=groupby)
    df = df.groupby(groupby, as_index=False)["consumption_mt"].sum()

    # Select the nutrient flows purely by ``bus_carrier`` (each consume link
    # has exactly one bus per nutrient, so this is unambiguous). We
    # deliberately do NOT pass ``at_port``: PyPSA resolves numeric port labels
    # positionally against a lexicographically-sorted port list, so once a
    # Link has >= 10 ports (``bus10`` exists, e.g. at finer
    # water.temporal_resolution) the labels ['1','2','3','4'] map to suffixes
    # '1','10','11','12' and silently select the wrong buses. Omitting at_port
    # scans all ports and filters by carrier, which is correct at any port
    # count and identical where both work.
    nutrient_flows = n.statistics.supply(
        components="Link",
        carrier=consume_carriers,
        bus_carrier=list(_NUTRIENT_MAP),
        groupby=[*groupby, "bus_carrier"],
        nice_names=False,
        round=False,
        drop_zero=False,
    )
    nutrients = (
        nutrient_flows.to_frame("value")
        .reset_index()
        .pivot_table(
            index=groupby, columns="bus_carrier", values="value", aggfunc="sum"
        )
        .rename(columns=_NUTRIENT_MAP)
        .reindex(columns=list(_NUTRIENT_MAP.values()))
        .reset_index()
    )

    df = df.merge(nutrients, on=groupby, how="left")
    for col in _NUTRIENT_MAP.values():
        df[col] = df[col].fillna(0.0)

    return df


def _finalize_consumption(
    n: pypsa.Network, detailed: pd.DataFrame, group_col: str
) -> pd.DataFrame:
    """Aggregate detailed consumption to (group_col, country) and add per-capita columns."""
    groupby = [group_col, "country"]

    if detailed.empty:
        columns = [
            *groupby,
            *_CONSUMPTION_VALUE_COLS,
            "consumption_g_per_person_day",
            "protein_g_per_person_day",
            "carb_g_per_person_day",
            "fat_g_per_person_day",
            "cal_kcal_per_person_day",
        ]
        return pd.DataFrame(columns=columns)

    df = detailed.groupby(groupby, as_index=False)[_CONSUMPTION_VALUE_COLS].sum()

    # Match the statistics module's default post-processing at this
    # aggregation level: round to 5 decimals, then drop rows whose
    # consumption rounds to zero.
    df[_CONSUMPTION_VALUE_COLS] = df[_CONSUMPTION_VALUE_COLS].round(5)
    df = df[df["consumption_mt"] != 0]

    # Add per-capita values
    population = get_country_population(n)

    # Surface missing countries explicitly: pandas .map silently produces
    # NaN for unmapped keys (the old "will raise KeyError" comment was
    # wrong). NaN per-capita factor would then propagate as NaN g/day.
    missing_countries = sorted(set(df["country"]) - set(population))
    if missing_countries:
        raise KeyError(
            f"Countries missing from population data: {missing_countries[:10]}"
        )
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


def extract_consumption_tables(
    n: pypsa.Network,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract food- and food-group-level consumption in one network pass.

    Consumption and nutrient flows are extracted once at (food, food_group,
    country) resolution, then aggregated to both output levels. Consume links
    carry validated ``food`` and ``food_group`` columns, so nothing is lost
    relative to extracting each level separately.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (food-level, food-group-level) consumption tables, each with mass,
        nutrient, and per-capita columns.
    """
    detailed = _extract_consumption_detailed(n)
    return (
        _finalize_consumption(n, detailed, group_col="food"),
        _finalize_consumption(n, detailed, group_col="food_group"),
    )


def _aggregate_animal_production_flows(
    n: pypsa.Network,
    *,
    metadata_col: str,
    label_map: dict[str, str],
    out_label_col: str,
) -> pd.DataFrame:
    """Sum animal_production p0 flows grouped by a metadata column.

    Shared backend for ``extract_feed_by_category`` and ``extract_feed_by_animal``:
    each animal_production link's bus0 draw is bucketed by ``metadata_col``
    (``feed_category`` or ``product``), then mapped to a human-readable label
    via ``label_map`` (falling back to ``"some_value".replace("_", " ").title()``
    for entries not in the map).
    """
    links = n.links.static
    feed_links = links[
        (links["carrier"] == "animal_production") & links[metadata_col].notna()
    ]
    out_cols = [out_label_col, "mt_dm"]
    if feed_links.empty:
        return pd.DataFrame(columns=out_cols)

    snapshot = n.snapshots[-1]
    flows = n.links.dynamic.p0.loc[snapshot].reindex(feed_links.index).abs()
    flows = flows[flows > 1e-12]
    if flows.empty:
        return pd.DataFrame(columns=out_cols)

    raw = feed_links.loc[flows.index, metadata_col].astype(str)
    label = raw.map(label_map).fillna(raw.str.replace("_", " ").str.title())
    return (
        flows.groupby(label.values)
        .sum()
        .rename_axis(out_label_col)
        .reset_index(name="mt_dm")
    )


def extract_feed_by_category(n: pypsa.Network) -> pd.DataFrame:
    """Extract total feed use grouped by feed category.

    Reads ``p0`` (feed input) of ``animal_production`` links and maps raw
    feed category names to human-readable labels.

    Returns
    -------
    pd.DataFrame
        Columns: category, mt_dm
    """
    return _aggregate_animal_production_flows(
        n,
        metadata_col="feed_category",
        label_map=_FEED_CATEGORY_LABELS,
        out_label_col="category",
    )


def extract_feed_by_animal(n: pypsa.Network) -> pd.DataFrame:
    """Extract total feed use grouped by animal type.

    Reads ``p0`` (feed input) of ``animal_production`` links and maps product
    names to animal type labels.

    Returns
    -------
    pd.DataFrame
        Columns: animal, mt_dm
    """
    return _aggregate_animal_production_flows(
        n,
        metadata_col="product",
        label_map=_PRODUCT_TO_ANIMAL,
        out_label_col="animal",
    )


# Source-of-supply categories used by ``extract_feed_by_source``. Atomic
# enough that downstream plots can decide how to relabel / aggregate, but
# coarse enough that a single solve produces O(50) rows after grouping
# rather than per-item explosion. All values are in tonnes DRY MATTER
# because every feed bus in the model is on a DM basis (see
# ``build_model/animals.py`` docstring on ``add_animal_production_links``).
_FEED_SOURCE_LABELS = {
    "grassland": "Grassland",
    "residue": "Crop residues",
    "fodder_crop": "Fodder crops",
    "grain_crop": "Grains",
    "protein_crop": "Oilseed cakes",
    "food_byproduct": "Food by-products",
    "exog_forage_cal": "Exog. forage (calibration)",
    "exog_roughage_cal": "Exog. roughage (calibration)",
    "exog_protein_cal": "Exog. protein (calibration)",
    "exog_browse": "Exog. browse / leaves",
    "exog_swill": "Exog. swill",
    "exog_other": "Exog. (other)",
}


# Feed categories whose crop-sourced supply maps to each source_key.
# Roughage covers low-digestibility whole crops (e.g. fodder beet, alfalfa
# routed by digestibility < 0.55 in categorize_feeds.py), which are
# naturally bundled under "Fodder crops". Crops routed as
# monogastric_low_quality (kitchen waste, swill-style by-products) are
# bundled with food by-products. Without an entry here the source falls
# through to "exog_other", which silently misattributes endogenous
# crops as exogenous supply.
_CROP_FEED_CATEGORY_TO_SOURCE_KEY = {
    "ruminant_forage": "fodder_crop",
    "ruminant_roughage": "fodder_crop",
    "ruminant_grain": "grain_crop",
    "monogastric_grain": "grain_crop",
    "ruminant_protein": "protein_crop",
    "monogastric_protein": "protein_crop",
    "monogastric_low_quality": "food_byproduct",
}


def _feed_conversion_source_keys(
    bus0_carrier: pd.Series, feed_category: pd.Series
) -> pd.Series:
    """Vectorised source_key classifier for feed_conversion links.

    Parameters
    ----------
    bus0_carrier
        The carrier of each link's input bus (``links.bus0.map(n.buses.carrier)``),
        e.g. ``crop_wheat`` / ``residue_wheat_straw`` / ``food_bread``. Using
        the carrier column rather than parsing bus names keeps the classifier
        decoupled from the bus-name convention.
    feed_category
        Per-link feed category from the link's own ``feed_category`` column.
    """
    keys = pd.Series("exog_other", index=bus0_carrier.index, dtype=object)
    keys.loc[bus0_carrier.str.startswith("residue_")] = "residue"
    keys.loc[bus0_carrier.str.startswith("food_")] = "food_byproduct"
    is_crop = bus0_carrier.str.startswith("crop_")
    crop_keys = feed_category.map(_CROP_FEED_CATEGORY_TO_SOURCE_KEY)
    keys.loc[is_crop & crop_keys.notna()] = crop_keys[is_crop & crop_keys.notna()]
    return keys


def _generator_source_keys(carrier: pd.Series, feed_category: pd.Series) -> pd.Series:
    """Vectorised source_key classifier for feed-bus generators.

    Slack generators (carriers ``slack_positive_feed`` / ``slack_negative_feed``)
    are penalty variables added by the build to keep the LP feasible under
    validation; they are not supply and the caller filters them out before
    this function sees them.
    """
    keys = pd.Series("exog_other", index=carrier.index, dtype=object)
    keys.loc[carrier.eq("exogenous_forage_cal")] = "exog_forage_cal"
    keys.loc[carrier.eq("exogenous_protein_cal")] = "exog_protein_cal"
    keys.loc[carrier.eq("exogenous_roughage_cal")] = "exog_roughage_cal"
    is_xog = carrier.eq("exogenous_feed")
    keys.loc[is_xog & feed_category.eq("ruminant_roughage")] = "exog_browse"
    keys.loc[is_xog & feed_category.eq("monogastric_low_quality")] = "exog_swill"
    return keys


# Slack generator carriers on feed buses. Positive slack is unmet demand
# materialised at penalty cost (not a real supply source); negative slack
# absorbs surplus dispatch (also not supply, and its sign is negative).
# Filtering both out of the supply mix is the only way to keep the
# attribution honest -- treating slack as supply would inflate the
# exogenous bucket by exactly the model's infeasibility.
_FEED_SLACK_CARRIERS = frozenset({"slack_positive_feed", "slack_negative_feed"})


def extract_feed_by_source(n: pypsa.Network) -> pd.DataFrame:
    """Decompose animal feed consumption by (animal, feed_category, source).

    Each ``animal_production`` link consumes from a single
    ``feed:{feed_category}:{country}`` bus. This function attributes that
    bus0 dispatch back to the **actual source of supply** at the feed
    bus, by computing the per-(country, feed_category) inflow mix from
    upstream links and generators and allocating each animal_production
    link's draw proportionally to that mix.

    Source taxonomy (column ``source``, human-readable; the raw key in
    ``_FEED_SOURCE_LABELS`` lives alongside in ``source_key`` for stable
    downstream filtering):

    - ``Grassland``: ``grassland_production`` links into a forage bus.
    - ``Crop residues``: ``feed_conversion`` from a ``residue:`` bus.
    - ``Fodder crops`` / ``Grains`` / ``Oilseed cakes``:
      ``feed_conversion`` from a ``crop:`` bus, classified by the
      target feed category.
    - ``Food by-products``: ``feed_conversion`` from a ``food:`` bus
      (DDGS, oilseed meals, brans, molasses, etc.).
    - ``Exog. forage (calibration)``, ``Exog. roughage (calibration)``,
      ``Exog. protein (calibration)``: calibration-residual generators on
      the forage / roughage / protein feed buses (carriers
      ``exogenous_forage_cal`` / ``exogenous_roughage_cal`` /
      ``exogenous_protein_cal``).
    - ``Exog. browse / leaves``: ``exogenous_feed`` generators on the
      ``ruminant_roughage`` bus (GLEAM's LEAVES + browse + other items
      the model does not produce endogenously).
    - ``Exog. swill``: ``exogenous_feed`` generators on the
      ``monogastric_low_quality`` bus (food-waste swill, etc.).

    Mass basis: ``mt_dm`` is **tonnes dry matter per year**, on the
    feed-bus basis (which is uniformly DM across all categories).

    Inter-country trade in feed nets out at the global level for a
    given feed_category (each export is paired with an import), so the
    attribution is computed on the GLOBAL supply mix per feed_category
    rather than per (country, feed_category). Per-country attribution
    would credit each importer's animal draws to its own (possibly
    empty) domestic supply mix, dropping countries whose feed bus is
    fed exclusively by imports; aggregating globally first sidesteps
    that and keeps the mt_dm totals consistent with
    ``extract_feed_by_category`` and ``extract_feed_by_animal``.

    Returns
    -------
    pd.DataFrame
        Columns: ``product``, ``animal``, ``feed_category``,
        ``source_key``, ``source``, ``mt_dm``.
    """
    cols = ["product", "animal", "feed_category", "source_key", "source", "mt_dm"]
    links = n.links.static
    ap_mask = (
        (links["carrier"] == "animal_production")
        & links["product"].notna()
        & links["feed_category"].notna()
    )
    if not ap_mask.any():
        return pd.DataFrame(columns=cols)

    snapshot = n.snapshots[-1]
    p0 = n.links.dynamic.p0.loc[snapshot]
    p_gen = n.generators.dynamic.p.loc[snapshot]
    bus_carrier = n.buses.static["carrier"]
    bus_feed_category = n.buses.static["feed_category"]

    # --- Step 1: build a global table of non-trade inflow mass per
    # (feed_category, source_key), aggregated across all countries.
    # Vectorised across feed_conversion + grassland_production links
    # and all non-slack feed-bus generators. trade_feed links are
    # deliberately excluded (each export is paired with an import, so
    # they sum to zero per feed_category globally).
    inflow_frames: list[pd.DataFrame] = []

    fc = links[links["carrier"] == "feed_conversion"].copy()
    if not fc.empty:
        fc["flow_in"] = p0.reindex(fc.index).abs().to_numpy()
        fc = fc[fc["flow_in"] > 1e-12]
    if not fc.empty:
        fc["flow_out"] = fc["flow_in"] * pd.to_numeric(
            fc["efficiency"], errors="coerce"
        ).fillna(1.0)
        fc["source_key"] = _feed_conversion_source_keys(
            fc["bus0"].map(bus_carrier), fc["feed_category"]
        )
        inflow_frames.append(
            fc[["feed_category", "source_key", "flow_out"]].rename(
                columns={"flow_out": "supply_mt_dm"}
            )
        )

    gp = links[links["carrier"] == "grassland_production"].copy()
    if not gp.empty:
        gp["flow_in"] = p0.reindex(gp.index).abs().to_numpy()
        gp = gp[gp["flow_in"] > 1e-12]
    if not gp.empty:
        gp["flow_out"] = gp["flow_in"] * pd.to_numeric(
            gp["efficiency"], errors="coerce"
        ).fillna(1.0)
        gp["source_key"] = "grassland"
        inflow_frames.append(
            gp[["feed_category", "source_key", "flow_out"]].rename(
                columns={"flow_out": "supply_mt_dm"}
            )
        )

    gens = n.generators.static
    feed_gen = gens[
        gens["bus"].map(bus_carrier).str.startswith("feed_", na=False)
        & ~gens["carrier"].isin(_FEED_SLACK_CARRIERS)
    ].copy()
    if not feed_gen.empty:
        feed_gen["flow"] = p_gen.reindex(feed_gen.index).abs().to_numpy()
        feed_gen = feed_gen[feed_gen["flow"] > 1e-12]
    if not feed_gen.empty:
        feed_gen["feed_category"] = feed_gen["bus"].map(bus_feed_category)
        feed_gen["source_key"] = _generator_source_keys(
            feed_gen["carrier"].astype(str), feed_gen["feed_category"]
        )
        inflow_frames.append(
            feed_gen[["feed_category", "source_key", "flow"]].rename(
                columns={"flow": "supply_mt_dm"}
            )
        )

    if not inflow_frames:
        return pd.DataFrame(columns=cols)

    supply = (
        pd.concat(inflow_frames, ignore_index=True)
        .groupby(["feed_category", "source_key"], as_index=False)["supply_mt_dm"]
        .sum()
    )
    category_totals = (
        supply.groupby("feed_category", as_index=False)["supply_mt_dm"]
        .sum()
        .rename(columns={"supply_mt_dm": "category_total_mt_dm"})
    )
    supply = supply.merge(category_totals, on="feed_category")
    supply = supply[supply["category_total_mt_dm"] > 0]
    supply["source_share"] = supply["supply_mt_dm"] / supply["category_total_mt_dm"]

    # --- Step 2: animal_production draws, vectorised join with the
    # global per-feed_category source mix. Each animal_production link
    # expands into one row per source_key with mt_dm = flow * share.
    ap = links[ap_mask].copy()
    ap["flow"] = p0.reindex(ap.index).abs().to_numpy()
    ap = ap[ap["flow"] > 1e-12]
    if ap.empty:
        return pd.DataFrame(columns=cols)
    ap_cols = ["product", "feed_category", "flow"]
    attributed = ap[ap_cols].merge(
        supply[["feed_category", "source_key", "source_share"]],
        on="feed_category",
        how="inner",
    )
    attributed["mt_dm"] = attributed["flow"] * attributed["source_share"]

    out = attributed.groupby(
        ["product", "feed_category", "source_key"], as_index=False
    )["mt_dm"].sum()
    out["animal"] = (
        out["product"]
        .map(_PRODUCT_TO_ANIMAL)
        .fillna(out["product"].str.replace("_", " ").str.title())
    )
    out["source"] = out["source_key"].map(_FEED_SOURCE_LABELS).fillna(out["source_key"])
    return out[cols].reset_index(drop=True)


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
    out_cols = ["groupby", "category", "emissions_mtco2", "area_mha"]

    # Region -> country mapping from crop production links. Region and
    # country are both link-level metadata columns, so just take the
    # first non-null pairing per region.
    region_to_country = (
        links[links["carrier"] == "crop_production"]
        .dropna(subset=["region", "country"])
        .drop_duplicates(subset=["region"])
        .set_index("region")["country"]
    )

    # (carrier, land_type_label, split_by_land_type, area_sign)
    carrier_configs = [
        ("land_conversion", "Cropland expansion", False, 1),
        ("new_to_pasture", "Pasture expansion", False, 1),
        ("spare_land", "Cropland sparing", False, -1),
        ("spare_existing_grassland", None, True, -1),
    ]

    frames: list[pd.DataFrame] = []
    for carrier, base_label, split_by_land_type, area_sign in carrier_configs:
        sub = links[links["carrier"] == carrier]
        if sub.empty:
            continue
        flow = p0.reindex(sub.index).fillna(0.0)
        mask = flow.abs() > 1e-12
        if not mask.any():
            continue
        sub = sub.loc[mask].copy()
        flow = flow.loc[mask]
        sub["emissions_mtco2"] = flow * pd.to_numeric(sub["efficiency2"]).astype(float)
        sub["area_mha"] = flow * area_sign
        if split_by_land_type:
            sub["land_type_label"] = np.where(
                sub["land_type"] == "marginal",
                "Grassland sparing (marginal)",
                "Grassland sparing (convertible)",
            )
        else:
            sub["land_type_label"] = base_label
        frames.append(sub[["region", "land_type_label", "emissions_mtco2", "area_mha"]])

    if not frames:
        return pd.DataFrame(columns=out_cols)

    flat = pd.concat(frames, ignore_index=True)

    by_land = (
        flat.groupby("land_type_label", as_index=False)[["emissions_mtco2", "area_mha"]]
        .sum()
        .rename(columns={"land_type_label": "category"})
        .assign(groupby="land_type")
    )

    cont = flat.assign(country=flat["region"].map(region_to_country))
    cont = cont.dropna(subset=["country"])
    cont = cont.assign(
        continent=cont["country"].map(country_to_continent).fillna("Other"),
    )
    by_continent = (
        cont.groupby("continent", as_index=False)[["emissions_mtco2", "area_mha"]]
        .sum()
        .rename(columns={"continent": "category"})
        .assign(groupby="continent")
    )

    return pd.concat([by_land, by_continent], ignore_index=True)[out_cols]
