# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for food conversion link construction (mass_basis handling)."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.food import add_food_conversion_links


@pytest.fixture
def empty_network():
    """Create a minimal PyPSA network with required buses."""
    n = pypsa.Network()
    n.buses.add("crop:wheat:USA")
    n.buses.add("crop:tea:USA")
    n.buses.add("food:flour-white:USA")
    n.buses.add("food:tea-dried:USA")
    return n


@pytest.fixture
def loss_waste():
    """Minimal loss/waste DataFrame with zero losses for simplicity."""
    return pd.DataFrame(
        {
            "country": ["USA", "USA"],
            "food_group": ["grain", "stimulants"],
            "loss_fraction": [0.0, 0.0],
            "waste_fraction": [0.0, 0.0],
        }
    )


class TestMassBasis:
    """Test that mass_basis=dry skips the crop_to_fresh_factor."""

    def test_fresh_applies_conversion_factor(self, empty_network, loss_waste):
        """A fresh-basis food should have efficiency = factor * crop_to_fresh_factor * FLW."""
        foods = pd.DataFrame(
            {
                "pathway": ["white_flour"],
                "crop": ["wheat"],
                "food": ["flour-white"],
                "factor": [0.75],
                "mass_basis": ["fresh"],
            }
        )
        crop_to_fresh = {"wheat": 1.2}
        food_to_group = {"flour-white": "grain"}

        add_food_conversion_links(
            empty_network,
            food_list=["flour-white"],
            foods=foods,
            countries=["USA"],
            crop_to_fresh_factor=crop_to_fresh,
            food_to_group=food_to_group,
            loss_waste=loss_waste,
            crop_list=["wheat"],
            byproduct_list=[],
        )

        link = empty_network.links.static.loc["pathway:white_flour:USA"]
        # efficiency = factor * conversion_factor * FLW_multiplier
        # = 0.75 * 1.2 * 1.0 = 0.9
        assert link["efficiency"] == pytest.approx(0.9)

    def test_dry_skips_conversion_factor(self, empty_network, loss_waste):
        """A dry-basis food should have efficiency = factor * FLW (no fresh conversion)."""
        foods = pd.DataFrame(
            {
                "pathway": ["tea_dried_leaves"],
                "crop": ["tea"],
                "food": ["tea-dried"],
                "factor": [1.0],
                "mass_basis": ["dry"],
            }
        )
        # Tea has a large crop_to_fresh_factor (~4.0) that should NOT be applied
        crop_to_fresh = {"tea": 4.0}
        food_to_group = {"tea-dried": "stimulants"}

        add_food_conversion_links(
            empty_network,
            food_list=["tea-dried"],
            foods=foods,
            countries=["USA"],
            crop_to_fresh_factor=crop_to_fresh,
            food_to_group=food_to_group,
            loss_waste=loss_waste,
            crop_list=["tea"],
            byproduct_list=[],
        )

        link = empty_network.links.static.loc["pathway:tea_dried_leaves:USA"]
        # efficiency = factor * 1.0 * FLW_multiplier = 1.0 * 1.0 * 1.0 = 1.0
        # NOT 1.0 * 4.0 = 4.0
        assert link["efficiency"] == pytest.approx(1.0)

    def test_mixed_pathway_outputs(self, empty_network, loss_waste):
        """Both fresh and dry outputs in separate pathways are handled correctly."""
        foods = pd.DataFrame(
            {
                "pathway": ["white_flour", "tea_dried_leaves"],
                "crop": ["wheat", "tea"],
                "food": ["flour-white", "tea-dried"],
                "factor": [0.75, 1.0],
                "mass_basis": ["fresh", "dry"],
            }
        )
        crop_to_fresh = {"wheat": 1.2, "tea": 4.0}
        food_to_group = {"flour-white": "grain", "tea-dried": "stimulants"}

        add_food_conversion_links(
            empty_network,
            food_list=["flour-white", "tea-dried"],
            foods=foods,
            countries=["USA"],
            crop_to_fresh_factor=crop_to_fresh,
            food_to_group=food_to_group,
            loss_waste=loss_waste,
            crop_list=["wheat", "tea"],
            byproduct_list=[],
        )

        links = empty_network.links.static
        assert links.loc["pathway:white_flour:USA", "efficiency"] == pytest.approx(0.9)
        assert links.loc["pathway:tea_dried_leaves:USA", "efficiency"] == pytest.approx(
            1.0
        )
