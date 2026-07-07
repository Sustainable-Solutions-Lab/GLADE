# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for feed categorization functions."""

import pandas as pd
import pytest

from workflow.scripts.categorize_feeds import (
    add_methane_yields,
    categorize_monogastric_feeds,
    categorize_ruminant_feeds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ruminant_feed(
    feed_item: str,
    source_type: str = "crop",
    ge: float = 18.0,
    n: float = 20.0,
    digestibility: float = 0.65,
    ash: float = 5.0,
) -> dict:
    """Create a single ruminant feed row as a dict."""
    return {
        "feed_item": feed_item,
        "source_type": source_type,
        "GE_MJ_per_kg_DM": ge,
        "N_g_per_kg_DM": n,
        "digestibility": digestibility,
        "ash_content_pct_dm": ash,
    }


def _make_monogastric_feed(
    feed_item: str,
    source_type: str = "crop",
    ge: float = 18.0,
    me: float = 13.0,
    n: float = 20.0,
    digestibility: float = 0.65,
    ash: float = 5.0,
) -> dict:
    """Create a single monogastric feed row as a dict."""
    return {
        "feed_item": feed_item,
        "source_type": source_type,
        "GE_MJ_per_kg_DM": ge,
        "ME_MJ_per_kg_DM": me,
        "N_g_per_kg_DM": n,
        "digestibility": digestibility,
        "ash_content_pct_dm": ash,
    }


# Empty ash content DataFrame (used when ash is already merged into feed_properties)
EMPTY_ASH = pd.DataFrame(columns=["feed", "ash_content_pct_dm"])


# ---------------------------------------------------------------------------
# Tests: categorize_ruminant_feeds
# ---------------------------------------------------------------------------


class TestCategorizeRuminantFeeds:
    """Tests for ruminant feed categorization by digestibility and N content."""

    def test_roughage_below_055(self):
        """Digestibility < 0.55 is categorized as roughage."""
        df = pd.DataFrame([_make_ruminant_feed("straw", digestibility=0.54)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "roughage"

    def test_forage_at_055(self):
        """Digestibility == 0.55 is categorized as forage (lower boundary inclusive)."""
        df = pd.DataFrame([_make_ruminant_feed("hay", digestibility=0.55)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "forage"

    def test_forage_at_069(self):
        """Digestibility == 0.69 is still forage (upper boundary exclusive at 0.70)."""
        df = pd.DataFrame([_make_ruminant_feed("silage", digestibility=0.69)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "forage"

    def test_grain_at_070(self):
        """Digestibility == 0.70 is categorized as grain."""
        df = pd.DataFrame([_make_ruminant_feed("barley", digestibility=0.70)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_grain_at_089(self):
        """Digestibility == 0.89 is still grain (upper boundary exclusive at 0.90)."""
        df = pd.DataFrame([_make_ruminant_feed("maize", digestibility=0.89)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_protein_at_090(self):
        """Digestibility >= 0.90 is categorized as protein."""
        df = pd.DataFrame([_make_ruminant_feed("soybean_meal", digestibility=0.90)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "protein"

    def test_high_nitrogen_overrides_digestibility(self):
        """N > 50 g/kg DM forces protein category regardless of digestibility."""
        df = pd.DataFrame(
            [_make_ruminant_feed("rapeseed_meal", digestibility=0.60, n=55.0)]
        )
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "protein"

    def test_nitrogen_at_50_does_not_override(self):
        """N == 50 g/kg DM does not trigger protein override (threshold is >50)."""
        df = pd.DataFrame(
            [_make_ruminant_feed("borderline_feed", digestibility=0.60, n=50.0)]
        )
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "forage"

    def test_grassland_always_forage(self):
        """Feed item 'grassland' is always categorized as forage."""
        df = pd.DataFrame(
            [_make_ruminant_feed("grassland", digestibility=0.95, n=60.0)]
        )
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "forage"

    def test_grassland_overrides_low_digestibility(self):
        """Grassland with very low digestibility is still forage."""
        df = pd.DataFrame([_make_ruminant_feed("grassland", digestibility=0.30)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "forage"

    def test_me_calculation(self):
        """ME is computed as GE * digestibility * 0.82."""
        ge = 18.5
        di = 0.65
        expected_me = ge * di * 0.82
        df = pd.DataFrame([_make_ruminant_feed("hay", ge=ge, digestibility=di)])
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert categories.iloc[0]["ME_MJ_per_kg_DM"] == pytest.approx(expected_me)

    def test_category_averages(self):
        """Category averages are computed correctly across multiple feeds."""
        df = pd.DataFrame(
            [
                _make_ruminant_feed("feed_a", digestibility=0.60, ge=18.0, n=20.0),
                _make_ruminant_feed("feed_b", digestibility=0.65, ge=20.0, n=30.0),
            ]
        )
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        # Both feeds are forage (0.55 <= di < 0.70)
        assert len(categories) == 1
        forage = categories[categories["category"] == "forage"].iloc[0]
        assert forage["GE_MJ_per_kg_DM"] == pytest.approx((18.0 + 20.0) / 2)
        assert forage["N_g_per_kg_DM"] == pytest.approx((20.0 + 30.0) / 2)
        assert forage["digestibility"] == pytest.approx((0.60 + 0.65) / 2)
        expected_me_a = 18.0 * 0.60 * 0.82
        expected_me_b = 20.0 * 0.65 * 0.82
        assert forage["ME_MJ_per_kg_DM"] == pytest.approx(
            (expected_me_a + expected_me_b) / 2
        )

    def test_n_feeds_count(self):
        """n_feeds column correctly counts feeds per category."""
        df = pd.DataFrame(
            [
                _make_ruminant_feed("straw", digestibility=0.40),
                _make_ruminant_feed("hay_a", digestibility=0.60),
                _make_ruminant_feed("hay_b", digestibility=0.62),
                _make_ruminant_feed("barley", digestibility=0.75),
            ]
        )
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        cat_dict = categories.set_index("category")["n_feeds"].to_dict()
        assert cat_dict["roughage"] == 1
        assert cat_dict["forage"] == 2
        assert cat_dict["grain"] == 1

    def test_feed_mapping_columns(self):
        """Feed mapping DataFrame has the expected columns."""
        df = pd.DataFrame([_make_ruminant_feed("hay", digestibility=0.60)])
        _categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        assert list(mapping.columns) == ["feed_item", "source_type", "category"]

    def test_categories_columns(self):
        """Categories DataFrame has the expected columns."""
        df = pd.DataFrame([_make_ruminant_feed("hay", digestibility=0.60)])
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        expected_cols = {
            "category",
            "ME_MJ_per_kg_DM",
            "GE_MJ_per_kg_DM",
            "N_g_per_kg_DM",
            "digestibility",
            "ash_content_pct_dm",
            "n_feeds",
        }
        assert expected_cols == set(categories.columns)

    def test_duplicate_feed_items_are_averaged(self):
        """Duplicate (feed_item, source_type) rows are averaged before categorization."""
        df = pd.DataFrame(
            [
                _make_ruminant_feed("maize", digestibility=0.72, ge=18.0),
                _make_ruminant_feed("maize", digestibility=0.78, ge=20.0),
            ]
        )
        categories, mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        # After averaging: digestibility=0.75, ge=19.0 -> grain
        assert len(mapping) == 1
        assert mapping.iloc[0]["category"] == "grain"
        grain = categories[categories["category"] == "grain"].iloc[0]
        assert grain["digestibility"] == pytest.approx(0.75)
        assert grain["GE_MJ_per_kg_DM"] == pytest.approx(19.0)

    def test_all_categories_present(self):
        """When feeds span all categories, all are represented."""
        df = pd.DataFrame(
            [
                _make_ruminant_feed("straw", digestibility=0.40),
                _make_ruminant_feed("hay", digestibility=0.60),
                _make_ruminant_feed("barley", digestibility=0.75),
                _make_ruminant_feed("soy_meal", digestibility=0.92),
                _make_ruminant_feed("grassland", digestibility=0.65),
            ]
        )
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        cat_set = set(categories["category"])
        assert cat_set == {"roughage", "forage", "grain", "protein"}


# ---------------------------------------------------------------------------
# Tests: categorize_monogastric_feeds
# ---------------------------------------------------------------------------


class TestCategorizeMonogastricFeeds:
    """Tests for monogastric feed categorization by ME and N content."""

    def test_low_quality_below_11(self):
        """ME < 11 is categorized as low_quality."""
        df = pd.DataFrame([_make_monogastric_feed("bran", me=10.9)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "low_quality"

    def test_grain_at_11(self):
        """ME == 11 is categorized as grain (lower boundary inclusive)."""
        df = pd.DataFrame([_make_monogastric_feed("wheat", me=11.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_grain_at_154(self):
        """ME == 15.4 is still grain (upper boundary exclusive at 15.5)."""
        df = pd.DataFrame([_make_monogastric_feed("maize", me=15.4)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_energy_at_155(self):
        """ME >= 15.5 is still categorized as grain (energy merged into grain)."""
        df = pd.DataFrame([_make_monogastric_feed("fat_feed", me=15.5)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_high_nitrogen_overrides_me(self):
        """N > 35 g/kg DM forces protein category regardless of ME."""
        df = pd.DataFrame([_make_monogastric_feed("soy_meal", me=12.0, n=40.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "protein"

    def test_nitrogen_at_35_does_not_override(self):
        """N == 35 g/kg DM does not trigger protein override (threshold is >35)."""
        df = pd.DataFrame([_make_monogastric_feed("borderline", me=12.0, n=35.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "grain"

    def test_protein_overrides_low_quality(self):
        """N > 35 forces protein even when ME < 11."""
        df = pd.DataFrame([_make_monogastric_feed("protein_meal", me=9.0, n=50.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "protein"

    def test_protein_overrides_energy(self):
        """N > 35 forces protein even when ME >= 15.5."""
        df = pd.DataFrame([_make_monogastric_feed("rich_meal", me=16.0, n=45.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert mapping.iloc[0]["category"] == "protein"

    def test_n_feeds_count(self):
        """n_feeds column correctly counts feeds per category."""
        df = pd.DataFrame(
            [
                _make_monogastric_feed("bran_a", me=9.0),
                _make_monogastric_feed("bran_b", me=10.0),
                _make_monogastric_feed("wheat", me=13.0),
                _make_monogastric_feed("oil_feed", me=16.0),
            ]
        )
        categories, _mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        cat_dict = categories.set_index("category")["n_feeds"].to_dict()
        assert cat_dict["low_quality"] == 2
        assert cat_dict["grain"] == 2

    def test_category_averages(self):
        """Category averages are computed correctly for monogastric feeds."""
        df = pd.DataFrame(
            [
                _make_monogastric_feed("wheat", me=12.0, n=15.0, digestibility=0.70),
                _make_monogastric_feed("maize", me=14.0, n=10.0, digestibility=0.80),
            ]
        )
        categories, _mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        # Both are grain (11 <= ME < 15.5, N <= 35)
        grain = categories[categories["category"] == "grain"].iloc[0]
        assert grain["ME_MJ_per_kg_DM"] == pytest.approx((12.0 + 14.0) / 2)
        assert grain["N_g_per_kg_DM"] == pytest.approx((15.0 + 10.0) / 2)
        assert grain["digestibility"] == pytest.approx((0.70 + 0.80) / 2)

    def test_feed_mapping_columns(self):
        """Feed mapping DataFrame has the expected columns."""
        df = pd.DataFrame([_make_monogastric_feed("wheat", me=13.0)])
        _categories, mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        assert list(mapping.columns) == ["feed_item", "source_type", "category"]

    def test_all_categories_present(self):
        """When feeds span all categories, all are represented."""
        df = pd.DataFrame(
            [
                _make_monogastric_feed("bran", me=9.0, n=10.0),
                _make_monogastric_feed("wheat", me=13.0, n=15.0),
                _make_monogastric_feed("oil_feed", me=16.0, n=10.0),
                _make_monogastric_feed("soy_meal", me=13.0, n=50.0),
            ]
        )
        categories, _mapping = categorize_monogastric_feeds(df, EMPTY_ASH)
        cat_set = set(categories["category"])
        assert cat_set == {"low_quality", "grain", "protein"}


# ---------------------------------------------------------------------------
# Tests: add_methane_yields
# ---------------------------------------------------------------------------


class TestAddMethaneYields:
    """Tests for adding CH4 emission factors to ruminant categories."""

    @pytest.fixture
    def methane_yields_df(self):
        """Methane yield data by feed category."""
        return pd.DataFrame(
            {
                "feed_category": ["roughage", "forage", "concentrate"],
                "MY_g_CH4_per_kg_DMI": [21.0, 19.5, 15.0],
            }
        )

    @pytest.fixture
    def ruminant_categories_df(self):
        """Ruminant categories DataFrame as produced by categorize_ruminant_feeds."""
        return pd.DataFrame(
            {
                "category": ["roughage", "forage", "grain", "protein"],
                "ME_MJ_per_kg_DM": [7.0, 9.0, 11.0, 13.0],
                "GE_MJ_per_kg_DM": [17.0, 18.0, 18.5, 19.0],
                "N_g_per_kg_DM": [10.0, 15.0, 20.0, 55.0],
                "digestibility": [0.50, 0.62, 0.80, 0.92],
                "n_feeds": [3, 5, 4, 2],
            }
        )

    def test_roughage_maps_to_roughage(self, ruminant_categories_df, methane_yields_df):
        """Roughage category maps to roughage methane yield."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        roughage = result[result["category"] == "roughage"]
        assert roughage["MY_g_CH4_per_kg_DMI"].iloc[0] == pytest.approx(21.0)

    def test_forage_maps_to_forage(self, ruminant_categories_df, methane_yields_df):
        """Forage category maps to forage methane yield."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        forage = result[result["category"] == "forage"]
        assert forage["MY_g_CH4_per_kg_DMI"].iloc[0] == pytest.approx(19.5)

    def test_grain_maps_to_concentrate(self, ruminant_categories_df, methane_yields_df):
        """Grain category maps to concentrate methane yield."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        grain = result[result["category"] == "grain"]
        assert grain["MY_g_CH4_per_kg_DMI"].iloc[0] == pytest.approx(15.0)

    def test_protein_maps_to_concentrate(
        self, ruminant_categories_df, methane_yields_df
    ):
        """Protein category maps to concentrate methane yield."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        protein = result[result["category"] == "protein"]
        assert protein["MY_g_CH4_per_kg_DMI"].iloc[0] == pytest.approx(15.0)

    def test_temporary_columns_removed(self, ruminant_categories_df, methane_yields_df):
        """Temporary ch4_category and feed_category columns are not in the result."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        assert "ch4_category" not in result.columns
        assert "feed_category" not in result.columns

    def test_all_rows_have_methane_yield(
        self, ruminant_categories_df, methane_yields_df
    ):
        """Every category row gets a methane yield value (no NaN)."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        assert not result["MY_g_CH4_per_kg_DMI"].isna().any()

    def test_original_columns_preserved(
        self, ruminant_categories_df, methane_yields_df
    ):
        """Original columns from ruminant_categories are preserved in the result."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        for col in [
            "category",
            "ME_MJ_per_kg_DM",
            "GE_MJ_per_kg_DM",
            "N_g_per_kg_DM",
            "digestibility",
            "n_feeds",
        ]:
            assert col in result.columns

    def test_result_row_count(self, ruminant_categories_df, methane_yields_df):
        """Result has the same number of rows as input categories."""
        result = add_methane_yields(ruminant_categories_df, methane_yields_df)
        assert len(result) == len(ruminant_categories_df)

    def test_integration_with_categorize_ruminant_feeds(self, methane_yields_df):
        """End-to-end: categorize feeds then add methane yields."""
        df = pd.DataFrame(
            [
                _make_ruminant_feed("straw", digestibility=0.40),
                _make_ruminant_feed("hay", digestibility=0.60),
                _make_ruminant_feed("barley", digestibility=0.75),
                _make_ruminant_feed("soy_meal", digestibility=0.92),
                _make_ruminant_feed("grassland", digestibility=0.65),
            ]
        )
        categories, _mapping = categorize_ruminant_feeds(df, EMPTY_ASH)
        result = add_methane_yields(categories, methane_yields_df)
        # grassland now merges into forage, so 4 categories
        assert len(result) == 4
        assert not result["MY_g_CH4_per_kg_DMI"].isna().any()

        # Verify specific mappings in the integrated result
        result_dict = result.set_index("category")["MY_g_CH4_per_kg_DMI"].to_dict()
        assert result_dict["roughage"] == pytest.approx(21.0)
        assert result_dict["forage"] == pytest.approx(19.5)
        assert result_dict["grain"] == pytest.approx(15.0)
        assert result_dict["protein"] == pytest.approx(15.0)
