#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Merge dietary intake data from multiple sources.

Three sources contribute, in increasing order of precedence:
1. GDD (Global Dietary Database, individual-level surveys, intake-based) --
   default for most food groups in most countries.
2. FAOSTAT FBS food supply (per-country, year-specific, supply-based) --
   waste-corrected via `food_loss_waste.csv`. Used for groups GDD does not
   cover well (dairy, eggs, poultry, vegetable oils, sugar). FAOSTAT
   overrides GDD on overlap.
3. NHANES / FPED (USA only, intake-based, 24-hour recall summaries).
   Overrides both GDD and FAOSTAT for the US for every (country, food
   group) it carries.

Each source's intake values are converted into the model's per-food
mass basis (declared in data/curated/food_basis.csv) before merging.
The basis-conversion is driven by the per-source per-group basis
declarations in config["diet"]["source_basis"] and the factor tables
in config["diet"]["weight_conversion"]; see workflow/scripts/diet/basis.py.

In particular GDD reports cooked / as-consumed weight for cereals,
legumes, and meats; the helper multiplies those values by the
matching cooked-to-dry (cereals/legumes) or cooked-to-fresh (meat)
factor so the merged dietary_intake.csv is consistently in model basis.

Input:
    - GDD dietary intake CSV
    - FAOSTAT food supply CSV (raw, not waste-adjusted)
    - NHANES dietary intake CSV (already in intake terms; no waste correction)
    - Food loss & waste fractions CSV
    - data/curated/food_basis.csv

Output:
    - Combined dietary intake CSV
"""

import logging

import pandas as pd

from workflow.scripts.diet.basis import (
    conversion_factor,
    load_food_basis,
    resolve_source_basis,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def _drop_overlap(
    target: pd.DataFrame, override: pd.DataFrame, label: str
) -> pd.DataFrame:
    """Remove rows from `target` that are superseded by entries in `override`.

    Overlap is defined per (country, item) so that an override source that
    covers only the United States doesn't drop the rest of the world's
    target rows.
    """
    if override.empty:
        return target
    override_keys = set(zip(override["country"], override["item"]))
    if not override_keys:
        return target
    target_keys = set(zip(target["country"], target["item"]))
    dropped = override_keys & target_keys
    if not dropped:
        return target
    countries_dropped = sorted({c for c, _ in dropped})
    items_dropped = sorted({i for _, i in dropped})
    logger.info(
        "%s overrides target for %d (country, item) pairs (countries: %s; items: %s)",
        label,
        len(dropped),
        ", ".join(countries_dropped[:8]) + ("…" if len(countries_dropped) > 8 else ""),
        ", ".join(items_dropped),
    )
    mask = pd.Series(
        list(zip(target["country"], target["item"])), index=target.index
    ).isin(dropped)
    return target.loc[~mask].copy()


def _apply_basis_conversion(
    df: pd.DataFrame,
    *,
    source: str,
    source_basis: dict[str, dict[str, str]],
    source_basis_country_overrides: dict[str, dict[str, dict[str, str]]],
    group_basis: dict[str, str],
    factors: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Convert intake values from each source's basis into the model's basis.

    For each row, look up the source's basis declaration for (country,
    food_group) -- consulting source_basis_country_overrides first and
    falling back to the global source_basis -- then compare to the
    group's target basis (derived from data/curated/food_basis.csv via
    food_groups.csv). When they differ, apply the matching factor table.
    Groups with no declared source basis pass through unchanged.
    """
    df = df.copy()
    multipliers = []
    # Aggregate counts at (group, country, src->tgt) granularity but log
    # at (group, src->tgt) granularity to keep the log readable.
    summary_counts: dict[tuple[str, str, str], int] = {}
    overridden_counts: dict[tuple[str, str], int] = {}
    for country, grp in zip(df["country"], df["item"]):
        src = resolve_source_basis(
            source, country, grp, source_basis, source_basis_country_overrides
        )
        tgt = group_basis.get(grp)
        # Track override usage regardless of whether it leads to a conversion:
        # an override that matches the target (so no conversion happens) is
        # still meaningful information when reasoning about the baseline.
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
    df["value"] = df["value"] * pd.Series(multipliers, index=df.index)
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


