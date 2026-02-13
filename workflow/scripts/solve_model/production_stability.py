# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production stability constraints and penalties for the food systems model.

This module provides constraint builders that limit production deviation from
baseline levels. Three penalty modes are supported:

- **hard**: Inequality bounds constraining production to within ±delta of baseline
- **l1**: Linear absolute-value penalty via linopy variables added to the objective
- **quadratic**: Quadratic penalty via linopy variables added to the objective

Crop stability operates at the **per-link** level: each crop production link is
individually constrained to stay near its own baseline (GAEZ harvested area x
yield, computed during model building and stored as ``baseline_production_mt``).

Animal stability operates at the **(product, country)** aggregate level.

Only links/groups with positive baselines are constrained. Links without GAEZ
data (zero baseline) are left unconstrained, avoiding the zero-forcing bug.
"""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)


def _aggregate_animal_baseline(
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
) -> pd.Series:
    """Aggregate animal baseline by (product, country) with FLW adjustment.

    Parameters
    ----------
    animal_baseline
        FAO animal production with columns: country, product, production_mt.
    food_to_group
        Mapping from product names to food group names for FLW lookup.
    loss_waste
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction.

    Returns
    -------
    pd.Series
        FLW-adjusted baseline production in Mt indexed by (product, country).
    """
    # Build FLW lookup
    flw_multipliers: dict[tuple[str, str], float] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_frac = float(row["loss_fraction"])
        waste_frac = float(row["waste_fraction"])
        flw_multipliers[key] = (1.0 - loss_frac) * (1.0 - waste_frac)

    target_series = animal_baseline.set_index(["product", "country"])[
        "production_mt"
    ].astype(float)

    # Adjust by FLW
    adjusted_targets = []
    for product, country in target_series.index:
        gross_value = target_series.loc[(product, country)]
        group = food_to_group.get(product, product)
        multiplier = flw_multipliers.get((country, group), 1.0)
        adjusted_targets.append(gross_value * multiplier)

    return pd.Series(adjusted_targets, index=target_series.index)


def add_production_stability_constraints(
    n: pypsa.Network,
    animal_baseline: pd.DataFrame | None,
    stability_cfg: dict,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    slack_marginal_cost: float,
) -> None:
    """Add constraints limiting production deviation from baseline levels.

    For crops: per-link bounds using ``baseline_production_mt`` set during build.
    For animals: per-(product, country) aggregate bounds.

    Three penalty modes are supported:
    - hard: inequality bounds ``(1 ± delta) * baseline``
    - l1: linear penalty via linopy abs-deviation variables
    - quadratic: quadratic penalty via linopy deviation variables

    Only links/groups with positive baselines are constrained.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    animal_baseline : pd.DataFrame | None
        FAO animal production with columns: country, product, production_mt.
    stability_cfg : dict
        Configuration with enabled, penalty_mode, l1_cost, quadratic_cost,
        deviation_type, crops.max_relative_deviation, etc.
    food_to_group : dict[str, str]
        Mapping from product names to food group names for FLW lookup.
    loss_waste : pd.DataFrame
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction.
    slack_marginal_cost : float
        Penalty cost in bn USD per Mt for production stability slack.
    """
    if not stability_cfg["enabled"]:
        return

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    penalty_mode = stability_cfg["penalty_mode"]

    # --- CROP PRODUCTION ---
    crops_cfg = stability_cfg["crops"]
    if crops_cfg["enabled"]:
        if penalty_mode == "hard":
            _add_crop_hard_constraints(
                n, link_p, links_df, crops_cfg, slack_marginal_cost
            )
        elif penalty_mode == "l1":
            _add_crop_l1_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["deviation_type"],
                stability_cfg["l1_cost"],
                crops_cfg["min_baseline_mt"],
            )
        elif penalty_mode == "quadratic":
            _add_crop_quadratic_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                crops_cfg["min_baseline_mt"],
            )

    # --- ANIMAL PRODUCTION ---
    animals_cfg = stability_cfg["animals"]
    if animals_cfg["enabled"] and animal_baseline is not None:
        if penalty_mode == "hard":
            _add_animal_hard_constraints(
                n,
                link_p,
                links_df,
                animal_baseline,
                animals_cfg,
                food_to_group,
                loss_waste,
                slack_marginal_cost,
            )
        elif penalty_mode == "l1":
            _add_animal_l1_penalty(
                n,
                link_p,
                links_df,
                animal_baseline,
                food_to_group,
                loss_waste,
                stability_cfg["deviation_type"],
                stability_cfg["l1_cost"],
                animals_cfg["min_baseline_mt"],
            )
        elif penalty_mode == "quadratic":
            _add_animal_quadratic_penalty(
                n,
                link_p,
                links_df,
                animal_baseline,
                food_to_group,
                loss_waste,
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                animals_cfg["min_baseline_mt"],
            )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _crop_production_and_baselines(
    link_p, links_df: pd.DataFrame, min_baseline_mt: float
) -> tuple | None:
    """Extract production expressions and baselines for constrained crop links.

    Returns ``(link_names, production, baselines)`` for crop production links
    with positive ``baseline_production_mt``, or ``None`` if there are none.
    """
    crop_links = links_df[links_df["carrier"] == "crop_production"]
    if crop_links.empty or "baseline_production_mt" not in crop_links.columns:
        logger.info("No crop production links with baselines; skipping crop stability")
        return None

    baseline_floor = float(min_baseline_mt)

    # Only constrain links with sufficiently large positive baselines.
    eligible = crop_links[crop_links["baseline_production_mt"] > baseline_floor]
    if eligible.empty:
        logger.info(
            "No crop baselines exceed %.6g Mt; skipping crop stability constraints",
            baseline_floor,
        )
        return None

    if baseline_floor > 0:
        removed = len(crop_links) - len(eligible)
        if removed > 0:
            logger.info(
                "Crop stability baseline filter removed %d/%d links below %.6g Mt",
                removed,
                len(crop_links),
                baseline_floor,
            )

    link_names = eligible.index
    efficiencies = xr.DataArray(
        eligible["efficiency"].values, coords={"name": link_names}, dims="name"
    )
    baselines = xr.DataArray(
        eligible["baseline_production_mt"].values,
        coords={"name": link_names},
        dims="name",
    )
    production = link_p.sel(name=link_names) * efficiencies
    return link_names, production, baselines


def _compute_total_animal_production(
    link_p, links_df: pd.DataFrame
) -> tuple[xr.DataArray, pd.Index]:
    """Compute total animal production grouped by (product, country).

    Returns (total_production DataArray, model_index).
    """
    prod_mask = links_df["product"].notna() & (links_df["product"] != "")
    prod_links = links_df[prod_mask]

    if prod_links.empty:
        return xr.DataArray(), pd.Index([])

    products = prod_links["product"].astype(str)
    countries = prod_links["country"].astype(str)
    link_names = prod_links.index

    efficiencies = xr.DataArray(
        prod_links["efficiency"].values, coords={"name": link_names}, dims="name"
    )
    production_vars = link_p.sel(name=link_names)

    grouper = pd.MultiIndex.from_arrays(
        [products.values, countries.values], names=["product", "country"]
    )
    da_grouper = xr.DataArray(grouper, coords={"name": link_names}, dims="name")

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()
    model_index = pd.Index(total_production.coords["group"].values, name="group")

    return total_production, model_index


def _animal_production_and_baselines(
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    min_baseline_mt: float,
) -> tuple | None:
    """Compute animal production expressions and baselines for nonzero groups.

    Returns ``(common_index, production, baselines)`` or ``None``.
    """
    total_production, model_index = _compute_total_animal_production(link_p, links_df)
    if total_production.sizes.get("group", 0) == 0:
        logger.info("No animal production to constrain")
        return None

    target_series = _aggregate_animal_baseline(
        animal_baseline, food_to_group, loss_waste
    )
    n_targets = len(target_series)
    baseline_floor = float(min_baseline_mt)
    # Only constrain sufficiently large positive baselines.
    target_series = target_series[target_series > baseline_floor]
    if baseline_floor > 0:
        removed = n_targets - len(target_series)
        if removed > 0:
            logger.info(
                "Animal stability baseline filter removed %d targets below %.6g Mt",
                removed,
                baseline_floor,
            )

    common_index = model_index.intersection(target_series.index)
    if common_index.empty:
        logger.warning("No matching animal production targets for stability bounds")
        return None

    baselines_arr = xr.DataArray(
        target_series.loc[common_index].values,
        coords={"group": common_index},
        dims="group",
    )
    production = total_production.sel(group=common_index)
    return common_index, production, baselines_arr


# ─── Crop: hard constraints ──────────────────────────────────────────────────


def _add_crop_hard_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    crops_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add per-link crop production stability bounds (hard mode).

    ``(1 - delta) * baseline <= p * efficiency <= (1 + delta) * baseline``
    """
    result = _crop_production_and_baselines(
        link_p, links_df, crops_cfg["min_baseline_mt"]
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result
    delta = crops_cfg["max_relative_deviation"]

    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    enable_slack = crops_cfg["enable_slack"]
    if enable_slack:
        slack_coords = xr.DataArray(
            np.zeros(len(link_names)),
            coords={"name": link_names},
            dims="name",
        ).coords
        crop_slack = m.add_variables(
            lower=0,
            coords=slack_coords,
            name="crop_production_slack",
        )
        m.add_constraints(
            production + crop_slack >= lower_bounds,
            name="GlobalConstraint-crop_production_min",
        )
        m.objective += slack_marginal_cost * crop_slack.sum()
        logger.info(
            "Added crop production slack variables for %d links (cost=%.1f bn USD/Mt)",
            len(link_names),
            slack_marginal_cost,
        )
    else:
        m.add_constraints(
            production >= lower_bounds,
            name="GlobalConstraint-crop_production_min",
        )

    m.add_constraints(
        production <= upper_bounds,
        name="GlobalConstraint-crop_production_max",
    )

    n.global_constraints.add(
        [f"crop_production_min_{name}" for name in link_names],
        sense=">=",
        constant=lower_bounds.values,
        type="production_stability",
    )
    n.global_constraints.add(
        [f"crop_production_max_{name}" for name in link_names],
        sense="<=",
        constant=upper_bounds.values,
        type="production_stability",
    )

    logger.info(
        "Added %d per-link crop production stability constraints (delta=%.0f%%)",
        2 * len(link_names),
        delta * 100,
    )


# ─── Crop: L1 penalty ────────────────────────────────────────────────────────


def _add_crop_l1_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    deviation_type: str,
    l1_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add L1 (absolute-value) penalty on crop production deviations.

    Creates a linopy variable ``abs_dev >= 0`` per constrained link and adds:
      abs_dev >= +(production - baseline)
      abs_dev >= -(production - baseline)
      objective += l1_cost * sum(abs_dev)
    """
    result = _crop_production_and_baselines(link_p, links_df, min_baseline_mt)
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    if deviation_type == "relative":
        deviation = (production - baselines) / baselines
    else:
        deviation = production - baselines

    abs_dev = m.add_variables(
        lower=0,
        coords=[link_names],
        dims=["name"],
        name="crop_stability_abs_dev",
    )

    m.add_constraints(
        abs_dev >= deviation,
        name="GlobalConstraint-crop_stability_pos",
    )
    m.add_constraints(
        abs_dev >= -deviation,
        name="GlobalConstraint-crop_stability_neg",
    )
    m.objective += l1_cost * abs_dev.sum()

    logger.info(
        "Added %d per-link crop L1 stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        l1_cost,
        deviation_type,
    )


# ─── Crop: quadratic penalty ─────────────────────────────────────────────────


def _add_crop_quadratic_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add quadratic penalty on crop production deviations.

    Creates a linopy variable ``dev`` per constrained link and adds:
      dev == production - baseline
      objective += 0.5 * quadratic_cost * sum(dev²)
    """
    result = _crop_production_and_baselines(link_p, links_df, min_baseline_mt)
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    if deviation_type == "relative":
        deviation = (production - baselines) / baselines
    else:
        deviation = production - baselines

    dev = m.add_variables(
        coords=[link_names],
        dims=["name"],
        name="crop_stability_dev",
    )

    m.add_constraints(
        dev == deviation,
        name="GlobalConstraint-crop_stability_dev",
    )
    m.objective += 0.5 * quadratic_cost * (dev * dev).sum()

    logger.info(
        "Added %d per-link crop quadratic stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        quadratic_cost,
        deviation_type,
    )


# ─── Animal: hard constraints ────────────────────────────────────────────────


def _add_animal_hard_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    animals_cfg: dict,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    slack_marginal_cost: float,
) -> None:
    """Add animal production stability bounds (hard mode)."""
    result = _animal_production_and_baselines(
        link_p,
        links_df,
        animal_baseline,
        food_to_group,
        loss_waste,
        animals_cfg["min_baseline_mt"],
    )
    if result is None:
        return

    m = n.model
    common_index, production, baselines = result
    delta = animals_cfg["max_relative_deviation"]

    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    enable_slack = animals_cfg["enable_slack"]
    if enable_slack:
        slack_coords = xr.DataArray(
            np.zeros(len(common_index)),
            coords={"group": common_index},
            dims="group",
        ).coords
        animal_slack = m.add_variables(
            lower=0,
            coords=slack_coords,
            name="animal_production_slack",
        )
        m.add_constraints(
            production + animal_slack >= lower_bounds,
            name="GlobalConstraint-animal_production_min",
        )
        m.objective += slack_marginal_cost * animal_slack.sum()
        logger.info(
            "Added animal production slack variables for %d (product, country) pairs "
            "(cost=%.1f bn USD/Mt)",
            len(common_index),
            slack_marginal_cost,
        )
    else:
        m.add_constraints(
            production >= lower_bounds,
            name="GlobalConstraint-animal_production_min",
        )

    m.add_constraints(
        production <= upper_bounds,
        name="GlobalConstraint-animal_production_max",
    )

    gc_products = [prod for prod, _country in common_index]
    gc_countries = [country for _prod, country in common_index]
    n.global_constraints.add(
        [f"animal_production_min_{prod}_{country}" for prod, country in common_index],
        sense=">=",
        constant=lower_bounds.values,
        type="production_stability",
        country=gc_countries,
        product=gc_products,
    )
    n.global_constraints.add(
        [f"animal_production_max_{prod}_{country}" for prod, country in common_index],
        sense="<=",
        constant=upper_bounds.values,
        type="production_stability",
        country=gc_countries,
        product=gc_products,
    )

    logger.info(
        "Added %d animal production stability constraints (delta=%.0f%%)",
        2 * len(common_index),
        delta * 100,
    )


