# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for production stability helper behavior."""

import copy

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import yaml

from workflow.scripts.solve_model.production_stability import (
    _animal_feed_and_baselines,
    _compute_stability_deviation,
    _production_and_baselines,
    add_animal_growth_cap_constraints,
    add_crop_growth_cap_constraints,
    add_reforestation_cap_constraints,
    resolve_calibrated_l1_costs,
)


def _make_links(baselines, carrier="crop_production", index=None, efficiency=None):
    """Build a minimal production links DataFrame for testing."""
    n = len(baselines)
    if index is None:
        index = [chr(ord("a") + i) for i in range(n)]
    if efficiency is None:
        efficiency = [1.0] * n
    return pd.DataFrame(
        {
            "carrier": [carrier] * n,
            "baseline_area_mha": baselines,
            "efficiency": efficiency,
        },
        index=index,
    )


class _DummyModel:
    def __init__(self, link_names):
        self.variables = {
            "Link-p": xr.DataArray(
                np.zeros((1, len(link_names))),
                coords={"snapshot": ["now"], "name": link_names},
                dims=("snapshot", "name"),
            )
        }

    def add_constraints(self, *args, **kwargs):
        return None


class _DummyGlobalConstraints:
    def __init__(self):
        self.rows = []

    def add(self, names, sense, constant, type):
        for name, value in zip(names, np.asarray(constant, dtype=float)):
            self.rows.append(
                {
                    "name": name,
                    "sense": sense,
                    "constant": value,
                    "type": type,
                }
            )


class _DummyNetwork:
    def __init__(self, links_df):
        self.links = type("Links", (), {"static": links_df})()
        self.model = _DummyModel(links_df.index)
        self.global_constraints = _DummyGlobalConstraints()


