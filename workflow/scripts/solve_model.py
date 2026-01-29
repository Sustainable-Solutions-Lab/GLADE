# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from linopy.constraints import print_single_constraint
import pandas as pd
import pypsa
import xarray as xr

from workflow.scripts import constants
from workflow.scripts.build_model.nutrition import (
    _build_food_group_equals_from_baseline,
)
from workflow.scripts.build_model.utils import _per_capita_mass_to_mt_per_year
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.population import get_country_population
from workflow.scripts.snakemake_utils import apply_scenario_config
from workflow.scripts.solve_model.health import (
    HEALTH_AUX_MAP,
    add_health_objective,
)
from workflow.scripts.solve_model.production_stability import (
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
    n: pypsa.Network, macronutrient_cfg: dict | None, population: dict[str, float]
) -> None:
    """Add per-country macronutrient bounds directly to the linopy model.

    The bounds are expressed on the storage level of each macronutrient store.
    RHS values are converted from per-person-per-day units using stored
    population and nutrient unit metadata.
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
            value: float,
            carrier_unit=carrier_unit,
            countries=countries,
            nutrient_stores=nutrient_stores,
        ) -> xr.DataArray:
            # Carrier unit encodes the nutrient type: "Mt" for mass, "PJ" for energy (kcal)
            if carrier_unit == "Mt":
                rhs_vals = [
                    _per_capita_mass_to_mt_per_year(
                        float(value), float(population[country])
                    )
                    for country in countries
                ]
            else:
                rhs_vals = [
                    float(value)
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
            if bounds.get(key) is None:
                continue
            rhs = rhs_from(bounds[key])
            constr_name = f"macronutrient_{label}_{nutrient}"

            if operator == "==":
                m.add_constraints(lhs == rhs, name=f"GlobalConstraint-{constr_name}")
                n.global_constraints.add(
                    f"{constr_name}_" + nutrient_stores.index,
                    sense="==",
                    constant=rhs.values,
                    type="nutrition",
                    country=countries.values,
                    nutrient=nutrient,
                )
                break

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


def _apply_solver_threads_option(
    solver_options: dict, solver_name: str, threads: int
) -> dict:
    """Ensure the solver options include a threads override when configured."""

    solver_key = solver_name.lower()
    if solver_key == "gurobi":
        solver_options["Threads"] = threads
    elif solver_key == "highs":
        solver_options["threads"] = threads

    return solver_options


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


def add_food_group_incentives_to_objective(
    n: pypsa.Network, incentives_paths: list[str]
) -> None:
    """Add food-group incentives/penalties to the objective function.

    Incentives are applied as adjustments to marginal storage costs of
    food group stores. Positive values penalize consumption; negative
    values subsidize consumption.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    incentives_paths : list[str]
        Paths to CSVs with columns: group, country, adjustment_bnusd_per_mt
    """
    if not incentives_paths:
        raise ValueError("food_group_incentives enabled but no sources are configured")

    combined = []
    for path in incentives_paths:
        incentives_df = pd.read_csv(path)
        required = {"group", "country", "adjustment_bnusd_per_mt"}
        missing = required - set(incentives_df.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(
                f"Missing required columns in incentives file {path}: {missing_text}"
            )

        incentives_df["country"] = incentives_df["country"].astype(str).str.upper()
        incentives_df["store_name"] = (
            "store:group:" + incentives_df["group"] + ":" + incentives_df["country"]
        )
        combined.append(incentives_df[["store_name", "adjustment_bnusd_per_mt"]].copy())

    all_incentives = pd.concat(combined, ignore_index=True)
    summed = (
        all_incentives.groupby("store_name")["adjustment_bnusd_per_mt"]
        .sum()
        .reset_index()
    )

    if "marginal_cost_storage" not in n.stores.static.columns:
        n.stores.static["marginal_cost_storage"] = 0.0

    store_index = n.stores.static.index
    missing_stores = summed[~summed["store_name"].isin(store_index)]
    if not missing_stores.empty:
        sample = ", ".join(missing_stores["store_name"].head(5))
        logger.warning(
            "Missing %d food group stores for incentives (examples: %s)",
            len(missing_stores),
            sample,
        )

    applicable = summed[summed["store_name"].isin(store_index)]
    if applicable.empty:
        logger.info(
            "No applicable food group incentives found in %d sources",
            len(incentives_paths),
        )
        return

    n.stores.static.loc[applicable["store_name"], "marginal_cost_storage"] = (
        n.stores.static.loc[applicable["store_name"], "marginal_cost_storage"].astype(
            float
        )
        + applicable["adjustment_bnusd_per_mt"].astype(float).values
    )

    logger.info(
        "Applied food-group incentives to %d stores from %d sources",
        len(applicable),
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


def add_animal_production_constraints(
    n: pypsa.Network,
    fao_production: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
) -> None:
    """Add constraints to fix animal production at FAO levels per country.

    For each (country, product) combination in the FAO data, adds a constraint
    that total production from all feed categories equals the FAO target,
    adjusted for food loss and waste. Since the model applies FLW to the
    feed→product efficiency, the constraint target must also be adjusted
    to net production (gross FAO production * (1-loss) * (1-waste)).

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    fao_production : pd.DataFrame
        FAO production data with columns: country, product, production_mt.
    food_to_group : dict[str, str]
        Mapping from product names to food group names for FLW lookup.
    loss_waste : pd.DataFrame
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction.
    """
    if fao_production.empty:
        logger.warning(
            "No FAO animal production data available; skipping production constraints"
        )
        return

    # Build FLW lookup: (country, food_group) -> (1-loss)*(1-waste)
    flw_multipliers: dict[tuple[str, str], float] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_frac = float(row["loss_fraction"])
        waste_frac = float(row["waste_fraction"])
        flw_multipliers[key] = (1.0 - loss_frac) * (1.0 - waste_frac)

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    # Filter to animal production links using carrier
    # Animal production links have carrier "animal_production"
    prod_mask = links_df["carrier"] == "animal_production"
    prod_links = links_df[prod_mask]

    if prod_links.empty:
        logger.info("No animal production links found.")
        return

    products = prod_links["product"].astype(str)
    countries = prod_links["country"].astype(str)

    # Prepare DataArrays aligned with the filtered links
    link_names = prod_links.index

    # Efficiencies
    efficiencies = xr.DataArray(
        prod_links["efficiency"].values, coords={"name": link_names}, dims="name"
    )

    # Production = p * efficiency
    # Group by (product, country) and sum
    production_vars = link_p.sel(name=link_names)

    grouper = pd.MultiIndex.from_arrays(
        [products.values, countries.values], names=["product", "country"]
    )
    da_grouper = xr.DataArray(grouper, coords={"name": link_names}, dims="name")

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()

    target_series = fao_production.set_index(["product", "country"])[
        "production_mt"
    ].astype(float)

    # Adjust targets by FLW: net_target = gross_target * (1-loss) * (1-waste)
    adjusted_targets = []
    for product, country in target_series.index:
        gross_value = target_series.loc[(product, country)]
        group = food_to_group[product]
        multiplier = flw_multipliers[(country, group)]
        adjusted_targets.append(gross_value * multiplier)
    target_series = pd.Series(adjusted_targets, index=target_series.index)

    model_index = pd.Index(total_production.coords["group"].values, name="group")
    common_index = model_index.intersection(target_series.index)

    if common_index.empty:
        logger.warning(
            "No matching animal production targets found for model structure."
        )
        return

    lhs = total_production.sel(group=common_index)
    rhs = xr.DataArray(
        target_series.loc[common_index].values,
        coords={"group": common_index},
        dims="group",
    )

    constr_name = "animal_production_target"
    m.add_constraints(lhs == rhs, name=f"GlobalConstraint-{constr_name}")

    # Add GlobalConstraints for shadow price tracking
    gc_names = [f"{constr_name}_{prod}_{country}" for prod, country in common_index]
    gc_products = [prod for prod, _country in common_index]
    gc_countries = [country for _prod, country in common_index]
    n.global_constraints.add(
        gc_names,
        sense="==",
        constant=rhs.values,
        type="production_target",
        country=gc_countries,
        product=gc_products,
    )

    logger.info(
        "Added %d country-level animal production constraints (FLW-adjusted)",
        len(common_index),
    )


# Production stability constraints moved to workflow/scripts/solve_model/production_stability.py


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

    # Add food-group incentives to the objective if enabled
    if snakemake.config["food_group_incentives"]["enabled"]:
        incentives_paths = list(snakemake.input.food_group_incentives)
        add_food_group_incentives_to_objective(n, incentives_paths)

    # Create the linopy model
    logger.info("Creating linopy model...")
    n.optimize.create_model(include_objective_constant=False)
    logger.info("Linopy model created.")

    solver_name = snakemake.params.solver
    solver_threads = snakemake.params.solver_threads
    solver_options = _apply_solver_threads_option(
        dict(snakemake.params.solver_options),
        solver_name,
        solver_threads,
    )
    io_api = snakemake.params.io_api
    netcdf_config = snakemake.params.netcdf

    # Configure Gurobi to write detailed logs to the same file
    if solver_name.lower() == "gurobi" and snakemake.log:
        if "LogFile" not in solver_options:
            solver_options["LogFile"] = snakemake.log[0]
        if "LogToConsole" not in solver_options:
            solver_options["LogToConsole"] = 1  # Also print to console

    # Get population from network metadata
    population_map = get_country_population(n)

    # Food group baseline equals (optional)
    per_country_equal: dict[str, dict[str, float]] | None = None
    equal_source = snakemake.config["food_groups"]["equal_by_country_source"]
    if bool(snakemake.params.enforce_baseline) and equal_source:
        raise ValueError(
            "Cannot combine enforce_gdd_baseline with food_groups.equal_by_country_source"
        )
    if bool(snakemake.params.enforce_baseline):
        baseline_df = pd.read_csv(snakemake.input.baseline_diet)
        per_country_equal = _build_food_group_equals_from_baseline(
            baseline_df,
            list(population_map.keys()),
            pd.read_csv(snakemake.input.food_groups)["group"].unique(),
            baseline_age=str(snakemake.params.diet["baseline_age"]),
            reference_year=int(snakemake.params.diet["baseline_reference_year"]),
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

    add_macronutrient_constraints(n, snakemake.params.macronutrients, population_map)
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

    # Add animal production constraints in validation mode
    use_actual_production = bool(
        snakemake.config["validation"]["use_actual_production"]
    )
    if use_actual_production:
        fao_animal_production = pd.read_csv(snakemake.input.animal_production)
        food_groups_df = pd.read_csv(snakemake.input.food_groups)
        food_to_group = food_groups_df.set_index("food")["group"].to_dict()
        food_loss_waste = pd.read_csv(snakemake.input.food_loss_waste)
        add_animal_production_constraints(
            n, fao_animal_production, food_to_group, food_loss_waste
        )

    # Add production stability constraints
    stability_cfg = snakemake.params.production_stability
    if stability_cfg["enabled"]:
        # Load food_to_group if not already loaded
        if "food_to_group" not in dir():
            food_groups_df = pd.read_csv(snakemake.input.food_groups)
            food_to_group = food_groups_df.set_index("food")["group"].to_dict()

        crop_baseline = None
        crop_to_fao_item: dict[str, str] = {}
        animal_baseline = None
        food_loss_waste_df = pd.DataFrame()

        if stability_cfg["crops"]["enabled"]:
            crop_baseline = pd.read_csv(snakemake.input.crop_production_baseline)
            # Load FAO item mapping to aggregate crops sharing an FAO item
            fao_map_df = pd.read_csv(snakemake.input.faostat_crop_item_map)
            crop_to_fao_item = dict(
                zip(
                    fao_map_df["crop"].astype(str),
                    fao_map_df["faostat_item"].astype(str),
                )
            )

        if stability_cfg["animals"]["enabled"]:
            animal_baseline = pd.read_csv(snakemake.input.animal_production_baseline)
            food_loss_waste_df = pd.read_csv(snakemake.input.food_loss_waste)

        slack_marginal_cost = float(
            snakemake.config["validation"]["slack_marginal_cost"]
        )
        add_production_stability_constraints(
            n,
            crop_baseline,
            crop_to_fao_item,
            animal_baseline,
            stability_cfg,
            food_to_group,
            food_loss_waste_df,
            slack_marginal_cost,
        )

    # Add within-group food ratio constraints if enabled
    ratio_cfg = snakemake.params.fix_within_group_ratios
    if ratio_cfg["enabled"]:
        ratios_df = pd.read_csv(snakemake.input.food_group_ratios)
        add_within_group_ratio_constraints(n, ratios_df)

    # Add health impacts if enabled
    health_enabled = bool(snakemake.params.health_enabled)
    if health_enabled:
        add_health_objective(
            n,
            snakemake.input.health_risk_breakpoints,
            snakemake.input.health_cluster_cause,
            snakemake.input.health_cause_log,
            snakemake.input.health_cluster_summary,
            snakemake.input.health_clusters,
            snakemake.params.health_risk_factors,
            snakemake.params.health_risk_cause_map,
            solver_name,
            float(snakemake.params.health_value_per_yll),
        )

    status, condition = n.model.solve(
        solver_name=solver_name,
        io_api=io_api,
        calculate_fixed_duals=snakemake.params.calculate_fixed_duals,
        **solver_options,
    )
    result = (status, condition)

    # Temporary debug export of the raw solved linopy model
    # linopy_debug_path = Path(snakemake.output.network).with_name("linopy_model.nc")
    # linopy_debug_path.parent.mkdir(parents=True, exist_ok=True)
    # n.model.to_netcdf(linopy_debug_path)
    # logger.info("Wrote linopy model snapshot to %s", linopy_debug_path)

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
