# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation checks for calibration section enabled/generate combinations.

Each calibration section follows the same two-flag pattern: ``enabled``
controls whether the calibration is applied at solve/build time, and
``generate`` controls whether the workflow produces the calibration file
from a source scenario. The canonical generation pattern is
``enabled: false, generate: true`` so that ``enabled`` is the single source
of truth at runtime; the alternative (``enabled: true, generate: true``)
is rejected here.
"""

from pathlib import Path

# Each entry describes one calibration section.
#
# - ``path``: tuple of keys to descend into ``config`` to reach the section.
# - ``files``: list of keys inside the section whose values are paths to
#   calibration artefacts that must exist when ``enabled=true,
#   generate=false``.
# - ``has_scenario``: whether the section uses a ``scenario`` field naming
#   the source scenario for generation.
_CALIBRATION_SECTIONS = [
    {
        "name": "grassland_forage_calibration",
        "path": ("grazing", "grassland_forage_calibration"),
        "files": [
            "grassland_yield_correction",
            "fodder_conversion_correction",
            "exogenous_forage",
        ],
        "has_scenario": True,
    },
    {
        "name": "feed_protein_calibration",
        "path": ("feed_protein_calibration",),
        "files": ["exogenous_protein"],
        "has_scenario": True,
    },
    {
        "name": "food_loss_waste_calibration",
        "path": ("food_loss_waste_calibration",),
        "files": ["calibration_file"],
        "has_scenario": True,
    },
    {
        "name": "food_demand_calibration",
        "path": ("food_demand_calibration",),
        "files": ["calibration_file"],
        "has_scenario": True,
    },
    {
        "name": "cost_calibration",
        "path": ("cost_calibration",),
        "files": [
            "crop_correction_csv",
            "grassland_correction_csv",
            "animal_correction_csv",
        ],
        "has_scenario": True,
    },
    {
        "name": "prod_stability_calibration",
        "path": ("prod_stability_calibration",),
        "files": ["calibrated_l1_yaml"],
        "has_scenario": False,
    },
]


def _resolve(config: dict, path: tuple) -> dict:
    node = config
    for key in path:
        node = node[key]
    return node


def validate_calibration(config: dict, project_root: Path | None = None) -> None:
    """Ensure calibration sections use the canonical enabled/generate pattern."""
    root = Path(project_root) if project_root else Path.cwd()
    scenario_names = set((config.get("scenarios") or {}).keys())

    errors: list[str] = []
    for section in _CALIBRATION_SECTIONS:
        name = section["name"]
        cfg = _resolve(config, section["path"])
        enabled = bool(cfg["enabled"])
        generate = bool(cfg["generate"])

        if enabled and generate:
            errors.append(
                f"{name}: enabled=true with generate=true is not allowed. "
                "The canonical generation pattern is enabled=false, generate=true "
                "so that 'enabled' is the single runtime source of truth."
            )

        if generate and section["has_scenario"]:
            scenario = cfg["scenario"]
            if scenario_names and scenario not in scenario_names:
                errors.append(
                    f"{name}: generate=true references scenario '{scenario}', "
                    f"but it is not defined under config['scenarios']."
                )

        if enabled and not generate:
            for key in section["files"]:
                path = root / cfg[key]
                if not path.exists():
                    errors.append(
                        f"{name}: enabled=true but {key} '{cfg[key]}' does not "
                        f"exist (resolved to {path})."
                    )

    if errors:
        bullet_list = "\n".join(f" - {msg}" for msg in errors)
        raise ValueError(f"Calibration configuration is inconsistent:\n{bullet_list}")
