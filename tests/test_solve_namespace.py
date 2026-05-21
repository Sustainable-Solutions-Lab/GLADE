# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for solve_namespace helpers shared between Snakemake and the cluster
manifest exporter."""

import pytest

from workflow.scripts.solve_namespace import validate_scenario_overrides


class TestValidateScenarioOverrides:
    def test_accepts_solve_time_only_overrides(self):
        defs = {
            "low_ghg": {"emissions": {"ghg_price": 50.0}},
            "no_health": {"health": {"enabled": False}},
            "stability": {"deviation_penalty": {"diet": {"enabled": True}}},
        }
        validate_scenario_overrides(defs)

    def test_rejects_structural_topology_override(self):
        """Scenario overriding 'countries' must fail: same built network is
        reused across scenarios, so changing topology silently mismatches it."""
        defs = {"bad": {"countries": ["USA", "FRA"]}}
        with pytest.raises(ValueError, match="structural key 'countries'"):
            validate_scenario_overrides(defs)

    def test_rejects_structural_residue_override(self):
        defs = {"bad": {"residues": {"max_feed_fraction": 0.5}}}
        with pytest.raises(ValueError, match="structural key"):
            validate_scenario_overrides(defs)

    def test_collects_multiple_errors(self):
        defs = {
            "bad1": {"countries": ["USA"]},
            "bad2": {"residues": {"max_feed_fraction": 0.5}},
        }
        with pytest.raises(ValueError) as info:
            validate_scenario_overrides(defs)
        msg = str(info.value)
        assert "bad1" in msg and "bad2" in msg
