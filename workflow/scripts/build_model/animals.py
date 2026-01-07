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

from . import constants
from .utils import _calculate_ch4_per_feed_intake, _calculate_manure_n_outputs

logger = logging.getLogger(__name__)


def add_feed_slack_generators(
    n: pypsa.Network,
    marginal_cost: float,
) -> None:
    """Add slack generators and stores to feed buses for validation mode feasibility.

    When both grassland production and animal production are fixed at baseline/FAO levels,
    the system may have either insufficient feed (needs positive slack) or excess feed
    (needs negative slack). Following the pattern from food group slack:
    - Generators provide positive slack (add feed when production is insufficient)
    - Stores absorb negative slack (consume feed when production exceeds requirements)

    Note: Positive slack is only added for ruminant grassland feeds to prevent the model
    from filling feed gaps with high-protein feeds (which would overestimate N2O emissions).
    Negative slack is only added for non-grassland feeds, since grassland use is capped
    rather than fixed.

    Parameters
    ----------
    n : pypsa.Network
        The network to add slack components to
    marginal_cost : float
        Cost per Mt of slack (billion USD/Mt)
    """
    # Find all feed buses (named feed_*_<country>)
    feed_buses = [bus for bus in n.buses.static.index if bus.startswith("feed_")]

    if not feed_buses:
        logger.info("No feed buses found; skipping feed slack")
        return

    # Only add positive slack for ruminant grassland and monogastric energy feed.
    # This keeps slack away from high-protein feeds while still allowing a
    # flexible monogastric-compatible pool.
    grassland_buses = [bus for bus in feed_buses if "ruminant_grassland" in bus]
    mono_energy_buses = [bus for bus in feed_buses if "monogastric_energy" in bus]
    positive_slack_buses = grassland_buses + mono_energy_buses

    # Add carriers for slack
    n.carriers.add(
        ["slack_positive_feed", "slack_negative_feed"],
        unit="Mt",
    )

    # Add positive slack generators for the selected feed buses (provide feed when insufficient)
    if positive_slack_buses:
        gen_pos_names = [f"slack_positive_feed_{bus}" for bus in positive_slack_buses]
        n.generators.add(
            gen_pos_names,
            bus=positive_slack_buses,
            carrier="slack_positive_feed",
            p_nom_extendable=True,
            marginal_cost=marginal_cost,
        )
        logger.info(
            "Added %d positive feed slack generators (grassland + monogastric energy)",
            len(gen_pos_names),
        )

    # Add negative slack stores for non-grassland feed buses (absorb excess feed)
    non_grassland_buses = [bus for bus in feed_buses if "ruminant_grassland" not in bus]
    if non_grassland_buses:
        gen_neg_names = [f"slack_negative_feed_{bus}" for bus in non_grassland_buses]
        n.generators.add(
            gen_neg_names,
            bus=non_grassland_buses,
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

    produce_carriers = sorted({f"produce_{product!s}" for product in animal_products})
    if produce_carriers:
        n.carriers.add(produce_carriers, unit="Mt")

    if not animal_products:
        logger.info("No animal products configured; skipping feed→animal links")
        return

    # Build food loss/waste lookup: (country, food_group) -> (loss_fraction, waste_fraction)
    loss_waste_pairs: dict[tuple[str, str], tuple[float, float]] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_waste_pairs[key] = (
            float(row["loss_fraction"]),
            float(row["waste_fraction"]),
        )

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

    # Build all link names and buses (expand each row for all countries)
    all_names = []
    all_bus0 = []
    all_bus1 = []
    all_bus3 = []
    all_carrier = []
    all_efficiency = []
    all_ch4 = []
    all_n_fert = []
    all_n2o = []
    all_marginal_cost = []
    all_country = []
    all_product = []
    all_manure_ch4_share = []
    all_pasture_n2o_share = []

    skipped_count = 0
    for _, row in df.iterrows():
        country = row["country"]
        if country not in countries:
            continue

        # Check if required buses exist
        feed_bus = f"feed_{row['feed_category']}_{country}"
        food_bus = f"food_{row['product']}_{country}"
        if feed_bus not in n.buses.static.index or food_bus not in n.buses.static.index:
            skipped_count += 1
            continue

        # Calculate total CH4 (enteric + manure) per tonne feed intake
        # This is relative to bus0 (feed), so it can be used directly as efficiency2
        ch4_per_t_feed, manure_ch4_per_t_feed = _calculate_ch4_per_feed_intake(
            product=row["product"],
            feed_category=row["feed_category"],
            country=country,
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )
        if ch4_per_t_feed > 0:
            manure_ch4_share = manure_ch4_per_t_feed / ch4_per_t_feed
        else:
            manure_ch4_share = 0.0

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

        # Calculate marginal cost (cost per Mt feed input)
        # animal_costs is in USD per Mt product, efficiency is Mt product per Mt feed
        # So: cost per Mt feed = (cost per Mt product) / (Mt product per Mt feed)
        if animal_costs is not None and row["product"] in animal_costs.index:
            cost_per_mt_product = float(animal_costs.loc[row["product"]])
            if row["efficiency"] > 0:
                # Convert from USD/Mt to billion USD/Mt
                marginal_cost = (
                    cost_per_mt_product / row["efficiency"] * constants.USD_TO_BNUSD
                )
            else:
                marginal_cost = 0.0
        else:
            marginal_cost = 0.0

        # Calculate FLW-adjusted efficiency
        base_efficiency = row["efficiency"]
        group = food_to_group[row["product"]]
        loss_frac, waste_frac = loss_waste_pairs[(country, group)]
        flw_multiplier = (1.0 - loss_frac) * (1.0 - waste_frac)
        adjusted_efficiency = base_efficiency * flw_multiplier

        all_names.append(
            f"produce_{row['product']}_from_{row['feed_category']}_{country}"
        )
        all_bus0.append(feed_bus)
        all_bus1.append(food_bus)
        all_bus3.append(f"fertilizer_{country}")
        all_carrier.append(f"produce_{row['product']}")
        all_efficiency.append(adjusted_efficiency)
        # Convert per-tonne emissions to per-Mt flows (CH4, N2O in t; feed in Mt)
        # Manure N needs no conversion: t N / t feed = Mt N / Mt feed (ratio is scale-invariant)
        all_ch4.append(ch4_per_t_feed * constants.MEGATONNE_TO_TONNE)
        all_n_fert.append(n_fert_per_t_feed)
        all_n2o.append(n2o_per_t_feed * constants.MEGATONNE_TO_TONNE)
        all_marginal_cost.append(marginal_cost)
        all_country.append(country)
        all_product.append(row["product"])
        all_manure_ch4_share.append(manure_ch4_share)
        all_pasture_n2o_share.append(pasture_n2o_share)

    # All animal production links now have multiple outputs:
    # bus1: animal product, bus2: CH4, bus3: manure N fertilizer (country-specific), bus4: N2O
    n.links.add(
        all_names,
        bus0=all_bus0,
        bus1=all_bus1,
        carrier=all_carrier,
        efficiency=all_efficiency,
        marginal_cost=all_marginal_cost,
        p_nom_extendable=True,
        bus2="ch4",
        efficiency2=all_ch4,
        bus3=all_bus3,
        efficiency3=all_n_fert,
        bus4="n2o",
        efficiency4=all_n2o,
        country=all_country,
        product=all_product,
        manure_ch4_share=all_manure_ch4_share,
        pasture_n2o_share=all_pasture_n2o_share,
    )

    logger.info(
        "Added %d feed→animal product links with outputs: product, CH4 (enteric+manure), manure N fertilizer, N2O",
        len(all_names),
    )
    if skipped_count > 0:
        logger.info("Skipped %d links due to missing buses", skipped_count)
