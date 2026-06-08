# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import contextlib
import ctypes
import functools
import gc
import logging
from pathlib import Path
import time

from linopy.common import format_single_constraint
import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from workflow.scripts import constants
from workflow.scripts.build_model.utils import _per_capita_mass_to_mt_per_year
from workflow.scripts.population import get_country_population
from workflow.scripts.solve_model.diet_stability import (
    add_diet_stability_constraints,
    evaluate_diet_stability_cost,
)
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
    add_bounded_subsidy_constraints,
    add_crop_growth_cap_constraints,
    add_production_stability_constraints,
    add_reforestation_cap_constraints,
    resolve_calibrated_l1_costs,
)

# Module-level logger (replaced by run_solve's caller)
logger = logging.getLogger(__name__)

_PHASE_TIMES: list[tuple[str, float]] = []


@contextlib.contextmanager
def _phase(label: str):
    """Log wall-clock time for a phase of solve_model.run_solve."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        _PHASE_TIMES.append((label, dt))
        logger.info("[phase] %-46s %7.2f s", label, dt)


def report_phase_timings() -> None:
    """Emit a final summary of all phase timings in order, plus a total."""
    if not _PHASE_TIMES:
        return
    total = sum(dt for _, dt in _PHASE_TIMES)
    logger.info("=" * 70)
    logger.info("PHASE TIMING SUMMARY")
    logger.info("=" * 70)
    for label, dt in _PHASE_TIMES:
        pct = 100 * dt / total if total else 0.0
        logger.info("  %-46s %7.2f s  (%5.1f%%)", label, dt, pct)
    logger.info("  %-46s %7.2f s", "TOTAL (phases sum)", total)


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


def _match_baseline_to_consume_links(
    baseline_df: pd.DataFrame,
    consume_links: pd.DataFrame,
    population: dict[str, float],
    *,
    food_demand_multiplier: dict[str, float] | None = None,
) -> pd.DataFrame | None:
    """Match baseline diet data to food consumption links and compute Mt targets.

    Returns a DataFrame with columns: name, food, food_group, country,
    consumption_g_per_day, target_mt — or None if no links matched.

    The baseline diet on disk is in intake basis (post-FLW), matching the
    GDD-IA / FAOSTAT intake exposures. The food bus and consume link p0
    are on post-loss / pre-waste retail-supply basis: producer-side loss
    is applied at food_processing / animal_production, and consumer-side
    waste is applied on the consume link via the ``flw_multiplier``
    attribute (which carries ``1 - waste_fraction`` per (country,
    food_group)). To pin the consume link's p0 correctly we divide the
    intake-basis Mt target by that multiplier.

    When ``food_demand_multiplier`` is provided it is applied uniformly
    across countries to each food's target_mt. This is the per-food global
    demand calibration (see ``food_demand_calibration`` in config), which
    reconciles the residual mismatch between FAOSTAT QCL-derived supply
    and GDD-IA-derived demand after food_waste calibration.
    """
    df = _prepare_baseline_diet_for_food_constraints(baseline_df, consume_links)

    consume_links_keyed = consume_links.copy()
    consume_links_keyed["key"] = (
        consume_links_keyed["food"] + ":" + consume_links_keyed["country"]
    )
    df["key"] = df["food"] + ":" + df["country"]

    matched = df.merge(
        consume_links_keyed[["key", "flw_multiplier"]].reset_index(),
        on="key",
    )

    if matched.empty:
        logger.warning("No matching food consumption links for baseline diet data")
        return None

    intake_target = np.array(
        [
            _per_capita_mass_to_mt_per_year(g, population[c])
            for g, c in zip(
                matched["consumption_g_per_day"].values, matched["country"].values
            )
        ]
    )
    flw_mult = pd.to_numeric(matched["flw_multiplier"], errors="coerce").fillna(1.0)
    flw_mult = flw_mult.clip(lower=0.01).to_numpy()
    matched["target_mt"] = intake_target / flw_mult

    if food_demand_multiplier is not None:
        food_names = matched["food"].astype(str)
        mapped = food_names.map(food_demand_multiplier)
        missing_foods = sorted(food_names[mapped.isna()].unique())
        if missing_foods:
            # Foods consumed in the network but absent from the
            # food_demand calibration: surfaces stale or incomplete
            # calibration data. Fall back to multiplier=1.0 for these
            # foods but record the divergence so it is not hidden.
            logger.warning(
                "food_demand calibration missing %d food(s); using "
                "multiplier=1.0 for: %s",
                len(missing_foods),
                ", ".join(missing_foods),
            )
        demand_mult = mapped.fillna(1.0).to_numpy()
        matched["target_mt"] = matched["target_mt"] * demand_mult
        n_adjusted = int((demand_mult != 1.0).sum())
        logger.info(
            "Applied food_demand multipliers on %d / %d (food, country) targets",
            n_adjusted,
            len(matched),
        )

    return matched


def add_food_slack_generators(
    n: pypsa.Network,
    matched: pd.DataFrame,
    slack_cost: float,
) -> None:
    """Add bidirectional slack generators on food buses for baseline enforcement.

    Must be called BEFORE ``n.optimize.create_model()`` so that PyPSA includes
    these generators in the linopy model.

    Parameters
    ----------
    n : pypsa.Network
        The network to add slack components to.
    matched : pd.DataFrame
        Output of ``_match_baseline_to_consume_links`` with food, food_group,
        country columns.
    slack_cost : float
        Penalty cost per Mt of slack (billion USD/Mt).
    """
    foods = matched["food"].values
    countries = matched["country"].values
    food_groups = matched["food_group"].values
    food_buses = pd.Index(
        "food:" + pd.Index(foods) + ":" + pd.Index(countries),
        dtype="object",
    )

    n.carriers.add(
        ["slack_positive_food", "slack_negative_food"],
        unit="Mt",
    )

    pos_names = pd.Index(
        "slack:food_positive:" + pd.Index(foods) + ":" + pd.Index(countries),
        dtype="object",
    )
    n.generators.add(
        pos_names,
        bus=food_buses.values,
        carrier="slack_positive_food",
        p_nom_extendable=True,
        marginal_cost=slack_cost,
        food=foods,
        food_group=food_groups,
        country=countries,
    )

    # Negative slack: p in [-p_nom, 0] withdraws from the bus when
    # supply exceeds the fixed baseline target. Objective contribution
    # is marginal_cost * p = (-slack_cost) * (negative p) = +slack_cost
    # * |p|, so withdrawals are correctly penalised at slack_cost per
    # Mt. Verified that n.statistics.opex() and energy_balance() both
    # interpret the sign correctly.
    neg_names = pd.Index(
        "slack:food_negative:" + pd.Index(foods) + ":" + pd.Index(countries),
        dtype="object",
    )
    n.generators.add(
        neg_names,
        bus=food_buses.values,
        carrier="slack_negative_food",
        p_nom_extendable=True,
        p_min_pu=-1.0,
        p_max_pu=0.0,
        marginal_cost=-slack_cost,
        food=foods,
        food_group=food_groups,
        country=countries,
    )

    logger.info(
        "Added %d positive + %d negative food slack generators",
        len(pos_names),
        len(neg_names),
    )


def fix_food_consumption_to_baseline(
    n: pypsa.Network,
    matched: pd.DataFrame,
) -> None:
    """Fix food consumption link dispatch to baseline values via p_set.

    Must be called BEFORE ``n.optimize.create_model()`` so that p_set
    values are included in the constraint definition.

    Also relaxes food-group store caps (``e_nom_max``) to infinity, since
    the baseline diet may slightly exceed per-capita caps that are only
    meaningful when the optimizer freely chooses the diet.

    Consumer value duals are available post-solve via ``n.links.dynamic.mu_p_set``
    (after calling ``_extract_p_set_duals``).
    """
    link_names = matched["name"].values
    targets_mt = matched["target_mt"].values

    new_p_set = pd.DataFrame(
        {link: [target] for link, target in zip(link_names, targets_mt)},
        index=n.snapshots,
    )
    existing = n.links.dynamic.get("p_set", pd.DataFrame(index=n.snapshots))
    if not existing.empty:
        # Drop any pre-existing p_set entries for the links we are about to
        # fix; otherwise pd.concat([existing, new_p_set], axis=1) would
        # produce duplicate column names and PyPSA's downstream Link-p_set
        # constraint would pick an arbitrary one.
        existing = existing.drop(columns=list(link_names), errors="ignore")
        new_p_set = pd.concat([existing, new_p_set], axis=1)
    n.links.dynamic["p_set"] = new_p_set

    # Relax food-group store caps: with consumption fixed exactly at baseline,
    # even tiny mismatches between baseline totals and per-capita caps
    # would cause hard infeasibility. The caps only constrain free-diet
    # optimization, not baseline validation.
    stores = n.stores.static
    group_stores = stores.index[stores["carrier"].str.startswith("group_", na=False)]
    if not group_stores.empty:
        n.stores.static.loc[group_stores, "e_nom_max"] = np.inf

    logger.info(
        "Fixed %d food consumption links to baseline via p_set",
        len(link_names),
    )


def _extract_p_set_duals(n: pypsa.Network) -> None:
    """Write Link p_set duals to n.links.dynamic.mu_p_set.

    Must be called after solving and ``assign_duals`` to populate the
    consumer value shadow prices used by ``extract_consumer_values``.
    """
    constraints = dict(n.model.constraints.items())
    if "Link-p_set" not in constraints:
        return
    dual_df = constraints["Link-p_set"].dual.to_pandas()
    n.links.dynamic["mu_p_set"] = dual_df


def _prepare_baseline_diet_for_food_constraints(
    baseline_df: pd.DataFrame,
    consume_links: pd.DataFrame,
    *,
    min_consumption_g_per_day: float = 0.1,
) -> pd.DataFrame:
    """Prepare baseline diet data for food constraints and ratio derivation.

    Uses the same filtering and clipping logic as per-food equality constraints
    so ratio-based runs are consistent with baseline-equality runs.

    On-disk column ``consumption_g_per_day_intake`` is renamed to
    ``consumption_g_per_day`` for internal use; the on-disk suffix
    documents that the value is post-loss, post-waste consumer-eaten mass
    (the same basis the food bus delivers after the FLW multiplier).
    """
    df = baseline_df.copy()
    if "consumption_g_per_day_intake" in df.columns:
        df = df.rename(
            columns={"consumption_g_per_day_intake": "consumption_g_per_day"}
        )
    df["country"] = df["country"].astype(str).str.upper()
    df["food"] = df["food"].astype(str)
    df["consumption_g_per_day"] = pd.to_numeric(
        df["consumption_g_per_day"], errors="coerce"
    ).fillna(0.0)

    # Negative consumption is unphysical (a data-quality artefact of upstream
    # FBS-supply override subtraction); clamp to zero before deciding whether
    # to lift to the floor. A negative p_set on consume links would make the
    # baseline-equality formulation infeasible.
    negative_mask = df["consumption_g_per_day"] < 0
    if negative_mask.any():
        worst = df.loc[negative_mask].nsmallest(3, "consumption_g_per_day")
        logger.warning(
            "Clamping %d negative baseline consumption rows to zero (worst: %s)",
            int(negative_mask.sum()),
            worst.to_dict("records"),
        )
        df.loc[negative_mask, "consumption_g_per_day"] = 0.0

    model_keys = set(zip(consume_links["food"], consume_links["country"]))
    df = df[df.apply(lambda r: (r["food"], r["country"]) in model_keys, axis=1)].copy()

    # Lift only tiny-but-positive consumptions to a minimum floor so that
    # downstream ratio derivation does not divide by near-zero values. True
    # zeros mean the food is not eaten in the country at all; preserve them so
    # baseline-equality and diet-stability runs do not inject artificial
    # demand.
    positive = df["consumption_g_per_day"] > 0
    df.loc[positive, "consumption_g_per_day"] = df.loc[
        positive, "consumption_g_per_day"
    ].clip(lower=min_consumption_g_per_day)
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

    # Apply once on the GHG aggregator store. The per-gas buses
    # (emission:co2, emission:ch4, emission:n2o) carry no stores; they
    # only feed the aggregator links built in primary_resources.py whose
    # efficiencies bake in the GWP factors. Pricing here is therefore
    # the single point of GHG monetisation in the objective.
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
    max_feed_fraction_by_region: dict,
    countries: list[str],
    m49_path: str,
) -> dict[str, float]:
    """Build per-country residue feed fraction overrides from config.

    Parameters
    ----------
    max_feed_fraction_by_region : dict
        Region/subregion/country-specific residue feed fraction overrides.
    countries : list[str]
        ISO-alpha3 country codes being modeled.
    m49_path : str
        Path to M49 codes CSV.
    """
    overrides = max_feed_fraction_by_region
    if not overrides:
        return {}

    countries = [str(country).upper() for country in countries]

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

    # Find residue feed links: feed_conversion links whose input bus is a
    # residue bus (carrier residue_*). Resolved via bus0.map(buses.carrier)
    # rather than parsing the bus name, so the convention can change in one
    # place if needed.
    bus_carrier = n.buses.static["carrier"]
    feed_mask = (links_df["carrier"] == "feed_conversion") & (
        links_df["bus0"].map(bus_carrier).str.startswith("residue_", na=False)
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


def _apply_health_pricing(n: pypsa.Network, value_per_yll_usd: float) -> None:
    """Set marginal_cost_storage on health (YLL) stores for this scenario."""
    cost_per_myll = (
        value_per_yll_usd * constants.USD_TO_BNUSD / constants.YLL_TO_MILLION_YLL
    )
    yll_mask = n.stores.static["carrier"].str.startswith("yll_")
    if yll_mask.any():
        n.stores.static.loc[yll_mask, "marginal_cost_storage"] = cost_per_myll


def _apply_regional_limit_scaling(n: pypsa.Network, solve_limit: float) -> None:
    """Rescale land supply generator capacities if scenario regional_limit differs."""
    build_limit = n.meta["land_regional_limit"]
    if abs(solve_limit - build_limit) < 1e-12:
        return

    ratio = solve_limit / build_limit

    # Scale all land supply generators (existing + new cropland and grassland)
    land_carriers = {
        "land_existing",
        "land_new",
        "land_existing_grassland",
        "land_existing_grassland_convertible",
        "land_existing_grassland_marginal",
    }
    mask = n.generators.static["carrier"].isin(land_carriers)
    if mask.any():
        n.generators.static.loc[mask, "p_nom"] *= ratio
        logger.info(
            "Scaled %d land generators p_nom by %.4f (regional_limit %.3f → %.3f)",
            mask.sum(),
            ratio,
            build_limit,
            solve_limit,
        )


def _apply_biofuel_demand_scaling(n: pypsa.Network, scale: float) -> None:
    """Scale fixed biofuel demand links at solve time."""
    biofuel_mask = n.links.static["carrier"] == "biofuel"
    if not biofuel_mask.any():
        return

    n.links.static.loc[biofuel_mask, "p_nom"] *= scale
    logger.info(
        "Scaled %d biofuel links by %.4f",
        biofuel_mask.sum(),
        scale,
    )


def _exogenous_feed_marginal_cost(n: pypsa.Network) -> float:
    """Marginal cost (bnUSD per Mt DM) for landless exogenous feed generators.

    Anchored to the model's own grassland grazing cost per tonne DM: the
    median of ``marginal_cost / efficiency`` across grassland_production
    links (which itself derives from USDA ERS / EU FADN grazed-feed costs;
    see docs/costs.rst). Pricing the landless exogenous backstop at the cost
    of obtaining the equivalent forage by grazing removes the artificial
    preference for exogenous feed over endogenous grassland, fodder, and
    crop-residue feed. Must be evaluated before forage calibration scales
    grassland efficiency. Returns 0.0 when no grassland links exist.
    """
    grass = n.links.static[n.links.static["carrier"] == "grassland_production"]
    if grass.empty:
        return 0.0
    eff = grass["efficiency"].astype(float)
    per_mt = (grass["marginal_cost"].astype(float) / eff.where(eff > 0)).dropna()
    if per_mt.empty:
        return 0.0
    return float(per_mt.median())


def _apply_forage_calibration(
    n: pypsa.Network,
    smk,
    forage_overlap_crops: list[str],
    enforce_baseline_feed: bool,
    exogenous_marginal_cost: float,
) -> None:
    """Apply grassland forage calibration corrections at solve time.

    Reads three calibration CSVs from snakemake inputs:
    - grassland_yield_correction: per-country yield multipliers for grassland links
    - fodder_conversion_correction: per-country efficiency multipliers for forage crop links
    - exogenous_forage: per-country exogenous forage supply (Mt DM) for deficit countries
    """
    read_csv = functools.partial(pd.read_csv, comment="#")

    # 1. Grassland yield correction
    yield_cal = read_csv(smk.input.grassland_yield_correction)
    grass_links = n.links.static[n.links.static["carrier"] == "grassland_production"]
    if not grass_links.empty and not yield_cal.empty:
        cal_map = yield_cal.set_index("country")["yield_correction"]
        corrections = grass_links["country"].map(cal_map).fillna(1.0).to_numpy()
        n.links.static.loc[grass_links.index, "efficiency"] *= corrections
        n_adjusted = int((corrections < 1.0).sum())
        logger.info(
            "Applied grassland yield corrections: %d/%d links adjusted",
            n_adjusted,
            len(grass_links),
        )

    # 2. Fodder-to-forage conversion correction
    fodder_cal = read_csv(smk.input.fodder_conversion_correction)
    fc_links = n.links.static[
        (n.links.static["carrier"] == "feed_conversion")
        & (n.links.static["feed_category"] == "ruminant_forage")
        & (n.links.static["crop"].isin(forage_overlap_crops))
    ]
    if not fc_links.empty:
        cal_map = fodder_cal.set_index("country")["fodder_conversion_correction"]
        corrections = fc_links["country"].map(cal_map).fillna(1.0).to_numpy()
        n.links.static.loc[fc_links.index, "efficiency"] *= corrections
        n_adjusted = int((corrections < 1.0).sum())
        logger.info(
            "Applied fodder conversion corrections: %d/%d links adjusted",
            n_adjusted,
            len(fc_links),
        )

    # 3. Exogenous forage generators for deficit countries
    exo_cal = read_csv(smk.input.exogenous_forage)
    exog = exo_cal[exo_cal["exogenous_forage_mt_dm"] > 0].copy()
    if not exog.empty:
        exog_buses = "feed:ruminant_forage:" + exog["country"]
        bus_exists = exog_buses.isin(n.buses.static.index)
        exog = exog[bus_exists.values]
        exog_buses = exog_buses[bus_exists.values]
        if not exog.empty:
            if "exogenous_forage_cal" not in n.carriers.static.index:
                n.carriers.add("exogenous_forage_cal", unit="Mt")
            gen_names = pd.Index(
                "supply:exogenous_forage:" + exog["country"].values,
                dtype="object",
            )
            if enforce_baseline_feed:
                n.generators.add(
                    gen_names,
                    bus=exog_buses.values,
                    carrier="exogenous_forage_cal",
                    p_nom=exog["exogenous_forage_mt_dm"].values,
                    p_nom_extendable=False,
                    p_min_pu=1.0,
                    p_max_pu=1.0,
                    country=exog["country"].values,
                )
            else:
                n.generators.add(
                    gen_names,
                    bus=exog_buses.values,
                    carrier="exogenous_forage_cal",
                    p_nom_extendable=True,
                    p_nom_max=exog["exogenous_forage_mt_dm"].values,
                    marginal_cost=exogenous_marginal_cost,
                    country=exog["country"].values,
                )
            logger.info(
                "Added %d exogenous forage generators (%.1f Mt DM total)",
                len(gen_names),
                exog["exogenous_forage_mt_dm"].sum(),
            )


def _apply_exogenous_feed_calibration(
    n: pypsa.Network,
    smk,
    enforce_baseline_feed: bool,
    exogenous_marginal_cost: float,
) -> None:
    """Inject per-country exogenous protein and roughage supply.

    Reads ``smk.input.exogenous_feed`` and adds generators on the matching
    ``feed:{monogastric_protein,ruminant_protein,ruminant_roughage}:{country}``
    buses up to the listed ceiling, priced at ``exogenous_marginal_cost``
    (roughage carries the ``exogenous_roughage_cal`` carrier; protein meals
    ``exogenous_protein_cal``). In validation/enforce_baseline_feed mode the
    generators are forced to dispatch at the listed amount.
    """
    read_csv = functools.partial(pd.read_csv, comment="#")
    df = read_csv(smk.input.exogenous_feed)

    # Roughage gets its own carrier so analysis can distinguish exogenous
    # protein meals from exogenous (unaccounted) roughage.
    categories = ("monogastric_protein", "ruminant_protein", "ruminant_roughage")
    for category in categories:
        col = f"{category}_mt_dm"
        if col not in df.columns:
            continue
        exog = df[["country", col]].copy()
        exog = exog[exog[col] > 0]
        if exog.empty:
            continue
        exog_buses = "feed:" + category + ":" + exog["country"]
        bus_exists = exog_buses.isin(n.buses.static.index)
        exog = exog[bus_exists.values]
        exog_buses = exog_buses[bus_exists.values]
        if exog.empty:
            continue

        carrier = (
            "exogenous_roughage_cal"
            if category == "ruminant_roughage"
            else "exogenous_protein_cal"
        )
        if carrier not in n.carriers.static.index:
            n.carriers.add(carrier, unit="Mt")

        # Use a "_cal" infix so these calibration generators never collide
        # with the build-time GLEAM exogenous_feed generators, which already
        # occupy supply:exogenous_{category}:{country} (e.g. browse on the
        # ruminant_roughage bus). A silent name collision would drop the
        # backstop and leave the bus short.
        gen_names = pd.Index(
            "supply:exogenous_cal_" + category + ":" + exog["country"].values,
            dtype="object",
        )
        if enforce_baseline_feed:
            n.generators.add(
                gen_names,
                bus=exog_buses.values,
                carrier=carrier,
                p_nom=exog[col].values,
                p_nom_extendable=False,
                p_min_pu=1.0,
                p_max_pu=1.0,
                country=exog["country"].values,
                feed_category=category,
            )
        else:
            n.generators.add(
                gen_names,
                bus=exog_buses.values,
                carrier=carrier,
                p_nom_extendable=True,
                p_nom_max=exog[col].values,
                marginal_cost=exogenous_marginal_cost,
                country=exog["country"].values,
                feed_category=category,
            )
        logger.info(
            "Added %d exogenous %s generators (%.1f Mt DM total)",
            len(gen_names),
            category,
            exog[col].sum(),
        )


def run_solve(
    smk,
    _logger,
    *,
    skip_post_processing: bool = False,
    skip_assign_duals: bool = False,
    accept_time_limit: bool = False,
) -> pypsa.Network | None:
    """Core solve logic returning the solved network.

    Parameters
    ----------
    smk
        Snakemake object providing inputs, params, config, wildcards, and log.
    _logger
        Logger instance (already configured by the caller).
    skip_post_processing
        If True, do not call ``n.optimize.post_processing()``.  Callers that
        only need ``n.links.dynamic.p0`` etc. (set by ``assign_solution``) can
        opt out of the PyPSA bus-injection recompute, which is dominated by
        per-link-port ``DataFrame.rename`` calls and is the single largest
        cost after the solver itself for the GLADE model.
    skip_assign_duals
        If True, do not call ``n.optimize.assign_duals(...)``.  This is safe
        whenever the caller does not need the generic dual assignment on
        non-Link components.  ``_extract_p_set_duals`` still runs and reads
        Link ``p_set`` duals directly from ``n.model.constraints``.
    accept_time_limit
        If True, treat ``time_limit`` termination with a feasible incumbent
        as a successful solve and proceed with solution assignment. Use for
        calibration loops where a near-optimal incumbent is acceptable.

    Returns
    -------
    pypsa.Network or None
        The solved network with solution assigned, or ``None`` when the
        solve fails (time-limit, infeasible, or other solver error).
    """
    global logger
    logger = _logger
    _PHASE_TIMES.clear()

    with _phase("pypsa.Network(load_netcdf)"):
        n = pypsa.Network(smk.input.network)

    # Apply sensitivity adjustments (moved from build_model to allow shared builds)
    sensitivity_cfg = smk.params.sensitivity
    if sensitivity_cfg:
        from workflow.scripts.solve_model.sensitivity import apply_sensitivity_factors

        with _phase("apply_sensitivity_factors"):
            logger.info("Applying sensitivity adjustments...")
            apply_sensitivity_factors(n, sensitivity_cfg)

    # Price the landless exogenous feed backstops at the grassland grazing
    # cost (evaluated before forage calibration scales grassland efficiency)
    # so they no longer undercut endogenous grassland/fodder/residue feed.
    exo_feed_cost = _exogenous_feed_marginal_cost(n)

    # Apply grassland forage calibration if enabled for this scenario
    if smk.params.forage_calibration_enabled:
        logger.info("Applying grassland forage calibration...")
        _apply_forage_calibration(
            n,
            smk,
            forage_overlap_crops=smk.params.forage_overlap_crops,
            enforce_baseline_feed=smk.params.enforce_baseline_feed,
            exogenous_marginal_cost=exo_feed_cost,
        )

    # Apply protein-feed calibration (exogenous mono/ruminant protein
    # supply) if enabled for this scenario.
    if smk.params.exogenous_feed_calibration_enabled:
        logger.info("Applying protein-feed calibration...")
        _apply_exogenous_feed_calibration(
            n,
            smk,
            enforce_baseline_feed=smk.params.enforce_baseline_feed,
            exogenous_marginal_cost=exo_feed_cost,
        )

    # Rescale land supply generators if scenario regional_limit differs from build
    _apply_regional_limit_scaling(n, smk.params.regional_limit)
    _apply_biofuel_demand_scaling(n, float(smk.params.biofuel_demand_scale))

    # Add GHG pricing to the objective if enabled
    if smk.params.ghg_pricing_enabled:
        ghg_price = float(smk.params.ghg_price)
        add_ghg_pricing_to_objective(n, ghg_price)

    # Update health store marginal costs to match scenario value_per_yll.
    # The build uses the base config value; scenarios may override it.
    _apply_health_pricing(n, float(smk.params.health_value_per_yll))

    incentives_enabled = bool(smk.params.food_incentives_enabled)
    piecewise_utility_cfg = smk.params.food_utility_piecewise
    piecewise_utility_enabled = bool(piecewise_utility_cfg["enabled"])

    if incentives_enabled and piecewise_utility_enabled:
        raise ValueError(
            "food_incentives and food_utility_piecewise cannot both be enabled"
        )

    # Add food-level linear incentives to marginal costs if enabled
    if incentives_enabled:
        incentives_paths = list(smk.input.food_incentives)
        add_food_incentives_to_objective(n, incentives_paths)

    # Get population from network metadata
    population_map = get_country_population(n)

    # Load baseline diet data (used for food-level enforcement and/or ratio constraints)
    baseline_df = pd.read_csv(smk.input.baseline_diet)
    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
    prepared_baseline_df = _prepare_baseline_diet_for_food_constraints(
        baseline_df,
        consume_links,
    )

    # Food-level baseline enforcement: add food slack generators and fix
    # consumption links to baseline via p_set. Both must happen BEFORE
    # create_model() so PyPSA includes them in the linopy model.
    per_country_equal: dict[str, dict[str, float]] | None = None
    equal_source = smk.params.equal_by_country_source
    enforce_baseline = bool(smk.params.enforce_baseline)
    if enforce_baseline and equal_source:
        raise ValueError(
            "Cannot combine enforce_baseline_diet with food_groups.equal_by_country_source"
        )
    matched_baseline: pd.DataFrame | None = None
    dp_cfg = smk.params.deviation_penalty
    diet_enabled = dp_cfg["enabled"] and dp_cfg["diet"]["enabled"]
    needs_baseline_match = enforce_baseline or diet_enabled
    if needs_baseline_match:
        food_demand_multiplier: dict[str, float] | None = None
        fd_cal_path = getattr(smk.input, "food_demand_calibration", None)
        if fd_cal_path:
            fd_cal = pd.read_csv(fd_cal_path)
            food_demand_multiplier = dict(
                zip(fd_cal["food"].astype(str), fd_cal["multiplier"].astype(float))
            )
            logger.info(
                "Loaded food_demand calibration: %d foods", len(food_demand_multiplier)
            )
        matched_baseline = _match_baseline_to_consume_links(
            prepared_baseline_df,
            consume_links,
            population_map,
            food_demand_multiplier=food_demand_multiplier,
        )
        # Stamp the per-link Mt baseline onto food_consumption links so
        # extract_baseline_deviation can compute a diet-deviation row
        # without re-running the (population, FLW) matching.
        if matched_baseline is not None and not matched_baseline.empty:
            targets = matched_baseline.set_index("name")["target_mt"]
            n.links.static.loc[targets.index, "baseline_consumption_mt"] = (
                targets.astype(float)
            )
    if enforce_baseline and matched_baseline is not None:
        slack_cost = float(smk.params.slack_marginal_cost)
        add_food_slack_generators(n, matched_baseline, slack_cost)
        fix_food_consumption_to_baseline(n, matched_baseline)

    # Create the linopy model
    with _phase("pypsa.optimize.create_model"):
        logger.info("Creating linopy model...")
        n.optimize.create_model(include_objective_constant=False)
        logger.info("Linopy model created.")

    if piecewise_utility_enabled:
        if enforce_baseline:
            raise ValueError(
                "food_utility_piecewise cannot be combined with "
                "validation.enforce_baseline_diet=true"
            )
        with _phase("add_piecewise_food_utility"):
            add_piecewise_food_utility(
                n,
                smk.input.food_utility_piecewise,
                float(piecewise_utility_cfg["min_block_width_mt"]),
            )

    solver_name = smk.params.solver
    solver_options = dict(smk.params.solver_options)
    io_api = smk.params.io_api

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

        if smk.log and "LogToConsole" not in solver_options:
            solver_options["LogToConsole"] = 0
        if smk.log and "LogFile" not in solver_options:
            solver_options["LogFile"] = str(smk.log[0])

    if not enforce_baseline and equal_source:
        equal_df = pd.read_csv(smk.input.food_group_equal)
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

    macronutrient_cfg = smk.params.macronutrients or {}
    baseline_by_nutrient: dict[str, dict[str, float]] | None = None
    needs_baseline = any(
        isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        for bounds in macronutrient_cfg.values()
    )
    if needs_baseline:
        nutrition_df = pd.read_csv(smk.input.nutrition)
        baseline_by_nutrient = {
            nutrient: _compute_baseline_macronutrient_by_country(
                prepared_baseline_df, nutrition_df, nutrient
            )
            for nutrient, bounds in macronutrient_cfg.items()
            if isinstance(bounds, dict) and bounds.get("equal_to_baseline")
        }

    with _phase("add_macronutrient_constraints"):
        add_macronutrient_constraints(
            n, macronutrient_cfg, population_map, baseline_by_nutrient
        )
    with _phase("add_food_group_constraints"):
        add_food_group_constraints(
            n,
            smk.params.food_group_constraints,
            population_map,
            per_country_equal,
        )

    # Add residue feed limit constraints
    max_feed_fraction = float(smk.params.residue_max_feed_fraction)
    max_feed_fraction_by_country = build_residue_feed_fraction_by_country(
        smk.params.residue_max_feed_fraction_by_region,
        smk.params.countries,
        smk.input.m49,
    )
    with _phase("add_residue_feed_constraints"):
        add_residue_feed_constraints(n, max_feed_fraction, max_feed_fraction_by_country)

    # Deviation penalty constraints (land + feed via production_stability;
    # diet via diet_stability). Resolve the "calibrated" sentinel once;
    # downstream objective-breakdown bookkeeping is also gated on `enabled`,
    # so callers can disable the feature without needing a calibration file.
    if dp_cfg["enabled"]:
        calibrated_yaml = getattr(smk.input, "deviation_penalty_calibration", None)
        dp_cfg = resolve_calibrated_l1_costs(dp_cfg, calibrated_yaml)
        slack_marginal_cost = float(smk.params.slack_marginal_cost)
        with _phase("add_production_stability_constraints"):
            add_production_stability_constraints(n, dp_cfg, slack_marginal_cost)
        # Diet penalty: per-(food, country) L1 (or quadratic) penalty anchoring
        # consumption to the baseline diet. Only applies when diet.enabled is
        # true and the diet is not already pinned via enforce_baseline_diet.
        if dp_cfg["diet"]["enabled"] and not enforce_baseline:
            with _phase("add_diet_stability_constraints"):
                add_diet_stability_constraints(n, matched_baseline, dp_cfg)

    # Add animal growth cap constraints (independent of production stability)
    animal_growth_cap_cfg = smk.params.animal_growth_cap
    with _phase("add_animal_growth_cap_constraints"):
        add_animal_growth_cap_constraints(n, animal_growth_cap_cfg)

    # Add crop growth cap constraints (independent of production stability)
    crop_growth_cap_cfg = smk.params.crop_growth_cap
    with _phase("add_crop_growth_cap_constraints"):
        add_crop_growth_cap_constraints(n, crop_growth_cap_cfg)

    # Per-country reforestation cap: bounds total spared land (cropland +
    # pasture) at a fraction of each country's reforestable agricultural
    # area. Driven by the sensitivity parameter; absent or >= 1.0 is a no-op.
    reforest_fraction = float(
        (smk.params.sensitivity or {}).get("max_reforestation_fraction", 1.0)
    )
    reforest_buffer_mha = float(
        (smk.params.sensitivity or {}).get("reforestation_cap_buffer_mha", 0.0)
    )
    with _phase("add_reforestation_cap_constraints"):
        add_reforestation_cap_constraints(n, reforest_fraction, reforest_buffer_mha)

    # Apply negative cost-calibration corrections only up to baseline (two-tier).
    # Positive corrections are already applied additively at build time;
    # negative corrections were stored on links as ``bounded_subsidy_*``
    # attributes and are activated here.
    with _phase("add_bounded_subsidy_constraints"):
        add_bounded_subsidy_constraints(n)

    # Add within-group food ratio constraints if enabled (separate from baseline enforcement)
    ratio_cfg = smk.params.fix_within_group_ratios
    if ratio_cfg["enabled"] and not enforce_baseline:
        ratios_df = _build_ratios_from_baseline(prepared_baseline_df)
        with _phase("add_within_group_ratio_constraints"):
            add_within_group_ratio_constraints(n, ratios_df)
    elif ratio_cfg["enabled"] and enforce_baseline:
        logger.info(
            "Skipping fix_within_group_ratios: redundant when enforce_baseline_diet=true"
        )

    # Add health impacts if enabled
    health_enabled = bool(smk.params.health_enabled)
    value_per_yll = float(smk.params.health_value_per_yll)
    if health_enabled and value_per_yll > 0:
        # Extract per-risk-factor RR quantiles from sensitivity config
        sensitivity_cfg = smk.params.sensitivity or {}
        rr_quantiles = sensitivity_cfg.get("health_relative_risk") or None

        with _phase("add_health_objective"):
            add_health_objective(
                n,
                smk.input.health_risk_breakpoints,
                smk.input.health_cluster_cause,
                smk.input.health_cause_log,
                smk.input.health_cluster_summary,
                smk.input.health_clusters,
                smk.params.health_risk_factors,
                smk.params.health_risk_cause_map,
                value_per_yll,
                smk.input.health_cluster_risk_baseline,
                rr_quantiles=rr_quantiles,
                tmrel_path=smk.input.health_tmrel,
            )

    # Export fully-constructed model to MPS for Gurobi parameter tuning
    if smk.params.export_for_tuning and hasattr(smk.output, "network"):
        output_path = Path(smk.output.network).with_suffix(".mps")
        logger.info("Exporting model to %s for tuning...", output_path)
        gp_model = n.model.to_gurobipy(env=gurobi_env)
        gp_model.update()
        gp_model.write(str(output_path))
        del gp_model
        gc.collect()
        logger.info("Model exported. Run tuning with:")
        logger.info("  pixi run -e gurobi python tools/tune_model.py %s", output_path)

    with _phase("model.solve (to_solver + solver run)"):
        status, condition = n.model.solve(
            solver_name=solver_name,
            io_api=io_api,
            env=gurobi_env,
            calculate_fixed_duals=smk.params.calculate_fixed_duals,
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

    if condition == "time_limit" and not (accept_time_limit and status == "ok"):
        logger.warning("Solver hit time limit -- treating as failed solve.")
        return None
    if condition == "time_limit" and accept_time_limit and status == "ok":
        logger.warning(
            "Solver hit time limit but returned a feasible incumbent; "
            "proceeding with solution assignment (accept_time_limit=True)."
        )

    if status == "ok":
        aux_names = HEALTH_AUX_MAP.pop(id(n.model), set())
        variables_container = n.model.variables
        removed = {}
        for name in aux_names:
            if name in variables_container.data:
                removed[name] = variables_container.data.pop(name)

        try:
            with _phase("assign_solution"):
                n.optimize.assign_solution()
            if not skip_assign_duals:
                with _phase("assign_duals"):
                    n.optimize.assign_duals(False)
            with _phase("_extract_p_set_duals"):
                _extract_p_set_duals(n)
            if not skip_post_processing:
                with _phase("post_processing"):
                    n.optimize.post_processing()
        finally:
            if removed:
                variables_container.data.update(removed)

        piecewise_utility_value = pop_piecewise_food_utility_value(n)
        if abs(piecewise_utility_value) > 1e-12:
            n.meta["food_utility_cost"] = -piecewise_utility_value

        # Extract production stability slack values if present. Both lower
        # ("_slack") and upper ("_slack_upper") variables are recorded;
        # they are added symmetrically when hard mode enables slack.
        production_slack = {}
        for label in ("crop", "grassland", "animal"):
            lo_name = f"{label}_production_slack"
            hi_name = f"{label}_production_slack_upper"
            sols = {}
            if lo_name in n.model.variables:
                sols["lower"] = n.model.variables[lo_name].solution
            if hi_name in n.model.variables:
                sols["upper"] = n.model.variables[hi_name].solution
            if not sols:
                continue
            production_slack[label] = {
                side: {str(k): v for k, v in sol.to_series().to_dict().items()}
                for side, sol in sols.items()
            }
            total = sum(float(sol.sum()) for sol in sols.values())
            if total > 1e-6:
                logger.info(
                    "%s production slack used: %.4f Mt total (lower+upper)",
                    label.capitalize(),
                    total,
                )
        if production_slack:
            n.meta["production_stability_slack"] = production_slack

        # Store production stability penalty cost for objective breakdown.
        # L1/quadratic penalties are linopy-level terms not visible to PyPSA
        # statistics; record them in metadata so the breakdown can account
        # for them.
        #
        # Gate on dp_cfg["enabled"]: when disabled there are no deviation
        # variables in the model, the sentinel "calibrated" may remain
        # unresolved in the config, and the float() conversions below would
        # crash. The breakdown is also a no-op in that case.
        #
        # The L1 coefficient mirrors what _add_animal_l1_penalty applies in
        # production_stability.py: when feed.l1_cost is set, the penalty
        # uses that value directly (animal_scale=1.0) and abs_dev is in
        # native Mt DM; when null, the penalty uses the cropland l1_cost on
        # Mha-equivalent units. In both cases the per-component coefficient
        # times sum(abs_dev.solution) reproduces the actual objective term.
        if dp_cfg["enabled"]:
            penalty_mode = dp_cfg.get("penalty_mode")
            stability_cost = 0.0
            if penalty_mode == "l1":
                crop_l1 = float(dp_cfg["land"]["crops"]["l1_cost"])
                grassland_l1 = float(dp_cfg["land"]["grassland"]["l1_cost"])
                feed_l1_override = dp_cfg["feed"]["l1_cost"]
                animal_l1 = (
                    float(feed_l1_override) if feed_l1_override is not None else crop_l1
                )
                for var_name, cost in [
                    ("crop_stability_abs_dev", crop_l1),
                    ("grassland_stability_abs_dev", grassland_l1),
                    ("animal_stability_abs_dev", animal_l1),
                    ("land_conversion_stability_abs_dev", crop_l1),
                ]:
                    if var_name in n.model.variables:
                        sol = n.model.variables[var_name].solution
                        stability_cost += cost * float(sol.sum())
            quad_var_names = [
                "crop_stability_dev",
                "grassland_stability_dev",
                "animal_stability_dev",
                "land_conversion_stability_dev",
            ]
            if any(name in n.model.variables for name in quad_var_names):
                quad_cost = float(dp_cfg["quadratic_cost"])
                for var_name in quad_var_names:
                    if var_name in n.model.variables:
                        sol = n.model.variables[var_name].solution
                        stability_cost += 0.5 * quad_cost * float((sol * sol).sum())
            if abs(stability_cost) > 1e-12:
                n.meta["production_stability_cost"] = stability_cost

            # Diet deviation post-hoc cost (separate so the objective
            # breakdown can show production and diet as distinct terms).
            diet_cost = evaluate_diet_stability_cost(n, matched_baseline, dp_cfg)
            if abs(diet_cost) > 1e-12:
                n.meta["diet_stability_cost"] = diet_cost

        # Post-hoc health evaluation when value_per_yll == 0
        if health_enabled and value_per_yll == 0:
            sensitivity_cfg = smk.params.sensitivity or {}
            rr_quantiles = sensitivity_cfg.get("health_relative_risk") or None
            evaluate_health_posthoc(
                n,
                risk_breakpoints_path=smk.input.health_risk_breakpoints,
                cluster_cause_path=smk.input.health_cluster_cause,
                cause_log_path=smk.input.health_cause_log,
                clusters_path=smk.input.health_clusters,
                cluster_risk_baseline_path=smk.input.health_cluster_risk_baseline,
                risk_factors=smk.params.health_risk_factors,
                risk_cause_map=smk.params.health_risk_cause_map,
                rr_quantiles=rr_quantiles,
                tmrel_path=smk.input.health_tmrel,
            )

        # Free the linopy model; all values have been assigned.
        n._model = None
        gc.collect()
        with contextlib.suppress(OSError):
            ctypes.CDLL("libc.so.6").malloc_trim(0)

        report_phase_timings()
        return n
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
                            detail = format_single_constraint(n.model, label)
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

    return None
