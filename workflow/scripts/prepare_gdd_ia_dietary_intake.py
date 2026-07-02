#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process Global Dietary Database — Integrated Assessment (GDD-IA) dataset.

GDD-IA ships two parallel CSVs (one in grams/day, one in kcal/day) at
country level. Most groups (vegetables, fruits, nuts/seeds, oil, sugar,
legumes, poultry, eggs) ship in mass that's already close to model
basis; we pass IA's reported grams through. Cereals keep GDD's total
energy but not its whole/processed split: GDD counts decorticated
coarse grains (millet, sorghum) as processed, colliding with the model
taxonomy where they are whole_grains foods, so the total cereal kcal is
re-split by the country's FBS cereal composition (via
``diet.fbs_intake.build_cereal_energy_shares``) and masses are derived
at model-basis group densities. Two further groups need basis
adjustment:

- ``red_meat``: IA implied kcal/g ≈ 2.4 (cooked); model uses raw retail
  (~2.15 kcal/g). Apply cooked-to-raw factor 1/0.7 ≈ 1.43.
- ``dairy``: IA reports a mix of dairy products (fluid milk, yoghurt,
  cheese in ``prim:milk``; butter and cream as separate ``prim``
  categories). The model represents dairy as a single bus in cow-milk-
  equivalents. We fold IA's milk + butter + cream kcal into one pool
  and derive mass at cow-milk density (0.607 kcal/g) so the result is
  strict milk-equivalent. This will tend to overshoot the current
  pipeline's lower dairy demand (which underestimates dairy intake by
  using FAOSTAT FBS supply minus waste); the right fix is on the
  production side (FLW factors, dairy → butter conversion losses), not
  to drop dairy components from intake.

Out-of-scope categories (kcal subtracted from country target):

- ``alcohol``, ``fish_*``, ``shellfish``, ``spices``, ``other``

``fruits_starch`` (plantain) maps to the ``starchy_vegetable`` food
group (model crop ``plantain`` added).

The output mirrors the schema of the legacy ``gdd_dietary_intake.csv``
(unit, item, country, age, year, value), plus a companion
``gdd_ia_kcal_target.csv`` that carries the per-country ``all-fg``
total kcal, the out-of-scope subtotal, and the FBS-aligned cereal kcal
split (whole_grains, grain) — all consumed by
``estimate_baseline_diet`` for the anchor-aware kcal normalisation
step.

Output rows are emitted at age = "All ages" only. GDD-IA stratifies
age 0-9/10-19/20-39/40-64/65+ which doesn't match the existing
pipeline buckets; baseline_age is "All ages" by default.

Input:
    - GDD-IA grams CSV (data/manually_downloaded/GDD-IA-intake_grams_{year}.csv)
    - GDD-IA kcal CSV  (data/manually_downloaded/GDD-IA-intake_kcals_{year}.csv)
    - nutrition CSV     (for global per-group density in model basis)
    - food_groups CSV   (food → group)
    - FBS items kcal CSV (per-(country, item) energy supply, for the
      cereal composition)
    - faostat_food_item_map CSV (food → FBS item code)

Output:
    - gdd_ia_dietary_intake.csv: unit,item,country,age,year,value
    - gdd_ia_kcal_target.csv:    country,kcal_all_fg,kcal_oos,kcal_target_modelled
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.diet.fbs_intake import build_cereal_energy_shares
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger("prepare_gdd_ia_dietary_intake")


# --------------------------------------------------------------------------
# Mapping: GDD-IA `prim` non-overlapping primary categories → GLADE
# food groups. Cereal categories are intentionally absent because we use
# the `prcd:whole_grains` / `prcd:prc_grains` split for the cereal
# allocation. butter/cream are handled specially (see below).
# fat_ani (rendered animal fat) maps to the animal_fat food group via
# rendered-fat, which is added as an animal_production co-product
# (config: animal_products.co_products.rendered-fat).
PRIM_TO_GLADE_GROUP: dict[str, str] = {
    "roots": "starchy_vegetable",
    "vegetables": "vegetables",
    "fruits_trop": "fruits",
    "fruits_temp": "fruits",
    "fruits_starch": "starchy_vegetable",  # plantain — model crop added
    "legumes": "legumes",
    "soybeans": "legumes",
    "nuts": "nuts_seeds",
    "seeds": "nuts_seeds",
    "oil_veg": "oil",
    "oil_palm": "oil",
    "sugar": "sugar",
    "poultry": "poultry",
    "beef": "red_meat",
    "lamb": "red_meat",
    "pork": "red_meat",
    "othr_meat": "red_meat",
    "milk": "dairy",
    "eggs": "eggs",
    "stimulants": "stimulants",
    "fat_ani": "animal_fat",  # rendered animal fat (lard/tallow)
}

