# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation that multi-cropping combinations reference only configured crops."""

from pathlib import Path


def validate_multi_cropping(config: dict, project_root: Path) -> None:
    """Validate that every crop in a multi-cropping combination is in the crops list.

    Multi-cropping combinations (defined in ``config["multiple_cropping"]``)
    reference crops by name. Each referenced crop must appear in
    ``config["crops"]`` so that the corresponding crop buses exist at model
    build time.
    """
    combinations = config.get("multiple_cropping")
    if not combinations:
        return

    config_crops = set(config["crops"])
    missing: list[str] = []

    for combo_name, entry in combinations.items():
        if entry is None:
            continue
        for crop in entry["crops"]:
            if crop not in config_crops:
                missing.append(f"{combo_name}: {crop}")

    if missing:
        detail = ", ".join(missing)
        raise ValueError(
            f"Multi-cropping combinations reference crops not in config['crops']: "
            f"{detail}"
        )
