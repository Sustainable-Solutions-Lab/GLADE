# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for crop-cost handling in model building."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.crops import add_regional_crop_production_links


def test_silage_maize_cost_not_zero_with_zero_harvested_area():
    """Per-tonne cost mixing must preserve explicit source-stage crop costs."""
    n = pypsa.Network()
    n.buses.add(
        [
            "land:cropland:regionA_c0_r",
            "crop:silage-maize:USA",
            "fertilizer:USA",
        ]
    )

    yields = pd.DataFrame(
        {
            "region": ["regionA"],
            "resource_class": [0],
            "yield": [2.0],
            "suitable_area": [1_000_000.0],
            "harvested_area": [0.0],
            "water_requirement_m3_per_ha": [0.0],
        }
    ).set_index(["region", "resource_class"])

    add_regional_crop_production_links(
        n=n,
        crop_list=["silage-maize"],
        yields_data={"silage-maize_yield_r": yields},
        region_to_country=pd.Series({"regionA": "USA"}),
        allowed_countries={"USA"},
        crop_costs_per_year=pd.Series({"silage-maize": 600.0}),
        crop_costs_per_planting=pd.Series({"silage-maize": 400.0}),
        fertilizer_n_rates={},
        rice_methane_factor=0.0,
        rainfed_wetland_rice_ch4_scaling_factor=1.0,
        use_actual_production=False,
        per_tonne_cost_fraction=0.9,
        min_yield_t_per_ha=0.01,
    )

    links = n.links.static[n.links.static["crop"] == "silage-maize"]
    assert len(links) == 1
    assert float(links["marginal_cost"].iloc[0]) == pytest.approx(1.0)
