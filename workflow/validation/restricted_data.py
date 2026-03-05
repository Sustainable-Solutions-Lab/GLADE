# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check that restricted (non-redistributable) data files are present.

Currently no restricted data files are required — the GLEAM 2.0 Mottet et al.
(2017) dependency was replaced by bundled GLEAM 3.0 data.
"""

from pathlib import Path


def validate_restricted_data(config: dict, root: Path) -> None:
    """Raise if required restricted data files are missing.

    Currently a no-op; retained for interface compatibility with the
    validation registry.
    """
