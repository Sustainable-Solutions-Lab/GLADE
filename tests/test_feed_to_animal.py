# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for feed-to-animal-product conversion efficiencies."""

import pandas as pd
import pytest

from workflow.scripts.build_feed_to_animal_products import (
    calculate_feed_efficiencies,
    calculate_ruminant_me_requirements,
    get_monogastric_me_requirements,
)

# ---------------------------------------------------------------------------
# Tests: calculate_ruminant_me_requirements
# ---------------------------------------------------------------------------


class TestCalculateRuminantMeRequirements:
    """Tests for NE-to-ME conversion for ruminant products."""

    def test_beef_me_calculation(self):
        """Beef ME = NE_m/k_m + NE_g/k_g, divided by carcass_to_retail."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "meat-cattle"],
                "region": ["North America", "North America"],
                "unit": ["NE_m", "NE_g"],
                "value": [10.0, 5.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            carcass_to_retail={"meat-cattle": 0.7},
            feed_proxy_map={},
        )
        # ME_carcass = 10/0.6 + 5/0.4 = 16.667 + 12.5 = 29.167
        # ME_retail = 29.167 / 0.7 = 41.667
        assert len(result) == 1
        assert result["animal_product"].iloc[0] == "meat-cattle"
        assert result["ME_MJ_per_kg"].iloc[0] == pytest.approx(
            (10.0 / 0.6 + 5.0 / 0.4) / 0.7
        )

    def test_dairy_me_calculation(self):
        """Dairy ME = NE_l/k_l + NE_m/k_m + NE_g/k_g, no carcass conversion."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["dairy", "dairy", "dairy"],
                "region": ["Europe", "Europe", "Europe"],
                "unit": ["NE_l", "NE_m", "NE_g"],
                "value": [3.0, 8.0, 2.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.65,
            carcass_to_retail={"dairy": 1.0},
            feed_proxy_map={},
        )
        expected = 3.0 / 0.65 + 8.0 / 0.6 + 2.0 / 0.4
        assert len(result) == 1
        assert result["animal_product"].iloc[0] == "dairy"
        assert result["ME_MJ_per_kg"].iloc[0] == pytest.approx(expected)

    def test_non_ruminants_skipped(self):
        """Non-ruminant products (e.g. meat-pig) are skipped."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": [
                    "meat-pig",
                    "meat-pig",
                    "meat-cattle",
                    "meat-cattle",
                ],
                "region": ["Europe", "Europe", "Europe", "Europe"],
                "unit": ["ME", "ME", "NE_m", "NE_g"],
                "value": [30.0, 30.0, 10.0, 5.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            carcass_to_retail={"meat-cattle": 0.7, "meat-pig": 0.8},
            feed_proxy_map={},
        )
        products = result["animal_product"].unique().tolist()
        assert "meat-pig" not in products
        assert "meat-cattle" in products

    def test_multiple_regions(self):
        """Each region gets its own ME requirement."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": [
                    "meat-cattle",
                    "meat-cattle",
                    "meat-cattle",
                    "meat-cattle",
                ],
                "region": [
                    "North America",
                    "North America",
                    "Sub-Saharan Africa",
                    "Sub-Saharan Africa",
                ],
                "unit": ["NE_m", "NE_g", "NE_m", "NE_g"],
                "value": [10.0, 5.0, 15.0, 8.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            carcass_to_retail={"meat-cattle": 0.7},
            feed_proxy_map={},
        )
        assert len(result) == 2
        na_row = result[result["region"] == "North America"]
        ssa_row = result[result["region"] == "Sub-Saharan Africa"]
        assert na_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(
            (10.0 / 0.6 + 5.0 / 0.4) / 0.7
        )
        assert ssa_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(
            (15.0 / 0.6 + 8.0 / 0.4) / 0.7
        )

    def test_proxy_product_copies_source(self):
        """Proxy products copy from their source with same carcass_to_retail."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["dairy", "dairy", "dairy"],
                "region": ["Europe", "Europe", "Europe"],
                "unit": ["NE_l", "NE_m", "NE_g"],
                "value": [3.0, 8.0, 2.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.65,
            carcass_to_retail={"dairy": 1.0, "dairy-buffalo": 1.0},
            feed_proxy_map={"dairy-buffalo": "dairy"},
        )
        dairy_row = result[result["animal_product"] == "dairy"]
        buffalo_row = result[result["animal_product"] == "dairy-buffalo"]
        assert len(dairy_row) == 1
        assert len(buffalo_row) == 1
        assert buffalo_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(
            dairy_row["ME_MJ_per_kg"].iloc[0]
        )

    def test_proxy_with_different_carcass_factor(self):
        """Proxy products adjust ME if carcass_to_retail differs from source."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "meat-cattle"],
                "region": ["Europe", "Europe"],
                "unit": ["NE_m", "NE_g"],
                "value": [10.0, 5.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            carcass_to_retail={"meat-cattle": 0.7, "meat-buffalo": 0.5},
            feed_proxy_map={"meat-buffalo": "meat-cattle"},
        )
        cattle_me = result[result["animal_product"] == "meat-cattle"][
            "ME_MJ_per_kg"
        ].iloc[0]
        buffalo_me = result[result["animal_product"] == "meat-buffalo"][
            "ME_MJ_per_kg"
        ].iloc[0]
        # adjustment = source_factor / proxy_factor = 0.7 / 0.5 = 1.4
        assert buffalo_me == pytest.approx(cattle_me * 0.7 / 0.5)

    def test_missing_ne_defaults_to_zero(self):
        """Missing NE component (e.g. NE_g for beef) defaults to zero."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-cattle"],
                "region": ["Europe"],
                "unit": ["NE_m"],
                "value": [10.0],
            }
        )
        result = calculate_ruminant_me_requirements(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            carcass_to_retail={"meat-cattle": 0.7},
            feed_proxy_map={},
        )
        # Only NE_m present, NE_g defaults to 0
        assert result["ME_MJ_per_kg"].iloc[0] == pytest.approx((10.0 / 0.6) / 0.7)


# ---------------------------------------------------------------------------
# Tests: get_monogastric_me_requirements
# ---------------------------------------------------------------------------


class TestGetMonogastricMeRequirements:
    """Tests for ME extraction for monogastric products."""

    def test_basic_me_extraction(self):
        """ME values are divided by carcass_to_retail."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-pig", "meat-chicken", "eggs"],
                "region": ["Europe", "Europe", "Europe"],
                "unit": ["ME", "ME", "ME"],
                "value": [30.0, 20.0, 15.0],
            }
        )
        carcass_to_retail = {
            "meat-pig": 0.7,
            "meat-chicken": 0.8,
            "eggs": 1.0,
        }
        result = get_monogastric_me_requirements(wirsenius_data, carcass_to_retail)
        pig_row = result[result["animal_product"] == "meat-pig"]
        chicken_row = result[result["animal_product"] == "meat-chicken"]
        egg_row = result[result["animal_product"] == "eggs"]
        assert pig_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(30.0 / 0.7)
        assert chicken_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(20.0 / 0.8)
        assert egg_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(15.0 / 1.0)

    def test_filters_to_monogastric_only(self):
        """Ruminant products are excluded."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-pig", "meat-cattle", "dairy"],
                "region": ["Europe", "Europe", "Europe"],
                "unit": ["ME", "NE_m", "NE_l"],
                "value": [30.0, 10.0, 5.0],
            }
        )
        carcass_to_retail = {"meat-pig": 0.7, "meat-cattle": 0.7, "dairy": 1.0}
        result = get_monogastric_me_requirements(wirsenius_data, carcass_to_retail)
        products = result["animal_product"].unique().tolist()
        assert "meat-pig" in products
        assert "meat-cattle" not in products
        assert "dairy" not in products

    def test_only_me_unit_rows_used(self):
        """Rows with non-ME units are excluded."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-pig", "meat-pig"],
                "region": ["Europe", "Europe"],
                "unit": ["ME", "NE_m"],
                "value": [30.0, 10.0],
            }
        )
        result = get_monogastric_me_requirements(
            wirsenius_data, carcass_to_retail={"meat-pig": 0.7}
        )
        assert len(result) == 1
        assert result["ME_MJ_per_kg"].iloc[0] == pytest.approx(30.0 / 0.7)

    def test_multiple_regions(self):
        """Each region produces a separate row."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["meat-pig", "meat-pig"],
                "region": ["Europe", "East Asia"],
                "unit": ["ME", "ME"],
                "value": [30.0, 35.0],
            }
        )
        result = get_monogastric_me_requirements(
            wirsenius_data, carcass_to_retail={"meat-pig": 0.7}
        )
        assert len(result) == 2
        eu_row = result[result["region"] == "Europe"]
        asia_row = result[result["region"] == "East Asia"]
        assert eu_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(30.0 / 0.7)
        assert asia_row["ME_MJ_per_kg"].iloc[0] == pytest.approx(35.0 / 0.7)

    def test_eggs_no_carcass_conversion(self):
        """Eggs with carcass_to_retail=1.0 keep original ME value."""
        wirsenius_data = pd.DataFrame(
            {
                "animal_product": ["eggs"],
                "region": ["Europe"],
                "unit": ["ME"],
                "value": [15.0],
            }
        )
        result = get_monogastric_me_requirements(
            wirsenius_data, carcass_to_retail={"eggs": 1.0}
        )
        assert result["ME_MJ_per_kg"].iloc[0] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Tests: calculate_feed_efficiencies
# ---------------------------------------------------------------------------


class TestCalculateFeedEfficiencies:
    """Tests for feed conversion efficiency calculation."""

    def test_basic_efficiency(self):
        """efficiency = ME_feed / ME_requirement."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [30.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        assert len(result) == 1
        assert result["efficiency"].iloc[0] == pytest.approx(12.0 / 30.0)

    def test_feed_category_naming(self):
        """Feed category is prefixed with animal_type."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-pig"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [40.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "monogastric"
        )
        assert result["feed_category"].iloc[0] == "monogastric_grain"

    def test_all_product_category_combinations(self):
        """All combinations of products and feed categories are generated."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "dairy"],
                "region": ["Europe", "Europe"],
                "ME_MJ_per_kg": [40.0, 20.0],
            }
        )
        feed_categories = pd.DataFrame(
            {
                "category": ["grain", "grass", "residue"],
                "ME_MJ_per_kg_DM": [12.0, 8.0, 6.0],
            }
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        # 2 products x 3 categories = 6 rows
        assert len(result) == 6
        combos = set(zip(result["product"], result["feed_category"]))
        assert ("meat-cattle", "ruminant_grain") in combos
        assert ("meat-cattle", "ruminant_grass") in combos
        assert ("meat-cattle", "ruminant_residue") in combos
        assert ("dairy", "ruminant_grain") in combos
        assert ("dairy", "ruminant_grass") in combos
        assert ("dairy", "ruminant_residue") in combos

    def test_higher_feed_energy_gives_higher_efficiency(self):
        """A feed with more ME per kg DM yields higher efficiency."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [40.0],
            }
        )
        feed_categories = pd.DataFrame(
            {
                "category": ["grain", "grass"],
                "ME_MJ_per_kg_DM": [12.0, 8.0],
            }
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        grain_eff = result[result["feed_category"] == "ruminant_grain"][
            "efficiency"
        ].iloc[0]
        grass_eff = result[result["feed_category"] == "ruminant_grass"][
            "efficiency"
        ].iloc[0]
        assert grain_eff > grass_eff

    def test_higher_me_requirement_gives_lower_efficiency(self):
        """A product with higher ME requirement yields lower efficiency."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "dairy"],
                "region": ["Europe", "Europe"],
                "ME_MJ_per_kg": [40.0, 20.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        cattle_eff = result[result["product"] == "meat-cattle"]["efficiency"].iloc[0]
        dairy_eff = result[result["product"] == "dairy"]["efficiency"].iloc[0]
        assert dairy_eff > cattle_eff

    def test_output_columns(self):
        """Result DataFrame has expected columns."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [30.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        assert set(result.columns) == {
            "product",
            "feed_category",
            "region",
            "efficiency",
        }

    def test_region_preserved(self):
        """Region from me_requirements is preserved in output."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "meat-cattle"],
                "region": ["Europe", "Sub-Saharan Africa"],
                "ME_MJ_per_kg": [30.0, 50.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        regions = result["region"].unique().tolist()
        assert "Europe" in regions
        assert "Sub-Saharan Africa" in regions


# ---------------------------------------------------------------------------
# Sanity checks: efficiency ranges
# ---------------------------------------------------------------------------


class TestEfficiencyRanges:
    """Sanity checks that efficiencies are in biologically plausible ranges."""

    def test_meat_efficiency_range(self):
        """Meat efficiencies should be in 0.01-0.5 range."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [50.0],
            }
        )
        feed_categories = pd.DataFrame(
            {
                "category": ["grain", "grass"],
                "ME_MJ_per_kg_DM": [12.0, 8.0],
            }
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        for _, row in result.iterrows():
            assert 0.01 <= row["efficiency"] <= 0.5, (
                f"Meat efficiency {row['efficiency']:.3f} outside plausible range "
                f"for {row['feed_category']}"
            )

    def test_dairy_efficiency_higher_than_meat(self):
        """Dairy should generally be more efficient than beef per unit feed."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "dairy"],
                "region": ["Europe", "Europe"],
                "ME_MJ_per_kg": [50.0, 5.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        beef_eff = result[result["product"] == "meat-cattle"]["efficiency"].iloc[0]
        dairy_eff = result[result["product"] == "dairy"]["efficiency"].iloc[0]
        assert dairy_eff > beef_eff

    def test_egg_efficiency_range(self):
        """Egg efficiencies should be in a reasonable range (higher than meat)."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["eggs"],
                "region": ["Europe"],
                "ME_MJ_per_kg": [15.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "monogastric"
        )
        eff = result["efficiency"].iloc[0]
        # 12/15 = 0.8 -- eggs are quite efficient per unit
        assert 0.1 <= eff <= 1.5, f"Egg efficiency {eff:.3f} outside plausible range"
