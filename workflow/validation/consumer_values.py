# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for consumer values configuration."""


def _consumer_values_enabled(config: dict, scenario_defs: dict) -> bool:
    def has_consumer_values_sources(cfg: dict) -> bool:
        sources = [str(src) for src in cfg.get("sources", [])]
        return any("consumer_values" in src for src in sources)

    base_cfg = config["food_incentives"]
    if has_consumer_values_sources(base_cfg) and bool(base_cfg["enabled"]):
        return True

    for overrides in scenario_defs.values():
        if not isinstance(overrides, dict):
            continue
        cv_cfg = overrides.get("food_incentives", {})
        if not isinstance(cv_cfg, dict) or not bool(cv_cfg.get("enabled", False)):
            continue
        merged_sources = cv_cfg.get("sources", base_cfg.get("sources", []))
        if any("consumer_values" in str(src) for src in merged_sources):
            return True

    return False


def validate_consumer_values(config: dict, _project_root=None) -> None:
    """Ensure consumer values runs have a baseline scenario defined."""
    scenario_defs = config.get("scenarios") or {}

    if not _consumer_values_enabled(config, scenario_defs):
        return

    if "baseline" not in scenario_defs:
        raise ValueError(
            "consumer values incentives enabled but scenarios does not define a 'baseline' scenario"
        )
