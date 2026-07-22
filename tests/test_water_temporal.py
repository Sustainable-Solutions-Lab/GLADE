# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the intra-year water temporal resolution.

Covers the month -> period mapping and per-period convex-curve aggregation in
``compose_water_supply`` and the crop-demand split in ``build_model.crops``.
Both use ``month * T // 12`` equal blocks and must agree; volume/mass must be
conserved regardless of the number of periods.
"""

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.build_model.crops import period_demand_shares
from workflow.scripts.compose_water_supply import (
    aggregate_months_to_periods,
    build_groundwater_bands,
    month_to_period,
)
from workflow.scripts.water_periods import calendar_period_shares, crop_monthly_shares


def _shares(start_day: float, length_days: float, temporal_resolution: int):
    """Single-season convenience wrapper over the vectorised (N, T) function."""
    return period_demand_shares(
        np.array([start_day]), np.array([length_days]), temporal_resolution
    )[0]


@pytest.mark.parametrize("temporal_resolution", [1, 2, 3, 4, 6, 12])
def test_month_to_period_equal_blocks(temporal_resolution):
    periods = month_to_period(np.arange(1, 13), temporal_resolution)
    assert periods.min() == 0
    assert periods.max() == temporal_resolution - 1
    # Every period spans the same number of whole months.
    counts = np.bincount(periods)
    assert (counts == 12 // temporal_resolution).all()


def _monthly_tiers() -> pd.DataFrame:
    """One region, two ascending-CF tiers per month."""
    months = list(range(1, 13))
    return pd.DataFrame(
        {
            "region": ["r"] * 24,
            "month": months * 2,
            "tier": [0] * 12 + [1] * 12,
            "capacity_mm3": [10.0] * 12 + [5.0] * 12,
            "marginal_cf": [1.0] * 12 + [4.0] * 12,
        }
    )


@pytest.mark.parametrize("temporal_resolution", [1, 2, 3, 4, 6, 12])
def test_aggregate_conserves_volume_and_period_count(temporal_resolution):
    monthly = _monthly_tiers()
    agg = aggregate_months_to_periods(monthly, temporal_resolution)
    assert set(agg["period"].unique()) == set(range(temporal_resolution))
    assert agg["capacity_mm3"].sum() == pytest.approx(monthly["capacity_mm3"].sum())
    # Tiers stay in ascending-CF (merit) order within each period.
    for _, group in agg.groupby("period"):
        assert group["marginal_cf"].is_monotonic_increasing


def test_groundwater_bands_are_annual_per_region():
    # Groundwater is an annual per-region resource (one renewable + one mined band
    # per region), independent of the number of periods -- not split /T.
    surface = aggregate_months_to_periods(_monthly_tiers(), 4)
    gw_tiers = pd.DataFrame(
        {
            "region": ["r", "r"],
            "tier": [0, 1],
            "capacity_mm3": [25.0, 15.0],
            "marginal_cf": [3.0, 9.0],
        }
    )
    agri = pd.Series({"r": 100.0})
    bands = build_groundwater_bands(
        gw_tiers, surface, agri, ceiling_factor=3.0, scarcity_tiers=True
    )
    renewable = bands[bands["source"] == "groundwater_renewable"]
    nonrenewable = bands[bands["source"] == "groundwater_nonrenewable"]
    # Annual bands: renewable = the curve slice volumes; ceiling = 3 * C.
    assert set(bands.columns) == {
        "region",
        "source",
        "band",
        "capacity_mm3",
        "marginal_cf",
    }
    assert renewable["capacity_mm3"].sum() == pytest.approx(40.0)
    assert renewable.sort_values("band")["marginal_cf"].tolist() == [3.0, 9.0]
    assert len(nonrenewable) == 1
    assert nonrenewable["capacity_mm3"].iloc[0] == pytest.approx(300.0)
    assert nonrenewable["marginal_cf"].iloc[0] == 0.0


def test_demand_shares_sum_to_one_and_match_period_mapping():
    # A season wholly inside the first quarter lands entirely in period 0.
    shares = _shares(1.0, 59.0, 4)
    assert shares.sum() == pytest.approx(1.0)
    assert shares[0] == pytest.approx(1.0)

    # A season spanning two quarters splits between them, nothing elsewhere.
    shares = _shares(91.0, 183.0, 4)
    assert shares.sum() == pytest.approx(1.0)
    assert shares[1] > 0.4 and shares[2] > 0.4
    assert shares[0] < 0.02 and shares[3] < 0.02


def test_demand_shares_wraps_year_boundary():
    # Nov -> Feb season spends time in period 3 (Oct-Dec) and period 0 (Jan-Mar).
    shares = _shares(305.0, 120.0, 4)
    assert shares.sum() == pytest.approx(1.0)
    assert shares[3] > 0.3 and shares[0] > 0.2
    assert shares[1] == pytest.approx(0.0) and shares[2] == pytest.approx(0.0)


def test_demand_shares_missing_season_even_split():
    shares = _shares(np.nan, np.nan, 4)
    assert np.allclose(shares, 0.25)


def test_demand_share_periods_agree_with_compose_mapping():
    # A short season inside a single calendar month must land in the same period
    # compose assigns that month to (both use ``month * T // 12``).
    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_start = np.concatenate([[1], 1 + np.cumsum(month_lengths)[:-1]])
    for temporal_resolution in [2, 3, 4, 6, 12]:
        for month in range(1, 13):
            start_day = float(month_start[month - 1] + 3)  # safely inside the month
            shares = _shares(start_day, 10.0, temporal_resolution)
            expected = month_to_period(np.array([month]), temporal_resolution)[0]
            assert np.argmax(shares) == expected, (temporal_resolution, month)


def test_calendar_period_shares_bins_observed_months():
    # A crop grown entirely in Jan-Mar (rabi) must land in period 0 at T=4,
    # overriding a GAEZ summer season; magnitude preserved (shares sum to 1).
    monthly = np.zeros((1, 12))
    monthly[0, 0] = monthly[0, 1] = monthly[0, 2] = 1 / 3  # Jan, Feb, Mar
    shares, observed = calendar_period_shares(
        monthly, np.array([180.0]), np.array([120.0]), 4
    )
    assert observed[0]
    assert shares[0, 0] == pytest.approx(1.0)
    assert shares[0].sum() == pytest.approx(1.0)


def test_calendar_period_shares_falls_back_to_gaez():
    # An all-zero monthly profile (no MIRCA observation) falls back to the GAEZ
    # growing-season split.
    monthly = np.zeros((1, 12))
    shares, observed = calendar_period_shares(
        monthly, np.array([1.0]), np.array([59.0]), 4
    )
    assert not observed[0]
    gaez = period_demand_shares(np.array([1.0]), np.array([59.0]), 4)
    assert np.allclose(shares, gaez)


def test_crop_monthly_shares_pivots_and_fills_missing_regions():
    calendar = pd.DataFrame(
        {
            "region": ["r1", "r1", "r2"],
            "crop": ["wheat", "wheat", "wheat"],
            "month": [1, 2, 7],
            "share": [0.6, 0.4, 1.0],
        }
    )
    out = crop_monthly_shares(calendar, "wheat", np.array(["r1", "r2", "r3"]))
    assert out.shape == (3, 12)
    assert out[0, 0] == pytest.approx(0.6) and out[0, 1] == pytest.approx(0.4)
    assert out[1, 6] == pytest.approx(1.0)  # r2, July
    assert out[2].sum() == pytest.approx(0.0)  # r3 absent -> all zero (fallback)
    # A crop with no rows returns an all-zero matrix.
    assert crop_monthly_shares(calendar, "maize", np.array(["r1"])).sum() == 0.0
