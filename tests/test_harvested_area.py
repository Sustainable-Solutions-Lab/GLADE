# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for harvested-area share allocation."""

import pandas as pd
import pytest

from workflow.scripts.build_harvested_area import _shares_for_crop


def test_missing_module_sibling_uses_uniform_share():
    """If a RES06 sibling crop is missing, avoid assigning 100% to one crop."""
    mapping_df = pd.DataFrame(
        {
            "crop_name": ["citrus", "mango"],
            "res06_code": ["FRT", "FRT"],
        }
    )
    production_df = pd.DataFrame(
        {
            "country": ["USA", "IND"],
            "crop": ["citrus", "citrus"],
            "production_tonnes": [100.0, 200.0],
        }
    )

    shares, fallback = _shares_for_crop("citrus", mapping_df, production_df)

    assert shares == {}
    assert fallback == pytest.approx(0.5)


def test_country_specific_shares_when_all_siblings_present():
    """When all RES06 siblings are present, use production-based shares."""
    mapping_df = pd.DataFrame(
        {
            "crop_name": ["citrus", "mango"],
            "res06_code": ["FRT", "FRT"],
        }
    )
    production_df = pd.DataFrame(
        {
            "country": ["USA", "USA", "IND", "IND"],
            "crop": ["citrus", "mango", "citrus", "mango"],
            "production_tonnes": [90.0, 10.0, 20.0, 80.0],
        }
    )

    shares, fallback = _shares_for_crop("citrus", mapping_df, production_df)

    assert shares["USA"] == pytest.approx(0.9)
    assert shares["IND"] == pytest.approx(0.2)
    # Global citrus share = (90 + 20) / (90 + 10 + 20 + 80) = 0.55
    assert fallback == pytest.approx(0.55)


def test_missing_non_food_sibling_is_ignored():
    """Missing non-food siblings should not force uniform module splitting."""
    mapping_df = pd.DataFrame(
        {
            "crop_name": ["maize", "silage-maize"],
            "res06_code": ["MZE", "MZE"],
        }
    )
    production_df = pd.DataFrame(
        {
            "country": ["USA", "IND"],
            "crop": ["maize", "maize"],
            "production_tonnes": [100.0, 200.0],
        }
    )

    shares, fallback = _shares_for_crop(
        "maize",
        mapping_df,
        production_df,
        non_food_crops={"silage-maize"},
    )

    assert shares["USA"] == pytest.approx(1.0)
    assert shares["IND"] == pytest.approx(1.0)
    assert fallback == pytest.approx(1.0)


def test_ovg_module_uses_blended_country_global_shares():
    """OVG crops use blended country/global production shares."""
    mapping_df = pd.DataFrame(
        {
            "crop_name": ["onion", "cabbage", "carrot"],
            "res06_code": ["OVG", "OVG", "OVG"],
        }
    )
    production_df = pd.DataFrame(
        {
            "country": ["USA", "USA", "USA", "IND", "IND", "IND"],
            "crop": ["onion", "cabbage", "carrot", "onion", "cabbage", "carrot"],
            "production_tonnes": [90.0, 10.0, 0.0, 0.0, 100.0, 0.0],
        }
    )

    shares, fallback = _shares_for_crop("onion", mapping_df, production_df)

    # Global onion share = 90 / (90 + 110 + 0) = 0.45
    # USA onion share = 0.7 * 0.9 + 0.3 * 0.45 = 0.765
    # IND onion share = 0.7 * 0.0 + 0.3 * 0.45 = 0.135
    assert shares["USA"] == pytest.approx(0.765)
    assert shares["IND"] == pytest.approx(0.135)
    assert fallback == pytest.approx(0.45)
