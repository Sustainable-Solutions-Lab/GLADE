#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge the configured group-intake source with NHANES (USA override).

Inputs:
- ``group_intake``: per-(country, group) intake in model basis (g/day)
  from the configured ``diet.source`` -- either GDD-IA (kcal-derived and
  proxy-filled) or the FBS-derived estimate (kcal-derived and
  waste-corrected).
- ``nhanes``: NHANES (FPED-derived) USA intake, per food group.
  NHANES values are intake-based and already in model basis; they
  override the source values for the country/items NHANES covers.
- ``faostat_supply``: FAOSTAT FBS-derived per-(country, group) supply
  (g/day). Used only to fill the ``animal_fat`` group on countries the
  source does not cover (a known GDD-IA coverage gap on ~37 countries;
  a no-op for the FBS source, which covers animal_fat natively), scaled
  by ``ANIMAL_FAT_SUPPLY_TO_INTAKE`` to approximate post-FLW intake.
  Other groups in the supply file are ignored here; they enter the
  pipeline through ``prepare_food_loss_waste``.

This script's job is just the source merge. GBD anchoring, the cereal
residual fix, and (for GDD-IA) the country-level kcal normalisation
happen in ``estimate_baseline_diet``.

No basis conversion is performed here: both sources emit model-basis
values by construction, and NHANES values are intake-based and in
model basis already (the FPED extraction handles that upstream).

Output:
- ``dietary_intake.csv``: merged per-(country, group) intake values.
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger("merge_dietary_sources")

# Supply-to-intake factor for FAOSTAT FBS animal-fat fallback. FAOSTAT
# reports household supply (kg/cap/yr); typical combined consumer
# loss+waste for animal fats is ~15%, matching the average waste fraction
# inherited from red_meat in prepare_food_loss_waste.
ANIMAL_FAT_SUPPLY_TO_INTAKE = 0.85


def _drop_overlap(
    target: pd.DataFrame, override: pd.DataFrame, label: str
) -> pd.DataFrame:
    """Remove rows in target that are superseded by entries in override."""
    if override.empty:
        return target
    override_keys = set(zip(override["country"], override["item"]))
    target_keys = set(zip(target["country"], target["item"]))
    dropped = override_keys & target_keys
    if not dropped:
        return target
    countries_dropped = sorted({c for c, _ in dropped})
    items_dropped = sorted({i for _, i in dropped})
    logger.info(
        "%s overrides %d (country, item) pairs (countries: %s; items: %s)",
        label,
        len(dropped),
        ", ".join(countries_dropped[:8]) + ("…" if len(countries_dropped) > 8 else ""),
        ", ".join(items_dropped),
    )
    mask = pd.Series(
        list(zip(target["country"], target["item"])), index=target.index
    ).isin(dropped)
    return target.loc[~mask].copy()


def _faostat_animal_fat_fallback(
    faostat_supply: pd.DataFrame,
    existing: pd.DataFrame,
) -> pd.DataFrame:
    """Build animal_fat intake rows for countries missing from ``existing``.

    GDD-IA's ``fat_ani`` is reported for ~146/175 modeled countries.
    The remaining ~30 (mostly European) need a baseline so that the
    ``consume:rendered-fat:{country}`` links get a non-trivial baseline
    diet target and the piecewise utility calibration covers every link.
    """
    rows = faostat_supply[faostat_supply["item"] == "animal_fat"].copy()
    if rows.empty:
        return rows
    have_animal_fat = set(
        existing.loc[existing["item"] == "animal_fat", "country"].unique()
    )
    rows = rows[~rows["country"].isin(have_animal_fat)].copy()
    if rows.empty:
        return rows
    rows["value"] = rows["value"] * ANIMAL_FAT_SUPPLY_TO_INTAKE
    return rows


def main() -> None:
    group_intake_path = snakemake.input["group_intake"]
    nhanes_path = snakemake.input["nhanes"]
    faostat_path = snakemake.input["faostat_supply"]
    diet_source = str(snakemake.params["diet_source"])
    output_path = snakemake.output["diet"]

    logger.info("Reading %s dietary intake from %s", diet_source, group_intake_path)
    group_intake = pd.read_csv(group_intake_path)
    logger.info(
        "%s: %d rows, %d countries, items %s",
        diet_source,
        len(group_intake),
        group_intake["country"].nunique(),
        sorted(group_intake["item"].unique()),
    )

    logger.info("Reading NHANES dietary intake from %s", nhanes_path)
    nhanes = pd.read_csv(nhanes_path)
    logger.info(
        "NHANES: %d rows, items %s", len(nhanes), sorted(nhanes["item"].unique())
    )

    logger.info("Reading FAOSTAT food group supply from %s", faostat_path)
    faostat_supply = pd.read_csv(faostat_path)

    # NHANES overrides the source for the (country, item) pairs it covers.
    group_intake = _drop_overlap(group_intake, nhanes, "NHANES")

    combined = pd.concat([group_intake, nhanes], ignore_index=True)

    # Supplement animal_fat from FAOSTAT FBS supply for countries that
    # neither the group-intake source nor NHANES covers.
    animal_fat_supplement = _faostat_animal_fat_fallback(faostat_supply, combined)
    if not animal_fat_supplement.empty:
        logger.info(
            "FAOSTAT animal_fat fallback: %d countries (%s)",
            len(animal_fat_supplement),
            ", ".join(sorted(animal_fat_supplement["country"].unique())[:8])
            + ("..." if animal_fat_supplement["country"].nunique() > 8 else ""),
        )
        combined = pd.concat([combined, animal_fat_supplement], ignore_index=True)

    merged = combined.sort_values(["country", "item", "age"]).reset_index(drop=True)

    merged.to_csv(output_path, index=False)
    logger.info(
        "Wrote %d rows to %s (%d countries, %d items)",
        len(merged),
        output_path,
        merged["country"].nunique(),
        merged["item"].nunique(),
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
