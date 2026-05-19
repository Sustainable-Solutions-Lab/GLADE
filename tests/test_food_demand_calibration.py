# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for compute_food_demand_calibration."""

import numpy as np
import pypsa
import pytest

from workflow.scripts.compute_food_demand_calibration import compute_calibration


def _build_network(
    *,
    consumption: float,
    pos_slack: float = 0.0,
    neg_slack: float = 0.0,
) -> pypsa.Network:
    """Build a minimal solved network for one food with prescribed flows.

    The food bus balances as
    ``real_supply + pos_slack - neg_absorbed = consumption``,
    so ``real_supply = consumption - (pos_slack - neg_absorbed)``.
    """
    n = pypsa.Network()
    n.set_snapshots(["now"])

    n.carriers.add(
        ["food_x", "food_consumption", "slack_positive_food", "slack_negative_food"]
    )
    n.buses.add(["food:x:USA", "nutrient:protein:USA"], carrier="food_x")
    n.links.add(
        ["consume:x:USA"],
        bus0=["food:x:USA"],
        bus1=["nutrient:protein:USA"],
        carrier="food_consumption",
        efficiency=[1.0],
        food=["x"],
    )
    n.links.dynamic["p0"].loc["now", "consume:x:USA"] = consumption

    n.generators.add(
        ["slack_pos:x:USA", "slack_neg:x:USA"],
        bus="food:x:USA",
        carrier=["slack_positive_food", "slack_negative_food"],
        food=["x", "x"],
    )
    n.generators.dynamic["p"].loc["now", "slack_pos:x:USA"] = pos_slack
    # The negative-slack generator absorbs mass: dispatch is reported as a
    # negative number whose absolute value is the absorbed amount.
    n.generators.dynamic["p"].loc["now", "slack_neg:x:USA"] = -neg_slack

    return n


def test_zero_slack_gives_unity_multiplier():
    n = _build_network(consumption=1.0)
    cal = compute_calibration(n, min_multiplier=0.5, max_multiplier=2.0)
    np.testing.assert_allclose(cal.loc["x", "multiplier"], 1.0)


@pytest.mark.parametrize(
    ("consumption", "pos", "neg", "expected"),
    [
        # Positive net slack (shortage) -> multiplier shrinks.
        (1.0, 0.1, 0.0, 0.9),
        # Negative net slack (excess) -> multiplier grows.
        (1.0, 0.0, 0.1, 1.1),
        # Both sides present -> uses signed net.
        (2.0, 0.3, 0.1, (2.0 - (0.3 - 0.1)) / 2.0),
    ],
)
def test_multiplier_uses_subtractive_form(consumption, pos, neg, expected):
    """Verify multiplier = (consumption - net_slack) / consumption.

    The food bus balances as supply + pos - neg = consumption, so the
    actual sustainable supply is consumption - (pos - neg). The
    multiplier rescales the baseline target to that supply.
    """
    n = _build_network(consumption=consumption, pos_slack=pos, neg_slack=neg)
    cal = compute_calibration(n, min_multiplier=0.0, max_multiplier=10.0)
    np.testing.assert_allclose(cal.loc["x", "multiplier"], expected)


def test_multiplier_clipping():
    """Extreme shortages get clipped at min_multiplier."""
    n = _build_network(consumption=1.0, pos_slack=0.9)  # raw = 0.1
    cal = compute_calibration(n, min_multiplier=0.5, max_multiplier=2.0)
    np.testing.assert_allclose(cal.loc["x", "multiplier"], 0.5)
