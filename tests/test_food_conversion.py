# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for food_processing pathway efficiency.

The per-crop ``inverse_moisture`` vs ``identity`` policy lives in
``data/curated/crop_moisture_content.csv`` and is baked into the
``crop_to_fresh_factor`` dict produced by
``utils._fresh_mass_conversion_factors``. These tests therefore exercise
``add_food_conversion_links`` with two contrived ``crop_to_fresh_factor``
entries representing the two cases.
"""

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


class TestFoodConversionFactor:
    """Pathway efficiency = factor * crop_to_fresh_factor[crop] * FLW."""

    def test_inverse_moisture_factor_applied(self, empty_network, loss_waste):
        """A crop with inverse_moisture policy passes a >1 factor through."""
        foods = pd.DataFrame(
            {
                "pathway": ["white_flour"],
                "crop": ["wheat"],
                "food": ["flour-white"],
                "factor": [0.75],
            }
        )
        # wheat: edible 1.0 / (1 - 0.13) = 1.149... approximated as 1.2 here.
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

    def test_identity_factor_passes_through(self, empty_network, loss_waste):
        """Identity-policy crops pass crop_to_fresh_factor=edible_portion (=1.0)."""
        foods = pd.DataFrame(
            {
                "pathway": ["tea_dried_leaves"],
                "crop": ["tea"],
                "food": ["tea-dried"],
                "factor": [1.0],
            }
        )
        # Tea: identity policy -> crop_to_fresh_factor = edible_portion (=1.0).
        # If the factor were the inverse-moisture value (~4.0) tea-dried would
        # be 4x over-supplied, hence the explicit 1.0 here.
        crop_to_fresh = {"tea": 1.0}
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
        # efficiency = factor * conversion_factor * FLW_multiplier
        # = 1.0 * 1.0 * 1.0 = 1.0
        assert link["efficiency"] == pytest.approx(1.0)

    def test_mixed_crops_use_per_crop_factor(self, empty_network, loss_waste):
        """Crops with different policies each see their own crop_to_fresh value."""
        foods = pd.DataFrame(
            {
                "pathway": ["white_flour", "tea_dried_leaves"],
                "crop": ["wheat", "tea"],
                "food": ["flour-white", "tea-dried"],
                "factor": [0.75, 1.0],
            }
        )
        crop_to_fresh = {"wheat": 1.2, "tea": 1.0}
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
