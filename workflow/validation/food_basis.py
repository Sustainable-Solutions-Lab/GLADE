# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation that every model food has a basis annotation."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema

FOOD_BASIS_SCHEMA = DataFrameSchema(
    {
        "food": Column(str, nullable=False, coerce=True),
        "basis": Column(
            str,
            nullable=False,
            coerce=True,
            checks=Check.isin(["dry", "fresh"]),
        ),
    },
    strict=True,
    coerce=True,
    unique=["food"],
)


def validate_food_basis(config: dict, project_root: Path) -> None:
    """Check coverage and group-consistency of food_basis.csv.

    1. Every food the model will instantiate (foods.csv pathway outputs
       whose input crop is in ``config["crops"]``, plus
       ``config["animal_products"]["include"]``) has a row in
       ``data/curated/food_basis.csv``.
    2. All foods within the same food group share a basis. The diet
       pipeline applies group-level conversion factors and silently
       drifts when a group is mixed.
    """
    foods_path = project_root / "data" / "curated" / "foods.csv"
    basis_path = project_root / "data" / "curated" / "food_basis.csv"
    groups_path = project_root / "data" / "curated" / "food_groups.csv"
    for path in (foods_path, basis_path, groups_path):
        if not path.exists():
            raise FileNotFoundError(f"Expected data file at {path}")

    foods_df = pd.read_csv(foods_path, comment="#")
    basis_df = FOOD_BASIS_SCHEMA.validate(pd.read_csv(basis_path, comment="#"))
    groups_df = pd.read_csv(groups_path)

    crops = set(config["crops"])
    pathway_foods = set(foods_df.loc[foods_df["crop"].isin(crops), "food"].unique())
    animal_products = set(config["animal_products"]["include"])
    expected_foods = pathway_foods | animal_products

    annotated = set(basis_df["food"])
    missing = sorted(expected_foods - annotated)
    if missing:
        raise ValueError(
            f"food_basis.csv missing entries for foods: {missing}. "
            "Every food bus instantiated in the model needs a basis "
            "annotation; extend data/curated/food_basis.csv."
        )

    # Group-consistency: foods within a group must share a basis
    food_to_basis = dict(zip(basis_df["food"], basis_df["basis"]))
    food_to_group = dict(zip(groups_df["food"], groups_df["group"]))
    by_group: dict[str, set[str]] = {}
    for food, basis in food_to_basis.items():
        group = food_to_group.get(food)
        if group is None:
            continue
        by_group.setdefault(group, set()).add(basis)
    inconsistent = {g: sorted(bs) for g, bs in by_group.items() if len(bs) > 1}
    if inconsistent:
        raise ValueError(
            f"Foods within these food groups disagree on basis: {inconsistent}. "
            "Group-level conversions are ambiguous; align food_basis.csv or "
            "split the group."
        )