def test_crop_baseline_filter_excludes_zero_in_hard_mode():
    """Hard-mode helper should keep only links above the baseline floor."""
    links_df = _make_links([0.0, 0.02, 1.5])
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0], coords={"name": ["a", "b", "c"]}, dims="name"
    )

    result = _production_and_baselines(
        link_p,
        links_df,
        "crop_production",
        min_baseline=0.1,
        include_all_links=False,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["c"]
    np.testing.assert_allclose(baselines.values, [1.5])


def test_crop_helper_includes_zero_baselines_by_default():
    """Default helper behavior should include zero baselines for hard caps."""
    links_df = _make_links([0.0, 0.02, 1.5])
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0], coords={"name": ["a", "b", "c"]}, dims="name"
    )

    result = _production_and_baselines(
        link_p,
        links_df,
        "crop_production",
        min_baseline=0.1,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["a", "b", "c"]
    np.testing.assert_allclose(baselines.values, [0.0, 0.02, 1.5])


def test_crop_penalty_mode_includes_zero_baselines():
    """Penalty-mode helper should include all crop links, including baseline=0."""
    links_df = _make_links([0.0, 2.0], index=["zero", "positive"])
    link_p = xr.DataArray(
        [0.3, 1.1], coords={"name": ["zero", "positive"]}, dims="name"
    )

    result = _production_and_baselines(
        link_p,
        links_df,
        "crop_production",
        min_baseline=0.1,
        include_all_links=True,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["zero", "positive"]
    np.testing.assert_allclose(baselines.values, [0.0, 2.0])


def test_relative_deviation_uses_floor_for_zero_baselines():
    """Relative deviation should stay finite when baseline is zero."""
    actual = xr.DataArray([0.5, 3.0], coords={"name": ["x", "y"]}, dims="name")
    baselines = xr.DataArray([0.0, 2.0], coords={"name": ["x", "y"]}, dims="name")

    deviation = _compute_stability_deviation(
        actual=actual,
        baselines=baselines,
        deviation_type="relative",
        min_baseline=0.1,
    )

    assert np.all(np.isfinite(deviation.values))
    # x: (0.5 - 0.0) / 0.1 = 5.0;  y: (3.0 - 2.0) / 2.0 = 0.5
    np.testing.assert_allclose(deviation.values, [5.0, 0.5])


def test_grassland_baseline_filter_excludes_zero_in_hard_mode():
    """Hard-mode helper should keep only grassland links above the baseline floor."""
    links_df = _make_links(
        [0.0, 0.05, 3.0], carrier="grassland_production", index=["g1", "g2", "g3"]
    )
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0], coords={"name": ["g1", "g2", "g3"]}, dims="name"
    )

    result = _production_and_baselines(
        link_p,
        links_df,
        "grassland_production",
        min_baseline=0.1,
        include_all_links=False,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["g3"]
    np.testing.assert_allclose(baselines.values, [3.0])


def test_animal_helper_includes_zero_baselines_by_default():
    """Default animal helper behavior should include zero-baseline links."""
    links_df = pd.DataFrame(
        {
            "carrier": ["animal_production"] * 3,
            "baseline_feed_use_mt_dm": [0.0, 0.000001, 2.0],
        },
        index=["zero", "tiny", "positive"],
    )
    link_p = xr.DataArray(
        [0.0, 0.0, 0.0],
        coords={"name": ["zero", "tiny", "positive"]},
        dims="name",
    )

    result = _animal_feed_and_baselines(
        link_p,
        links_df,
        min_baseline=0.1,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["zero", "tiny", "positive"]
    np.testing.assert_allclose(baselines.values, [0.0, 0.000001, 2.0])


def test_grassland_penalty_mode_includes_zero_baselines():
    """Penalty-mode helper should include all grassland links, including baseline=0."""
    links_df = _make_links(
        [0.0, 5.0], carrier="grassland_production", index=["gz", "gp"]
    )
    link_p = xr.DataArray([1.0, 4.0], coords={"name": ["gz", "gp"]}, dims="name")

    result = _production_and_baselines(
        link_p,
        links_df,
        "grassland_production",
        min_baseline=0.1,
        include_all_links=True,
    )

    assert result is not None
    link_names, _, baselines = result
    assert list(link_names) == ["gz", "gp"]
    np.testing.assert_allclose(baselines.values, [0.0, 5.0])


def test_production_and_baselines_returns_area_not_production():
    """Verify _production_and_baselines returns link_p (area) not link_p * efficiency."""
    links_df = _make_links(
        [1.0, 2.0],
        index=["x", "y"],
        efficiency=[3.0, 5.0],
    )
    link_p = xr.DataArray([0.5, 0.8], coords={"name": ["x", "y"]}, dims="name")

    result = _production_and_baselines(
        link_p,
        links_df,
        "crop_production",
        min_baseline=0.001,
        include_all_links=True,
    )

    assert result is not None
    _, area, baselines = result
    # area should be link_p values directly (Mha), NOT multiplied by efficiency
    np.testing.assert_allclose(area.values, [0.5, 0.8])
    # baselines should be baseline_area_mha, not baseline * efficiency
    np.testing.assert_allclose(baselines.values, [1.0, 2.0])


def test_animal_growth_cap_constrains_zero_and_tiny_baselines():
    """Animal growth cap should add hard caps for every animal link."""
    links_df = pd.DataFrame(
        {
            "carrier": ["animal_production"] * 3,
            "baseline_feed_use_mt_dm": [0.0, 0.000001, 2.0],
        },
        index=["zero", "tiny", "positive"],
    )
    n = _DummyNetwork(links_df)

    add_animal_growth_cap_constraints(
        n,
        {"enabled": True, "max_relative_increase": 0.1},
    )

    rows = pd.DataFrame(n.global_constraints.rows).set_index("name")
    assert list(rows.index) == [
        "animal_growth_cap_zero",
        "animal_growth_cap_tiny",
        "animal_growth_cap_positive",
    ]
    np.testing.assert_allclose(rows["constant"].values, [0.0, 0.0000011, 2.2])


def test_crop_growth_cap_constrains_zero_baseline_groups():
    """Crop growth cap should add hard caps for every crop-country group."""
    links_df = pd.DataFrame(
        {
            "carrier": ["crop_production"] * 4,
            "crop": ["wheat", "wheat", "rice", "wheat"],
            "country": ["USA", "USA", "USA", "CAN"],
            "baseline_area_mha": [0.0, 0.0, 0.5, 0.000001],
        },
        index=["wheat_usa_1", "wheat_usa_2", "rice_usa", "wheat_can"],
    )
    n = _DummyNetwork(links_df)

    add_crop_growth_cap_constraints(
        n,
        {"enabled": True, "max_relative_increase": 10.0},
    )

    rows = pd.DataFrame(n.global_constraints.rows).set_index("name")
    assert set(rows.index) == {
        "crop_growth_cap_wheat_USA",
        "crop_growth_cap_rice_USA",
        "crop_growth_cap_wheat_CAN",
    }
    assert rows.loc["crop_growth_cap_wheat_USA", "constant"] == 0.0
    assert rows.loc["crop_growth_cap_rice_USA", "constant"] == 5.5
    assert rows.loc["crop_growth_cap_wheat_CAN", "constant"] == 0.000011


def _make_spare_links_network():
    """Network with spare links in two countries plus a non-spare link."""
    links_df = pd.DataFrame(
        {
            "carrier": [
                "spare_land",
                "spare_land",
                "spare_existing_grassland",
                "crop_production",
            ],
            "country": ["USA", "CAN", "USA", "USA"],
            "p_nom_max": [10.0, 4.0, 6.0, 99.0],
        },
        index=["spare_usa_crop", "spare_can_crop", "spare_usa_grass", "produce_usa"],
    )
    return _DummyNetwork(links_df)


def test_reforestation_cap_groups_by_country():
    """Cap should bound per-country spared dispatch at fraction * p_nom_max sum."""
    n = _make_spare_links_network()

    add_reforestation_cap_constraints(n, max_fraction=0.5, buffer_mha=0.1)

    rows = pd.DataFrame(n.global_constraints.rows).set_index("name")
    assert set(rows.index) == {
        "reforestation_cap_CAN",
        "reforestation_cap_USA",
    }
    assert (rows["sense"] == "<=").all()
    # USA: 0.5 * (10 + 6) + 0.1; CAN: 0.5 * 4 + 0.1. The crop_production
    # link's p_nom_max must not enter the reforestable denominator.
    np.testing.assert_allclose(rows.loc["reforestation_cap_USA", "constant"], 8.1)
    np.testing.assert_allclose(rows.loc["reforestation_cap_CAN", "constant"], 2.1)


def test_reforestation_cap_noop_at_full_fraction():
    """fraction >= 1.0 must skip the constraint entirely."""
    n = _make_spare_links_network()

    add_reforestation_cap_constraints(n, max_fraction=1.0, buffer_mha=0.05)

    assert n.global_constraints.rows == []


def test_reforestation_cap_rejects_missing_country():
    """A spare link without a country tag must fail, not silently escape."""
    links_df = pd.DataFrame(
        {
            "carrier": ["spare_land", "spare_land"],
            "country": ["USA", ""],
            "p_nom_max": [1.0, 2.0],
        },
        index=["tagged", "untagged"],
    )
    n = _DummyNetwork(links_df)

    with pytest.raises(ValueError, match="no country tag"):
        add_reforestation_cap_constraints(n, max_fraction=0.5, buffer_mha=0.0)


def test_reforestation_cap_rejects_invalid_parameters():
    """Out-of-range fraction or negative buffer must raise."""
    n = _make_spare_links_network()

    with pytest.raises(ValueError, match="max_fraction"):
        add_reforestation_cap_constraints(n, max_fraction=1.5, buffer_mha=0.0)
    with pytest.raises(ValueError, match="max_fraction"):
        add_reforestation_cap_constraints(n, max_fraction=-0.1, buffer_mha=0.0)
    with pytest.raises(ValueError, match="buffer_mha"):
        add_reforestation_cap_constraints(n, max_fraction=0.5, buffer_mha=-1.0)


class TestResolveCalibratedL1Costs:
    @pytest.fixture(scope="class")
    def dp_cfg(self):
        with open("config/default.yaml") as f:
            return yaml.safe_load(f)["deviation_penalty"]

    ARTEFACT = "data/curated/calibration/deviation_penalty.yaml"

    def test_resolves_shipped_artefact(self, dp_cfg):
        """Default config sentinels must resolve against the shipped artefact.

        This is the drift guard for the component->config-block map: a
        mismatch between the calibration components and the resolver's
        paths (the e18c168 class of bug) fails here instead of at solve
        time.
        """
        with open(self.ARTEFACT) as f:
            artefact = yaml.safe_load(f)

        resolved = resolve_calibrated_l1_costs(dp_cfg, self.ARTEFACT)

        # Input must not be mutated
        assert dp_cfg["land"]["crops"]["l1_cost"] == "calibrated"
        assert resolved["land"]["crops"]["l1_cost"] == pytest.approx(
            artefact["l1_costs"]["cropland"]
        )
        assert resolved["land"]["grassland"]["l1_cost"] == pytest.approx(
            artefact["l1_costs"]["grassland"]
        )
        assert resolved["feed"]["l1_cost"] == pytest.approx(
            artefact["l1_costs"]["feed"]
        )

    def test_applies_component_factor(self, dp_cfg):
        cfg = copy.deepcopy(dp_cfg)
        cfg["land"]["crops"]["l1_cost_factor"] = 2.0

        resolved = resolve_calibrated_l1_costs(cfg, self.ARTEFACT)

        with open(self.ARTEFACT) as f:
            artefact = yaml.safe_load(f)
        assert resolved["land"]["crops"]["l1_cost"] == pytest.approx(
            2.0 * artefact["l1_costs"]["cropland"]
        )
        # Other components keep factor 1.0 and stay at the central value
        assert resolved["feed"]["l1_cost"] == pytest.approx(
            artefact["l1_costs"]["feed"]
        )

    def test_errors_when_component_missing_from_artefact(self, dp_cfg, tmp_path):
        artefact = tmp_path / "partial.yaml"
        artefact.write_text("components: [cropland]\nl1_costs:\n  cropland: 1.0\n")
        with pytest.raises(ValueError, match="did not calibrate 'grassland'"):
            resolve_calibrated_l1_costs(dp_cfg, str(artefact))
