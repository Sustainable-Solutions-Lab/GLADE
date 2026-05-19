# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Diet stability constraints and penalties for the food systems model.

Mirrors :mod:`workflow.scripts.solve_model.production_stability`, but anchors
**food consumption** (the per-link dispatch on ``food_consumption`` links) to
the per-(food, country) targets derived from the same ``baseline_diet.csv``
that :func:`fix_food_consumption_to_baseline` consumes when
``validation.enforce_baseline_diet`` is true.

Land-side and animal-feed-side ``production_stability`` only anchor what is
*produced*; they leave the model free to reroute the same hectares toward a
very different *diet* (e.g. less feed-grain → more legumes/whole-grains for
direct human consumption). Diet stability fills that gap by penalising
deviations of consumption from the observed baseline-year diet.

Two penalty modes are supported, mirroring the production_stability spelling:

- **l1**: linear absolute-value penalty on ``|p - baseline|`` per
  food_consumption link (units: Mt/yr deviation, scaled by ``food_l1_cost``
  bn USD/Mt).
- **quadratic**: ``0.5 * quadratic_cost * sum((p - baseline)**2)``.

The baseline ``target_mt`` per link is *always* the observed-diet anchor (for
the configured ``baseline_year``) and is independent of the scenario's GHG/YLL
pricing, so diet stability composes cleanly with piecewise consumer-values
utility (the latter expresses the *revealed-preference* shape, while diet
stability adds a hard-currency substitution cost).
"""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)


def add_diet_stability_constraints(
    n: pypsa.Network,
    matched_baseline: pd.DataFrame,
    diet_stability_cfg: dict,
) -> None:
    """Add diet-stability penalties to the linopy model.

    Parameters
    ----------
    n : pypsa.Network
        Network whose ``n.model`` already exists (call after
        ``n.optimize.create_model``).
    matched_baseline : pd.DataFrame
        Output of ``_match_baseline_to_consume_links`` with columns
        ``name`` (food_consumption link name) and ``target_mt`` (Mt/yr
        baseline consumption).
    diet_stability_cfg : dict
        ``validation.diet_stability`` config block.
    """
    if not diet_stability_cfg["enabled"]:
        return

    if matched_baseline is None or matched_baseline.empty:
        logger.warning(
            "diet_stability is enabled but no baseline diet matched any "
            "food_consumption links; skipping."
        )
        return

    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
    if consume_links.empty:
        logger.info("No food_consumption links present; skipping diet stability.")
        return

    targets = (
        matched_baseline.set_index("name")["target_mt"]
        .reindex(consume_links.index)
        .fillna(0.0)
        .astype(float)
    )

    min_baseline = float(diet_stability_cfg["min_baseline"])
    if min_baseline <= 0:
        raise ValueError(
            "validation.diet_stability.min_baseline must be > 0; " f"got {min_baseline}"
        )
    deviation_type = diet_stability_cfg["deviation_type"]
    penalty_mode = diet_stability_cfg["penalty_mode"]

    link_p = n.model.variables["Link-p"].sel(snapshot="now", name=consume_links.index)
    baselines = xr.DataArray(
        targets.to_numpy(),
        coords={"name": consume_links.index},
        dims="name",
    )

    if deviation_type == "relative":
        denominator = xr.where(baselines > min_baseline, baselines, min_baseline)
        deviation = (link_p - baselines) / denominator
    elif deviation_type == "absolute":
        deviation = link_p - baselines
    else:
        raise ValueError(
            f"validation.diet_stability.deviation_type must be 'absolute' or "
            f"'relative', got {deviation_type!r}"
        )

    if penalty_mode == "l1":
        cost = float(diet_stability_cfg["food_l1_cost"])
        abs_dev = n.model.add_variables(
            lower=0,
            coords=[consume_links.index],
            dims=["name"],
            name="diet_stability_abs_dev",
        )
        n.model.add_constraints(
            abs_dev >= deviation,
            name="GlobalConstraint-diet_stability_pos",
        )
        n.model.add_constraints(
            abs_dev >= -deviation,
            name="GlobalConstraint-diet_stability_neg",
        )
        n.model.objective += cost * abs_dev.sum()
        logger.info(
            "Added %d per-(food, country) diet-stability L1 penalties "
            "(cost=%.4f bn USD/Mt, mode=%s)",
            len(consume_links),
            cost,
            deviation_type,
        )
    elif penalty_mode == "quadratic":
        cost = float(diet_stability_cfg["quadratic_cost"])
        dev = n.model.add_variables(
            coords=[consume_links.index],
            dims=["name"],
            name="diet_stability_dev",
        )
        n.model.add_constraints(
            dev == deviation,
            name="GlobalConstraint-diet_stability_dev",
        )
        n.model.objective += 0.5 * cost * (dev * dev).sum()
        logger.info(
            "Added %d per-(food, country) diet-stability quadratic penalties "
            "(cost=%.4f bn USD per (Mt)^2, mode=%s)",
            len(consume_links),
            cost,
            deviation_type,
        )
    else:
        raise ValueError(
            f"validation.diet_stability.penalty_mode must be 'l1' or "
            f"'quadratic', got {penalty_mode!r}"
        )


def evaluate_diet_stability_cost(
    n: pypsa.Network,
    matched_baseline: pd.DataFrame,
    diet_stability_cfg: dict,
) -> float:
    """Re-evaluate the diet-stability cost from a solved network.

    Used for objective-breakdown bookkeeping. Returns the L1 (or quadratic)
    penalty contribution to the objective in bn USD/yr; 0.0 when the feature
    is disabled.
    """
    if not diet_stability_cfg["enabled"]:
        return 0.0
    if matched_baseline is None or matched_baseline.empty:
        return 0.0

    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
    if consume_links.empty:
        return 0.0

    targets = (
        matched_baseline.set_index("name")["target_mt"]
        .reindex(consume_links.index)
        .fillna(0.0)
        .astype(float)
        .to_numpy()
    )
    actual = (
        n.links.dynamic["p0"]
        .iloc[-1]
        .reindex(consume_links.index)
        .fillna(0.0)
        .to_numpy()
    )

    min_baseline = float(diet_stability_cfg["min_baseline"])
    deviation_type = diet_stability_cfg["deviation_type"]

    if deviation_type == "relative":
        denominator = np.where(targets > min_baseline, targets, min_baseline)
        dev = (actual - targets) / denominator
    else:
        dev = actual - targets

    if diet_stability_cfg["penalty_mode"] == "l1":
        return float(diet_stability_cfg["food_l1_cost"]) * float(np.abs(dev).sum())
    if diet_stability_cfg["penalty_mode"] == "quadratic":
        return (
            0.5 * float(diet_stability_cfg["quadratic_cost"]) * float((dev * dev).sum())
        )
    return 0.0
