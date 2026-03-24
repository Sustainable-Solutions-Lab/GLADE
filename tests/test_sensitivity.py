# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the sensitivity adjustment module."""

import numpy as np
import pandas as pd
import pypsa
import pytest

from workflow.scripts.solve_model.health import _expand_rr_groups
from workflow.scripts.solve_model.sensitivity import (
    _apply_cost_factors,
    _apply_crop_yield_factors,
    _apply_emission_factors,
    apply_sensitivity_factors,
)


@pytest.fixture
def mock_network():
    """Create a minimal mock network for testing."""
    n = pypsa.Network()

    # Add carriers
    n.carriers.add(
        [
            "crop_production",
            "animal_production",
            "land_conversion",
            "new_to_pasture",
            "yll_heart",
        ],
        unit="Mt",
    )

    # Add buses
    n.buses.add(
        [
            "crop:wheat:USA",
            "crop:maize:USA",
            "food:beef:USA",
            "emission:ch4",
            "land:pasture:region1_c1",
        ],
        carrier=["crop_wheat", "crop_maize", "food_beef", "ch4", "land_pasture"],
    )

    # Add crop production links
    n.links.add(
        ["produce:wheat_rainfed:region1", "produce:maize_rainfed:region1"],
        bus0=["land:cropland:region1", "land:cropland:region1"],
        bus1=["crop:wheat:USA", "crop:maize:USA"],
        carrier="crop_production",
        efficiency=[2.5, 4.0],
        marginal_cost=[0.1, 0.15],
        crop=["wheat", "maize"],
    )

    # Add animal production links
    n.links.add(
        ["animal:beef_grassfed:USA"],
        bus0=["feed:ruminant_forage:USA"],
        bus1=["food:beef:USA"],
        carrier="animal_production",
        efficiency=[0.05],
        efficiency2=[100.0],  # CH4
        efficiency4=[5.0],  # N2O
        marginal_cost=[0.5],
    )

    # Add land conversion links (forest/nonforest split)
    n.links.add(
        ["convert:new_land_forest:region1_c1_r"],
        bus0=["land:new:region1_c1_r"],
        bus1=["land:cropland:region1_c1_r"],
        carrier="land_conversion",
        efficiency=[1.0],
        efficiency2=[50.0],  # CO2 emissions
    )
    n.links.add(
        ["convert:new_to_pasture_nonforest:region1_c1"],
        bus0=["land:new:region1_c1_r"],
        bus1=["land:pasture:region1_c1"],
        carrier="new_to_pasture",
        efficiency=[1.0],
        efficiency2=[20.0],  # CO2 emissions
    )

    # Add health stores
    n.stores.add(
        ["store:yll:B01:cluster001", "store:yll:B02:cluster001"],
        bus="health:cluster:001",
        carrier=["yll_B01", "yll_B02"],
        rr_ref=[1.5, 2.0],
    )

    return n


class TestApplyCropYieldFactors:
    def test_all_factor(self, mock_network):
        """Test applying a global yield factor to all crops."""
        n = mock_network
        original = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "efficiency"
        ].copy()

        _apply_crop_yield_factors(n, {"all": 0.9})

        result = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "efficiency"
        ]
        np.testing.assert_allclose(result.values, original.values * 0.9)

    def test_by_crop_factor(self, mock_network):
        """Test applying crop-specific factors."""
        n = mock_network
        original_wheat = n.links.static.loc[
            "produce:wheat_rainfed:region1", "efficiency"
        ]
        original_maize = n.links.static.loc[
            "produce:maize_rainfed:region1", "efficiency"
        ]

        _apply_crop_yield_factors(n, {"by_crop": {"wheat": 0.8}})

        result_wheat = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        result_maize = n.links.static.loc["produce:maize_rainfed:region1", "efficiency"]

        np.testing.assert_allclose(result_wheat, original_wheat * 0.8)
        np.testing.assert_allclose(result_maize, original_maize)  # Unchanged

    def test_combined_factors(self, mock_network):
        """Test that all factor is applied first, then per-crop factors."""
        n = mock_network
        original_wheat = n.links.static.loc[
            "produce:wheat_rainfed:region1", "efficiency"
        ]
        original_maize = n.links.static.loc[
            "produce:maize_rainfed:region1", "efficiency"
        ]

        _apply_crop_yield_factors(n, {"all": 0.9, "by_crop": {"wheat": 1.1}})

        result_wheat = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        result_maize = n.links.static.loc["produce:maize_rainfed:region1", "efficiency"]

        # Wheat gets both: 0.9 * 1.1 = 0.99
        np.testing.assert_allclose(result_wheat, original_wheat * 0.9 * 1.1)
        # Maize only gets global factor
        np.testing.assert_allclose(result_maize, original_maize * 0.9)

    def test_factor_of_one_is_noop(self, mock_network):
        """Test that factor of 1.0 doesn't change values."""
        n = mock_network
        original = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "efficiency"
        ].copy()

        _apply_crop_yield_factors(n, {"all": 1.0, "by_crop": {"wheat": 1.0}})

        result = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "efficiency"
        ]
        np.testing.assert_allclose(result.values, original.values)


