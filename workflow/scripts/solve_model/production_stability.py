# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

Land conversion stability penalizes deviations from zero on links that route
land between uses: cropland-to-pasture, new land conversion, and spare land.
These links have zero baseline (no conversion in the reference year), so any
flow incurs a stability cost.

Hard constraints apply to all links, so zero-baseline links are constrained to
stay exactly at zero production/feed use. Penalty modes (L1/quadratic) also
apply to all links, so zero-baseline links incur a stability cost when
activated.
"""

import copy
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import yaml

logger = logging.getLogger(__name__)

# Carriers representing land-use transitions (all have zero baseline).
LAND_CONVERSION_CARRIERS = [
    "land_conversion",
    "existing_to_pasture",
    "new_to_pasture",
    "spare_land",
    "spare_existing_grassland",
]

CALIBRATED_SENTINEL = "calibrated"


def resolve_calibrated_l1_costs(
    stability_cfg: dict, calibrated_l1_yaml: str | None
) -> dict:
    """Return ``stability_cfg`` with the ``"calibrated"`` sentinel resolved.

    ``validation.production_stability.land_l1_cost`` and
    ``validation.production_stability.animal_feed_l1_cost`` may each be set
    to the string ``"calibrated"``; when they are, we look up the calibrated
    numeric values in ``calibrated_l1_yaml`` (produced by
    ``calibrate_prod_stability``).

    The input dict is not mutated. If no sentinel is present, returns the
    input unchanged.
    """

    def _needs_lookup(cfg: dict) -> bool:
        return (
            cfg.get("land_l1_cost") == CALIBRATED_SENTINEL
            or cfg.get("animal_feed_l1_cost") == CALIBRATED_SENTINEL
        )

    if not _needs_lookup(stability_cfg):
        return stability_cfg

    if calibrated_l1_yaml is None:
        raise ValueError(
            "validation.production_stability contains the sentinel "
            f"'{CALIBRATED_SENTINEL}' but no calibrated-L1 YAML was provided "
            "to the solve. Check that prod_stability_calibration.enabled is "
            "true and that the file exists."
        )

    path = Path(calibrated_l1_yaml)
    with path.open() as f:
        calibrated = yaml.safe_load(f)
    land_val = float(calibrated["land_l1_cost"])
    animal_feed_val = float(calibrated["animal_feed_l1_cost"])

    resolved = copy.deepcopy(stability_cfg)
    if resolved.get("land_l1_cost") == CALIBRATED_SENTINEL:
        resolved["land_l1_cost"] = land_val
        logger.info(
            "Resolved production_stability.land_l1_cost='calibrated' -> %.6f (from %s)",
            land_val,
            path,
        )
    if resolved.get("animal_feed_l1_cost") == CALIBRATED_SENTINEL:
        resolved["animal_feed_l1_cost"] = animal_feed_val
        logger.info(
            "Resolved production_stability.animal_feed_l1_cost='calibrated' -> %.6f "
            "(from %s)",
            animal_feed_val,
            path,
        )
    return resolved


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

    Hard mode and penalty modes apply to all links. Penalty modes use a
    denominator floor for relative deviations.

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
                stability_cfg["land_l1_cost"],
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
                stability_cfg["land_l1_cost"],
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
        # Determine animal L1/quadratic cost and scaling.
        # If animal_feed_l1_cost is set, use it directly in native Mt DM units
        # (no scaling). Otherwise, compute a dynamic scaling coefficient so
        # that animal feed deviations (Mt DM) are converted to Mha-equivalent
        # units, making land_l1_cost/quadratic_cost comparable across
        # crop/grassland (Mha) and animal (Mt DM) components.
        animal_l1_cost_override = stability_cfg.get("animal_feed_l1_cost")
        if animal_l1_cost_override is not None:
            animal_l1_cost = float(animal_l1_cost_override)
            animal_scale = 1.0
            logger.info(
                "Using animal_feed_l1_cost directly: %.4f bn USD/Mt DM (no scaling)",
                animal_l1_cost,
            )
        else:
            animal_l1_cost = stability_cfg["land_l1_cost"]
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
                animal_l1_cost,
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

    # --- LAND CONVERSION ---
    land_conversion_cfg = stability_cfg["land_conversion"]
    if land_conversion_cfg["enabled"]:
        if penalty_mode == "hard":
            logger.warning(
                "Hard mode is not supported for land conversion stability "
                "(zero baselines would forbid all conversion); skipping"
            )
        elif penalty_mode == "l1":
            _add_land_conversion_l1_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["land_l1_cost"],
            )
        elif penalty_mode == "quadratic":
            _add_land_conversion_quadratic_penalty(
                n,
                link_p,
                links_df,
                stability_cfg["quadratic_cost"],
            )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _production_and_baselines(
    link_p,
    links_df,
    carrier: str,
    min_baseline: float,
    *,
    include_all_links: bool = True,
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
    include_all_links: bool = True,
) -> tuple | None:
    """Extract feed use expressions and baselines for animal links.

    Returns ``(link_names, feed_use, baselines)`` for animal production links,
    or ``None`` if there are no eligible links. When ``include_all_links`` is
    false, links at or below ``min_baseline`` are excluded.

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
    include_all_links: bool = True,
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
    result = _animal_feed_and_baselines(
        link_p,
        links_df,
        animals_cfg["min_baseline"],
        include_all_links=True,
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
            "Added animal feed use slack variables for %d links (cost=%.1f bn USD/Mt)",
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


# ─── Land conversion: L1 penalty ────────────────────────────────────────────


def _add_land_conversion_l1_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    l1_cost: float,
) -> None:
    """Add L1 penalty on land conversion link flows (zero baseline).

    Since baseline is zero and all flows are non-negative, the absolute
    deviation equals the flow itself: ``|p - 0| = p``.
    """
    conv_links = links_df[links_df["carrier"].isin(LAND_CONVERSION_CARRIERS)]
    if conv_links.empty:
        logger.info("No land conversion links found; skipping stability")
        return

    m = n.model
    link_names = conv_links.index
    flow = link_p.sel(name=link_names)

    abs_dev = m.add_variables(
        lower=0,
        coords=[link_names],
        dims=["name"],
        name="land_conversion_stability_abs_dev",
    )

    m.add_constraints(
        abs_dev >= flow,
        name="GlobalConstraint-land_conversion_stability_pos",
    )
    m.add_constraints(
        abs_dev >= -flow,
        name="GlobalConstraint-land_conversion_stability_neg",
    )
    m.objective += l1_cost * abs_dev.sum()

    logger.info(
        "Added %d per-link land conversion L1 stability penalties (cost=%.4f)",
        len(link_names),
        l1_cost,
    )


