# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run all model analysis steps with the network loaded once.

Combines extract_statistics, extract_net_emissions, extract_objective_breakdown,
extract_ghg_attribution, and extract_health_impacts into a single pass over the
solved network. Intermediate DataFrames (food_consumption, food_group_consumption)
are passed in memory rather than written and re-read from disk.
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.analysis.extract_baseline_deviation import (
    extract_baseline_deviation,
)
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
    compute_health_attribution,
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


def write_empty_outputs(output) -> None:
    """Write empty Parquet files for all declared outputs.

    Parameters
    ----------
    output
        Snakemake output object (or any object whose non-underscore string
        attributes ending in ``.parquet`` should be created as empty files).
    """
    for attr in dir(output):
        if attr.startswith("_"):
            continue
        path = getattr(output, attr, None)
        if isinstance(path, str) and path.endswith(".parquet"):
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame().to_parquet(p)


def run_analysis(
    n: pypsa.Network,
    *,
    output_paths: dict[str, str],
    food_groups_path: str,
    m49_codes_path: str,
    risk_breakpoints_path: str,
    health_cluster_cause_path: str,
    health_cause_log_path: str,
    health_clusters_path: str,
    population_path: str,
    derived_tmrel_path: str,
    ghg_price: float,
    ch4_gwp: float,
    n2o_gwp: float,
    value_per_yll: float,
    health_risk_factors: list[str],
    logger: logging.Logger,
) -> None:
    """Run all analysis extractions on a solved network.

    Parameters
    ----------
    n
        Solved PyPSA network with solution assigned.
    output_paths
        Mapping of output name to file path (e.g. ``{"crop_production": "/.../crop_production.parquet"}``).
    food_groups_path, m49_codes_path, ...
        Paths to input data files needed by analysis.
    ghg_price, ch4_gwp, n2o_gwp, value_per_yll
        Scalar parameters for emissions and health valuation.
    health_risk_factors
        List of risk factor names for health impact computation.
    logger
        Logger instance.
    """
    # --- Statistics ---
    logger.info("Extracting statistics...")
    crop_production = extract_crop_production(n)
    land_use = extract_land_use(n)
    animal_production = extract_animal_production(n)
    food_consumption = extract_food_consumption(n)
    food_group_consumption = extract_food_group_consumption(n)
    feed_by_category = extract_feed_by_category(n)
    feed_by_animal = extract_feed_by_animal(n)

    # --- Baseline deviation ---
    logger.info("Extracting baseline deviation...")
    baseline_deviation = extract_baseline_deviation(n)

    # --- LUC breakdown ---
    logger.info("Extracting LUC breakdown...")
    m49 = pd.read_csv(m49_codes_path, sep=";", comment="#")
    country_to_continent = {}
    for _, row in m49.iterrows():
        iso3 = row.get("ISO-alpha3 Code")
        region = row.get("Region Name")
        if pd.notna(iso3) and pd.notna(region) and str(iso3).strip():
            country_to_continent[str(iso3).strip()] = str(region).strip()
    luc_breakdown = extract_luc_breakdown(n, country_to_continent)

    # --- Net emissions ---
    logger.info("Extracting net emissions...")
    net_emissions = extract_net_emissions(n, ch4_gwp, n2o_gwp)

    # --- Objective breakdown ---
    logger.info("Extracting objective breakdown...")
    objective_breakdown = extract_objective_breakdown(n)

    # --- GHG attribution ---
    logger.info("Computing GHG attribution...")
    food_groups = pd.read_csv(food_groups_path)
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
    risk_factors = list(health_risk_factors)
    health_data = load_health_data(
        {
            "risk_breakpoints": risk_breakpoints_path,
            "health_cluster_cause": health_cluster_cause_path,
            "health_cause_log": health_cause_log_path,
            "health_clusters": health_clusters_path,
            "population": population_path,
            "derived_tmrel": derived_tmrel_path,
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
    health_attribution = compute_health_attribution(
        food_group_consumption, health_data, risk_factors, n
    )

    # Write all outputs
    output_dir = Path(output_paths["crop_production"]).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "crop_production": crop_production,
        "land_use": land_use,
        "animal_production": animal_production,
        "food_consumption": food_consumption,
        "food_group_consumption": food_group_consumption,
        "net_emissions": net_emissions,
        "objective_breakdown": objective_breakdown,
        "ghg_attribution": ghg_attribution,
        "ghg_attribution_totals": ghg_attribution_totals,
        "health_marginals": health_marginals,
        "health_totals": health_totals,
        "health_attribution": health_attribution,
        "feed_by_category": feed_by_category,
        "feed_by_animal": feed_by_animal,
        "luc_breakdown": luc_breakdown,
        "baseline_deviation": baseline_deviation,
    }
    for name, df in results.items():
        df.to_parquet(output_paths[name])

    logger.info("Wrote all analysis outputs to %s", output_dir)


def main() -> None:
    """Snakemake entry point: load solved network and run analysis."""
    logger = setup_script_logging(snakemake.log[0])

    network_path = Path(snakemake.input.network)
    if network_path.stat().st_size == 0:
        logger.warning(
            "Network file is empty (solve failed or timed out). "
            "Writing empty outputs."
        )
        write_empty_outputs(snakemake.output)
        return

    try:
        n = pypsa.Network(snakemake.input.network)
    except (KeyError, Exception) as e:
        logger.warning(
            "Failed to load network (%s) — likely an unsolved model. "
            "Writing empty outputs.",
            e,
        )
        write_empty_outputs(snakemake.output)
        return

    if n.links.empty:
        logger.warning(
            "Network has no links — likely an unsolved model. Writing empty outputs."
        )
        write_empty_outputs(snakemake.output)
        return

    logger.info("Loaded network with %d links", len(n.links))

    # Build output_paths dict from snakemake.output
    output_paths = {
        attr: getattr(snakemake.output, attr)
        for attr in dir(snakemake.output)
        if not attr.startswith("_")
        and isinstance(getattr(snakemake.output, attr), str)
        and getattr(snakemake.output, attr).endswith(".parquet")
    }

    run_analysis(
        n,
        output_paths=output_paths,
        food_groups_path=snakemake.input.food_groups,
        m49_codes_path=snakemake.input.m49_codes,
        risk_breakpoints_path=snakemake.input.risk_breakpoints,
        health_cluster_cause_path=snakemake.input.health_cluster_cause,
        health_cause_log_path=snakemake.input.health_cause_log,
        health_clusters_path=snakemake.input.health_clusters,
        population_path=snakemake.input.population,
        derived_tmrel_path=snakemake.input.derived_tmrel,
        ghg_price=float(snakemake.params.ghg_price),
        ch4_gwp=float(snakemake.params.ch4_gwp),
        n2o_gwp=float(snakemake.params.n2o_gwp),
        value_per_yll=float(snakemake.params.value_per_yll),
        health_risk_factors=list(snakemake.params.health_risk_factors),
        logger=logger,
    )


if __name__ == "__main__":
    main()