# ─── Animal: L1 penalty ──────────────────────────────────────────────────────


def _add_animal_l1_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    deviation_type: str,
    l1_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add L1 penalty on animal production deviations."""
    result = _animal_production_and_baselines(
        link_p,
        links_df,
        animal_baseline,
        food_to_group,
        loss_waste,
        min_baseline_mt,
    )
    if result is None:
        return

    m = n.model
    common_index, production, baselines = result

    if deviation_type == "relative":
        deviation = (production - baselines) / baselines
    else:
        deviation = production - baselines

    abs_dev = m.add_variables(
        lower=0,
        coords=[common_index],
        dims=["group"],
        name="animal_stability_abs_dev",
    )

    m.add_constraints(
        abs_dev >= deviation,
        name="GlobalConstraint-animal_stability_pos",
    )
    m.add_constraints(
        abs_dev >= -deviation,
        name="GlobalConstraint-animal_stability_neg",
    )
    m.objective += l1_cost * abs_dev.sum()

    logger.info(
        "Added %d animal L1 stability penalties (cost=%.4f, mode=%s)",
        len(common_index),
        l1_cost,
        deviation_type,
    )


# ─── Animal: quadratic penalty ───────────────────────────────────────────────


def _add_animal_quadratic_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add quadratic penalty on animal production deviations."""
    result = _animal_production_and_baselines(
        link_p,
        links_df,
        animal_baseline,
        food_to_group,
        loss_waste,
        min_baseline_mt,
    )
    if result is None:
        return

    m = n.model
    common_index, production, baselines = result

    if deviation_type == "relative":
        deviation = (production - baselines) / baselines
    else:
        deviation = production - baselines

    dev = m.add_variables(
        coords=[common_index],
        dims=["group"],
        name="animal_stability_dev",
    )

    m.add_constraints(
        dev == deviation,
        name="GlobalConstraint-animal_stability_dev",
    )
    m.objective += 0.5 * quadratic_cost * (dev * dev).sum()

    logger.info(
        "Added %d animal quadratic stability penalties (cost=%.4f, mode=%s)",
        len(common_index),
        quadratic_cost,
        deviation_type,
    )
