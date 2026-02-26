# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for prepare_fao_edible_portion exceptions."""

from workflow.scripts.prepare_fao_edible_portion import EDIBLE_PORTION_EXCEPTIONS


def test_rapeseed_is_forced_full_edible_portion() -> None:
    """Rapeseed edible coefficient must be overridden to avoid double-counting."""
    assert "rapeseed" in EDIBLE_PORTION_EXCEPTIONS
