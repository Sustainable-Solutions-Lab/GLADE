# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validate that plotting.crop_groups covers all configured crops."""

from collections import Counter
from pathlib import Path


def validate_crop_groups(config: dict, project_root: Path) -> None:
    """Check every configured crop belongs to exactly one plotting crop group.

    Crop groups may list crops not present in the active ``crops`` list (e.g.
    when a sub-config uses a crop subset); that is allowed. What is *not*
    allowed is a configured crop that appears in no group or in multiple groups.
    """
    all_crops = set(config["crops"])
    # Non-food crops and grassland can also appear on production maps
    all_crops.update(config.get("non_food_crops", []))
    all_crops.add("grassland")

    group_cfg = config["plotting"]["crop_groups"]

    # Collect all crops assigned to groups and detect duplicates
    assigned: list[str] = []
    for _group_name, group_def in group_cfg.items():
        assigned.extend(group_def["crops"])

    counts = Counter(assigned)
    duplicates = sorted(c for c, n in counts.items() if n > 1)
    if duplicates:
        raise ValueError(
            f"Crops assigned to multiple plotting crop groups: {', '.join(duplicates)}"
        )

    grouped_crops = set(assigned)

    # Crops in config but not in any group
    missing = sorted(all_crops - grouped_crops)
    if missing:
        raise ValueError(
            f"Crops missing from plotting.crop_groups: {', '.join(missing)}"
        )
