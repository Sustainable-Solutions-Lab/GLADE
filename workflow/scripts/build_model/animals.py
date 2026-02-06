# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Animal production components for the food systems model.

This module handles the conversion of feed into animal products,
including emissions tracking for CH4 and N2O, and manure nitrogen
outputs for fertilizer.
"""

import logging

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
    allow_negative_grassland_slack: bool = False,
) -> None:
    """Add slack generators and stores to feed buses for validation mode feasibility.

    When both grassland production and animal production are fixed at baseline/FAO levels,
    the system may have either insufficient feed (needs positive slack) or excess feed
    (needs negative slack). Following the pattern from food group slack:
    - Generators provide positive slack (add feed when production is insufficient)
    - Stores absorb negative slack (consume feed when production exceeds requirements)

    Note: Positive slack is only added for ruminant grassland feeds to prevent the model
    from filling feed gaps with high-protein feeds (which would overestimate N2O emissions).
    Negative slack is only added for non-grassland feeds by default. When
    ``allow_negative_grassland_slack`` is True (validation mode with fixed
    grassland dispatch), grassland buses also get negative slack to absorb
    unavoidable overproduction.

    Parameters
    ----------
    n : pypsa.Network
        The network to add slack components to
    marginal_cost : float
        Cost per Mt of slack (billion USD/Mt)
    allow_negative_grassland_slack : bool, optional
        Whether negative slack should also be added to grassland feed buses.
    """
    # Find all feed buses (named feed:{category}:{country})
    feed_mask = n.buses.static.index.str.startswith("feed:")
    feed_buses = n.buses.static.index[feed_mask]

    if feed_buses.empty:
        logger.info("No feed buses found; skipping feed slack")
        return

    # Only add positive slack for ruminant grassland (to avoid inflating N2O via protein feed)
    grassland_mask = feed_buses.str.contains("ruminant_grassland")
    grassland_buses = feed_buses[grassland_mask].tolist()

    # Add carriers for slack
    n.carriers.add(
        ["slack_positive_feed", "slack_negative_feed"],
        unit="Mt",
    )

    # Add positive slack generators only for grassland (provide feed when insufficient)
    if grassland_buses:
        gen_pos_names = [
            f"slack:feed_positive:{bus.split(':')[-1]}" for bus in grassland_buses
        ]
        n.generators.add(
            gen_pos_names,
            bus=grassland_buses,
            carrier="slack_positive_feed",
            p_nom_extendable=True,
            marginal_cost=marginal_cost,
        )
        logger.info(
            "Added %d positive feed slack generators (grassland only)",
            len(gen_pos_names),
        )

    # Add negative slack stores to absorb excess feed.
    if allow_negative_grassland_slack:
        negative_slack_buses = feed_buses.tolist()
    else:
        negative_slack_buses = feed_buses[~grassland_mask].tolist()

    if negative_slack_buses:
        # Build unique names: extract category and country from index
        # Convention exception: using str.extract on bus names for category/country
        _ng = pd.Index(negative_slack_buses).str.extract(
            r"^feed:(?P<category>[^:]+):(?P<country>.+)$"
        )
        gen_neg_names = (
            "slack:feed_negative_" + _ng["category"] + ":" + _ng["country"]
        ).tolist()
        n.generators.add(
            gen_neg_names,
            bus=negative_slack_buses,
            carrier="slack_negative_feed",
            p_nom_extendable=True,
            p_min_pu=-1.0,
            p_max_pu=0.0,
            marginal_cost=-marginal_cost,
        )

        logger.info(
            "Added %d negative feed slack stores for validation feasibility",
            len(gen_neg_names),
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

    # Compute CH4 and N2O/manure-N per row (helpers are complex; keep per-row calls
    # but benefit from pre-indexed DataFrames for the inner lookups)
    ch4_results = []
    n2o_results = []
    for _, row in df.iterrows():
        # Calculate total CH4 (enteric + manure) per tonne feed intake
        # This is relative to bus0 (feed), so it can be used directly as efficiency2
        ch4_per_t_feed, manure_ch4_per_t_feed = _calculate_ch4_per_feed_intake(
            product=row["product"],
            feed_category=row["feed_category"],
            country=row["country"],
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )
        ch4_results.append((ch4_per_t_feed, manure_ch4_per_t_feed))

        # Calculate manure N fertilizer and N2O outputs per tonne feed intake
        n_fert_per_t_feed, n2o_per_t_feed, pasture_n2o_share = (
            _calculate_manure_n_outputs(
                product=row["product"],
                feed_category=row["feed_category"],
                efficiency=row["efficiency"],
                ruminant_categories=ruminant_feed_categories,
                monogastric_categories=monogastric_feed_categories,
                nutrition=nutrition,
                manure_emissions=manure_emissions,
                manure_n_to_fertilizer=manure_n_to_fert,
                indirect_ef4=indirect_ef4,
                indirect_ef5=indirect_ef5,
                frac_gasm=frac_gasm,
                frac_leach=frac_leach,
            )
        )
        n2o_results.append((n_fert_per_t_feed, n2o_per_t_feed, pasture_n2o_share))

    # Unpack results into columns
    df["ch4_per_t_feed"] = [r[0] for r in ch4_results]
    df["manure_ch4_per_t_feed"] = [r[1] for r in ch4_results]
    df["manure_ch4_share"] = df.apply(
        lambda r: r["manure_ch4_per_t_feed"] / r["ch4_per_t_feed"]
        if r["ch4_per_t_feed"] > 0
        else 0.0,
        axis=1,
    )
    df["n_fert_per_t_feed"] = [r[0] for r in n2o_results]
    df["n2o_per_t_feed"] = [r[1] for r in n2o_results]
    df["pasture_n2o_share"] = [r[2] for r in n2o_results]

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

    # Calculate FLW-adjusted efficiency
    df["group"] = df["product"].map(food_to_group)
    lw_keys = list(zip(df["country"], df["group"]))
    df["loss_frac"] = [loss_waste_pairs[k][0] for k in lw_keys]
    df["waste_frac"] = [loss_waste_pairs[k][1] for k in lw_keys]
    df["flw_multiplier"] = (1.0 - df["loss_frac"]) * (1.0 - df["waste_frac"])
    df["adjusted_efficiency"] = df["efficiency"] * df["flw_multiplier"]

    # Build all link data with vectorized string ops
    all_names = (
        "animal:" + df["product"] + "_" + df["feed_category"] + ":" + df["country"]
    ).tolist()
    all_bus0 = df["feed_bus"].tolist()
    all_bus1 = df["food_bus"].tolist()
    all_bus3 = ("fertilizer:" + df["country"]).tolist()
    # Convert per-tonne emissions to per-Mt flows (CH4, N2O in t; feed in Mt)
    # Manure N needs no conversion: t N / t feed = Mt N / Mt feed (ratio is scale-invariant)
    all_ch4 = (df["ch4_per_t_feed"] * constants.MEGATONNE_TO_TONNE).tolist()
    all_n_fert = df["n_fert_per_t_feed"].tolist()
    all_n2o = (df["n2o_per_t_feed"] * constants.MEGATONNE_TO_TONNE).tolist()

    # All animal production links now have multiple outputs:
    # bus1: animal product, bus2: CH4, bus3: manure N fertilizer (country-specific), bus4: N2O
    n.links.add(
        all_names,
        bus0=all_bus0,
        bus1=all_bus1,
        carrier="animal_production",
        efficiency=df["adjusted_efficiency"].tolist(),
        marginal_cost=df["marginal_cost"].tolist(),
        p_nom_extendable=True,
        bus2="emission:ch4",
        efficiency2=all_ch4,
        bus3=all_bus3,
        efficiency3=all_n_fert,
        bus4="emission:n2o",
        efficiency4=all_n2o,
        country=df["country"].tolist(),
        product=df["product"].tolist(),
        feed_category=df["feed_category"].tolist(),
        manure_ch4_share=df["manure_ch4_share"].tolist(),
        pasture_n2o_share=df["pasture_n2o_share"].tolist(),
    )

    logger.info(
        "Added %d feed→animal product links with outputs: product, CH4 (enteric+manure), manure N fertilizer, N2O",
        len(all_names),
    )
    if skipped_count > 0:
        logger.info("Skipped %d links due to missing buses", skipped_count)
