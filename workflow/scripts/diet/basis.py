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
the food's basis, ``convert_intake`` multiplies by the matching factor
in the top-level ``config["weight_conversion"]``.

The factor tables are keyed ``"<from>_to_<to>"``:
- ``cooked_to_dry``: cereals/legumes lose ~55-60% mass when uncooked
  (cooked rice 100g ≈ 33g raw; cooked pasta 100g ≈ 40g dry; cooked beans
  100g ≈ 40g dry).
- ``cooked_to_fresh``: meat *gains* mass-density when raw (raw meat 100g
  is ~70g cooked; equivalently cooked 100g ≈ 143g raw retail).
- ``carcass_to_fresh``: meat carcass weight → boneless retail weight
  (OECD-FAO Outlook 2023 Box 6.1). Shared with the animal-product
  pipeline so FAOSTAT-QCL carcass production, FBS-supply-anchored intake
  (FBS override path), implicit FLW deductions, and feed-to-ME
  requirements all use one canonical conversion table.

For groups whose physical conversion is well-approximated by 1.0 (fresh
vegetables / fruits cooked vs. fresh: density barely changes), the table
entry can be omitted; the helper falls back to 1.0.
"""

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


def load_source_basis_country_overrides(
    path: str | object,
) -> dict[str, dict[str, dict[str, str]]]:
    """Load (source, country, food_group) -> basis from a curated CSV.

    Expected columns: source, country, food_group, basis. Additional
    columns (region, note, etc.) are ignored — they exist for human
    documentation only.
    """
    df = pd.read_csv(str(path), comment="#")
    required = {"source", "country", "food_group", "basis"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"{path}: missing required columns {sorted(missing)}; "
            f"have {sorted(df.columns)}"
        )
    overrides: dict[str, dict[str, dict[str, str]]] = {}
    for _, row in df.iterrows():
        src = str(row["source"]).strip()
        country = str(row["country"]).strip().upper()
        group = str(row["food_group"]).strip()
        basis = str(row["basis"]).strip()
        overrides.setdefault(src, {}).setdefault(country, {})[group] = basis
    return overrides


def build_group_basis(
    food_basis: Mapping[str, str], food_to_group: Mapping[str, str]
) -> dict[str, str]:
    """Derive each food group's basis from its constituent foods.

    All foods within a group are expected to share a basis (cereals
    dry, fresh produce fresh, etc.). Raises if a group has mixed
    bases, since that would make group-level conversion ambiguous.
    """
    by_group: dict[str, set[str]] = {}
    for food, basis in food_basis.items():
        group = food_to_group.get(food)
        if group is None:
            continue
        by_group.setdefault(group, set()).add(basis)
    inconsistent = {g: bs for g, bs in by_group.items() if len(bs) > 1}
    if inconsistent:
        raise ValueError(
            f"Foods within these groups disagree on basis: {inconsistent}. "
            "Either align food_basis.csv or split the group."
        )
    return {g: next(iter(bs)) for g, bs in by_group.items()}


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
    *food_or_group* (defaulting to 1.0 when absent: foods that don't
    appear in a particular basis-pair table simply don't need conversion
    along that pair, e.g. plant foods called against carcass_to_fresh).

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


def convert_intake(
    df: pd.DataFrame,
    *,
    source: str,
    value_column: str,
    group_column: str,
    country_column: str | None,
    source_basis: Mapping[str, Mapping[str, str]],
    source_basis_country_overrides: Mapping[str, Mapping[str, Mapping[str, str]]],
    target_basis_by_key: Mapping[str, str],
    factors: Mapping[str, Mapping[str, float]],
) -> pd.DataFrame:
    """Apply per-(country, key) basis conversion to *df[value_column]*.

    The *group_column* values are looked up in *target_basis_by_key*;
    keys may be food groups, individual foods, or a mix of both
    (callers like ``merge_dietary_sources`` supply a merged
    food→basis merged with group→basis dict so the same row format can carry
    direct food rows alongside group totals).

    Returns a copy of *df* with *value_column* multiplied by the
    appropriate factor on each row. Rows whose source basis is not
    declared, whose target basis is unknown, or whose source and
    target match are passed through unchanged.

    Logs a per-(key, src→tgt) summary count and (when overrides are
    in play) the set of countries hit by an override.
    """
    df = df.copy()
    multipliers: list[float] = []
    summary_counts: dict[tuple[str, str, str], int] = {}
    overridden_counts: dict[tuple[str, str], int] = {}

    if country_column is not None:
        country_iter = df[country_column].tolist()
    else:
        country_iter = [None] * len(df)
    group_iter = df[group_column].tolist()

    for country, grp in zip(country_iter, group_iter):
        src = resolve_source_basis(
            source, country, grp, source_basis, source_basis_country_overrides
        )
        tgt = target_basis_by_key.get(grp)
        # Track override usage even when the override matches the global
        # default (so logs reflect declared intent, not just deltas).
        if country is not None:
            global_src = source_basis.get(source, {}).get(grp)
            if global_src is not None and src is not None and src != global_src:
                overridden_counts[(country, grp)] = (
                    overridden_counts.get((country, grp), 0) + 1
                )
        if src is None or tgt is None or src == tgt:
            multipliers.append(1.0)
            continue
        f = conversion_factor(src, tgt, grp, factors)
        multipliers.append(f)
        summary_counts[(grp, src, tgt)] = summary_counts.get((grp, src, tgt), 0) + 1

    df[value_column] = df[value_column] * pd.Series(multipliers, index=df.index)

    if summary_counts:
        for (grp, src, tgt), n in sorted(summary_counts.items()):
            f = conversion_factor(src, tgt, grp, factors)
            logger.info(
                "%s: converted %d %r rows %s -> %s (factor %.3f)",
                source,
                n,
                grp,
                src,
                tgt,
                f,
            )
    else:
        logger.info("%s: no basis conversions applied", source)
    if overridden_counts:
        n_pairs = len(overridden_counts)
        countries = sorted({c for (c, _) in overridden_counts})
        logger.info(
            "%s: country overrides active for %d (country, group) pairs "
            "covering %d countries (%s%s)",
            source,
            n_pairs,
            len(countries),
            ", ".join(countries[:8]),
            "..." if len(countries) > 8 else "",
        )
    return df
