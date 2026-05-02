"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Compute fractions mapping GLEAM 3.0 aggregate feed categories to model feed
categories, using the authoritative feed items classification xlsx as the
source of truth for which individual feed codes belong to each GLEAM3 category.

Chain: xlsx (item → GLEAM3 category) → gleam_feed_mapping.csv (item → model
entity) → {rum,mono}_feed_mapping.csv (entity → model category).

For GLEAM3 categories that contain multiple model feed categories, fractions
are computed using FAOSTAT crop production volumes as weights (per country).
Non-crop items receive equal weight.

Output: CSV with columns (gleam3_category, animal_type, country,
model_feed_category, fraction, exogenous).

Fractions sum to 1.0 within each (gleam3_category, animal_type, country) group.
Constant fractions use country='_global'.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

OUTPUT_COLS = [
    "gleam3_category",
    "animal_type",
    "country",
    "model_feed_category",
    "fraction",
    "exogenous",
]


def _parse_feed_items_xlsx(xlsx_path: str) -> pd.DataFrame:
    """Parse feed items classification xlsx using 'Other systems' column.

    Returns DataFrame with columns: animal_type, raw_code, gleam3_category.
    """
    df = pd.read_excel(xlsx_path)
    result = pd.DataFrame(
        {
            "animal_type": df["AnimalGroup"].map(
                {"Ruminants": "ruminant", "Monogastrics": "monogastric"}
            ),
            "raw_code": df["Name"],
            "gleam3_category": df["Other systems"],
        }
    )
    result = result.dropna(subset=["gleam3_category"])
    return result


def _normalize_code(code: str, valid_codes: set[str]) -> str:
    """Normalize GLEAM feed code.

    Strips ``C`` prefix for commercial variants (when the base code exists
    in the reference set), and maps known spelling variants.
    """
    code = str(code).strip()
    if code == "SOY OIL":
        return "SOYOIL"
    if code == "LIME":
        return "LIMESTONE"
    # Strip C prefix for commercial variants when the base code exists
    if code.startswith("C") and len(code) > 1 and code[1:] in valid_codes:
        return code[1:]
    return code


def _build_item_table(
    xlsx_items: pd.DataFrame,
    gleam_mapping: pd.DataFrame,
    rum_mapping: pd.DataFrame,
    mono_mapping: pd.DataFrame,
) -> pd.DataFrame:
    """Build expanded table of (gleam3_category, animal_type, model_entity,
    entity_type, model_feed_category, exogenous).

    Each row is a unique combination of a GLEAM3 category, animal type, and
    model entity.  Items that have no model entity or whose model entity is
    absent from the feed-category mapping are marked exogenous.
    """
    valid_codes = set(gleam_mapping["gleam_code"].dropna())

    items = xlsx_items.copy()
    items["gleam_code"] = items["raw_code"].apply(
        lambda c: _normalize_code(c, valid_codes)
    )
    items = items[["animal_type", "gleam_code", "gleam3_category"]].drop_duplicates()

    # Split gleam_feed_mapping by compatible animal type
    rum_codes = gleam_mapping[gleam_mapping["animal_type"].isin(["ruminant", "both"])]
    mono_codes = gleam_mapping[
        gleam_mapping["animal_type"].isin(["monogastric", "both"])
    ]

    # Left-join xlsx items → gleam_feed_mapping (one xlsx code can expand to
    # multiple model entities)
    rum_items = items[items["animal_type"] == "ruminant"].merge(
        rum_codes[["gleam_code", "model_entity", "entity_type"]],
        on="gleam_code",
        how="left",
    )
    mono_items = items[items["animal_type"] == "monogastric"].merge(
        mono_codes[["gleam_code", "model_entity", "entity_type"]],
        on="gleam_code",
        how="left",
    )
    joined = pd.concat([rum_items, mono_items], ignore_index=True)

    # Mark exogenous: no model_entity (NaN or empty)
    joined["exogenous"] = joined["model_entity"].isna() | (joined["model_entity"] == "")

    # Map model_entity → list[(model_feed_category, share)] via {rum,mono}_feed_mapping.
    # An item may appear in multiple categories with shares summing to 1.0
    # (see workflow/scripts/categorize_feeds.py::apply_category_overrides).
    def _entity_to_cats(mapping: pd.DataFrame) -> dict[str, list[tuple[str, float]]]:
        out: dict[str, list[tuple[str, float]]] = {}
        share_col = "share" if "share" in mapping.columns else None
        for _, row in mapping.iterrows():
            entity = row["feed_item"]
            cat = row["category"]
            share = float(row[share_col]) if share_col else 1.0
            out.setdefault(entity, []).append((cat, share))
        return out

    rum_cat = _entity_to_cats(rum_mapping)
    mono_cat = _entity_to_cats(mono_mapping)

    # Fan out joined rows: one row per (entity, model_feed_category, share).
    fanned_rows = []
    for _, row in joined.iterrows():
        if row["exogenous"]:
            fanned_rows.append(
                {**row.to_dict(), "model_feed_category": None, "share": 1.0}
            )
            continue
        cat_map = rum_cat if row["animal_type"] == "ruminant" else mono_cat
        cats = cat_map.get(row["model_entity"], [])
        if not cats:
            # Entity present in gleam mapping but absent from category mapping → exogenous
            fanned_rows.append(
                {**row.to_dict(), "model_feed_category": None, "share": 1.0}
            )
            continue
        for cat, share in cats:
            new_row = row.to_dict()
            new_row["model_feed_category"] = f"{row['animal_type']}_{cat}"
            new_row["share"] = share
            fanned_rows.append(new_row)

    joined = pd.DataFrame(fanned_rows)
    joined.loc[joined["model_feed_category"].isna(), "exogenous"] = True

    # Deduplicate expanded rows on the fully-qualified key (preserves split rows)
    joined = joined.drop_duplicates(
        subset=["gleam3_category", "animal_type", "model_entity", "model_feed_category"]
    )

    return joined


