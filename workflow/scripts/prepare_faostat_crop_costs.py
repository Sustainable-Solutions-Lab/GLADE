# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare per-(crop, country) crop production costs from FAOSTAT price and yield data.

Cost model:
    cost_usd_per_ha = producer_price_usd_per_tonne * yield_t_per_ha * non_endogenous_cost_share

Both price and yield come from FAOSTAT bulk downloads (PP and QCL domains).
Prices are CPI-deflated to the configured base year before averaging.

An optional per-crop upper winsorization caps non-fallback country values
at a configured quantile of that crop's non-fallback distribution. This
removes FAOSTAT outliers in cold-climate / greenhouse-heavy producers
(e.g. tomato, carrot, mango in northern Europe and Japan) where producer
prices reflect protected-cultivation systems that the model treats as
field cost. Capped rows are flagged via the ``is_capped`` column.

Inputs
------
- PP.parquet : FAOSTAT Prices bulk (element 5532 = Producer Price USD/tonne)
- QCL.parquet : FAOSTAT Production bulk (element 5412 = Yield kg/ha)
- faostat_crop_item_map.csv : model crop -> FAOSTAT item mapping
- M49-codes.csv : M49 -> ISO3 mapping
- cpi_annual.csv : US CPI-U annual averages for deflation
- faostat_cost_proxies.yaml : proxy mappings for unmapped crops

Output
------
- faostat_crop_costs.csv : columns (crop, country, cost_usd_{base_year}_per_ha,
  n_years, is_fallback, is_capped)
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


