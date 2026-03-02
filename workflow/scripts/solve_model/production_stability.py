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
link is individually constrained to stay near its own baseline (observed area x
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
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)


def add_production_stability_constraints(
    n: pypsa.Network,
    stability_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add constraints limiting production deviation from baseline levels.

    For crops/grassland: per-link bounds using ``baseline_production_mt``.
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
                crops_cfg["min_baseline_mt"],
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
                crops_cfg["min_baseline_mt"],
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
                grassland_cfg["min_baseline_mt"],
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
                grassland_cfg["min_baseline_mt"],
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


def _production_and_baselines(
    link_p,
    links_df,
    carrier: str,
    min_baseline_mt: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract production expressions and baselines for production links.

    Returns ``(link_names, production, baselines)`` for links matching the
    given ``carrier``, or ``None`` if there are no eligible links. In hard mode,
    only links above ``min_baseline_mt`` are included; in penalty modes all
    links are included.
    """
    prod_links = links_df[links_df["carrier"] == carrier]
    if prod_links.empty or "baseline_production_mt" not in prod_links.columns:
        logger.info("No %s links with baselines; skipping stability", carrier)
        return None

    if not include_all_links:
        prod_links = prod_links[prod_links["baseline_production_mt"] > min_baseline_mt]
        if prod_links.empty:
            logger.info(
                "No %s baselines exceed %.6g Mt; skipping stability constraints",
                carrier,
                min_baseline_mt,
            )
            return None

    link_names = prod_links.index
    baselines = xr.DataArray(
        prod_links["baseline_production_mt"].to_numpy(dtype=float),
        coords={"name": link_names},
        dims="name",
    )
    efficiencies = xr.DataArray(
        prod_links["efficiency"].to_numpy(dtype=float),
        coords={"name": link_names},
        dims="name",
    )
    production = link_p.sel(name=link_names) * efficiencies
    return link_names, production, baselines


def _animal_feed_and_baselines(
    link_p,
    links_df,
    min_baseline_mt: float,
    *,
    include_all_links: bool = False,
) -> tuple | None:
    """Extract feed use expressions and baselines for animal links.

    Returns ``(link_names, feed_use, baselines)`` for animal production links,
    or ``None`` if there are no eligible links. In hard mode, only links above
    ``min_baseline_mt`` are included; in penalty modes all links are included.

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
            animal_links["baseline_feed_use_mt_dm"] > min_baseline_mt
        ]
        if animal_links.empty:
            logger.info(
                "No animal feed baselines exceed %.6g Mt; "
                "skipping animal stability constraints",
                min_baseline_mt,
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
    min_baseline_mt: float,
) -> xr.DataArray:
    """Compute stability deviation, flooring the denominator for relative mode.

    For relative deviations, ``min_baseline_mt`` is used as the denominator
    floor so that near-zero/zero baselines produce finite, bounded deviations.
    """
    if deviation_type == "relative":
        denominator = xr.where(baselines > min_baseline_mt, baselines, min_baseline_mt)
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
) -> None:
    """Add per-link production stability bounds (hard mode).

    ``(1 - delta) * baseline <= p * efficiency <= (1 + delta) * baseline``
    """
    result = _production_and_baselines(
        link_p, links_df, carrier, cfg["min_baseline_mt"]
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result
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
            production + prod_slack >= lower_bounds,
            name=f"GlobalConstraint-{label}_production_min",
        )
        m.objective += slack_marginal_cost * prod_slack.sum()
        logger.info(
            "Added %s production slack variables for %d links (cost=%.1f bn USD/Mt)",
            label,
            len(link_names),
            slack_marginal_cost,
        )
    else:
        m.add_constraints(
            production >= lower_bounds,
            name=f"GlobalConstraint-{label}_production_min",
        )

    m.add_constraints(
        production <= upper_bounds,
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
    min_baseline_mt: float,
) -> None:
    """Add L1 (absolute-value) penalty on production deviations.

    Creates a linopy variable ``abs_dev >= 0`` per constrained link and adds:
      abs_dev >= +(production - baseline)
      abs_dev >= -(production - baseline)
      objective += l1_cost * sum(abs_dev)
    """
    result = _production_and_baselines(
        link_p, links_df, carrier, min_baseline_mt, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    deviation = _compute_stability_deviation(
        production, baselines, deviation_type, min_baseline_mt
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
    min_baseline_mt: float,
) -> None:
    """Add quadratic penalty on production deviations.

    Creates a linopy variable ``dev`` per constrained link and adds:
      dev == production - baseline
      objective += 0.5 * quadratic_cost * sum(dev^2)
    """
    result = _production_and_baselines(
        link_p, links_df, carrier, min_baseline_mt, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, production, baselines = result

    deviation = _compute_stability_deviation(
        production, baselines, deviation_type, min_baseline_mt
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
    links_df,
    deviation_type: str,
    l1_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add L1 penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p, links_df, min_baseline_mt, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use, baselines, deviation_type, min_baseline_mt
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
    links_df,
    deviation_type: str,
    quadratic_cost: float,
    min_baseline_mt: float,
) -> None:
    """Add quadratic penalty on animal feed use deviations."""
    result = _animal_feed_and_baselines(
        link_p, links_df, min_baseline_mt, include_all_links=True
    )
    if result is None:
        return

    m = n.model
    link_names, feed_use, baselines = result

    deviation = _compute_stability_deviation(
        feed_use, baselines, deviation_type, min_baseline_mt
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
