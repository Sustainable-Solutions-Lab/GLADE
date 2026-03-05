# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for feed-to-animal-product conversion efficiencies."""

import pandas as pd
import pytest

from workflow.scripts.build_feed_to_animal_products import (
    calculate_feed_efficiencies,
)

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
                "country": ["Europe"],
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
                "country": ["Europe"],
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
                "country": ["Europe", "Europe"],
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
                "country": ["Europe"],
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
                "country": ["Europe", "Europe"],
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
                "country": ["Europe"],
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
            "country",
            "efficiency",
        }

    def test_country_preserved(self):
        """Country from me_requirements is preserved in output."""
        me_requirements = pd.DataFrame(
            {
                "animal_product": ["meat-cattle", "meat-cattle"],
                "country": ["USA", "BRA"],
                "ME_MJ_per_kg": [30.0, 50.0],
            }
        )
        feed_categories = pd.DataFrame(
            {"category": ["grain"], "ME_MJ_per_kg_DM": [12.0]}
        )
        result = calculate_feed_efficiencies(
            me_requirements, feed_categories, "ruminant"
        )
        countries = result["country"].unique().tolist()
        assert "USA" in countries
        assert "BRA" in countries


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
                "country": ["Europe"],
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
                "country": ["Europe", "Europe"],
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
                "country": ["Europe"],
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
