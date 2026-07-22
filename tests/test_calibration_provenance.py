# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for calibration artefact provenance tracking."""

import copy
import shutil

import pytest
import yaml

from workflow.scripts.solve_namespace import (
    load_merged_config,
    resolve_calibration_source_paths,
)
from workflow.validation.calibration_provenance import (
    diff_snapshots,
    is_generation_run,
    structural_snapshot,
    validate_calibration_provenance,
)

# Minimal config carrying every key the provenance machinery touches.
MINIMAL_CONFIG = {
    "name": "unit",
    "calibration": {"source": "unit", "accept_provenance_mismatch": False},
    "diet": {
        "source": "fbs",
        "fbs": {"whole_grain_shares": {"flour-wholemeal": 0.11}},
        "gdd_ia": {"cooked_to_raw": {"red_meat": 1.43}},
    },
    "crops": ["wheat", "maize"],
    "multiple_cropping": {},
    "planning_horizon": 2030,
    "baseline_year": 2020,
    "emissions": {"ghg_price": 100},
    "validation": {"use_actual_yields": True},
    "grazing": {
        "grassland_forage_calibration": {"generate": False, "scenario": "default"}
    },
    "exogenous_feed_calibration": {"generate": False},
    "food_loss_waste_calibration": {"generate": False, "food_groups": ["fruits"]},
    "food_demand_calibration": {"generate": False, "min_multiplier": 0.5},
    "cost_calibration": {"generate": False},
    "deviation_penalty": {"calibration": {"generate": False}},
}


class TestStructuralSnapshot:
    def test_keeps_structural_leaves(self):
        snap = structural_snapshot(MINIMAL_CONFIG)
        assert snap["crops"] == ["wheat", "maize"]
        assert snap["baseline_year"] == 2020
        assert snap["food_loss_waste_calibration.food_groups"] == ["fruits"]
        assert snap["food_demand_calibration.min_multiplier"] == 0.5
        assert snap["derived.multiple_cropping.mirca_source_year"] == 2020
        assert set(snap["derived.multiple_cropping.observed"]) == {"wheat_maize"}
        assert (
            snap["derived.multiple_cropping.effective"]
            == snap["derived.multiple_cropping.observed"]
        )

    def test_drops_solve_time_and_exempt_keys(self):
        snap = structural_snapshot(MINIMAL_CONFIG)
        for key in snap:
            assert not key.startswith(
                ("emissions.ghg_price", "deviation_penalty", "validation")
            )
        assert "name" not in snap
        assert "planning_horizon" not in snap
        assert "calibration.source" not in snap
        assert not any(
            k.startswith("grazing.grassland_forage_calibration") for k in snap
        )
        assert "cost_calibration.generate" not in snap
        assert "food_loss_waste_calibration.generate" not in snap

    def test_records_resolved_mirca_source_year(self):
        config = copy.deepcopy(MINIMAL_CONFIG)
        config["baseline_year"] = 2016

        snap = structural_snapshot(config)

        assert snap["derived.multiple_cropping.mirca_source_year"] == 2015


class TestDiffSnapshots:
    def test_identical(self):
        snap = structural_snapshot(MINIMAL_CONFIG)
        assert diff_snapshots(snap, dict(snap)) == []

    def test_changed_added_removed(self):
        stamped = {"a": 1, "b": [1, 2]}
        active = {"b": [1, 3], "c": "x"}
        diffs = diff_snapshots(stamped, active)
        assert len(diffs) == 3
        assert any(d.startswith("a:") and "not in config" in d for d in diffs)
        assert any(d.startswith("b:") and "!=" in d for d in diffs)
        assert any(d.startswith("c:") and "not in stamp" in d for d in diffs)


class TestGenerationRunDetection:
    def test_not_generation_run(self):
        assert not is_generation_run(MINIMAL_CONFIG)

    @pytest.mark.parametrize(
        "path",
        [
            ("grazing", "grassland_forage_calibration", "generate"),
            ("food_loss_waste_calibration", "generate"),
            ("deviation_penalty", "calibration", "generate"),
        ],
    )
    def test_generation_run(self, path):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        node = cfg
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = True
        assert is_generation_run(cfg)


