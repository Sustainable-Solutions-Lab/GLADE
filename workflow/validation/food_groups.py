# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for food group configuration against data files."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Column, DataFrameSchema

FOOD_GROUP_SCHEMA = DataFrameSchema(
    {
        "food": Column(str, nullable=False, unique=True, coerce=True),
        "group": Column(str, nullable=False, coerce=True),
    },
    strict=True,
    coerce=True,
)


def validate_food_groups(config: dict, project_root: Path) -> None:
    """Validate that config food groups cover all categories in data/curated/food_groups.csv.

    Note: Basic structure validation (types, uniqueness, non-empty) is handled
    by the JSON Schema. This validator only checks against the data file.
    """
    if (
        bool(config["validation"]["enforce_baseline_diet"])
        and config["food_groups"]["equal_by_country_source"]
    ):
        raise ValueError(
            "food_groups.equal_by_country_source cannot be set when "
            "validation.enforce_baseline_diet is true"
        )

    # JSON Schema already validated structure; we can safely access included
    included_groups = set(config["food_groups"]["included"])

    csv_path = project_root / "data" / "curated" / "food_groups.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = FOOD_GROUP_SCHEMA.validate(pd.read_csv(csv_path))
    csv_groups = set(df["group"].dropna().astype(str).str.strip().unique())

    missing = sorted(csv_groups - included_groups)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            f"Config food_groups.included missing groups present in data file: {missing_text}"
        )
