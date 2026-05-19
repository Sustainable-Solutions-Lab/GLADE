# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 feed-fraction computation."""

import pandas as pd
import pytest

from workflow.scripts.compute_gleam3_feed_fractions import (
    _build_item_table,
    _compute_food_production,
    _compute_fractions,
    _normalize_code,
)

_EMPTY_FOOD_PROD = pd.DataFrame(columns=["country", "food", "production_tonnes"])


@pytest.fixture
def gleam_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "gleam_code": [
                "CORN",
                "GRAINS",
                "GRAINS",
                "MLSOY",
                "GRNBYDRY",
                "GRNBYWET",
                "MOLASSES",
            ],
            "model_entity": [
                "maize",
                "barley",
                "oat",
                "oilseed-meal",
                "wheat-bran",
                "ddgs",
                "molasses",
            ],
            "entity_type": ["crop", "crop", "crop", "food", "food", "food", "food"],
            "animal_type": [
                "both",
                "ruminant",
                "ruminant",
                "both",
                "both",
                "both",
                "both",
            ],
            "notes": [""] * 7,
        }
    )


@pytest.fixture
def rum_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "feed_item": [
                "maize",
                "barley",
                "oat",
                "oilseed-meal",
                "wheat-bran",
                "ddgs",
                "molasses",
            ],
            "source_type": ["crop", "crop", "crop", "food", "food", "food", "food"],
            "category": [
                "grain",
                "grain",
                "grain",
                "protein",
                "grain",
                "forage",
                "grain",
            ],
        }
    )


@pytest.fixture
def mono_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "feed_item": ["maize", "oilseed-meal", "wheat-bran", "ddgs", "molasses"],
            "source_type": ["crop", "food", "food", "food", "food"],
            "category": ["grain", "protein", "low_quality", "protein", "grain"],
        }
    )


@pytest.fixture
def crop_production() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "country": ["USA", "USA", "USA", "GUF"],
            "crop": ["maize", "barley", "oat", "maize"],
            "production_tonnes": [300_000_000.0, 5_000_000.0, 1_000_000.0, 0.0],
        }
    )


def test_normalize_code_strips_commercial_prefix() -> None:
    valid = {"MAIZE", "BARLEY", "CASSAVA", "PULSES"}
    assert _normalize_code("CMAIZE", valid) == "MAIZE"
    assert _normalize_code("CBARLEY", valid) == "BARLEY"
    assert _normalize_code("CCASSAVA", valid) == "CASSAVA"
    # CORN shouldn't be stripped (ORN not valid)
    assert _normalize_code("CORN", valid) == "CORN"
    # CASSAVA shouldn't be stripped (ASSAVA not valid)
    assert _normalize_code("CASSAVA", valid) == "CASSAVA"


def test_normalize_code_special_cases() -> None:
    valid: set[str] = set()
    assert _normalize_code("SOY OIL", valid) == "SOYOIL"
    assert _normalize_code("LIME", valid) == "LIMESTONE"


def test_build_item_table_marks_exogenous(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
) -> None:
    """Items without model entities are marked exogenous."""
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["ruminant", "ruminant"],
            "raw_code": ["CORN", "UNKNOWN"],
            "gleam3_category": ["Grains", "Grains"],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)
    endo = table[~table["exogenous"]]
    exo = table[table["exogenous"]]
    assert len(endo) == 1
    assert endo.iloc[0]["model_entity"] == "maize"
    assert len(exo) == 1  # UNKNOWN → exogenous


def test_fractions_sum_to_one(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
    crop_production: pd.DataFrame,
) -> None:
    """Fractions within each group must sum to 1.0."""
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["ruminant", "ruminant", "ruminant"],
            "raw_code": ["GRNBYDRY", "GRNBYWET", "MOLASSES"],
            "gleam3_category": ["By-products", "By-products", "By-products"],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)
    result = _compute_fractions(
        table, crop_production, _EMPTY_FOOD_PROD, ["USA", "GUF"]
    )

    for _, grp in result.groupby(["gleam3_category", "animal_type", "country"]):
        assert grp["fraction"].sum() == pytest.approx(1.0)


def test_zero_volume_country_uses_global_fallback(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
) -> None:
    """Countries with zero crop production get global-average fractions."""
    # Create a group with multiple model categories so fractions are
    # country-varying (By-products has forage + grain for ruminants).
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["ruminant", "ruminant", "ruminant"],
            "raw_code": ["GRNBYDRY", "GRNBYWET", "MOLASSES"],
            "gleam3_category": ["By-products", "By-products", "By-products"],
        }
    )
    crop_production = pd.DataFrame(
        {
            "country": ["USA"],
            "crop": ["maize"],
            "production_tonnes": [100.0],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)
    result = _compute_fractions(
        table, crop_production, _EMPTY_FOOD_PROD, ["USA", "GUF"]
    )

    # GUF has no crop data → falls back to global fractions
    guf = result[result["country"] == "GUF"]
    assert guf["fraction"].sum() == pytest.approx(1.0)
    assert len(guf) > 0


def test_fully_exogenous_group(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
    crop_production: pd.DataFrame,
) -> None:
    """Groups where all items are exogenous get a single exogenous row."""
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["monogastric", "monogastric"],
            "raw_code": ["UNKNOWN1", "UNKNOWN2"],
            "gleam3_category": ["Other non-edible", "Other non-edible"],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)
    result = _compute_fractions(table, crop_production, _EMPTY_FOOD_PROD, ["USA"])

    assert len(result) == 1
    assert result.iloc[0]["exogenous"] == True  # noqa: E712
    assert result.iloc[0]["fraction"] == 1.0
    assert result.iloc[0]["country"] == "_global"


