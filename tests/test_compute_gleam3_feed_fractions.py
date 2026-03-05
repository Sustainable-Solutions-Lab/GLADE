# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 feed-fraction computation."""

import pandas as pd
import pytest

from workflow.scripts.compute_gleam3_feed_fractions import (
    _compute_byproduct_fractions,
    _estimate_byproduct_volumes,
)


def test_estimate_byproduct_volumes_includes_all_countries() -> None:
    """Countries with no crop rows still get zero-volume byproduct rows."""
    crop_production = pd.DataFrame(
        {
            "country": ["USA"],
            "crop": ["maize"],
            "production_tonnes": [100.0],
        }
    )
    foods = pd.DataFrame(
        {
            "food": ["ddgs", "molasses", "wheat-bran"],
            "crop": ["maize", "sugarcane", "wheat"],
            "factor": [0.3, 0.1, 0.2],
        }
    )
    volumes = _estimate_byproduct_volumes(crop_production, foods, ["USA", "GUF"])
    assert set(volumes["country"]) == {"USA", "GUF"}
    assert set(volumes["byproduct"]) == {"bran", "ddgs", "molasses"}
    assert (volumes[volumes["country"] == "GUF"]["volume"] == 0).all()


def test_byproduct_fallback_uses_global_for_zero_country() -> None:
    """Zero-volume countries get a valid global fallback split."""
    byproduct_volumes = pd.DataFrame(
        {
            "country": ["USA", "USA", "USA", "GUF", "GUF", "GUF"],
            "byproduct": [
                "bran",
                "ddgs",
                "molasses",
                "bran",
                "ddgs",
                "molasses",
            ],
            "volume": [20.0, 30.0, 50.0, 0.0, 0.0, 0.0],
        }
    )
    rum_item_to_cat = {"wheat-bran": "grain", "ddgs": "forage", "molasses": "grain"}
    mono_item_to_cat = {
        "wheat-bran": "low_quality",
        "ddgs": "protein",
        "molasses": "grain",
    }
    fractions = _compute_byproduct_fractions(
        byproduct_volumes, rum_item_to_cat, mono_item_to_cat, ["USA", "GUF"]
    )

    guf = fractions[
        (fractions["country"] == "GUF") & (fractions["animal_type"] == "ruminant")
    ]
    assert guf["fraction"].sum() == pytest.approx(1.0)
    assert len(guf) == 2
