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


def test_crop_baseline_filter_excludes_zero_in_hard_mode():
    """Hard-mode helper should keep only links above the baseline floor."""
    links_df = pd.DataFrame(
        {
            "carrier": ["crop_production", "crop_production", "crop_production"],
            "baseline_production_mt": [0.0, 0.02, 1.5],
            "efficiency": [1.0, 1.0, 1.0],
        },
        index=["a", "b", "c"],
    )
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0], coords={"name": ["a", "b", "c"]}, dims="name"
    )

    result = _crop_production_and_baselines(
        link_p,
        links_df,
        min_baseline_mt=0.1,
        include_all_links=False,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["c"]
    np.testing.assert_allclose(baselines.values, [1.5])


def test_crop_penalty_mode_includes_zero_baselines():
    """Penalty-mode helper should include all crop links, including baseline=0."""
    links_df = pd.DataFrame(
        {
            "carrier": ["crop_production", "crop_production"],
            "baseline_production_mt": [0.0, 2.0],
            "efficiency": [1.0, 1.0],
        },
        index=["zero", "positive"],
    )
    link_p = xr.DataArray(
        [0.3, 1.1],
        coords={"name": ["zero", "positive"]},
        dims="name",
    )

    result = _crop_production_and_baselines(
        link_p,
        links_df,
        min_baseline_mt=0.1,
        include_all_links=True,
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

    np.testing.assert_allclose(deviation.values, [5.0, 0.5])
