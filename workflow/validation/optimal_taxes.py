# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for optimal taxes configuration."""


def _optimal_taxes_enabled(config: dict, scenario_defs: dict) -> bool:
    if bool(config["optimal_taxes"]["enabled"]):
        return True

    for overrides in scenario_defs.values():
        if not isinstance(overrides, dict):
            continue
        ot_cfg = overrides.get("optimal_taxes", {})
        if isinstance(ot_cfg, dict) and bool(ot_cfg.get("enabled", False)):
            return True

    return False


def validate_optimal_taxes(config: dict, _project_root=None) -> None:
    """Ensure optimal taxes runs have required scenarios defined."""
    scenario_defs = config.get("scenarios") or {}

    if not _optimal_taxes_enabled(config, scenario_defs):
        return

    required_scenarios = {"baseline", "optimize", "extract_taxes", "apply_taxes"}
    missing = required_scenarios - set(scenario_defs.keys())
    if missing:
        raise ValueError(
            "optimal_taxes enabled but scenarios is missing required scenarios: "
            f"{missing}"
        )
