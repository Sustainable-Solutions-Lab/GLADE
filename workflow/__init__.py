# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Python package marker for workflow utilities (rules, validation, scripts)."""

from pathlib import Path

import tomllib

# Single source of truth for the project version is pixi.toml; the editable
# package version (pyproject dynamic attr) and the docs both derive from it.
with (Path(__file__).resolve().parent.parent / "pixi.toml").open("rb") as _f:
    __version__ = tomllib.load(_f)["workspace"]["version"]