class TestApplyEmissionFactors:
    def test_ch4_factor(self, mock_network):
        """Test applying CH4 emission factor."""
        n = mock_network
        original = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]

        _apply_emission_factors(n, {"ch4": 1.2})

        result = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]
        np.testing.assert_allclose(result, original * 1.2)

    def test_n2o_factor(self, mock_network):
        """Test applying N2O emission factor."""
        n = mock_network
        original = n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"]

        _apply_emission_factors(n, {"n2o": 0.8})

        result = n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"]
        np.testing.assert_allclose(result, original * 0.8)

    def test_luc_factor(self, mock_network):
        """Test applying LUC emission factor to both land_conversion and new_to_pasture."""
        n = mock_network
        orig_crop = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]
        orig_past = n.links.static.loc[
            "convert:new_to_pasture_nonforest:region1_c1", "efficiency2"
        ]

        _apply_emission_factors(n, {"luc": 0.5})

        result_crop = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]
        result_past = n.links.static.loc[
            "convert:new_to_pasture_nonforest:region1_c1", "efficiency2"
        ]
        np.testing.assert_allclose(result_crop, orig_crop * 0.5)
        np.testing.assert_allclose(result_past, orig_past * 0.5)

    def test_multiple_factors(self, mock_network):
        """Test applying multiple emission factors simultaneously."""
        n = mock_network
        original_ch4 = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]
        original_n2o = n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"]
        original_luc = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]

        _apply_emission_factors(n, {"ch4": 1.3, "n2o": 0.7, "luc": 1.5})

        result_ch4 = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]
        result_n2o = n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"]
        result_luc = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]

        np.testing.assert_allclose(result_ch4, original_ch4 * 1.3)
        np.testing.assert_allclose(result_n2o, original_n2o * 0.7)
        np.testing.assert_allclose(result_luc, original_luc * 1.5)


class TestApplyCostFactors:
    def test_crop_cost_factor(self, mock_network):
        """Test applying crop cost factor."""
        n = mock_network
        original = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "marginal_cost"
        ].copy()

        _apply_cost_factors(n, {"crop": 1.5})

        result = n.links.static.loc[
            n.links.static["carrier"] == "crop_production", "marginal_cost"
        ]
        np.testing.assert_allclose(result.values, original.values * 1.5)

    def test_animal_cost_factor(self, mock_network):
        """Test applying animal cost factor."""
        n = mock_network
        original = n.links.static.loc["animal:beef_grassfed:USA", "marginal_cost"]

        _apply_cost_factors(n, {"animal": 2.0})

        result = n.links.static.loc["animal:beef_grassfed:USA", "marginal_cost"]
        np.testing.assert_allclose(result, original * 2.0)


