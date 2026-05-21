# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Diet-side deviation penalty constraints for the food systems model.

Anchors per-(food, country) ``food_consumption`` link dispatch toward the
observed baseline-year diet (the same matched_baseline that
:func:`fix_food_consumption_to_baseline` consumes when
``validation.enforce_baseline_diet`` is true).

Land-side and feed-side penalties only anchor what is *produced*; they leave
the model free to reroute the same hectares toward a different *diet* (e.g.
less feed grain -> more legumes for direct human consumption). The diet
component fills that gap by penalising consumption deviations.

The shared ``penalty_mode`` and ``deviation_type`` from the parent
``deviation_penalty`` block apply:

- ``l1``: linear absolute-value penalty per link, scaled by
  ``deviation_penalty.diet.l1_cost`` (bn USD/Mt).
- ``quadratic``: ``0.5 * deviation_penalty.quadratic_cost * sum((p - baseline)^2)``.

The baseline ``target_mt`` per link is always the observed-diet anchor for
the configured ``baseline_year``, independent of the scenario's GHG/YLL
pricing, so diet penalties compose cleanly with piecewise consumer-values
utility.
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
    dp_cfg: dict,
) -> None:
    """Add diet-component deviation penalty constraints.

    Parameters
    ----------
    n : pypsa.Network
        Network whose ``n.model`` already exists.
    matched_baseline : pd.DataFrame
        Output of ``_match_baseline_to_consume_links`` with columns
        ``name`` and ``target_mt``.
    dp_cfg : dict
        The resolved ``deviation_penalty`` block. ``l1_cost`` must already
        be numeric (see ``resolve_calibrated_l1_costs``).
    """
    if not dp_cfg["enabled"]:
        return
    diet_cfg = dp_cfg["diet"]
    if not diet_cfg["enabled"]:
        return

    if matched_baseline is None or matched_baseline.empty:
        logger.warning(
            "deviation_penalty.diet is enabled but no baseline diet matched "
            "any food_consumption links; skipping."
        )
        return

    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
    if consume_links.empty:
        logger.info("No food_consumption links present; skipping diet penalty.")
        return

    targets = (
        matched_baseline.set_index("name")["target_mt"]
        .reindex(consume_links.index)
        .fillna(0.0)
        .astype(float)
    )

    min_baseline = float(diet_cfg["min_baseline"])
    if min_baseline <= 0:
        raise ValueError(
            f"deviation_penalty.diet.min_baseline must be > 0; got {min_baseline}"
        )
    deviation_type = dp_cfg["deviation_type"]
    penalty_mode = dp_cfg["penalty_mode"]

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
            f"deviation_penalty.deviation_type must be 'absolute' or "
            f"'relative', got {deviation_type!r}"
        )

    if penalty_mode == "l1":
        cost = float(diet_cfg["l1_cost"])
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
            "Added %d per-(food, country) diet L1 penalties "
            "(cost=%.4f bn USD/Mt, mode=%s)",
            len(consume_links),
            cost,
            deviation_type,
        )
    elif penalty_mode == "quadratic":
        cost = float(dp_cfg["quadratic_cost"])
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
            "Added %d per-(food, country) diet quadratic penalties "
            "(cost=%.4f bn USD per (Mt)^2, mode=%s)",
            len(consume_links),
            cost,
            deviation_type,
        )
    else:
        raise ValueError(
            f"deviation_penalty.penalty_mode 'hard' is not supported for diet; "
            f"set deviation_penalty.diet.enabled=false or use enforce_baseline_diet. "
            f"Got penalty_mode={penalty_mode!r}"
        )


def evaluate_diet_stability_cost(
    n: pypsa.Network,
    matched_baseline: pd.DataFrame,
    dp_cfg: dict,
) -> float:
    """Re-evaluate the diet deviation cost from a solved network.

    Used for objective-breakdown bookkeeping. Returns the L1 (or quadratic)
    penalty contribution to the objective in bn USD/yr; 0.0 when disabled.
    """
    if not dp_cfg["enabled"]:
        return 0.0
    diet_cfg = dp_cfg["diet"]
    if not diet_cfg["enabled"]:
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

    min_baseline = float(diet_cfg["min_baseline"])
    deviation_type = dp_cfg["deviation_type"]

    if deviation_type == "relative":
        denominator = np.where(targets > min_baseline, targets, min_baseline)
        dev = (actual - targets) / denominator
    else:
        dev = actual - targets

    penalty_mode = dp_cfg["penalty_mode"]
    if penalty_mode == "l1":
        return float(diet_cfg["l1_cost"]) * float(np.abs(dev).sum())
    if penalty_mode == "quadratic":
        return 0.5 * float(dp_cfg["quadratic_cost"]) * float((dev * dev).sum())
    return 0.0
