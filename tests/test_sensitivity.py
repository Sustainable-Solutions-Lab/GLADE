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
    _apply_fcr_factor,
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
            "food:tallow:USA",
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
            "food_tallow",
            "food_flour",
            "nutrient_protein",
            "ch4",
            "n2o",
            "land_pasture",
            "water",
        ],
    )

    # Add crop production links. ``loss_multiplier = 1 - loss_fraction``
    # is stored at build time so the food_loss sensitivity can recover
    # and rescale the underlying loss fraction. Here: wheat 10% loss
    # (mult=0.9), maize 20% loss (mult=0.8).
    n.links.add(
        ["produce:wheat_rainfed:region1", "produce:maize_rainfed:region1"],
        bus0=["land:cropland:region1", "land:cropland:region1"],
        bus1=["crop:wheat:USA", "crop:maize:USA"],
        carrier="crop_production",
        efficiency=[2.5, 4.0],
        marginal_cost=[0.1, 0.15],
        crop=["wheat", "maize"],
        loss_multiplier=[0.9, 0.8],
    )

    # Add crop production multi links. Loss multipliers are per output
    # bus (wheat at bus1 -> 0.9, soy at bus2 -> 0.85). Non-crop buses
    # have no loss_multiplier (NaN) and must not be touched.
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
        loss_multiplier=[0.9],
        loss_multiplier2=[0.85],
    )

    # Add animal production links. Beef 15% loss (mult=0.85). bus5 carries
    # a co-product (tallow) whose efficiency is structurally proportional
    # to the primary product efficiency.
    n.links.add(
        ["animal:beef_grassfed:USA"],
        bus0=["feed:ruminant_forage:USA"],
        bus1=["food:beef:USA"],
        carrier="animal_production",
        efficiency=[0.05],
        efficiency2=[100.0],  # CH4 (enteric/manure) - per-feed-unit
        efficiency4=[5.0],  # N2O (manure) - per-feed-unit
        bus5=["food:tallow:USA"],
        efficiency5=[0.01],  # Co-product, proportional to efficiency
        marginal_cost=[0.5],
        loss_multiplier=[0.85],
        loss_multiplier5=[0.85],
    )

    # Add food processing links (intentionally have no loss/waste hooks).
    n.links.add(
        ["pathway:milling:USA"],
        bus0=["crop:wheat:USA"],
        bus1=["food:flour:USA"],
        carrier="food_processing",
        efficiency=[0.8],
    )

    # Add food consumption links. flw_multiplier = 1 - waste_fraction.
    # Here 20% waste -> 0.8.
    n.links.add(
        ["consume:flour:USA"],
        bus0=["food:flour:USA"],
        bus1=["nutrient:protein:USA"],
        carrier="food_consumption",
        efficiency=[0.12],
        flw_multiplier=[0.8],
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


def _scaled_efficiency(orig_eff: float, old_mult: float, factor: float) -> float:
    """Expected new efficiency under flipped semantics.

    new_loss = clip(factor * (1 - old_mult), 0, 0.99)
    new_mult = 1 - new_loss
    new_eff  = orig_eff * (new_mult / old_mult)
    """
    new_loss = min(max(factor * (1.0 - old_mult), 0.0), 0.99)
    new_mult = 1.0 - new_loss
    return orig_eff * (new_mult / old_mult)


class TestApplyFoodLossFactor:
    def test_crop_production_losses(self, mock_network):
        """factor multiplies the per-link loss fraction (not the efficiency)."""
        n = mock_network
        # wheat link has loss_multiplier=0.9 (10% loss), maize 0.8 (20%).
        orig_wheat = float(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        )
        orig_maize = float(
            n.links.static.loc["produce:maize_rainfed:region1", "efficiency"]
        )

        # multi-crop link: wheat output (loss_multiplier=0.9), soy (0.85),
        # plus non-crop n2o (bus3) / water (bus4) outputs that must not move.
        orig_multi_wheat = float(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency"]
        )
        orig_multi_soy = float(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency2"]
        )
        orig_multi_n2o = float(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency3"]
        )
        orig_multi_water = float(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency4"]
        )

        factor = 1.5  # 50% more loss
        _apply_food_loss_factor(n, factor)

        # 10% loss * 1.5 -> 15%, mult 0.9 -> 0.85. Efficiency ratio 0.85/0.9.
        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            _scaled_efficiency(orig_wheat, 0.9, factor),
        )
        # 20% loss * 1.5 -> 30%, mult 0.8 -> 0.7.
        np.testing.assert_allclose(
            n.links.static.loc["produce:maize_rainfed:region1", "efficiency"],
            _scaled_efficiency(orig_maize, 0.8, factor),
        )
        # multi-crop wheat output scaled identically to wheat single-output.
        np.testing.assert_allclose(
            n.links.static.loc["produce_multi:wheat_soy_rainfed:region1", "efficiency"],
            _scaled_efficiency(orig_multi_wheat, 0.9, factor),
        )
        # multi-crop soy output: 15% loss * 1.5 -> 22.5%, mult 0.85 -> 0.775.
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "efficiency2"
            ],
            _scaled_efficiency(orig_multi_soy, 0.85, factor),
        )

        # Non-crop outputs/inputs untouched (no loss_multiplierN column).
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

        # Stored loss_multiplier columns are kept in sync.
        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "loss_multiplier"],
            0.85,
        )
        np.testing.assert_allclose(
            n.links.static.loc["produce:maize_rainfed:region1", "loss_multiplier"],
            0.7,
        )
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "loss_multiplier"
            ],
            0.85,
        )
        np.testing.assert_allclose(
            n.links.static.loc[
                "produce_multi:wheat_soy_rainfed:region1", "loss_multiplier2"
            ],
            0.775,
        )

    def test_animal_production_losses(self, mock_network):
        """Animal primary efficiency scales by the loss-fraction ratio."""
        n = mock_network
        # beef link has loss_multiplier=0.85 (15% loss).
        orig_beef = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency"])
        orig_ch4 = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"])
        orig_n2o = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"])

        factor = 0.5  # halve the loss fraction
        _apply_food_loss_factor(n, factor)

        # 15% * 0.5 -> 7.5%, mult 0.85 -> 0.925.
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency"],
            _scaled_efficiency(orig_beef, 0.85, factor),
        )
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "loss_multiplier"],
            0.925,
        )

        # Emissions / N2O on non-food output buses untouched.
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"], orig_ch4
        )
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"], orig_n2o
        )

    def test_animal_co_product_losses_scale_consistently(self, mock_network):
        """Co-product (bus5) efficiency scales by the same loss ratio as bus1.

        Mirrors the FCR co-product test: the primary product and any
        co-product whose yield is structurally proportional to it must
        rescale together. Otherwise food-loss sensitivity breaks the
        physical primary:co-product mass ratio.
        """
        n = mock_network
        orig_beef = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency"])
        orig_tallow = float(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency5"]
        )

        factor = 0.5
        _apply_food_loss_factor(n, factor)

        new_beef = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency"])
        new_tallow = float(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency5"]
        )

        # Primary and co-product must scale by identical ratio.
        np.testing.assert_allclose(new_beef / orig_beef, new_tallow / orig_tallow)
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "loss_multiplier5"], 0.925
        )

    def test_factor_one_is_noop(self, mock_network):
        """factor=1.0 leaves efficiency and loss_multiplier untouched."""
        n = mock_network
        before = n.links.static[["efficiency", "loss_multiplier"]].copy()
        _apply_food_loss_factor(n, 1.0)
        after = n.links.static[["efficiency", "loss_multiplier"]]
        pd.testing.assert_frame_equal(before, after)

    def test_food_processing_is_invariant_to_loss(self, mock_network):
        """food_processing has no loss_multiplier column; left alone."""
        n = mock_network
        orig_milling = n.links.static.loc["pathway:milling:USA", "efficiency"]

        _apply_food_loss_factor(n, 1.5)

        np.testing.assert_allclose(
            n.links.static.loc["pathway:milling:USA", "efficiency"], orig_milling
        )

    def test_loss_fraction_clipped(self, mock_network):
        """A very large factor clamps loss at _MAX_LOSS_FRACTION."""
        n = mock_network
        # maize: 20% loss * 100 = 2000%, clipped to 99% -> mult 0.01.
        orig_maize = float(
            n.links.static.loc["produce:maize_rainfed:region1", "efficiency"]
        )
        _apply_food_loss_factor(n, 100.0)
        np.testing.assert_allclose(
            n.links.static.loc["produce:maize_rainfed:region1", "loss_multiplier"],
            0.01,
        )
        np.testing.assert_allclose(
            n.links.static.loc["produce:maize_rainfed:region1", "efficiency"],
            orig_maize * (0.01 / 0.8),
        )


