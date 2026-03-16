# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import contextlib
import ctypes
import gc
import logging

from linopy.constraints import print_single_constraint
import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from workflow.scripts import constants
from workflow.scripts.build_model.utils import _per_capita_mass_to_mt_per_year
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.population import get_country_population
from workflow.scripts.snakemake_utils import apply_scenario_config
from workflow.scripts.solve_model.food_utility import (
    add_piecewise_food_utility,
    pop_piecewise_food_utility_value,
)
from workflow.scripts.solve_model.health import (
    HEALTH_AUX_MAP,
    add_health_objective,
    evaluate_health_posthoc,
)
from workflow.scripts.solve_model.production_stability import (
    add_animal_growth_cap_constraints,
    add_production_stability_constraints,
)

# Module-level logger (replaced by setup_script_logging when run as __main__)
logger = logging.getLogger(__name__)


class _ShadowPriceLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == "pypsa.optimization.optimize"
            and record.getMessage().startswith("The shadow-prices of the constraints")
        )


def add_macronutrient_constraints(
    n: pypsa.Network,
    macronutrient_cfg: dict | None,
    population: dict[str, float],
    baseline_by_nutrient: dict[str, dict[str, float]] | None = None,
) -> None:
    """Add per-country macronutrient bounds directly to the linopy model.

    The bounds are expressed on the storage level of each macronutrient store.
    RHS values are converted from per-person-per-day units using stored
    population and nutrient unit metadata.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    macronutrient_cfg : dict | None
        Macronutrient constraint configuration keyed by nutrient name.
    population : dict[str, float]
        Country → population (thousands of people).
    baseline_by_nutrient : dict[str, dict[str, float]] | None
        Pre-computed per-country baseline values keyed by nutrient name
        (country → per-capita daily intake). Required when any nutrient
        has ``equal_to_baseline: true``.
    """

    if not macronutrient_cfg:
        return

    m = n.model
    store_e = m.variables["Store-e"].sel(snapshot="now")
    stores_df = n.stores.static

    for nutrient, bounds in macronutrient_cfg.items():
        if not bounds:
            continue

        carrier_unit = n.carriers.static.at[nutrient, "unit"]
        nutrient_stores = stores_df[stores_df["carrier"] == nutrient]
        countries = nutrient_stores["country"].astype(str)

        lhs = store_e.sel(name=nutrient_stores.index)

        def rhs_from(
            value: float | dict[str, float],
            carrier_unit=carrier_unit,
            countries=countries,
            nutrient_stores=nutrient_stores,
        ) -> xr.DataArray:
            # Carrier unit encodes the nutrient type: "Mt" for mass, "PJ" for energy (kcal)
            if carrier_unit == "Mt":
                rhs_vals = [
                    _per_capita_mass_to_mt_per_year(
                        float(value[country] if isinstance(value, dict) else value),
                        float(population[country]),
                    )
                    for country in countries
                ]
            else:
                rhs_vals = [
                    float(value[country] if isinstance(value, dict) else value)
                    * float(population[country])
                    * constants.DAYS_PER_YEAR
                    * constants.KCAL_TO_PJ
                    for country in countries
                ]
            return xr.DataArray(
                rhs_vals, coords={"name": nutrient_stores.index}, dims="name"
            )

        for key, operator, label in (
            ("equal", "==", "equal"),
            ("min", ">=", "min"),
            ("max", "<=", "max"),
        ):
            if key == "equal":
                if bounds.get("equal_to_baseline"):
                    rhs = rhs_from(baseline_by_nutrient[nutrient])
                elif bounds.get("equal") is not None:
                    rhs = rhs_from(bounds["equal"])
                else:
                    continue  # no equality constraint

                constr_name = f"macronutrient_equal_{nutrient}"
                m.add_constraints(lhs == rhs, name=f"GlobalConstraint-{constr_name}")
                n.global_constraints.add(
                    f"{constr_name}_" + nutrient_stores.index,
                    sense="==",
                    constant=rhs.values,
                    type="nutrition",
                    country=countries.values,
                    nutrient=nutrient,
                )
                break  # equality silences min/max

            if bounds.get(key) is None:
                continue
            rhs = rhs_from(bounds[key])
            constr_name = f"macronutrient_{label}_{nutrient}"

            if operator == ">=":
                m.add_constraints(lhs >= rhs, name=f"GlobalConstraint-{constr_name}")
            else:
                m.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{constr_name}")

            n.global_constraints.add(
                f"{constr_name}_" + nutrient_stores.index,
                sense=operator,
                constant=rhs.values,
                type="nutrition",
                country=countries.values,
                nutrient=nutrient,
            )