class TestApplySensitivityFactors:
    def test_full_config(self, mock_network):
        """Test applying a complete sensitivity configuration."""
        n = mock_network
        original_yield = n.links.static.loc[
            "produce:wheat_rainfed:region1", "efficiency"
        ]
        original_ch4 = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]

        cfg = {
            "crop_yields": {"all": 0.95},
            "emission_factors": {"ch4": 1.1},
        }
        apply_sensitivity_factors(n, cfg)

        result_yield = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        result_ch4 = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]

        np.testing.assert_allclose(result_yield, original_yield * 0.95)
        np.testing.assert_allclose(result_ch4, original_ch4 * 1.1)

    def test_health_rr_config_ignored_at_build_time(self, mock_network):
        """Test that health_relative_risk in config is ignored at build time.

        Health RR sensitivity is now applied at solve time via per-risk-factor
        quantile interpolation, not at build time.
        """
        n = mock_network
        original_rr = n.stores.static.loc[
            n.stores.static["carrier"].str.startswith("yll_"), "rr_ref"
        ].copy()

        cfg = {
            "health_relative_risk": {"fruits": 0.5, "vegetables": 0.8},
        }
        apply_sensitivity_factors(n, cfg)

        # rr_ref should be unchanged — health RR is now handled at solve time
        result_rr = n.stores.static.loc[
            n.stores.static["carrier"].str.startswith("yll_"), "rr_ref"
        ]
        np.testing.assert_allclose(result_rr.values, original_rr.values)

    def test_empty_config_is_noop(self, mock_network):
        """Test that empty config doesn't modify network."""
        n = mock_network
        original_links = n.links.static.copy()
        original_stores = n.stores.static.copy()

        apply_sensitivity_factors(n, {})

        pd.testing.assert_frame_equal(n.links.static, original_links)
        pd.testing.assert_frame_equal(n.stores.static, original_stores)

    def test_none_config_is_noop(self, mock_network):
        """Test that None config doesn't modify network."""
        n = mock_network
        original_links = n.links.static.copy()
        original_stores = n.stores.static.copy()

        apply_sensitivity_factors(n, None)

        pd.testing.assert_frame_equal(n.links.static, original_links)
        pd.testing.assert_frame_equal(n.stores.static, original_stores)


@pytest.fixture
def risk_breakpoints():
    """Create risk breakpoints with protective and harmful factors."""
    return pd.DataFrame(
        {
            "health_cluster": 0,
            "risk_factor": [
                # whole_grains: protective (log_rr decreases with intake)
                "whole_grains",
                "whole_grains",
                "whole_grains",
                # legumes: protective
                "legumes",
                "legumes",
                # red_meat: harmful (log_rr increases with intake)
                "red_meat",
                "red_meat",
                "red_meat",
            ],
            "intake_g_per_day": [0, 50, 100, 0, 50, 0, 50, 100],
            "cause": "cvd",
            "log_rr": [0.0, -0.1, -0.2, 0.0, -0.15, 0.0, 0.1, 0.2],
            "log_rr_low": 0.0,
            "log_rr_high": 0.0,
        }
    )


class TestExpandRrGroups:
    def test_protective_group(self, risk_breakpoints):
        """Protective group expands to all decreasing-RR risk factors."""
        result = _expand_rr_groups({"protective": 0.5}, risk_breakpoints)
        assert result == {"whole_grains": 0.5, "legumes": 0.5}

    def test_harmful_group(self, risk_breakpoints):
        """Harmful group expands to all increasing-RR risk factors."""
        result = _expand_rr_groups({"harmful": 0.3}, risk_breakpoints)
        assert result == {"red_meat": 0.3}

    def test_both_groups(self, risk_breakpoints):
        """Both groups expand simultaneously."""
        result = _expand_rr_groups(
            {"protective": 0.5, "harmful": 0.8}, risk_breakpoints
        )
        assert result == {"whole_grains": 0.5, "legumes": 0.5, "red_meat": 0.8}

    def test_individual_keys_passthrough(self, risk_breakpoints):
        """Individual risk factor keys pass through unchanged."""
        result = _expand_rr_groups({"whole_grains": 0.7}, risk_breakpoints)
        assert result == {"whole_grains": 0.7}

    def test_no_groups_passthrough(self, risk_breakpoints):
        """Dict without group keys is returned unchanged."""
        original = {"whole_grains": 0.5, "red_meat": 0.3}
        result = _expand_rr_groups(original, risk_breakpoints)
        assert result == original

    def test_overlap_raises(self, risk_breakpoints):
        """Overlap between group and individual key raises ValueError."""
        with pytest.raises(ValueError, match="whole_grains.*protective"):
            _expand_rr_groups(
                {"protective": 0.5, "whole_grains": 0.7}, risk_breakpoints
            )
