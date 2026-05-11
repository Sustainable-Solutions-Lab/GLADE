# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for per-crop sowing rates against config crops."""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema
from snakemake.logging import logger

SEED_RATES_SCHEMA = DataFrameSchema(
    {
        "crop": Column(str, nullable=False, unique=True, coerce=True),
        "seed_kg_per_ha": Column(
            float,
            nullable=False,
            coerce=True,
            checks=Check.ge(0.0),
        ),
        "source": Column(str, nullable=False, coerce=True),
        "url": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)


def validate_seed_rates(config: dict, project_root: Path) -> None:
    """Validate that every config crop has a sowing-rate row in seed_rates.csv.

    The crop_production link builders deduct a seed share from yield using
    this table; there is no fallback, so a missing crop would silently leave
    seed unaccounted-for. We require strict coverage.
    """
    csv_path = project_root / "data" / "curated" / "seed_rates.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected data file at {csv_path}")

    df = SEED_RATES_SCHEMA.validate(pd.read_csv(csv_path, comment="#"))

    config_crops = set(config["crops"])
    mapped_crops = set(df["crop"].unique())

    missing = sorted(config_crops - mapped_crops)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            "Config crops missing sowing rates in seed_rates.csv: " f"{missing_text}"
        )

    unused = sorted(mapped_crops - config_crops)
    if unused:
        unused_text = ", ".join(unused)
        logger.warning(
            f"seed_rates.csv has rates for crops not in config (future crops?): {unused_text}"
        )