def add_food_group_constraints(
    n: pypsa.Network,
    food_group_cfg: dict | None,
    population: dict[str, float],
    per_country_equal: dict[str, dict[str, float]] | None = None,
) -> None:
    """Add per-country food group bounds on store levels."""

    if not food_group_cfg and not per_country_equal:
        return

    food_group_cfg = food_group_cfg or {}
    per_country_equal = per_country_equal or {}

    m = n.model
    store_e = m.variables["Store-e"].sel(snapshot="now")
    stores_df = n.stores.static

    groups = set(food_group_cfg) | set(per_country_equal)
    for group in groups:
        bounds = food_group_cfg.get(group, {})
        if not bounds and group not in per_country_equal:
            continue

        group_stores = stores_df[stores_df["carrier"] == f"group_{group}"]
        countries = group_stores["country"].astype(str)
        lhs = store_e.sel(name=group_stores.index)

        def rhs_from(
            value: float, countries=countries, group_stores=group_stores
        ) -> xr.DataArray:
            rhs_vals = [
                _per_capita_mass_to_mt_per_year(
                    float(value), float(population[country])
                )
                for country in countries
            ]
            return xr.DataArray(
                rhs_vals, coords={"name": group_stores.index}, dims="name"
            )

        def rhs_from_equal(
            group=group, countries=countries, group_stores=group_stores, bounds=bounds
        ) -> xr.DataArray | None:
            overrides = per_country_equal.get(group)
            if overrides:
                rhs_vals = [
                    _per_capita_mass_to_mt_per_year(
                        float(overrides[country]), float(population[country])
                    )
                    for country in countries
                ]
                return xr.DataArray(
                    rhs_vals, coords={"name": group_stores.index}, dims="name"
                )
            if bounds.get("equal") is None:
                return None
            return rhs_from(bounds["equal"])

        # Apply at most one equality; otherwise allow independent min/max bounds
        for key, operator, label in (
            ("equal", "==", "equal"),
            ("min", ">=", "min"),
            ("max", "<=", "max"),
        ):
            if key == "equal":
                rhs = rhs_from_equal()
                if rhs is None:
                    continue
            else:
                if bounds.get(key) is None:
                    continue
                rhs = rhs_from(bounds[key])

            constr_name = f"food_group_{label}_{group}"

            if operator == "==":
                m.add_constraints(lhs == rhs, name=f"GlobalConstraint-{constr_name}")
                n.global_constraints.add(
                    f"{constr_name}_" + group_stores.index,
                    sense="==",
                    constant=rhs.values,
                    type="nutrition",
                    country=countries.values,
                    food_group=group,
                )
                break

            if operator == ">=":
                m.add_constraints(lhs >= rhs, name=f"GlobalConstraint-{constr_name}")
            else:
                m.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{constr_name}")

            n.global_constraints.add(
                f"{constr_name}_" + group_stores.index,
                sense=operator,
                constant=rhs.values,
                type="nutrition",
                country=countries.values,
                food_group=group,
            )


def add_food_consumption_constraints(
    n: pypsa.Network,
    baseline_df: pd.DataFrame,
    population: dict[str, float],
    slack_cost: float,
) -> None:
    """Add per-food equality constraints on food consumption links.

    For each (food, country), constrains Link-p to the baseline
    consumption value from baseline_diet.csv, with slack variables
    to ensure feasibility.
    """
    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static
    consume_links = links_df[links_df["carrier"] == "food_consumption"]

    df = _prepare_baseline_diet_for_food_constraints(
        baseline_df,
        consume_links,
    )

    # Build link name → target mapping
    consume_links_keyed = consume_links.copy()
    consume_links_keyed["key"] = (
        consume_links_keyed["food"] + ":" + consume_links_keyed["country"]
    )
    df["key"] = df["food"] + ":" + df["country"]

    # Match baseline to links
    matched = df.merge(
        consume_links_keyed[["key"]].reset_index(),
        on="key",
    )

    if matched.empty:
        logger.warning("No matching food consumption links for baseline diet data")
        return

    link_names = matched["name"].values
    targets_g = matched["consumption_g_per_day"].values
    countries = matched["country"].values

    # Convert g/person/day → Mt/year per country
    targets_mt = np.array(
        [
            _per_capita_mass_to_mt_per_year(g, population[c])
            for g, c in zip(targets_g, countries)
        ]
    )

    target_arr = xr.DataArray(
        targets_mt, coords={"name": list(link_names)}, dims="name"
    )
    food_var = link_p.sel(name=list(link_names))

    # Linopy slack variables
    link_coords = [list(link_names)]
    link_dims = ["name"]
    slack_pos = m.add_variables(
        lower=0, coords=link_coords, dims=link_dims, name="food_slack_pos"
    )
    slack_neg = m.add_variables(
        lower=0, coords=link_coords, dims=link_dims, name="food_slack_neg"
    )

    # Constraint: food_var + slack_neg - slack_pos == target
    m.add_constraints(
        food_var + slack_neg - slack_pos == target_arr,
        name="GlobalConstraint-food_equal",
    )

    # Slack penalty in objective
    m.objective += slack_cost * (slack_pos.sum() + slack_neg.sum())

    # Register global constraints for dual extraction
    foods = matched["food"].values
    # Constraint dual assignment in PyPSA maps vectorized GlobalConstraint duals
    # by appending the linopy dimension value (here: link name) to the
    # "GlobalConstraint-food_equal" prefix. Use matching GlobalConstraint names
    # so duals are written back correctly (e.g. food_equal_consume:wheat:USA).
    gc_names = [f"food_equal_{link}" for link in link_names]
    food_groups = matched["food_group"].values
    n.global_constraints.add(
        gc_names,
        sense="==",
        constant=targets_mt,
        type="food_consumption",
        food=foods,
        food_group=food_groups,
        country=countries,
    )

    logger.info(
        "Added %d per-food consumption equality constraints (slack cost=%.1f)",
        len(matched),
        slack_cost,
    )


