# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Helpers for converting external dietary intake values into the model's basis.

Each food in the model has a native mass basis (``data/curated/food_basis.csv``)
matching the kcal-density rows in nutrition.csv: cereals/legumes/oils/sugar
are ``dry``; fruits, vegetables, raw retail meat, eggs, and dairy are
``fresh``. External data sources (GDD, GBD, FAOSTAT FBS, NHANES) report
intake in their own basis per food group, declared in
``config["diet"]["source_basis"]``. When the source's basis differs from
the food's basis, ``convert_to_food_basis`` multiplies by the matching
factor in ``config["diet"]["weight_conversion"]``.

The factor tables are keyed ``"<from>_to_<to>"``:
- ``cooked_to_dry``: cereals/legumes lose ~55-60% mass when uncooked
  (cooked rice 100g ≈ 33g raw; cooked pasta 100g ≈ 40g dry; cooked beans
  100g ≈ 40g dry).
- ``cooked_to_fresh``: meat *gains* mass-density when raw (raw meat 100g
  is ~70g cooked; equivalently cooked 100g ≈ 143g raw retail).

For groups whose physical conversion is well-approximated by 1.0 (fresh
vegetables / fruits cooked vs. fresh: density barely changes), the table
entry can be omitted; the helper falls back to 1.0.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging

import pandas as pd

logger = logging.getLogger(__name__)


def load_food_basis(path: str | object) -> dict[str, str]:
    """Load food→basis ('dry' or 'fresh') from data/curated/food_basis.csv."""
    df = pd.read_csv(str(path), comment="#")
    bad = df[~df["basis"].isin(["dry", "fresh"])]
    if not bad.empty:
        raise ValueError(
            f"Invalid basis values in {path}: "
            f"{bad[['food', 'basis']].to_dict('records')}"
        )
    return df.set_index("food")["basis"].to_dict()


def resolve_source_basis(
    source: str,
    country: str | None,
    food_group: str,
    defaults: Mapping[str, Mapping[str, str]],
    country_overrides: Mapping[str, Mapping[str, Mapping[str, str]]] | None = None,
) -> str | None:
    """Return the basis ('dry'/'fresh'/'cooked') for a (source, country, group).

    Lookup order: (source, country, group) override, then (source, group)
    default. Returns None when nothing is declared, in which case callers
    should treat the value as already-in-target-basis (no conversion).
    """
    if country_overrides is not None and country is not None:
        per_country = country_overrides.get(source, {}).get(country, {})
        if food_group in per_country:
            return per_country[food_group]
    return defaults.get(source, {}).get(food_group)


def conversion_factor(
    from_basis: str,
    to_basis: str,
    food_or_group: str,
    factors: Mapping[str, Mapping[str, float]],
) -> float:
    """Return the multiplicative factor that converts from *from_basis* to *to_basis*.

    Returns 1.0 when the bases match. Looks up the table named
    ``"<from>_to_<to>"`` in *factors* and returns the entry for
    *food_or_group* (defaulting to 1.0 if absent).

    Raises ValueError if the requested basis pair has no table at all,
    so silent unit drift gets caught early.
    """
    if from_basis == to_basis:
        return 1.0
    table_name = f"{from_basis}_to_{to_basis}"
    if table_name not in factors:
        raise ValueError(
            f"No conversion table {table_name!r} configured "
            f"(needed for '{food_or_group}'). "
            f"Add weight_conversion.{table_name} to config or align the "
            f"source/food bases."
        )
    return float(factors[table_name].get(food_or_group, 1.0))


def convert_to_food_basis(
    value: float,
    *,
    food: str,
    source_basis: str,
    food_basis_map: Mapping[str, str],
    factors: Mapping[str, Mapping[str, float]],
    food_group: str | None = None,
) -> float:
    """Convert *value* from *source_basis* into the basis declared for *food*.

    The factor lookup uses *food_group* if provided (since the cooked-
    to-dry conversion is uniform per group), falling back to *food* if
    not. This lets callers that already have a per-group factor table
    work without restating it per food.
    """
    target = food_basis_map[food]
    key = food_group if food_group is not None else food
    f = conversion_factor(source_basis, target, key, factors)
    return value * f


def convert_series(
    values: pd.Series,
    *,
    foods: pd.Series,
    food_groups: pd.Series | None,
    source_basis_by_group: Mapping[str, str],
    food_basis_map: Mapping[str, str],
    factors: Mapping[str, Mapping[str, float]],
) -> pd.Series:
    """Vectorised variant: convert a Series of intake values per (food, group).

    *foods* and *food_groups* (optional) are aligned with *values*.
    Foods missing from *food_basis_map* or groups missing from
    *source_basis_by_group* fall through with no conversion.
    """
    foods = foods.astype(object)
    groups = foods if food_groups is None else food_groups.astype(object)
    multipliers = []
    for food, group in zip(foods.values, groups.values):
        target = food_basis_map.get(food)
        src = source_basis_by_group.get(group)
        if target is None or src is None or src == target:
            multipliers.append(1.0)
            continue
        f = conversion_factor(src, target, group, factors)
        multipliers.append(f)
    return values * pd.Series(multipliers, index=values.index)
