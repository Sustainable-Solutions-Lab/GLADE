#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gurobi parameter tuning for food-opt models.

This script runs Gurobi's parameter tuning on an exported MPS model file.

Usage:
    # Step 1: Export the model to MPS by enabling export_for_tuning in config.
    # Add to your config file (e.g., config/sensitivity.yaml):
    #   solving:
    #     export_for_tuning: true
    #
    # Then run the solve (it will export the complete model before solving):
    tools/smk -e gurobi -j4 --configfile config/sensitivity.yaml -- \\
        results/sensitivity/solved/model_scen-ghg_0.nc

    # The MPS file is written next to the solved network:
    #   results/sensitivity/solved/model_scen-ghg_0.mps

    # Step 2: Run tuning on the exported MPS file:
    pixi run -e gurobi python tools/tune_model.py \\
        results/sensitivity/solved/model_scen-ghg_0.mps \\
        --time-limit 7200 \\
        --tune-trials 5 \\
        --tune-results 10 \\
        --tune-jobs 5

    # Optionally provide a .prm hint file to seed the search:
    pixi run -e gurobi python tools/tune_model.py \\
        results/sensitivity/solved/model_scen-ghg_0.mps \\
        --hint results/sensitivity/solved/tuning/hint.prm

The script will:
1. Load the MPS model into Gurobi
2. Apply hint parameters (if provided) as a starting point
3. Run Gurobi's parameter tuning (optionally in parallel)
4. Save optimal parameters to .prm files
"""

import argparse
import logging
from pathlib import Path

import gurobipy as gp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_tuning(
    mps_path: Path,
    output_dir: Path | None,
    time_limit: int,
    tune_trials: int,
    tune_results: int,
    threads: int | None,
    tune_jobs: int | None,
    hint_path: Path | None,
) -> Path | None:
    """Load MPS model and run Gurobi tuning."""
    if not mps_path.exists():
        raise FileNotFoundError(f"MPS file not found: {mps_path}")

    # Set output directory
    if output_dir is None:
        output_dir = mps_path.parent / "tuning"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load into Gurobi
    logger.info("Loading model from %s", mps_path)
    model = gp.read(str(mps_path))
    logger.info(
        "Model loaded: %d variables, %d constraints",
        model.NumVars,
        model.NumConstrs,
    )

    # Apply hint parameters as baseline for tuning
    if hint_path is not None:
        if not hint_path.exists():
            raise FileNotFoundError(f"Hint file not found: {hint_path}")
        logger.info("Loading hint parameters from %s", hint_path)
        with open(hint_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        param_name, param_value = parts
                        try:
                            model.setParam(param_name, int(param_value))
                        except (ValueError, TypeError):
                            try:
                                model.setParam(param_name, float(param_value))
                            except (ValueError, TypeError):
                                model.setParam(param_name, param_value)
                        logger.info("  Hint: %s = %s", param_name, param_value)

    # Configure tuning parameters
    model.setParam("TuneTimeLimit", time_limit)
    model.setParam("TuneTrials", tune_trials)
    model.setParam("TuneResults", tune_results)

    if threads is not None:
        model.setParam("Threads", threads)
        logger.info("Limiting to %d threads", threads)

    if tune_jobs is not None:
        model.setParam("TuneJobs", tune_jobs)
        logger.info("Running %d parallel tuning jobs", tune_jobs)

    # Run tuning
    logger.info(
        "Starting Gurobi tuning (time limit: %ds, trials: %d, results: %d)",
        time_limit,
        tune_trials,
        tune_results,
    )
    logger.info("This may take a while...")
    model.tune()

    # Get number of tuning results
    tune_result_count = model.tuneResultCount
    logger.info("Tuning complete. Found %d parameter sets.", tune_result_count)

    if tune_result_count == 0:
        logger.warning("No tuning results found")
        return None

    # Save all results
    base_name = mps_path.stem
    best_prm_path = None

    for i in range(tune_result_count):
        model.getTuneResult(i)
        prm_path = output_dir / f"{base_name}_tuned_{i}.prm"
        model.write(str(prm_path))
        logger.info("Result %d saved to %s", i, prm_path)
        if i == 0:
            best_prm_path = prm_path

    return best_prm_path


def main():
    parser = argparse.ArgumentParser(
        description="Run Gurobi parameter tuning on an exported MPS model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example workflow:

  # 1. Enable MPS export in your config file:
  #    solving:
  #      export_for_tuning: true

  # 2. Run the solve (exports MPS before solving):
  tools/smk -e gurobi -j4 --configfile config/sensitivity.yaml -- \\
      results/sensitivity/solved/model_scen-ghg_0.nc

  # 3. Run tuning (5 parallel jobs, starting from a hint):
  pixi run -e gurobi python tools/tune_model.py \\
      results/sensitivity/solved/model_scen-ghg_0.mps \\
      --tune-jobs 5 --hint hint.prm

  # 4. Apply tuned parameters by adding to your config:
  #    solving:
  #      options_gurobi:
  #        # Copy parameters from the .prm file
        """,
    )
    parser.add_argument(
        "mps_file",
        type=Path,
        help="Path to the MPS model file",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Output directory for tuning results (default: {mps_dir}/tuning/)",
    )
    parser.add_argument(
        "--time-limit",
        "-t",
        type=int,
        default=3600,
        help="Tuning time limit in seconds (default: 3600 = 1 hour)",
    )
    parser.add_argument(
        "--tune-trials",
        type=int,
        default=3,
        help="Number of trials per parameter set (default: 3)",
    )
    parser.add_argument(
        "--tune-results",
        type=int,
        default=5,
        help="Number of result sets to keep (default: 5)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Number of threads to use per solve (default: all available)",
    )
    parser.add_argument(
        "--tune-jobs",
        "-j",
        type=int,
        default=None,
        help="Number of parallel tuning jobs (default: 1, sequential)",
    )
    parser.add_argument(
        "--hint",
        type=Path,
        default=None,
        help="Path to a .prm file with parameters to seed the tuning search",
    )

    args = parser.parse_args()

    # Run tuning
    best_prm = run_tuning(
        args.mps_file,
        args.output_dir,
        args.time_limit,
        args.tune_trials,
        args.tune_results,
        args.threads,
        args.tune_jobs,
        args.hint,
    )

    if best_prm:
        logger.info("\n" + "=" * 60)
        logger.info("TUNING COMPLETE")
        logger.info("=" * 60)
        logger.info("Best parameters saved to: %s", best_prm)
        logger.info("\nTo use these parameters, add to your config file:")
        logger.info("  solving:")
        logger.info("    options_gurobi:")

        # Read and display the parameters
        with open(best_prm) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        logger.info("      %s: %s", parts[0], parts[1])


if __name__ == "__main__":
    main()