def _build_group_basis(
    food_basis: dict[str, str], food_to_group: dict[str, str]
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


def main():
    gdd_file = snakemake.input.gdd
    fao_file = snakemake.input.faostat
    nhanes_file = snakemake.input.nhanes
    flw_file = snakemake.input.food_loss_waste
    food_groups_file = snakemake.input.food_groups
    food_basis_file = snakemake.input.food_basis
    output_file = snakemake.output.diet

    source_basis = {
        src: {str(g): str(b) for g, b in groups.items()}
        for src, groups in dict(snakemake.params.source_basis).items()
    }
    source_basis_country_overrides = {
        src: {
            str(country): {str(g): str(b) for g, b in groups.items()}
            for country, groups in countries.items()
        }
        for src, countries in dict(
            snakemake.params.source_basis_country_overrides
        ).items()
    }
    weight_conversion = {
        str(table): {str(k): float(v) for k, v in entries.items()}
        for table, entries in dict(snakemake.params.weight_conversion).items()
    }

    food_basis = load_food_basis(food_basis_file)
    food_to_group = pd.read_csv(food_groups_file).set_index("food")["group"].to_dict()
    group_basis = _build_group_basis(food_basis, food_to_group)

    logger.info(f"Reading GDD data from {gdd_file}")
    gdd_df = pd.read_csv(gdd_file)
    gdd_df = _apply_basis_conversion(
        gdd_df,
        source="gdd",
        source_basis=source_basis,
        source_basis_country_overrides=source_basis_country_overrides,
        group_basis=group_basis,
        factors=weight_conversion,
    )

    logger.info(f"Reading FAOSTAT food supply data from {fao_file}")
    fao_df = pd.read_csv(fao_file)
    # FAOSTAT supplements arrive in "raw supply" basis; declared per group
    # in source_basis.faostat_fbs_supplement. Apply the same basis
    # conversion before the waste-correction step below.
    fao_df = _apply_basis_conversion(
        fao_df,
        source="faostat_fbs_supplement",
        source_basis=source_basis,
        source_basis_country_overrides=source_basis_country_overrides,
        group_basis=group_basis,
        factors=weight_conversion,
    )

    logger.info(f"Reading NHANES dietary intake from {nhanes_file}")
    nhanes_df = pd.read_csv(nhanes_file)
    # NHANES (FPED) is in a hybrid basis we leave untouched.

    logger.info(f"Reading food loss/waste data from {flw_file}")
    flw_df = pd.read_csv(flw_file)

    # Apply waste correction to FAOSTAT data (convert supply to intake).
    # NHANES values are already intake-based and do not get this correction.
    waste_lookup = flw_df.set_index(["country", "food_group"])[
        "waste_fraction"
    ].to_dict()

    def apply_waste(row):
        key = (row["country"], row["item"])
        waste_frac = waste_lookup.get(key, 0.0)
        return row["value"] * (1.0 - waste_frac)

    fao_df["value"] = fao_df.apply(apply_waste, axis=1)
    logger.info("Applied waste fractions to FAOSTAT food supply data")

    # Apply precedence: FAOSTAT overrides GDD (existing behaviour); NHANES
    # overrides both for the (country, item) pairs it covers.
    gdd_df = _drop_overlap(gdd_df, fao_df, "FAOSTAT")
    gdd_df = _drop_overlap(gdd_df, nhanes_df, "NHANES")
    fao_df = _drop_overlap(fao_df, nhanes_df, "NHANES")

    combined = pd.concat([gdd_df, fao_df, nhanes_df], ignore_index=True)

    # Sort for consistency
    combined = combined.sort_values(["country", "item", "age"]).reset_index(drop=True)

    combined.to_csv(output_file, index=False)
    logger.info(f"Wrote {len(combined)} rows to {output_file}")


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
