# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Helpers for the Global Dietary Database for Impact Assessments."""

GDD_IA_RELEASE_YEARS = tuple(range(1990, 2021, 5))


def closest_gdd_ia_release_year(reference_year: int) -> int:
    """Return the closest GDD-IA release year, preferring earlier ties."""
    return min(
        GDD_IA_RELEASE_YEARS,
        key=lambda year: (abs(year - reference_year), year),
    )
