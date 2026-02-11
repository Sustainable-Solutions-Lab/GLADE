# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for sensitivity scenario generator assumptions."""


def validate_sensitivity_generator(config: dict, _project_root=None) -> None:
    """Ensure scenarios contains at most one sensitivity generator.

    The current PCE sensitivity analysis implementation assumes one sensitivity
    generator per config file.
    """
    scenario_defs = config.get("scenarios") or {}

    sensitivity_generators = [
        generator
        for generator in scenario_defs.get("_generators", [])
        if generator.get("mode") == "sensitivity"
    ]
    if len(sensitivity_generators) > 1:
        raise ValueError(
            "scenarios has multiple sensitivity generators. "
            "Only one sensitivity generator per config is currently supported."
        )
