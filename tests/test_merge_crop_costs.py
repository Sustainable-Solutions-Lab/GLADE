# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for source-stage crop cost fallback construction."""

import pandas as pd
import pytest

from workflow.scripts.merge_crop_costs import merge_costs


def test_merge_costs_applies_feed_crop_fallbacks():
    """silage-maize and biomass-sorghum should inherit maize/sorghum costs."""
    base_year = 2024
    cost_per_year = f"cost_per_year_usd_{base_year}_per_ha"
    cost_per_planting = f"cost_per_planting_usd_{base_year}_per_ha"

    costs_df = pd.DataFrame(
        [
            {
                "crop": "maize",
                "source": "usda",
                cost_per_year: 600.0,
                cost_per_planting: 400.0,
            },
            {
                "crop": "sorghum",
                "source": "usda",
                cost_per_year: 500.0,
                cost_per_planting: 250.0,
            },
        ]
    )
    fallback_mapping = {
        "silage-maize": {"usda_crop": "maize"},
        "biomass-sorghum": {"usda_crop": "sorghum"},
    }

    merged = merge_costs(
        costs_df=costs_df,
        all_crops=["silage-maize", "biomass-sorghum"],
        fallback_mapping=fallback_mapping,
        base_year=base_year,
    ).set_index("crop")

    assert merged.loc["silage-maize", "n_sources"] == 0
    assert merged.loc["silage-maize", cost_per_year] == pytest.approx(600.0)
    assert merged.loc["silage-maize", cost_per_planting] == pytest.approx(400.0)

    assert merged.loc["biomass-sorghum", "n_sources"] == 0
    assert merged.loc["biomass-sorghum", cost_per_year] == pytest.approx(500.0)
    assert merged.loc["biomass-sorghum", cost_per_planting] == pytest.approx(250.0)
