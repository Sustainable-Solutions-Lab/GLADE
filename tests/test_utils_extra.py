# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for workflow.scripts.build_model.utils utility functions."""

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.build_model.utils import (
    _build_luc_lef_lookup,
    _carrier_unit_for_nutrient,
    _fresh_mass_conversion_factors,
    _load_crop_yield_table,
    _nutrient_kind,
    _nutrition_efficiency_factor,
    _per_capita_mass_to_mt_per_year,
    merge_lef,
)
from workflow.scripts.constants import (
    FOOD_PORTION_TO_MASS_FRACTION,
    KCAL_PER_100G_TO_PJ_PER_MEGATONNE,
)

# ---------------------------------------------------------------------------
# Tests: _per_capita_mass_to_mt_per_year
# ---------------------------------------------------------------------------


class TestPerCapitaMassToMtPerYear:
    """Tests for _per_capita_mass_to_mt_per_year."""

    def test_known_values(self):
        """100 g/person/day with 1,000,000 people → 100 * 1e6 * 365 / 1e12."""
        result = _per_capita_mass_to_mt_per_year(100.0, 1_000_000)
        expected = 100.0 * 1_000_000 * 365 / 1e12
        assert result == pytest.approx(expected)
        assert result == pytest.approx(3.65e-2)

    def test_zero_population(self):
        """Zero population should yield zero."""
        result = _per_capita_mass_to_mt_per_year(50.0, 0)
        assert result == pytest.approx(0.0)

    def test_zero_value(self):
        """Zero per-capita value should yield zero."""
        result = _per_capita_mass_to_mt_per_year(0.0, 1_000_000)
        assert result == pytest.approx(0.0)

    def test_large_population(self):
        """Test with a realistically large population (1 billion)."""
        result = _per_capita_mass_to_mt_per_year(200.0, 1e9)
        expected = 200.0 * 1e9 * 365 / 1e12
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Tests: _nutrient_kind and _nutrition_efficiency_factor
# ---------------------------------------------------------------------------


class TestNutrientKind:
    """Tests for _nutrient_kind."""

    def test_mass_unit(self):
        """'g/100g' should map to kind 'mass'."""
        assert _nutrient_kind("g/100g") == "mass"

    def test_energy_unit(self):
        """'kcal/100g' should map to kind 'energy'."""
        assert _nutrient_kind("kcal/100g") == "energy"

    def test_unknown_unit_raises(self):
        """An unsupported unit should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported nutrition unit"):
            _nutrient_kind("mg/L")


class TestNutritionEfficiencyFactor:
    """Tests for _nutrition_efficiency_factor."""

    def test_mass_factor(self):
        """'g/100g' factor should be FOOD_PORTION_TO_MASS_FRACTION (0.01)."""
        factor = _nutrition_efficiency_factor("g/100g")
        assert factor == pytest.approx(FOOD_PORTION_TO_MASS_FRACTION)
        assert factor == pytest.approx(0.01)

    def test_energy_factor(self):
        """'kcal/100g' factor should be KCAL_PER_100G_TO_PJ_PER_MEGATONNE."""
        factor = _nutrition_efficiency_factor("kcal/100g")
        assert factor == pytest.approx(KCAL_PER_100G_TO_PJ_PER_MEGATONNE)
        # Also verify numerical value: 0.01 * 1e12 * 4.184e-12 = 0.04184
        assert factor == pytest.approx(0.04184)

    def test_unknown_unit_raises(self):
        """An unsupported unit should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported nutrition unit"):
            _nutrition_efficiency_factor("oz/cup")


# ---------------------------------------------------------------------------
# Tests: _carrier_unit_for_nutrient
# ---------------------------------------------------------------------------


class TestCarrierUnitForNutrient:
    """Tests for _carrier_unit_for_nutrient."""

    def test_mass_unit(self):
        """'g/100g' should map to carrier unit 'Mt'."""
        assert _carrier_unit_for_nutrient("g/100g") == "Mt"

    def test_energy_unit(self):
        """'kcal/100g' should map to carrier unit 'PJ'."""
        assert _carrier_unit_for_nutrient("kcal/100g") == "PJ"

    def test_unknown_unit_raises(self):
        """An unsupported unit should raise ValueError."""
        with pytest.raises(ValueError):
            _carrier_unit_for_nutrient("unknown/unit")


# ---------------------------------------------------------------------------
# Tests: _fresh_mass_conversion_factors
# ---------------------------------------------------------------------------


