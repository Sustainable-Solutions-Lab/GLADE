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
   cover well (dairy, eggs, poultry, vegetable oils). FAOSTAT overrides
   GDD on overlap.
3. NHANES / FPED (USA only, intake-based, 24-hour recall summaries).
   Overrides both GDD and FAOSTAT for the US for every (country, food
   group) it carries.

Input:
    - GDD dietary intake CSV
    - FAOSTAT food supply CSV (raw, not waste-adjusted)
    - NHANES dietary intake CSV (already in intake terms; no waste correction)
    - Food loss & waste fractions CSV

Output:
    - Combined dietary intake CSV
"""

import logging

import pandas as pd

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


def main():
    gdd_file = snakemake.input.gdd
    fao_file = snakemake.input.faostat
    nhanes_file = snakemake.input.nhanes
    flw_file = snakemake.input.food_loss_waste
    output_file = snakemake.output.diet

    logger.info(f"Reading GDD data from {gdd_file}")
    gdd_df = pd.read_csv(gdd_file)

    logger.info(f"Reading FAOSTAT food supply data from {fao_file}")
    fao_df = pd.read_csv(fao_file)

    logger.info(f"Reading NHANES dietary intake from {nhanes_file}")
    nhanes_df = pd.read_csv(nhanes_file)

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