def _compute_volume_weighted_fractions(
    entities: pd.DataFrame,
    crop_production: pd.DataFrame,
    countries: list[str],
    g3cat: str,
    animal: str,
) -> pd.DataFrame:
    """Compute per-country volume-weighted fractions for a group that spans
    multiple model feed categories."""
    # Identify which crop entities have FAOSTAT production data at all.
    # Crops absent from the data (e.g. silage-maize) receive the per-country
    # mean volume of tracked crops in this group, so scales remain comparable.
    crops_in_data = set(crop_production["crop"].unique())
    tracked_crop_mask = (entities["entity_type"] == "crop") & (
        entities["model_entity"].isin(crops_in_data)
    )
    tracked_crop_entities = set(entities.loc[tracked_crop_mask, "model_entity"])

    records = []
    for country in countries:
        country_prod = crop_production[crop_production["country"] == country]
        prod_lookup = dict(zip(country_prod["crop"], country_prod["production_tonnes"]))

        # Compute mean volume of tracked crops in this group for this country
        tracked_vols = [prod_lookup.get(e, 0.0) for e in tracked_crop_entities]
        mean_tracked = sum(tracked_vols) / len(tracked_vols) if tracked_vols else 1.0

        cat_volumes: dict[str, float] = {}
        for _, row in entities.iterrows():
            cat = row["model_feed_category"]
            entity = row["model_entity"]
            share = float(row["share"]) if "share" in row else 1.0
            if row["entity_type"] == "crop" and entity in crops_in_data:
                vol = prod_lookup.get(entity, 0.0)
            else:
                # Non-crop items and untracked crops use the mean volume
                # of tracked crops to keep scales comparable; falls back
                # to 1.0 when no tracked crops exist in the group.
                vol = mean_tracked
            # Multi-category items contribute volume * share to each
            # category (mass balance: shares sum to 1 per entity).
            cat_volumes[cat] = cat_volumes.get(cat, 0.0) + vol * share

        for cat, vol in cat_volumes.items():
            records.append(
                {
                    "country": country,
                    "model_feed_category": cat,
                    "volume": vol,
                }
            )

    vol_df = pd.DataFrame(records)

    # Normalize to fractions per country
    country_totals = vol_df.groupby("country")["volume"].transform("sum")
    vol_df["fraction"] = 0.0
    nonzero = country_totals > 0
    vol_df.loc[nonzero, "fraction"] = (
        vol_df.loc[nonzero, "volume"] / country_totals[nonzero]
    )

    # Global fallback for countries with zero total volume
    global_vols = vol_df.groupby("model_feed_category")["volume"].sum()
    global_sum = global_vols.sum()
    unique_cats = entities["model_feed_category"].unique()
    if global_sum > 0:
        global_frac = (global_vols / global_sum).to_dict()
    else:
        global_frac = {cat: 1.0 / len(unique_cats) for cat in unique_cats}

    zero_countries = vol_df.loc[~nonzero, "country"].unique()
    if len(zero_countries) > 0:
        mask = vol_df["country"].isin(zero_countries)
        vol_df.loc[mask, "fraction"] = vol_df.loc[mask, "model_feed_category"].map(
            global_frac
        )

    vol_df["gleam3_category"] = g3cat
    vol_df["animal_type"] = animal
    vol_df["exogenous"] = False
    return vol_df[OUTPUT_COLS]


