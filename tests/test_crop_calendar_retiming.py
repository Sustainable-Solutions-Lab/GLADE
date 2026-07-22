"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Unit tests for the IPF retiming of MIRCA crop-calendar shares to WaterGAP's
monthly irrigation requirement (build_mirca_crop_calendar).
"""

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.build_mirca_crop_calendar import retime_shares_to_demand

MONTHS = list(range(1, 13))


def _shares(region, crop, profile):
    profile = np.asarray(profile, dtype=float)
    return pd.DataFrame(
        {
            "region": region,
            "crop": crop,
            "month": MONTHS,
            "share": profile / profile.sum(),
            "area_ha": profile * 100.0,
        }
    )


def _demand(region, profile):
    return pd.DataFrame(
        {
            "region": region,
            "month": MONTHS,
            "irrigation_consumption_mm3": np.asarray(profile, dtype=float),
        }
    )


def test_retiming_matches_demand_shape_and_preserves_rows():
    # Two crops with overlapping flat seasons; demand concentrated late.
    shares = pd.concat(
        [
            _shares("r1", "wheat", [1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1]),
            _shares("r1", "rice", [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0]),
        ],
        ignore_index=True,
    )
    weights = pd.Series({("r1", "wheat"): 60.0, ("r1", "rice"): 60.0}).rename_axis(
        ["region", "crop"]
    )
    # A feasible target: the column sums of an explicit crop x month matrix
    # respecting both seasons (wheat 10 in each of its 6 months; rice
    # 5/10/5/5/5/15/15 over months 4-10), so IPF can converge exactly.
    demand = _demand("r1", [10, 10, 10, 15, 10, 5, 5, 5, 15, 15, 10, 10])

    out = retime_shares_to_demand(shares, weights, demand)

    # Per-crop annual totals preserved exactly: shares sum to 1.
    sums = out.groupby(["region", "crop"])["share"].sum()
    assert np.allclose(sums, 1.0)

    # Region-month totals (share x weight) match the demand shape.
    wide = out.pivot_table(index="crop", columns="month", values="share")
    total = (wide.loc["wheat"] * 60.0 + wide.loc["rice"] * 60.0).to_numpy()
    target = np.array([10, 10, 10, 15, 10, 5, 5, 5, 15, 15, 10, 10], dtype=float)
    assert np.allclose(total, target, atol=1e-3 * 120.0)


def test_structural_zeros_preserved():
    shares = _shares("r1", "wheat", [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1])
    weights = pd.Series({("r1", "wheat"): 10.0}).rename_axis(["region", "crop"])
    # Demand peaks in the monsoon months where wheat does not grow.
    demand = _demand("r1", [1, 1, 1, 1, 5, 20, 30, 20, 5, 1, 1, 1])

    out = retime_shares_to_demand(shares, weights, demand)
    monsoon = out[(out["month"] >= 4) & (out["month"] <= 10)]
    assert (monsoon["share"] == 0.0).all()
    assert np.isclose(out["share"].sum(), 1.0)


def test_zero_weight_and_missing_demand_pass_through():
    shares = pd.concat(
        [
            _shares("r1", "wheat", [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]),
            _shares("r2", "wheat", [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]),
        ],
        ignore_index=True,
    )
    # r1 has no weight; r2 has weight but no demand row at all.
    weights = pd.Series({("r1", "wheat"): 0.0, ("r2", "wheat"): 5.0}).rename_axis(
        ["region", "crop"]
    )
    demand = _demand("r1", [0.0] * 12)  # also below min_demand_mm3

    out = retime_shares_to_demand(shares, weights, demand)
    pd.testing.assert_frame_equal(
        out.reset_index(drop=True), shares.reset_index(drop=True)
    )


def test_unservable_demand_months_left_unmet_but_rows_normalised():
    # Demand exists in months no crop grows: IPF cannot serve them; the
    # profile must still normalise to 1 within the crop's season.
    shares = _shares("r1", "wheat", [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    weights = pd.Series({("r1", "wheat"): 10.0}).rename_axis(["region", "crop"])
    demand = _demand("r1", [1, 1, 0, 0, 0, 30, 0, 0, 0, 0, 0, 0])

    out = retime_shares_to_demand(shares, weights, demand)
    assert np.isclose(out["share"].sum(), 1.0)
    assert out.loc[out["month"] == 6, "share"].item() == 0.0
    # The two feasible months split evenly (equal demand, equal prior).
    jan_feb = out.loc[out["month"].isin([1, 2]), "share"]
    assert np.allclose(jan_feb, 0.5)


def test_multi_region_independence():
    shares = pd.concat(
        [
            _shares("r1", "wheat", [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
            _shares("r2", "wheat", [0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0]),
        ],
        ignore_index=True,
    )
    weights = pd.Series({("r1", "wheat"): 10.0, ("r2", "wheat"): 10.0}).rename_axis(
        ["region", "crop"]
    )
    demand = pd.concat(
        [
            _demand("r1", [3, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
            _demand("r2", [0, 0, 0, 0, 0, 0, 1, 2, 3, 0, 0, 0]),
        ],
        ignore_index=True,
    )

    out = retime_shares_to_demand(shares, weights, demand)
    r1 = out[out["region"] == "r1"].set_index("month")["share"]
    r2 = out[out["region"] == "r2"].set_index("month")["share"]
    assert np.allclose(r1.loc[[1, 2, 3]], np.array([3, 2, 1]) / 6.0)
    assert np.allclose(r2.loc[[7, 8, 9]], np.array([1, 2, 3]) / 6.0)


def test_no_overlap_with_demand_keeps_observed_shares():
    # The crop's whole season falls in months with zero demand: it cannot be
    # retimed and must keep its observed (MIRCA) profile.
    shares = _shares("r1", "wheat", [1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    weights = pd.Series({("r1", "wheat"): 10.0}).rename_axis(["region", "crop"])
    demand = _demand("r1", [0, 0, 0, 0, 0, 10, 20, 10, 0, 0, 0, 0])

    out = retime_shares_to_demand(shares, weights, demand)
    pd.testing.assert_frame_equal(
        out.reset_index(drop=True), shares.reset_index(drop=True)
    )


def test_retiming_shape_error_on_lost_rows():
    # A malformed weights index (unknown region) must not corrupt the output.
    shares = _shares("r1", "wheat", [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    weights = pd.Series({("rX", "wheat"): 1.0}).rename_axis(["region", "crop"])
    demand = _demand("r1", [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    out = retime_shares_to_demand(shares, weights, demand)
    # No active rows -> passthrough.
    pd.testing.assert_frame_equal(
        out.reset_index(drop=True), shares.reset_index(drop=True)
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
