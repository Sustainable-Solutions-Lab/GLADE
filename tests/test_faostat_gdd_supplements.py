# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for FAOSTAT dietary supplements configuration."""

from workflow.scripts.prepare_faostat_gdd_supplements import FAO_ITEMS


def test_eggs_are_sourced_from_faostat_supplements() -> None:
    """Egg baseline intake should be available from FAOSTAT FBS item 2744."""
    assert FAO_ITEMS["eggs"] == [2744]
