# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Validation for health risk/cause configuration.
"""

from pathlib import Path


def validate_health_map(config: dict, project_root: Path) -> None:
    health = config.get("health", {})
    risks = set(health.get("risk_factors", []))
    causes = set(health.get("causes", []))
    risk_cause_map: dict[str, list[str]] = health.get("risk_cause_map", {})

    missing_risks = risks - set(risk_cause_map.keys())
    extra_risks = set(risk_cause_map.keys()) - risks
    if missing_risks or extra_risks:
        parts = []
        if missing_risks:
            parts.append(f"missing in risk_cause_map: {sorted(missing_risks)}")
        if extra_risks:
            parts.append(f"not listed in risk_factors: {sorted(extra_risks)}")
        raise ValueError("health.risk_cause_map keys mismatch: " + "; ".join(parts))

    map_causes = {c for cs in risk_cause_map.values() for c in cs}
    missing_causes = causes - map_causes
    extra_causes = map_causes - causes
    if missing_causes or extra_causes:
        parts = []
        if missing_causes:
            parts.append(f"causes missing from map: {sorted(missing_causes)}")
        if extra_causes:
            parts.append(
                f"causes in map but not in causes list: {sorted(extra_causes)}"
            )
        raise ValueError("health.risk_cause_map causes mismatch: " + "; ".join(parts))
