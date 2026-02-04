# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for crop-to-food processing pathways against config crops."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Column, DataFrameSchema
from snakemake.logging import logger

FOODS_SCHEMA = DataFrameSchema(
    {
        "pathway": Column(str, nullable=False, coerce=True),
        "crop": Column(str, nullable=False, coerce=True),
        "food": Column(str, nullable=False, coerce=True),
        "factor": Column(float, nullable=False, coerce=True),
        "description": Column(str, nullable=False, coerce=True),
    },
    strict=True,
    coerce=True,
    # Each (pathway, food) combination should be unique
    unique=["pathway", "food"],
)


def validate_crop_food_pathways(config: dict, project_root: Path) -> None:
    """Validate that foods.csv references only food crops defined in config.

    Crops listed in config["non_food_crops"] (e.g. fodder, biomass) are excluded
    from this validation since they don't produce human food.

    Also validates CSV structure and warns about crops without pathways.

    Note: Basic config structure is validated by JSON Schema.
    """
    csv_path = project_root / "data" / "curated" / "foods.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = FOODS_SCHEMA.validate(pd.read_csv(csv_path, comment="#"))

    # Ensure all food crops have a pathway entry in foods.csv.
    # non_food_crops (fodder, biomass) are excluded from this check.
    config_crops = set(config["crops"])
    non_food_crops = set(config["non_food_crops"])
    food_crops = config_crops - non_food_crops
    csv_crops = set(df["crop"].unique())

    missing = sorted(food_crops - csv_crops)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Config crops missing in foods.csv pathways: {missing_text}")

    # Warn about crops in foods.csv that are not in config
    # (allowed for reduced-scope configs like doc_figures).
    unused = sorted(csv_crops - config_crops)
    if unused:
        unused_text = ", ".join(unused)
        logger.warning(
            "foods.csv contains crops not in config (ignored): " f"{unused_text}"
        )

    # Check that byproducts listed in config appear as foods
    byproducts_cfg = set(config.get("byproducts", []))
    foods_in_csv = set(df["food"].unique())

    missing_byproducts = sorted(byproducts_cfg - foods_in_csv)
    if missing_byproducts:
        missing_text = ", ".join(missing_byproducts)
        raise ValueError(f"Byproducts in config not found in foods.csv: {missing_text}")
