# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Solving utilities for the food systems optimization model.

Modular package providing specialized constraint builders for the PyPSA
linopy model during optimization.
"""

from . import health, production_stability

__all__ = ["health", "production_stability"]