class TestApplyFoodWasteFactor:
    def test_food_consumption_waste(self, mock_network):
        """factor multiplies the waste fraction, not the efficiency directly."""
        n = mock_network
        # consume link has flw_multiplier=0.8 (20% waste), efficiency=0.12.
        orig_eff = float(n.links.static.loc["consume:flour:USA", "efficiency"])

        factor = 1.5  # 20% waste -> 30% waste, mult 0.8 -> 0.7
        _apply_food_waste_factor(n, factor)

        expected_mult = 0.7
        ratio = expected_mult / 0.8
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"], expected_mult
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"], orig_eff * ratio
        )

    def test_factor_one_is_noop(self, mock_network):
        """factor=1.0 leaves efficiency and flw_multiplier untouched."""
        n = mock_network
        before_eff = float(n.links.static.loc["consume:flour:USA", "efficiency"])
        before_mult = float(n.links.static.loc["consume:flour:USA", "flw_multiplier"])
        _apply_food_waste_factor(n, 1.0)
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"], before_eff
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"], before_mult
        )

    def test_food_processing_is_invariant_to_waste(self, mock_network):
        """food_processing links carry no waste hook; left alone."""
        n = mock_network
        orig_milling = n.links.static.loc["pathway:milling:USA", "efficiency"]

        _apply_food_waste_factor(n, 1.5)

        np.testing.assert_allclose(
            n.links.static.loc["pathway:milling:USA", "efficiency"], orig_milling
        )


