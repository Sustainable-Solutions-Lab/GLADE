#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Aggregate FAOSTAT FBS cereal supply to per-country intake (g/day).

GDD and GBD systematically under-cover refined grain consumption in
some HICs (Norway's GDD reports 13.5 g/day grain against a FAOSTAT
flour-equivalent intake of ~185 g/day — a 90% data hole). This script
produces an FBS-derived cereal intake estimate per country that the
baseline-diet pipeline can use to backfill the refined-grain hole
without disturbing the GBD-anchored ``whole_grains`` total.

Method:
    1. Sum FAOSTAT FBS supply (kg/cap/year) over the cereal items in
       ``faostat_food_item_map.csv`` whose mapped foods belong to the
       configured cereal food groups.
    2. Convert to g/day fresh weight.
    3. Apply (1 - loss)(1 - waste) using the food_loss_waste table.
       The cereal supply is attributed to the union of the relevant
       food groups; we apply the FLW for the ``grain`` group as a
       conservative estimate (refined and whole grains share most
       loss/waste pathways: storage, milling, retail).

Input:
    - FAOSTAT FBS bulk Parquet
    - data/curated/faostat_food_item_map.csv
    - data/curated/food_groups.csv
    - data/curated/M49-codes.csv
    - food_loss_waste.csv

Output:
    - CSV with columns: country, fbs_cereal_intake_g_per_day
"""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
    FBS_COUNTRY_FALLBACKS,
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Food groups whose foods we treat as "cereals" for the FBS aggregation.
# The output is a single per-country total covering both refined (grain)
# and whole-grain consumption; downstream logic in estimate_baseline_diet
# subtracts the GBD-anchored whole_grains total to recover the refined
# residual.
CEREAL_FOOD_GROUPS = ("grain", "whole_grains")


def main():
    countries = [str(c).upper() for c in snakemake.params.countries]
    reference_year = int(snakemake.params.reference_year)
    fbs_element_code = int(snakemake.params.fbs_element_code)
    fbs_csv = snakemake.input.fbs_csv
    food_item_map_path = snakemake.input.food_item_map
    food_groups_path = snakemake.input.food_groups
    m49_codes = snakemake.input.m49_codes
    flw_path = snakemake.input.food_loss_waste
    output_file = snakemake.output.cereal_intake

    # Build set of FBS item codes that contribute to the cereal food groups.
    food_groups_df = pd.read_csv(food_groups_path)
    fg_map = food_groups_df.set_index("food")["group"].to_dict()
    item_map = pd.read_csv(food_item_map_path, comment="#")
    item_map["item_code"] = pd.to_numeric(item_map["item_code"], errors="coerce")
    item_map = item_map.dropna(subset=["item_code"]).copy()
    item_map["item_code"] = item_map["item_code"].astype(int)
    item_map["food_group"] = item_map["food"].map(fg_map)
    cereal_items = sorted(
        item_map.loc[
            item_map["food_group"].isin(CEREAL_FOOD_GROUPS), "item_code"
        ].unique()
    )
    if not cereal_items:
        raise ValueError(
            f"No FBS items mapped to cereal food groups {CEREAL_FOOD_GROUPS}; "
            "check faostat_food_item_map.csv and food_groups.csv."
        )
    logger.info(
        "Aggregating FBS supply over %d cereal items: %s",
        len(cereal_items),
        cereal_items,
    )

    bulk = load_bulk(fbs_csv)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Include proxy countries to support fallback fill.
    all_proxies = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    filter_countries = list(set(countries) | all_proxies)

    df = filter_bulk(
        bulk,
        element_codes=[fbs_element_code],
        item_codes=cereal_items,
        years=[reference_year],
        iso3_codes=filter_countries,
    )
    if df.empty:
        raise ValueError(
            f"FAOSTAT FBS returned no cereal supply for year {reference_year}."
        )
    df["country"] = df["iso3"].astype(str).str.upper()
    df["Value"] = df["Value"].fillna(0.0)

    # Sum cereal supply (kg/cap/year) per country
    supply = df.groupby("country", as_index=False)["Value"].sum()
    supply = supply.rename(columns={"Value": "supply_kg_per_capita_year"})
    supply["fbs_cereal_supply_g_per_day"] = (
        supply["supply_kg_per_capita_year"] * 1000.0 / 365.25
    )

    # Apply waste/loss correction. Cereal-group FLW factors live under
    # food_group="grain"; we apply the same factor to the aggregate
    # since whole and refined cereals share nearly identical
    # loss/waste pathways (storage, milling, retail, household).
    flw = pd.read_csv(flw_path)
    flw_grain = flw[flw["food_group"] == "grain"][
        ["country", "loss_fraction", "waste_fraction"]
    ].copy()
    if flw_grain.empty:
        raise ValueError(
            "food_loss_waste table has no rows for food_group='grain'; "
            "cannot apply waste correction."
        )
    supply = supply.merge(flw_grain, on="country", how="left")
    # Fall back to global mean for any country missing FLW (rare edge case)
    global_loss = flw_grain["loss_fraction"].mean()
    global_waste = flw_grain["waste_fraction"].mean()
    supply["loss_fraction"] = supply["loss_fraction"].fillna(global_loss)
    supply["waste_fraction"] = supply["waste_fraction"].fillna(global_waste)
    supply["fbs_cereal_intake_g_per_day"] = (
        supply["fbs_cereal_supply_g_per_day"]
        * (1.0 - supply["loss_fraction"])
        * (1.0 - supply["waste_fraction"])
    )

    # Restrict to configured countries and fill missing via proxies
    result = supply[supply["country"].isin(countries)][
        ["country", "fbs_cereal_intake_g_per_day"]
    ].copy()
    present = set(result["country"])
    missing = sorted(set(countries) - present)
    if missing:
        proxy_rows = []
        for iso in missing:
            for proxy in FBS_COUNTRY_FALLBACKS.get(iso, []):
                if proxy in present:
                    val = float(
                        result.loc[
                            result["country"] == proxy, "fbs_cereal_intake_g_per_day"
                        ].iloc[0]
                    )
                    proxy_rows.append(
                        {"country": iso, "fbs_cereal_intake_g_per_day": val}
                    )
                    logger.info(
                        "FBS cereal intake: filling %s via proxy %s", iso, proxy
                    )
                    break
            else:
                logger.warning(
                    "FBS cereal intake: no data and no usable proxy for %s; "
                    "row will be omitted from the output.",
                    iso,
                )
        if proxy_rows:
            result = pd.concat([result, pd.DataFrame(proxy_rows)], ignore_index=True)

    result = result.sort_values("country").reset_index(drop=True)
    result.to_csv(output_file, index=False)
    logger.info(
        "Wrote FBS cereal intake for %d countries (median=%.1f g/day) to %s",
        len(result),
        result["fbs_cereal_intake_g_per_day"].median(),
        output_file,
    )


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
