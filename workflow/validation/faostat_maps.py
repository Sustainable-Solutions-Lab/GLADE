# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema validation for FAOSTAT mapping tables.

Cross-config / cross-data semantic checks for ``faostat_biofuel_crop_map.csv``
and ``faostat_fiber_demand_map.csv`` (e.g. crop coverage, ``source_item``
validity) live in ``crop_food_pathways.py`` since they depend on
``foods.csv``.
"""

from pathlib import Path

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema

CROP_ITEM_MAP_SCHEMA = DataFrameSchema(
    {
        # alfalfa, biomass-sorghum etc. carry no FAOSTAT counterpart.
        "crop": Column(str, nullable=False, coerce=True),
        "faostat_item": Column(str, nullable=True, coerce=True),
        "share": Column(
            float,
            nullable=True,
            coerce=True,
            checks=[Check.gt(0.0), Check.le(1.0)],
        ),
    },
    strict=True,
    coerce=True,
)

FOOD_ITEM_MAP_SCHEMA = DataFrameSchema(
    {
        "food": Column(str, nullable=False, coerce=True),
        "faostat_item": Column(str, nullable=False, coerce=True),
        "item_code": Column(int, nullable=False, coerce=True),
    },
    strict=True,
    coerce=True,
)

BIOFUEL_CROP_MAP_SCHEMA = DataFrameSchema(
    {
        "crop": Column(str, nullable=False, unique=True, coerce=True),
        "source_item": Column(str, nullable=False, coerce=True),
        "fbs_item": Column(str, nullable=False, coerce=True),
        "fbs_item_code": Column(int, nullable=False, coerce=True),
        "pathway_factor": Column(
            float,
            nullable=False,
            coerce=True,
            checks=[Check.gt(0.0), Check.le(1.0)],
        ),
        "fbs_is_processed": Column(bool, nullable=False, coerce=True),
        "notes": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)

FIBER_DEMAND_MAP_SCHEMA = DataFrameSchema(
    {
        "crop": Column(str, nullable=False, unique=True, coerce=True),
        "source_item": Column(str, nullable=False, coerce=True),
        "qcl_item": Column(str, nullable=False, coerce=True),
        "qcl_item_code": Column(int, nullable=False, coerce=True),
        "qcl_element_code": Column(int, nullable=False, coerce=True),
        "notes": Column(str, nullable=True, coerce=True),
    },
    strict=True,
    coerce=True,
)

FOOD_QCL_RESOLUTION_SCHEMA = DataFrameSchema(
    {
        "food": Column(str, nullable=False, coerce=True),
        "fbs_item_code": Column(int, nullable=False, coerce=True),
        "qcl_item_name": Column(str, nullable=False, coerce=True),
        "qcl_item_code": Column(int, nullable=False, coerce=True),
    },
    strict=True,
    coerce=True,
    unique=["food", "qcl_item_code"],
)


_SCHEMAS = {
    "faostat_crop_item_map.csv": CROP_ITEM_MAP_SCHEMA,
    "faostat_food_item_map.csv": FOOD_ITEM_MAP_SCHEMA,
    "faostat_biofuel_crop_map.csv": BIOFUEL_CROP_MAP_SCHEMA,
    "faostat_fiber_demand_map.csv": FIBER_DEMAND_MAP_SCHEMA,
    "faostat_food_qcl_resolution.csv": FOOD_QCL_RESOLUTION_SCHEMA,
}


def validate_faostat_maps(config: dict, project_root: Path) -> None:
    """Run schema validation on every FAOSTAT mapping CSV."""
    curated = project_root / "data" / "curated"
    for filename, schema in _SCHEMAS.items():
        path = curated / filename
        if not path.exists():
            raise FileNotFoundError(f"Expected data file at {path}")
        schema.validate(pd.read_csv(path, comment="#"))