def _compute_fractions(
    item_table: pd.DataFrame,
    crop_production: pd.DataFrame,
    countries: list[str],
) -> pd.DataFrame:
    """Compute fractions for every (gleam3_category, animal_type) group."""
    results = []

    for (g3cat, animal), group in item_table.groupby(
        ["gleam3_category", "animal_type"]
    ):
        endogenous = group[~group["exogenous"]]

        if endogenous.empty:
            # Fully exogenous → single row, 100% to a catch-all category
            catch_all = (
                f"{animal}_low_quality"
                if animal == "monogastric"
                else f"{animal}_roughage"
            )
            logger.info("%s/%s: all items exogenous → %s", g3cat, animal, catch_all)
            results.append(
                pd.DataFrame(
                    [
                        {
                            "gleam3_category": g3cat,
                            "animal_type": animal,
                            "country": "_global",
                            "model_feed_category": catch_all,
                            "fraction": 1.0,
                            "exogenous": True,
                        }
                    ]
                )
            )
            continue

        unique_cats = endogenous["model_feed_category"].unique()

        if len(unique_cats) == 1:
            # All endogenous items map to the same category → constant
            logger.info("%s/%s: constant → %s", g3cat, animal, unique_cats[0])
            results.append(
                pd.DataFrame(
                    [
                        {
                            "gleam3_category": g3cat,
                            "animal_type": animal,
                            "country": "_global",
                            "model_feed_category": unique_cats[0],
                            "fraction": 1.0,
                            "exogenous": False,
                        }
                    ]
                )
            )
            continue

        # Multiple model categories → volume-weighted per-country fractions
        logger.info(
            "%s/%s: volume-weighted across %d categories",
            g3cat,
            animal,
            len(unique_cats),
        )
        fracs = _compute_volume_weighted_fractions(
            endogenous, crop_production, countries, g3cat, animal
        )
        results.append(fracs)

    return pd.concat(results, ignore_index=True)


def main() -> None:
    xlsx_path = snakemake.input.feed_items_categories  # type: ignore[name-defined]
    gleam_mapping_path = snakemake.input.gleam_feed_mapping  # type: ignore[name-defined]
    crop_production_path = snakemake.input.faostat_crop_production  # type: ignore[name-defined]
    ruminant_mapping_path = snakemake.input.ruminant_feed_mapping  # type: ignore[name-defined]
    monogastric_mapping_path = snakemake.input.monogastric_feed_mapping  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    # Load inputs
    xlsx_items = _parse_feed_items_xlsx(xlsx_path)
    gleam_mapping = pd.read_csv(gleam_mapping_path, comment="#")
    crop_production = pd.read_csv(crop_production_path, comment="#")
    rum_mapping = pd.read_csv(ruminant_mapping_path, comment="#")
    mono_mapping = pd.read_csv(monogastric_mapping_path, comment="#")

    # Build the expanded item → model category table
    item_table = _build_item_table(xlsx_items, gleam_mapping, rum_mapping, mono_mapping)

    n_endo = (~item_table["exogenous"]).sum()
    n_exo = item_table["exogenous"].sum()
    logger.info(
        "Item table: %d endogenous entries, %d exogenous entries", n_endo, n_exo
    )

    # Compute fractions
    result = _compute_fractions(item_table, crop_production, countries)

    # Validation
    duplicate_mask = result.duplicated(
        subset=["gleam3_category", "animal_type", "country", "model_feed_category"],
        keep=False,
    )
    if duplicate_mask.any():
        dupes = result.loc[duplicate_mask].sort_values(
            ["gleam3_category", "animal_type", "country", "model_feed_category"]
        )
        raise ValueError(
            "Duplicate fraction rows detected for the same mapping key:\n"
            + dupes.head(20).to_string(index=False)
        )

    if (result["fraction"] < 0).any():
        bad = result[result["fraction"] < 0].head(20)
        raise ValueError("Negative fractions detected:\n" + bad.to_string(index=False))

    sums = result.groupby(
        ["gleam3_category", "animal_type", "country"], as_index=False
    )["fraction"].sum()
    bad_sums = sums[sums["fraction"].sub(1.0).abs() > 1e-6]
    if not bad_sums.empty:
        raise ValueError(
            "Fractions must sum to 1.0 for each "
            "(gleam3_category, animal_type, country). Bad groups:\n"
            + bad_sums.head(20).to_string(index=False)
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    logger.info("Wrote %d fraction records to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
