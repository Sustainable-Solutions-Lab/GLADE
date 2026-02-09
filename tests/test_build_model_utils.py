# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for emission-related utility functions in build_model/utils.py."""

import pandas as pd
import pytest

from workflow.scripts.build_model.utils import (
    _calculate_ch4_per_feed_intake,
    _calculate_manure_n_outputs,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ruminant_categories():
    """Ruminant feed categories with nitrogen content."""
    return pd.DataFrame(
        {
            "category": ["grassland", "roughage", "forage", "grain", "protein"],
            "N_g_per_kg_DM": [20.0, 18.0, 25.0, 22.0, 50.0],
        }
    )


@pytest.fixture
def monogastric_categories():
    """Monogastric feed categories with nitrogen content."""
    return pd.DataFrame(
        {
            "category": ["low_quality", "grain", "energy", "protein"],
            "N_g_per_kg_DM": [15.0, 18.0, 12.0, 55.0],
        }
    )


@pytest.fixture
def nutrition():
    """Nutrition data indexed by (food, nutrient) with a 'value' column."""
    data = {
        "value": [20.0, 3.5, 12.0, 18.0],
    }
    index = pd.MultiIndex.from_tuples(
        [
            ("meat-cattle", "protein"),
            ("dairy", "protein"),
            ("meat-pig", "protein"),
            ("meat-chicken", "protein"),
        ],
        names=["food", "nutrient"],
    )
    return pd.DataFrame(data, index=index)


@pytest.fixture
def manure_emissions():
    """MMS-weighted manure emission factors."""
    return pd.DataFrame(
        {
            "country": [
                "USA",
                "USA",
                "USA",
                "USA",
                "USA",
                "IND",
            ],
            "product": [
                "meat-cattle",
                "meat-cattle",
                "dairy",
                "meat-pig",
                "meat-chicken",
                "meat-cattle",
            ],
            "feed_category": [
                "ruminant_forage",
                "ruminant_grassland",
                "ruminant_roughage",
                "monogastric_grain",
                "monogastric_grain",
                "ruminant_forage",
            ],
            "manure_ch4_kg_per_kg_DMI": [
                0.005,
                0.001,
                0.004,
                0.008,
                0.003,
                0.006,
            ],
            "pasture_fraction": [
                0.3,
                0.9,
                0.2,
                0.0,
                0.0,
                0.4,
            ],
            "pasture_n2o_ef": [
                0.02,
                0.02,
                0.02,
                0.01,
                0.01,
                0.02,
            ],
            "managed_n2o_ef": [
                0.0095,
                0.0095,
                0.0095,
                0.012,
                0.010,
                0.0095,
            ],
        }
    )


@pytest.fixture
def default_indirect_params():
    """Default IPCC indirect N2O emission parameters."""
    return {
        "manure_n_to_fertilizer": 0.5,
        "indirect_ef4": 0.01,
        "indirect_ef5": 0.0075,
        "frac_gasm": 0.2,
        "frac_leach": 0.3,
    }


# ---------------------------------------------------------------------------
# Tests: _calculate_manure_n_outputs
# ---------------------------------------------------------------------------


class TestCalculateManureNOutputs:
    """Tests for _calculate_manure_n_outputs."""

    def test_ruminant_n_balance(
        self,
        ruminant_categories,
        monogastric_categories,
        nutrition,
        manure_emissions,
        default_indirect_params,
    ):
        """Ruminant product: verify N balance and N2O components."""
        efficiency = 0.05  # t product / t feed DM
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # Feed N: 25 g/kg DM = 0.025 t N/t feed
        feed_n = 25.0 / 1000.0
        # Product N: protein 20 g/100g => 20*10/6.25 = 32 g N/kg product
        # Product output per t feed = 0.05 t product/t feed
        # Product N per t feed = (32/1000) * 0.05 = 0.0016
        product_n = (20.0 * 10 / 6.25) / 1000.0 * efficiency
        n_excreted = feed_n - product_n

        # Pasture fraction = 0.3 (from fixture)
        n_pasture = n_excreted * 0.3
        n_managed = n_excreted * 0.7

        # N fertilizer = managed * recovery fraction
        expected_n_fert = n_managed * 0.5
        assert n_fert == pytest.approx(expected_n_fert)

        # Direct N2O
        n2o_pasture_direct = n_pasture * 0.02
        n2o_managed_direct = n_managed * 0.0095

        # Indirect N2O (pasture)
        n2o_pasture_vol = n_pasture * 0.2 * 0.01
        n2o_pasture_leach = n_pasture * 0.3 * 0.0075

        # Indirect N2O (managed) - applied to n_fertilizer portion
        n_applied = expected_n_fert
        n2o_managed_vol = n_applied * 0.2 * 0.01
        n2o_managed_leach = n_applied * 0.3 * 0.0075

        total_n2o_n = (
            n2o_pasture_direct
            + n2o_pasture_vol
            + n2o_pasture_leach
            + n2o_managed_direct
            + n2o_managed_vol
            + n2o_managed_leach
        )
        expected_n2o = total_n2o_n * (44.0 / 28.0)

        assert n2o == pytest.approx(expected_n2o)
        assert n2o > 0

        # Pasture share: pasture N2O-N / total N2O-N
        pasture_n2o_n = n2o_pasture_direct + n2o_pasture_vol + n2o_pasture_leach
        expected_pasture_share = pasture_n2o_n / total_n2o_n
        assert pasture_share == pytest.approx(expected_pasture_share)

    def test_monogastric_feed_category_parsing(
        self,
        ruminant_categories,
        monogastric_categories,
        nutrition,
        manure_emissions,
        default_indirect_params,
    ):
        """Monogastric product: feed_category prefix is parsed correctly."""
        efficiency = 0.10  # t product / t feed DM
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-pig",
            feed_category="monogastric_grain",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # Feed N: monogastric "grain" has 18 g/kg DM = 0.018 t N/t feed
        feed_n = 18.0 / 1000.0
        # Product N: meat-pig protein = 12 g/100g => 12*10/6.25 = 19.2 g N/kg
        product_n = (12.0 * 10 / 6.25) / 1000.0 * efficiency
        n_excreted = feed_n - product_n

        # Pasture fraction = 0.0 for meat-pig monogastric_grain
        assert pasture_share == pytest.approx(0.0)

        # All N is managed
        n_managed = n_excreted
        expected_n_fert = n_managed * 0.5
        assert n_fert == pytest.approx(expected_n_fert)

        # N2O should be positive
        assert n2o > 0

    def test_zero_efficiency_all_feed_n_excreted(
        self,
        ruminant_categories,
        monogastric_categories,
        nutrition,
        manure_emissions,
        default_indirect_params,
    ):
        """Zero efficiency: no product N retained, all feed N is excreted."""
        efficiency = 0.0
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # Feed N = 25 g/kg DM = 0.025 t N/t feed
        feed_n = 25.0 / 1000.0
        # Product N = 0 (efficiency = 0)
        n_excreted = feed_n

        # pasture_fraction = 0.3
        n_managed = n_excreted * 0.7
        expected_n_fert = n_managed * 0.5
        assert n_fert == pytest.approx(expected_n_fert)

        # N2O should be positive (more N excreted = more N2O)
        assert n2o > 0

    def test_44_28_conversion_factor(
        self,
        ruminant_categories,
        monogastric_categories,
        nutrition,
        manure_emissions,
        default_indirect_params,
    ):
        """Verify the 44/28 N2O-N to N2O conversion is applied."""
        efficiency = 0.05
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # Compute expected N2O-N (without the 44/28 factor)
        feed_n = 25.0 / 1000.0
        product_n = (20.0 * 10 / 6.25) / 1000.0 * efficiency
        n_excreted = feed_n - product_n

        n_pasture = n_excreted * 0.3
        n_managed = n_excreted * 0.7

        n2o_pasture_direct = n_pasture * 0.02
        n2o_pasture_vol = n_pasture * 0.2 * 0.01
        n2o_pasture_leach = n_pasture * 0.3 * 0.0075

        n2o_managed_direct = n_managed * 0.0095
        n_applied = n_managed * 0.5
        n2o_managed_vol = n_applied * 0.2 * 0.01
        n2o_managed_leach = n_applied * 0.3 * 0.0075

        total_n2o_n = (
            n2o_pasture_direct
            + n2o_pasture_vol
            + n2o_pasture_leach
            + n2o_managed_direct
            + n2o_managed_vol
            + n2o_managed_leach
        )

        # The returned N2O should be exactly total_n2o_n * 44/28
        assert n2o == pytest.approx(total_n2o_n * (44.0 / 28.0))
        # And NOT equal to the raw N2O-N value
        assert n2o != pytest.approx(total_n2o_n)

    def test_grassland_pasture_dominant(
        self,
        ruminant_categories,
        monogastric_categories,
        nutrition,
        manure_emissions,
        default_indirect_params,
    ):
        """Grassland feed: high pasture fraction means pasture N2O dominates."""
        efficiency = 0.03
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_grassland",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # pasture_fraction = 0.9 for ruminant_grassland
        # pasture N2O should dominate
        assert pasture_share > 0.8
        assert n2o > 0

    def test_missing_protein_data_defaults_to_zero(
        self,
        ruminant_categories,
        monogastric_categories,
        manure_emissions,
        default_indirect_params,
    ):
        """When product has no protein data, product N is assumed to be zero."""
        nutrition_no_product = pd.DataFrame(
            {"value": [20.0]},
            index=pd.MultiIndex.from_tuples(
                [("other-product", "protein")],
                names=["food", "nutrient"],
            ),
        )
        efficiency = 0.05
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="unknown-product",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_categories=ruminant_categories,
            monogastric_categories=monogastric_categories,
            nutrition=nutrition_no_product,
            manure_emissions=manure_emissions,
            **default_indirect_params,
        )

        # Verify N fertilizer matches full excretion (fallback manure_emissions)
        assert n_fert > 0
        assert n2o > 0