def _prepare_baseline_diet_for_food_constraints(
    baseline_df: pd.DataFrame,
    consume_links: pd.DataFrame,
    *,
    min_consumption_g_per_day: float = 0.1,
) -> pd.DataFrame:
    """Prepare baseline diet data for food constraints and ratio derivation.

    Uses the same filtering and clipping logic as per-food equality constraints
    so ratio-based runs are consistent with baseline-equality runs.
    """
    df = baseline_df.copy()
    df["country"] = df["country"].astype(str).str.upper()
    df["food"] = df["food"].astype(str)
    df["consumption_g_per_day"] = pd.to_numeric(
        df["consumption_g_per_day"], errors="coerce"
    ).fillna(0.0)

    model_keys = set(zip(consume_links["food"], consume_links["country"]))
    df = df[df.apply(lambda r: (r["food"], r["country"]) in model_keys, axis=1)].copy()

    df["consumption_g_per_day"] = df["consumption_g_per_day"].clip(
        lower=min_consumption_g_per_day
    )
    return df


def _compute_baseline_macronutrient_by_country(
    baseline_df: pd.DataFrame,
    nutrition_df: pd.DataFrame,
    nutrient: str,
) -> dict[str, float]:
    """Compute per-country per-capita macronutrient intake from the baseline diet.

    Parameters
    ----------
    baseline_df : pd.DataFrame
        Baseline diet with columns: country, food, consumption_g_per_day.
        Should already be filtered to foods present in the model.
    nutrition_df : pd.DataFrame
        Nutrition table with columns: food, nutrient, unit, value.
        Values are per 100 g of food.
    nutrient : str
        Nutrient identifier matching nutrition_df (e.g. "cal", "carb").

    Returns
    -------
    dict[str, float]
        Country → per-capita daily intake (g/person/day for mass nutrients,
        kcal/person/day for "cal").
    """
    nut_vals = nutrition_df[nutrition_df["nutrient"] == nutrient].set_index("food")[
        "value"
    ]
    df = baseline_df[baseline_df["food"].isin(nut_vals.index)].copy()
    df["nutrient_per_day"] = (
        df["consumption_g_per_day"] * df["food"].map(nut_vals) / 100
    )
    return df.groupby("country")["nutrient_per_day"].sum().to_dict()


def _build_ratios_from_baseline(baseline_df: pd.DataFrame) -> pd.DataFrame:
    """Derive within-group food ratios from baseline diet data."""
    df = baseline_df.copy()
    totals = df.groupby(["country", "food_group"])["consumption_g_per_day"].transform(
        "sum"
    )
    df["ratio"] = 0.0
    nonzero = totals > 0
    df.loc[nonzero, "ratio"] = (
        df.loc[nonzero, "consumption_g_per_day"] / totals[nonzero]
    )
    return df[["country", "food_group", "food", "ratio"]].copy()


def add_ghg_pricing_to_objective(n: pypsa.Network, ghg_price_usd_per_t: float) -> None:
    """Add GHG emissions pricing to the objective function.

    Adds the cost of GHG emissions (stored in the 'ghg' store) to the
    objective function at solve time.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    ghg_price_usd_per_t : float
        Price per tonne of CO2-equivalent in USD (config currency_year).
    """
    # Convert USD/tCO2 to bnUSD/MtCO2 (matching model units)
    ghg_price_bnusd_per_mt = (
        ghg_price_usd_per_t / constants.TONNE_TO_MEGATONNE * constants.USD_TO_BNUSD
    )

    # Add marginal storage cost to store
    n.stores.static.at["store:emission:ghg", "marginal_cost_storage"] = (
        ghg_price_bnusd_per_mt
    )


def add_food_incentives_to_objective(
    n: pypsa.Network, incentives_paths: list[str]
) -> None:
    """Add food-level incentives/penalties to the objective function.

    Incentives are applied as adjustments to marginal costs of food
    consumption links. Positive values penalize consumption; negative
    values subsidize consumption.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    incentives_paths : list[str]
        Paths to CSVs with columns: food, country, adjustment_bnusd_per_mt
    """
    if not incentives_paths:
        raise ValueError("food_incentives enabled but no sources are configured")

    combined = []
    for path in incentives_paths:
        incentives_df = pd.read_csv(path)
        required = {"food", "country", "adjustment_bnusd_per_mt"}
        missing = required - set(incentives_df.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(
                f"Missing required columns in incentives file {path}: {missing_text}"
            )

        incentives_df["country"] = incentives_df["country"].astype(str).str.upper()
        combined.append(
            incentives_df[["food", "country", "adjustment_bnusd_per_mt"]].copy()
        )

    all_incentives = pd.concat(combined, ignore_index=True)
    summed = (
        all_incentives.groupby(["food", "country"])["adjustment_bnusd_per_mt"]
        .sum()
        .reset_index()
    )

    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]

    applied = 0
    for _, row in summed.iterrows():
        mask = (consume_links["food"] == row["food"]) & (
            consume_links["country"] == row["country"]
        )
        link_names = consume_links[mask].index
        if not link_names.empty:
            n.links.static.loc[link_names, "marginal_cost"] += row[
                "adjustment_bnusd_per_mt"
            ]
            applied += len(link_names)

    if applied == 0:
        logger.info(
            "No applicable food incentives found in %d sources",
            len(incentives_paths),
        )
        return

    logger.info(
        "Applied food incentives to %d consumption links from %d sources",
        applied,
        len(incentives_paths),
    )


