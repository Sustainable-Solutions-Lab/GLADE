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
        water_periods=1,
        irrigation_calendar=pd.DataFrame(
            columns=["region", "crop", "month", "share", "area_ha"]
        ),
        min_yield_t_per_ha=0.01,
        seed_kg_dm_per_ha=pd.Series({"silage-maize": 0.0}),
        crop_loss_multiplier=pd.Series(dtype=float),
        crop_marketing_cost_usd_per_t={"silage-maize": 0.0},
    )

    links = n.links.static[n.links.static["crop"] == "silage-maize"]
    assert len(links) == 1
    assert float(links["marginal_cost"].iloc[0]) == pytest.approx(1.0)


def _add_rice_wheat_multi_link(
    baseline_ha,
    multi_crop_cost_calibration,
    *,
    baseline_region="regionA",
    baseline_combination="rice_wheat",
    potential_region="regionA",
    cycle_crops=("wetland-rice", "wheat"),
):
    """Build a single rice-wheat multi-cropping link and return the network."""
    n = pypsa.Network()
    n.buses.add(
        [
            f"land:cropland:{potential_region}_c0_r",
            "crop:wetland-rice:USA",
            "crop:wheat:USA",
            "fertilizer:USA",
        ]
    )
    n.add(
        "Link",
        ["single_rice", "single_wheat"],
        bus0=[
            f"land:cropland:{potential_region}_c0_r",
            f"land:cropland:{potential_region}_c0_r",
        ],
        bus1=["crop:wetland-rice:USA", "crop:wheat:USA"],
        carrier="crop_production",
        baseline_area_mha=1.0,
        crop=["wetland-rice", "wheat"],
        country="USA",
        water_supply="rainfed",
        region=potential_region,
        resource_class=0,
    )
    eligible_area = pd.DataFrame(
        {
            "combination": ["rice_wheat"],
            "region": [potential_region],
            "resource_class": [0],
            "water_supply": ["r"],
            "eligible_area_ha": [1_000_000.0],
            "water_requirement_m3_per_ha_p0": [0.0],
        }
    )
    cycle_yields = pd.DataFrame(
        {
            "combination": ["rice_wheat"] * len(cycle_crops),
            "region": [potential_region] * len(cycle_crops),
            "resource_class": [0] * len(cycle_crops),
            "water_supply": ["r"] * len(cycle_crops),
            "cycle_index": list(range(1, len(cycle_crops) + 1)),
            "crop": list(cycle_crops),
            "yield_t_per_ha": [2.0] * len(cycle_crops),
        }
    )
    baseline_area = pd.DataFrame(
        {
            "combination": [baseline_combination],
            "region": [baseline_region],
            "resource_class": [0],
            "water_supply": ["r"],
            "baseline_area_ha": [baseline_ha],
        }
    )
    add_multi_cropping_links(
        n=n,
        eligible_area=eligible_area,
        cycle_yields=cycle_yields,
        region_to_country=pd.Series({baseline_region: "USA", potential_region: "USA"}),
        allowed_countries={"USA"},
        crop_costs=pd.Series(
            {
                ("wetland-rice", "USA"): 100.0,
                ("wheat", "USA"): 200.0,
            }
        ),
        global_median_cost=pd.Series({"wetland-rice": 100.0, "wheat": 200.0}),
        fertilizer_n_rates={"wetland-rice": 0.0, "wheat": 0.0},
        water_periods=1,
        rice_methane_factor=0.0,
        rainfed_wetland_rice_ch4_scaling_factor=1.0,
        min_yield_t_per_ha=0.01,
        seed_kg_dm_per_ha=pd.Series({"wetland-rice": 0.0, "wheat": 0.0}),
        crop_loss_multiplier=pd.Series(dtype=float),
        crop_marketing_cost_usd_per_t={"wetland-rice": 0.0, "wheat": 0.0},
        combinations={"rice_wheat": {"crops": ["wetland-rice", "wheat"]}},
        baseline_area=baseline_area,
        multi_crop_cost_calibration=multi_crop_cost_calibration,
    )
    return n