def _compute_revenue_per_ha(
    pp_bulk: pd.DataFrame,
    qcl_bulk: pd.DataFrame,
    *,
    crops: list[str],
    crop_to_items: dict[str, list[int]],
    crop_to_qcl_items: dict[str, list[int]],
    proxies: dict,
    cpi_df: pd.DataFrame,
    base_year: int,
    years: list[int],
    price_element: int,
    yield_element: int,
    iso3_codes: list[str] | None,
) -> pd.DataFrame:
    """Compute per-(crop, country) revenue_per_ha and yield from FAOSTAT bulks.

    Pass ``iso3_codes=None`` to compute across all countries (used to derive
    the global median fallback for crops absent from the configured-country
    subset). Returns a DataFrame with columns ``crop``, ``country``,
    ``revenue_per_ha``, ``yield_t_per_ha``, ``n_years``.
    """
    all_pp_item_codes = sorted(
        {code for codes in crop_to_items.values() for code in codes}
    )
    all_qcl_item_codes = sorted(
        {code for codes in crop_to_qcl_items.values() for code in codes}
    )

    pp_df = filter_bulk(
        pp_bulk,
        element_codes=[price_element],
        item_codes=all_pp_item_codes,
        years=years,
        iso3_codes=iso3_codes,
    )
    pp_df = pp_df.dropna(subset=["Value"])
    pp_df["country"] = pp_df["iso3"].str.upper()
    pp_df["year"] = pd.to_numeric(pp_df["Year"], errors="coerce").astype(int)
    pp_df["value"] = pp_df["Value"].astype(float)

    qcl_df = filter_bulk(
        qcl_bulk,
        element_codes=[yield_element],
        item_codes=all_qcl_item_codes,
        years=years,
        iso3_codes=iso3_codes,
    )
    qcl_df = qcl_df.dropna(subset=["Value"])
    # Element 5412 is kg/ha in modern FAOSTAT vintages; element 5419 was
    # hg/ha in older releases. The conversion factor /1000 below assumes
    # kg/ha, so a future swap to 5419 would silently produce a 10x error.
    if "Unit" in qcl_df.columns:
        unit_set = set(qcl_df["Unit"].astype(str).str.strip().unique())
        assert unit_set <= {"kg/ha"}, f"FAOSTAT QCL yield units unexpected: {unit_set}"
    qcl_df["country"] = qcl_df["iso3"].str.upper()
    qcl_df["year"] = pd.to_numeric(qcl_df["Year"], errors="coerce").astype(int)
    qcl_df["yield_t_per_ha"] = qcl_df["Value"].astype(float) / 1_000.0

    results = []
    for crop in crops:
        pp_items = crop_to_items.get(crop)
        qcl_items = crop_to_qcl_items.get(crop)
        if pp_items is None or qcl_items is None:
            proxy = proxies.get(crop)
            if proxy is None:
                continue
            pp_items = crop_to_items.get(proxy)
            qcl_items = crop_to_qcl_items.get(proxy)
            if pp_items is None or qcl_items is None:
                continue

        crop_pp = pp_df[pp_df["Item Code"].isin(pp_items)]
        crop_qcl = qcl_df[qcl_df["Item Code"].isin(qcl_items)]
        if crop_pp.empty or crop_qcl.empty:
            continue

        crop_pp_agg = crop_pp.groupby(["country", "year"])["value"].mean().reset_index()
        crop_qcl_agg = (
            crop_qcl.groupby(["country", "year"])["yield_t_per_ha"].mean().reset_index()
        )
        crop_pp_agg = _deflate_to_base_year(crop_pp_agg, cpi_df, base_year)
        crop_pp_agg = crop_pp_agg.dropna(subset=["value"])

        merged = crop_pp_agg.merge(crop_qcl_agg, on=["country", "year"], how="inner")
        merged = merged[(merged["value"] > 0) & (merged["yield_t_per_ha"] > 0)]
        if merged.empty:
            continue

        merged["revenue_per_ha"] = merged["value"] * merged["yield_t_per_ha"]
        avg = (
            merged.groupby("country")
            .agg(
                revenue_per_ha=("revenue_per_ha", "mean"),
                yield_t_per_ha=("yield_t_per_ha", "mean"),
                n_years=("year", "nunique"),
            )
            .reset_index()
        )
        avg["crop"] = crop
        results.append(avg)

    if not results:
        return pd.DataFrame(
            columns=["crop", "country", "revenue_per_ha", "yield_t_per_ha", "n_years"]
        )
    return pd.concat(results, ignore_index=True)


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
    cap_quantile_raw = snakemake.params.outlier_cap_quantile
    cap_quantile = float(cap_quantile_raw) if cap_quantile_raw is not None else None
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

    # Per-(crop, country) revenue computed on the configured-country subset.
    costs_df = _compute_revenue_per_ha(
        pp_bulk,
        qcl_bulk,
        crops=crops,
        crop_to_items=crop_to_items,
        crop_to_qcl_items=crop_to_qcl_items,
        proxies=proxies,
        cpi_df=cpi_df,
        base_year=base_year,
        years=years,
        price_element=price_element,
        yield_element=yield_element,
        iso3_codes=countries,
    )
    if costs_df.empty:
        raise RuntimeError("No valid FAOSTAT crop cost data produced")
    costs_df["is_fallback"] = False
    logger.info(
        "Configured-country FAOSTAT revenue: %d (crop, country) rows", len(costs_df)
    )

    # Apply non-endogenous cost share
    cost_col = f"cost_usd_{base_year}_per_ha"
    costs_df[cost_col] = costs_df["revenue_per_ha"] * non_endogenous_cost_share

    # Upper winsorization per crop, on per-tonne cost (USD/t).
    #
    # We cap producer-price-equivalent cost rather than per-hectare cost
    # because the original outlier pattern is greenhouse / protected-
    # cultivation FAOSTAT data: BOTH producer price AND reported yield
    # are elevated in those countries. Capping per-hectare cost while
    # leaving yields untouched collapses the implied per-tonne cost
    # (USD/ha / t/ha) to artificially low values -- e.g. tomato:BEL at
    # the previous USD/ha cap had per-tonne cost ~$700/t versus the
    # crop's wholesale-realistic ~$2-3k/t. That collapse then fed large
    # positive corrections in the cost calibration (the LP wanted to
    # over-produce greenhouse tomato), which in turn inflated the L1
    # production-stability calibration.
    #
    # Capping per-tonne cost preserves the elevated implicit price but
    # bounds it at a realistic crop-wholesale level. Per-hectare cost is
    # then ``capped_cost_per_t * actual_yield_per_ha``, so high-yield
    # greenhouse countries still see proportionally high per-hectare
    # cost (consistent with their actual production cost).
    costs_df["is_capped"] = False
    if cap_quantile is not None and len(costs_df) > 0:
        cost_per_t = costs_df[cost_col] / costs_df["yield_t_per_ha"].where(
            costs_df["yield_t_per_ha"] > 0
        )
        costs_df["cost_usd_per_t"] = cost_per_t
        caps = costs_df.groupby("crop")["cost_usd_per_t"].quantile(cap_quantile)
        n_capped_total = 0
        for crop, cap in caps.items():
            if not np.isfinite(cap):
                continue
            mask = (costs_df["crop"] == crop) & (costs_df["cost_usd_per_t"] > cap)
            n = int(mask.sum())
            if n == 0:
                continue
            # Recompute per-ha cost from capped per-tonne cost * actual yield.
            costs_df.loc[mask, "cost_usd_per_t"] = cap
            costs_df.loc[mask, cost_col] = cap * costs_df.loc[mask, "yield_t_per_ha"]
            costs_df.loc[mask, "is_capped"] = True
            n_capped_total += n
            logger.info(
                "  %s: capped %d countries at p%.0f = %.0f USD/t",
                crop,
                n,
                cap_quantile * 100,
                cap,
            )
        logger.info(
            "Outlier cap (q=%.2f, on USD/t): clipped %d of %d non-fallback rows (%.1f%%)",
            cap_quantile,
            n_capped_total,
            len(costs_df),
            100.0 * n_capped_total / max(len(costs_df), 1),
        )
        costs_df = costs_df.drop(columns=["cost_usd_per_t"])

    # Fill missing (crop, country) with the per-crop median (post-cap).
    # Primary fallback: median across configured countries. For crops that
    # have no data at all in the configured-country subset (e.g. tropical
    # crops in a Europe-only run), fall back further to a global median
    # computed from the unfiltered FAOSTAT data.
    crop_medians = costs_df.groupby("crop")[cost_col].median()
    crops_needing_global_median = [
        c
        for c in crops
        if (crop_medians.get(c) is None)
        or (not np.isfinite(crop_medians.get(c, np.nan)))
    ]
    if crops_needing_global_median:
        logger.info(
            "Computing global FAOSTAT median for crops absent from configured-country "
            "subset: %s",
            ", ".join(sorted(crops_needing_global_median)),
        )
        global_df = _compute_revenue_per_ha(
            pp_bulk,
            qcl_bulk,
            crops=crops_needing_global_median,
            crop_to_items=crop_to_items,
            crop_to_qcl_items=crop_to_qcl_items,
            proxies=proxies,
            cpi_df=cpi_df,
            base_year=base_year,
            years=years,
            price_element=price_element,
            yield_element=yield_element,
            iso3_codes=None,
        )
        if not global_df.empty:
            global_df[cost_col] = (
                global_df["revenue_per_ha"] * non_endogenous_cost_share
            )
            global_medians = global_df.groupby("crop")[cost_col].median()
            crop_medians = crop_medians.combine_first(global_medians)

    n_fallbacks = 0
    fallback_rows = []
    for crop in crops:
        existing_countries = set(costs_df.loc[costs_df["crop"] == crop, "country"])
        missing_countries = set(countries) - existing_countries
        if not missing_countries:
            continue

        median_cost = crop_medians.get(crop)
        if median_cost is None or not np.isfinite(median_cost):
            raise ValueError(
                f"No median FAOSTAT cost available for crop '{crop}' (neither "
                f"configured-country subset nor global FAOSTAT has price*yield "
                f"data); {len(missing_countries)} countries would otherwise "
                f"inherit a zero cost. Check the input parquet for this crop."
            )

        for country in sorted(missing_countries):
            fallback_rows.append(
                {
                    "crop": crop,
                    "country": country,
                    cost_col: median_cost,
                    "n_years": 0,
                    "is_fallback": True,
                    "is_capped": False,
                }
            )
            n_fallbacks += 1

    output_cols = ["crop", "country", cost_col, "n_years", "is_fallback", "is_capped"]
    if fallback_rows:
        fallback_df = pd.DataFrame(fallback_rows)
        costs_df = pd.concat(
            [costs_df[output_cols], fallback_df[output_cols]],
            ignore_index=True,
        )

    logger.info(
        "FAOSTAT crop costs: %d (crop, country) pairs, %d fallbacks (%.1f%%)",
        len(costs_df),
        n_fallbacks,
        100.0 * n_fallbacks / max(len(costs_df), 1),
    )

    # Sort and write output
    costs_df = costs_df[output_cols]
    costs_df = costs_df.sort_values(["crop", "country"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    costs_df.to_csv(output_path, index=False)
    logger.info("Wrote %d rows to %s", len(costs_df), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
