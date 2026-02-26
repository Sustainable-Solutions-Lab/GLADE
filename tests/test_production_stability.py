# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for production stability helper behavior."""

import numpy as np
import pandas as pd
import xarray as xr

from workflow.scripts.solve_model.production_stability import (
    _compute_stability_deviation,
    _crop_production_and_baselines,
)


def _make_crop_links(baselines, index=None):
    """Build a minimal crop links DataFrame for testing."""
    n = len(baselines)
    if index is None:
        index = [chr(ord("a") + i) for i in range(n)]
    return pd.DataFrame(
        {
            "carrier": ["crop_production"] * n,
            "baseline_production_mt": baselines,
            "efficiency": [1.0] * n,
        },
        index=index,
    )


def test_crop_baseline_filter_excludes_zero_in_hard_mode():
    """Hard-mode helper should keep only links above the baseline floor."""
    links_df = _make_crop_links([0.0, 0.02, 1.5])
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0], coords={"name": ["a", "b", "c"]}, dims="name"
    )

    result = _crop_production_and_baselines(
        link_p, links_df, min_baseline_mt=0.1, include_all_links=False
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["c"]
    np.testing.assert_allclose(baselines.values, [1.5])


def test_crop_penalty_mode_includes_zero_baselines():
    """Penalty-mode helper should include all crop links, including baseline=0."""
    links_df = _make_crop_links([0.0, 2.0], index=["zero", "positive"])
    link_p = xr.DataArray(
        [0.3, 1.1], coords={"name": ["zero", "positive"]}, dims="name"
    )

    result = _crop_production_and_baselines(
        link_p, links_df, min_baseline_mt=0.1, include_all_links=True
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["zero", "positive"]
    np.testing.assert_allclose(baselines.values, [0.0, 2.0])


def test_relative_deviation_uses_floor_for_zero_baselines():
    """Relative deviation should stay finite when baseline is zero."""
    actual = xr.DataArray([0.5, 3.0], coords={"name": ["x", "y"]}, dims="name")
    baselines = xr.DataArray([0.0, 2.0], coords={"name": ["x", "y"]}, dims="name")

    deviation = _compute_stability_deviation(
        actual=actual,
        baselines=baselines,
        deviation_type="relative",
        min_baseline_mt=0.1,
    )

    assert np.all(np.isfinite(deviation.values))
    # x: (0.5 - 0.0) / 0.1 = 5.0;  y: (3.0 - 2.0) / 2.0 = 0.5
    np.testing.assert_allclose(deviation.values, [5.0, 0.5])