def build_residue_feed_fraction_by_country(
    config: dict, m49_path: str
) -> dict[str, float]:
    """Build per-country residue feed fraction overrides from config."""
    overrides = config["residues"]["max_feed_fraction_by_region"]
    if not overrides:
        return {}

    countries = [str(country).upper() for country in config["countries"]]

    m49_df = pd.read_csv(m49_path, sep=";", encoding="utf-8-sig", comment="#")
    m49_df = m49_df[m49_df["ISO-alpha3 Code"].notna()]
    m49_df["iso3"] = m49_df["ISO-alpha3 Code"].astype(str).str.upper()
    m49_df = m49_df[m49_df["iso3"].isin(countries)]

    region_to_countries = m49_df.groupby("Region Name")["iso3"].apply(list).to_dict()
    subregion_to_countries = (
        m49_df.groupby("Sub-region Name")["iso3"].apply(list).to_dict()
    )

    region_overrides = {
        key: overrides[key] for key in overrides if key in region_to_countries
    }
    subregion_overrides = {
        key: overrides[key] for key in overrides if key in subregion_to_countries
    }
    country_overrides = {key: overrides[key] for key in overrides if key in countries}

    unknown = (
        set(overrides)
        - set(region_overrides)
        - set(subregion_overrides)
        - set(country_overrides)
    )
    if unknown:
        unknown_text = ", ".join(sorted(unknown))
        raise ValueError(
            f"Unknown residues.max_feed_fraction_by_region keys: {unknown_text}"
        )

    per_country: dict[str, float] = {}
    for region, value in region_overrides.items():
        for country in region_to_countries[region]:
            per_country[country] = float(value)
    for subregion, value in subregion_overrides.items():
        for country in subregion_to_countries[subregion]:
            per_country[country] = float(value)
    for country, value in country_overrides.items():
        per_country[country] = float(value)

    return per_country


def add_residue_feed_constraints(
    n: pypsa.Network,
    max_feed_fraction: float,
    max_feed_fraction_by_country: dict[str, float],
) -> None:
    """Add constraints limiting residue removal for animal feed.

    Constrains the fraction of residues that can be removed for feed vs.
    incorporated into soil. The constraint is formulated as::

        feed_use ≤ (max_feed_fraction / (1 - max_feed_fraction)) x incorporation

    This ensures that if a total amount R of residue is generated::

        R = feed_use + incorporation
        feed_use ≤ max_feed_fraction x R

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    max_feed_fraction : float
        Maximum fraction of residues that can be used for feed (e.g., 0.30 for 30%).
    max_feed_fraction_by_country : dict[str, float]
        Overrides keyed by ISO3 country code.
    """

    m = n.model

    # Get link flow variables and link data
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    # Find residue feed links (carrier="feed_conversion", bus0 starts with "residue:")
    feed_mask = (links_df["carrier"] == "feed_conversion") & (
        links_df["bus0"].str.startswith("residue:")
    )
    feed_links_df = links_df[feed_mask]

    # Find incorporation links (carrier="residue_incorporation")
    incorp_mask = links_df["carrier"] == "residue_incorporation"
    incorp_links_df = links_df[incorp_mask]

    if feed_links_df.empty or incorp_links_df.empty:
        logger.info(
            "No residue feed limit constraints added (missing feed or incorporation links)"
        )
        return

    # Identify common residue buses
    feed_buses = set(feed_links_df["bus0"].unique())
    incorp_buses = set(incorp_links_df["bus0"].unique())
    common_buses = sorted(feed_buses.intersection(incorp_buses))

    if not common_buses:
        logger.info(
            "No residue feed limit constraints added (no matching residue flows found)"
        )
        return

    # Filter DataFrames to common buses
    feed_links_df = feed_links_df[feed_links_df["bus0"].isin(common_buses)]
    incorp_links_df = incorp_links_df[incorp_links_df["bus0"].isin(common_buses)]

    # Prepare mapping DataArrays for groupby
    # Map feed link names to their residue bus
    feed_bus_map = xr.DataArray(
        feed_links_df["bus0"],
        coords={"name": feed_links_df.index},
        dims="name",
        name="residue_bus",
    )

    # Map incorp link names to their residue bus
    incorp_bus_map = xr.DataArray(
        incorp_links_df["bus0"],
        coords={"name": incorp_links_df.index},
        dims="name",
        name="residue_bus",
    )

    # Get variables
    feed_vars = link_p.sel(name=feed_links_df.index)
    incorp_vars = link_p.sel(name=incorp_links_df.index)

    # Sum/Group
    # Group feed vars by residue bus and sum
    feed_sum = feed_vars.groupby(feed_bus_map).sum()

    # Group incorp vars by residue bus and sum (handles alignment)
    incorp_flow = incorp_vars.groupby(incorp_bus_map).sum()

    # Build bus-to-country mapping from incorporation links (which have country column)
    bus_to_country = incorp_links_df.groupby("bus0")["country"].first().to_dict()

    ratios = []
    for bus in common_buses:
        country = str(bus_to_country.get(bus, "")).upper()
        max_fraction = max_feed_fraction_by_country.get(country, max_feed_fraction)
        ratios.append(max_fraction / (1.0 - max_fraction))

    ratio = xr.DataArray(
        ratios, coords={"residue_bus": common_buses}, dims="residue_bus"
    )

    # Add constraints
    constr_name = "residue_feed_limit"
    m.add_constraints(
        feed_sum <= ratio * incorp_flow,
        name=f"GlobalConstraint-{constr_name}",
    )

    # Add GlobalConstraints for shadow price tracking
    gc_names = [f"{constr_name}_{bus}" for bus in common_buses]
    gc_countries = [str(bus_to_country.get(bus, "")).upper() for bus in common_buses]
    n.global_constraints.add(
        gc_names,
        sense="<=",
        constant=0.0,  # RHS is dynamic (depends on incorp_flow), use 0 as placeholder
        type="residue_feed",
        country=gc_countries,
    )

    if max_feed_fraction_by_country:
        logger.info(
            "Applied residue feed fraction overrides for %d countries",
            len(max_feed_fraction_by_country),
        )

    logger.info(
        "Added %d residue feed limit constraints (max %.0f%% for feed)",
        len(common_buses),
        max_feed_fraction * 100,
    )


