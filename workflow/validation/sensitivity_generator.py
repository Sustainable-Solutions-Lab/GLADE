# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for sensitivity scenario generator assumptions."""

from pathlib import Path

import yaml


def validate_sensitivity_generator(config: dict, project_root: Path) -> None:
    """Ensure scenario_defs contains at most one sensitivity generator.

    The current PCE sensitivity analysis implementation assumes one sensitivity
    generator per config file.
    """
    scenario_defs_path = config.get("scenario_defs")
    if not scenario_defs_path:
        return

    scenario_defs_file = project_root / scenario_defs_path
    if not scenario_defs_file.exists():
        raise FileNotFoundError(
            f"scenario_defs not found at '{scenario_defs_file.as_posix()}'"
        )

    with open(scenario_defs_file, encoding="utf-8") as f:
        scenario_defs = yaml.safe_load(f) or {}

    sensitivity_generators = [
        generator
        for generator in scenario_defs.get("_generators", [])
        if generator.get("mode") == "sensitivity"
    ]
    if len(sensitivity_generators) > 1:
        raise ValueError(
            "scenario_defs has multiple sensitivity generators. "
            "Only one sensitivity generator per config is currently supported."
        )
