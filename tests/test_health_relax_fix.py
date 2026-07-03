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
    n_fixed = fix_nonconvex_segments(m, registry)
    assert n_fixed == 1

    var = m.variables["health_delta_group_0_ra"]
    lower = var.lower.sel(cluster_risk="c0_ra").values
    upper = var.upper.sel(cluster_risk="c0_ra").values
    np.testing.assert_array_equal(lower, [1, 0, 0])
    np.testing.assert_array_equal(upper, [1, 1, 0])

    # The convex label keeps its original free bounds.
    np.testing.assert_array_equal(var.lower.sel(cluster_risk="c1_ra").values, [0, 0, 0])
    np.testing.assert_array_equal(var.upper.sel(cluster_risk="c1_ra").values, [1, 1, 1])