def add_within_group_ratio_constraints(
    n: pypsa.Network,
    ratios_df: pd.DataFrame,
) -> None:
    """Fix relative food contributions within each food group.

    For each (country, food_group), adds linear constraints that fix the ratio
    between different foods based on baseline consumption data. For foods
    f_1, ..., f_n with baseline ratios r_1, ..., r_n, the reference food f_1
    (highest ratio) is unconstrained while others satisfy:

        consumption(f_i) = (r_i / r_1) * consumption(f_1)   for i = 2, ..., n

    This preserves baseline proportions while allowing total group consumption
    to vary. Groups with only one food are skipped.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model (with linopy model attached).
    ratios_df : pd.DataFrame
        Ratios with columns: country, food_group, food, ratio
    """
    m = n.model
    links_df = n.links.static
    link_p = m.variables["Link-p"].sel(snapshot="now")

    # Get food consumption links
    consume_links = links_df[links_df["carrier"] == "food_consumption"].copy()

    if consume_links.empty:
        logger.warning("No food consumption links found; skipping ratio constraints")
        return

    # Build (country, food) -> link name mapping
    consume_links["key"] = consume_links["country"] + ":" + consume_links["food"]

    # For each (country, food_group), identify reference food (highest ratio)
    ref_foods = (
        ratios_df.sort_values("ratio", ascending=False)
        .groupby(["country", "food_group"])
        .first()
        .reset_index()
        .rename(columns={"food": "ref_food", "ratio": "ref_ratio"})
    )

    # Merge to get relative ratios for each food
    ratios_with_ref = ratios_df.merge(
        ref_foods[["country", "food_group", "ref_food", "ref_ratio"]],
        on=["country", "food_group"],
    )

    # Exclude reference foods (they don't need constraints)
    non_ref = ratios_with_ref[
        ratios_with_ref["food"] != ratios_with_ref["ref_food"]
    ].copy()

    if non_ref.empty:
        logger.info("No within-group ratio constraints to add (each group has ≤1 food)")
        return

    # Calculate relative ratio (handle zero ref_ratio)
    non_ref["rel_ratio"] = 0.0
    nonzero_mask = non_ref["ref_ratio"] > 0
    non_ref.loc[nonzero_mask, "rel_ratio"] = (
        non_ref.loc[nonzero_mask, "ratio"] / non_ref.loc[nonzero_mask, "ref_ratio"]
    )

    non_ref["food_key"] = non_ref["country"] + ":" + non_ref["food"]
    non_ref["ref_key"] = non_ref["country"] + ":" + non_ref["ref_food"]

    # Filter to foods that exist in the model
    existing_keys = set(consume_links["key"])
    non_ref = non_ref[
        non_ref["food_key"].isin(existing_keys) & non_ref["ref_key"].isin(existing_keys)
    ].copy()

    if non_ref.empty:
        logger.info("No within-group ratio constraints to add (no matching foods)")
        return

    # Build link name arrays
    food_link_names = (
        non_ref["food_key"]
        .map(lambda k: consume_links[consume_links["key"] == k].index[0])
        .values
    )
    ref_link_names = (
        non_ref["ref_key"]
        .map(lambda k: consume_links[consume_links["key"] == k].index[0])
        .values
    )

    # Get link variables
    food_vars = link_p.sel(name=list(food_link_names))
    ref_vars = link_p.sel(name=list(ref_link_names))

    # Build relative ratio array with matching coordinates
    rel_ratio_arr = xr.DataArray(
        non_ref["rel_ratio"].values,
        coords={"name": list(food_link_names)},
        dims="name",
    )

    # Rename ref_vars dimension to align with food_vars
    ref_vars_aligned = ref_vars.assign_coords(name=list(food_link_names))

    # Add vectorized constraint: food_var == rel_ratio * ref_var
    m.add_constraints(
        food_vars - rel_ratio_arr * ref_vars_aligned == 0,
        name="GlobalConstraint-food_ratio",
    )

    # Add GlobalConstraints for shadow price tracking
    gc_names = [
        f"food_ratio_{row['country']}_{row['food_group']}_{row['food']}"
        for _, row in non_ref.iterrows()
    ]
    n.global_constraints.add(
        gc_names,
        sense="==",
        constant=0.0,
        type="food_ratio",
        country=non_ref["country"].values,
        food_group=non_ref["food_group"].values,
        food=non_ref["food"].values,
    )

    logger.info("Added %d within-group food ratio constraints", len(non_ref))


