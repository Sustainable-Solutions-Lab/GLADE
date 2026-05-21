# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for the UN M49 -> ISO3 country code lookup table."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Column, DataFrameSchema

M49_CODES_SCHEMA = DataFrameSchema(
    {
        "M49 Code": Column(int, nullable=False, unique=True, coerce=True),
        "ISO-alpha3 Code": Column(str, nullable=False, unique=True, coerce=True),
        "Country or Area": Column(str, nullable=False, coerce=True),
    },
    strict=False,
    coerce=True,
)


def validate_m49_codes(config: dict, project_root: Path) -> None:
    """Validate M49-codes.csv and check that every configured country is listed.

    The schema only constrains the columns the workflow actually consumes
    (M49 Code, ISO-alpha3 Code, Country or Area); the additional UN
    metadata columns are tolerated via ``strict=False``.
    """
    csv_path = project_root / "data" / "curated" / "M49-codes.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = M49_CODES_SCHEMA.validate(
        pd.read_csv(csv_path, sep=";", encoding="utf-8-sig", comment="#")
    )

    config_countries = {str(c).upper() for c in config["countries"]}
    listed = set(df["ISO-alpha3 Code"].str.upper())
    missing = sorted(config_countries - listed)
    if missing:
        raise ValueError(
            "M49-codes.csv missing entries for configured ISO3 countries: "
            + ", ".join(missing)
        )