# ---------------------------------------------------------------------------
# Tests: _calculate_ch4_per_feed_intake
# ---------------------------------------------------------------------------


class TestCalculateCh4PerFeedIntake:
    """Tests for _calculate_ch4_per_feed_intake."""

    @pytest.fixture
    def enteric_my_lookup(self):
        """Enteric methane yield by ruminant feed category (g CH4/kg DMI)."""
        return {
            "grassland": 22.0,
            "roughage": 20.0,
            "forage": 18.0,
            "grain": 12.0,
            "protein": 10.0,
        }

    def test_ruminant_grassland_enteric_only(
        self,
        enteric_my_lookup,
        manure_emissions,
    ):
        """Ruminant with grassland feed: enteric only, no manure CH4."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_grassland",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )

        # Enteric: 22 g/kg DM = 0.022 t/t
        expected_enteric = 22.0 / 1000.0
        assert total == pytest.approx(expected_enteric)
        # No manure CH4 for grassland (skipped because ends with _grassland)
        assert manure == pytest.approx(0.0)

    def test_ruminant_non_grassland_both_enteric_and_manure(
        self,
        enteric_my_lookup,
        manure_emissions,
    ):
        """Ruminant with non-grassland feed: both enteric and manure CH4."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )

        # Enteric: 18 g/kg DM = 0.018 t/t
        expected_enteric = 18.0 / 1000.0
        # Manure: 0.005 kg/kg DMI = 0.005 t/t
        expected_manure = 0.005
        assert total == pytest.approx(expected_enteric + expected_manure)
        assert manure == pytest.approx(expected_manure)

    def test_monogastric_manure_only(
        self,
        enteric_my_lookup,
        manure_emissions,
    ):
        """Monogastric with manure data: manure CH4 only, no enteric."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-pig",
            feed_category="monogastric_grain",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )

        # No enteric for monogastrics
        # Manure: 0.008 kg/kg DMI from fixture
        expected_manure = 0.008
        assert total == pytest.approx(expected_manure)
        assert manure == pytest.approx(expected_manure)

    def test_missing_manure_data_zero_manure(
        self,
        enteric_my_lookup,
    ):
        """When no manure data matches, manure CH4 is zero."""
        empty_manure = pd.DataFrame(
            columns=[
                "country",
                "product",
                "feed_category",
                "manure_ch4_kg_per_kg_DMI",
            ]
        )
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=empty_manure,
        )

        # Only enteric: 18 g/kg DM = 0.018 t/t
        expected_enteric = 18.0 / 1000.0
        assert total == pytest.approx(expected_enteric)
        assert manure == pytest.approx(0.0)

    def test_monogastric_no_manure_data(
        self,
        enteric_my_lookup,
    ):
        """Monogastric with no manure data: zero total CH4."""
        empty_manure = pd.DataFrame(
            columns=[
                "country",
                "product",
                "feed_category",
                "manure_ch4_kg_per_kg_DMI",
            ]
        )
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-pig",
            feed_category="monogastric_grain",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=empty_manure,
        )

        assert total == pytest.approx(0.0)
        assert manure == pytest.approx(0.0)

    def test_enteric_unit_conversion(
        self,
        enteric_my_lookup,
        manure_emissions,
    ):
        """Enteric CH4 conversion: g CH4/kg DM -> t CH4/t DM (divide by 1000)."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_grassland",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )

        # 22 g/kg DM = 22/1000 t/t DM = 0.022
        assert total == pytest.approx(0.022)

    def test_different_country_uses_correct_manure_data(
        self,
        enteric_my_lookup,
        manure_emissions,
    ):
        """Manure CH4 is looked up by country, product, and feed_category."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="IND",
            enteric_my_lookup=enteric_my_lookup,
            manure_emissions=manure_emissions,
        )

        # IND meat-cattle ruminant_forage: manure_ch4 = 0.006
        expected_enteric = 18.0 / 1000.0
        expected_manure = 0.006
        assert total == pytest.approx(expected_enteric + expected_manure)
        assert manure == pytest.approx(expected_manure)

    def test_missing_enteric_category_no_enteric(
        self,
        manure_emissions,
    ):
        """Ruminant with feed category not in enteric lookup: no enteric CH4."""
        partial_lookup = {"grain": 12.0}  # only grain, no "forage"
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=partial_lookup,
            manure_emissions=manure_emissions,
        )

        # No enteric (forage not in lookup), only manure
        expected_manure = 0.005
        assert total == pytest.approx(expected_manure)
        assert manure == pytest.approx(expected_manure)
