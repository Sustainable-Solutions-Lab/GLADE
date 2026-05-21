# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production-side deviation penalty constraints (land + feed).

This module implements the land-side (crop + grassland area, Mha) and
feed-side (animal feed use, Mt DM) portions of the model's
``deviation_penalty`` block. Three penalty modes are supported:

- **hard**: Inequality bounds constraining each link to (1 +/- delta) * baseline.
- **l1**: Linear absolute-value penalty via linopy variables added to objective.
- **quadratic**: Quadratic penalty via linopy variables added to objective.

Land penalties anchor each crop/grassland production link to its own
``baseline_area_mha``; deviations are in Mha so each hectare is penalised
equally regardless of yield. Feed penalties anchor each animal_production
link to its ``baseline_feed_use_mt_dm``. Land-conversion penalties anchor
links that route land between uses (conversion, pasture routing, sparing)
toward zero (their baseline).

Diet-side penalties (food_consumption) live in
:mod:`workflow.scripts.solve_model.diet_stability`. The L1-cost resolver
:func:`resolve_calibrated_l1_costs` here also resolves the diet sentinel
so all three components share a single calibration artefact.
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


def resolve_calibrated_l1_costs(dp_cfg: dict, calibrated_yaml: str | None) -> dict:
    """Resolve ``"calibrated"`` sentinels and apply per-component factors.

    For each component in ``{land, feed, diet}`` whose ``l1_cost`` is the
    string ``"calibrated"``, the numeric value is substituted from
    ``calibrated_yaml`` (produced by ``calibrate_deviation_penalty``). The
    per-component ``l1_cost_factor`` is then multiplied in so scenarios can
    scan around the calibrated central value without hard-coding absolute
    numbers that drift whenever the calibration is refreshed.

    The input dict is not mutated. When ``penalty_mode != "l1"`` the L1
    costs are unused and the input is returned unchanged.
    """
    if dp_cfg.get("penalty_mode") != "l1":
        return dp_cfg

    components = ("land", "feed", "diet")

    def _needs_lookup() -> bool:
        return any(dp_cfg[c]["l1_cost"] == CALIBRATED_SENTINEL for c in components)

    def _any_nontrivial_factor() -> bool:
        return any(float(dp_cfg[c]["l1_cost_factor"]) != 1.0 for c in components)

    if not _needs_lookup() and not _any_nontrivial_factor():
        return dp_cfg

    resolved = copy.deepcopy(dp_cfg)
    calibrated: dict | None = None
    if _needs_lookup():
        if calibrated_yaml is None:
            raise ValueError(
                "deviation_penalty contains the sentinel "
                f"'{CALIBRATED_SENTINEL}' but no calibrated YAML was provided "
                "to the solve. Check that deviation_penalty.calibration.enabled "
                "is true and that the file exists."
            )
        path = Path(calibrated_yaml)
        with path.open() as f:
            calibrated = yaml.safe_load(f)
        cal_components = set(calibrated.get("components", []))
        cal_l1 = calibrated.get("l1_costs", {})
        for component in components:
            if resolved[component]["l1_cost"] != CALIBRATED_SENTINEL:
                continue
            if component not in cal_components or component not in cal_l1:
                raise ValueError(
                    f"deviation_penalty.{component}.l1_cost='calibrated' but the "
                    f"calibrated YAML at {path} did not calibrate '{component}' "
                    f"(components: {sorted(cal_components)}). Either remove the "
                    "sentinel, set an explicit numeric value, or regenerate the "
                    "calibration including this component."
                )
            resolved[component]["l1_cost"] = float(cal_l1[component])
            logger.info(
                "Resolved deviation_penalty.%s.l1_cost='calibrated' -> %.6f (from %s)",
                component,
                resolved[component]["l1_cost"],
                path,
            )

    for component in components:
        factor = float(resolved[component]["l1_cost_factor"])
        value = resolved[component]["l1_cost"]
        if factor != 1.0 and value is not None:
            resolved[component]["l1_cost"] = float(value) * factor
            logger.info(
                "Applied deviation_penalty.%s.l1_cost_factor=%.6g -> l1_cost=%.6f",
                component,
                factor,
                resolved[component]["l1_cost"],
            )

    return resolved


