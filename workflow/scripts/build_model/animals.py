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
    grassland_buses = feed_buses[grassland_mask]

    # Add carriers for slack
    n.carriers.add(
        ["slack_positive_feed", "slack_negative_feed"],
        unit="Mt",
    )

    # Add positive slack generators only for grassland (provide feed when insufficient)
    if not grassland_buses.empty:
        gen_pos_names = pd.Index(
            "slack:feed_positive:"
            + pd.Series(grassland_buses, index=grassland_buses).str.split(":").str[-1],
            dtype="object",
        )
        pos_df = pd.DataFrame(index=gen_pos_names)
        pos_df["bus"] = grassland_buses.to_numpy()
        n.generators.add(
            pos_df.index,
            bus=pos_df["bus"],
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
        negative_slack_buses = feed_buses
    else:
        negative_slack_buses = feed_buses[~grassland_mask]

    if not negative_slack_buses.empty:
        # Build unique names: extract category and country from index
        # Convention exception: using str.extract on bus names for category/country
        neg_df = pd.DataFrame(index=negative_slack_buses)
        neg_df["bus"] = negative_slack_buses.to_numpy()
        _ng = neg_df["bus"].str.extract(r"^feed:(?P<category>[^:]+):(?P<country>.+)$")
        neg_df.index = pd.Index(
            "slack:feed_negative_" + _ng["category"] + ":" + _ng["country"],
            dtype="object",
        )
        n.generators.add(
            neg_df.index,
            bus=neg_df["bus"],
            carrier="slack_negative_feed",
            p_nom_extendable=True,
            p_min_pu=-1.0,
            p_max_pu=0.0,
            marginal_cost=-marginal_cost,
        )

        logger.info(
            "Added %d negative feed slack stores for validation feasibility",
            len(neg_df),
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

    logger.info(
        "Added %d feed→animal product links with outputs: product, CH4 (enteric+manure), manure N fertilizer, N2O",
        len(link_df),
    )
    if skipped_count > 0:
        logger.info("Skipped %d links due to missing buses", skipped_count)
