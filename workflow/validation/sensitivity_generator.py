# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for sensitivity scenario generator assumptions."""


def validate_sensitivity_generator(config: dict, _project_root=None) -> None:
    """Ensure sensitivity generators have unique prefixes."""
    scenario_defs = config.get("scenarios") or {}

    sensitivity_generators = [
        generator
        for generator in scenario_defs.get("_generators", [])
        if generator.get("mode") == "sensitivity"
    ]

    # Check that all sensitivity generators have unique name prefixes.
    prefixes = [gen["name"].split("_{")[0] for gen in sensitivity_generators]
    if len(prefixes) != len(set(prefixes)):
        raise ValueError(
            f"Sensitivity generators must have unique name prefixes, "
            f"got duplicates in: {prefixes}"
        )
