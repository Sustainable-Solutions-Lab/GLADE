# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 feed-fraction computation."""

import pandas as pd
import pytest

from workflow.scripts.compute_gleam3_feed_fractions import (
    _build_item_table,
    _compute_fractions,
    _normalize_code,
)


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
    result = _compute_fractions(table, crop_production, ["USA", "GUF"])

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
    result = _compute_fractions(table, crop_production, ["USA", "GUF"])

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
    result = _compute_fractions(table, crop_production, ["USA"])

    assert len(result) == 1
    assert result.iloc[0]["exogenous"] == True  # noqa: E712
    assert result.iloc[0]["fraction"] == 1.0
    assert result.iloc[0]["country"] == "_global"