# ─── Land conversion: quadratic penalty ────────────────────────────────────


def _add_land_conversion_quadratic_penalty(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    quadratic_cost: float,
) -> None:
    """Add quadratic penalty on land conversion link flows (zero baseline)."""
    conv_links = links_df[links_df["carrier"].isin(LAND_CONVERSION_CARRIERS)]
    if conv_links.empty:
        logger.info("No land conversion links found; skipping stability")
        return

    m = n.model
    link_names = conv_links.index
    flow = link_p.sel(name=link_names)

    dev = m.add_variables(
        coords=[link_names],
        dims=["name"],
        name="land_conversion_stability_dev",
    )

    m.add_constraints(
        dev == flow,
        name="GlobalConstraint-land_conversion_stability_dev",
    )
    m.objective += 0.5 * quadratic_cost * (dev * dev).sum()

    logger.info(
        "Added %d per-link land conversion quadratic stability penalties (cost=%.4f)",
        len(link_names),
        quadratic_cost,
    )


# ─── Animal growth caps ────────────────────────────────────────────────────


def add_animal_growth_cap_constraints(
    n: pypsa.Network,
    growth_cap_cfg: dict,
) -> None:
    """Add per-link upper bounds on animal production growth.

    Constrains each animal production link to at most
    ``(1 + max_relative_increase) * baseline_feed_use_mt_dm``, preventing
    unrealistic spatial reallocation of livestock production.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    growth_cap_cfg : dict
        Configuration with ``enabled`` and ``max_relative_increase``.
    """
    if not growth_cap_cfg["enabled"]:
        return

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    result = _animal_feed_and_baselines(link_p, links_df, 0.0, include_all_links=True)
    if result is None:
        return

    link_names, feed_use, baselines = result
    cap = growth_cap_cfg["max_relative_increase"]
    upper_bounds = (1.0 + cap) * baselines

    m.add_constraints(
        feed_use <= upper_bounds,
        name="GlobalConstraint-animal_growth_cap",
    )

    n.global_constraints.add(
        [f"animal_growth_cap_{name}" for name in link_names],
        sense="<=",
        constant=upper_bounds.values,
        type="animal_growth_cap",
    )

    logger.info(
        "Added %d per-link animal growth cap constraints (max +%.0f%%)",
        len(link_names),
        cap * 100,
    )


