# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare per-(crop, country) crop production costs from FAOSTAT price and yield data.

Cost model:
    cost_usd_per_ha = producer_price_usd_per_tonne * yield_t_per_ha * non_endogenous_cost_share

Both price and yield come from FAOSTAT bulk downloads (PP and QCL domains).
Prices are CPI-deflated to the configured base year before averaging.

Inputs
------
- PP.parquet : FAOSTAT Prices bulk (element 5531 = Producer Price USD/tonne)
- QCL.parquet : FAOSTAT Production bulk (element 5419 = Yield hg/ha)
- faostat_crop_item_map.csv : model crop → FAOSTAT item mapping
- M49-codes.csv : M49 → ISO3 mapping
- cpi_annual.csv : US CPI-U annual averages for deflation
- faostat_cost_proxies.yaml : proxy mappings for unmapped crops

Output
------
- faostat_crop_costs.csv : columns (crop, country, cost_usd_{base_year}_per_ha, n_years, is_fallback)
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    get_item_map,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_crop_mapping(mapping_path: str) -> pd.DataFrame:
    """Load and clean the FAOSTAT crop item mapping."""
    df = pd.read_csv(mapping_path)
    df["crop"] = df["crop"].astype(str).str.strip()
    df["faostat_item"] = df["faostat_item"].astype(str).str.strip()
    # Drop crops without FAOSTAT mapping (e.g. alfalfa, biomass-sorghum)
    valid = ~(df["faostat_item"].eq("") | df["faostat_item"].str.lower().eq("nan"))
    return df[valid].copy()


def _deflate_to_base_year(
    df: pd.DataFrame, cpi_df: pd.DataFrame, base_year: int
) -> pd.DataFrame:
    """CPI-deflate a 'value' column using year-indexed CPI data.

    Returns the DataFrame with 'value' adjusted to base_year dollars.
    """
    cpi_map = cpi_df.set_index("year")["cpi_u"]
    base_cpi = cpi_map.get(base_year)
    if base_cpi is None or not np.isfinite(base_cpi):
        raise ValueError(f"No CPI data for base year {base_year}")
    df = df.copy()
    df["cpi"] = df["year"].map(cpi_map)
    valid = df["cpi"].notna() & (df["cpi"] > 0)
    df.loc[valid, "value"] = df.loc[valid, "value"] * (base_cpi / df.loc[valid, "cpi"])
    df.loc[~valid, "value"] = np.nan
    return df


