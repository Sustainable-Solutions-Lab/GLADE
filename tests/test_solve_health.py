# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for solve-time health helpers."""

import pytest

from workflow.scripts.solve_model.health import (
    MAX_HEALTH_TEMPORAL_GAP_YEARS,
    _check_health_temporal_gap,
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
