# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
    _apply_food_loss_factor,
    _apply_food_waste_factor,
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
            "crop_production_multi",
            "animal_production",
            "food_processing",
            "food_consumption",
            "land_conversion",
            "new_to_pasture",
            "spare_land",
            "spare_existing_grassland",
            "yll_heart",
        ],
        unit="Mt",
    )

    # Add buses
    n.buses.add(
        [
            "crop:wheat:USA",
            "crop:maize:USA",
            "crop:soy:USA",
            "food:beef:USA",
            "food:flour:USA",
            "nutrient:protein:USA",
            "emission:ch4",
            "emission:n2o",
            "land:pasture:region1_c1",
            "water:region1",
        ],
        carrier=[
            "crop_wheat",
            "crop_maize",
            "crop_soy",
            "food_beef",
            "food_flour",
            "nutrient_protein",
            "ch4",
            "n2o",
            "land_pasture",
            "water",
        ],
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

    # Add crop production multi links
    n.links.add(
        ["produce_multi:wheat_soy_rainfed:region1"],
        bus0=["land:cropland:region1"],
        bus1=["crop:wheat:USA"],
        bus2=["crop:soy:USA"],
        bus3=["emission:n2o"],
        bus4=["water:region1"],
        carrier="crop_production_multi",
        efficiency=[2.0],
        efficiency2=[1.5],
        efficiency3=[0.1],
        efficiency4=[-0.5],
        marginal_cost=[0.2],
        crop=["wheat_soy"],
    )

    # Add animal production links
    n.links.add(
        ["animal:beef_grassfed:USA"],
        bus0=["feed:ruminant_forage:USA"],
        bus1=["food:beef:USA"],
        carrier="animal_production",
        efficiency=[0.05],
        efficiency2=[100.0],  # CH4 (enteric/manure)
        efficiency4=[5.0],  # N2O (manure)
        marginal_cost=[0.5],
    )

    # Add food processing links
    n.links.add(
        ["pathway:milling:USA"],
        bus0=["crop:wheat:USA"],
        bus1=["food:flour:USA"],
        carrier="food_processing",
        efficiency=[0.8],
    )

    # Add food consumption links
    n.links.add(
        ["consume:flour:USA"],
        bus0=["food:flour:USA"],
        bus1=["nutrient:protein:USA"],
        carrier="food_consumption",
        efficiency=[0.12],
        flw_multiplier=[1.25],
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

    # Add spare land links (sequestration credits, negative efficiency2)
    n.links.add(
        ["spare:wheat_rainfed:region1_c1_r"],
        bus0=["land:cropland:region1_c1_r"],
        bus1=["land:new:region1_c1_r"],
        carrier="spare_land",
        efficiency=[1.0],
        efficiency2=[-30.0],  # CO2 sequestration credit
    )
    n.links.add(
        ["spare:grassland:region1_c1"],
        bus0=["land:pasture:region1_c1"],
        bus1=["land:new:region1_c1_r"],
        carrier="spare_existing_grassland",
        efficiency=[1.0],
        efficiency2=[-10.0],  # CO2 sequestration credit
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
        """Test applying LUC emission factor to all LUC carriers."""
        n = mock_network
        orig_crop = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]
        orig_past = n.links.static.loc[
            "convert:new_to_pasture_nonforest:region1_c1", "efficiency2"
        ]
        orig_spare = n.links.static.loc[
            "spare:wheat_rainfed:region1_c1_r", "efficiency2"
        ]
        orig_spare_grass = n.links.static.loc[
            "spare:grassland:region1_c1", "efficiency2"
        ]

        _apply_emission_factors(n, {"luc": 0.5})

        result_crop = n.links.static.loc[
            "convert:new_land_forest:region1_c1_r", "efficiency2"
        ]
        result_past = n.links.static.loc[
            "convert:new_to_pasture_nonforest:region1_c1", "efficiency2"
        ]
        result_spare = n.links.static.loc[
            "spare:wheat_rainfed:region1_c1_r", "efficiency2"
        ]
        result_spare_grass = n.links.static.loc[
            "spare:grassland:region1_c1", "efficiency2"
        ]
        np.testing.assert_allclose(result_crop, orig_crop * 0.5)
        np.testing.assert_allclose(result_past, orig_past * 0.5)
        np.testing.assert_allclose(result_spare, orig_spare * 0.5)
        np.testing.assert_allclose(result_spare_grass, orig_spare_grass * 0.5)

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


class TestApplyFoodLossFactor:
    def test_crop_production_losses(self, mock_network):
        """Test applying food loss factor to crop production links."""
        n = mock_network
        # crop_production link: produce:wheat_rainfed:region1 (efficiency)
        orig_wheat = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        orig_maize = n.links.static.loc["produce:maize_rainfed:region1", "efficiency"]

        # crop_production_multi: produce_multi:wheat_soy_rainfed:region1
        # output crops are wheat (efficiency) and soy (efficiency2)
        # n2o emission (efficiency3) and water input (efficiency4) are secondary non-crops
        orig_multi_wheat = n.links.static.loc[
            "produce_multi:wheat_soy_rainfed:region1", "efficiency"
        ]
        orig_multi_soy = n.links.static.loc[
            "produce_multi:wheat_soy_rainfed:region1", "efficiency2"
        ]
        orig_multi_n2o = n.links.static.loc[
            "produce_multi:wheat_soy_rainfed:region1", "efficiency3"
        ]
        orig_multi_water = n.links.static.loc[
            "produce_multi:wheat_soy_rainfed:region1", "efficiency4"
        ]

        factor = 0.9
        _apply_food_loss_factor(n, factor)

        # Assert crop outputs on crop production links are scaled
        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            orig_wheat * factor,
        )
        np.testing.assert_allclose(
            n.links.static.loc["produce:maize_rainfed:region1", "efficiency"],
            orig_maize * factor,
        )
        np.testing.assert_allclose(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency"],
            orig_multi_wheat * factor,
        )
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "efficiency2"
            ],
            orig_multi_soy * factor,
        )

        # Assert non-crop outputs/inputs are NOT scaled
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "efficiency3"
            ],
            orig_multi_n2o,
        )
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "efficiency4"
            ],
            orig_multi_water,
        )

    def test_animal_production_losses(self, mock_network):
        """Test applying food loss factor to animal production links."""
        n = mock_network
        # animal_production link: animal:beef_grassfed:USA
        # Primary product beef (efficiency). secondary CH4 (efficiency2), N2O (efficiency4)
        orig_beef = n.links.static.loc["animal:beef_grassfed:USA", "efficiency"]
        orig_ch4 = n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"]
        orig_n2o = n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"]

        factor = 0.85
        _apply_food_loss_factor(n, factor)

        # Primary output should be scaled
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency"],
            orig_beef * factor,
        )

        # Secondary components (emissions/manure/feed) should remain unchanged
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"], orig_ch4
        )
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"], orig_n2o
        )

    def test_food_processing_is_invariant_to_loss(self, mock_network):
        """Test that food processing links are completely unaffected by loss."""
        n = mock_network
        orig_milling = n.links.static.loc["pathway:milling:USA", "efficiency"]

        _apply_food_loss_factor(n, 0.9)

        np.testing.assert_allclose(
            n.links.static.loc["pathway:milling:USA", "efficiency"], orig_milling
        )


