"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Shared intra-year water-period helpers.

The model resolves water in ``T`` equal intra-year periods
(``config["water"]["temporal_resolution"]``). Months (0-based) map to periods by
``month * T // 12`` (equal blocks), matching ``compose_water_supply``. These
helpers place a crop's irrigation demand into those periods by day-overlap of its
growing season, and are used by both the single-crop path
(``build_model.crops``) and the multi-cropping Stage-2 split
(``build_multi_cropping``), so the two stay consistent.
"""

import numpy as np
import pandas as pd

MONTH_LENGTHS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)
MONTH_ENDS = np.cumsum(MONTH_LENGTHS)
MONTH_STARTS = MONTH_ENDS - MONTH_LENGTHS
DAYS_IN_YEAR = float(MONTH_ENDS[-1])


def month_overlaps(start_days: np.ndarray, length_days: np.ndarray) -> np.ndarray:
    """``(N, 12)`` matrix of days each season overlaps each calendar month.

    Vectorised over the ``N`` seasons; wrap-around past new year is handled by
    also intersecting each season with the ``+365``-day copy of every month
    (a season is clipped to at most one year, so two copies suffice). Rows with
    a missing or non-positive season are all-zero.
    """
    start_days = np.asarray(start_days, dtype=float)
    length_days = np.asarray(length_days, dtype=float)
    start = (start_days - 1.0) % DAYS_IN_YEAR
    length = np.clip(length_days, 0.0, DAYS_IN_YEAR)
    end = start + length
    finite = np.isfinite(start) & np.isfinite(length) & (length > 0.0)

    lo = start[:, None]
    hi = end[:, None]
    a = MONTH_STARTS[None, :]
    b = MONTH_ENDS[None, :]
    overlaps = np.clip(np.minimum(hi, b) - np.maximum(lo, a), 0.0, None)
    overlaps += np.clip(
        np.minimum(hi, b + DAYS_IN_YEAR) - np.maximum(lo, a + DAYS_IN_YEAR), 0.0, None
    )
    overlaps[~finite] = 0.0
    return overlaps


def month_to_period_matrix(water_periods: int) -> np.ndarray:
    """``(12, T)`` one-hot map from calendar month to intra-year period."""
    periods = int(water_periods)
    matrix = np.zeros((12, periods))
    matrix[np.arange(12), (np.arange(12) * periods) // 12] = 1.0
    return matrix


def period_demand_shares(
    start_days: np.ndarray, length_days: np.ndarray, water_periods: int
) -> np.ndarray:
    """``(N, T)`` fraction of each season's irrigation demand per period.

    The requirement is apportioned by the days the growing season spends in each
    month, aggregated to periods. A missing or degenerate season falls back to an
    even split across periods. Fully vectorised over the ``N`` seasons.
    """
    periods = int(water_periods)
    overlaps = month_overlaps(start_days, length_days)  # (N, 12)
    shares = overlaps @ month_to_period_matrix(periods)  # (N, T)
    totals = shares.sum(axis=1, keepdims=True)
    even = np.full_like(shares, 1.0 / periods)
    return np.where(totals > 0.0, shares / np.where(totals > 0.0, totals, 1.0), even)


def crop_monthly_shares(
    calendar: pd.DataFrame, crop: str, regions: np.ndarray
) -> np.ndarray:
    """``(N, 12)`` observed monthly demand shares for ``crop`` over ``regions``.

    ``calendar`` is the long MIRCA-OS calendar table (``region, crop, month,
    share``). Regions absent for this crop get an all-zero row (GAEZ fallback
    downstream). The 12 columns are calendar months 1..12.
    """
    regions = np.asarray(regions, dtype=object)
    out = np.zeros((len(regions), 12), dtype=float)
    sub = calendar[calendar["crop"] == crop]
    if sub.empty:
        return out
    wide = (
        sub.pivot_table(index="region", columns="month", values="share", aggfunc="sum")
        .reindex(index=regions, columns=range(1, 13))
        .to_numpy()
    )
    return np.nan_to_num(wide, nan=0.0)


def calendar_period_shares(
    monthly_shares: np.ndarray,
    start_days: np.ndarray,
    length_days: np.ndarray,
    water_periods: int,
) -> np.ndarray:
    """``(N, T)`` period demand shares from an observed monthly calendar.

    ``monthly_shares`` is ``(N, 12)`` of observed per-month growing-area shares
    (MIRCA-OS). Rows with a positive monthly profile are binned into the ``T``
    periods; rows that are all-zero (no observed calendar for that season) fall
    back to the GAEZ growing-season split ``period_demand_shares(start, length)``.
    Fully vectorised.
    """
    periods = int(water_periods)
    monthly = np.asarray(monthly_shares, dtype=float)
    binned = monthly @ month_to_period_matrix(periods)  # (N, T)
    totals = binned.sum(axis=1, keepdims=True)
    observed = totals[:, 0] > 0.0
    fallback = period_demand_shares(start_days, length_days, periods)
    shares = np.where(
        totals > 0.0, binned / np.where(totals > 0.0, totals, 1.0), fallback
    )
    return shares, observed
