# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validate diet basis declarations and conversion-factor tables.

Covers config.diet.source_basis (basis values, source coverage),
config.diet.weight_conversion (table-name pattern), and
data/curated/diet_source_basis_overrides.csv (columns, basis values,
source consistency).

Note: source_basis and weight_conversion keys are *not* required to
match food_groups.csv — sources may carry their own internal labels
(e.g. GBD emits ``milk`` for cross-validation) that get matched
downstream against per-source prepared dataframes. Validating those
labels reliably would require enumerating every prepare_* script.
"""

from pathlib import Path
import re

import pandas as pd

ALLOWED_BASES = {"dry", "fresh", "cooked", "brewed"}
TABLE_NAME_RE = re.compile(r"^([a-z]+)_to_([a-z]+)$")
OVERRIDES_REQUIRED_COLUMNS = {"source", "country", "food_group", "basis"}


def validate_diet_basis(config: dict, project_root: Path) -> None:
    diet_cfg = config["diet"]
    source_basis = diet_cfg["source_basis"]
    weight_conversion = diet_cfg["weight_conversion"]

    errors: list[str] = []

    # source_basis: basis values must be valid (also enforced by JSON
    # schema; re-asserted defensively in case the schema relaxes).
    for source, mapping in source_basis.items():
        for key, basis in mapping.items():
            if basis not in ALLOWED_BASES:
                errors.append(
                    f"diet.source_basis.{source}.{key}: basis {basis!r} not in "
                    f"{sorted(ALLOWED_BASES)}"
                )

    # weight_conversion: table name must be <basis>_to_<basis> with both
    # sides in the basis vocabulary; src != dst (a no-op table is a bug).
    for table_name in weight_conversion:
        m = TABLE_NAME_RE.match(table_name)
        if not m:
            errors.append(
                f"diet.weight_conversion.{table_name}: name does not match "
                "'<from>_to_<to>'"
            )
            continue
        src, dst = m.group(1), m.group(2)
        bad = [b for b in (src, dst) if b not in ALLOWED_BASES]
        if bad:
            errors.append(
                f"diet.weight_conversion.{table_name}: bases {bad} not in "
                f"{sorted(ALLOWED_BASES)}"
            )
        if src == dst:
            errors.append(
                f"diet.weight_conversion.{table_name}: src and dst basis are equal"
            )

    # diet_source_basis_overrides.csv
    overrides_path = (
        project_root / "data" / "curated" / "diet_source_basis_overrides.csv"
    )
    if not overrides_path.exists():
        raise FileNotFoundError(f"Expected data file at {overrides_path}")
    df = pd.read_csv(overrides_path, comment="#")
    missing_cols = OVERRIDES_REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        errors.append(
            f"{overrides_path.name}: missing required columns {sorted(missing_cols)}"
        )
    else:
        bad_basis = sorted(set(df["basis"].astype(str).str.strip()) - ALLOWED_BASES)
        if bad_basis:
            errors.append(
                f"{overrides_path.name}: basis values not in "
                f"{sorted(ALLOWED_BASES)}: {bad_basis}"
            )
        bad_source = sorted(
            {
                s
                for s in df["source"].astype(str).str.strip().unique()
                if s not in source_basis
            }
        )
        if bad_source:
            errors.append(
                f"{overrides_path.name}: source values not declared in "
                f"diet.source_basis: {bad_source}"
            )

    if errors:
        bullet = "\n".join(f" - {msg}" for msg in errors)
        raise ValueError(f"Diet basis validation found problems:\n{bullet}")
