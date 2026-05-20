# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the piecewise food-utility helper.

These tests are intentionally formulation-agnostic: they probe the
behaviour of the LP after a real (small) HiGHS solve rather than the
internal variable/constraint structure.  This way the same suite locks
the contract before and after refactoring the formulation.
"""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.solve_model.food_utility import (
    _merge_small_width_blocks,
    add_piecewise_food_utility,
    pop_piecewise_food_utility_value,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_network(
    foods: tuple[str, ...] = ("x",),
    supply_costs: dict[str, float] | None = None,
    p_nom_consume: dict[str, float] | None = None,
) -> pypsa.Network:
    """Build a minimal solvable network with one or more food_consumption links.

    Each food gets a dedicated food bus with a generator (the supply, at
    ``supply_costs[food]`` per Mt) and a consume link routing into a shared
    nutrient bus backed by a high-capacity store sink.  The solver's only
    incentive to consume is the piecewise utility added later.
    """
    supply_costs = supply_costs or dict.fromkeys(foods, 1.0)
    p_nom_consume = p_nom_consume or dict.fromkeys(foods, 1000.0)

    n = pypsa.Network()
    n.set_snapshots(["now"])

    food_carriers = [f"food_{f}" for f in foods]
    n.carriers.add([*food_carriers, "nutrient_protein", "food_consumption"], unit="Mt")

    food_buses = [f"food:{f}:USA" for f in foods]
    n.buses.add(food_buses, carrier=food_carriers)
    n.buses.add(["nutrient:protein:USA"], carrier="nutrient_protein")

    n.generators.add(
        [f"supply:{f}:USA" for f in foods],
        bus=food_buses,
        carrier=food_carriers,
        p_nom=1e6,
        marginal_cost=[supply_costs[f] for f in foods],
    )
    n.stores.add(
        ["sink:protein:USA"],
        bus="nutrient:protein:USA",
        e_nom=1e9,
        e_initial=0.0,
        e_cyclic=False,
        marginal_cost=0.0,
    )
    n.links.add(
        [f"consume:{f}:USA" for f in foods],
        bus0=food_buses,
        bus1=["nutrient:protein:USA"] * len(foods),
        carrier="food_consumption",
        efficiency=1.0,
        p_nom=[p_nom_consume[f] for f in foods],
        marginal_cost=0.0,
        food=list(foods),
        country=["USA"] * len(foods),
    )
    return n


def _write_blocks(
    tmp_path,
    spec: dict[str, list[tuple[float, float]]],
) -> str:
    """Write a blocks CSV from ``{food: [(width, mu), ...]}``."""
    rows = []
    for food, blocks in spec.items():
        for i, (width, mu) in enumerate(blocks):
            rows.append(
                {
                    "food": food,
                    "country": "USA",
                    "block_id": i + 1,
                    "width_mt_per_year": width,
                    "marginal_utility_bnusd_per_mt": mu,
                }
            )
    path = tmp_path / "blocks.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _solve_with_utility(
    n: pypsa.Network, blocks_path: str, min_block_width_mt: float = 0.0
) -> None:
    """Create the linopy model, add piecewise utility, solve with HiGHS."""
    n.optimize.create_model(include_objective_constant=False)
    add_piecewise_food_utility(n, blocks_path, min_block_width_mt=min_block_width_mt)
    status, condition = n.model.solve(solver_name="highs")
    assert (status, condition) == ("ok", "optimal"), (status, condition)


def _link_p(n: pypsa.Network, food: str) -> float:
    return float(
        n.model.variables["Link-p"].solution.sel(name=f"consume:{food}:USA").item()
    )


# ---------------------------------------------------------------------------
# Validation / prep-time tests (no solve required)
# ---------------------------------------------------------------------------


def test_missing_required_columns_raises(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame(
        [{"food": "x", "country": "USA", "block_id": 1, "width_mt_per_year": 1.0}]
    ).to_csv(path, index=False)
    n = _make_network()
    n.optimize.create_model(include_objective_constant=False)
    with pytest.raises(ValueError, match="Missing required utility block columns"):
        add_piecewise_food_utility(n, str(path), min_block_width_mt=0.0)


def test_non_monotonic_mu_raises(tmp_path):
    # Block 2's MU > block 1's MU -> rising marginal utility, invalid.
    blocks_path = _write_blocks(tmp_path, {"x": [(1.0, 1.0), (1.0, 5.0)]})
    n = _make_network()
    n.optimize.create_model(include_objective_constant=False)
    with pytest.raises(ValueError, match="non-increasing"):
        add_piecewise_food_utility(n, blocks_path, min_block_width_mt=0.0)


def test_non_contiguous_block_ids_raises(tmp_path):
    df = pd.DataFrame(
        [
            {
                "food": "x",
                "country": "USA",
                "block_id": 1,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": 5.0,
            },
            {
                "food": "x",
                "country": "USA",
                "block_id": 3,
                "width_mt_per_year": 1.0,
                "marginal_utility_bnusd_per_mt": 3.0,
            },
        ]
    )
    path = tmp_path / "blocks.csv"
    df.to_csv(path, index=False)
    n = _make_network()
    n.optimize.create_model(include_objective_constant=False)
    with pytest.raises(ValueError, match="contiguous"):
        add_piecewise_food_utility(n, str(path), min_block_width_mt=0.0)


def test_missing_link_coverage_logs(tmp_path, caplog):
    # Network has two consume links; blocks file only covers one. The
    # uncovered link earns zero utility (no piecewise variable), which
    # is the documented semantic for foods filtered out at calibration
    # time for negligible baseline consumption.
    blocks_path = _write_blocks(tmp_path, {"x": [(1.0, 5.0)]})
    n = _make_network(foods=("x", "y"))
    n.optimize.create_model(include_objective_constant=False)
    with caplog.at_level("INFO"):
        add_piecewise_food_utility(n, blocks_path, min_block_width_mt=0.0)
    assert any(
        "omits 1 of 2 food_consumption links" in rec.message for rec in caplog.records
    )
    # `food_utility_value` should only carry the covered link.
    names = n.model.variables["food_utility_value"].coords["name"].values.tolist()
    assert any("consume:x:" in n_ for n_ in names)
    assert not any("consume:y:" in n_ for n_ in names)


# ---------------------------------------------------------------------------
# Behavioural solve tests
# ---------------------------------------------------------------------------


def test_diminishing_mu_stops_at_break_even_block(tmp_path):
    """Optimal consumption equals the cumulative width of blocks whose
    marginal utility strictly exceeds the marginal supply cost."""
    # MU: 5, 3, 1; widths 2 each; supply cost 2.
    # Blocks 1 (5>2) and 2 (3>2) fill; block 3 (1<2) and overflow (mu=1<2) skipped.
    blocks_path = _write_blocks(tmp_path, {"x": [(2.0, 5.0), (2.0, 3.0), (2.0, 1.0)]})
    n = _make_network(supply_costs={"x": 2.0})
    _solve_with_utility(n, blocks_path)

    assert _link_p(n, "x") == pytest.approx(4.0, abs=1e-6)


def test_overflow_continues_at_last_block_mu(tmp_path):
    """When supply cost < last-block MU, all blocks fill AND overflow
    grows to p_nom; the overflow region must carry the last-block MU."""
    blocks_path = _write_blocks(tmp_path, {"x": [(1.0, 5.0), (1.0, 3.0)]})
    # Last MU = 3, supply = 2 -> overflow rewarded at +1 per Mt up to p_nom.
    n = _make_network(supply_costs={"x": 2.0}, p_nom_consume={"x": 10.0})
    _solve_with_utility(n, blocks_path)

    # All 2 Mt of blocks + 8 Mt of overflow.
    assert _link_p(n, "x") == pytest.approx(10.0, abs=1e-6)


def test_overflow_skipped_when_last_mu_below_cost(tmp_path):
    """Sanity counterpart: overflow stays at zero when its marginal
    reward does not cover supply cost."""
    blocks_path = _write_blocks(tmp_path, {"x": [(1.0, 5.0), (1.0, 1.0)]})
    n = _make_network(supply_costs={"x": 2.0}, p_nom_consume={"x": 10.0})
    _solve_with_utility(n, blocks_path)

    # Only block 1 (5>2) fills; block 2 and overflow (mu=1<2) skipped.
    assert _link_p(n, "x") == pytest.approx(1.0, abs=1e-6)


def test_per_link_overflow_uses_link_specific_last_mu(tmp_path):
    """Two links with different block counts must each carry their own
    last-block MU on overflow.

    Behavioural guard against the historical "padding leak" bug where the
    overflow MU of a short link inherited the zero padding from a longer
    link's wider block_id range, removing any penalty on overflow.
    """
    # x: 2 blocks (last MU = 3); y: 4 blocks (last MU = 2). Supply = 2.5.
    # x's overflow MU = 3 > 2.5 -> overflows to p_nom.
    # y's overflow MU = 2 < 2.5 -> stops at the last positive-margin block.
    blocks_path = _write_blocks(
        tmp_path,
        {
            "x": [(1.0, 5.0), (1.0, 3.0)],
            "y": [(1.0, 5.0), (1.0, 4.0), (1.0, 3.0), (1.0, 2.0)],
        },
    )
    n = _make_network(
        foods=("x", "y"),
        supply_costs={"x": 2.5, "y": 2.5},
        p_nom_consume={"x": 10.0, "y": 10.0},
    )
    _solve_with_utility(n, blocks_path)

    # x: full 10 Mt (blocks + overflow).  If the bug were present, x.p
    # would cap at 2.0 (overflow MU would be 0 < 2.5).
    assert _link_p(n, "x") == pytest.approx(10.0, abs=1e-6)
    # y: blocks 1..3 fill (5,4,3 > 2.5); block 4 (2 < 2.5) and overflow
    # skipped.
    assert _link_p(n, "y") == pytest.approx(3.0, abs=1e-6)


def test_realized_utility_matches_expected_credit(tmp_path):
    """``pop_piecewise_food_utility_value`` must return the realized
    utility credit (sum_k mu_k * x_k including overflow) consistent with
    the optimal solution."""
    blocks_path = _write_blocks(tmp_path, {"x": [(2.0, 5.0), (2.0, 3.0), (2.0, 1.0)]})
    n = _make_network(supply_costs={"x": 2.0})
    _solve_with_utility(n, blocks_path)

    p = _link_p(n, "x")
    assert p == pytest.approx(4.0, abs=1e-6)

    # Expected realized utility = 2 Mt at mu=5 + 2 Mt at mu=3 = 16.
    util = pop_piecewise_food_utility_value(n)
    assert util == pytest.approx(16.0, rel=1e-6)

    # Objective value cross-check: supply_cost*p - utility = 2*4 - 16 = -8.
    obj = float(n.model.objective.value)
    assert obj == pytest.approx(2.0 * p - util, abs=1e-6)


def test_realized_utility_includes_overflow(tmp_path):
    """The overflow contribution must show up in the realized utility."""
    blocks_path = _write_blocks(tmp_path, {"x": [(1.0, 5.0), (1.0, 3.0)]})
    n = _make_network(supply_costs={"x": 2.0}, p_nom_consume={"x": 10.0})
    _solve_with_utility(n, blocks_path)

    p = _link_p(n, "x")
    assert p == pytest.approx(10.0, abs=1e-6)
    # Realized utility = 1*5 (block 1) + 1*3 (block 2) + 8*3 (overflow at last MU) = 32.
    util = pop_piecewise_food_utility_value(n)
    assert util == pytest.approx(32.0, rel=1e-6)


def test_block_merging_preserves_total_utility_when_all_blocks_fill(tmp_path):
    """When supply cost is below the last MU (so every block fills),
    realized utility = sum(mu_k * w_k), which is mass-conserved by the
    width-weighted merge in ``_merge_small_width_blocks``."""
    # Mixed thin and wide blocks.  Use a low-but-nonzero supply cost (PyPSA
    # refuses to build the linopy model if no component carries any cost
    # at create_model time) and cap p_nom to the sum of widths so every
    # block fills exactly and no overflow region is exercised.
    spec = {"x": [(0.5, 10.0), (0.5, 8.0), (1.0, 4.0)]}
    expected_util = 0.5 * 10.0 + 0.5 * 8.0 + 1.0 * 4.0  # = 13.0
    total_width = sum(w for w, _ in spec["x"])  # = 2.0

    # Solve without merging.
    blocks_path = _write_blocks(tmp_path, spec)
    n = _make_network(supply_costs={"x": 0.5}, p_nom_consume={"x": total_width})
    _solve_with_utility(n, blocks_path, min_block_width_mt=0.0)
    assert _link_p(n, "x") == pytest.approx(total_width, abs=1e-6)
    util_no_merge = pop_piecewise_food_utility_value(n)
    assert util_no_merge == pytest.approx(expected_util, rel=1e-6)

    # Solve with a width floor that forces blocks 1 and 2 to merge.
    n2 = _make_network(supply_costs={"x": 0.5}, p_nom_consume={"x": total_width})
    _solve_with_utility(n2, blocks_path, min_block_width_mt=0.7)
    assert _link_p(n2, "x") == pytest.approx(total_width, abs=1e-6)
    util_merged = pop_piecewise_food_utility_value(n2)
    assert util_merged == pytest.approx(expected_util, rel=1e-6)


# ---------------------------------------------------------------------------
# Unit tests for the merge preprocessing (independent of the LP formulation)
# ---------------------------------------------------------------------------


def test_merge_small_width_blocks_passthrough_when_all_above_floor():
    """No floor or all blocks already wide enough -> unchanged."""
    rows = pd.DataFrame(
        {
            "name": ["L"] * 3,
            "block_id": [1, 2, 3],
            "width_mt_per_year": [1.0, 1.0, 1.0],
            "marginal_utility_bnusd_per_mt": [5.0, 3.0, 1.0],
        }
    )
    out, merged, affected = _merge_small_width_blocks(rows, 0.5)
    assert merged == 0 and affected == 0
    pd.testing.assert_frame_equal(out.reset_index(drop=True), rows)


def test_merge_small_width_blocks_width_weighted_average():
    """Merging blocks 1 and 2 must conserve total width and total utility
    mass (mu * w)."""
    rows = pd.DataFrame(
        {
            "name": ["L", "L", "L"],
            "block_id": [1, 2, 3],
            "width_mt_per_year": [0.2, 0.8, 1.0],
            "marginal_utility_bnusd_per_mt": [10.0, 5.0, 2.0],
        }
    )
    out, merged, affected = _merge_small_width_blocks(rows, 0.5)
    assert merged == 1 and affected == 1
    # Donor (block 1, width 0.2) merges into block 2 (the right neighbour
    # since donor is at the left edge).  Merged width = 1.0, merged mu =
    # (10*0.2 + 5*0.8) / 1.0 = 6.0.  Block 3 stays.
    assert list(out["block_id"]) == [1, 2]
    assert out.loc[0, "width_mt_per_year"] == pytest.approx(1.0)
    assert out.loc[0, "marginal_utility_bnusd_per_mt"] == pytest.approx(6.0)
    assert out.loc[1, "width_mt_per_year"] == pytest.approx(1.0)
    assert out.loc[1, "marginal_utility_bnusd_per_mt"] == pytest.approx(2.0)

    # Total width and total mu*w preserved.
    assert out["width_mt_per_year"].sum() == pytest.approx(2.0)
    assert (out["width_mt_per_year"] * out["marginal_utility_bnusd_per_mt"]).sum() == (
        pytest.approx(10.0 * 0.2 + 5.0 * 0.8 + 2.0 * 1.0)
    )
