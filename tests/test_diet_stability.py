# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for diet_stability cost evaluation.

The constraint-building path (``add_diet_stability_constraints``) needs a real
linopy model to exercise; here we cover the post-hoc cost evaluator that the
objective-breakdown extraction relies on.
"""

import pandas as pd
import pypsa

from workflow.scripts.solve_model.diet_stability import (
    evaluate_diet_stability_cost,
)


def _make_network(
    consumption_mt: list[float], baseline_mt: list[float]
) -> pypsa.Network:
    """Build a minimal pypsa.Network with food_consumption links and dispatch."""
    n = pypsa.Network()
    n.set_snapshots(["now"])
    names = [f"consume:f{i}:CTY" for i in range(len(consumption_mt))]
    # Single bus is fine; we never call n.optimize, just inspect static + dynamic.
    n.add("Bus", "food:f0:CTY")
    for nm in names:
        n.links.static.loc[nm, "carrier"] = "food_consumption"
        n.links.static.loc[nm, "bus0"] = "food:f0:CTY"
    p0 = pd.DataFrame(
        {nm: [val] for nm, val in zip(names, consumption_mt)}, index=n.snapshots
    )
    n.links.dynamic["p0"] = p0
    matched = pd.DataFrame({"name": names, "target_mt": baseline_mt})
    return n, matched


def test_disabled_returns_zero():
    n, matched = _make_network([1.0, 2.0], [1.0, 2.0])
    cfg = {"enabled": False}
    assert evaluate_diet_stability_cost(n, matched, cfg) == 0.0


def test_l1_absolute_no_deviation():
    n, matched = _make_network([1.0, 2.0], [1.0, 2.0])
    cfg = {
        "enabled": True,
        "penalty_mode": "l1",
        "deviation_type": "absolute",
        "food_l1_cost": 10.0,
        "min_baseline": 1e-6,
    }
    assert evaluate_diet_stability_cost(n, matched, cfg) == 0.0


def test_l1_absolute_symmetric_deviation():
    # Deviations: +0.5, -0.5 => |.|.sum() = 1.0
    n, matched = _make_network([1.5, 1.5], [1.0, 2.0])
    cfg = {
        "enabled": True,
        "penalty_mode": "l1",
        "deviation_type": "absolute",
        "food_l1_cost": 10.0,
        "min_baseline": 1e-6,
    }
    assert evaluate_diet_stability_cost(n, matched, cfg) == 10.0


def test_l1_relative_uses_min_baseline_floor():
    # Baseline 0 with min_baseline floor 1e-3, consumption 0.01 -> rel dev = 10
    n, matched = _make_network([0.01], [0.0])
    cfg = {
        "enabled": True,
        "penalty_mode": "l1",
        "deviation_type": "relative",
        "food_l1_cost": 1.0,
        "min_baseline": 1e-3,
    }
    cost = evaluate_diet_stability_cost(n, matched, cfg)
    assert cost == 10.0


def test_quadratic_absolute():
    # Deviations: +1, -1 => (.)^2 .sum = 2 => 0.5 * 5 * 2 = 5
    n, matched = _make_network([2.0, 1.0], [1.0, 2.0])
    cfg = {
        "enabled": True,
        "penalty_mode": "quadratic",
        "deviation_type": "absolute",
        "quadratic_cost": 5.0,
        "min_baseline": 1e-6,
    }
    assert evaluate_diet_stability_cost(n, matched, cfg) == 5.0