class TestFreshMassConversionFactors:
    """Tests for _fresh_mass_conversion_factors."""

    def test_missing_crop_in_edible_portion_raises(self):
        """Missing crop in edible portion data should raise ValueError."""
        edible_df = pd.DataFrame(
            {"crop": ["wheat"], "edible_portion_coefficient": [0.8]}
        )
        moisture_df = pd.DataFrame(
            {"crop": ["wheat", "rice"], "moisture_fraction": [0.1, 0.12]}
        )
        with pytest.raises(ValueError, match="Missing edible portion data"):
            _fresh_mass_conversion_factors(edible_df, moisture_df, {"wheat", "rice"})

    def test_conversion_factors_follow_food_conversion_policy(self):
        """Factors combine edible portion and moisture per food_conversion.

        ``moisture_df`` is indexed by crop with completeness guaranteed
        upstream (validation/crop_moisture_content.py), so the function no
        longer re-checks moisture coverage itself.
        """
        edible_df = pd.DataFrame(
            {"crop": ["wheat", "tea"], "edible_portion_coefficient": [0.8, 1.0]}
        )
        moisture_df = pd.DataFrame(
            {
                "crop": ["wheat", "tea"],
                "moisture_fraction": [0.125, 0.0],
                "food_conversion": ["inverse_moisture", "identity"],
            }
        ).set_index("crop")
        factors = _fresh_mass_conversion_factors(
            edible_df, moisture_df, {"wheat", "tea"}
        )
        # inverse_moisture: edible / (1 - moisture) = 0.8 / 0.875
        assert factors["wheat"] == pytest.approx(0.8 / 0.875)
        # identity: factor is just the edible portion
        assert factors["tea"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tests: _load_crop_yield_table
# ---------------------------------------------------------------------------


class TestLoadCropYieldTable:
    """Tests for _load_crop_yield_table."""

    def test_valid_csv(self, tmp_path):
        """Load a small valid CSV and verify pivoted structure."""
        csv_path = tmp_path / "yields.csv"
        csv_path.write_text(
            "region,resource_class,variable,value,unit\n"
            "usa_east,1,yield,3.5,t/ha\n"
            "usa_east,1,area,100.0,ha\n"
            "usa_east,2,yield,2.8,t/ha\n"
            "usa_east,2,area,50.0,ha\n"
            "usa_west,1,yield,4.0,t/ha\n"
            "usa_west,1,area,200.0,ha\n"
        )
        pivot, units = _load_crop_yield_table(str(csv_path))

        assert pivot.index.names == ["region", "resource_class"]
        assert "yield" in pivot.columns
        assert "area" in pivot.columns
        assert pivot.loc[("usa_east", 1), "yield"] == pytest.approx(3.5)
        assert pivot.loc[("usa_west", 1), "area"] == pytest.approx(200.0)
        assert units["yield"] == "t/ha"
        assert units["area"] == "ha"

    def test_empty_file(self, tmp_path):
        """A completely empty CSV returns an empty DataFrame."""
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("")
        pivot, units = _load_crop_yield_table(str(csv_path))

        assert pivot.empty
        assert pivot.index.names == ["region", "resource_class"]
        assert units == {}

    def test_headers_only(self, tmp_path):
        """A CSV with only headers (no data rows) returns empty DataFrame."""
        csv_path = tmp_path / "headers_only.csv"
        csv_path.write_text("region,resource_class,variable,value,unit\n")
        pivot, units = _load_crop_yield_table(str(csv_path))

        assert pivot.empty
        assert pivot.index.names == ["region", "resource_class"]
        assert units == {}

    def test_resource_class_is_integer(self, tmp_path):
        """Resource class level should be integer type."""
        csv_path = tmp_path / "yields.csv"
        csv_path.write_text(
            "region,resource_class,variable,value,unit\n"
            "reg_a,1,yield,5.0,t/ha\n"
            "reg_a,2,yield,3.0,t/ha\n"
        )
        pivot, _ = _load_crop_yield_table(str(csv_path))
        rc_level = pivot.index.get_level_values("resource_class")
        assert rc_level.dtype == int


# ---------------------------------------------------------------------------
# Tests: merge_lef
# ---------------------------------------------------------------------------


class TestMergeLef:
    """Tests for merge_lef."""

    def test_normal_merge(self):
        """Matching keys merge correctly."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
            }
        )
        lef_df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
                "use": ["cropland", "cropland"],
                "lef": [10.5, 20.3],
            }
        )
        result = merge_lef(df, lef_df, "cropland")
        assert result.iloc[0] == pytest.approx(10.5)
        assert result.iloc[1] == pytest.approx(20.3)

    def test_missing_lef_raises(self):
        """Missing LEF with allow_missing=False should raise ValueError."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
            }
        )
        lef_df = pd.DataFrame(
            {
                "region": ["reg_a"],
                "resource_class": [1],
                "water_supply": ["rainfed"],
                "use": ["cropland"],
                "lef": [10.5],
            }
        )
        with pytest.raises(ValueError, match="Missing LEF data"):
            merge_lef(df, lef_df, "cropland", allow_missing=False)

    def test_missing_lef_allow_missing_fills_zero(self):
        """Missing LEF with allow_missing=True fills NaN with 0.0."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
            }
        )
        lef_df = pd.DataFrame(
            {
                "region": ["reg_a"],
                "resource_class": [1],
                "water_supply": ["rainfed"],
                "use": ["cropland"],
                "lef": [10.5],
            }
        )
        result = merge_lef(df, lef_df, "cropland", allow_missing=True)
        assert result.iloc[0] == pytest.approx(10.5)
        assert result.iloc[1] == pytest.approx(0.0)

    def test_filters_by_use(self):
        """Only LEF rows matching the requested use type are merged."""
        df = pd.DataFrame(
            {
                "region": ["reg_a"],
                "resource_class": [1],
                "water_supply": ["rainfed"],
            }
        )
        lef_df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_a"],
                "resource_class": [1, 1],
                "water_supply": ["rainfed", "rainfed"],
                "use": ["cropland", "pasture"],
                "lef": [10.5, 5.0],
            }
        )
        result_cropland = merge_lef(df, lef_df, "cropland")
        assert result_cropland.iloc[0] == pytest.approx(10.5)

        result_pasture = merge_lef(df, lef_df, "pasture")
        assert result_pasture.iloc[0] == pytest.approx(5.0)

    def test_result_index_matches_input(self):
        """The returned Series index should match the input DataFrame index."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
            },
            index=[10, 20],
        )
        lef_df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water_supply": ["rainfed", "irrigated"],
                "use": ["cropland", "cropland"],
                "lef": [10.5, 20.3],
            }
        )
        result = merge_lef(df, lef_df, "cropland")
        assert list(result.index) == [10, 20]


