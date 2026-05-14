#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge GDD-IA dietary intake with NHANES (USA override).

Inputs:
- ``gdd_ia``: GDD-IA-derived per-(country, group) intake in model basis
  (g/day), already kcal-derived and proxy-filled.
- ``nhanes``: NHANES (FPED-derived) USA intake, per food group.
  NHANES values are intake-based and already in model basis; they
  override the GDD-IA values for the country/items NHANES covers.
- ``faostat_supply``: FAOSTAT FBS-derived per-(country, group) supply
  (g/day). Used only to fill the ``animal_fat`` group where GDD-IA's
  ``fat_ani`` is not reported (a known coverage gap on ~37 countries),
  scaled by ``ANIMAL_FAT_SUPPLY_TO_INTAKE`` to approximate post-FLW
  intake. Other groups in the supply file are ignored here; they enter
  the pipeline through ``prepare_food_loss_waste``.

This script's job is just the source merge. The country-level
kcal-normalisation step (against GDD-IA's `all-fg` minus out-of-scope
categories) happens in ``estimate_baseline_diet``, after GBD anchoring
and the cereal residual fix.

No basis conversion is performed here. GDD-IA mass is derived via
``kcal_ia / kcal_per_g_model_basis``, so the output is already in
model basis by construction. NHANES values are intake-based and in
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
    gdd_ia_path = snakemake.input["gdd_ia"]
    nhanes_path = snakemake.input["nhanes"]
    faostat_path = snakemake.input["faostat_supply"]
    output_path = snakemake.output["diet"]

    logger.info("Reading GDD-IA dietary intake from %s", gdd_ia_path)
    gdd_ia = pd.read_csv(gdd_ia_path)
    logger.info(
        "GDD-IA: %d rows, %d countries, items %s",
        len(gdd_ia),
        gdd_ia["country"].nunique(),
        sorted(gdd_ia["item"].unique()),
    )

    logger.info("Reading NHANES dietary intake from %s", nhanes_path)
    nhanes = pd.read_csv(nhanes_path)
    logger.info(
        "NHANES: %d rows, items %s", len(nhanes), sorted(nhanes["item"].unique())
    )

    logger.info("Reading FAOSTAT food group supply from %s", faostat_path)
    faostat_supply = pd.read_csv(faostat_path)

    # NHANES overrides GDD-IA for the (country, item) pairs it covers.
    gdd_ia = _drop_overlap(gdd_ia, nhanes, "NHANES")

    combined = pd.concat([gdd_ia, nhanes], ignore_index=True)

    # Supplement animal_fat from FAOSTAT FBS supply for countries that
    # neither GDD-IA nor NHANES covers.
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
