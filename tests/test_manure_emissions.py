# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for manure emission calculations."""

import pandas as pd
import pytest

from workflow.scripts.calculate_manure_emissions import (
    EF1_APPLICATION,
    EF3PRP_CATTLE,
    EF3PRP_OTHER,
    M3_CH4_TO_KG,
    MANURE_N_RECOVERY,
    MONOGASTRIC_LPS_MAPPING,
    PRODUCT_TO_EF3PRP,
    PRODUCT_TO_URINARY_CATEGORY,
    URINARY_FRACTIONS,
    average_mcf_over_climate_zones,
    calculate_manure_ch4_for_product,
    calculate_n2o_factors_for_feed_category,
    calculate_volatile_solids,
    get_mms_fractions_for_lps,
    get_mms_fractions_for_product,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify that key constants have the expected IPCC values."""

    def test_urinary_fractions(self):
        assert URINARY_FRACTIONS["ruminant"] == pytest.approx(0.04)
        assert URINARY_FRACTIONS["pig"] == pytest.approx(0.02)
        assert URINARY_FRACTIONS["chicken"] == pytest.approx(0.0)

    def test_m3_ch4_to_kg(self):
        assert pytest.approx(0.67) == M3_CH4_TO_KG

    def test_ef3prp_values(self):
        assert pytest.approx(0.02) == EF3PRP_CATTLE
        assert pytest.approx(0.01) == EF3PRP_OTHER

    def test_manure_n_recovery(self):
        assert pytest.approx(0.75) == MANURE_N_RECOVERY

    def test_ef1_application(self):
        assert pytest.approx(0.006) == EF1_APPLICATION

    def test_product_to_ef3prp_cattle(self):
        """Cattle and buffalo products should use the higher EF3PRP."""
        for product in ["meat-cattle", "dairy", "dairy-buffalo"]:
            assert PRODUCT_TO_EF3PRP[product] == pytest.approx(EF3PRP_CATTLE)

    def test_product_to_ef3prp_other(self):
        """Sheep, pig, chicken, eggs should use the lower EF3PRP."""
        for product in ["meat-sheep", "meat-pig", "meat-chicken", "eggs"]:
            assert PRODUCT_TO_EF3PRP[product] == pytest.approx(EF3PRP_OTHER)

    def test_product_to_urinary_category_mapping(self):
        assert PRODUCT_TO_URINARY_CATEGORY["meat-cattle"] == "ruminant"
        assert PRODUCT_TO_URINARY_CATEGORY["dairy"] == "ruminant"
        assert PRODUCT_TO_URINARY_CATEGORY["meat-pig"] == "pig"
        assert PRODUCT_TO_URINARY_CATEGORY["meat-chicken"] == "chicken"
        assert PRODUCT_TO_URINARY_CATEGORY["eggs"] == "chicken"

    def test_monogastric_lps_mapping(self):
        assert MONOGASTRIC_LPS_MAPPING["meat-pig"] == ["Industrial", "Intermediate"]
        assert MONOGASTRIC_LPS_MAPPING["meat-chicken"] == ["Broiler"]
        assert MONOGASTRIC_LPS_MAPPING["eggs"] == ["Layer"]


# ---------------------------------------------------------------------------
# Fixtures: reusable test data
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_feed_categories():
    """Feed categories with known digestibility and ash content."""
    return pd.DataFrame(
        {
            "category": ["grain", "forage"],
            "digestibility": [0.80, 0.55],
            "ash_content_pct_dm": [5.0, 10.0],
        }
    )


@pytest.fixture
def mms_fractions_df():
    """MMS fractions table mimicking GLEAM structure.

    Columns: area, animal, lps, and then one column per MMS type (values in %).
    """
    return pd.DataFrame(
        {
            "area": ["Global", "Global", "Global", "Global"],
            "animal": ["Cattle", "Cattle", "Pigs", "Chickens"],
            "lps": ["Grassland", "Mixed", "Industrial", "Broiler"],
            "pasture & paddock": [70.0, 20.0, 0.0, 0.0],
            "drylot": [10.0, 30.0, 0.0, 0.0],
            "liquid/slurry": [5.0, 40.0, 80.0, 0.0],
            "solid storage": [15.0, 10.0, 20.0, 100.0],
        }
    )


@pytest.fixture
def n2o_efs_df():
    """N2O emission factors by MMS type."""
    return pd.DataFrame(
        {
            "mms_type": [
                "pasture & paddock",
                "drylot",
                "liquid/slurry",
                "solid storage",
            ],
            "storage_ef": [0.0, 0.02, 0.005, 0.01],
            "is_pasture": [True, False, False, False],
        }
    )


@pytest.fixture
def mcf_data_df():
    """MCF data with multiple climate zones per MMS type."""
    return pd.DataFrame(
        {
            "manure management system": [
                "pasture & paddock",
                "pasture & paddock",
                "drylot",
                "drylot",
                "liquid/slurry",
                "liquid/slurry",
                "solid storage",
                "solid storage",
            ],
            "climate zone": [
                "temperate",
                "tropical",
                "temperate",
                "tropical",
                "temperate",
                "tropical",
                "temperate",
                "tropical",
            ],
            "methane conversion factor": [
                0.01,
                0.02,
                0.02,
                0.04,
                0.40,
                0.80,
                0.04,
                0.06,
            ],
        }
    )


@pytest.fixture
def b0_data_df():
    """B0 values for animal products."""
    return pd.DataFrame(
        {
            "animal product": ["meat-cattle", "meat-pig", "meat-chicken"],
            "B0": [0.24, 0.45, 0.36],
        }
    )


# ---------------------------------------------------------------------------
# Test calculate_volatile_solids
# ---------------------------------------------------------------------------


class TestCalculateVolatileSolids:
    """Tests for calculate_volatile_solids."""

    def test_cattle_known_values(self, simple_feed_categories):
        """Hand-compute VS for cattle (urinary=0.04).

        For grain: VS = (1 - 0.80 + 0.04) * (1 - 5.0/100)
                      = 0.24 * 0.95 = 0.228
        For forage: VS = (1 - 0.55 + 0.04) * (1 - 10.0/100)
                       = 0.49 * 0.90 = 0.441
        """
        result = calculate_volatile_solids(simple_feed_categories, "meat-cattle")
        assert "VS_kg_per_kg_DMI" in result.columns
        assert result.loc[0, "VS_kg_per_kg_DMI"] == pytest.approx(0.228)
        assert result.loc[1, "VS_kg_per_kg_DMI"] == pytest.approx(0.441)

    def test_pig_known_values(self, simple_feed_categories):
        """Hand-compute VS for pig (urinary=0.02).

        For grain: VS = (1 - 0.80 + 0.02) * (1 - 5.0/100)
                      = 0.22 * 0.95 = 0.209
        For forage: VS = (1 - 0.55 + 0.02) * (1 - 10.0/100)
                       = 0.47 * 0.90 = 0.423
        """
        result = calculate_volatile_solids(simple_feed_categories, "meat-pig")
        assert result.loc[0, "VS_kg_per_kg_DMI"] == pytest.approx(0.209)
        assert result.loc[1, "VS_kg_per_kg_DMI"] == pytest.approx(0.423)

    def test_chicken_known_values(self, simple_feed_categories):
        """Hand-compute VS for chicken (urinary=0.0).

        For grain: VS = (1 - 0.80 + 0.0) * (1 - 5.0/100)
                      = 0.20 * 0.95 = 0.190
        For forage: VS = (1 - 0.55 + 0.0) * (1 - 10.0/100)
                       = 0.45 * 0.90 = 0.405
        """
        result = calculate_volatile_solids(simple_feed_categories, "meat-chicken")
        assert result.loc[0, "VS_kg_per_kg_DMI"] == pytest.approx(0.190)
        assert result.loc[1, "VS_kg_per_kg_DMI"] == pytest.approx(0.405)

    def test_eggs_uses_chicken_urinary(self, simple_feed_categories):
        """Eggs should use the same urinary fraction as chicken (0.0)."""
        result_chicken = calculate_volatile_solids(
            simple_feed_categories, "meat-chicken"
        )
        result_eggs = calculate_volatile_solids(simple_feed_categories, "eggs")
        pd.testing.assert_series_equal(
            result_chicken["VS_kg_per_kg_DMI"],
            result_eggs["VS_kg_per_kg_DMI"],
        )

    def test_dairy_uses_ruminant_urinary(self, simple_feed_categories):
        """Dairy should use the same urinary fraction as cattle (0.04)."""
        result_cattle = calculate_volatile_solids(simple_feed_categories, "meat-cattle")
        result_dairy = calculate_volatile_solids(simple_feed_categories, "dairy")
        pd.testing.assert_series_equal(
            result_cattle["VS_kg_per_kg_DMI"],
            result_dairy["VS_kg_per_kg_DMI"],
        )

    def test_does_not_modify_input(self, simple_feed_categories):
        """The function should not modify the input DataFrame."""
        original = simple_feed_categories.copy()
        calculate_volatile_solids(simple_feed_categories, "meat-cattle")
        pd.testing.assert_frame_equal(simple_feed_categories, original)

    def test_zero_digestibility(self):
        """Extreme case: digestibility = 0, ash = 0 => VS = 1 + urinary."""
        df = pd.DataFrame(
            {
                "category": ["low_quality"],
                "digestibility": [0.0],
                "ash_content_pct_dm": [0.0],
            }
        )
        result = calculate_volatile_solids(df, "meat-cattle")
        # VS = (1 - 0 + 0.04) * (1 - 0) = 1.04
        assert result.loc[0, "VS_kg_per_kg_DMI"] == pytest.approx(1.04)

    def test_full_digestibility(self):
        """Extreme case: digestibility = 1.0 => VS comes only from urinary."""
        df = pd.DataFrame(
            {
                "category": ["perfect_feed"],
                "digestibility": [1.0],
                "ash_content_pct_dm": [0.0],
            }
        )
        result = calculate_volatile_solids(df, "meat-pig")
        # VS = (1 - 1.0 + 0.02) * (1 - 0) = 0.02
        assert result.loc[0, "VS_kg_per_kg_DMI"] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Test average_mcf_over_climate_zones
# ---------------------------------------------------------------------------


class TestAverageMcfOverClimateZones:
    """Tests for average_mcf_over_climate_zones."""

    def test_simple_averaging(self, mcf_data_df):
        """Average two climate zones per MMS type."""
        result = average_mcf_over_climate_zones(mcf_data_df)
        assert set(result.columns) == {
            "manure management system",
            "methane conversion factor",
        }
        # pasture & paddock: (0.01 + 0.02) / 2 = 0.015
        row = result[result["manure management system"] == "pasture & paddock"]
        assert row["methane conversion factor"].values[0] == pytest.approx(0.015)
        # drylot: (0.02 + 0.04) / 2 = 0.03
        row = result[result["manure management system"] == "drylot"]
        assert row["methane conversion factor"].values[0] == pytest.approx(0.03)
        # liquid/slurry: (0.40 + 0.80) / 2 = 0.60
        row = result[result["manure management system"] == "liquid/slurry"]
        assert row["methane conversion factor"].values[0] == pytest.approx(0.60)
        # solid storage: (0.04 + 0.06) / 2 = 0.05
        row = result[result["manure management system"] == "solid storage"]
        assert row["methane conversion factor"].values[0] == pytest.approx(0.05)

    def test_single_climate_zone(self):
        """With one climate zone, the average is the same value."""
        df = pd.DataFrame(
            {
                "manure management system": ["lagoon", "pit"],
                "climate zone": ["temperate", "temperate"],
                "methane conversion factor": [0.70, 0.30],
            }
        )
        result = average_mcf_over_climate_zones(df)
        assert len(result) == 2
        row_lagoon = result[result["manure management system"] == "lagoon"]
        assert row_lagoon["methane conversion factor"].values[0] == pytest.approx(0.70)

    def test_three_climate_zones(self):
        """Average across three climate zones."""
        df = pd.DataFrame(
            {
                "manure management system": ["lagoon"] * 3,
                "climate zone": ["cool", "temperate", "warm"],
                "methane conversion factor": [0.3, 0.6, 0.9],
            }
        )
        result = average_mcf_over_climate_zones(df)
        assert len(result) == 1
        assert result["methane conversion factor"].values[0] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Test get_mms_fractions_for_product
# ---------------------------------------------------------------------------


class TestGetMmsFractionsForProduct:
    """Tests for get_mms_fractions_for_product."""

    def test_cattle_product_averages_over_lps(self, mms_fractions_df):
        """meat-cattle maps to Cattle; should average Grassland + Mixed LPS."""
        result = get_mms_fractions_for_product(mms_fractions_df, "meat-cattle")

        # Check that fraction values are in [0, 1] (converted from percent)
        assert (result["fraction"] >= 0).all()
        assert (result["fraction"] <= 1).all()

        # Expected: average of Grassland and Mixed rows, divided by 100
        # pasture & paddock: (70 + 20) / 2 / 100 = 0.45
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.45)

        # drylot: (10 + 30) / 2 / 100 = 0.20
        drylot_row = result[result["manure management system"] == "drylot"]
        assert drylot_row["fraction"].values[0] == pytest.approx(0.20)

        # liquid/slurry: (5 + 40) / 2 / 100 = 0.225
        liquid_row = result[result["manure management system"] == "liquid/slurry"]
        assert liquid_row["fraction"].values[0] == pytest.approx(0.225)

        # solid storage: (15 + 10) / 2 / 100 = 0.125
        solid_row = result[result["manure management system"] == "solid storage"]
        assert solid_row["fraction"].values[0] == pytest.approx(0.125)

    def test_dairy_maps_to_cattle(self, mms_fractions_df):
        """dairy also maps to Cattle; should get same result as meat-cattle."""
        result_cattle = get_mms_fractions_for_product(mms_fractions_df, "meat-cattle")
        result_dairy = get_mms_fractions_for_product(mms_fractions_df, "dairy")
        pd.testing.assert_frame_equal(
            result_cattle.reset_index(drop=True),
            result_dairy.reset_index(drop=True),
        )

    def test_pig_product(self, mms_fractions_df):
        """meat-pig maps to Pigs; only one LPS row (Industrial)."""
        result = get_mms_fractions_for_product(mms_fractions_df, "meat-pig")
        # pasture & paddock: 0 / 100 = 0.0
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.0)
        # liquid/slurry: 80 / 100 = 0.80
        liquid_row = result[result["manure management system"] == "liquid/slurry"]
        assert liquid_row["fraction"].values[0] == pytest.approx(0.80)

    def test_chicken_product(self, mms_fractions_df):
        """meat-chicken maps to Chickens; only one LPS row (Broiler)."""
        result = get_mms_fractions_for_product(mms_fractions_df, "meat-chicken")
        # solid storage: 100 / 100 = 1.0
        solid_row = result[result["manure management system"] == "solid storage"]
        assert solid_row["fraction"].values[0] == pytest.approx(1.0)

    def test_unknown_product_raises(self, mms_fractions_df):
        """Unknown product should raise ValueError."""
        with pytest.raises(ValueError, match="No GLEAM animal mapping"):
            get_mms_fractions_for_product(mms_fractions_df, "meat-horse")

    def test_returns_long_format(self, mms_fractions_df):
        """Result should have 'manure management system' and 'fraction' columns."""
        result = get_mms_fractions_for_product(mms_fractions_df, "meat-cattle")
        assert "manure management system" in result.columns
        assert "fraction" in result.columns
        # Should have one row per MMS type (4 types in our fixture)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Test get_mms_fractions_for_lps
# ---------------------------------------------------------------------------


class TestGetMmsFractionsForLps:
    """Tests for get_mms_fractions_for_lps."""

    def test_grassland_lps_for_cattle(self, mms_fractions_df):
        """Filter to Grassland LPS for cattle products."""
        result = get_mms_fractions_for_lps(
            mms_fractions_df, "meat-cattle", ["Grassland"]
        )
        # pasture & paddock: 70 / 100 = 0.70
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.70)
        # drylot: 10 / 100 = 0.10
        drylot_row = result[result["manure management system"] == "drylot"]
        assert drylot_row["fraction"].values[0] == pytest.approx(0.10)

    def test_mixed_lps_for_cattle(self, mms_fractions_df):
        """Filter to Mixed LPS for cattle products."""
        result = get_mms_fractions_for_lps(mms_fractions_df, "meat-cattle", ["Mixed"])
        # pasture & paddock: 20 / 100 = 0.20
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.20)
        # liquid/slurry: 40 / 100 = 0.40
        liquid_row = result[result["manure management system"] == "liquid/slurry"]
        assert liquid_row["fraction"].values[0] == pytest.approx(0.40)

    def test_grassland_vs_mixed_differ(self, mms_fractions_df):
        """Grassland and Mixed LPS should give different fractions."""
        grassland = get_mms_fractions_for_lps(
            mms_fractions_df, "meat-cattle", ["Grassland"]
        )
        mixed = get_mms_fractions_for_lps(mms_fractions_df, "meat-cattle", ["Mixed"])
        # The pasture fractions should differ
        g_pasture = grassland[
            grassland["manure management system"] == "pasture & paddock"
        ]["fraction"].values[0]
        m_pasture = mixed[mixed["manure management system"] == "pasture & paddock"][
            "fraction"
        ].values[0]
        assert g_pasture != pytest.approx(m_pasture)
        # Grassland should have higher pasture fraction
        assert g_pasture > m_pasture

    def test_unknown_product_raises(self, mms_fractions_df):
        """Unknown product should raise ValueError."""
        with pytest.raises(ValueError, match="No GLEAM animal mapping"):
            get_mms_fractions_for_lps(mms_fractions_df, "meat-horse", ["Mixed"])

    def test_unavailable_lps_raises(self, mms_fractions_df):
        """LPS type not in data should raise ValueError."""
        with pytest.raises(ValueError, match="No MMS data"):
            get_mms_fractions_for_lps(mms_fractions_df, "meat-cattle", ["Backyard"])

    def test_broiler_lps_for_chicken(self, mms_fractions_df):
        """Broiler LPS for meat-chicken."""
        result = get_mms_fractions_for_lps(
            mms_fractions_df, "meat-chicken", ["Broiler"]
        )
        # solid storage: 100 / 100 = 1.0
        solid_row = result[result["manure management system"] == "solid storage"]
        assert solid_row["fraction"].values[0] == pytest.approx(1.0)
        # pasture & paddock: 0 / 100 = 0.0
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.0)

    def test_multiple_lps_types_averaged(self, mms_fractions_df):
        """When two LPS types are provided, values are averaged."""
        # Add an Intermediate row for Pigs to the fixture
        extra_row = pd.DataFrame(
            {
                "area": ["Global"],
                "animal": ["Pigs"],
                "lps": ["Intermediate"],
                "pasture & paddock": [10.0],
                "drylot": [20.0],
                "liquid/slurry": [50.0],
                "solid storage": [20.0],
            }
        )
        mms_extended = pd.concat([mms_fractions_df, extra_row], ignore_index=True)
        result = get_mms_fractions_for_lps(
            mms_extended, "meat-pig", ["Industrial", "Intermediate"]
        )
        # liquid/slurry: (80 + 50) / 2 / 100 = 0.65
        liquid_row = result[result["manure management system"] == "liquid/slurry"]
        assert liquid_row["fraction"].values[0] == pytest.approx(0.65)
        # pasture & paddock: (0 + 10) / 2 / 100 = 0.05
        pasture_row = result[result["manure management system"] == "pasture & paddock"]
        assert pasture_row["fraction"].values[0] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Test calculate_n2o_factors_for_feed_category
# ---------------------------------------------------------------------------


class TestCalculateN2oFactorsForFeedCategory:
    """Tests for calculate_n2o_factors_for_feed_category."""

    def test_ruminant_forage_uses_mixed_lps(self, mms_fractions_df, n2o_efs_df):
        """All ruminant feed categories should use Mixed LPS."""
        result = calculate_n2o_factors_for_feed_category(
            "meat-cattle",
            "ruminant_forage",
            mms_fractions_df,
            n2o_efs_df,
        )

        # Mixed LPS for Cattle: pasture & paddock = 20%
        assert result["pasture_fraction"] == pytest.approx(0.20)
        # Cattle uses EF3PRP_CATTLE
        assert result["pasture_n2o_ef"] == pytest.approx(EF3PRP_CATTLE)

    def test_sheep_uses_ef3prp_other(self, mms_fractions_df, n2o_efs_df):
        """Sheep should use EF3PRP_OTHER."""
        # Need Sheep with Mixed LPS in the MMS data
        sheep_row = pd.DataFrame(
            {
                "area": ["Global"],
                "animal": ["Sheep"],
                "lps": ["Mixed"],
                "pasture & paddock": [60.0],
                "drylot": [10.0],
                "liquid/slurry": [10.0],
                "solid storage": [20.0],
            }
        )
        mms_with_sheep = pd.concat([mms_fractions_df, sheep_row], ignore_index=True)
        result = calculate_n2o_factors_for_feed_category(
            "meat-sheep",
            "ruminant_forage",
            mms_with_sheep,
            n2o_efs_df,
        )
        assert result["pasture_n2o_ef"] == pytest.approx(EF3PRP_OTHER)

    def test_managed_n2o_ef_formula(self, mms_fractions_df, n2o_efs_df):
        """Managed EF = storage_ef + recovery * application_ef.

        For Cattle Mixed LPS:
          non-pasture MMS = drylot (30%), liquid/slurry (40%), solid storage (10%)
          total non-pasture = 80%
          weighted storage_ef = (0.30*0.02 + 0.40*0.005 + 0.10*0.01) / 0.80
                              = (0.006 + 0.002 + 0.001) / 0.80
                              = 0.009 / 0.80
                              = 0.01125
          managed_ef = 0.01125 + 0.75 * 0.006 = 0.01125 + 0.0045 = 0.01575
        """
        result = calculate_n2o_factors_for_feed_category(
            "meat-cattle",
            "ruminant_forage",
            mms_fractions_df,
            n2o_efs_df,
        )
        assert result["storage_n2o_ef"] == pytest.approx(0.01125)
        assert result["managed_n2o_ef"] == pytest.approx(
            0.01125 + MANURE_N_RECOVERY * EF1_APPLICATION
        )

    def test_ruminant_roughage_uses_mixed_lps(self, mms_fractions_df, n2o_efs_df):
        """Verify storage EF for Cattle Mixed LPS with roughage feed.

        non-pasture MMS = drylot (30%), liquid/slurry (40%), solid storage (10%)
        total non-pasture = 80%
        weighted storage_ef = (0.30*0.02 + 0.40*0.005 + 0.10*0.01) / 0.80
                            = (0.006 + 0.002 + 0.001) / 0.80
                            = 0.009 / 0.80
                            = 0.01125
        """
        result = calculate_n2o_factors_for_feed_category(
            "meat-cattle",
            "ruminant_roughage",
            mms_fractions_df,
            n2o_efs_df,
        )
        assert result["storage_n2o_ef"] == pytest.approx(0.01125)

    def test_monogastric_uses_product_lps_mapping(self, mms_fractions_df, n2o_efs_df):
        """Monogastrics should use MONOGASTRIC_LPS_MAPPING."""
        result = calculate_n2o_factors_for_feed_category(
            "meat-chicken",
            "monogastric_grain",
            mms_fractions_df,
            n2o_efs_df,
        )
        # Broiler LPS for Chickens: pasture & paddock = 0%
        assert result["pasture_fraction"] == pytest.approx(0.0)
        # meat-chicken uses EF3PRP_OTHER
        assert result["pasture_n2o_ef"] == pytest.approx(EF3PRP_OTHER)

    def test_all_pasture_storage_ef_zero(self, n2o_efs_df):
        """If all manure goes to pasture, storage EF should be 0."""
        mms_all_pasture = pd.DataFrame(
            {
                "area": ["Global"],
                "animal": ["Cattle"],
                "lps": ["Mixed"],
                "pasture & paddock": [100.0],
                "drylot": [0.0],
                "liquid/slurry": [0.0],
                "solid storage": [0.0],
            }
        )
        result = calculate_n2o_factors_for_feed_category(
            "meat-cattle",
            "ruminant_forage",
            mms_all_pasture,
            n2o_efs_df,
        )
        assert result["pasture_fraction"] == pytest.approx(1.0)
        assert result["storage_n2o_ef"] == pytest.approx(0.0)
        # managed_n2o_ef = 0 + recovery * application
        assert result["managed_n2o_ef"] == pytest.approx(
            MANURE_N_RECOVERY * EF1_APPLICATION
        )

    def test_result_keys(self, mms_fractions_df, n2o_efs_df):
        """Result dict should have the expected keys."""
        result = calculate_n2o_factors_for_feed_category(
            "meat-cattle",
            "ruminant_forage",
            mms_fractions_df,
            n2o_efs_df,
        )
        expected_keys = {
            "pasture_fraction",
            "pasture_n2o_ef",
            "storage_n2o_ef",
            "managed_n2o_ef",
        }
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test calculate_manure_ch4_for_product
# ---------------------------------------------------------------------------


class TestCalculateManureCh4ForProduct:
    """Tests for calculate_manure_ch4_for_product."""

    def test_simple_ch4_calculation(self):
        """Verify full CH4 pipeline with hand-computed values.

        Setup:
        - Single feed category: digestibility=0.70, ash=5%
        - Product: meat-cattle (urinary=0.04)
        - B0 = 0.24 m3 CH4 / kg VS
        - Single MMS: liquid/slurry at 100%, MCF = 0.50

        VS = (1 - 0.70 + 0.04) * (1 - 5/100) = 0.34 * 0.95 = 0.323
        weighted_MCF = 1.0 * 0.50 = 0.50
        CH4 = 0.323 * 0.24 * 0.50 * 0.67 = 0.025933...
        """
        feed_categories = pd.DataFrame(
            {
                "category": ["concentrate"],
                "digestibility": [0.70],
                "ash_content_pct_dm": [5.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-cattle"], "B0": [0.24]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["liquid/slurry"],
                "methane conversion factor": [0.50],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["liquid/slurry"],
                "fraction": [1.0],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-cattle", feed_categories, b0_data, mcf_avg, mms_fractions
        )

        expected_vs = 0.34 * 0.95  # = 0.323
        expected_ch4 = expected_vs * 0.24 * 0.50 * M3_CH4_TO_KG
        assert result["manure_ch4_kg_per_kg_DMI"].values[0] == pytest.approx(
            expected_ch4
        )

    def test_multiple_feed_categories(self):
        """Test with two feed categories producing different CH4 values."""
        feed_categories = pd.DataFrame(
            {
                "category": ["grain", "forage"],
                "digestibility": [0.80, 0.55],
                "ash_content_pct_dm": [5.0, 10.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-cattle"], "B0": [0.24]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "methane conversion factor": [0.05],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "fraction": [1.0],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-cattle", feed_categories, b0_data, mcf_avg, mms_fractions
        )

        # grain: VS = 0.24 * 0.95 = 0.228; CH4 = 0.228 * 0.24 * 0.05 * 0.67
        expected_grain = 0.228 * 0.24 * 0.05 * M3_CH4_TO_KG
        # forage: VS = 0.49 * 0.90 = 0.441; CH4 = 0.441 * 0.24 * 0.05 * 0.67
        expected_forage = 0.441 * 0.24 * 0.05 * M3_CH4_TO_KG

        assert result.loc[0, "manure_ch4_kg_per_kg_DMI"] == pytest.approx(
            expected_grain
        )
        assert result.loc[1, "manure_ch4_kg_per_kg_DMI"] == pytest.approx(
            expected_forage
        )

    def test_weighted_mcf_from_multiple_mms(self):
        """Test that multiple MMS types produce a weighted MCF.

        Two MMS: 60% liquid/slurry (MCF=0.50), 40% solid storage (MCF=0.05)
        weighted_MCF = 0.60 * 0.50 + 0.40 * 0.05 = 0.30 + 0.02 = 0.32
        """
        feed_categories = pd.DataFrame(
            {
                "category": ["grain"],
                "digestibility": [0.80],
                "ash_content_pct_dm": [5.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-pig"], "B0": [0.45]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["liquid/slurry", "solid storage"],
                "methane conversion factor": [0.50, 0.05],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["liquid/slurry", "solid storage"],
                "fraction": [0.60, 0.40],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-pig", feed_categories, b0_data, mcf_avg, mms_fractions
        )

        # VS for pig: (1 - 0.80 + 0.02) * (1 - 0.05) = 0.22 * 0.95 = 0.209
        # weighted_MCF = 0.60 * 0.50 + 0.40 * 0.05 = 0.32
        # CH4 = 0.209 * 0.45 * 0.32 * 0.67
        expected_ch4 = 0.209 * 0.45 * 0.32 * M3_CH4_TO_KG
        assert result["manure_ch4_kg_per_kg_DMI"].values[0] == pytest.approx(
            expected_ch4
        )

    def test_result_has_product_column(self):
        """Result should have a 'product' column with the product name."""
        feed_categories = pd.DataFrame(
            {
                "category": ["grain"],
                "digestibility": [0.80],
                "ash_content_pct_dm": [5.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-chicken"], "B0": [0.36]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "methane conversion factor": [0.05],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "fraction": [1.0],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-chicken", feed_categories, b0_data, mcf_avg, mms_fractions
        )
        assert "product" in result.columns
        assert result["product"].values[0] == "meat-chicken"

    def test_result_has_category_column(self):
        """Result should have a 'category' column from the feed categories."""
        feed_categories = pd.DataFrame(
            {
                "category": ["concentrate", "roughage"],
                "digestibility": [0.75, 0.50],
                "ash_content_pct_dm": [3.0, 8.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-cattle"], "B0": [0.24]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "methane conversion factor": [0.05],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["solid storage"],
                "fraction": [1.0],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-cattle", feed_categories, b0_data, mcf_avg, mms_fractions
        )
        assert "category" in result.columns
        assert list(result["category"]) == ["concentrate", "roughage"]

    def test_zero_mcf_produces_zero_ch4(self):
        """If MCF is 0 (e.g., all pasture), CH4 should be 0."""
        feed_categories = pd.DataFrame(
            {
                "category": ["grain"],
                "digestibility": [0.70],
                "ash_content_pct_dm": [5.0],
            }
        )
        b0_data = pd.DataFrame({"animal product": ["meat-cattle"], "B0": [0.24]})
        mcf_avg = pd.DataFrame(
            {
                "manure management system": ["pasture"],
                "methane conversion factor": [0.0],
            }
        )
        mms_fractions = pd.DataFrame(
            {
                "manure management system": ["pasture"],
                "fraction": [1.0],
            }
        )

        result = calculate_manure_ch4_for_product(
            "meat-cattle", feed_categories, b0_data, mcf_avg, mms_fractions
        )
        assert result["manure_ch4_kg_per_kg_DMI"].values[0] == pytest.approx(0.0)
