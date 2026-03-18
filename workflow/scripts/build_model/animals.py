# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Animal production components for the food systems model.

This module handles the conversion of feed into animal products,
including emissions tracking for CH4 and N2O, and manure nitrogen
outputs for fertilizer.
"""

import logging

import numpy as np
import pandas as pd
import pypsa

from .. import constants
from .utils import (
    _build_loss_waste_lookup,
    _calculate_ch4_per_feed_intake,
    _calculate_manure_n_outputs,
)

logger = logging.getLogger(__name__)


def add_feed_slack_generators(
    n: pypsa.Network,
    marginal_cost: float,
) -> None:
    """Add slack generators to feed buses for validation mode feasibility.

    When crop production, grassland production and animal feed use are all fixed
    at baseline levels, small mismatches between supply-side and demand-side
    data sources make exact bus balance impossible. Bidirectional slack on every
    feed bus absorbs these discrepancies at a high marginal cost (so the solver
    only uses slack where truly necessary).

    Parameters
    ----------
    n : pypsa.Network
        The network to add slack components to
    marginal_cost : float
        Cost per Mt of slack (billion USD/Mt)
    """
    # Find all feed buses (named feed:{category}:{country})
    feed_mask = n.buses.static.index.str.startswith("feed:")
    feed_buses = n.buses.static.index[feed_mask]

    if feed_buses.empty:
        logger.info("No feed buses found; skipping feed slack")
        return

    # Add carriers for slack
    n.carriers.add(
        ["slack_positive_feed", "slack_negative_feed"],
        unit="Mt",
    )

    # Extract category and country from bus names for naming
    bus_series = pd.Series(feed_buses, index=feed_buses)
    parts = bus_series.str.extract(r"^feed:(?P<category>[^:]+):(?P<country>.+)$")

    # Positive slack generators (provide feed when supply is insufficient)
    pos_names = pd.Index(
        "slack:feed_positive_" + parts["category"] + ":" + parts["country"],
        dtype="object",
    )
    n.generators.add(
        pos_names,
        bus=feed_buses,
        carrier="slack_positive_feed",
        p_nom_extendable=True,
        marginal_cost=marginal_cost,
    )

    # Negative slack generators (absorb feed when supply exceeds demand)
    neg_names = pd.Index(
        "slack:feed_negative_" + parts["category"] + ":" + parts["country"],
        dtype="object",
    )
    n.generators.add(
        neg_names,
        bus=feed_buses,
        carrier="slack_negative_feed",
        p_nom_extendable=True,
        p_min_pu=-1.0,
        p_max_pu=0.0,
        marginal_cost=-marginal_cost,
    )

    logger.info(
        "Added %d positive + %d negative feed slack generators",
        len(pos_names),
        len(neg_names),
    )


def add_exogenous_feed_generators(
    n: pypsa.Network,
    feed_baseline: pd.DataFrame,
    enforce_baseline_feed: bool,
) -> None:
    """Add generators for exogenous feed supply (leaves/browse, swill).

    Some GLEAM feed types cannot be produced endogenously by the model (tree
    leaves and forest browse for ruminants, food-waste swill for monogastrics).
    This function reads the ``exogenous_mt_dm`` column from the feed baseline
    and creates fixed-capacity generators on the relevant feed buses.

    Parameters
    ----------
    n : pypsa.Network
        The network to add generators to.
    feed_baseline : pd.DataFrame
        GLEAM feed baseline with columns: country, product, feed_category,
        feed_use_mt_dm, exogenous_mt_dm.
    enforce_baseline_feed : bool
        If True (validation mode), generators are fixed at the exogenous
        amount.  If False (optimisation mode), generators are extendable up
        to the exogenous amount at zero cost.
    """
    if "exogenous_mt_dm" not in feed_baseline.columns:
        logger.info("No exogenous_mt_dm column in feed baseline; skipping")
        return

    # Aggregate to (country, feed_category) — product dimension is irrelevant
    # for supply generators on per-country feed buses
    agg = (
        feed_baseline.groupby(["country", "feed_category"])["exogenous_mt_dm"]
        .sum()
        .reset_index()
    )
    agg = agg[agg["exogenous_mt_dm"] > 0].copy()

    if agg.empty:
        logger.info("No exogenous feed amounts; skipping")
        return

    # Filter to entries with existing feed buses
    agg["bus"] = "feed:" + agg["feed_category"] + ":" + agg["country"]
    bus_exists = agg["bus"].isin(n.buses.static.index)
    agg = agg[bus_exists].copy()

    if agg.empty:
        logger.info("No matching feed buses for exogenous feed; skipping")
        return

    # Add carrier
    if "exogenous_feed" not in n.carriers.static.index:
        n.carriers.add("exogenous_feed", unit="Mt")

    names = pd.Index(
        "supply:exogenous_" + agg["feed_category"] + ":" + agg["country"],
        dtype="object",
    )

    if enforce_baseline_feed:
        # Validation mode: forced dispatch at baseline level
        n.generators.add(
            names,
            bus=agg["bus"].values,
            carrier="exogenous_feed",
            p_nom=agg["exogenous_mt_dm"].values,
            p_nom_extendable=False,
            p_min_pu=1.0,
            p_max_pu=1.0,
            country=agg["country"].values,
            feed_category=agg["feed_category"].values,
        )
    else:
        # Optimisation mode: available up to baseline, free
        n.generators.add(
            names,
            bus=agg["bus"].values,
            carrier="exogenous_feed",
            p_nom_extendable=True,
            p_nom_max=agg["exogenous_mt_dm"].values,
            marginal_cost=0.0,
            country=agg["country"].values,
            feed_category=agg["feed_category"].values,
        )

    logger.info(
        "Added %d exogenous feed generators (%.1f Mt DM total)",
        len(names),
        agg["exogenous_mt_dm"].sum(),
    )


def add_feed_to_animal_product_links(
    n: pypsa.Network,
    animal_products: list,
    feed_requirements: pd.DataFrame,
    ruminant_feed_categories: pd.DataFrame,
    monogastric_feed_categories: pd.DataFrame,
    manure_emissions: pd.DataFrame,
    nutrition: pd.DataFrame,
    fertilizer_config: dict,
    emissions_config: dict,
    countries: list,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    animal_costs: pd.Series | None = None,
    feed_baseline: pd.DataFrame | None = None,
    enforce_baseline_feed: bool = False,
    cost_calibration: pd.Series | None = None,
) -> None:
    """Add links that convert feed pools into animal products with emissions and manure N.

    UNITS:

    - Input (bus0): Feed in DRY MATTER (Mt DM)
    - Output (bus1): Animal products in FRESH WEIGHT, RETAIL MEAT (Mt fresh)

      - For meats: retail/edible meat weight (boneless, trimmed)
      - For dairy: whole milk (fresh weight)
      - For eggs: whole eggs (fresh weight)

    - Efficiency: Mt retail product per Mt feed DM

      - Incorporates carcass-to-retail conversion for meat products
      - Generated from Wirsenius (2000) + GLEAM feed energy values
      - Adjusted for food loss and waste fractions

    Outputs per link:

    - bus1: Animal product (fresh weight, retail meat)
    - bus2: CH4 emissions (enteric + manure)
    - bus3: Manure N available as fertilizer
    - bus4: N2O emissions from manure N application
    - manure_ch4_share: Fraction of CH4 from manure management (for plotting)

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to
    animal_products : list
        List of animal product names
    feed_requirements : pd.DataFrame
        Feed requirements with columns: product, feed_category, efficiency
        Efficiency in Mt RETAIL PRODUCT per Mt FEED DM
    ruminant_feed_categories : pd.DataFrame
        Ruminant feed categories with enteric CH4 yields and N content
    monogastric_feed_categories : pd.DataFrame
        Monogastric feed categories with N content
    manure_emissions : pd.DataFrame
        Manure CH4 emission factors by country, product, and feed_category
    nutrition : pd.DataFrame
        Nutrition data (indexed by food, nutrient) with protein content
    fertilizer_config : dict
        Fertilizer configuration with manure_n_to_fertilizer
    countries : list
        List of country codes
    food_to_group : dict[str, str]
        Mapping from food names to food group names for FLW lookup
    loss_waste : pd.DataFrame
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction
    animal_costs : pd.Series | None, optional
        Animal product costs indexed by product (USD per Mt product).
        If provided, converted to cost per Mt feed via efficiency.
        If None, marginal_cost defaults to 0.
    cost_calibration : pd.Series | None, optional
        Additive cost corrections with MultiIndex (product, country) in
        bnUSD/Mt-feed. Applied after base cost computation. If None, no
        calibration is applied.
    """

    # Add animal_production carrier
    if "animal_production" not in n.carriers.static.index:
        n.carriers.add("animal_production", unit="Mt")

    if not animal_products:
        logger.info("No animal products configured; skipping feed→animal links")
        return

    # Build food loss/waste lookup: (country, food_group) -> (loss_fraction, waste_fraction)
    loss_waste_pairs = _build_loss_waste_lookup(loss_waste)

    # Build enteric methane yield lookup from ruminant feed categories
    enteric_my_lookup = (
        ruminant_feed_categories.set_index("category")["MY_g_CH4_per_kg_DMI"]
        .astype(float)
        .to_dict()
    )
    ruminant_n_lookup = (
        ruminant_feed_categories.assign(
            category=ruminant_feed_categories["category"].astype(str),
            N_g_per_kg_DM=pd.to_numeric(
                ruminant_feed_categories["N_g_per_kg_DM"], errors="coerce"
            ),
        )
        .dropna(subset=["N_g_per_kg_DM"])
        .set_index("category")["N_g_per_kg_DM"]
        .to_dict()
    )
    monogastric_n_lookup = (
        monogastric_feed_categories.assign(
            category=monogastric_feed_categories["category"].astype(str),
            N_g_per_kg_DM=pd.to_numeric(
                monogastric_feed_categories["N_g_per_kg_DM"], errors="coerce"
            ),
        )
        .dropna(subset=["N_g_per_kg_DM"])
        .set_index("category")["N_g_per_kg_DM"]
        .to_dict()
    )
    protein_rows = nutrition.reset_index()
    protein_rows = protein_rows[protein_rows["nutrient"] == "protein"]
    protein_rows["value"] = pd.to_numeric(protein_rows["value"], errors="coerce")
    protein_rows = protein_rows.dropna(subset=["value"])
    product_protein_lookup = (
        protein_rows.assign(food=protein_rows["food"].astype(str))
        .set_index("food")["value"]
        .to_dict()
    )

    manure_cols = [
        "country",
        "product",
        "feed_category",
        "manure_ch4_kg_per_kg_DMI",
        "pasture_fraction",
        "pasture_n2o_ef",
        "managed_n2o_ef",
    ]
    manure_rows = manure_emissions.loc[:, manure_cols].copy()
    for col in (
        "manure_ch4_kg_per_kg_DMI",
        "pasture_fraction",
        "pasture_n2o_ef",
        "managed_n2o_ef",
    ):
        manure_rows[col] = pd.to_numeric(manure_rows[col], errors="coerce")

    manure_ch4_lookup: dict[tuple[str, str, str], float] = {}
    manure_n2o_lookup: dict[tuple[str, str], tuple[float, float, float]] = {}
    manure_n2o_by_product_lookup: dict[str, tuple[float, float, float]] = {}
    for row in manure_rows.itertuples(index=False):
        country = str(row.country)
        product = str(row.product)
        feed_category = str(row.feed_category)
        manure_ch4 = row.manure_ch4_kg_per_kg_DMI
        if pd.notna(manure_ch4):
            manure_ch4_lookup.setdefault(
                (country, product, feed_category), float(manure_ch4)
            )

        n2o_values = (row.pasture_fraction, row.pasture_n2o_ef, row.managed_n2o_ef)
        if all(pd.notna(v) for v in n2o_values):
            factors = (float(n2o_values[0]), float(n2o_values[1]), float(n2o_values[2]))
            manure_n2o_lookup.setdefault((product, feed_category), factors)
            manure_n2o_by_product_lookup.setdefault(product, factors)

    df = feed_requirements.copy()
    df = df[df["product"].isin(animal_products)]

    if df.empty:
        return

    df["efficiency"] = df["efficiency"].astype(float)

    # Get config parameters
    manure_n_to_fert = fertilizer_config["manure_n_to_fertilizer"]
    indirect_ef4 = emissions_config["fertilizer"]["indirect_ef4"]
    indirect_ef5 = emissions_config["fertilizer"]["indirect_ef5"]
    frac_gasm = emissions_config["fertilizer"]["frac_gasm"]
    frac_leach = emissions_config["fertilizer"]["frac_leach"]

    # Pre-filter to rows where country is in the configured list
    df = df[df["country"].isin(countries)].copy()

    # Pre-build bus names as columns and filter by bus existence in one operation
    df["feed_bus"] = "feed:" + df["feed_category"] + ":" + df["country"]
    df["food_bus"] = "food:" + df["product"] + ":" + df["country"]
    bus_index = n.buses.static.index
    bus_exists = df["feed_bus"].isin(bus_index) & df["food_bus"].isin(bus_index)
    skipped_count = int((~bus_exists).sum())
    df = df[bus_exists].copy()

    if df.empty:
        if skipped_count > 0:
            logger.info("Skipped %d links due to missing buses", skipped_count)
        return

    warned_missing_protein: set[str] = set()
    ch4_per_t_feed_values: list[float] = []
    manure_ch4_per_t_feed_values: list[float] = []
    n_fert_per_t_feed_values: list[float] = []
    n2o_per_t_feed_values: list[float] = []
    pasture_n2o_share_values: list[float] = []
    for product, feed_category, country, efficiency in df[
        ["product", "feed_category", "country", "efficiency"]
    ].itertuples(index=False, name=None):
        # Calculate total CH4 (enteric + manure) per tonne feed intake
        # This is relative to bus0 (feed), so it can be used directly as efficiency2
        ch4_per_t_feed, manure_ch4_per_t_feed = _calculate_ch4_per_feed_intake(
            product=product,
            feed_category=feed_category,
            country=country,
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
        )
        ch4_per_t_feed_values.append(ch4_per_t_feed)
        manure_ch4_per_t_feed_values.append(manure_ch4_per_t_feed)

        # Calculate manure N fertilizer and N2O outputs per tonne feed intake
        n_fert_per_t_feed, n2o_per_t_feed, pasture_n2o_share = (
            _calculate_manure_n_outputs(
                product=product,
                feed_category=feed_category,
                efficiency=efficiency,
                ruminant_n_lookup=ruminant_n_lookup,
                monogastric_n_lookup=monogastric_n_lookup,
                product_protein_lookup=product_protein_lookup,
                manure_n2o_lookup=manure_n2o_lookup,
                manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
                manure_n_to_fertilizer=manure_n_to_fert,
                indirect_ef4=indirect_ef4,
                indirect_ef5=indirect_ef5,
                frac_gasm=frac_gasm,
                frac_leach=frac_leach,
                warned_missing_protein=warned_missing_protein,
            )
        )
        n_fert_per_t_feed_values.append(n_fert_per_t_feed)
        n2o_per_t_feed_values.append(n2o_per_t_feed)
        pasture_n2o_share_values.append(pasture_n2o_share)

    # Unpack results into columns
    df["ch4_per_t_feed"] = ch4_per_t_feed_values
    df["manure_ch4_per_t_feed"] = manure_ch4_per_t_feed_values
    ch4_values = df["ch4_per_t_feed"].to_numpy(dtype=float)
    manure_ch4_values = df["manure_ch4_per_t_feed"].to_numpy(dtype=float)
    df["manure_ch4_share"] = np.divide(
        manure_ch4_values,
        ch4_values,
        out=np.zeros_like(ch4_values),
        where=ch4_values > 0,
    )
    df["n_fert_per_t_feed"] = n_fert_per_t_feed_values
    df["n2o_per_t_feed"] = n2o_per_t_feed_values
    df["pasture_n2o_share"] = pasture_n2o_share_values

    # Calculate marginal cost (cost per Mt feed input)
    # animal_costs is in USD per Mt product, efficiency is Mt product per Mt feed
    # So: cost per Mt feed = (cost per Mt product) / (Mt product per Mt feed)
    if animal_costs is not None:
        cost_series = df["product"].map(animal_costs).fillna(0.0)
        df["marginal_cost"] = (
            cost_series.where(df["efficiency"] > 0, 0.0)
            / df["efficiency"].where(df["efficiency"] > 0, 1.0)
            * constants.USD_TO_BNUSD
        )
    else:
        df["marginal_cost"] = 0.0

    # Apply cost calibration corrections (additive, per (product, country), in bnUSD/Mt-feed)
    if cost_calibration is not None:
        cal_idx = pd.MultiIndex.from_arrays(
            [df["product"], df["country"]], names=["product", "country"]
        )
        cal_adj = cost_calibration.reindex(cal_idx, fill_value=0.0).to_numpy()
        cost_before = df["marginal_cost"].to_numpy().copy()
        df["marginal_cost"] = np.maximum(df["marginal_cost"] + cal_adj, 0.0)
        n_clipped = int((cost_before + cal_adj < 0).sum())
        n_adjusted = int((cal_adj != 0).sum())
        logger.info(
            "Applied animal cost calibration: %d/%d links adjusted, %d clipped to 0",
            n_adjusted,
            len(df),
            n_clipped,
        )

    # Calculate FLW-adjusted efficiency
    df["group"] = df["product"].map(food_to_group)
    lw_keys = list(zip(df["country"], df["group"]))
    df["loss_frac"] = [loss_waste_pairs[k][0] for k in lw_keys]
    df["waste_frac"] = [loss_waste_pairs[k][1] for k in lw_keys]
    df["flw_multiplier"] = (1.0 - df["loss_frac"]) * (1.0 - df["waste_frac"])
    df["adjusted_efficiency"] = df["efficiency"] * df["flw_multiplier"]

    # Build all link data with vectorized string ops
    names = pd.Index(
        "animal:" + df["product"] + "_" + df["feed_category"] + ":" + df["country"],
        dtype="object",
    )
    link_df = df.set_index(names, drop=False).copy()
    link_df["bus3"] = "fertilizer:" + link_df["country"]
    # Convert per-tonne emissions to per-Mt flows (CH4, N2O in t; feed in Mt)
    # Manure N needs no conversion: t N / t feed = Mt N / Mt feed (ratio is scale-invariant)
    link_df["efficiency2"] = link_df["ch4_per_t_feed"] * constants.MEGATONNE_TO_TONNE
    link_df["efficiency3"] = link_df["n_fert_per_t_feed"]
    link_df["efficiency4"] = link_df["n2o_per_t_feed"] * constants.MEGATONNE_TO_TONNE

    # All animal production links now have multiple outputs:
    # bus1: animal product, bus2: CH4, bus3: manure N fertilizer (country-specific), bus4: N2O
    n.links.add(
        link_df.index,
        bus0=link_df["feed_bus"],
        bus1=link_df["food_bus"],
        carrier="animal_production",
        efficiency=link_df["adjusted_efficiency"],
        marginal_cost=link_df["marginal_cost"],
        p_nom_extendable=True,
        bus2="emission:ch4",
        efficiency2=link_df["efficiency2"],
        bus3=link_df["bus3"],
        efficiency3=link_df["efficiency3"],
        bus4="emission:n2o",
        efficiency4=link_df["efficiency4"],
        country=link_df["country"],
        product=link_df["product"],
        feed_category=link_df["feed_category"],
        manure_ch4_share=link_df["manure_ch4_share"],
        pasture_n2o_share=link_df["pasture_n2o_share"],
    )

    # Store GLEAM feed baseline on links (for production stability)
    if feed_baseline is not None and not feed_baseline.empty:
        bl = feed_baseline.copy()
        bl["country"] = bl["country"].astype(str)
        bl["product"] = bl["product"].astype(str)
        bl["feed_category"] = bl["feed_category"].astype(str)
        lookup = bl.set_index(["country", "product", "feed_category"])[
            "feed_use_mt_dm"
        ].to_dict()
        baseline_values = pd.Series(
            [
                lookup.get(
                    (
                        n.links.static.at[i, "country"],
                        n.links.static.at[i, "product"],
                        n.links.static.at[i, "feed_category"],
                    ),
                    0.0,
                )
                for i in link_df.index
            ],
            index=link_df.index,
        )
        n.links.static.loc[link_df.index, "baseline_feed_use_mt_dm"] = (
            baseline_values.values
        )
        n_with_baseline = int((baseline_values > 0).sum())
        logger.info(
            "Stored GLEAM feed baselines on %d/%d animal links",
            n_with_baseline,
            len(link_df),
        )

        if enforce_baseline_feed:
            n.links.static.loc[link_df.index, "p_nom"] = baseline_values.values
            n.links.static.loc[link_df.index, "p_nom_extendable"] = False
            n.links.static.loc[link_df.index, "p_min_pu"] = 1.0
            n.links.static.loc[link_df.index, "p_max_pu"] = 1.0
            logger.info(
                "Fixed %d animal links at GLEAM feed baseline values",
                len(link_df),
            )

    logger.info(
        "Added %d feed→animal product links with outputs: product, CH4 (enteric+manure), manure N fertilizer, N2O",
        len(link_df),
    )
    if skipped_count > 0:
        logger.info("Skipped %d links due to missing buses", skipped_count)
