#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export GLADE model to MPS format using Gurobi's native export.

This script builds the complete optimization model (including all constraints
added at solve time) and exports it to MPS format using Gurobi's native API,
which properly preserves integer/binary variable types.

Usage:
    pixi run -e gurobi python tools/export_model.py \
        --config config/yll.yaml \
        --scenario yll_20000

The exported MPS file can then be used for Gurobi parameter tuning:
    pixi run -e gurobi python tools/tune_model.py results/yll/exported/model_scen-yll_20000.mps
"""

import argparse
import logging
import os
from pathlib import Path

import pandas as pd
import pypsa
import yaml

from workflow.scripts.snakemake_utils import _recursive_update, apply_scenario_config
from workflow.scripts.solve_model.core import (
    add_food_group_constraints,
    add_food_incentives_to_objective,
    add_ghg_pricing_to_objective,
    add_macronutrient_constraints,
    add_residue_feed_constraints,
    build_residue_feed_fraction_by_country,
)
from workflow.scripts.solve_model.health import add_health_objective
from workflow.scripts.solve_namespace import resolve_calibration_source_paths

# Enable new PyPSA components API
pypsa.options.api.new_components_api = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def resolve_path_root(raw_path: str, key: str) -> Path:
    """Resolve environment variables and user-home markers in a path root."""
    resolved = os.path.expanduser(os.path.expandvars(raw_path))
    if "$" in resolved:
        raise ValueError(f"Unresolved environment variable in config.paths.{key}")
    return Path(resolved)


def load_config(config_path: str) -> dict:
    """Load a config file and merge with default.yaml."""
    project_root = Path(__file__).parent.parent

    # Load default config
    default_path = project_root / "config" / "default.yaml"
    with open(default_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Load and merge user config
    with open(config_path, encoding="utf-8") as f:
        user_config = yaml.safe_load(f)

    _recursive_update(config, user_config)
    return resolve_calibration_source_paths(config)


def build_and_export_model(
    config_path: str,
    scenario: str,
    output_path: Path | None = None,
) -> Path:
    """Build the complete model and export to MPS using Gurobi."""
    # Load and apply scenario config
    config = load_config(config_path)
    apply_scenario_config(config, scenario)

    config_name = config["name"]
    paths_cfg = config["paths"]
    results_dir = (
        resolve_path_root(paths_cfg["results_root"], "results_root") / config_name
    )

    # Determine output path
    if output_path is None:
        output_dir = results_dir / "exported"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"model_scen-{scenario}.mps"

    # Load the built network
    network_path = results_dir / f"build/model_scen-{scenario}.nc"
    if not network_path.exists():
        raise FileNotFoundError(
            f"Built network not found: {network_path}\n"
            f"First run: tools/smk -e gurobi -j4 --configfile {config_path} "
            f"-- {network_path}"
        )

    logger.info("Loading network from %s", network_path)
    n = pypsa.Network(network_path)

    # Add GHG pricing if enabled
    if config["emissions"]["ghg_pricing_enabled"]:
        ghg_price = float(config["emissions"]["ghg_price"])
        add_ghg_pricing_to_objective(n, ghg_price)
        logger.info("Added GHG pricing: $%.2f/tCO2", ghg_price)

    # Add food incentives if enabled
    if config["food_incentives"]["enabled"]:
        sources = config["food_incentives"]["sources"]
        incentive_paths = [str(Path(s.format(name=config_name))) for s in sources]
        existing_paths = [p for p in incentive_paths if Path(p).exists()]
        if existing_paths:
            add_food_incentives_to_objective(n, existing_paths)
            logger.info("Added food incentives from %d sources", len(existing_paths))

    # Create linopy model
    logger.info("Creating linopy model...")
    n.optimize.create_model()

    # Load population data
    processing_dir = (
        resolve_path_root(paths_cfg["processing_root"], "processing_root") / config_name
    )
    population_path = processing_dir / "population.csv"
    if not population_path.exists():
        raise FileNotFoundError(f"Population data not found: {population_path}")
    population_df = pd.read_csv(population_path)
    population_df["iso3"] = population_df["iso3"].astype(str).str.upper()
    population_map = (
        population_df.set_index("iso3")["population"].astype(float).to_dict()
    )

    # Add macronutrient constraints
    macronutrients = config.get("macronutrients")
    if macronutrients:
        add_macronutrient_constraints(n, macronutrients, population_map)
        logger.info("Added macronutrient constraints")

    # Add food group constraints
    food_group_cfg = config.get("food_groups", {}).get("constraints")
    if food_group_cfg:
        add_food_group_constraints(n, food_group_cfg, population_map, None)
        logger.info("Added food group constraints")

    # Add residue feed constraints
    max_feed_fraction = float(config["residues"]["max_feed_fraction"])
    m49_path = Path("data/curated/M49-codes.csv")
    if m49_path.exists():
        max_feed_by_country = build_residue_feed_fraction_by_country(
            config, str(m49_path)
        )
        add_residue_feed_constraints(n, max_feed_fraction, max_feed_by_country)
        logger.info("Added residue feed constraints")

    # Add health objective if enabled
    if config["health"]["enabled"]:
        health_data_dir = processing_dir / "health"
        clusters_path = processing_dir / "health/country_clusters.csv"

        required_health_files = [
            health_data_dir / "risk_breakpoints.csv",
            health_data_dir / "cluster_cause_baseline.csv",
            health_data_dir / "cause_log_breakpoints.csv",
            health_data_dir / "cluster_summary.csv",
            clusters_path,
        ]

        if all(f.exists() for f in required_health_files):
            risk_factors = config["health"]["risk_factors"]
            risk_cause_map = config["health"]["risk_cause_map"]
            value_per_yll = float(config["health"]["value_per_yll"])
            add_health_objective(
                n,
                str(health_data_dir / "risk_breakpoints.csv"),
                str(health_data_dir / "cluster_cause_baseline.csv"),
                str(health_data_dir / "cause_log_breakpoints.csv"),
                str(health_data_dir / "cluster_summary.csv"),
                str(clusters_path),
                str(population_path),
                risk_factors,
                risk_cause_map,
                "gurobi",
                value_per_yll,
            )
            logger.info("Added health objective (value_per_yll=%.2f)", value_per_yll)
        else:
            missing = [f for f in required_health_files if not f.exists()]
            logger.warning("Health data incomplete, skipping. Missing: %s", missing[:3])

    logger.info(
        "Model built: %d variables, %d constraints",
        n.model.nvars,
        n.model.ncons,
    )

    # Use linopy's to_gurobipy to build the native Gurobi model with SOS constraints
    logger.info("Building Gurobi model via linopy.Model.to_gurobipy()...")

    # Convert linopy model to gurobipy model (includes SOS constraints)
    gp_model = n.model.to_gurobipy()
    gp_model.update()

    # Check model characteristics
    num_sos = gp_model.NumSOS
    logger.info(
        "Gurobi model: %d vars (%d binary, %d integer), %d constraints, %d SOS constraints",
        gp_model.NumVars,
        gp_model.NumBinVars,
        gp_model.NumIntVars,
        gp_model.NumConstrs,
        num_sos,
    )

    if gp_model.NumBinVars == 0 and gp_model.NumIntVars == 0:
        if num_sos > 0:
            logger.info("Model type: LP with SOS constraints (not a MIP)")
            logger.info(
                "Note: Gurobi handles SOS2 natively. The model will solve as "
                "a continuous problem with SOS branching."
            )
        else:
            logger.info("Model type: Pure LP (no integer variables or SOS constraints)")
    else:
        logger.info("Model type: MIP")

    # Export using Gurobi's native MPS writer
    logger.info("Exporting to %s", output_path)
    gp_model.write(str(output_path))

    logger.info("Export complete: %s", output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Export GLADE model to MPS format for Gurobi tuning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
    # First build the network:
    tools/smk -e gurobi -j4 --configfile config/yll.yaml -- results/yll/build/model_scen-yll_20000.nc

    # Then export for tuning:
    pixi run -e gurobi python tools/export_model.py --config config/yll.yaml --scenario yll_20000

    # Run tuning:
    pixi run -e gurobi python tools/tune_model.py results/yll/exported/model_scen-yll_20000.mps
        """,
    )
    parser.add_argument(
        "--config",
        "-c",
        required=True,
        help="Path to config file (e.g., config/yll.yaml)",
    )
    parser.add_argument(
        "--scenario",
        "-s",
        required=True,
        help="Scenario name (e.g., yll_20000)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=(
            "Output MPS file path "
            "(default: {paths.results_root}/{name}/exported/model_scen-{scenario}.mps)"
        ),
    )

    args = parser.parse_args()

    output_path = build_and_export_model(
        args.config,
        args.scenario,
        args.output,
    )

    logger.info("\nNext steps:")
    logger.info("  pixi run -e gurobi python tools/tune_model.py %s", output_path)


if __name__ == "__main__":
    main()