# ─── Bounded negative cost-calibration corrections (two-tier) ─────────────


def add_bounded_subsidy_constraints(
    n: pypsa.Network,
    carriers: list[str] = (
        "crop_production",
        "grassland_production",
        "animal_production",
    ),
) -> None:
    """Apply negative cost-calibration corrections only up to the baseline.

    Cost calibration extracts duals at the ±1% hard bound, which represent
    the local marginal-cost gradient *at baseline production*. Applied as
    a flat per-Mha (or per-Mt-DM-feed) correction at any production level
    under L1 stability, a moderate negative correction calibrated on a
    small baseline can drive runaway expansion (the canonical olive-USA
    case at -0.40 bnUSD/Mha calibrated on 0.04 Mha would grow olive 19x
    if unbounded; the magnitude cap by itself doesn't help because the
    pathological case has a moderate gradient, not a large one).

    The two-tier resolution: positive corrections are applied additively
    at all levels (already done in build_model). Negative corrections
    (subsidies) are stored as a per-link
    ``bounded_subsidy_bnusd_per_<unit>`` attribute and applied here only
    on the first ``baseline_<...>`` units of dispatch. Beyond baseline,
    the subsidy stops contributing — production faces uncorrected base
    cost. This preserves the calibration's local-gradient interpretation
    exactly and bounds the per-link subsidy budget at
    ``correction x baseline``.

    Implementation: for each link with a non-zero subsidy, an auxiliary
    variable ``aux_p ∈ [0, baseline]`` is introduced together with the
    constraint ``aux_p <= link_p``. The objective gains
    ``rate x aux_p`` (rate is negative, so the model maximises
    ``aux_p`` up to ``min(p, baseline)``).

    Parameters
    ----------
    n : pypsa.Network
        Network containing the model. Expected to have per-link
        attributes ``bounded_subsidy_bnusd_per_<unit>`` and a baseline
        column matching the carrier (``baseline_area_mha`` for crop /
        grassland; ``baseline_feed_use_mt_dm`` for animals).
    carriers : list[str]
        Link carriers to scan; controls which subsidies are activated.
    """
    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    for carrier in carriers:
        if carrier == "animal_production":
            attr = "bounded_subsidy_bnusd_per_mt"
            baseline_col = "baseline_feed_use_mt_dm"
        else:
            attr = "bounded_subsidy_bnusd_per_mha"
            baseline_col = "baseline_area_mha"

        sub = links_df[links_df["carrier"] == carrier]
        if attr not in sub.columns or baseline_col not in sub.columns:
            continue
        sub = sub[(sub[attr] < 0) & (sub[baseline_col] > 0)]
        if sub.empty:
            continue

        link_names = sub.index
        baselines = sub[baseline_col].astype(float).to_numpy()
        rates = sub[attr].astype(float).to_numpy()

        baselines_xr = xr.DataArray(baselines, coords={"name": link_names}, dims="name")
        rates_xr = xr.DataArray(rates, coords={"name": link_names}, dims="name")

        aux = m.add_variables(
            lower=0,
            upper=baselines_xr,
            coords=[link_names],
            dims=["name"],
            name=f"{carrier}_bounded_subsidy_p",
        )
        m.add_constraints(
            aux <= link_p.sel(name=link_names),
            name=f"GlobalConstraint-{carrier}_bounded_subsidy_le_p",
        )
        m.objective += (rates_xr * aux).sum()

        logger.info(
            "Bounded subsidy active on %d %s links (rates min=%.4f, max=%.4f, "
            "total subsidy budget at baseline = %.2f bnUSD)",
            len(sub),
            carrier,
            rates.min(),
            rates.max(),
            float((rates * baselines).sum()),
        )


