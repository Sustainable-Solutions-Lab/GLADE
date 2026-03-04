# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production stability constraints and penalties for the food systems model.

This module provides constraint builders that limit production deviation from
baseline levels. Three penalty modes are supported:

- **hard**: Inequality bounds constraining production to within ±delta of baseline
- **l1**: Linear absolute-value penalty via linopy variables added to the objective
- **quadratic**: Quadratic penalty via linopy variables added to the objective

Crop and grassland stability operate at the **per-link** level: each production
link is individually constrained to stay near its own baseline area (observed
harvested area, computed during model building and stored as ``baseline_area_mha``).
Deviations are measured in Mha so that each hectare is penalized equally
regardless of yield.

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

    For crops/grassland: per-link bounds using ``baseline_area_mha``.
    For animals: per-link bounds using ``baseline_feed_use_mt_dm``.

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
            _add_production_hard_constraints(
                n,
                link_p,
                links_df,
                "crop_production",
                "crop",
                crops_cfg,
                slack_marginal_cost,
            )
        elif penalty_mode == "l1":
            _add_production_l1_penalty(
                n,
                link_p,
                links_df,
                "crop_production",
                "crop",
                stability_cfg["deviation_type"],
                stability_cfg["l1_cost"],
                crops_cfg["min_baseline"],
            )
        elif penalty_mode == "quadratic":
            _add_production_quadratic_penalty(
                n,
                link_p,
                links_df,
                "crop_production",
                "crop",
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                crops_cfg["min_baseline"],
            )

    # --- GRASSLAND PRODUCTION ---
    grassland_cfg = stability_cfg["grassland"]
    if grassland_cfg["enabled"]:
        if penalty_mode == "hard":
            _add_production_hard_constraints(
                n,
                link_p,
                links_df,
                "grassland_production",
                "grassland",
                grassland_cfg,
                slack_marginal_cost,
                include_all_links=True,
            )
        elif penalty_mode == "l1":
            _add_production_l1_penalty(
                n,
                link_p,
                links_df,
                "grassland_production",
                "grassland",
                stability_cfg["deviation_type"],
                stability_cfg["l1_cost"],
                grassland_cfg["min_baseline"],
            )
        elif penalty_mode == "quadratic":
            _add_production_quadratic_penalty(
                n,
                link_p,
                links_df,
                "grassland_production",
                "grassland",
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                grassland_cfg["min_baseline"],
            )

    # --- ANIMAL FEED USE ---
    animals_cfg = stability_cfg["animals"]
    if animals_cfg["enabled"]:
        # Compute dynamic scaling coefficient for absolute mode so that
        # animal feed deviations (in Mt DM) are converted to Mha-equivalent
        # units, making the shared l1_cost/quadratic_cost comparable across
        # crop/grassland (Mha) and animal (Mt DM) components.
        animal_scale = 1.0
        if stability_cfg["deviation_type"] == "absolute":
            crop_links = links_df[links_df["carrier"] == "crop_production"]
            grass_links = links_df[links_df["carrier"] == "grassland_production"]
            total_area = (
                crop_links.get("baseline_area_mha", pd.Series(dtype=float)).sum()
                + grass_links.get("baseline_area_mha", pd.Series(dtype=float)).sum()
            )
            animal_links = links_df[links_df["carrier"] == "animal_production"]
            total_feed = animal_links.get(
                "baseline_feed_use_mt_dm", pd.Series(dtype=float)
            ).sum()
            if total_feed > 0:
                animal_scale = total_area / total_feed
            logger.info(
                "Animal scaling: %.4f Mha/Mt (area=%.1f, feed=%.1f)",
                animal_scale,
                total_area,
                total_feed,
            )

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
                animals_cfg["min_baseline"],
                animal_scale,
            )
        elif penalty_mode == "quadratic":
            _add_animal_quadratic_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["deviation_type"],
                stability_cfg["quadratic_cost"],
                animals_cfg["min_baseline"],
                animal_scale,
            )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _production_and_baselines(
    link_p,
    links_df,
    carrier: str,
    min_baseline: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract area expressions and baselines for production links.

    Returns ``(link_names, area, baselines)`` where ``area`` is the link
    dispatch variable (Mha) and ``baselines`` is the observed baseline area
    (Mha).  Returns ``None`` if there are no eligible links. When
    ``include_all_links`` is False, only links above ``min_baseline`` are
    included; when True, all links are included (including zero-baseline).
    """
    prod_links = links_df[links_df["carrier"] == carrier]
    if prod_links.empty or "baseline_area_mha" not in prod_links.columns:
        logger.info("No %s links with baselines; skipping stability", carrier)
        return None

    if not include_all_links:
        prod_links = prod_links[prod_links["baseline_area_mha"] > min_baseline]
        if prod_links.empty:
            logger.info(
                "No %s baselines exceed %.6g Mha; skipping stability constraints",
                carrier,
                min_baseline,
            )
            return None

    link_names = prod_links.index
    baselines = xr.DataArray(
        prod_links["baseline_area_mha"].to_numpy(dtype=float),
        coords={"name": link_names},
        dims="name",
    )
    area = link_p.sel(name=link_names)
    return link_names, area, baselines


def _animal_feed_and_baselines(
    link_p,
    links_df,
    min_baseline: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract feed use expressions and baselines for animal links.

    Returns ``(link_names, feed_use, baselines)`` for animal production links,
    or ``None`` if there are no eligible links. In hard mode, only links above
    ``min_baseline`` are included; in penalty modes all links are included.

    Feed use is ``link_p`` directly (p0 = feed input in Mt DM), so no
    efficiency multiplication is needed.
    """
    animal_links = links_df[links_df["carrier"] == "animal_production"]
    if animal_links.empty or "baseline_feed_use_mt_dm" not in animal_links.columns:
        logger.info(
            "No animal production links with feed baselines; skipping animal stability"
        )
        return None

    if not include_all_links:
        animal_links = animal_links[
            animal_links["baseline_feed_use_mt_dm"] > min_baseline
        ]
        if animal_links.empty:
            logger.info(
                "No animal feed baselines exceed %.6g Mt; "
                "skipping animal stability constraints",
                min_baseline,
            )
            return None

    link_names = animal_links.index
    baselines = xr.DataArray(
        animal_links["baseline_feed_use_mt_dm"].to_numpy(dtype=float),
        coords={"name": link_names},
        dims="name",
    )
    feed_use = link_p.sel(name=link_names)
    return link_names, feed_use, baselines


def _compute_stability_deviation(
    actual: xr.DataArray,
    baselines: xr.DataArray,
    deviation_type: str,
    min_baseline: float,
) -> xr.DataArray:
    """Compute stability deviation, flooring the denominator for relative mode.

    For relative deviations, ``min_baseline`` is used as the denominator
    floor so that near-zero/zero baselines produce finite, bounded deviations.
    """
    if deviation_type == "relative":
        denominator = xr.where(baselines > min_baseline, baselines, min_baseline)
        return (actual - baselines) / denominator
    return actual - baselines


# ─── Production: hard constraints ─────────────────────────────────────────────


def _add_production_hard_constraints(
    n: pypsa.Network,
    link_p,
    links_df,
    carrier: str,
    label: str,
    cfg: dict,
    slack_marginal_cost: float,
    *,
    include_all_links: bool = False,
) -> None:
    """Add per-link production stability bounds (hard mode).

    ``(1 - delta) * baseline <= area <= (1 + delta) * baseline``
    """
    result = _production_and_baselines(
        link_p,
        links_df,
        carrier,
        cfg["min_baseline"],
        include_all_links=include_all_links,
    )
    if result is None:
        return

    m = n.model
    link_names, area, baselines = result
    delta = cfg["max_relative_deviation"]

    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    enable_slack = cfg["enable_slack"]
    if enable_slack:
        slack_coords = xr.DataArray(
            np.zeros(len(link_names)),
            coords={"name": link_names},
            dims="name",
        ).coords
        prod_slack = m.add_variables(
            lower=0,
            coords=slack_coords,
            name=f"{label}_production_slack",
        )
        m.add_constraints(
            area + prod_slack >= lower_bounds,
            name=f"GlobalConstraint-{label}_production_min",
        )
        m.objective += slack_marginal_cost * prod_slack.sum()
        logger.info(
            "Added %s production slack variables for %d links (cost=%.1f bn USD/Mha)",
            label,
            len(link_names),
            slack_marginal_cost,
        )
    else:
        m.add_constraints(
            area >= lower_bounds,
            name=f"GlobalConstraint-{label}_production_min",
        )

    m.add_constraints(
        area <= upper_bounds,
        name=f"GlobalConstraint-{label}_production_max",
    )

    n.global_constraints.add(
        [f"{label}_production_min_{name}" for name in link_names],
        sense=">=",
        constant=lower_bounds.values,
        type="production_stability",
    )
    n.global_constraints.add(
        [f"{label}_production_max_{name}" for name in link_names],
        sense="<=",
        constant=upper_bounds.values,
        type="production_stability",
    )

    logger.info(
        "Added %d per-link %s production stability constraints (delta=%.0f%%)",
        2 * len(link_names),
        label,
        delta * 100,
    )


# ─── Production: L1 penalty ──────────────────────────────────────────────────


def _add_production_l1_penalty(
    n: pypsa.Network,
    link_p,
    links_df,
    carrier: str,
    label: str,
    deviation_type: str,
    l1_cost: float,
    min_baseline: float,
) -> None:
    """Add L1 (absolute-value) penalty on area deviations.

    Creates a linopy variable ``abs_dev >= 0`` per constrained link and adds:
      abs_dev >= +(area - baseline)
      abs_dev >= -(area - baseline)
      objective += l1_cost * sum(abs_dev)
    """
    result = _production_and_baselines(
        link_p, links_df, carrier, min_baseline, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, area, baselines = result

    deviation = _compute_stability_deviation(
        area, baselines, deviation_type, min_baseline
    )

    abs_dev = m.add_variables(
        lower=0,
        coords=[link_names],
        dims=["name"],
        name=f"{label}_stability_abs_dev",
    )

    m.add_constraints(
        abs_dev >= deviation,
        name=f"GlobalConstraint-{label}_stability_pos",
    )
    m.add_constraints(
        abs_dev >= -deviation,
        name=f"GlobalConstraint-{label}_stability_neg",
    )
    m.objective += l1_cost * abs_dev.sum()

    logger.info(
        "Added %d per-link %s L1 stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        label,
        l1_cost,
        deviation_type,
    )


# ─── Production: quadratic penalty ───────────────────────────────────────────


def _add_production_quadratic_penalty(
    n: pypsa.Network,
    link_p,
    links_df,
    carrier: str,
    label: str,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline: float,
) -> None:
    """Add quadratic penalty on area deviations.

    Creates a linopy variable ``dev`` per constrained link and adds:
      dev == area - baseline
      objective += 0.5 * quadratic_cost * sum(dev^2)
    """
    result = _production_and_baselines(
        link_p, links_df, carrier, min_baseline, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, area, baselines = result

    deviation = _compute_stability_deviation(
        area, baselines, deviation_type, min_baseline
    )

    dev = m.add_variables(
        coords=[link_names],
        dims=["name"],
        name=f"{label}_stability_dev",
    )

    m.add_constraints(
        dev == deviation,
        name=f"GlobalConstraint-{label}_stability_dev",
    )
    m.objective += 0.5 * quadratic_cost * (dev * dev).sum()

    logger.info(
        "Added %d per-link %s quadratic stability penalties (cost=%.4f, mode=%s)",
        len(link_names),
        label,
        quadratic_cost,
        deviation_type,
    )


# ─── Animal: hard constraints ────────────────────────────────────────────────


def _add_animal_hard_constraints(
    n: pypsa.Network,
    link_p,
    links_df,
    animals_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add per-link animal feed use stability bounds (hard mode).

    ``(1 - delta) * baseline <= feed_use <= (1 + delta) * baseline``
    """
    result = _animal_feed_and_baselines(link_p, links_df, animals_cfg["min_baseline"])
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
    links_df,
    deviation_type: str,
    l1_cost: float,
    min_baseline: float,
    animal_scale: float = 1.0,
) -> None:
    """Add L1 penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p, links_df, min_baseline, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use, baselines, deviation_type, min_baseline
    )
    if animal_scale != 1.0:
        deviation = deviation * animal_scale

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
    links_df,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline: float,
    animal_scale: float = 1.0,
) -> None:
    """Add quadratic penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p, links_df, min_baseline, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use, baselines, deviation_type, min_baseline
    )
    if animal_scale != 1.0:
        deviation = deviation * animal_scale

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