# ---------------------------------------------------------------------------
# Tests: _build_luc_lef_lookup
# ---------------------------------------------------------------------------


class TestBuildLucLefLookup:
    """Tests for _build_luc_lef_lookup."""

    def test_valid_dataframe(self):
        """Valid input should be renamed and filtered."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b"],
                "resource_class": [1, 2],
                "water": ["rainfed", "irrigated"],
                "use": ["cropland", "pasture"],
                "LEF_tCO2_per_ha_yr": [12.5, 8.3],
            }
        )
        result = _build_luc_lef_lookup(df)

        assert "water_supply" in result.columns
        assert "lef" in result.columns
        assert "water" not in result.columns
        assert "LEF_tCO2_per_ha_yr" not in result.columns
        assert len(result) == 2
        assert result.iloc[0]["lef"] == pytest.approx(12.5)
        assert result.iloc[1]["lef"] == pytest.approx(8.3)

    def test_filters_non_finite_lef(self):
        """Rows with NaN or inf LEF should be filtered out."""
        df = pd.DataFrame(
            {
                "region": ["reg_a", "reg_b", "reg_c"],
                "resource_class": [1, 2, 3],
                "water": ["rainfed", "irrigated", "rainfed"],
                "use": ["cropland", "cropland", "cropland"],
                "LEF_tCO2_per_ha_yr": [12.5, np.nan, np.inf],
            }
        )
        result = _build_luc_lef_lookup(df)
        assert len(result) == 1
        assert result.iloc[0]["region"] == "reg_a"

    def test_empty_dataframe(self):
        """An empty input should return an empty DataFrame with correct columns."""
        df = pd.DataFrame()
        result = _build_luc_lef_lookup(df)
        assert result.empty
        assert list(result.columns) == [
            "region",
            "resource_class",
            "water_supply",
            "use",
            "lef",
            "conversion_share",
        ]

    def test_resource_class_is_integer(self):
        """Resource class should be cast to integer."""
        df = pd.DataFrame(
            {
                "region": ["reg_a"],
                "resource_class": [1.0],
                "water": ["rainfed"],
                "use": ["cropland"],
                "LEF_tCO2_per_ha_yr": [5.0],
            }
        )
        result = _build_luc_lef_lookup(df)
        assert result["resource_class"].dtype == int