def main() -> None:
    pp_path = snakemake.input.pp_parquet
    qcl_path = snakemake.input.qcl_parquet
    mapping_path = snakemake.input.mapping
    m49_path = snakemake.input.m49_codes
    cpi_path = snakemake.input.cpi
    proxies_path = snakemake.input.proxies
    output_path = Path(snakemake.output[0])

    countries = [str(c).upper() for c in snakemake.params.countries]
    crops = list(snakemake.params.crops)
    base_year = int(snakemake.params.currency_base_year)
    avg_start = int(snakemake.params.averaging_period["start_year"])
    avg_end = int(snakemake.params.averaging_period["end_year"])
    non_endogenous_cost_share = float(snakemake.params.non_endogenous_cost_share)
    price_element = int(snakemake.params.price_element_code)
    yield_element = int(snakemake.params.yield_element_code)

    years = list(range(avg_start, avg_end + 1))

    # Load mappings
    mapping_df = _load_crop_mapping(mapping_path)
    m49_to_iso3 = load_m49_to_iso3(m49_path)
    cpi_df = pd.read_csv(cpi_path)

    with open(proxies_path) as f:
        proxies = yaml.safe_load(f)

    # Build item code mapping
    logger.info("Loading FAOSTAT PP bulk data")
    pp_bulk = load_bulk(pp_path)
    pp_item_map = get_item_map(pp_bulk)

    logger.info("Loading FAOSTAT QCL bulk data")
    qcl_bulk = load_bulk(qcl_path)
    qcl_item_map = get_item_map(qcl_bulk)

    # Map crop→item_code via the shared mapping table
    mapping_df["item_code"] = mapping_df["faostat_item"].map(pp_item_map)
    missing = mapping_df[mapping_df["item_code"].isna()]["faostat_item"].unique()
    if len(missing) > 0:
        logger.warning(
            "FAOSTAT items missing from PP data: %s", ", ".join(str(m) for m in missing)
        )
    mapping_df = mapping_df.dropna(subset=["item_code"])
    mapping_df["item_code"] = mapping_df["item_code"].astype(int)

    # For crops sharing a FAOSTAT item (e.g. dryland-rice/wetland-rice → Rice),
    # each gets the same price/yield — no share splitting needed for costs.
    crop_to_items = mapping_df.groupby("crop")["item_code"].apply(list).to_dict()

    # Also add QCL item codes (may differ slightly)
    mapping_df["qcl_item_code"] = mapping_df["faostat_item"].map(qcl_item_map)
    qcl_valid = mapping_df.dropna(subset=["qcl_item_code"])
    qcl_valid = qcl_valid.copy()
    qcl_valid["qcl_item_code"] = qcl_valid["qcl_item_code"].astype(int)
    crop_to_qcl_items = qcl_valid.groupby("crop")["qcl_item_code"].apply(list).to_dict()

    # Add ISO3 to bulk data
    pp_bulk = add_iso3_column(pp_bulk, m49_to_iso3)
    qcl_bulk = add_iso3_column(qcl_bulk, m49_to_iso3)

    all_pp_item_codes = sorted(
        {code for codes in crop_to_items.values() for code in codes}
    )
    all_qcl_item_codes = sorted(
        {code for codes in crop_to_qcl_items.values() for code in codes}
    )

    # Filter PP data: element 5531 = Producer Price (USD/tonne)
    pp_df = filter_bulk(
        pp_bulk,
        element_codes=[price_element],
        item_codes=all_pp_item_codes,
        years=years,
        iso3_codes=countries,
    )
    pp_df = pp_df.dropna(subset=["Value"])
    pp_df["country"] = pp_df["iso3"].str.upper()
    pp_df["year"] = pd.to_numeric(pp_df["Year"], errors="coerce").astype(int)
    pp_df["value"] = pp_df["Value"].astype(float)
    logger.info("PP data: %d rows after filtering", len(pp_df))

    # Filter QCL data: element 5419 = Yield (hg/ha)
    qcl_df = filter_bulk(
        qcl_bulk,
        element_codes=[yield_element],
        item_codes=all_qcl_item_codes,
        years=years,
        iso3_codes=countries,
    )
    qcl_df = qcl_df.dropna(subset=["Value"])
    qcl_df["country"] = qcl_df["iso3"].str.upper()
    qcl_df["year"] = pd.to_numeric(qcl_df["Year"], errors="coerce").astype(int)
    # Convert hg/ha to t/ha (1 hg = 0.0001 t)
    qcl_df["yield_t_per_ha"] = qcl_df["Value"].astype(float) / 10_000.0
    logger.info("QCL yield data: %d rows after filtering", len(qcl_df))

    # Build per-(crop, country, year) price and yield tables
    results = []

    for crop in crops:
        pp_items = crop_to_items.get(crop)
        qcl_items = crop_to_qcl_items.get(crop)

        # Handle proxy crops
        actual_crop = crop
        if pp_items is None or qcl_items is None:
            proxy = proxies.get(crop)
            if proxy is None:
                logger.warning(
                    "No FAOSTAT mapping or proxy for crop '%s'; skipping", crop
                )
                continue
            pp_items = crop_to_items.get(proxy)
            qcl_items = crop_to_qcl_items.get(proxy)
            if pp_items is None or qcl_items is None:
                logger.warning(
                    "Proxy crop '%s' for '%s' also has no FAOSTAT mapping; skipping",
                    proxy,
                    crop,
                )
                continue
            logger.info("Using proxy '%s' for crop '%s'", proxy, crop)

        # Get prices for this crop
        crop_pp = pp_df[pp_df["Item Code"].isin(pp_items)].copy()
        if crop_pp.empty:
            logger.warning("No FAOSTAT prices for crop '%s'", actual_crop)
            continue

        # Get yields for this crop
        crop_qcl = qcl_df[qcl_df["Item Code"].isin(qcl_items)].copy()
        if crop_qcl.empty:
            logger.warning("No FAOSTAT yields for crop '%s'", actual_crop)
            continue

        # When multiple FAOSTAT items map to one crop, average them
        crop_pp_agg = crop_pp.groupby(["country", "year"])["value"].mean().reset_index()
        crop_qcl_agg = (
            crop_qcl.groupby(["country", "year"])["yield_t_per_ha"].mean().reset_index()
        )

        # CPI-deflate prices to base year
        crop_pp_agg = _deflate_to_base_year(crop_pp_agg, cpi_df, base_year)
        crop_pp_agg = crop_pp_agg.dropna(subset=["value"])

        # Merge price and yield on (country, year)
        merged = crop_pp_agg.merge(crop_qcl_agg, on=["country", "year"], how="inner")
        merged = merged[(merged["value"] > 0) & (merged["yield_t_per_ha"] > 0)]

        if merged.empty:
            logger.warning("No valid price*yield pairs for crop '%s'", actual_crop)
            continue

        # revenue_per_ha = price (USD/t fresh) * yield (t fresh/ha)
        merged["revenue_per_ha"] = merged["value"] * merged["yield_t_per_ha"]

        # Average across years per (country)
        avg = (
            merged.groupby("country")
            .agg(
                revenue_per_ha=("revenue_per_ha", "mean"),
                n_years=("year", "nunique"),
            )
            .reset_index()
        )
        avg["crop"] = crop
        avg["is_fallback"] = False
        results.append(avg)

    if not results:
        raise RuntimeError("No valid FAOSTAT crop cost data produced")

    costs_df = pd.concat(results, ignore_index=True)

    # Apply non-endogenous cost share
    cost_col = f"cost_usd_{base_year}_per_ha"
    costs_df[cost_col] = costs_df["revenue_per_ha"] * non_endogenous_cost_share

    # Fill missing (crop, country) with global production-weighted median
    # Use simple median per crop as fallback
    crop_medians = costs_df.groupby("crop")[cost_col].median()
    n_fallbacks = 0

    fallback_rows = []
    for crop in crops:
        existing_countries = set(costs_df.loc[costs_df["crop"] == crop, "country"])
        missing_countries = set(countries) - existing_countries
        if not missing_countries:
            continue

        median_cost = crop_medians.get(crop)
        if median_cost is None or not np.isfinite(median_cost):
            logger.warning(
                "No median cost available for crop '%s'; %d countries will have zero cost",
                crop,
                len(missing_countries),
            )
            median_cost = 0.0

        for country in sorted(missing_countries):
            fallback_rows.append(
                {
                    "crop": crop,
                    "country": country,
                    cost_col: median_cost,
                    "n_years": 0,
                    "is_fallback": True,
                }
            )
            n_fallbacks += 1

    if fallback_rows:
        fallback_df = pd.DataFrame(fallback_rows)
        costs_df = pd.concat(
            [
                costs_df[["crop", "country", cost_col, "n_years", "is_fallback"]],
                fallback_df,
            ],
            ignore_index=True,
        )

    logger.info(
        "FAOSTAT crop costs: %d (crop, country) pairs, %d fallbacks (%.1f%%)",
        len(costs_df),
        n_fallbacks,
        100.0 * n_fallbacks / max(len(costs_df), 1),
    )

    # Sort and write output
    costs_df = costs_df[["crop", "country", cost_col, "n_years", "is_fallback"]]
    costs_df = costs_df.sort_values(["crop", "country"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    costs_df.to_csv(output_path, index=False)
    logger.info("Wrote %d rows to %s", len(costs_df), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
