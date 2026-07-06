# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the health relax-and-fix segment-fixing helpers."""

from pathlib import Path
import sys

import linopy
import numpy as np
import pandas as pd
import pytest
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.scripts.solve_model.health import (
    _fillup_bounds_for_intake,
    fix_nonconvex_segments,
    run_relax_and_fix,
)


@pytest.mark.parametrize(
    "intake, expected_lower, expected_upper",
    [
        (15.0, [1, 0, 0], [1, 1, 0]),  # inside segment 1
        (-5.0, [0, 0, 0], [1, 0, 0]),  # below first breakpoint -> segment 0
        (35.0, [1, 1, 0], [1, 1, 1]),  # above last breakpoint -> last segment
        (10.0, [1, 0, 0], [1, 1, 0]),  # exactly on interior breakpoint
        (0.0, [0, 0, 0], [1, 0, 0]),  # exactly on first breakpoint
    ],
)
def test_fillup_bounds_for_intake(intake, expected_lower, expected_upper):
    breakpoints = np.array([0.0, 10.0, 20.0, 30.0])
    lower, upper = _fillup_bounds_for_intake(breakpoints, intake, 3)
    np.testing.assert_array_equal(lower, expected_lower)
    np.testing.assert_array_equal(upper, expected_upper)


def test_fix_nonconvex_segments_bounds_active_segment():
    """A relaxed fill-up solution is pinned to the segment of its intake."""
    breakpoints = np.array([0.0, 10.0, 20.0, 30.0])
    labels = pd.Index(["c0_ra", "c1_ra"], name="cluster_risk")
    segments = pd.Index(range(3), name="intake_step_seg")

    m = linopy.Model()
    delta = m.add_variables(
        lower=0, upper=1, coords=[labels, segments], name="health_delta_group_0_ra"
    )
    # Fill-up ordering and a fixed intake per label: c0_ra at 15 (segment 1),
    # c1_ra at 25 (segment 2). Minimizing the fill drives delta to the
    # canonical fill-up pattern.
    delta_rolled = delta.roll({"intake_step_seg": -1})
    m.add_constraints(
        delta_rolled.isel(intake_step_seg=slice(0, -1))
        <= delta.isel(intake_step_seg=slice(0, -1)),
        name="fillup",
    )
    widths = xr.DataArray(np.diff(breakpoints), coords={"intake_step_seg": segments})
    intake = (delta * widths).sum("intake_step_seg")
    targets = xr.DataArray(np.array([15.0, 25.0]), coords={"cluster_risk": labels})
    m.add_constraints(intake >= targets, name="target")
    m.objective = delta.sum()
    _, condition = m.solve(solver_name="highs")
    assert condition == "optimal"

    registry = [
        {
            "delta_name": "health_delta_group_0_ra",
            "intake_breakpoints": breakpoints,
            "nonconvex_labels": ["c0_ra"],
        }
    ]
    n_fixed, changed = fix_nonconvex_segments(m, registry)
    assert n_fixed == 1
    assert changed

    var = m.variables["health_delta_group_0_ra"]
    lower = var.lower.sel(cluster_risk="c0_ra").values
    upper = var.upper.sel(cluster_risk="c0_ra").values
    np.testing.assert_array_equal(lower, [1, 0, 0])
    np.testing.assert_array_equal(upper, [1, 1, 0])

    # The convex label keeps its original free bounds.
    np.testing.assert_array_equal(var.lower.sel(cluster_risk="c1_ra").values, [0, 0, 0])
    np.testing.assert_array_equal(var.upper.sel(cluster_risk="c1_ra").values, [1, 1, 1])

    # Re-fixing from the same solution pins the same segments: no change.
    _, changed = fix_nonconvex_segments(m, registry)
    assert not changed


def _build_s_curve_model(intake_target: float):
    """Build a tiny relax-and-fix model around one S-shaped curve.

    The curve has breakpoints x = [0, 1, 2, 3] and values
    f = [0, -0.1, -1.0, -1.05] (segment slopes -0.1, -0.9, -0.05: genuinely
    non-convex). The objective minimizes f subject to the fill-up ordering
    and a fixed intake, mirroring the monotone role of log(RR) in the health
    objective. For intakes inside the first segment span the LP relaxation
    interpolates across the convex hull and undercuts the true curve value.
    """
    breakpoints = np.array([0.0, 1.0, 2.0, 3.0])
    f_values = np.array([0.0, -0.1, -1.0, -1.05])
    labels = pd.Index(["c0_r"], name="cluster_risk")
    segments = pd.Index(range(3), name="intake_step_seg")

    m = linopy.Model()
    delta = m.add_variables(
        lower=0, upper=1, coords=[labels, segments], name="health_delta_group_0_r"
    )
    delta_rolled = delta.roll({"intake_step_seg": -1})
    m.add_constraints(
        delta_rolled.isel(intake_step_seg=slice(0, -1))
        <= delta.isel(intake_step_seg=slice(0, -1)),
        name="fillup",
    )
    widths = xr.DataArray(np.diff(breakpoints), coords={"intake_step_seg": segments})
    intake = (delta * widths).sum("intake_step_seg")
    m.add_constraints(intake == intake_target, name="target")
    delta_f = xr.DataArray(np.diff(f_values), coords={"intake_step_seg": segments})
    m.objective = (delta * delta_f).sum()

    registry = [
        {
            "delta_name": "health_delta_group_0_r",
            "name_suffix": "0_r",
            "intake_breakpoints": breakpoints,
            "nonconvex_labels": ["c0_r"],
        }
    ]
    return m, registry


def _solve(m):
    return m.solve(solver_name="highs", reformulate_sos="auto")


def test_run_relax_and_fix_falls_back_to_mip():
    """An uncertifiable gap triggers the automatic SOS1 MIP fallback."""
    m, registry = _build_s_curve_model(intake_target=1.0)
    _, condition = _solve(m)
    assert condition == "optimal"
    # The relaxation undercuts the curve: chord across segments 1+2 gives
    # -0.5 at x=1 while the true curve value is -0.1.
    assert m.objective.value == pytest.approx(-0.5)

    _, condition = run_relax_and_fix(
        m,
        registry,
        max_gap=1e-3,
        solve_repair=lambda: _solve(m),
        solve_fallback=lambda: _solve(m),
    )
    assert condition == "optimal"
    # Fallback restored the exact formulation: segment indicators exist and
    # the solution lies exactly on the piecewise curve.
    assert "health_segment_ind_0_r" in m.variables
    assert m.objective.value == pytest.approx(-0.1)
    delta_sol = m.variables["health_delta_group_0_r"].solution
    np.testing.assert_allclose(
        delta_sol.sel(cluster_risk="c0_r").values, [1.0, 0.0, 0.0], atol=1e-6
    )


def test_run_relax_and_fix_certifies_without_fallback():
    """A relaxed solution on the curve certifies in the first repair round."""
    # At x = 2 the fill-up relaxation is exact (delta = [1, 1, 0] is on the
    # curve), so the certified gap is zero and no MIP machinery is added.
    m, registry = _build_s_curve_model(intake_target=2.0)
    _, condition = _solve(m)
    assert condition == "optimal"
    assert m.objective.value == pytest.approx(-1.0)

    def fail_fallback():
        raise AssertionError("fallback must not run when the gap certifies")

    _, condition = run_relax_and_fix(
        m,
        registry,
        max_gap=1e-3,
        solve_repair=lambda: _solve(m),
        solve_fallback=fail_fallback,
    )
    assert condition == "optimal"
    assert "health_segment_ind_0_r" not in m.variables
    assert m.objective.value == pytest.approx(-1.0)
