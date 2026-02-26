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

Animal stability operates at the **per-link** level: each animal production link
is individually constrained to stay near its GLEAM feed-use baseline (stored as
``baseline_feed_use_mt_dm`` during model building). Feed use is constrained
directly (no efficiency multiplication needed).

Hard constraints only apply to links with sufficiently positive baselines to
avoid forcing zero-baseline links to stay exactly at zero production/feed use.
Penalty modes (L1/quadratic) apply to all links so zero-baseline links also
incur a stability cost when activated.
"""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)


def add_production_stability_constraints(
    n: pypsa.Network,
    stability_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add constraints limiting production deviation from baseline levels.

    For crops: per-link bounds using ``baseline_production_mt`` set during build.
    For animals: per-link bounds using ``baseline_feed_use_mt_dm`` set during build.

    Three penalty modes are supported:
    - hard: inequality bounds ``(1 ± delta) * baseline``
    - l1: linear penalty via linopy abs-deviation variables
    - quadratic: quadratic penalty via linopy deviation variables

    Hard mode constrains only links with positive baselines. Penalty modes apply
    to all links and use a denominator floor for relative deviations.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    stability_cfg : dict
        Configuration with enabled, penalty_mode, l1_cost, quadratic_cost,
        deviation_type, crops.max_relative_deviation, etc.
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

    # --- ANIMAL FEED USE ---
    animals_cfg = stability_cfg["animals"]
    if animals_cfg["enabled"]:
        if penalty_mode == "hard":
            _add_animal_hard_constraints(
                n, link_p, links_df, animals_cfg, slack_marginal_cost
            )
        elif penalty_mode == "l1":
            _add_animal_l1_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["deviation_type"],
                stability_cfg["l1_cost"],
                animals_cfg["min_baseline_mt"],
            )
        elif penalty_mode == "quadratic":
            _add_animal_quadratic_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                animals_cfg["min_baseline_mt"],
            )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _crop_production_and_baselines(
    link_p,
    links_df: pd.DataFrame,
    min_baseline_mt: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract production expressions and baselines for constrained crop links.

    Returns ``(link_names, production, baselines)`` for crop production links.
    In hard mode, only links above ``min_baseline_mt`` are returned. In penalty
    modes, all links are returned (including zero-baseline links).
    """
    crop_links = links_df[links_df["carrier"] == "crop_production"]
    if crop_links.empty or "baseline_production_mt" not in crop_links.columns:
        logger.info("No crop production links with baselines; skipping crop stability")
        return None

    baseline_floor = float(min_baseline_mt)

    if include_all_links:
        eligible = crop_links.copy()
        baselines_series = (
            pd.to_numeric(eligible["baseline_production_mt"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0.0)
        )
        if baseline_floor > 0:
            low_count = int((baselines_series <= baseline_floor).sum())
            if low_count > 0:
                logger.info(
                    "Crop stability penalties include %d/%d links at or below %.6g Mt baseline",
                    low_count,
                    len(eligible),
                    baseline_floor,
                )
    else:
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
        baselines_series = pd.to_numeric(
            eligible["baseline_production_mt"], errors="coerce"
        ).fillna(0.0)

    link_names = eligible.index
    efficiencies = xr.DataArray(
        eligible["efficiency"].values, coords={"name": link_names}, dims="name"
    )
    baselines = xr.DataArray(
        baselines_series.values, coords={"name": link_names}, dims="name"
    )
    production = link_p.sel(name=link_names) * efficiencies
    return link_names, production, baselines


def _animal_feed_and_baselines(
    link_p,
    links_df: pd.DataFrame,
    min_baseline_mt: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract feed use expressions and baselines for animal links.

    Returns ``(link_names, feed_use, baselines)`` for animal production links.
    In hard mode, only links above ``min_baseline_mt`` are returned. In penalty
    modes, all links are returned (including zero-baseline links).

    Feed use is ``link_p`` directly (p0 = feed input in Mt DM), so no
    efficiency multiplication is needed.
    """
    animal_links = links_df[links_df["carrier"] == "animal_production"]
    if animal_links.empty or "baseline_feed_use_mt_dm" not in animal_links.columns:
        logger.info(
            "No animal production links with feed baselines; skipping animal stability"
        )
        return None

    baseline_floor = float(min_baseline_mt)

    if include_all_links:
        eligible = animal_links.copy()
        baselines_series = (
            pd.to_numeric(eligible["baseline_feed_use_mt_dm"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0.0)
        )
        if baseline_floor > 0:
            low_count = int((baselines_series <= baseline_floor).sum())
            if low_count > 0:
                logger.info(
                    "Animal stability penalties include %d/%d links at or below %.6g Mt baseline",
                    low_count,
                    len(eligible),
                    baseline_floor,
                )
    else:
        eligible = animal_links[
            animal_links["baseline_feed_use_mt_dm"] > baseline_floor
        ]
        if eligible.empty:
            logger.info(
                "No animal feed baselines exceed %.6g Mt; "
                "skipping animal stability constraints",
                baseline_floor,
            )
            return None

        if baseline_floor > 0:
            removed = len(animal_links) - len(eligible)
            if removed > 0:
                logger.info(
                    "Animal stability baseline filter removed %d/%d links below %.6g Mt",
                    removed,
                    len(animal_links),
                    baseline_floor,
                )
        baselines_series = pd.to_numeric(
            eligible["baseline_feed_use_mt_dm"], errors="coerce"
        ).fillna(0.0)

    link_names = eligible.index
    feed_use = link_p.sel(name=link_names)
    baselines = xr.DataArray(
        baselines_series.values, coords={"name": link_names}, dims="name"
    )
    return link_names, feed_use, baselines


def _compute_stability_deviation(
    actual: xr.DataArray,
    baselines: xr.DataArray,
    deviation_type: str,
    min_baseline_mt: float,
) -> xr.DataArray:
    """Compute stability deviation with safe handling for near-zero baselines."""
    if deviation_type == "relative":
        denominator_floor = max(float(min_baseline_mt), 1e-9)
        denominator = xr.where(
            baselines > denominator_floor, baselines, denominator_floor
        )
        return (actual - baselines) / denominator
    return actual - baselines


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
    result = _crop_production_and_baselines(
        link_p,
        links_df,
        min_baseline_mt,
        include_all_links=True,
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    deviation = _compute_stability_deviation(
        production,
        baselines,
        deviation_type,
        min_baseline_mt,
    )

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
    result = _crop_production_and_baselines(
        link_p,
        links_df,
        min_baseline_mt,
        include_all_links=True,
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    deviation = _compute_stability_deviation(
        production,
        baselines,
        deviation_type,
        min_baseline_mt,
    )

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
    animals_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add per-link animal feed use stability bounds (hard mode).

    ``(1 - delta) * baseline <= feed_use <= (1 + delta) * baseline``
    """
    result = _animal_feed_and_baselines(
        link_p, links_df, animals_cfg["min_baseline_mt"]
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result
    delta = animals_cfg["max_relative_deviation"]

    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    enable_slack = animals_cfg["enable_slack"]
    if enable_slack:
        slack_coords = xr.DataArray(
            np.zeros(len(link_names)),
            coords={"name": link_names},
            dims="name",
        ).coords
        animal_slack = m.add_variables(
            lower=0,
            coords=slack_coords,
            name="animal_production_slack",
        )
        m.add_constraints(
            feed_use + animal_slack >= lower_bounds,
            name="GlobalConstraint-animal_production_min",
        )
        m.objective += slack_marginal_cost * animal_slack.sum()
        logger.info(
            "Added animal feed use slack variables for %d links "
            "(cost=%.1f bn USD/Mt)",
            len(link_names),
            slack_marginal_cost,
        )
    else:
        m.add_constraints(
            feed_use >= lower_bounds,
            name="GlobalConstraint-animal_production_min",
        )

    m.add_constraints(
        feed_use <= upper_bounds,
        name="GlobalConstraint-animal_production_max",
    )

    n.global_constraints.add(
        [f"animal_production_min_{name}" for name in link_names],
        sense=">=",
        constant=lower_bounds.values,
        type="production_stability",
    )
    n.global_constraints.add(
        [f"animal_production_max_{name}" for name in link_names],
        sense="<=",
        constant=upper_bounds.values,
        type="production_stability",
    )

    logger.info(
        "Added %d per-link animal feed use stability constraints (delta=%.0f%%)",
        2 * len(link_names),
        delta * 100,
    )


# ─── Animal: L1 penalty ──────────────────────────────────────────────────────


def _add_animal_l1_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    deviation_type: str,
    l1_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add L1 penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p,
        links_df,
        min_baseline_mt,
        include_all_links=True,
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use,
        baselines,
        deviation_type,
        min_baseline_mt,
    )

    abs_dev = m.add_variables(
        lower=0,
        coords=[link_names],
        dims=["name"],
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
        "Added %d per-link animal L1 stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        l1_cost,
        deviation_type,
    )


# ─── Animal: quadratic penalty ───────────────────────────────────────────────


def _add_animal_quadratic_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add quadratic penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p,
        links_df,
        min_baseline_mt,
        include_all_links=True,
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use,
        baselines,
        deviation_type,
        min_baseline_mt,
    )

    dev = m.add_variables(
        coords=[link_names],
        dims=["name"],
        name="animal_stability_dev",
    )

    m.add_constraints(
        dev == deviation,
        name="GlobalConstraint-animal_stability_dev",
    )
    m.objective += 0.5 * quadratic_cost * (dev * dev).sum()

    logger.info(
        "Added %d per-link animal quadratic stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        quadratic_cost,
        deviation_type,
    )
