# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for build_blended_crop_shares from vegetable_projection."""

import pandas as pd
import pytest

from workflow.scripts.vegetable_projection import build_blended_crop_shares


class TestBuildBlendedCropShares:
    """Tests for the build_blended_crop_shares function."""

    # -----------------------------------------------------------------------
    # 1. Basic blending with blend_weight=0.7
    # -----------------------------------------------------------------------

    def test_basic_blending(self):
        """Verify blended shares with blend_weight=0.7 for two countries."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "B", "B"],
                "crop": ["onion", "carrot", "onion", "carrot"],
                "production_tonnes": [80.0, 20.0, 50.0, 50.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.7)

        # Global totals: 130 onion, 70 carrot -> global shares 0.65, 0.35
        assert global_shares["onion"] == pytest.approx(0.65)
        assert global_shares["carrot"] == pytest.approx(0.35)

        # Country A: country_share onion=0.8, carrot=0.2
        # Blended onion: 0.7*0.8 + 0.3*0.65 = 0.755
        # Blended carrot: 0.7*0.2 + 0.3*0.35 = 0.245
        assert lookup[("A", "onion")] == pytest.approx(0.755)
        assert lookup[("A", "carrot")] == pytest.approx(0.245)

        # Country B: country_share onion=0.5, carrot=0.5
        # Blended onion: 0.7*0.5 + 0.3*0.65 = 0.545
        # Blended carrot: 0.7*0.5 + 0.3*0.35 = 0.455
        assert lookup[("B", "onion")] == pytest.approx(0.545)
        assert lookup[("B", "carrot")] == pytest.approx(0.455)

    def test_basic_blending_shares_sum_to_one(self):
        """Blended shares must sum to 1.0 per country."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "B", "B"],
                "crop": ["onion", "carrot", "onion", "carrot"],
                "production_tonnes": [80.0, 20.0, 50.0, 50.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, _ = build_blended_crop_shares(df, crops, blend_weight=0.7)

        for country in ["A", "B"]:
            total = sum(lookup[(country, c)] for c in crops)
            assert total == pytest.approx(1.0)

    # -----------------------------------------------------------------------
    # 2. blend_weight=0.0 (pure global)
    # -----------------------------------------------------------------------

    def test_pure_global_shares(self):
        """With blend_weight=0.0, all countries get global shares."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "B", "B"],
                "crop": ["onion", "carrot", "onion", "carrot"],
                "production_tonnes": [80.0, 20.0, 50.0, 50.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.0)

        # All countries should match global shares exactly
        for country in ["A", "B"]:
            for crop in crops:
                assert lookup[(country, crop)] == pytest.approx(global_shares[crop])

    # -----------------------------------------------------------------------
    # 3. blend_weight=1.0 (pure country)
    # -----------------------------------------------------------------------

    def test_pure_country_shares(self):
        """With blend_weight=1.0, each country gets its own production shares."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "B", "B"],
                "crop": ["onion", "carrot", "onion", "carrot"],
                "production_tonnes": [80.0, 20.0, 50.0, 50.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, _ = build_blended_crop_shares(df, crops, blend_weight=1.0)

        # Country A: 80/100 = 0.8 onion, 20/100 = 0.2 carrot
        assert lookup[("A", "onion")] == pytest.approx(0.8)
        assert lookup[("A", "carrot")] == pytest.approx(0.2)

        # Country B: 50/100 = 0.5 onion, 50/100 = 0.5 carrot
        assert lookup[("B", "onion")] == pytest.approx(0.5)
        assert lookup[("B", "carrot")] == pytest.approx(0.5)

    # -----------------------------------------------------------------------
    # 4. Zero-production country
    # -----------------------------------------------------------------------

    def test_zero_production_country_falls_back_to_global(self):
        """A country with no production falls back to global shares."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "B", "B"],
                "crop": ["onion", "carrot", "onion", "carrot"],
                "production_tonnes": [80.0, 20.0, 0.0, 0.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.7)

        # Country B has zero production -> falls back to global shares
        assert lookup[("B", "onion")] == pytest.approx(global_shares["onion"])
        assert lookup[("B", "carrot")] == pytest.approx(global_shares["carrot"])

    # -----------------------------------------------------------------------
    # 5. Empty DataFrame
    # -----------------------------------------------------------------------

    def test_empty_dataframe(self):
        """An empty DataFrame returns empty lookup and uniform global shares."""
        df = pd.DataFrame(columns=["country", "crop", "production_tonnes"])
        crops = ["onion", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.7)

        assert lookup == {}
        uniform = 1.0 / len(crops)
        for crop in crops:
            assert global_shares[crop] == pytest.approx(uniform)

    # -----------------------------------------------------------------------
    # 6. Single country
    # -----------------------------------------------------------------------

    def test_single_country(self):
        """Works correctly with just one country."""
        df = pd.DataFrame(
            {
                "country": ["A", "A"],
                "crop": ["onion", "carrot"],
                "production_tonnes": [60.0, 40.0],
            }
        )
        crops = ["onion", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.7)

        # With a single country, country shares == global shares
        # So blended = 0.7*0.6 + 0.3*0.6 = 0.6 for onion
        assert lookup[("A", "onion")] == pytest.approx(0.6)
        assert lookup[("A", "carrot")] == pytest.approx(0.4)
        assert global_shares["onion"] == pytest.approx(0.6)
        assert global_shares["carrot"] == pytest.approx(0.4)

        # Shares still sum to 1.0
        total = sum(lookup[("A", c)] for c in crops)
        assert total == pytest.approx(1.0)

    # -----------------------------------------------------------------------
    # 7. Invalid blend_weight
    # -----------------------------------------------------------------------

    def test_blend_weight_below_zero_raises(self):
        """blend_weight < 0 raises ValueError."""
        df = pd.DataFrame(
            {
                "country": ["A"],
                "crop": ["onion"],
                "production_tonnes": [100.0],
            }
        )
        with pytest.raises(ValueError, match="blend_weight"):
            build_blended_crop_shares(df, ["onion"], blend_weight=-0.1)

    def test_blend_weight_above_one_raises(self):
        """blend_weight > 1 raises ValueError."""
        df = pd.DataFrame(
            {
                "country": ["A"],
                "crop": ["onion"],
                "production_tonnes": [100.0],
            }
        )
        with pytest.raises(ValueError, match="blend_weight"):
            build_blended_crop_shares(df, ["onion"], blend_weight=1.5)

    # -----------------------------------------------------------------------
    # 8. Three crops
    # -----------------------------------------------------------------------

    def test_three_crops(self):
        """Correct handling with three crops (onion, cabbage, carrot)."""
        df = pd.DataFrame(
            {
                "country": ["A", "A", "A", "B", "B", "B"],
                "crop": [
                    "onion",
                    "cabbage",
                    "carrot",
                    "onion",
                    "cabbage",
                    "carrot",
                ],
                "production_tonnes": [60.0, 30.0, 10.0, 20.0, 40.0, 40.0],
            }
        )
        crops = ["onion", "cabbage", "carrot"]
        lookup, global_shares = build_blended_crop_shares(df, crops, blend_weight=0.7)

        # Global totals: onion=80, cabbage=70, carrot=50, total=200
        assert global_shares["onion"] == pytest.approx(80.0 / 200.0)
        assert global_shares["cabbage"] == pytest.approx(70.0 / 200.0)
        assert global_shares["carrot"] == pytest.approx(50.0 / 200.0)

        # Country A: country shares = 60/100, 30/100, 10/100
        # Blended onion: 0.7*(60/100) + 0.3*(80/200) = 0.42 + 0.12 = 0.54
        # Blended cabbage: 0.7*(30/100) + 0.3*(70/200) = 0.21 + 0.105 = 0.315
        # Blended carrot: 0.7*(10/100) + 0.3*(50/200) = 0.07 + 0.075 = 0.145
        assert lookup[("A", "onion")] == pytest.approx(0.54)
        assert lookup[("A", "cabbage")] == pytest.approx(0.315)
        assert lookup[("A", "carrot")] == pytest.approx(0.145)

        # Country B: country shares = 20/100, 40/100, 40/100
        # Blended onion: 0.7*(20/100) + 0.3*(80/200) = 0.14 + 0.12 = 0.26
        # Blended cabbage: 0.7*(40/100) + 0.3*(70/200) = 0.28 + 0.105 = 0.385
        # Blended carrot: 0.7*(40/100) + 0.3*(50/200) = 0.28 + 0.075 = 0.355
        assert lookup[("B", "onion")] == pytest.approx(0.26)
        assert lookup[("B", "cabbage")] == pytest.approx(0.385)
        assert lookup[("B", "carrot")] == pytest.approx(0.355)

        # Verify all shares sum to 1.0 per country
        for country in ["A", "B"]:
            total = sum(lookup[(country, c)] for c in crops)
            assert total == pytest.approx(1.0)
