# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Nutrition components for the food systems model.

This module handles food groups, macronutrients, and the links that
convert foods into nutritional outputs for human consumption.
"""

import logging

import numpy as np
import pandas as pd
import pypsa

from .. import constants
from .utils import _nutrition_efficiency_factor

logger = logging.getLogger(__name__)

_LOW_DEFAULT_MARGINAL_COST = (
    0.01 * constants.USD_TO_BNUSD / constants.TONNE_TO_MEGATONNE
)


def add_food_group_buses_and_loads(
    n: pypsa.Network,
    food_group_list: list,
    countries: list,
    population: pd.Series,
    *,
    max_per_capita: dict[str, float] | None = None,
) -> None:
    """Add carriers, buses, and stores for food groups.

    Parameters
    ----------
    n
        The PyPSA network.
    food_group_list
        List of food groups to add.
    countries
        List of country ISO3 codes.
    population
        Population per country (indexed by ISO3).
    max_per_capita
        Optional per-group consumption caps in g/person/day. Applied as e_nom_max
        on stores after converting to Mt/year using country population.
    """

    countries_index = pd.Index(countries, dtype="object")
    pop_values = population.loc[countries].values

    all_store_names = []
    all_store_buses = []
    all_store_carriers = []
    all_store_e_nom_max = []
    all_store_countries = []
    all_store_food_groups = []

    logger.info("Adding food group stores for nutrition requirements...")
    for group in food_group_list:
        buses = "group:" + group + ":" + countries_index
        carrier = f"group_{group}"

        # Compute e_nom_max from per-capita cap if specified
        # Convert g/person/day -> Mt/year: cap_g * pop * 365 / 1e12
        if max_per_capita and group in max_per_capita:
            cap_g = max_per_capita[group]
            e_nom_max_values = cap_g * pop_values * 365 / 1e12
        else:
            e_nom_max_values = np.full(len(countries), np.inf)

        store_names = "store:group:" + group + ":" + countries_index

        all_store_names.extend(store_names)
        all_store_buses.extend(buses)
        all_store_carriers.extend([carrier] * len(countries))
        all_store_e_nom_max.extend(e_nom_max_values)
        all_store_countries.extend(countries)
        all_store_food_groups.extend([group] * len(countries))

    n.stores.add(
        all_store_names,
        bus=all_store_buses,
        carrier=all_store_carriers,
        e_nom_extendable=True,
        e_nom_max=all_store_e_nom_max,
        country=all_store_countries,
        food_group=all_store_food_groups,
    )


def add_macronutrient_loads(
    n: pypsa.Network,
    all_nutrients: list,
    countries: list,
    population: pd.Series,
    nutrient_units: dict[str, str],
) -> None:
    """Add per-country stores for macronutrient tracking.

    Each macronutrient gets an extendable Store per country; the actual
    nutritional bounds are enforced later in ``solve_model`` via explicit
    linopy constraints on the storage level. This keeps the network
    structure simple while making the constraint logic easier to follow.
    """

    logger.info("Adding macronutrient stores and constraints per country...")

    countries_index = pd.Index(countries, dtype="object")
    for nutrient in all_nutrients:
        buses = "nutrient:" + nutrient + ":" + countries_index
        carriers = nutrient

        store_names = "store:nutrient:" + nutrient + ":" + countries_index

        n.stores.add(
            store_names,
            bus=buses,
            carrier=carriers,
            e_nom_extendable=True,
            e_cyclic=False,
            country=countries,
            nutrient=nutrient,
        )


def add_food_nutrition_links(
    n: pypsa.Network,
    food_list: list,
    foods: pd.DataFrame,
    food_groups: pd.DataFrame,
    nutrition: pd.DataFrame,
    nutrient_units: dict[str, str],
    countries: list,
    byproduct_list: list,
    loss_waste: pd.DataFrame,
) -> None:
    """Add multilinks per country for converting foods to groups and macronutrients.

    Byproduct foods (from config) are excluded from human consumption.

    Country- and food-group-specific food-loss-and-waste (FLW) fractions are
    applied here, on the consumption side, rather than on food_processing.
    Each consume link's nutrient and group efficiencies are scaled by the
    country's FLW multiplier ``(1 - loss) * (1 - waste)`` for the food's
    group, and the per-link multiplier is preserved on a ``flw_multiplier``
    column so downstream code (e.g. ``fix_food_consumption_to_baseline``)
    can translate between intake-basis demand and supply-basis bus flow.

    Putting FLW on consumption (rather than on processing as previously)
    removes a global LP arbitrage where low-FLW countries would import dry
    crop and re-export fresh food to extract the cross-country efficiency
    differential. With FLW on consumption every country pays its own waste
    regardless of where processing happened, so the LP defaults to local
    processing and trade carries supply-mass between countries.
    """
    # Pre-index food_groups for lookup
    food_to_group = food_groups.set_index("food")["group"].to_dict()

    # Filter out byproducts from human consumption (using config list)
    byproduct_foods = set(byproduct_list)
    consumable_foods = [f for f in food_list if f not in byproduct_foods]

    if byproduct_foods:
        logger.info(
            "Excluding %d byproduct foods from human consumption: %s",
            len(byproduct_foods),
            ", ".join(sorted(byproduct_foods)),
        )

    # Add food_consumption carrier
    if "food_consumption" not in n.carriers.static.index:
        n.carriers.add("food_consumption", unit="Mt")

    nutrients = list(nutrition.index.get_level_values("nutrient").unique())

    # Pre-compute efficiency factors and the full efficiency matrix
    nutrient_factors = {
        nt: _nutrition_efficiency_factor(nutrient_units[nt]) for nt in nutrients
    }
    eff_matrix = (
        nutrition.reset_index()
        .pivot(index="food", columns="nutrient", values="value")
        .reindex(index=consumable_foods, columns=nutrients)
        .fillna(0.0)
    )
    for nutrient in nutrients:
        eff_matrix[nutrient] *= nutrient_factors[nutrient]

    if not consumable_foods:
        logger.info("No consumable foods configured; skipping food consumption links")
        return

    # Build per-(country, food_group) FLW multipliers. Foods without a group
    # (e.g. byproducts that re-enter as ingredients) get multiplier 1.0.
    normalized_countries = [str(c).upper() for c in countries]
    _lw = loss_waste.copy()
    _lw["loss_fraction"] = _lw["loss_fraction"].clip(0.0, 1.0)
    _lw["waste_fraction"] = _lw["waste_fraction"].clip(0.0, 1.0)
    _lw["multiplier"] = (1 - _lw["loss_fraction"]) * (1 - _lw["waste_fraction"])
    _lw.loc[_lw["multiplier"] <= 0, "multiplier"] = 0.01
    multiplier_lookup = _lw.set_index(["country", "food_group"])["multiplier"]
    extreme_pairs = set(
        zip(
            _lw.loc[
                ((_lw["loss_fraction"] > 0.99) | (_lw["waste_fraction"] > 0.99))
                | (_lw["multiplier"] <= 0.01),
                "country",
            ],
            _lw.loc[
                ((_lw["loss_fraction"] > 0.99) | (_lw["waste_fraction"] > 0.99))
                | (_lw["multiplier"] <= 0.01),
                "food_group",
            ],
        )
    )

    countries_index = pd.Index(normalized_countries, dtype="object")
    foods_index = pd.Index(consumable_foods, dtype="object")

    links_df = (
        pd.MultiIndex.from_product(
            [foods_index, countries_index], names=["food", "country"]
        )
        .to_frame(index=False)
        .astype({"food": "object", "country": "object"})
    )
    links_df["name"] = "consume:" + links_df["food"] + ":" + links_df["country"]
    links_df["bus0"] = "food:" + links_df["food"] + ":" + links_df["country"]
    links_df["food_group"] = links_df["food"].map(food_to_group)

    # Per-link FLW multiplier: lookup by (country, food_group). For links
    # without a food_group the multiplier is 1.0 (no waste accounting).
    with_group_keys = pd.MultiIndex.from_arrays(
        [links_df["country"].values, links_df["food_group"].astype(str).values]
    )
    looked_up = multiplier_lookup.reindex(with_group_keys).to_numpy()
    flw_mult = pd.Series(looked_up, index=links_df.index)
    flw_mult = flw_mult.where(links_df["food_group"].notna(), 1.0)
    if flw_mult.isna().any():
        missing = links_df.loc[
            flw_mult.isna(), ["country", "food_group"]
        ].drop_duplicates()
        raise ValueError(
            "Missing food_loss_waste entries for (country, food_group) pairs: "
            f"{missing.to_dict('records')[:5]} ..."
        )
    links_df["flw_multiplier"] = flw_mult.astype(float)

    encountered_extreme = {
        (c, g)
        for c, g in zip(links_df["country"], links_df["food_group"])
        if (c, g) in extreme_pairs
    }
    if encountered_extreme:
        sample = ", ".join(f"{c}:{g}" for c, g in sorted(encountered_extreme)[:10])
        logger.warning(
            "Extreme food loss/waste values for %d country-group pairs (multiplier clamped to feasible range). Examples: %s",
            len(encountered_extreme),
            sample,
        )

    # Food bus flows are Mt/year (supply basis: pre-consumer-FLW). Each
    # nutrient/group efficiency is scaled by the consumer-side FLW multiplier
    # so the downstream loads receive intake-basis mass.
    def _add_links(batch_df: pd.DataFrame, *, include_group: bool) -> None:
        links = batch_df.set_index("name", drop=False)
        mult = links["flw_multiplier"].astype(float).to_numpy()
        params = {
            "bus0": links["bus0"],
            "carrier": "food_consumption",
            "marginal_cost": _LOW_DEFAULT_MARGINAL_COST,
            "food": links["food"],
            "country": links["country"],
            "flw_multiplier": links["flw_multiplier"],
        }

        for i, nutrient in enumerate(nutrients, start=1):
            bus_key = f"bus{i}"
            eff_key = "efficiency" if i == 1 else f"efficiency{i}"
            params[bus_key] = "nutrient:" + nutrient + ":" + links["country"]
            params[eff_key] = links["food"].map(eff_matrix[nutrient]).to_numpy() * mult

        if include_group:
            idx = len(nutrients) + 1
            params[f"bus{idx}"] = (
                "group:" + links["food_group"].astype(str) + ":" + links["country"]
            )
            params[f"efficiency{idx}"] = mult
            params["food_group"] = links["food_group"]

        n.links.add(links.index, p_nom_extendable=True, **params)

    with_group_df = links_df[links_df["food_group"].notna()]
    if not with_group_df.empty:
        _add_links(with_group_df, include_group=True)

    without_group_df = links_df[links_df["food_group"].isna()]
    if not without_group_df.empty:
        _add_links(without_group_df, include_group=False)
