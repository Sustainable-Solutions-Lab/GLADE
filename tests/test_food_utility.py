# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the piecewise food-utility helper."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.solve_model.food_utility import add_piecewise_food_utility


def _make_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.carriers.add(
        ["food_x", "food_y", "food_consumption", "nutrient_protein"], unit="Mt"
    )
    n.buses.add(
        ["food:x:USA", "food:y:USA", "nutrient:protein:USA"],
        carrier=["food_x", "food_y", "nutrient_protein"],
    )
    n.links.add(
        ["consume:x:USA", "consume:y:USA"],
        bus0=["food:x:USA", "food:y:USA"],
        bus1=["nutrient:protein:USA", "nutrient:protein:USA"],
        carrier="food_consumption",
        efficiency=1.0,
        p_nom=10.0,
        marginal_cost=0.001,
        food=["x", "y"],
        country=["USA", "USA"],
    )
    return n


def test_overflow_utility_uses_per_link_last_block(tmp_path):
    """A link with fewer blocks than the global max must still get its own
    last-block marginal utility on the overflow term — not the zero padding
    from a wider link's block_id range.

    The padded-overflow bug let the LP route all consumption of negative-mu
    foods through overflow at zero penalty, completely bypassing the
    piecewise schedule for shorter links.
    """
    # x has 2 blocks (last mu = -2.0); y has 4 blocks (last mu = -4.0).
    # The global max block_id is 4, so x's rows 3 and 4 are padding.
    blocks = pd.DataFrame(
        [
            {
                "food": "x",
                "country": "USA",
                "block_id": 1,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -1.0,
            },
            {
                "food": "x",
                "country": "USA",
                "block_id": 2,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -2.0,
            },
            {
                "food": "y",
                "country": "USA",
                "block_id": 1,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -1.0,
            },
            {
                "food": "y",
                "country": "USA",
                "block_id": 2,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -2.0,
            },
            {
                "food": "y",
                "country": "USA",
                "block_id": 3,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -3.0,
            },
            {
                "food": "y",
                "country": "USA",
                "block_id": 4,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": -4.0,
            },
        ]
    )
    path = tmp_path / "blocks.csv"
    blocks.to_csv(path, index=False)

    n = _make_network()
    n.optimize.create_model()
    add_piecewise_food_utility(n, str(path), min_block_width_mt=0.0)

    # The overflow utility coordinate per link must equal the link's own
    # last-block marginal utility, not the global max-block padding (0.0).
    from workflow.scripts.solve_model.food_utility import FOOD_UTILITY_COEFFS

    _, overflow_utilities = FOOD_UTILITY_COEFFS[id(n.model)]
    by_name = {
        name: float(overflow_utilities.sel(name=name).item())
        for name in overflow_utilities["name"].values
    }

    assert by_name["consume:x:USA"] == pytest.approx(-2.0), (
        "Overflow utility for the shorter link should be its own last block's "
        f"mu (-2.0), not {by_name['consume:x:USA']} (padding leak)"
    )
    assert by_name["consume:y:USA"] == pytest.approx(-4.0)