# ─── Crop growth caps ──────────────────────────────────────────────────────


def add_crop_growth_cap_constraints(
    n: pypsa.Network,
    growth_cap_cfg: dict,
) -> None:
    """Add per-(crop, country) upper bounds on total harvested area.

    Aggregates ``crop_production`` link dispatch across regions, resource
    classes and water-supply types within each (crop, country), then bounds
    the total at ``(1 + max_relative_increase) * sum_baseline``. This is a
    structural backstop against pathological extrapolation of cost-
    calibration corrections under L1 production stability — without it, a
    moderate per-Mha negative correction calibrated at a tiny baseline can
    drive large absolute expansion (the canonical olive-USA case turns
    0.04 Mha into 0.72 Mha at the current calibration).

    Country-level (rather than per-link) granularity is chosen so the
    constraint preserves within-country reallocation freedom (yield-driven
    shifts between regions / resource classes / water supply) while still
    bounding total country-level expansion of any single crop. This is
    structurally analogous to ``animal_growth_cap``, which is per-link =
    per-(product, feed_category, country) since animals lack the regional
    dimension.

    All (crop, country) groups are constrained. Groups with zero baseline get
    an upper bound of zero, so crops cannot be introduced into countries where
    they were not present in the baseline.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    growth_cap_cfg : dict
        Configuration with ``enabled`` and ``max_relative_increase``.
    """
    if not growth_cap_cfg["enabled"]:
        return

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static
    prod_links = links_df[links_df["carrier"] == "crop_production"]
    if prod_links.empty or "baseline_area_mha" not in prod_links.columns:
        logger.info("No crop_production links with baselines; skipping crop growth cap")
        return

    cap = growth_cap_cfg["max_relative_increase"]
    # Build a group key per link and aggregate baselines per (crop, country).
    group_keys = (
        prod_links["crop"].astype(str) + "::" + prod_links["country"].astype(str)
    )
    baseline_per_group = (
        prod_links.assign(_group=group_keys.values)
        .groupby("_group")["baseline_area_mha"]
        .sum()
        .sort_index()
    )

    # Vectorised: groupby-sum Link-p over the (crop, country) key, then
    # add all constraints in a single linopy call.
    group_map = xr.DataArray(
        group_keys.values,
        coords={"name": prod_links.index},
        dims="name",
        name="cap_group",
    )
    link_vars = link_p.sel(name=prod_links.index)
    grouped = link_vars.groupby(group_map).sum()

    upper_bounds = xr.DataArray(
        ((1.0 + cap) * baseline_per_group).to_numpy(),
        coords={"cap_group": baseline_per_group.index.to_numpy()},
        dims="cap_group",
    )

    m.add_constraints(
        grouped <= upper_bounds,
        name="GlobalConstraint-crop_growth_cap",
    )

    constraint_names = [
        f"crop_growth_cap_{key.replace('::', '_')}" for key in baseline_per_group.index
    ]
    n.global_constraints.add(
        constraint_names,
        sense="<=",
        constant=upper_bounds.to_numpy(),
        type="crop_growth_cap",
    )

    logger.info(
        "Added %d crop growth cap constraints at +%.0f%%",
        len(constraint_names),
        100.0 * cap,
    )
