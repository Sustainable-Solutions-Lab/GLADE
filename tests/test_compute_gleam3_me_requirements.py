# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 ME requirement helpers."""

import pandas as pd
import pytest

from workflow.scripts.compute_gleam3_me_requirements import (
    _assign_feed_me,
    _compute_country_me,
)


def test_assign_feed_me_maps_ruminant_grass_and_leaves_to_forage() -> None:
    """Ruminant grass/leaves rows use the forage ME bucket."""
    intakes = pd.DataFrame(
        {
            "Animal": ["Cattle"],
            "feed_category": ["Grass and leaves"],
            "DM.intake": [2.0],
            "LPS": ["Mixed"],
        }
    )
    me_lookup = {("ruminant", "forage"): 9.5}

    result = _assign_feed_me(intakes, me_lookup)

    assert result.loc[0, "feed_ME_MJ"] == pytest.approx(19.0)


def test_compute_country_me_uses_fallback_for_zero_buffalo_feed() -> None:
    """Zero buffalo feed should not emit a zero ME requirement."""
    ci = pd.DataFrame({"Animal": ["Buffalo"], "feed_ME_MJ": [0.0], "LPS": ["Mixed"]})
    cp = pd.DataFrame(
        {
            "Animal": ["Buffalo"],
            "Item": ["Milk"],
            "Element": ["Weight"],
            "Total": [5.0],
            "LPS": ["Mixed"],
        }
    )
    wirsenius = pd.DataFrame(
        {
            "animal_product": ["dairy", "meat-cattle"],
            "region": ["SSA", "SSA"],
            "unit": ["NE_l", "NE_m"],
            "value": [10.0, 20.0],
        }
    )

    me_dict, _f_dict = _compute_country_me(
        ci, cp, wirsenius, "SSA", k_m=1.0, k_g=1.0, k_l=1.0
    )

    assert me_dict["dairy-buffalo"] is None
