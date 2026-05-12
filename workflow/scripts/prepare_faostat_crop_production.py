"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    get_item_map,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def main() -> None:
    mapping_path = Path(snakemake.input.mapping)  # type: ignore[name-defined]
    qcl_csv = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    override_path = Path(snakemake.input.banana_plantain_override)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    production_year = int(snakemake.params.production_year)  # type: ignore[name-defined]

    mapping_df = pd.read_csv(mapping_path)
    if mapping_df.empty:
        raise RuntimeError("FAOSTAT item mapping table is empty")

    mapping_df["crop"] = mapping_df["crop"].astype(str).str.strip()
    mapping_df["faostat_item"] = mapping_df["faostat_item"].astype(str).str.strip()

    missing_item_mask = mapping_df["faostat_item"].eq("") | mapping_df[
        "faostat_item"
    ].str.lower().eq("nan")
    if missing_item_mask.any():
        skipped = mapping_df.loc[missing_item_mask, "crop"].tolist()
        logger.warning(
            "Skipping %d crops without FAOSTAT item mapping: %s",
            len(skipped),
            ", ".join(skipped[:5]) + ("..." if len(skipped) > 5 else ""),
        )
        mapping_df = mapping_df.loc[~missing_item_mask].copy()

    if mapping_df.empty:
        raise RuntimeError(
            "All FAOSTAT item mappings are empty after filtering missing entries"
        )

    # Load bulk data and extract metadata
    logger.info("Loading FAOSTAT QCL bulk data")
    bulk = load_bulk(qcl_csv)
    item_map = get_item_map(bulk)

    element_code = int(snakemake.params.qcl_element_code)  # type: ignore[name-defined]
    logger.info("Using FAOSTAT element 'Production' (code %s)", element_code)

    missing_items = sorted(
        {item for item in mapping_df["faostat_item"].unique() if item not in item_map}
    )
    if missing_items:
        raise RuntimeError(
            "FAOSTAT item(s) missing from parameter table: " + ", ".join(missing_items)
        )

    mapping_df["item_code"] = mapping_df["faostat_item"].map(item_map)

    # Build exploded mapping: one row per (item_code, crop) with a share that
    # determines how the FAOSTAT item's production is split among modelled
    # crops when more than one maps to the same item.
    #
    # Default policy is an equal 1/N split among the crops mapped to a given
    # item. The mapping CSV may carry an optional ``share`` column to override
    # this — useful when modelled crops share a FAOSTAT item but real-world
    # production is heavily skewed (e.g. FAOSTAT "Rice, paddy" is ~95 %
    # wetland-rice and ~5 % dryland/upland). The override is all-or-nothing
    # per FAOSTAT item: every row for an item must have an explicit share if
    # any does, and the shares must sum to 1.
    if "share" in mapping_df.columns:
        share_input = pd.to_numeric(mapping_df["share"], errors="coerce")
        crop_mapping = mapping_df[["item_code", "crop"]].assign(share=share_input)
    else:
        crop_mapping = mapping_df[["item_code", "crop"]].assign(share=float("nan"))

    has_override = crop_mapping.groupby("item_code")["share"].transform(
        lambda s: s.notna().any()
    )
    mixed = (
        has_override
        & crop_mapping["share"].isna()
        & (crop_mapping.groupby("item_code")["crop"].transform("count") > 1)
    )
    if mixed.any():
        bad_items = sorted(crop_mapping.loc[mixed, "item_code"].unique().tolist())
        raise ValueError(
            "FAOSTAT crop item map specifies share for only some crops of "
            f"item(s) {bad_items}; either set share on every row of an item "
            "or leave them all blank."
        )

    # Default 1/N for items with no override; explicit shares retained as-is.
    crop_counts = crop_mapping.groupby("item_code")["crop"].transform("count")
    default_share = 1.0 / crop_counts
    crop_mapping["share"] = crop_mapping["share"].where(has_override, default_share)

    # Sanity check: shares should sum to ~1.0 per FAOSTAT item.
    share_sums = crop_mapping.groupby("item_code")["share"].sum()
    bad_sums = share_sums[(share_sums < 0.99) | (share_sums > 1.01)]
    if not bad_sums.empty:
        raise ValueError(
            "FAOSTAT item share overrides do not sum to 1.0 per item: "
            + ", ".join(f"{int(i)}={s:.3f}" for i, s in bad_sums.items())
        )

    # Add ISO3 column from M49 codes
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Filter bulk data
    df = filter_bulk(
        bulk,
        element_codes=[element_code],
        item_codes=sorted(crop_mapping["item_code"].dropna().astype(int).unique()),
        years=[production_year],
        iso3_codes=countries,
    )

    logger.info(
        "Filtered FAOSTAT production data for year %s (%d items, %d countries): %d rows",
        production_year,
        crop_mapping["item_code"].nunique(),
        len(countries),
        len(df),
    )

    if df.empty:
        raise RuntimeError(
            "FAOSTAT returned no production data for the requested selection"
        )

    df = df.dropna(subset=["Value"])
    if df.empty:
        raise RuntimeError("FAOSTAT production data contains no numeric values")

    df["country"] = df["iso3"].astype(str).str.upper()

    if df.empty:
        raise RuntimeError(
            "FAOSTAT returned no records for the requested ISO3 countries"
        )

    # Merge with crop mapping and compute per-crop production shares
    df = df.assign(item_code=df["Item Code"])
    merged = df.merge(crop_mapping, on="item_code", how="inner")
    merged = merged[np.isfinite(merged["Value"])]
    merged["production_tonnes"] = merged["Value"] * merged["share"]
    merged["year"] = pd.to_numeric(merged["Year"], errors="coerce").astype(int)

    if merged.empty:
        raise RuntimeError(
            "No FAOSTAT production records matched the configured crop list"
        )

    result = (
        merged.groupby(["country", "crop", "year"], as_index=False)["production_tonnes"]
        .sum()
        .sort_values(["country", "crop"])
    )

    # Apply banana/plantain override derived from FAOSTAT FBS, which has a
    # more reliable per-country split than QCL (where many large plantain
    # producers misclassify all output as "Bananas").
    override_df = pd.read_csv(override_path)
    if not override_df.empty:
        override_df["country"] = override_df["country"].astype(str).str.upper()
        override_df["crop"] = override_df["crop"].astype(str).str.strip()
        override_keys = set(
            zip(override_df["country"], override_df["crop"], strict=False)
        )
        keep_mask = [
            (c, cr) not in override_keys
            for c, cr in zip(result["country"], result["crop"], strict=False)
        ]
        result = pd.concat(
            [
                result.loc[keep_mask],
                override_df[["country", "crop", "year", "production_tonnes"]],
            ],
            ignore_index=True,
        ).sort_values(["country", "crop"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
