# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for per-food macronutrient densities."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema

VALID_NUTRIENTS = ("cal", "carb", "fat", "protein")
VALID_UNITS = ("kcal/100g", "g/100g")

NUTRITION_SCHEMA = DataFrameSchema(
    {
        "food": Column(str, nullable=False, coerce=True),
        "nutrient": Column(
            str,
            nullable=False,
            coerce=True,
            checks=Check.isin(VALID_NUTRIENTS),
        ),
        "unit": Column(
            str,
            nullable=False,
            coerce=True,
            checks=Check.isin(VALID_UNITS),
        ),
        "value": Column(
            float,
            nullable=False,
            coerce=True,
            checks=Check.ge(0.0),
        ),
    },
    strict=True,
    coerce=True,
    unique=["food", "nutrient"],
)


def validate_nutrition(config: dict, project_root: Path) -> None:
    """Validate that nutrition.csv covers every food in food_groups.csv.

    The diet pipeline uses per-food kcal densities (``nutrient == "cal"``)
    and macronutrient shares; missing rows would silently drop foods from
    the kcal-anchored normalisation steps.
    """
    csv_path = project_root / "data" / "curated" / "nutrition.csv"
    groups_path = project_root / "data" / "curated" / "food_groups.csv"
    for path in (csv_path, groups_path):
        if not path.exists():
            raise FileNotFoundError(f"Expected data file at {path}")

    df = NUTRITION_SCHEMA.validate(pd.read_csv(csv_path))
    groups_df = pd.read_csv(groups_path)

    expected_foods = set(groups_df["food"].unique())
    pairs = set(zip(df["food"], df["nutrient"]))

    missing = sorted(
        f"{food}/{nutrient}"
        for food in expected_foods
        for nutrient in VALID_NUTRIENTS
        if (food, nutrient) not in pairs
    )
    if missing:
        raise ValueError(
            "nutrition.csv missing rows (food/nutrient pairs): " + ", ".join(missing)
        )