class TestFoodLossWasteBundle:
    def test_bundle_only_config(self, mock_network):
        """food_loss_waste applies the same factor to both loss and waste."""
        n = mock_network
        orig_wheat = float(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        )
        orig_consume_eff = float(n.links.static.loc["consume:flour:USA", "efficiency"])

        cfg = {"food_loss_waste": 1.5}
        apply_sensitivity_factors(n, cfg)

        # Loss side: wheat 10% loss -> 15%, mult 0.9 -> 0.85.
        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            _scaled_efficiency(orig_wheat, 0.9, 1.5),
        )
        # Waste side: 20% waste -> 30%, mult 0.8 -> 0.7.
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"], 0.7
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"],
            orig_consume_eff * (0.7 / 0.8),
        )

    def test_component_keys_override_bundle(self, mock_network):
        """food_loss / food_waste override food_loss_waste when both set."""
        n = mock_network
        orig_wheat = float(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"]
        )
        orig_consume_eff = float(n.links.static.loc["consume:flour:USA", "efficiency"])

        # bundle=1.5 would scale both; explicit food_loss=2.0 overrides loss,
        # food_waste falls through to the bundle value (1.5).
        cfg = {"food_loss_waste": 1.5, "food_loss": 2.0}
        apply_sensitivity_factors(n, cfg)

        # Wheat: 10% loss * 2.0 -> 20%, mult 0.9 -> 0.8.
        np.testing.assert_allclose(
            n.links.static.loc["produce:wheat_rainfed:region1", "efficiency"],
            _scaled_efficiency(orig_wheat, 0.9, 2.0),
        )
        # Waste uses bundle factor 1.5.
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "flw_multiplier"], 0.7
        )
        np.testing.assert_allclose(
            n.links.static.loc["consume:flour:USA", "efficiency"],
            orig_consume_eff * (0.7 / 0.8),
        )


class TestApplyFcrFactor:
    def test_scales_primary_and_coproduct_food_outputs(self, mock_network):
        """FCR scales every food-output bus on animal links (incl. co-products)."""
        n = mock_network
        orig_beef = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency"])
        orig_tallow = float(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency5"]
        )
        orig_ch4 = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"])
        orig_n2o = float(n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"])

        factor = 1.25
        _apply_fcr_factor(n, factor)

        # Primary food output and co-product scale by the same factor, so
        # their physical ratio is preserved.
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency"],
            orig_beef * factor,
        )
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency5"],
            orig_tallow * factor,
        )
        np.testing.assert_allclose(
            (
                n.links.static.loc["animal:beef_grassfed:USA", "efficiency5"]
                / n.links.static.loc["animal:beef_grassfed:USA", "efficiency"]
            ),
            orig_tallow / orig_beef,
        )

        # Per-feed-unit outputs (CH4, N2O on non-food carriers) are untouched.
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency2"], orig_ch4
        )
        np.testing.assert_allclose(
            n.links.static.loc["animal:beef_grassfed:USA", "efficiency4"], orig_n2o
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