def _run_solve() -> None:
    """Main solve logic, factored out for profiling."""
    global logger
    logger = setup_script_logging(snakemake.log[0])
    # Suppress the noisy PyPSA shadow-price info log.
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())

    # Apply scenario config overrides based on wildcard
    apply_scenario_config(snakemake.config, snakemake.wildcards.scenario)

    n = pypsa.Network(snakemake.input.network)

    # Add GHG pricing to the objective if enabled
    if snakemake.config["emissions"]["ghg_pricing_enabled"]:
        ghg_price = float(snakemake.params.ghg_price)
        add_ghg_pricing_to_objective(n, ghg_price)

    incentives_enabled = bool(snakemake.config["food_incentives"]["enabled"])
    piecewise_utility_cfg = snakemake.params.food_utility_piecewise
    piecewise_utility_enabled = bool(piecewise_utility_cfg["enabled"])

    if incentives_enabled and piecewise_utility_enabled:
        raise ValueError(
            "food_incentives and food_utility_piecewise cannot both be enabled"
        )

    # Add food-level linear incentives to marginal costs if enabled
    if incentives_enabled:
        incentives_paths = list(snakemake.input.food_incentives)
        add_food_incentives_to_objective(n, incentives_paths)

    # Create the linopy model
    logger.info("Creating linopy model...")
    n.optimize.create_model(include_objective_constant=False)
    logger.info("Linopy model created.")

    if piecewise_utility_enabled:
        if bool(snakemake.params.enforce_baseline):
            raise ValueError(
                "food_utility_piecewise cannot be combined with "
                "validation.enforce_baseline_diet=true"
            )
        add_piecewise_food_utility(
            n,
            snakemake.input.food_utility_piecewise,
            float(piecewise_utility_cfg["min_block_width_mt"]),
        )

    solver_name = snakemake.params.solver
    solver_options = dict(snakemake.params.solver_options)
    io_api = snakemake.params.io_api
    netcdf_config = snakemake.params.netcdf

    # Configure Gurobi logging. Explicitly creating an Env with
    # OutputFlag=0 silences the license banner and "Set parameter"
    # messages that are otherwise printed to stdout before linopy
    # starts solving.
    #
    # Solver progress (iteration tables, barrier log, etc.) is
    # generated by the C library and only goes to LogToConsole or
    # LogFile — it does NOT flow through Python's logging module.
    # We therefore set LogFile to the snakemake log so that solver
    # progress appears in the written log.  Gurobi opens LogFile in
    # append mode, so it coexists with the Python FileHandler.
    gurobi_env = None
    if solver_name.lower() == "gurobi":
        import gurobipy as gp

        gurobi_env = gp.Env(params={"OutputFlag": 0})

        if snakemake.log and "LogToConsole" not in solver_options:
            solver_options["LogToConsole"] = 0
        if snakemake.log and "LogFile" not in solver_options:
            solver_options["LogFile"] = str(snakemake.log[0])

    # Get population from network metadata
    population_map = get_country_population(n)

    # Load baseline diet data (used for food-level enforcement and/or ratio constraints)
    baseline_df = pd.read_csv(snakemake.input.baseline_diet)
    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
    prepared_baseline_df = _prepare_baseline_diet_for_food_constraints(
        baseline_df,
        consume_links,
    )

    # Food-level baseline enforcement (per-food equality constraints)
    per_country_equal: dict[str, dict[str, float]] | None = None
    equal_source = snakemake.config["food_groups"]["equal_by_country_source"]
    enforce_baseline = bool(snakemake.params.enforce_baseline)
    if enforce_baseline and equal_source:
        raise ValueError(
            "Cannot combine enforce_baseline_diet with food_groups.equal_by_country_source"
        )
    if enforce_baseline:
        slack_cost = float(snakemake.config["validation"]["slack_marginal_cost"])
        add_food_consumption_constraints(
            n, prepared_baseline_df, population_map, slack_cost
        )
    elif equal_source:
        equal_df = pd.read_csv(snakemake.input.food_group_equal)
        required = {"group", "country", "consumption_g_per_day"}
        missing = required - set(equal_df.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(
                f"Missing required columns in food group equality file: {missing_text}"
            )
        equal_df["country"] = equal_df["country"].astype(str).str.upper()
        per_country_equal = {}
        all_countries = set(population_map.keys())
        for group, group_df in equal_df.groupby("group"):
            values = dict.fromkeys(all_countries, 0.0)
            for _, row in group_df.iterrows():
                country = str(row["country"]).upper()
                if country not in values:
                    logger.warning(
                        "Unknown country '%s' in food group equality file", country
                    )
                    continue
                values[country] = float(row["consumption_g_per_day"])
            missing_countries = sorted(all_countries - set(group_df["country"]))
            if missing_countries:
                preview = ", ".join(missing_countries[:5])
                logger.warning(
                    "Food group '%s' missing %d countries in equality file; "
                    "setting them to 0 (examples: %s)",
                    group,
                    len(missing_countries),
                    preview,
                )
            per_country_equal[str(group)] = values

    macronutrient_cfg = snakemake.params.macronutrients or {}
    baseline_by_nutrient: dict[str, dict[str, float]] | None = None
    needs_baseline = any(
        isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        for bounds in macronutrient_cfg.values()
    )
    if needs_baseline:
        nutrition_df = pd.read_csv(snakemake.input.nutrition)
        baseline_by_nutrient = {
            nutrient: _compute_baseline_macronutrient_by_country(
                prepared_baseline_df, nutrition_df, nutrient
            )
            for nutrient, bounds in macronutrient_cfg.items()
            if isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        }

    add_macronutrient_constraints(
        n, macronutrient_cfg, population_map, baseline_by_nutrient
    )
    add_food_group_constraints(
        n,
        snakemake.params.food_group_constraints,
        population_map,
        per_country_equal,
    )

    # Add residue feed limit constraints
    max_feed_fraction = float(snakemake.config["residues"]["max_feed_fraction"])
    max_feed_fraction_by_country = build_residue_feed_fraction_by_country(
        snakemake.config, snakemake.input.m49
    )
    add_residue_feed_constraints(n, max_feed_fraction, max_feed_fraction_by_country)

    # Add production stability constraints (per-link for both crops and animals)
    stability_cfg = snakemake.params.production_stability
    if stability_cfg["enabled"]:
        slack_marginal_cost = float(
            snakemake.config["validation"]["slack_marginal_cost"]
        )
        add_production_stability_constraints(n, stability_cfg, slack_marginal_cost)

    # Add animal growth cap constraints (independent of production stability)
    animal_growth_cap_cfg = snakemake.params.animal_growth_cap
    add_animal_growth_cap_constraints(n, animal_growth_cap_cfg)

    # Add within-group food ratio constraints if enabled (separate from baseline enforcement)
    ratio_cfg = snakemake.params.fix_within_group_ratios
    if ratio_cfg["enabled"] and not enforce_baseline:
        ratios_df = _build_ratios_from_baseline(prepared_baseline_df)
        add_within_group_ratio_constraints(n, ratios_df)
    elif ratio_cfg["enabled"] and enforce_baseline:
        logger.info(
            "Skipping fix_within_group_ratios: redundant when enforce_baseline_diet=true"
        )

    # Add health impacts if enabled
    health_enabled = bool(snakemake.params.health_enabled)
    value_per_yll = float(snakemake.params.health_value_per_yll)
    if health_enabled and value_per_yll > 0:
        # Extract per-risk-factor RR quantiles from sensitivity config
        sensitivity_cfg = snakemake.params.sensitivity or {}
        rr_quantiles = sensitivity_cfg.get("health_relative_risk") or None

        add_health_objective(
            n,
            snakemake.input.health_risk_breakpoints,
            snakemake.input.health_cluster_cause,
            snakemake.input.health_cause_log,
            snakemake.input.health_cluster_summary,
            snakemake.input.health_clusters,
            snakemake.params.health_risk_factors,
            snakemake.params.health_risk_cause_map,
            value_per_yll,
            snakemake.input.health_cluster_risk_baseline,
            rr_quantiles=rr_quantiles,
            tmrel_path=snakemake.input.health_derived_tmrel,
        )

    # Export fully-constructed model to MPS for Gurobi parameter tuning
    if snakemake.config["solving"].get("export_for_tuning"):
        from pathlib import Path

        output_path = Path(snakemake.output.network).with_suffix(".mps")
        logger.info("Exporting model to %s for tuning...", output_path)
        gp_model = n.model.to_gurobipy(env=gurobi_env)
        gp_model.update()
        gp_model.write(str(output_path))
        del gp_model
        gc.collect()
        logger.info("Model exported. Run tuning with:")
        logger.info("  pixi run -e gurobi python tools/tune_model.py %s", output_path)

    status, condition = n.model.solve(
        solver_name=solver_name,
        io_api=io_api,
        env=gurobi_env,
        calculate_fixed_duals=snakemake.params.calculate_fixed_duals,
        reformulate_sos="auto",
        **solver_options,
    )
    result = (status, condition)

    # Free solver-internal model (Gurobi/HiGHS); solution is already stored
    # in linopy variables so assign_solution/assign_duals still work.
    # Keep the solver model alive when infeasible so IIS can be computed.
    if (
        condition not in ("infeasible", "infeasible_or_unbounded")
        and hasattr(n.model, "solver_model")
        and n.model.solver_model is not None
    ):
        with contextlib.suppress(AttributeError):
            n.model.solver_model.dispose()
        n.model.solver_model = None
        gc.collect()

    if status == "ok":
        aux_names = HEALTH_AUX_MAP.pop(id(n.model), set())
        variables_container = n.model.variables
        removed = {}
        for name in aux_names:
            if name in variables_container.data:
                removed[name] = variables_container.data.pop(name)

        try:
            n.optimize.assign_solution()
            n.optimize.assign_duals(False)
            n.optimize.post_processing()
        finally:
            if removed:
                variables_container.data.update(removed)

        piecewise_utility_value = pop_piecewise_food_utility_value(n)
        if abs(piecewise_utility_value) > 1e-12:
            n.meta["food_utility_cost"] = -piecewise_utility_value

        # Extract production stability slack values if present
        production_slack = {}
        if "crop_production_slack" in n.model.variables:
            crop_slack_sol = n.model.variables["crop_production_slack"].solution
            # Convert tuple keys to strings for JSON serialization
            production_slack["crop"] = {
                str(k): v for k, v in crop_slack_sol.to_series().to_dict().items()
            }
            total_crop_slack = float(crop_slack_sol.sum())
            if total_crop_slack > 1e-6:
                logger.info(
                    "Crop production slack used: %.4f Mt total", total_crop_slack
                )
        if "animal_production_slack" in n.model.variables:
            animal_slack_sol = n.model.variables["animal_production_slack"].solution
            # Convert tuple keys to strings for JSON serialization
            production_slack["animal"] = {
                str(k): v for k, v in animal_slack_sol.to_series().to_dict().items()
            }
            total_animal_slack = float(animal_slack_sol.sum())
            if total_animal_slack > 1e-6:
                logger.info(
                    "Animal production slack used: %.4f Mt total", total_animal_slack
                )
        if production_slack:
            n.meta["production_stability_slack"] = production_slack

        # Store food slack cost for objective breakdown extraction
        if "food_slack_pos" in n.model.variables:
            slack_pos_sol = n.model.variables["food_slack_pos"].solution
            slack_neg_sol = n.model.variables["food_slack_neg"].solution
            food_slack_total = float(slack_pos_sol.sum() + slack_neg_sol.sum())
            food_slack_cost_val = float(
                snakemake.config["validation"]["slack_marginal_cost"]
            )
            n.meta["food_slack_cost"] = food_slack_total * food_slack_cost_val
            if food_slack_total > 1e-6:
                logger.info(
                    "Food slack used: %.4f Mt total (cost: %.2f bnUSD)",
                    food_slack_total,
                    n.meta["food_slack_cost"],
                )

        # Store production stability penalty cost for objective breakdown.
        # L1/quadratic penalties are linopy-level terms not visible to PyPSA
        # statistics; record them in metadata so the breakdown can account
        # for them.
        stability_cost = 0.0
        for var_name, cost_key in [
            ("crop_stability_abs_dev", "l1_cost"),
            ("grassland_stability_abs_dev", "l1_cost"),
            ("animal_stability_abs_dev", "l1_cost"),
            ("land_conversion_stability_abs_dev", "l1_cost"),
        ]:
            if var_name in n.model.variables:
                sol = n.model.variables[var_name].solution
                cost = float(stability_cfg.get(cost_key, 0))
                stability_cost += cost * float(sol.sum())
        for var_name, cost_key in [
            ("crop_stability_dev", "quadratic_cost"),
            ("grassland_stability_dev", "quadratic_cost"),
            ("animal_stability_dev", "quadratic_cost"),
            ("land_conversion_stability_dev", "quadratic_cost"),
        ]:
            if var_name in n.model.variables:
                sol = n.model.variables[var_name].solution
                cost = float(stability_cfg.get(cost_key, 0))
                stability_cost += 0.5 * cost * float((sol * sol).sum())
        if abs(stability_cost) > 1e-12:
            n.meta["production_stability_cost"] = stability_cost

        # Post-hoc health evaluation when value_per_yll == 0
        if health_enabled and value_per_yll == 0:
            sensitivity_cfg = snakemake.params.sensitivity or {}
            rr_quantiles = sensitivity_cfg.get("health_relative_risk") or None
            evaluate_health_posthoc(
                n,
                risk_breakpoints_path=snakemake.input.health_risk_breakpoints,
                cluster_cause_path=snakemake.input.health_cluster_cause,
                cause_log_path=snakemake.input.health_cause_log,
                clusters_path=snakemake.input.health_clusters,
                risk_factors=snakemake.params.health_risk_factors,
                risk_cause_map=snakemake.params.health_risk_cause_map,
                rr_quantiles=rr_quantiles,
                tmrel_path=snakemake.input.health_derived_tmrel,
            )

        # Free the linopy model before export; all values have been assigned.
        n._model = None
        gc.collect()
        with contextlib.suppress(OSError):
            ctypes.CDLL("libc.so.6").malloc_trim(0)

        n.export_to_netcdf(
            snakemake.output.network,
            compression=netcdf_config["compression"],
            float32=netcdf_config["float32"],
        )
    elif condition in {"infeasible", "infeasible_or_unbounded"}:
        logger.error("Model is infeasible or unbounded!")
        if solver_name.lower() == "gurobi":
            try:
                logger.error("Computing IIS (Irreducible Inconsistent Subsystem)...")

                # Get infeasible constraint labels
                infeasible_labels = n.model.compute_infeasibilities()

                if not infeasible_labels:
                    logger.error("No infeasible constraints found in IIS")
                else:
                    logger.error(
                        "Found %d infeasible constraints:", len(infeasible_labels)
                    )

                    constraint_details = []
                    for label in infeasible_labels:
                        try:
                            detail = print_single_constraint(n.model, label)
                            constraint_details.append(detail)
                        except Exception as e:
                            constraint_details.append(
                                f"Label {label}: <error formatting: {e}>"
                            )

                    # Log all infeasible constraints
                    iis_output = "\n".join(constraint_details)
                    logger.error("IIS constraints:\n%s", iis_output)

            except Exception as exc:
                logger.error("Could not compute infeasibilities: %s", exc)
        else:
            logger.error("Infeasibility diagnosis only available with Gurobi solver")
    else:
        logger.error("Optimization unsuccessful: %s", result)


if __name__ == "__main__":
    import os

    profile_enabled = os.environ.get("PROFILE_SOLVE", "0") == "1"

    if profile_enabled:
        import cProfile
        from pathlib import Path
        import pstats

        # Run with profiling
        profile_path = Path(snakemake.output.network).with_suffix(".prof")
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            _run_solve()
        finally:
            profiler.disable()
            # Save raw profile for later analysis (e.g., snakeviz)
            profiler.dump_stats(str(profile_path))

            # Print summary stats to log
            stats = pstats.Stats(profiler)
            stats.strip_dirs()
            stats.sort_stats("cumulative")

            # Print top 50 functions by cumulative time
            print("\n" + "=" * 80)
            print("PROFILING RESULTS - Top 50 by cumulative time")
            print("=" * 80)
            stats.print_stats(50)

            print("\n" + "=" * 80)
            print("PROFILING RESULTS - Top 50 by total time (self)")
            print("=" * 80)
            stats.sort_stats("tottime")
            stats.print_stats(50)

            print(f"\nFull profile saved to: {profile_path}")
            print("Analyze with: pixi run python -m snakeviz " + str(profile_path))
    else:
        _run_solve()
