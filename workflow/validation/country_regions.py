# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for country-to-Wirsenius-region mapping."""

from pathlib import Path

import pandas as pd


def validate_country_regions(config: dict, project_root: Path) -> None:
    """Validate that every config country has a Wirsenius region mapping.

    This mapping is needed by the GLEAM3 ME requirements computation
    (for dairy:meat ratio guidance via Wirsenius).
    """
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
