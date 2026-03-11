# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run all model analysis steps with the network loaded once.

Combines extract_statistics, extract_net_emissions, extract_objective_breakdown,
extract_ghg_attribution, and extract_health_impacts into a single pass over the
solved network. Intermediate DataFrames (food_consumption, food_group_consumption)
are passed in memory rather than written and re-read from disk.
"""

from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.analysis.extract_ghg_attribution import (
    add_monetary_value as add_ghg_monetary_value,
)
from workflow.scripts.analysis.extract_ghg_attribution import (
    compute_bus_intensities,
    compute_ghg_totals,
    join_intensities_to_consumption,
)
from workflow.scripts.analysis.extract_health_impacts import (
    add_monetary_value as add_health_monetary_value,
)
from workflow.scripts.analysis.extract_health_impacts import (
    compute_health_marginals,
    extract_yll_totals,
    load_health_data,
)
from workflow.scripts.analysis.extract_net_emissions import extract_net_emissions
from workflow.scripts.analysis.extract_objective_breakdown import (
    extract_objective_breakdown,
)
from workflow.scripts.analysis.extract_statistics import (
    extract_animal_production,
    extract_crop_production,
    extract_feed_by_animal,
    extract_feed_by_category,
    extract_food_consumption,
    extract_food_group_consumption,
    extract_land_use,
    extract_luc_breakdown,
)
from workflow.scripts.logging_config import setup_script_logging


def _write_empty_outputs() -> None:
    """Write empty CSV files for all declared outputs."""
    for attr in dir(snakemake.output):
        if attr.startswith("_"):
            continue
        path = getattr(snakemake.output, attr, None)
        if isinstance(path, str) and path.endswith(".csv"):
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])

    try:
        n = pypsa.Network(snakemake.input.network)
    except (KeyError, Exception) as e:
        logger.warning(
            "Failed to load network (%s) — likely an unsolved model. "
            "Writing empty outputs.",
            e,
        )
        _write_empty_outputs()
        return

    if n.links.empty:
        logger.warning(
            "Network has no links — likely an unsolved model. " "Writing empty outputs."
        )
        _write_empty_outputs()
        return

    logger.info("Loaded network with %d links", len(n.links))

    # --- Statistics ---
    logger.info("Extracting statistics...")
    crop_production = extract_crop_production(n)
    land_use = extract_land_use(n)
    animal_production = extract_animal_production(n)
    food_consumption = extract_food_consumption(n)
    food_group_consumption = extract_food_group_consumption(n)
    feed_by_category = extract_feed_by_category(n)
    feed_by_animal = extract_feed_by_animal(n)

    # --- LUC breakdown ---
    logger.info("Extracting LUC breakdown...")
    m49 = pd.read_csv(snakemake.input.m49_codes, sep=";", comment="#")
    country_to_continent = {}
    for _, row in m49.iterrows():
        iso3 = row.get("ISO-alpha3 Code")
        region = row.get("Region Name")
        if pd.notna(iso3) and pd.notna(region) and str(iso3).strip():
            country_to_continent[str(iso3).strip()] = str(region).strip()
    luc_breakdown = extract_luc_breakdown(n, country_to_continent)

    # --- Net emissions ---
    logger.info("Extracting net emissions...")
    ch4_gwp = float(snakemake.params.ch4_gwp)
    n2o_gwp = float(snakemake.params.n2o_gwp)
    net_emissions = extract_net_emissions(n, ch4_gwp, n2o_gwp)

    # --- Objective breakdown ---
    logger.info("Extracting objective breakdown...")
    objective_breakdown = extract_objective_breakdown(n)

    # --- GHG attribution ---
    logger.info("Computing GHG attribution...")
    ghg_price = float(snakemake.params.ghg_price)
    food_groups = pd.read_csv(snakemake.input.food_groups)
    bus_intensities = compute_bus_intensities(n, ch4_gwp, n2o_gwp)
    ghg_attribution = join_intensities_to_consumption(
        food_consumption, food_groups, bus_intensities
    )
    ghg_attribution = add_ghg_monetary_value(ghg_attribution, ghg_price)
    ghg_attribution = ghg_attribution.sort_values(["country", "food"]).reset_index(
        drop=True
    )
    ghg_attribution_totals = compute_ghg_totals(ghg_attribution)

    # --- Health impacts ---
    logger.info("Extracting health impacts...")
    value_per_yll = float(snakemake.params.value_per_yll)
    risk_factors = list(snakemake.params.health_risk_factors)
    health_data = load_health_data(
        {
            "risk_breakpoints": snakemake.input.risk_breakpoints,
            "health_cluster_cause": snakemake.input.health_cluster_cause,
            "health_cause_log": snakemake.input.health_cause_log,
            "health_clusters": snakemake.input.health_clusters,
            "population": snakemake.input.population,
        }
    )
    health_marginals = compute_health_marginals(
        food_group_consumption, health_data, risk_factors
    )
    health_marginals = add_health_monetary_value(health_marginals, value_per_yll)
    health_marginals = health_marginals.sort_values(
        ["country", "food_group"]
    ).reset_index(drop=True)
    health_totals = extract_yll_totals(n)

    # Write all outputs
    output_dir = Path(snakemake.output.crop_production).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    crop_production.to_csv(snakemake.output.crop_production, index=False)
    land_use.to_csv(snakemake.output.land_use, index=False)
    animal_production.to_csv(snakemake.output.animal_production, index=False)
    food_consumption.to_csv(snakemake.output.food_consumption, index=False)
    food_group_consumption.to_csv(snakemake.output.food_group_consumption, index=False)
    net_emissions.to_csv(snakemake.output.net_emissions, index=False)
    objective_breakdown.to_csv(snakemake.output.objective_breakdown, index=False)
    ghg_attribution.to_csv(snakemake.output.ghg_attribution, index=False)
    ghg_attribution_totals.to_csv(snakemake.output.ghg_attribution_totals, index=False)
    health_marginals.to_csv(snakemake.output.health_marginals, index=False)
    health_totals.to_csv(snakemake.output.health_totals, index=False)
    feed_by_category.to_csv(snakemake.output.feed_by_category, index=False)
    feed_by_animal.to_csv(snakemake.output.feed_by_animal, index=False)
    luc_breakdown.to_csv(snakemake.output.luc_breakdown, index=False)

    logger.info("Wrote all analysis outputs to %s", output_dir)


if __name__ == "__main__":
    main()
