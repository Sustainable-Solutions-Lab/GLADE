# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for emission-related utility functions in build_model/utils.py."""

import pytest

from workflow.scripts.build_model.utils import (
    _calculate_ch4_per_feed_intake,
    _calculate_manure_n_outputs,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ruminant_n_lookup():
    """Ruminant feed N content lookup (g N/kg DM) keyed by category."""
    return {"roughage": 18.0, "forage": 25.0, "grain": 22.0, "protein": 50.0}


@pytest.fixture
def monogastric_n_lookup():
    """Monogastric feed N content lookup (g N/kg DM) keyed by category."""
    return {"low_quality": 15.0, "grain": 18.0, "protein": 55.0}


@pytest.fixture
def product_protein_lookup():
    """Product protein lookup (g protein/100g product) keyed by product."""
    return {
        "meat-cattle": 20.0,
        "dairy": 3.5,
        "meat-pig": 12.0,
        "meat-chicken": 18.0,
    }


@pytest.fixture
def manure_n2o_lookup():
    """MMS N2O factors keyed by (product, feed_category).

    Triple is (pasture_fraction, pasture_n2o_ef, storage_n2o_ef).
    """
    return {
        ("meat-cattle", "ruminant_forage"): (0.3, 0.02, 0.005),
        ("dairy", "ruminant_roughage"): (0.2, 0.02, 0.005),
        ("meat-pig", "monogastric_grain"): (0.0, 0.01, 0.008),
        ("meat-chicken", "monogastric_grain"): (0.0, 0.01, 0.006),
    }


@pytest.fixture
def manure_n2o_by_product_lookup():
    """Fallback MMS N2O factors keyed by product only."""
    return {}


@pytest.fixture
def manure_ch4_lookup():
    """Manure CH4 emission factors keyed by (country, product, feed_category)."""
    return {
        ("USA", "meat-cattle", "ruminant_forage"): 0.005,
        ("USA", "dairy", "ruminant_roughage"): 0.004,
        ("USA", "meat-pig", "monogastric_grain"): 0.008,
        ("USA", "meat-chicken", "monogastric_grain"): 0.003,
        ("IND", "meat-cattle", "ruminant_forage"): 0.006,
    }


@pytest.fixture
def default_indirect_params():
    """Default IPCC indirect N2O emission parameters."""
    return {
        "manure_n_to_fertilizer": 0.5,
        "indirect_ef4": 0.01,
        "indirect_ef5": 0.0075,
        "organic_n2o_factor": 0.006,
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
        ruminant_n_lookup,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """Ruminant product: verify N balance and N2O components."""
        efficiency = 0.05  # t product / t feed DM
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
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

        # Direct N2O. Storage applies to all managed N; application EF1
        # (organic_n2o_factor=0.006) applies to the actual applied flow.
        n2o_pasture_direct = n_pasture * 0.02
        n_applied = expected_n_fert
        n2o_managed_direct = n_managed * 0.005 + n_applied * 0.006

        # Indirect N2O (pasture)
        n2o_pasture_vol = n_pasture * 0.2 * 0.01
        n2o_pasture_leach = n_pasture * 0.3 * 0.0075

        # Indirect N2O (managed) - applied to n_fertilizer portion
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
        ruminant_n_lookup,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """Monogastric product: feed_category prefix is parsed correctly."""
        efficiency = 0.10  # t product / t feed DM
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-pig",
            feed_category="monogastric_grain",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
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
        ruminant_n_lookup,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """Zero efficiency: no product N retained, all feed N is excreted."""
        efficiency = 0.0
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
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
        ruminant_n_lookup,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """Verify the 44/28 N2O-N to N2O conversion is applied."""
        efficiency = 0.05
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
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

        n_applied = n_managed * 0.5
        n2o_managed_direct = n_managed * 0.005 + n_applied * 0.006
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

    def test_forage_pasture_present(
        self,
        ruminant_n_lookup,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """Forage feed: pasture fraction from MMS data contributes to N2O."""
        efficiency = 0.03
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="meat-cattle",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
            **default_indirect_params,
        )

        # pasture_fraction = 0.3 for ruminant_forage (from fixture)
        assert pasture_share > 0.0
        assert n2o > 0

    def test_negative_excretion_clamped_to_zero(
        self,
        monogastric_n_lookup,
        product_protein_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """High-efficiency, low-feed-N combinations cannot push n_excreted < 0.

        With low-N roughage (e.g. 8 g/kg DM) and a high-yield dairy link
        (efficiency ~1.6 t milk per t feed DM), the raw difference
        feed_N - product_N is negative. Without clamping this would flip the
        fertilizer and N2O outputs into negative coefficients that the
        optimizer could exploit. We require the clamp to make both outputs
        non-negative.
        """
        low_n_ruminant = {
            "forage": 8.0,
            "roughage": 8.0,
            "grain": 22.0,
            "protein": 50.0,
        }
        n_fert, n2o, _ = _calculate_manure_n_outputs(
            product="dairy",
            feed_category="ruminant_roughage",
            efficiency=1.6,
            ruminant_n_lookup=low_n_ruminant,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup=product_protein_lookup,
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
            **default_indirect_params,
        )

        # Raw N excretion would be 0.008 - (3.5*10/6.25)/1000 * 1.6 = -0.00096
        # After clamp: zero excretion, zero fertilizer N, zero N2O.
        assert n_fert == pytest.approx(0.0)
        assert n2o == pytest.approx(0.0)

    def test_missing_protein_data_defaults_to_zero(
        self,
        ruminant_n_lookup,
        monogastric_n_lookup,
        manure_n2o_lookup,
        manure_n2o_by_product_lookup,
        default_indirect_params,
    ):
        """When product has no protein data, product N is assumed to be zero."""
        efficiency = 0.05
        n_fert, n2o, pasture_share = _calculate_manure_n_outputs(
            product="unknown-product",
            feed_category="ruminant_forage",
            efficiency=efficiency,
            ruminant_n_lookup=ruminant_n_lookup,
            monogastric_n_lookup=monogastric_n_lookup,
            product_protein_lookup={},
            manure_n2o_lookup=manure_n2o_lookup,
            manure_n2o_by_product_lookup=manure_n2o_by_product_lookup,
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
            "roughage": 20.0,
            "forage": 18.0,
            "grain": 12.0,
            "protein": 10.0,
        }

    def test_ruminant_forage_enteric_and_manure(
        self,
        enteric_my_lookup,
        manure_ch4_lookup,
    ):
        """Ruminant forage feed: both enteric and manure CH4."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
        )

        # Enteric: 18 g/kg DM = 0.018 t/t
        expected_enteric = 18.0 / 1000.0
        # Manure: 0.005 kg/kg DMI = 0.005 t/t (from fixture)
        expected_manure = 0.005
        assert total == pytest.approx(expected_enteric + expected_manure)
        assert manure == pytest.approx(expected_manure)

    def test_ruminant_non_grassland_both_enteric_and_manure(
        self,
        enteric_my_lookup,
        manure_ch4_lookup,
    ):
        """Ruminant with non-grassland feed: both enteric and manure CH4."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
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
        manure_ch4_lookup,
    ):
        """Monogastric with manure data: manure CH4 only, no enteric."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-pig",
            feed_category="monogastric_grain",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
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
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup={},
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
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-pig",
            feed_category="monogastric_grain",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup={},
        )

        assert total == pytest.approx(0.0)
        assert manure == pytest.approx(0.0)

    def test_enteric_unit_conversion(
        self,
        enteric_my_lookup,
        manure_ch4_lookup,
    ):
        """Enteric CH4 conversion: g CH4/kg DM -> t CH4/t DM (divide by 1000)."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_roughage",
            country="USA",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
        )

        # 20 g/kg DM = 20/1000 t/t DM = 0.020, no manure data for roughage/USA
        assert total == pytest.approx(0.020)

    def test_different_country_uses_correct_manure_data(
        self,
        enteric_my_lookup,
        manure_ch4_lookup,
    ):
        """Manure CH4 is looked up by country, product, and feed_category."""
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="IND",
            enteric_my_lookup=enteric_my_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
        )

        # IND meat-cattle ruminant_forage: manure_ch4 = 0.006
        expected_enteric = 18.0 / 1000.0
        expected_manure = 0.006
        assert total == pytest.approx(expected_enteric + expected_manure)
        assert manure == pytest.approx(expected_manure)

    def test_missing_enteric_category_no_enteric(
        self,
        manure_ch4_lookup,
    ):
        """Ruminant with feed category not in enteric lookup: no enteric CH4."""
        partial_lookup = {"grain": 12.0}  # only grain, no "forage"
        total, manure = _calculate_ch4_per_feed_intake(
            product="meat-cattle",
            feed_category="ruminant_forage",
            country="USA",
            enteric_my_lookup=partial_lookup,
            manure_ch4_lookup=manure_ch4_lookup,
        )

        # No enteric (forage not in lookup), only manure
        expected_manure = 0.005
        assert total == pytest.approx(expected_manure)
        assert manure == pytest.approx(expected_manure)
