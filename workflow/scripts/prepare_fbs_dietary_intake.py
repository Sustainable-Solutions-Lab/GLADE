#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Derive baseline per-(country, food-group) dietary intake from FAOSTAT FBS.

Thin wrapper around :mod:`workflow.scripts.diet.fbs_intake` (see its
module docstring for the method): group intake mass is derived from the
FBS "Food supply (kcal/capita/day)" element at model-basis energy
densities and corrected for consumer waste. The output mirrors the
schema of ``gdd_ia_dietary_intake.csv`` (unit, item, country, age,
year, value) so ``merge_dietary_sources`` can consume either source.

Input:
    - faostat_fbs_items_kcal.csv (per-(country, item) FBS energy supply)
    - faostat_food_item_map.csv  (food -> FBS item codes)
    - food_groups.csv            (food -> group)
    - nutrition.csv              (model-basis energy densities)
    - food_loss_waste.csv        (consumer waste fractions)

Output:
    - fbs_dietary_intake.csv: unit,item,country,age,year,value
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.diet.fbs_intake import UNIT_BY_GROUP, build_fbs_group_intake
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger("prepare_fbs_dietary_intake")


def main() -> None:
    kcal_supply = pd.read_csv(snakemake.input["fbs_items_kcal"])
    food_item_map = pd.read_csv(snakemake.input["food_item_map"], comment="#")
    food_groups = pd.read_csv(snakemake.input["food_groups"])
    nutrition = pd.read_csv(snakemake.input["nutrition"])
    food_loss_waste = pd.read_csv(snakemake.input["food_loss_waste"])

    countries = [str(c).upper() for c in snakemake.params["countries"]]
    food_groups_included = list(snakemake.params["food_groups"])
    byproducts = list(snakemake.params["byproducts"])
    whole_grain_shares = {
        str(k): float(v)
        for k, v in dict(snakemake.params["whole_grain_shares"]).items()
    }
    baseline_age = str(snakemake.params["baseline_age"])
    reference_year = int(snakemake.params["reference_year"])

    intake = build_fbs_group_intake(
        kcal_supply=kcal_supply,
        food_item_map=food_item_map,
        food_groups=food_groups,
        nutrition=nutrition,
        food_loss_waste=food_loss_waste,
        countries=countries,
        food_groups_included=food_groups_included,
        byproducts=byproducts,
        whole_grain_shares=whole_grain_shares,
    )

    out = intake.rename(columns={"food_group": "item"})
    out["unit"] = out["item"].map(UNIT_BY_GROUP)
    out["age"] = baseline_age
    out["year"] = reference_year
    out = (
        out[["unit", "item", "country", "age", "year", "value"]]
        .sort_values(["country", "item"])
        .reset_index(drop=True)
    )

    output_path = Path(snakemake.output["diet"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %d groups) to %s",
        len(out),
        out["country"].nunique(),
        out["item"].nunique(),
        output_path,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
