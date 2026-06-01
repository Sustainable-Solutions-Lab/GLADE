# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for solve-time health helpers."""

import numpy as np
import pytest
import xarray as xr

from workflow.scripts.solve_model.health import (
    MAX_HEALTH_TEMPORAL_GAP_YEARS,
    _check_health_temporal_gap,
    _convex_cluster_risk_mask,
)


def _make_log_rr(curves: dict[str, list[float]], intakes: list[float]) -> xr.DataArray:
    """Build a (cluster_risk, intake_step, cause) array for a single cause."""
    labels = list(curves)
    arr = np.array([[curves[label]] for label in labels])  # (cr, cause=1, step)
    arr = arr.transpose(0, 2, 1)  # (cr, step, cause)
    return xr.DataArray(
        arr,
        dims=["cluster_risk", "intake_step", "cause"],
        coords={
            "cluster_risk": labels,
            "intake_step": range(len(intakes)),
            "cause": ["CHD"],
        },
    )


def _intake(values: list[float]) -> xr.DataArray:
    return xr.DataArray(
        values, dims=["intake_step"], coords={"intake_step": range(len(values))}
    )


def test_temporal_gap_zero_passes():
    _check_health_temporal_gap(2020, 2020)


def test_temporal_gap_at_boundary_passes():
    _check_health_temporal_gap(2020, 2020 + MAX_HEALTH_TEMPORAL_GAP_YEARS)
    _check_health_temporal_gap(2020, 2020 - MAX_HEALTH_TEMPORAL_GAP_YEARS)


def test_temporal_gap_beyond_boundary_raises():
    with pytest.raises(ValueError, match="gap "):
        _check_health_temporal_gap(2020, 2020 + MAX_HEALTH_TEMPORAL_GAP_YEARS + 1)
    with pytest.raises(ValueError, match="gap "):
        _check_health_temporal_gap(2020, 2020 - MAX_HEALTH_TEMPORAL_GAP_YEARS - 1)


def test_convexity_mask_flags_protective_convex_curve():
    # Decreasing curve with diminishing returns (slopes rising toward 0): convex.
    intakes = [0.0, 100.0, 200.0, 300.0]
    log_rr = _make_log_rr({"c0_rfruits": [0.0, -0.20, -0.32, -0.36]}, intakes)
    mask = _convex_cluster_risk_mask(log_rr, _intake(intakes), tol=1e-3)
    assert mask["c0_rfruits"] is True


def test_convexity_mask_flags_s_shaped_curve_nonconvex():
    # Returns accelerate in the middle (slope dips more negative): non-convex.
    intakes = [0.0, 100.0, 200.0, 300.0]
    log_rr = _make_log_rr({"c0_rnuts": [0.0, -0.02, -0.30, -0.34]}, intakes)
    mask = _convex_cluster_risk_mask(log_rr, _intake(intakes), tol=1e-3)
    assert mask["c0_rnuts"] is False


def test_convexity_mask_linear_curve_is_convex():
    # A linear ramp (e.g. de-plateaued red meat) is convex -> LP-eligible.
    intakes = [0.0, 100.0, 200.0, 300.0]
    log_rr = _make_log_rr({"c0_rred_meat": [0.0, 0.08, 0.16, 0.24]}, intakes)
    mask = _convex_cluster_risk_mask(log_rr, _intake(intakes), tol=1e-3)
    assert mask["c0_rred_meat"] is True


def test_convexity_mask_requires_all_causes_convex():
    # The segment deltas are shared across a risk's causes, so a single
    # non-convex cause must make the whole (cluster, risk) pair MIP.
    intakes = [0.0, 100.0, 200.0, 300.0]
    arr = np.array(
        [
            [  # cluster_risk 0; columns: (convex cause, non-convex cause)
                [0.0, 0.0],
                [-0.20, -0.02],
                [-0.32, -0.30],
                [-0.36, -0.34],
            ]
        ]
    )
    log_rr = xr.DataArray(
        arr,
        dims=["cluster_risk", "intake_step", "cause"],
        coords={
            "cluster_risk": ["c0_rwhole_grains"],
            "intake_step": range(4),
            "cause": ["CHD", "CRC"],
        },
    )
    mask = _convex_cluster_risk_mask(log_rr, _intake(intakes), tol=1e-3)
    assert mask["c0_rwhole_grains"] is False
