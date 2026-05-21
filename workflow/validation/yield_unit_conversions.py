# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for per-crop GAEZ yield raster unit-conversion overrides."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema
from snakemake.logging import logger

YIELD_UNIT_CONVERSIONS_SCHEMA = DataFrameSchema(
    {
        "code": Column(str, nullable=False, unique=True, coerce=True),
        "factor_to_t_per_ha": Column(
            float,
            nullable=False,
            coerce=True,
            checks=Check.gt(0.0),
        ),
        "note": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)


def validate_yield_unit_conversions(config: dict, project_root: Path) -> None:
    """Validate yield_unit_conversions.csv schema.

    Only crops whose GAEZ rasters deviate from the kg DM/ha default have
    rows; missing crops fall back to the 1e-3 multiplier at runtime. No
    completeness check against ``config["crops"]`` is therefore required,
    but unknown codes are flagged for drift.
    """
    csv_path = project_root / "data" / "curated" / "yield_unit_conversions.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = YIELD_UNIT_CONVERSIONS_SCHEMA.validate(pd.read_csv(csv_path, comment="#"))

    config_crops = set(config["crops"])
    unused = sorted(set(df["code"]) - config_crops)
    if unused:
        logger.warning(
            "yield_unit_conversions.csv has overrides for crops not in config: "
            + ", ".join(unused)
        )
