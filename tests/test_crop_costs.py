# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for crop-cost handling in model building."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.crops import add_regional_crop_production_links


def test_silage_maize_cost_not_zero_with_zero_harvested_area():
    """Per-(crop, country) cost lookup must produce non-zero marginal cost."""
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

    # Cost = 1000 USD/ha → 1000 * 1e6 * 1e-9 = 1.0 bnUSD/Mha
    crop_costs = pd.Series(
        {("silage-maize", "USA"): 1000.0},
    )
    global_median_cost = pd.Series({"silage-maize": 1000.0})

    add_regional_crop_production_links(
        n=n,
        crop_list=["silage-maize"],
        yields_data={"silage-maize_yield_r": yields},
        region_to_country=pd.Series({"regionA": "USA"}),
        allowed_countries={"USA"},
        crop_costs=crop_costs,
        global_median_cost=global_median_cost,
        fertilizer_n_rates={"silage-maize": 0.0},
        rice_methane_factor=0.0,
        rainfed_wetland_rice_ch4_scaling_factor=1.0,
        use_actual_production=False,
        min_yield_t_per_ha=0.01,
        seed_kg_dm_per_ha=pd.Series({"silage-maize": 0.0}),
        crop_loss_multiplier=pd.Series(dtype=float),
        crop_marketing_cost_usd_per_t={"silage-maize": 0.0},
    )

    links = n.links.static[n.links.static["crop"] == "silage-maize"]
    assert len(links) == 1
    assert float(links["marginal_cost"].iloc[0]) == pytest.approx(1.0)
