# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for consumer values configuration."""

from pathlib import Path

import yaml


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


def validate_consumer_values(config: dict, project_root: Path) -> None:
    """Ensure consumer values runs have a baseline scenario defined."""
    scenario_defs_path = config.get("scenario_defs")
    if not scenario_defs_path:
        if _consumer_values_enabled(config, {}):
            raise ValueError(
                "consumer values incentives enabled but scenario_defs is not configured; "
                "a baseline scenario is required"
            )
        return

    scenario_defs_file = project_root / scenario_defs_path
    if not scenario_defs_file.exists():
        raise FileNotFoundError(
            f"scenario_defs not found at '{scenario_defs_file.as_posix()}'"
        )

    with open(scenario_defs_file, encoding="utf-8") as f:
        scenario_defs = yaml.safe_load(f) or {}

    if not _consumer_values_enabled(config, scenario_defs):
        return

    if "baseline" not in scenario_defs:
        raise ValueError(
            "consumer values incentives enabled but scenario_defs does not define a 'baseline' scenario"
        )
