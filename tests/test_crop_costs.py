# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for crop-cost handling in model building."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.crops import (
    add_multi_cropping_links,
    add_regional_crop_production_links,
)


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


def _add_rice_wheat_multi_link(baseline_ha, multi_crop_cost_calibration):
    """Build a single rice-wheat multi-cropping link and return the network."""
    n = pypsa.Network()
    n.buses.add(
        [
            "land:cropland:regionA_c0_r",
            "crop:wetland-rice:USA",
            "crop:wheat:USA",
            "fertilizer:USA",
        ]
    )
    eligible_area = pd.DataFrame(
        {
            "combination": ["rice_wheat"],
            "region": ["regionA"],
            "resource_class": [0],
            "water_supply": ["r"],
            "eligible_area_ha": [1_000_000.0],
            "water_requirement_m3_per_ha": [0.0],
        }
    )
    cycle_yields = pd.DataFrame(
        {
            "combination": ["rice_wheat", "rice_wheat"],
            "region": ["regionA", "regionA"],
            "resource_class": [0, 0],
            "water_supply": ["r", "r"],
            "cycle_index": [0, 1],
            "crop": ["wetland-rice", "wheat"],
            "yield_t_per_ha": [2.0, 3.0],
        }
    )
    baseline_area = pd.DataFrame(
        {
            "combination": ["rice_wheat"],
            "region": ["regionA"],
            "resource_class": [0],
            "water_supply": ["r"],
            "baseline_area_ha": [baseline_ha],
        }
    )
    add_multi_cropping_links(
        n=n,
        eligible_area=eligible_area,
        cycle_yields=cycle_yields,
        region_to_country=pd.Series({"regionA": "USA"}),
        allowed_countries={"USA"},
        crop_costs=pd.Series(
            {
                ("wetland-rice", "USA"): 100.0,
                ("wheat", "USA"): 200.0,
            }
        ),
        global_median_cost=pd.Series({"wetland-rice": 100.0, "wheat": 200.0}),
        fertilizer_n_rates={"wetland-rice": 0.0, "wheat": 0.0},
        min_yield_t_per_ha=0.01,
        seed_kg_dm_per_ha=pd.Series({"wetland-rice": 0.0, "wheat": 0.0}),
        crop_loss_multiplier=pd.Series(dtype=float),
        crop_marketing_cost_usd_per_t={"wetland-rice": 0.0, "wheat": 0.0},
        baseline_area=baseline_area,
        multi_crop_cost_calibration=multi_crop_cost_calibration,
    )
    return n


def test_multi_cropping_uses_direct_bundle_cost_calibration():
    """Multi-crop links use per-(combination, country) corrections."""
    n = _add_rice_wheat_multi_link(
        baseline_ha=500_000.0,
        multi_crop_cost_calibration=pd.Series({("rice_wheat", "USA"): 4.0}),
    )

    links = n.links.static[n.links.static["carrier"] == "crop_production_multi"]
    assert len(links) == 1
    link = links.iloc[0]
    assert float(link["marginal_cost"]) == pytest.approx(0.3)
    assert float(link["bounded_penalty_bnusd_per_mha"]) == pytest.approx(4.0)
    assert float(link["bounded_subsidy_bnusd_per_mha"]) == pytest.approx(0.0)


def test_multi_cropping_raises_on_stale_cost_calibration():
    """A positive-baseline bundle missing from the artefact is a stale-set error."""
    with pytest.raises(ValueError, match="stale"):
        _add_rice_wheat_multi_link(
            baseline_ha=500_000.0,
            multi_crop_cost_calibration=pd.Series({("maize_soybean", "USA"): 1.0}),
        )


def test_multi_cropping_zero_baseline_missing_correction_is_zero():
    """A zero-baseline bundle absent from the artefact gets a zero correction."""
    n = _add_rice_wheat_multi_link(
        baseline_ha=0.0,
        multi_crop_cost_calibration=pd.Series({("maize_soybean", "USA"): 1.0}),
    )

    links = n.links.static[n.links.static["carrier"] == "crop_production_multi"]
    assert len(links) == 1
    link = links.iloc[0]
    assert float(link["bounded_penalty_bnusd_per_mha"]) == pytest.approx(0.0)
    assert float(link["bounded_subsidy_bnusd_per_mha"]) == pytest.approx(0.0)