class TestValidateCalibrationProvenance:
    def _write_stamp(self, root, config):
        catalog = root / "data/curated/mirca_os_multicropping_combinations.yaml"
        catalog.parent.mkdir(parents=True)
        shutil.copy("data/curated/mirca_os_multicropping_combinations.yaml", catalog)
        path = root / "data/curated/calibration/unit/provenance.yaml"
        path.parent.mkdir(parents=True)
        stamp = {
            "source": "unit",
            "structural_config": structural_snapshot(config, root),
        }
        with open(path, "w") as f:
            yaml.safe_dump(stamp, f)

    def test_match_passes(self, tmp_path):
        self._write_stamp(tmp_path, MINIMAL_CONFIG)
        validate_calibration_provenance(MINIMAL_CONFIG, tmp_path)

    def test_solve_time_change_passes(self, tmp_path):
        self._write_stamp(tmp_path, MINIMAL_CONFIG)
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["emissions"]["ghg_price"] = 500
        validate_calibration_provenance(cfg, tmp_path)

    def test_structural_change_raises(self, tmp_path):
        self._write_stamp(tmp_path, MINIMAL_CONFIG)
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["crops"] = ["wheat"]
        with pytest.raises(ValueError, match="crops"):
            validate_calibration_provenance(cfg, tmp_path)

    def test_catalog_change_raises(self, tmp_path):
        self._write_stamp(tmp_path, MINIMAL_CONFIG)
        catalog_path = (
            tmp_path / "data/curated/mirca_os_multicropping_combinations.yaml"
        )
        catalog = yaml.safe_load(catalog_path.read_text())
        catalog.pop("wheat_maize")
        catalog_path.write_text(yaml.safe_dump(catalog))

        with pytest.raises(ValueError, match=r"derived\.multiple_cropping\.catalog"):
            validate_calibration_provenance(MINIMAL_CONFIG, tmp_path)

    def test_accept_flag_downgrades_to_warning(self, tmp_path):
        self._write_stamp(tmp_path, MINIMAL_CONFIG)
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["crops"] = ["wheat"]
        cfg["calibration"]["accept_provenance_mismatch"] = True
        validate_calibration_provenance(cfg, tmp_path)

    def test_missing_stamp_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no provenance stamp"):
            validate_calibration_provenance(MINIMAL_CONFIG, tmp_path)

    def test_generation_run_skipped(self, tmp_path):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["cost_calibration"]["generate"] = True
        # No stamp written: generation runs must not require one.
        validate_calibration_provenance(cfg, tmp_path)


class TestResolveCalibrationSourcePaths:
    def test_substitutes_placeholder(self):
        cfg = {
            "calibration": {"source": "foo"},
            "cost_calibration": {
                "crop_correction_csv": (
                    "data/curated/calibration/{calibration_source}/crop_cost.csv"
                )
            },
            "nested": {"list": ["{calibration_source}/x", 3]},
            "untouched": "results/{name}/foo.csv",
        }
        resolved = resolve_calibration_source_paths(cfg)
        assert (
            resolved["cost_calibration"]["crop_correction_csv"]
            == "data/curated/calibration/foo/crop_cost.csv"
        )
        assert resolved["nested"]["list"] == ["foo/x", 3]
        assert resolved["untouched"] == "results/{name}/foo.csv"


class TestDefaultStampConsistency:
    def test_default_stamp_matches_default_config(self):
        """The committed default stamp must match config/default.yaml.

        This is the enforcement point for structural default-config
        changes: editing config/default.yaml structurally without
        recalibrating (or at least restamping via
        workflow/scripts/write_calibration_provenance.py) fails here and
        in every workflow invocation.
        """
        config = load_merged_config("config/default.yaml")
        with open("data/curated/calibration/default/provenance.yaml") as f:
            stamp = yaml.safe_load(f)
        diffs = diff_snapshots(stamp["structural_config"], structural_snapshot(config))
        assert diffs == [], "\n".join(diffs)
