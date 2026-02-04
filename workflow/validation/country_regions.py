# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for country-to-Wirsenius-region mapping and feed efficiency config."""

from pathlib import Path

import pandas as pd

# Valid Wirsenius (2000) region names
VALID_WIRSENIUS_REGIONS = {
    "East Asia",
    "East Europe",
    "Latin America & Caribbean",
    "North Africa & West Asia",
    "North America & Oceania",
    "South & Central Asia",
    "Sub-Saharan Africa",
    "West Europe",
}


def validate_country_regions(config: dict, project_root: Path) -> None:
    """Validate country-region mappings and feed efficiency region config.

    Checks:
    1. Every country in config["countries"] has a mapping in country_wirsenius_region.csv
    2. If feed_efficiency_regions is a list, all entries are valid Wirsenius region names
    """
    # Check 1: All countries have region mappings
    config_countries = set(config["countries"])

    csv_path = project_root / "data" / "curated" / "country_wirsenius_region.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = pd.read_csv(csv_path, comment="#")
    mapped_countries = set(df["country"].dropna().astype(str).str.strip().unique())

    missing = sorted(config_countries - mapped_countries)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            f"Countries in config missing from data/curated/country_wirsenius_region.csv: {missing_text}. "
            f"Add mappings for these countries to enable feed conversion efficiency calculations."
        )

    # Check 2: If feed_efficiency_regions is a list, validate region names
    regions = config["animal_products"]["feed_efficiency_regions"]
    if regions is not None:
        invalid = sorted(set(regions) - VALID_WIRSENIUS_REGIONS)
        if invalid:
            invalid_text = ", ".join(f"'{r}'" for r in invalid)
            valid_text = ", ".join(sorted(VALID_WIRSENIUS_REGIONS))
            raise ValueError(
                f"Invalid feed_efficiency_regions: {invalid_text}. "
                f"Valid regions are: {valid_text}"
            )
