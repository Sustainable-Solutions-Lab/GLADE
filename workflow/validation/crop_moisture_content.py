# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for per-crop moisture and food-conversion policy."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema
from snakemake.logging import logger

VALID_FOOD_CONVERSIONS = ("inverse_moisture", "identity")

CROP_MOISTURE_SCHEMA = DataFrameSchema(
    {
        "crop": Column(str, nullable=False, unique=True, coerce=True),
        "moisture_fraction": Column(
            float,
            nullable=False,
            coerce=True,
            checks=[Check.ge(0.0), Check.lt(1.0)],
        ),
        "food_conversion": Column(
            str,
            nullable=False,
            coerce=True,
            checks=Check.isin(VALID_FOOD_CONVERSIONS),
        ),
        "source": Column(str, nullable=True, coerce=True),
        "note": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)


def validate_crop_moisture_content(config: dict, project_root: Path) -> None:
    """Validate crop_moisture_content.csv covers every config crop.

    Schema enforces:
      - moisture_fraction in [0, 1).
      - food_conversion in {inverse_moisture, identity}: the per-crop policy
        consumed by ``utils._fresh_mass_conversion_factors`` to translate
        dry-matter crop bus mass into food bus mass (see the CSV header for
        the semantics).
    """
    csv_path = project_root / "data" / "curated" / "crop_moisture_content.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = CROP_MOISTURE_SCHEMA.validate(pd.read_csv(csv_path, comment="#"))

    config_crops = set(config["crops"])
    mapped_crops = set(df["crop"].unique())

    missing = sorted(config_crops - mapped_crops)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            "Config crops missing rows in crop_moisture_content.csv: " f"{missing_text}"
        )

    unused = sorted(mapped_crops - config_crops)
    if unused:
        unused_text = ", ".join(unused)
        logger.warning(
            "crop_moisture_content.csv has rows for crops not in config (future crops?): "
            f"{unused_text}"
        )
