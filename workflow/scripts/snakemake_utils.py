# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Utilities for Snakemake workflow execution."""

from pathlib import Path
import sys

# Add workflow directory to path for imports
_workflow_dir = Path(__file__).parent.parent
if str(_workflow_dir) not in sys.path:
    sys.path.insert(0, str(_workflow_dir))

from scenario_generators import expand_scenario_defs  # noqa: E402


def _recursive_update(target: dict, source: dict) -> dict:
    """Recursively update the target dictionary with the source dictionary."""
    for key, value in source.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            _recursive_update(target[key], value)
        else:
            target[key] = value
    return target


def load_scenarios(config: dict) -> dict:
    """Load scenario definitions from the config's `scenarios` key."""
    raw_defs = config.get("scenarios") or {}
    return expand_scenario_defs(raw_defs)


def apply_scenario_config(config: dict, scenario_name: str) -> None:
    """Apply scenario config overrides in-place.

    Parameters
    ----------
    config : dict
        The Snakemake config dictionary (will be modified in-place)
    scenario_name : str
        The scenario name (e.g., "HG", "HighGHG")
    """
    if not scenario_name:
        return

    scenarios = load_scenarios(config)

    if scenario_name not in scenarios:
        raise ValueError(
            f"Scenario '{scenario_name}' not found in scenario definitions."
        )

    overrides = scenarios[scenario_name]
    _recursive_update(config, overrides)