# `prcd` rows providing GDD-IA's total cereal energy. Only the total is
# kept: GDD's own whole/processed split counts decorticated coarse
# grains (millet, sorghum) as processed, colliding with the model's food
# taxonomy where they are whole_grains foods. The total is re-split by
# the country's FBS cereal composition (see _align_cereals_to_fbs).
PRCD_TO_GLADE_GROUP: dict[str, str] = {
    "whole_grains": "whole_grains",
    "prc_grains": "grain",
}

CEREAL_GROUPS = ("whole_grains", "grain")

# Categories whose kcal is added to the `dairy` group at cow-milk
# density. butter and cream are reported as separate prim categories
# in GDD-IA (not inside `prim:milk`, which already includes fluid
# milk + yoghurt + cheese + condensed/evaporated).
PRIM_DAIRY_AUXILIARY = ["butter", "cream"]

# Cow milk density: nutrition.csv `dairy` is 60.71 kcal/100g.
COW_MILK_KCAL_PER_G = 0.6071

# Categories subtracted from the country-level kcal target (their energy
# is consumed but not represented in the model's foods).
OUT_OF_SCOPE_KCAL: list[str] = [
    "alcohol",
    "fish_freshw",
    "fish_pelag",
    "fish_demrs",
    "fish_other",
    "shellfish",
    "spices",
    "other",
]

# Country proxies for the 12 GDD-IA-missing required countries.
# AFG/ERI/SOM use already-validated regional analogues from the legacy
# pipeline; the new entries (BRN/BTN/GNQ/PSE/SSD/TWN) are chosen by
# dietary similarity and geographic proximity.
COUNTRY_PROXIES: dict[str, str] = {
    "AFG": "IRN",  # diet similar (Persian/Pashtun)
    "ASM": "WSM",  # American Samoa → Samoa
    "BRN": "MYS",  # Brunei → Malaysia
    "BTN": "NPL",  # Bhutan → Nepal
    "ERI": "ETH",  # Eritrea → Ethiopia (existing convention)
    "GNQ": "CMR",  # Equatorial Guinea → Cameroon
    "GUF": "FRA",  # French Guiana → France (existing convention)
    "PRI": "USA",  # Puerto Rico → USA (existing convention)
    "PSE": "JOR",  # Palestine → Jordan
    "SOM": "ETH",  # Somalia → Ethiopia (existing convention)
    "SSD": "SDN",  # South Sudan → Sudan
    "TWN": "CHN",  # Taiwan → China
}

# Unit strings (kept consistent with gdd_dietary_intake.csv conventions
# so downstream consumers see the same labels).
UNIT_BY_GROUP = {
    "dairy": "g/day (milk equiv)",
    "sugar": "g/day (refined sugar eq)",
    "oil": "g/day (fresh wt)",
    "grain": "g/day (fresh wt)",
    "whole_grains": "g/day (fresh wt)",
    "legumes": "g/day (fresh wt)",
    "fruits": "g/day (fresh wt)",
    "vegetables": "g/day (fresh wt)",
    "nuts_seeds": "g/day (fresh wt)",
    "starchy_vegetable": "g/day (fresh wt)",
    "red_meat": "g/day (fresh wt)",
    "poultry": "g/day (fresh wt)",
    "eggs": "g/day (fresh wt)",
    "stimulants": "g/day (fresh wt)",
}

# UN/World Bank region aggregates that appear in the IA region column
# and should be excluded (we only want country rows).
REGION_AGGREGATES = {
    "EAS",
    "ECS",
    "HIC",
    "LCN",
    "LIC",
    "LMC",
    "MEA",
    "NAC",
    "SAS",
    "SSF",
    "UMC",
    "WLD",
}


