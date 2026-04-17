#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Merge dietary intake data from multiple sources (GDD and FAOSTAT).

FAOSTAT data is raw food supply; this script applies waste fractions
to convert it to intake before merging with GDD data.

Input:
    - GDD dietary intake CSV
    - FAOSTAT food supply CSV (raw, not waste-adjusted)
    - Food loss & waste fractions CSV

Output:
    - Combined dietary intake CSV
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def main():
    gdd_file = snakemake.input.gdd
    fao_file = snakemake.input.faostat
    flw_file = snakemake.input.food_loss_waste
    output_file = snakemake.output.diet

    logger.info(f"Reading GDD data from {gdd_file}")
    gdd_df = pd.read_csv(gdd_file)

    logger.info(f"Reading FAOSTAT food supply data from {fao_file}")
    fao_df = pd.read_csv(fao_file)

    logger.info(f"Reading food loss/waste data from {flw_file}")
    flw_df = pd.read_csv(flw_file)

    # Apply waste correction to FAOSTAT data (convert supply to intake)
    # Build waste fraction lookup: (country, food_group) -> waste_fraction
    waste_lookup = flw_df.set_index(["country", "food_group"])[
        "waste_fraction"
    ].to_dict()

    def apply_waste(row):
        key = (row["country"], row["item"])
        waste_frac = waste_lookup.get(key, 0.0)
        return row["value"] * (1.0 - waste_frac)

    fao_df["value"] = fao_df.apply(apply_waste, axis=1)
    logger.info("Applied waste fractions to FAOSTAT food supply data")

    # Identify overlapping items
    gdd_items = set(gdd_df["item"].unique())
    fao_items = set(fao_df["item"].unique())

    overlap = gdd_items.intersection(fao_items)

    if overlap:
        logger.warning(
            f"Overlapping items found in both sources: {overlap}. FAOSTAT will take precedence."
        )
        # Remove overlapping items from GDD
        gdd_df = gdd_df[~gdd_df["item"].isin(overlap)]

    combined = pd.concat([gdd_df, fao_df], ignore_index=True)

    # Sort for consistency
    combined = combined.sort_values(["country", "item", "age"]).reset_index(drop=True)

    combined.to_csv(output_file, index=False)
    logger.info(f"Wrote {len(combined)} rows to {output_file}")


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