def add_production_stability_constraints(
    n: pypsa.Network,
    dp_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add land + feed deviation penalty constraints.

    Reads from the unified ``deviation_penalty`` block:
    - ``dp_cfg["land"]`` covers crop_production, grassland_production and
      land_conversion carriers (anchored to ``baseline_area_mha``).
    - ``dp_cfg["feed"]`` covers animal_production feed use (anchored to
      ``baseline_feed_use_mt_dm``).

    The diet component is handled separately by
    :func:`workflow.scripts.solve_model.diet_stability.add_diet_stability_constraints`.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    dp_cfg : dict
        The resolved ``deviation_penalty`` block. ``l1_cost`` values must
        already be numeric (see :func:`resolve_calibrated_l1_costs`).
    slack_marginal_cost : float
        Penalty cost in bn USD per Mt for hard-mode production-stability slack.
    """
    if not dp_cfg["enabled"]:
        return

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    penalty_mode = dp_cfg["penalty_mode"]
    deviation_type = dp_cfg["deviation_type"]
    quadratic_cost = dp_cfg["quadratic_cost"]

    land_cfg = dp_cfg["land"]
    feed_cfg = dp_cfg["feed"]
    land_enabled = land_cfg["enabled"]
    feed_enabled = feed_cfg["enabled"]

    # --- CROP PRODUCTION ---
    crops_cfg = land_cfg["crops"]
    if land_enabled and crops_cfg["enabled"]:
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
                deviation_type,
                land_cfg["l1_cost"],
                crops_cfg["min_baseline"],
            )
        elif penalty_mode == "quadratic":
            _add_production_quadratic_penalty(
                n,
                link_p,
                links_df,
                "crop_production",
                "crop",
                deviation_type,
                quadratic_cost,
                crops_cfg["min_baseline"],
            )

    # --- GRASSLAND PRODUCTION ---
    grassland_cfg = land_cfg["grassland"]
    if land_enabled and grassland_cfg["enabled"]:
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
                deviation_type,
                land_cfg["l1_cost"],
                grassland_cfg["min_baseline"],
            )
        elif penalty_mode == "quadratic":
            _add_production_quadratic_penalty(
                n,
                link_p,
                links_df,
                "grassland_production",
                "grassland",
                deviation_type,
                quadratic_cost,
                grassland_cfg["min_baseline"],
            )

    # --- ANIMAL FEED USE ---
    if feed_enabled:
        # Determine animal L1 cost and scaling (only meaningful outside hard
        # mode; hard mode adds box constraints and ignores both values).
        # If feed.l1_cost is set, use it directly in native Mt DM units
        # (no scaling). Otherwise (null), compute a dynamic scaling so
        # that feed deviations (Mt DM) are converted to Mha-equivalent
        # units, making land.l1_cost comparable across crop/grassland
        # (Mha) and animal (Mt DM) components.
        animal_l1_cost = None
        animal_scale = 1.0
        feed_l1_cost = feed_cfg["l1_cost"]
        if penalty_mode == "hard":
            pass
        elif feed_l1_cost is not None:
            animal_l1_cost = float(feed_l1_cost)
            logger.info(
                "Using feed.l1_cost directly: %.4f bn USD/Mt DM (no scaling)",
                animal_l1_cost,
            )
        else:
            animal_l1_cost = land_cfg["l1_cost"]
            if deviation_type == "absolute":
                crop_links = links_df[links_df["carrier"] == "crop_production"]
                grass_links = links_df[links_df["carrier"] == "grassland_production"]
                animal_links = links_df[links_df["carrier"] == "animal_production"]
                if not crop_links.empty and "baseline_area_mha" not in crop_links:
                    raise ValueError(
                        "crop_production links missing baseline_area_mha; "
                        "build_model must populate it before solve"
                    )
                if not grass_links.empty and "baseline_area_mha" not in grass_links:
                    raise ValueError(
                        "grassland_production links missing baseline_area_mha; "
                        "build_model must populate it before solve"
                    )
                if (
                    not animal_links.empty
                    and "baseline_feed_use_mt_dm" not in animal_links
                ):
                    raise ValueError(
                        "animal_production links missing baseline_feed_use_mt_dm; "
                        "build_model must populate it before solve"
                    )
                total_area = (
                    crop_links["baseline_area_mha"].sum()
                    if not crop_links.empty
                    else 0.0
                ) + (
                    grass_links["baseline_area_mha"].sum()
                    if not grass_links.empty
                    else 0.0
                )
                total_feed = (
                    animal_links["baseline_feed_use_mt_dm"].sum()
                    if not animal_links.empty
                    else 0.0
                )
                if total_feed > 0 and total_area > 0:
                    animal_scale = total_area / total_feed
                logger.info(
                    "Animal scaling: %.4f Mha/Mt (area=%.1f, feed=%.1f)",
                    animal_scale,
                    total_area,
                    total_feed,
                )

        if penalty_mode == "hard":
            _add_animal_hard_constraints(
                n, link_p, links_df, feed_cfg, slack_marginal_cost
            )
        elif penalty_mode == "l1":
            _add_animal_l1_penalty(
                n,
                link_p,
                links_df,
                deviation_type,
                animal_l1_cost,
                feed_cfg["min_baseline"],
                animal_scale,
            )
        elif penalty_mode == "quadratic":
            _add_animal_quadratic_penalty(
                n,
                link_p,
                links_df,
                deviation_type,
                quadratic_cost,
                feed_cfg["min_baseline"],
                animal_scale,
            )

    # --- LAND CONVERSION ---
    land_conversion_cfg = land_cfg["land_conversion"]
    if land_enabled and land_conversion_cfg["enabled"]:
        if penalty_mode == "hard":
            logger.warning(
                "Hard mode is not supported for land-conversion deviation "
                "penalty (zero baselines would forbid all conversion); skipping"
            )
        elif penalty_mode == "l1":
            _add_land_conversion_l1_penalty(
                n,
                link_p,
                links_df,
                land_cfg["l1_cost"],
            )
        elif penalty_mode == "quadratic":
            _add_land_conversion_quadratic_penalty(
                n,
                link_p,
                links_df,
                quadratic_cost,
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
        if min_baseline <= 0:
            raise ValueError(
                "deviation_penalty <component>.min_baseline must be > 0 in "
                f"relative mode; got {min_baseline}"
            )
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
        prod_slack_lo = m.add_variables(
            lower=0,
            coords=slack_coords,
            name=f"{label}_production_slack",
        )
        prod_slack_hi = m.add_variables(
            lower=0,
            coords=slack_coords,
            name=f"{label}_production_slack_upper",
        )
        m.add_constraints(
            area + prod_slack_lo >= lower_bounds,
            name=f"GlobalConstraint-{label}_production_min",
        )
        m.add_constraints(
            area - prod_slack_hi <= upper_bounds,
            name=f"GlobalConstraint-{label}_production_max",
        )
        m.objective += slack_marginal_cost * (prod_slack_lo.sum() + prod_slack_hi.sum())
        logger.info(
            "Added %s production slack variables (lower+upper) for %d links "
            "(cost=%.1f bn USD/Mha)",
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
        animal_slack_lo = m.add_variables(
            lower=0,
            coords=slack_coords,
            name="animal_production_slack",
        )
        animal_slack_hi = m.add_variables(
            lower=0,
            coords=slack_coords,
            name="animal_production_slack_upper",
        )
        m.add_constraints(
            feed_use + animal_slack_lo >= lower_bounds,
            name="GlobalConstraint-animal_production_min",
        )
        m.add_constraints(
            feed_use - animal_slack_hi <= upper_bounds,
            name="GlobalConstraint-animal_production_max",
        )
        m.objective += slack_marginal_cost * (
            animal_slack_lo.sum() + animal_slack_hi.sum()
        )
        logger.info(
            "Added animal feed use slack variables (lower+upper) for %d links "
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

    All (product, feed_category, country) links are constrained. Links
    with zero baseline (no GLEAM entry) get an upper bound of zero --
    structurally analogous to the crop growth cap, which prevents
    introducing new animal products in countries where they were absent
    in the baseline.

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
    """Apply cost-calibration corrections as locally-bounded subsidies / penalties.

    Cost calibration extracts duals at the ±1% hard bound, which represent
    the local marginal-cost gradient *at baseline production*. Applied as
    a flat per-Mha (or per-Mt-DM-feed) correction at any production level
    under L1 stability:

    * a moderate **negative** correction calibrated on a small baseline
      drives runaway expansion (the canonical olive-USA case at
      -0.40 bnUSD/Mha calibrated on 0.04 Mha would grow olive 19x);
    * a **positive** correction (e.g. +346 bnUSD/Mha on tomato:BEL after
      winsorization made greenhouse tomato look cheap per tonne) becomes
      a flat penalty that pushes the LP toward zero production, forcing
      the L1 production-stability penalty to do the anchoring work.

    Symmetric two-tier resolution:

    * **Negative** corrections (subsidies) are stored as
      ``bounded_subsidy_bnusd_per_<unit>`` and applied only on the first
      ``baseline_<...>`` units of dispatch. Beyond baseline the subsidy
      stops contributing.
    * **Positive** corrections (penalties) are stored as
      ``bounded_penalty_bnusd_per_<unit>`` and applied only on dispatch
      *above* ``baseline_<...>``. Up to baseline the penalty is zero.

    Both bounds preserve the calibration's local-gradient interpretation
    exactly and bound the per-link cost impact at
    ``|correction| x baseline``.

    Implementation: for each link with a non-zero correction an
    auxiliary variable ``aux_p`` is introduced.

    * Subsidy (negative ``rate``): ``aux_p in [0, baseline]`` with
      ``aux_p <= link_p``. Objective gains ``rate * aux_p`` so the model
      maximises ``aux_p`` up to ``min(p, baseline)``.
    * Penalty (positive ``rate``): ``aux_p in [0, inf)`` with
      ``aux_p >= link_p - baseline``. Objective gains ``rate * aux_p``
      so the model minimises ``aux_p`` to ``max(0, p - baseline)``.

    Parameters
    ----------
    n : pypsa.Network
        Network containing the model. Expected to have per-link
        attributes ``bounded_subsidy_bnusd_per_<unit>`` and
        ``bounded_penalty_bnusd_per_<unit>`` plus a baseline column
        matching the carrier (``baseline_area_mha`` for crop / grassland;
        ``baseline_feed_use_mt_dm`` for animals).
    carriers : list[str]
        Link carriers to scan; controls which corrections are activated.
    """
    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    for carrier in carriers:
        if carrier == "animal_production":
            sub_attr = "bounded_subsidy_bnusd_per_mt"
            pen_attr = "bounded_penalty_bnusd_per_mt"
            baseline_col = "baseline_feed_use_mt_dm"
        else:
            sub_attr = "bounded_subsidy_bnusd_per_mha"
            pen_attr = "bounded_penalty_bnusd_per_mha"
            baseline_col = "baseline_area_mha"

        carrier_df = links_df[links_df["carrier"] == carrier]
        if baseline_col not in carrier_df.columns:
            continue

        # --- Bounded subsidy branch (negative corrections) ---
        if sub_attr in carrier_df.columns:
            sub = carrier_df[
                (carrier_df[sub_attr] < 0) & (carrier_df[baseline_col] > 0)
            ]
            if not sub.empty:
                link_names = sub.index
                baselines = sub[baseline_col].astype(float).to_numpy()
                rates = sub[sub_attr].astype(float).to_numpy()
                baselines_xr = xr.DataArray(
                    baselines, coords={"name": link_names}, dims="name"
                )
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
                    "Bounded subsidy active on %d %s links (rates min=%.4f, "
                    "max=%.4f, total subsidy budget at baseline = %.2f bnUSD)",
                    len(sub),
                    carrier,
                    rates.min(),
                    rates.max(),
                    float((rates * baselines).sum()),
                )

        # --- Bounded penalty branch (positive corrections) ---
        if pen_attr in carrier_df.columns:
            pen = carrier_df[
                (carrier_df[pen_attr] > 0) & (carrier_df[baseline_col] > 0)
            ]
            if not pen.empty:
                link_names = pen.index
                baselines = pen[baseline_col].astype(float).to_numpy()
                rates = pen[pen_attr].astype(float).to_numpy()
                baselines_xr = xr.DataArray(
                    baselines, coords={"name": link_names}, dims="name"
                )
                rates_xr = xr.DataArray(rates, coords={"name": link_names}, dims="name")
                aux = m.add_variables(
                    lower=0,
                    coords=[link_names],
                    dims=["name"],
                    name=f"{carrier}_bounded_penalty_p",
                )
                m.add_constraints(
                    aux >= link_p.sel(name=link_names) - baselines_xr,
                    name=f"GlobalConstraint-{carrier}_bounded_penalty_ge_p_minus_baseline",
                )
                m.objective += (rates_xr * aux).sum()
                logger.info(
                    "Bounded penalty active on %d %s links (rates min=%.4f, "
                    "max=%.4f, total penalty budget at +1x baseline = %.2f bnUSD)",
                    len(pen),
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