def test_mixed_endogenous_exogenous_preserves_exogenous_share(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
    crop_production: pd.DataFrame,
) -> None:
    """A bucket with both endogenous and exogenous items must emit an
    exogenous row alongside the endogenous fractions; previously the
    exogenous share was silently absorbed into the endogenous categories
    (the real-world failure was GLEAM's 'Grass and leaves' bucket dropping
    the LEAVES item)."""
    # Build a Grains bucket with two endogenous items (maize, barley) and
    # one unmapped GLEAM code that will be flagged as exogenous.
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["ruminant", "ruminant", "ruminant"],
            "raw_code": ["CORN", "GRAINS", "UNKNOWN-EXO"],
            "gleam3_category": ["Grains", "Grains", "Grains"],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)
    # Sanity-check: the item table flags the unknown code as exogenous.
    assert table["exogenous"].any()
    assert (~table["exogenous"]).any()

    result = _compute_fractions(table, crop_production, _EMPTY_FOOD_PROD, ["USA"])

    usa = result[result["country"] == "USA"]
    # Fractions per country must still sum to 1.
    assert usa["fraction"].sum() == pytest.approx(1.0)
    # An exogenous row must be present (the bug was that this row vanished).
    exo_rows = usa[usa["exogenous"]]
    assert not exo_rows.empty
    assert (exo_rows["fraction"] > 0).all()


def test_compute_food_production_sums_pathways() -> None:
    """A food entity produced by multiple pathways gets the per-country sum."""
    foods = pd.DataFrame(
        {
            "pathway": ["soybean_oil", "groundnut_oil", "maize_wetmill"],
            "crop": ["soybean", "groundnut", "maize"],
            "food": ["oilseed-meal", "oilseed-meal", "maize-gluten-meal"],
            "factor": [0.78, 0.54, 0.054],
        }
    )
    crop_production = pd.DataFrame(
        {
            "country": ["USA", "USA", "USA", "BRA"],
            "crop": ["soybean", "groundnut", "maize", "soybean"],
            "production_tonnes": [100.0, 50.0, 200.0, 80.0],
        }
    )
    # Dispatch shares: maize_wetmill is rare (~7 %), others assumed 1.0.
    result = _compute_food_production(foods, crop_production, {"maize_wetmill": 0.07})
    lookup = result.set_index(["country", "food"])["production_tonnes"].to_dict()

    # USA oilseed-meal = 100 * 0.78 + 50 * 0.54 = 78 + 27 = 105
    assert lookup[("USA", "oilseed-meal")] == pytest.approx(105.0)
    # USA maize-gluten-meal = 200 * 0.054 * 0.07 ~= 0.756
    assert lookup[("USA", "maize-gluten-meal")] == pytest.approx(0.756)
    # BRA oilseed-meal = 80 * 0.78 = 62.4
    assert lookup[("BRA", "oilseed-meal")] == pytest.approx(62.4)
    # BRA maize-gluten-meal: no maize production → no row
    assert ("BRA", "maize-gluten-meal") not in lookup


def test_food_production_changes_fraction_weights(
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
    crop_production: pd.DataFrame,
) -> None:
    """When food-entity production is supplied, fractions follow real volumes
    rather than entity count.

    Two ruminant by-products entities (wheat-bran → grain, ddgs → forage
    per the rum_mapping fixture). With empty food_production both fall
    back to mean_tracked → equal shares of 0.5 / 0.5. With explicit
    food_production showing ddgs is 10x bigger than wheat-bran, the
    forage share should dominate.
    """
    xlsx_items = pd.DataFrame(
        {
            "animal_type": ["ruminant", "ruminant"],
            "raw_code": ["GRNBYDRY", "GRNBYWET"],
            "gleam3_category": ["By-products", "By-products"],
        }
    )
    table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)

    # Without food_production: equal weighting (both fall back to mean_tracked).
    flat = _compute_fractions(table, crop_production, _EMPTY_FOOD_PROD, ["USA"])
    flat_usa = flat[flat["country"] == "USA"].set_index("model_feed_category")[
        "fraction"
    ]
    assert flat_usa["ruminant_forage"] == pytest.approx(0.5)
    assert flat_usa["ruminant_grain"] == pytest.approx(0.5)

    # With food_production showing ddgs (→ forage) dominates wheat-bran
    # (→ grain) 10:1 in USA, the forage share should dominate.
    food_prod = pd.DataFrame(
        {
            "country": ["USA", "USA"],
            "food": ["wheat-bran", "ddgs"],
            "production_tonnes": [1.0, 10.0],
        }
    )
    weighted = _compute_fractions(table, crop_production, food_prod, ["USA"])
    weighted_usa = weighted[weighted["country"] == "USA"].set_index(
        "model_feed_category"
    )["fraction"]
    assert weighted_usa["ruminant_forage"] == pytest.approx(10 / 11)
    assert weighted_usa["ruminant_grain"] == pytest.approx(1 / 11)
