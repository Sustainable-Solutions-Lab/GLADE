# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Trade network components for the food systems model.

This module handles the creation of hierarchical trade networks for crops
and foods, using clustering-based hub systems for efficient trade routing.
"""

import itertools
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from sklearn.cluster import KMeans

from .. import constants

logger = logging.getLogger(__name__)


def compute_trade_hubs(regions_gdf: gpd.GeoDataFrame, n_hubs: int) -> np.ndarray:
    """Run KMeans once and return cluster centers in EPSG:6933 coordinates.

    Args:
        regions_gdf: GeoDataFrame with regional geometries.
        n_hubs: Desired number of trade hubs.

    Returns:
        Array of shape (k, 2) with hub center coordinates in EPSG:6933.
    """
    gdf_ee = regions_gdf.to_crs(6933)
    cent = gdf_ee.geometry.centroid
    region_coords = np.column_stack([cent.x.values, cent.y.values])
    k = min(max(1, n_hubs), len(region_coords))
    if k < n_hubs:
        logger.info(
            "Reducing hub count from %d to %d (regions=%d)",
            n_hubs,
            k,
            len(region_coords),
        )
    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    km.fit_predict(region_coords)
    return km.cluster_centers_


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

    def _to_bnusd_per_mt(cost_per_tonne: float) -> float:
        # Trade inputs are provided as USD per tonne-km; convert to bnUSD per Mt-km.
        return cost_per_tonne * constants.USD_TO_BNUSD / constants.TONNE_TO_MEGATONNE

    # Get default cost from config hierarchy
    if default_cost_key is not None:
        default_cost = float(trade_config[default_cost_key])
    else:
        default_cost = float(trade_config[fallback_cost_key])

    default_cost = _to_bnusd_per_mt(default_cost)

    item_costs = {str(item): default_cost for item in items}

    if categories_key is None:
        return item_costs, default_cost

    # Override with category-specific costs
    categories = trade_config[categories_key]
    for _category, cfg in categories.items():
        category_cost = float(cfg["cost_per_km"])
        category_cost = _to_bnusd_per_mt(category_cost)
        configured_items = cfg[category_item_key]

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
    hub_centers: np.ndarray,
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
    link_carrier: str,
    item_column: str,
) -> None:
    """Shared implementation for adding trade hubs and links for a set of items."""

    n_hubs = len(hub_centers)
    item_costs, _default_cost = _resolve_trade_costs(
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

    items = [str(i) for i in dict.fromkeys(items)]
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

    centers = hub_centers
    hub_ids = list(range(n_hubs))

    pairs = pd.MultiIndex.from_product([tradable_items, hub_ids], names=["item", "hub"])
    pairs_df = pairs.to_frame(index=False)
    hub_bus_names = (
        hub_name_prefix + ":" + pairs_df["hub"].astype(str) + "_" + pairs_df["item"]
    ).tolist()
    hub_bus_carriers = (carrier_prefix + pairs_df["item"]).tolist()

    if hub_bus_names:
        n.buses.add(hub_bus_names, carrier=hub_bus_carriers)

    gdf_ee = regions_gdf.to_crs(6933)
    gdf_countries = gdf_ee[gdf_ee["country"].isin(countries)].dissolve(
        by="country", as_index=True
    )
    ccent = gdf_countries.geometry.centroid
    country_coords = np.column_stack([ccent.x.values, ccent.y.values])
    dch = ((country_coords[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2) ** 0.5
    nearest_hub_idx = dch.argmin(axis=1)
    nearest_hub_dist_km = dch[np.arange(len(country_coords)), nearest_hub_idx] / 1000.0

    country_index = gdf_countries.index.to_list()
    country_to_hub = pd.Series(nearest_hub_idx.astype(int), index=country_index)
    country_to_dist_km = pd.Series(nearest_hub_dist_km, index=country_index)

    valid_countries = [c for c in countries if c in country_to_hub.index]

    link_names: list[str] = []
    link_bus0: list[str] = []
    link_bus1: list[str] = []
    link_costs: list[float] = []
    link_items: list[str] = []

    if valid_countries:
        pairs = pd.DataFrame(
            list(itertools.product(tradable_items, valid_countries)),
            columns=["item", "country"],
        )
        pairs["hub_idx"] = pairs["country"].map(country_to_hub)
        pairs["dist_km"] = pairs["country"].map(country_to_dist_km)
        pairs["item_cost"] = pairs["item"].map(item_costs)
        pairs["cost"] = pairs["dist_km"] * pairs["item_cost"]

        hub_idx_str = pairs["hub_idx"].astype(str)

        # Build to-hub direction
        pairs["name_to"] = (
            link_name_prefix
            + ":"
            + pairs["item"]
            + ":"
            + pairs["country"]
            + "_to_hub"
            + hub_idx_str
        )
        pairs["bus0_to"] = bus_prefix + ":" + pairs["item"] + ":" + pairs["country"]
        pairs["bus1_to"] = hub_name_prefix + ":" + hub_idx_str + "_" + pairs["item"]

        # Build from-hub direction
        pairs["name_from"] = (
            link_name_prefix
            + ":"
            + pairs["item"]
            + ":hub"
            + hub_idx_str
            + "_to_"
            + pairs["country"]
        )
        pairs["bus0_from"] = pairs["bus1_to"]  # hub bus
        pairs["bus1_from"] = pairs["bus0_to"]  # country bus

        # Interleave to-hub and from-hub rows to match original ordering
        link_names = list(
            itertools.chain.from_iterable(zip(pairs["name_to"], pairs["name_from"]))
        )
        link_bus0 = list(
            itertools.chain.from_iterable(zip(pairs["bus0_to"], pairs["bus0_from"]))
        )
        link_bus1 = list(
            itertools.chain.from_iterable(zip(pairs["bus1_to"], pairs["bus1_from"]))
        )
        link_costs = list(
            itertools.chain.from_iterable(zip(pairs["cost"], pairs["cost"]))
        )
        link_items = list(
            itertools.chain.from_iterable(zip(pairs["item"], pairs["item"]))
        )

    if link_names:
        # Add trade carrier if not present
        if link_carrier not in n.carriers.static.index:
            n.carriers.add(link_carrier, unit="Mt")
        n.links.add(
            link_names,
            bus0=link_bus0,
            bus1=link_bus1,
            marginal_cost=link_costs,
            p_nom_extendable=True,
            carrier=link_carrier,
            **{item_column: link_items},
        )

    if n_hubs >= 2:
        hub_distances = (
            np.sqrt(((centers[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2))
            / 1000.0
        )
        ii, jj = np.where(~np.eye(n_hubs, dtype=bool))

        hub_link_names: list[str] = []
        hub_link_bus0: list[str] = []
        hub_link_bus1: list[str] = []
        hub_link_costs: list[float] = []
        hub_link_items: list[str] = []

        if len(ii) > 0:
            dists_km = hub_distances[ii, jj]
            hub_pairs_list = list(zip(ii, jj, dists_km))
            pairs = pd.DataFrame(
                list(itertools.product(tradable_items, hub_pairs_list)),
                columns=["item", "hub_pair"],
            )
            pairs[["i", "j", "dist"]] = pd.DataFrame(
                pairs["hub_pair"].tolist(), index=pairs.index
            )
            pairs = pairs.drop(columns="hub_pair")
            pairs["item_cost"] = pairs["item"].map(item_costs)

            i_str = pairs["i"].astype(int).astype(str)
            j_str = pairs["j"].astype(int).astype(str)

            hub_link_names = (
                link_name_prefix
                + ":"
                + pairs["item"]
                + ":hub"
                + i_str
                + "_to_hub"
                + j_str
            ).tolist()
            hub_link_bus0 = (
                hub_name_prefix + ":" + i_str + "_" + pairs["item"]
            ).tolist()
            hub_link_bus1 = (
                hub_name_prefix + ":" + j_str + "_" + pairs["item"]
            ).tolist()
            hub_link_costs = (pairs["dist"] * pairs["item_cost"]).tolist()
            hub_link_items = pairs["item"].tolist()

        if hub_link_names:
            n.links.add(
                hub_link_names,
                bus0=hub_link_bus0,
                bus1=hub_link_bus1,
                marginal_cost=hub_link_costs,
                p_nom_extendable=True,
                carrier=link_carrier,
                **{item_column: hub_link_items},
            )


def add_crop_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    crop_list: list,
    *,
    hub_centers: np.ndarray,
) -> None:
    """Add crop trading hubs and connect crop buses via hubs."""

    _add_trade_hubs_and_links(
        n,
        trade_config,
        regions_gdf,
        countries,
        crop_list,
        hub_centers=hub_centers,
        marginal_cost_key="crop_trade_marginal_cost_per_km",
        cost_categories_key="crop_trade_cost_categories",
        default_cost_key="crop_default_trade_cost_per_km",
        category_item_key="crops",
        non_tradable_key="non_tradable_crops",
        bus_prefix="crop",
        carrier_prefix="crop_",
        hub_name_prefix="hub:crop",
        link_name_prefix="trade",
        log_label="crop",
        link_carrier="trade_crop",
        item_column="crop",
    )


def add_food_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    food_list: list,
    *,
    hub_centers: np.ndarray,
) -> None:
    """Add trading hubs and links for foods (including byproducts)."""

    _add_trade_hubs_and_links(
        n,
        trade_config,
        regions_gdf,
        countries,
        food_list,
        hub_centers=hub_centers,
        marginal_cost_key="food_trade_marginal_cost_per_km",
        cost_categories_key="food_trade_cost_categories",
        default_cost_key="food_default_trade_cost_per_km",
        category_item_key="foods",
        non_tradable_key="non_tradable_foods",
        bus_prefix="food",
        carrier_prefix="food_",
        hub_name_prefix="hub:food",
        link_name_prefix="trade_food",
        log_label="food",
        link_carrier="trade_food",
        item_column="food",
    )


def add_feed_trade_hubs_and_links(
    n: pypsa.Network,
    trade_config: dict,
    regions_gdf: gpd.GeoDataFrame,
    countries: list,
    feed_categories: list,
    *,
    hub_centers: np.ndarray,
) -> None:
    """Add trading hubs and links for animal feed categories.

    Creates a hierarchical trade network for feed categories using pre-computed
    hub positions. Feed buses follow the naming convention
    feed_{category}_{country}.

    Grassland feed is excluded from trading (fresh, location-specific).
    Other feeds are tradable with costs reflecting bulkiness:
    - Grain/protein feeds: Low cost (concentrated, easy handling)
    - Forage feeds: Medium cost (moderately bulky)
    - Roughage/low-quality: High cost (very bulky, low value)

    Args:
        n: PyPSA network to modify
        trade_config: Trade configuration dictionary
        regions_gdf: GeoDataFrame with regional geometries for hub placement
        countries: List of country codes to connect
        feed_categories: List of feed category names (from infrastructure)
        hub_centers: Pre-computed hub center coordinates in EPSG:6933.
    """
    _add_trade_hubs_and_links(
        n,
        trade_config,
        regions_gdf,
        countries,
        feed_categories,
        hub_centers=hub_centers,
        marginal_cost_key="feed_default_trade_cost_per_km",
        cost_categories_key="feed_trade_cost_categories",
        default_cost_key="feed_default_trade_cost_per_km",
        category_item_key="feeds",
        non_tradable_key="non_tradable_feeds",
        bus_prefix="feed",
        carrier_prefix="feed_",
        hub_name_prefix="hub:feed",
        link_name_prefix="trade_feed",
        log_label="feed",
        link_carrier="trade_feed",
        item_column="feed_category",
    )
