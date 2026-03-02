# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Grassland feed production components for the food systems model.

This module handles the creation of links that produce ruminant feed from
the pasture pool.
"""

import logging

import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.constants import (
    HA_PER_MHA,
    MEGATONNE_TO_TONNE,
    USD_TO_BNUSD,
)

logger = logging.getLogger(__name__)


def calculate_grazing_cost_per_tonne_dm(
    animal_costs_df: pd.DataFrame,
    feed_to_products_df: pd.DataFrame,
    base_year: int,
) -> float:
    """
    Calculate global average grazing cost per tonne of dry matter.

    Logic:
    1. Get grazing cost per tonne of animal product (e.g. beef, milk) from animal_costs_df.
    2. Get feed efficiency (tonne product / tonne feed DM) from feed_to_products_df.
    3. Calculate implied feed cost: Cost_Feed = Cost_Product * Efficiency
    4. Average across all relevant entries.

    Parameters
    ----------
    animal_costs_df : pd.DataFrame
        Animal cost data with columns: product, grazing_cost_per_mt_usd_{base_year}
    feed_to_products_df : pd.DataFrame
        Feed efficiency data with columns: product, feed_category, region, efficiency
    base_year : int
        Base year for cost data

    Returns
    -------
    float
        Average grazing cost per tonne of dry matter in USD/t
    """
    grazing_col = f"grazing_cost_per_mt_usd_{base_year}"

    # Filter for products with grazing costs
    grazing_costs = animal_costs_df[animal_costs_df[grazing_col] > 0][
        ["product", grazing_col]
    ].copy()

    # Filter feed_to_products for grass-based feed categories
    # The costs are allocated from "Grazed feed" item in USDA/FADN,
    # which corresponds to grassland production routed to ruminant_forage.
    grass_feeds = feed_to_products_df[
        feed_to_products_df["feed_category"] == "ruminant_forage"
    ].copy()

    # Merge costs and efficiencies
    merged = pd.merge(grazing_costs, grass_feeds, on="product", how="inner")

    # Cost_Feed ($/tDM) = Cost_Product ($/tProduct) * Efficiency (tProduct/tFeedDM)
    merged["implied_feed_cost"] = merged[grazing_col] * merged["efficiency"]

    # Calculate average
    avg_cost = merged["implied_feed_cost"].mean()

    logger.info(
        f"Calculated average grazing cost: ${avg_cost:.2f}/tDM "
        f"(from {len(merged)} product-feed combinations)"
    )

    return float(avg_cost)


def add_grassland_feed_links(
    n: pypsa.Network,
    grassland: pd.DataFrame,
    land_rainfed: pd.DataFrame,
    region_to_country: pd.Series,
    allowed_countries: set,
    marginal_cost: float = 0.0,
    current_grassland_area: pd.DataFrame | None = None,
    marginal_grassland_area: pd.Series | None = None,
    use_actual_production: bool = False,
    fix_current_production: bool = False,
    *,
    min_yield_t_per_ha: float,
) -> None:
    """Add links supplying ruminant feed directly from rainfed land.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    grassland : pd.DataFrame
        Grassland yield data with pre-corrected effective feed yields.
    land_rainfed : pd.DataFrame
        Rainfed land area availability.
    region_to_country : pd.Series
        Mapping from region to country code.
    allowed_countries : set
        Set of allowed country codes.
    marginal_cost : float, optional
        Marginal cost of grassland feed in USD per tonne DM, by default 0.0.
        Converted internally to bnUSD per Mha based on yield.
    current_grassland_area : pd.DataFrame | None, optional
        Observed grassland area for validation, by default None.
    marginal_grassland_area : pd.Series | None, optional
        Grazing-only current grassland area (not suitable for crops) indexed by
        (region, resource_class), by default None.
    use_actual_production : bool, optional
        Whether to cap production at observed values, by default False.
    fix_current_production : bool, optional
        If True and ``use_actual_production`` is enabled, force grassland links
        to dispatch at their observed area (instead of only capping them).
    """
    # Add grassland_production carrier
    if "grassland_production" not in n.carriers.static.index:
        n.carriers.add("grassland_production", unit="Mt")

    grass_df = grassland.copy()
    # Filter invalid yields and low yields for numerical stability in one pass
    grass_df = grass_df[
        np.isfinite(grass_df["yield"]) & (grass_df["yield"] >= min_yield_t_per_ha)
    ]

    if grass_df.empty:
        logger.warning("No valid grassland yield data available; skipping")
        return

    grass_df = grass_df.reset_index()
    grass_df["resource_class"] = grass_df["resource_class"].astype(int)
    grass_df = grass_df.set_index(["region", "resource_class"])

    base_df = grass_df.join(
        land_rainfed[["area_ha"]].rename(columns={"area_ha": "land_area"}),
        how="inner",
    )
    if use_actual_production:
        observed_area = (
            current_grassland_area.set_index(["region", "resource_class"])["area_ha"]
            .astype(float)
            .rename("observed_area")
        )
        base_df = base_df.join(observed_area, how="left")

    candidate_area = base_df["suitable_area"].fillna(base_df["land_area"])
    # candidate_area and base_df["land_area"] share the same index
    land_cap_series = np.minimum(candidate_area, base_df["land_area"])
    idx = base_df.index

    # Compute total available area per region/class: cropland-eligible + marginal
    if marginal_grassland_area is not None and not marginal_grassland_area.empty:
        marginal_cap_series = marginal_grassland_area.reindex(idx, fill_value=0.0)
    else:
        marginal_cap_series = pd.Series(0.0, index=idx, dtype=float)

    if use_actual_production:
        observed_series = (
            pd.to_numeric(base_df.get("observed_area"), errors="coerce")
            .fillna(0.0)
            .astype(float)
        )
        base_df = base_df.drop(columns=["observed_area"])
        observed_aligned = observed_series.reindex(idx)
        # Total available = observed, capped by combined land potential
        total_cap = land_cap_series + marginal_cap_series
        base_df["available_area"] = np.minimum(observed_aligned, total_cap)
    else:
        # Total available = cropland-eligible cap + marginal cap
        base_df["available_area"] = (land_cap_series + marginal_cap_series).reindex(
            base_df.index
        )

    production_df = base_df[base_df["available_area"] > 0].copy()

    if production_df is None or production_df.empty:
        logger.info("Grassland entries have zero available area; skipping")
        return

    work = production_df.reset_index()
    work["country"] = work["region"].map(region_to_country)
    work = work[work["country"].isin(allowed_countries)]
    work = work.dropna(subset=["country"])
    if work.empty:
        logger.info("Grassland entries have zero available area; skipping")
        return

    # All grassland links consume from the pasture pool, which aggregates
    # land from existing cropland, new land conversion, and marginal grazing land.
    suffix = work["region"] + "_c" + work["resource_class"].astype(str)
    work["name"] = "produce:grassland:" + suffix
    work["bus0"] = "land:pasture:" + suffix
    work["bus1"] = "feed:ruminant_forage:" + work["country"]

    available_mha = work["available_area"].to_numpy() / HA_PER_MHA

    # Efficiency (Mt/Mha): yields are per managed hectare, so multiply by
    # grazing intensity to get effective yield per physical hectare.
    # Yields are in t/ha, which equals Mt/Mha numerically.
    grazing_intensity = work["grazing_intensity"].to_numpy()
    yield_per_managed_ha = work["yield"].to_numpy()
    efficiencies = grazing_intensity * yield_per_managed_ha

    # Calculate marginal cost per Mha (bnUSD/Mha).
    # In PyPSA, marginal_cost is per unit of bus0 (land in Mha).
    # To get cost per unit output (feed in Mt), we need:
    #   cost_per_output = marginal_cost_pypsa / efficiency
    # We want: cost_per_output = marginal_cost (USD/t) * conversion to bnUSD/Mt
    # Therefore: marginal_cost_pypsa = marginal_cost * conversion * efficiency
    cost_per_mha_bnusd = (
        marginal_cost * efficiencies * MEGATONNE_TO_TONNE * USD_TO_BNUSD
    )

    # Compute baseline production from observed grazing area * yield.
    # This is used by production stability constraints regardless of
    # whether use_actual_production caps dispatch.
    if current_grassland_area is not None:
        observed_area = current_grassland_area.set_index(["region", "resource_class"])[
            "area_ha"
        ].astype(float)
        observed_area_mha = (
            observed_area.reindex(
                pd.MultiIndex.from_arrays(
                    [work["region"], work["resource_class"]],
                    names=["region", "resource_class"],
                ),
            )
            .fillna(0.0)
            .to_numpy()
            / HA_PER_MHA
        )
    else:
        observed_area_mha = np.zeros(len(work))
    baseline_area_mha = observed_area_mha

    # Index by name for proper alignment with PyPSA component names
    work_indexed = work.set_index("name")
    params = {
        "carrier": "grassland_production",
        "bus0": work_indexed["bus0"],
        "bus1": work_indexed["bus1"],
        "efficiency": efficiencies,
        "p_nom_max": available_mha,
        "p_nom_extendable": not use_actual_production,
        "marginal_cost": cost_per_mha_bnusd,
        "region": work_indexed["region"],
        "resource_class": work_indexed["resource_class"],
        "country": work_indexed["country"],
        "crop": "grassland",
        "water_supply": "rainfed",
        "grazing_intensity": grazing_intensity,
        "yield_per_managed_ha": yield_per_managed_ha,
        "baseline_area_mha": baseline_area_mha,
    }
    if use_actual_production:
        params["p_nom"] = available_mha
        if fix_current_production:
            # Fix dispatch to observed current area in validation mode.
            params["p_min_pu"] = 1.0
            params["p_max_pu"] = 1.0

    n.links.add(work_indexed.index, **params)