class TestApplyFoodWasteFactor:
    def test_food_consumption_waste(self, mock_network):
        """Test applying food waste factor to food consumption links."""
        n = mock_network
        # food_consumption link: consume:flour:USA (efficiency and flw_multiplier)
        orig_eff = n.links.static.loc["consume:flour:USA", "efficiency"]
        orig_flw = n.links.static.loc["consume:flour:USA", "flw_multiplier"]

        factor = 0.95
        _apply_food_waste_factor(n, factor)

        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"], orig_eff * factor
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"], orig_flw * factor
        )

    def test_food_processing_is_invariant_to_waste(self, mock_network):
        """Test that food processing links are completely unaffected by waste."""
        n = mock_network
        orig_milling = n.links.static.loc["pathway:milling:USA", "efficiency"]

        _apply_food_waste_factor(n, 0.9)

        np.testing.assert_allclose(
            n.links.static.loc["pathway:milling:USA", "efficiency"], orig_milling
        )


class TestLegacyFoodLossWasteMapping:
    def test_legacy_only_config(self, mock_network):
        """Legacy food_loss_waste should map to both food_loss and food_waste."""
        n = mock_network
        orig_crop = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        orig_consume_eff = n.links.static.loc["consume:flour:USA", "efficiency"]
        orig_consume_flw = n.links.static.loc["consume:flour:USA", "flw_multiplier"]

        cfg = {"food_loss_waste": 0.9}
        apply_sensitivity_factors(n, cfg)

        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            orig_crop * 0.9,
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"],
            orig_consume_eff * 0.9,
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"],
            orig_consume_flw * 0.9,
        )

    def test_new_keys_precedence(self, mock_network):
        """New keys food_loss and food_waste should take precedence over food_loss_waste."""
        n = mock_network
        orig_crop = n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        orig_consume_eff = n.links.static.loc["consume:flour:USA", "efficiency"]
        orig_consume_flw = n.links.static.loc["consume:flour:USA", "flw_multiplier"]

        # If food_loss is specified, it overrides food_loss_waste for loss.
        # If food_waste is NOT specified, food_waste falls back to food_loss_waste.
        cfg = {"food_loss_waste": 0.9, "food_loss": 0.8}
        apply_sensitivity_factors(n, cfg)

        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            orig_crop * 0.8,
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"],
            orig_consume_eff * 0.9,
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"],
            orig_consume_flw * 0.9,
        )


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
