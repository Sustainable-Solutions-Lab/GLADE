# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for GAEZ crop code mappings against config crops."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Column, DataFrameSchema
from snakemake.logging import logger

GAEZ_MAPPING_SCHEMA = DataFrameSchema(
    {
        "crop_name": Column(str, nullable=False, unique=True, coerce=True),
        "description": Column(str, nullable=False, coerce=True),
        "res02_code": Column(str, nullable=False, coerce=True),
        "res05_code": Column(str, nullable=False, coerce=True),
        "res06_code": Column(str, nullable=False, coerce=True),
        "res02_fallback_crop": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)


def validate_gaez_crop_mapping(config: dict, project_root: Path) -> None:
    """Validate that all config crops have GAEZ code mappings.

    This ensures that GAEZ data can be retrieved for all crops defined
    in the configuration.

    Note: Basic config structure is validated by JSON Schema.
    """
    csv_path = project_root / "data" / "curated" / "gaez_crop_code_mapping.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = GAEZ_MAPPING_SCHEMA.validate(pd.read_csv(csv_path))

    config_crops = set(config["crops"])
    mapped_crops = set(df["crop_name"].unique())

    fallback_crops = (
        df["res02_fallback_crop"]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s != ""]
    )
    missing_fallbacks = sorted(set(fallback_crops) - mapped_crops)
    if missing_fallbacks:
        missing_text = ", ".join(missing_fallbacks)
        raise ValueError(
            "GAEZ RES02 fallback crops missing mappings in "
            f"gaez_crop_code_mapping.csv: {missing_text}"
        )

    # Check that all config crops have mappings
    missing = sorted(config_crops - mapped_crops)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            f"Config crops missing GAEZ mappings in gaez_crop_code_mapping.csv: {missing_text}"
        )

    # Warn about unused mappings (could be future crops or indicate typos)
    unused = sorted(mapped_crops - config_crops)
    if unused:
        unused_text = ", ".join(unused)
        logger.warning(
            f"GAEZ mappings exist for crops not in config (future crops?): {unused_text}"
        )