def _filter_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only baseline strata: all-ages, both sexes, all residences, mean stat."""
    mask = (
        (df["age"] == "all-a")
        & (df["sex"] == "BTH")
        & (df["residence"] == "all-u")
        & (df["stats"] == "mean")
    )
    return df.loc[mask].copy()


def _build_kcal_density(
    food_groups_df: pd.DataFrame,
    nutrition_df: pd.DataFrame,
) -> dict[str, float]:
    """Global per-group kcal/g from nutrition.csv averaged over foods in
    each group. Used only for the cooked-to-raw inflation step (we keep
    kcal consistency for meat: after x 1.43, kcal_per_g_eff is divided
    correspondingly so total kcal is preserved).
    """
    kcal_per_100g = nutrition_df[nutrition_df["nutrient"] == "cal"].set_index("food")[
        "value"
    ]
    fg_kcal = food_groups_df.merge(
        kcal_per_100g.rename("kcal_per_100g").reset_index(),
        on="food",
        how="left",
    )
    return (fg_kcal.groupby("group")["kcal_per_100g"].mean() / 100.0).to_dict()


def _aggregate(
    df: pd.DataFrame,
    prim_map: dict[str, str],
    prcd_map: dict[str, str],
    value_name: str,
) -> pd.DataFrame:
    """Sum GDD-IA rows into GLADE groups."""
    prim = df[df["type"] == "prim"].copy()
    prim["group"] = prim["food_group"].map(prim_map)
    prim_grp = (
        prim.dropna(subset=["group"])
        .groupby(["region", "group"], as_index=False)["value"]
        .sum()
    )
    prcd = df[(df["type"] == "prcd") & df["food_group"].isin(prcd_map)].copy()
    prcd["group"] = prcd["food_group"].map(prcd_map)
    prcd_grp = prcd.groupby(["region", "group"], as_index=False)["value"].sum()
    out = pd.concat([prim_grp, prcd_grp], ignore_index=True)
    return out.rename(columns={"region": "country", "value": value_name})


def _derive_mass(
    df: pd.DataFrame,
    cooked_to_raw: dict[str, float],
) -> pd.DataFrame:
    """Mass values.

    Default: IA's reported grams (already in approximately model basis
    for most groups).

    Overrides:
      - red_meat: x cooked_to_raw[red_meat] (cooked → raw retail).
      - dairy: kcal-based at cow-milk density (mass interpreted as
        strict milk-equivalent). Dairy mass = dairy_kcal_total /
        COW_MILK_KCAL_PER_G. dairy_kcal_total = prim:milk kcal +
        prim:butter kcal + prim:cream kcal (latter two folded in via
        ``_fold_butter_cream_into_dairy``).
    """
    out = df.copy()
    # Default: use reported grams.
    out["value"] = out["g_as_reported"]
    # Dairy: kcal-derived at cow-milk density.
    dairy_mask = out["group"] == "dairy"
    out.loc[dairy_mask, "value"] = out.loc[dairy_mask, "kcal"] / COW_MILK_KCAL_PER_G
    # Meat: cooked-to-raw inflation.
    for group, factor in cooked_to_raw.items():
        m = out["group"] == group
        out.loc[m, "value"] = out.loc[m, "value"] * factor
    return out


def split_cereal_energy(
    e_total: float, f_whole: float, k_whole: float, k_grain: float
) -> tuple[float, float, float, float]:
    """Split a country's total cereal energy by the FBS whole-grain fraction.

    Returns ``(kcal_whole, kcal_grain, g_whole, g_grain)`` where masses
    are derived at the model-basis group densities (kcal/g). Energy is
    conserved: ``kcal_whole + kcal_grain == e_total``.
    """
    kcal_whole = f_whole * e_total
    kcal_grain = e_total - kcal_whole
    return kcal_whole, kcal_grain, kcal_whole / k_whole, kcal_grain / k_grain


def _align_cereals_to_fbs(
    df: pd.DataFrame,
    fbs_cereal: pd.DataFrame,
    density: dict[str, float],
    required_countries: set[str],
) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    """Re-split each country's GDD cereal energy by its FBS composition.

    GDD-IA's total cereal energy (whole_grains + grain kcal) is kept;
    its whole/processed split is replaced by the country's FBS cereal
    composition (``fbs_cereal`` from
    :func:`workflow.scripts.diet.fbs_intake.build_cereal_energy_shares`).
    Masses are re-derived from the aligned kcal at model-basis group
    densities. Countries absent from ``fbs_cereal`` (GDD rows outside
    the configured country set) keep GDD's own split.

    Returns the updated intake DataFrame and a per-country mapping
    ``country -> (kcal_whole_grains, kcal_grain)`` of the aligned split,
    used to override the kcal-target columns.
    """
    fbs = fbs_cereal.set_index("country")
    cereal_mask = df["group"].isin(CEREAL_GROUPS)
    e_total = df.loc[cereal_mask].groupby("country")["kcal"].sum()

    k_whole = density["whole_grains"]
    k_grain = density["grain"]
    aligned: dict[str, tuple[float, float]] = {}
    new_rows = []
    for country, e in e_total.items():
        if country not in fbs.index:
            continue
        fbs_total = float(fbs.at[country, "kcal_whole_grains"]) + float(
            fbs.at[country, "kcal_grain"]
        )
        if fbs_total <= 0.0:
            if country in required_countries:
                raise ValueError(
                    f"FBS reports no cereal energy supply for {country}; "
                    "cannot derive the whole-grain fraction for the GDD-IA "
                    "cereal alignment."
                )
            continue
        f_whole = float(fbs.at[country, "kcal_whole_grains"]) / fbs_total
        kcal_whole, kcal_grain, g_whole, g_grain = split_cereal_energy(
            float(e), f_whole, k_whole, k_grain
        )
        aligned[country] = (kcal_whole, kcal_grain)
        new_rows.append(
            {
                "country": country,
                "group": "whole_grains",
                "kcal": kcal_whole,
                "value": g_whole,
            }
        )
        new_rows.append(
            {"country": country, "group": "grain", "kcal": kcal_grain, "value": g_grain}
        )

    drop_mask = cereal_mask & df["country"].isin(aligned)
    out = pd.concat([df.loc[~drop_mask], pd.DataFrame(new_rows)], ignore_index=True)
    logger.info(
        "Aligned cereal whole/refined split to FBS composition for %d countries",
        len(aligned),
    )
    return out, aligned


def _fold_butter_cream_into_dairy(
    df: pd.DataFrame, kcal_ia_full: pd.DataFrame
) -> pd.DataFrame:
    """Add butter+cream kcal to the dairy kcal pool per country.

    ``prim:milk`` already includes fluid milk, yoghurt, cheese,
    condensed/evaporated and ice cream; butter and cream are reported
    separately. After this step, the dairy row's ``kcal`` column carries
    the full dairy energy budget, which ``_derive_mass`` translates to
    milk-equivalent mass via cow-milk density.
    """
    aux = (
        kcal_ia_full[
            (kcal_ia_full["type"] == "prim")
            & kcal_ia_full["food_group"].isin(PRIM_DAIRY_AUXILIARY)
        ]
        .groupby("region", as_index=False)["value"]
        .sum()
        .rename(columns={"region": "country", "value": "kcal_aux"})
    )
    if aux.empty:
        return df
    out = df.copy()
    dairy_mask = out["group"] == "dairy"
    aux_lookup = aux.set_index("country")["kcal_aux"].to_dict()
    out.loc[dairy_mask, "kcal"] = out.loc[dairy_mask].apply(
        lambda row: row["kcal"] + aux_lookup.get(row["country"], 0.0),
        axis=1,
    )
    return out


def _apply_proxies(
    df: pd.DataFrame, required_countries: set[str], proxies: dict[str, str]
) -> pd.DataFrame:
    """Fill missing required countries by duplicating rows from a proxy."""
    have = set(df["country"].unique())
    missing = required_countries - have
    if not missing:
        return df
    additions = []
    used = []
    skipped = []
    for c in sorted(missing):
        proxy = proxies.get(c)
        if proxy is None or proxy not in have:
            skipped.append(c)
            continue
        copy = df[df["country"] == proxy].copy()
        copy["country"] = c
        additions.append(copy)
        used.append(f"{c}←{proxy}")
    if additions:
        df = pd.concat([df, *additions], ignore_index=True)
        logger.info("Filled %d countries via proxy: %s", len(used), ", ".join(used))
    if skipped:
        logger.warning(
            "%d required countries still missing after proxy fill: %s",
            len(skipped),
            ", ".join(skipped),
        )
    return df


def main() -> None:
    grams_path = Path(snakemake.input["grams"])
    kcal_path = Path(snakemake.input["kcal"])
    food_groups_path = Path(snakemake.input["food_groups"])
    nutrition_path = Path(snakemake.input["nutrition"])
    fbs_items_kcal_path = Path(snakemake.input["fbs_items_kcal"])
    food_item_map_path = Path(snakemake.input["food_item_map"])

    out_diet_path = Path(snakemake.output["diet"])
    out_kcal_path = Path(snakemake.output["kcal_target"])

    required_countries = set(snakemake.params["countries"])
    food_groups_included = set(snakemake.params["food_groups"])
    reference_year = int(snakemake.params["reference_year"])
    cooked_to_raw = {
        str(k): float(v) for k, v in dict(snakemake.params["cooked_to_raw"]).items()
    }
    byproducts = list(snakemake.params["byproducts"])
    whole_grain_shares = {
        str(k): float(v)
        for k, v in dict(snakemake.params["whole_grain_shares"]).items()
    }
    extra_proxies = dict(snakemake.params.get("country_proxies", {}) or {})
    proxies = {**COUNTRY_PROXIES, **extra_proxies}

    logger.info("Reading GDD-IA grams from %s", grams_path)
    grams = _filter_baseline(pd.read_csv(grams_path))
    logger.info("Reading GDD-IA kcal from %s", kcal_path)
    kcal = _filter_baseline(pd.read_csv(kcal_path))

    # --- Aggregate to GLADE groups ---
    g_df = _aggregate(grams, PRIM_TO_GLADE_GROUP, PRCD_TO_GLADE_GROUP, "g_as_reported")
    k_df = _aggregate(kcal, PRIM_TO_GLADE_GROUP, PRCD_TO_GLADE_GROUP, "kcal")
    df = g_df.merge(k_df, on=["country", "group"], how="outer")

    # Drop non-country region aggregates.
    df = df[~df["country"].isin(REGION_AGGREGATES)].copy()

    # --- Fold butter + cream kcal into the dairy kcal pool ---
    df = _fold_butter_cream_into_dairy(df, kcal)

    # --- Restrict groups to those configured in food_groups.included ---
    df = df[df["group"].isin(food_groups_included)].copy()

    # --- Per-group density (used for the aligned cereal mass derivation) ---
    food_groups_df = pd.read_csv(food_groups_path)
    nutrition_df = pd.read_csv(nutrition_path)
    density = _build_kcal_density(food_groups_df, nutrition_df)
    logger.info("Per-group nutrition.csv density (kcal/g): %s", density)

    # --- Mass = IA's reported g/d, with cooked-to-raw inflation for meat ---
    df = _derive_mass(df, cooked_to_raw)

    # --- Re-split cereal energy by the FBS whole/refined composition ---
    fbs_cereal = build_cereal_energy_shares(
        pd.read_csv(fbs_items_kcal_path),
        pd.read_csv(food_item_map_path, comment="#"),
        food_groups_df,
        nutrition_df,
        sorted(required_countries),
        byproducts,
        whole_grain_shares,
    )
    df, aligned_cereal = _align_cereals_to_fbs(
        df, fbs_cereal, density, required_countries
    )

    # --- Apply country proxies ---
    df = _apply_proxies(df, required_countries, proxies)

    # --- Build country-level kcal targets ---
    # all-fg total (one row per country, prim/all-fg category):
    all_fg = kcal[(kcal["type"] == "prim") & (kcal["food_group"] == "all-fg")][
        ["region", "value"]
    ].rename(columns={"region": "country", "value": "kcal_all_fg"})
    # OOS subtotal:
    oos = (
        kcal[(kcal["type"] == "prim") & kcal["food_group"].isin(OUT_OF_SCOPE_KCAL)]
        .groupby("region", as_index=False)["value"]
        .sum()
        .rename(columns={"region": "country", "value": "kcal_oos"})
    )
    targets = all_fg.merge(oos, on="country", how="left")
    targets["kcal_oos"] = targets["kcal_oos"].fillna(0.0)
    targets["kcal_target_modelled"] = targets["kcal_all_fg"] - targets["kcal_oos"]
    targets = targets[~targets["country"].isin(REGION_AGGREGATES)]

    # Carry per-country whole_grain and refined-grain kcal so the cereal
    # residual fix in estimate_baseline_diet sees the same cereal-kcal
    # accounting as the intake rows. IA's own split is the fallback for
    # countries outside the FBS alignment; aligned countries are
    # overridden below.
    prcd_whole = kcal[
        (kcal["type"] == "prcd") & (kcal["food_group"] == "whole_grains")
    ][["region", "value"]].rename(
        columns={"region": "country", "value": "kcal_whole_grains"}
    )
    prcd_grain = kcal[(kcal["type"] == "prcd") & (kcal["food_group"] == "prc_grains")][
        ["region", "value"]
    ].rename(columns={"region": "country", "value": "kcal_grain"})
    targets = targets.merge(prcd_whole, on="country", how="left")
    targets = targets.merge(prcd_grain, on="country", how="left")
    targets["kcal_whole_grains"] = targets["kcal_whole_grains"].fillna(0.0)
    targets["kcal_grain"] = targets["kcal_grain"].fillna(0.0)

    # Override with the FBS-aligned cereal split (total energy unchanged).
    targets["kcal_whole_grains"] = (
        targets["country"]
        .map({c: v[0] for c, v in aligned_cereal.items()})
        .fillna(targets["kcal_whole_grains"])
    )
    targets["kcal_grain"] = (
        targets["country"]
        .map({c: v[1] for c, v in aligned_cereal.items()})
        .fillna(targets["kcal_grain"])
    )

    # Apply proxy filling to kcal targets too.
    have = set(targets["country"].unique())
    missing = required_countries - have
    if missing:
        additions = []
        for c in sorted(missing):
            proxy = proxies.get(c)
            if proxy is None or proxy not in have:
                continue
            copy = targets[targets["country"] == proxy].copy()
            copy["country"] = c
            additions.append(copy)
        if additions:
            targets = pd.concat([targets, *additions], ignore_index=True)

    # --- Emit dietary intake CSV ---
    out_diet_path.parent.mkdir(parents=True, exist_ok=True)
    diet_out = df.rename(columns={"group": "item"})[["country", "item", "value"]].copy()
    diet_out["unit"] = diet_out["item"].map(UNIT_BY_GROUP)
    diet_out["age"] = "All ages"
    diet_out["year"] = reference_year
    diet_out = diet_out[["unit", "item", "country", "age", "year", "value"]]
    diet_out = diet_out.sort_values(["country", "item"]).reset_index(drop=True)
    diet_out.to_csv(out_diet_path, index=False)
    logger.info(
        "Wrote %d rows (%d countries, %d groups) to %s",
        len(diet_out),
        diet_out["country"].nunique(),
        diet_out["item"].nunique(),
        out_diet_path,
    )

    # --- Emit kcal target CSV ---
    out_kcal_path.parent.mkdir(parents=True, exist_ok=True)
    targets = (
        targets[
            [
                "country",
                "kcal_all_fg",
                "kcal_oos",
                "kcal_target_modelled",
                "kcal_whole_grains",
                "kcal_grain",
            ]
        ]
        .sort_values("country")
        .reset_index(drop=True)
    )
    targets.to_csv(out_kcal_path, index=False)
    logger.info(
        "Wrote kcal targets for %d countries to %s",
        len(targets),
        out_kcal_path,
    )

    # --- Final coverage check ---
    have = set(diet_out["country"].unique())
    missing = required_countries - have
    if missing:
        raise ValueError(
            f"[prepare_gdd_ia_dietary_intake] {len(missing)} required countries "
            f"still missing after proxy fill: {sorted(missing)}. Extend "
            f"country_proxies in the script or config."
        )

    # Log per-group global means for a sanity check.
    means = diet_out.groupby("item")["value"].mean().round(2)
    logger.info("Per-group mean g/d across countries:")
    for g, v in means.sort_index().items():
        logger.info("  %s: %.2f", g, v)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
