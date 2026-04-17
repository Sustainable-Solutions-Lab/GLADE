# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Utility helpers for consistent categorical color assignment."""

from collections.abc import Iterable, Mapping

import matplotlib.cm as cm
import matplotlib.colors as mcolors


def categorical_colors(
    labels: Iterable[str],
    overrides: Mapping[str, str] | None = None,
    *,
    cmap_name: str = "tab20",
) -> dict[str, str]:
    """Return deterministic colors for labels, applying overrides when provided."""

    overrides_normalized = {
        str(key): mcolors.to_hex(value)
        for key, value in (overrides or {}).items()
        if value is not None
    }

    cmap = cm.get_cmap(cmap_name)
    colors: dict[str, str] = {}
    for idx, label in enumerate(labels):
        label_str = str(label)
        if label_str in overrides_normalized:
            colors[label_str] = overrides_normalized[label_str]
        else:
            colors[label_str] = mcolors.to_hex(cmap(idx % cmap.N))
    return colors