def test_multi_cropping_relocates_baseline_without_local_potential():
    """Observed anchors relocate only within their combination/country/water group."""
    n = _add_rice_wheat_multi_link(
        baseline_ha=250_000.0,
        multi_crop_cost_calibration=None,
        baseline_region="observed",
        potential_region="eligible",
    )
    multi = n.links.static[n.links.static["carrier"] == "crop_production_multi"]
    assert len(multi) == 1
    assert multi.iloc[0]["region"] == "eligible"
    assert multi.iloc[0]["baseline_area_mha"] == pytest.approx(0.25)


def test_multi_cropping_ignores_disabled_catalog_baseline():
    """A catalog baseline cannot reactivate a combination disabled by config."""
    n = _add_rice_wheat_multi_link(
        baseline_ha=250_000.0,
        baseline_combination="disabled_catalog_entry",
        multi_crop_cost_calibration=None,
    )
    multi = n.links.static[n.links.static["carrier"] == "crop_production_multi"]
    assert len(multi) == 1
    assert multi.iloc[0]["baseline_area_mha"] == 0.0


def test_multi_cropping_keeps_incomplete_bundle_on_single_baselines():
    """A missing cycle excludes the bundle without stripping single baselines."""
    n = _add_rice_wheat_multi_link(
        baseline_ha=250_000.0,
        multi_crop_cost_calibration=None,
        cycle_crops=("wetland-rice",),
    )
    multi = n.links.static[n.links.static["carrier"] == "crop_production_multi"]
    assert multi.empty
    singles = n.links.static[n.links.static["carrier"] == "crop_production"]
    assert singles["baseline_area_mha"].sum() == 2.0


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


def test_multi_cropping_rice_emits_methane_per_cycle():
    """Rice CH4 is invariant to single-crop vs multi-cropping representation.

    A bundle running ``m`` wetland-rice cycles floods its hectare ``m`` times, so
    it must carry ``m`` times the per-hectare emission factor. Without this the
    model could abate rice methane for free by shifting rice onto multi links.
    """
    n = pypsa.Network()
    n.buses.add(
        [
            "land:cropland:regionA_c0_i",
            "crop:wetland-rice:USA",
            "fertilizer:USA",
            "emission:ch4",
            "water_field:regionA:p0",
        ]
    )
    eligible_area = pd.DataFrame(
        {
            "combination": ["double_rice"],
            "region": ["regionA"],
            "resource_class": [0],
            "water_supply": ["i"],
            "eligible_area_ha": [1_000_000.0],
            "water_requirement_m3_per_ha_p0": [1000.0],
        }
    )
    cycle_yields = pd.DataFrame(
        {
            "combination": ["double_rice", "double_rice"],
            "region": ["regionA", "regionA"],
            "resource_class": [0, 0],
            "water_supply": ["i", "i"],
            "cycle_index": [1, 2],
            "crop": ["wetland-rice", "wetland-rice"],
            "yield_t_per_ha": [2.0, 2.0],
        }
    )
    add_multi_cropping_links(
        n=n,
        eligible_area=eligible_area,
        cycle_yields=cycle_yields,
        region_to_country=pd.Series({"regionA": "USA"}),
        allowed_countries={"USA"},
        crop_costs=pd.Series({("wetland-rice", "USA"): 100.0}),
        global_median_cost=pd.Series({"wetland-rice": 100.0}),
        fertilizer_n_rates={"wetland-rice": 0.0},
        water_periods=1,
        rice_methane_factor=110.0,
        rainfed_wetland_rice_ch4_scaling_factor=0.5,
        min_yield_t_per_ha=0.01,
        seed_kg_dm_per_ha=pd.Series({"wetland-rice": 0.0}),
        crop_loss_multiplier=pd.Series(dtype=float),
        crop_marketing_cost_usd_per_t={"wetland-rice": 0.0},
        combinations={"double_rice": {"crops": ["wetland-rice", "wetland-rice"]}},
    )

    links = n.links.static
    assert len(links) == 1
    link = links.iloc[0]
    bus_cols = [c for c in links.columns if c.startswith("bus") and c[3:].isdigit()]
    ch4_ports = [c for c in bus_cols if link[c] == "emission:ch4"]
    assert len(ch4_ports) == 1, "double-rice link must carry exactly one CH4 port"
    eff = float(link[f"efficiency{ch4_ports[0][3:]}"])
    # Two irrigated rice cycles at 110 kg CH4/ha each (no rainfed scaling).
    assert eff == pytest.approx(220.0)
