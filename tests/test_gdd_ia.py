# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for GDD-IA source selection."""

import pytest

from workflow.scripts.diet.gdd_ia import closest_gdd_ia_release_year


@pytest.mark.parametrize(
    ("reference_year", "expected"),
    [
        (2015, 2015),
        (2017, 2015),
        (2018, 2020),
    ],
)
def test_closest_gdd_ia_release_year(reference_year, expected):
    assert closest_gdd_ia_release_year(reference_year) == expected
