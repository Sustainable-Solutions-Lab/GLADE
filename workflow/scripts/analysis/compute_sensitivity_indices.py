# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute global sensitivity indices from ensemble scenario runs.

This script aggregates outputs from Sobol-sampled scenarios and computes
first-order (S1) and total-order (ST) Sobol sensitivity indices using SALib.

The analysis reveals which uncertain parameters contribute most to variance
in key model outputs:
- Total system cost (billion USD)
- GHG emissions (Mt CO2-eq)
- Total land use (Mha)
- Years of life lost (million YLL)

Requirements:
- Scenarios must be generated using sobol mode in scenario_generators.py
- Per-scenario analysis CSVs must exist (objective_breakdown, ghg_totals, etc.)
- Parameter bounds must match those used for sampling
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from SALib.analyze import sobol as salib_analyze

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def load_scenario_outputs(
    analysis_dir: Path,
    scenario_names: list[str],
) -> pd.DataFrame:
    """Load and aggregate outputs from Sobol-sampled scenarios.

    Parameters
    ----------
    analysis_dir : Path
        Base analysis directory (results/{name}/analysis/)
    scenario_names : list[str]
        Ordered Sobol scenario names. Order must match sample generation order.

    Returns
    -------
    pd.DataFrame
        DataFrame with one row per scenario and columns for each output metric
    """
    outputs = []

    for scenario_name in scenario_names:
        scenario_dir = analysis_dir / f"scen-{scenario_name}"

        if not scenario_dir.exists():
            raise FileNotFoundError(
                f"Missing scenario directory: {scenario_dir}. "
                f"Expected scenario outputs for all scenarios: {scenario_names}"
            )

        row = {"scenario": scenario_name}

        # Load objective breakdown for total cost
        obj_path = scenario_dir / "objective_breakdown.csv"
        if obj_path.exists():
            obj_df = pd.read_csv(obj_path, index_col=0)
            # Sum total cost across all categories
            row["total_cost"] = obj_df["total_bnusd"].sum()
        else:
            row["total_cost"] = np.nan

        # Load GHG totals
        ghg_path = scenario_dir / "ghg_totals.csv"
        if ghg_path.exists():
            ghg_df = pd.read_csv(ghg_path)
            if "ghg_mtco2eq" in ghg_df.columns:
                row["ghg_emissions"] = ghg_df["ghg_mtco2eq"].sum()
            elif "ghg_total_mt_co2eq" in ghg_df.columns:
                row["ghg_emissions"] = ghg_df["ghg_total_mt_co2eq"].sum()
            else:
                row["ghg_emissions"] = np.nan
        else:
            row["ghg_emissions"] = np.nan

        # Load land use totals
        land_path = scenario_dir / "land_use.csv"
        if land_path.exists():
            land_df = pd.read_csv(land_path)
            if "area_mha" in land_df.columns:
                row["land_use"] = land_df["area_mha"].sum()
            else:
                row["land_use"] = np.nan
        else:
            row["land_use"] = np.nan

        # Load health totals
        health_path = scenario_dir / "health_totals.csv"
        if health_path.exists():
            health_df = pd.read_csv(health_path)
            if "yll_myll" in health_df.columns:
                row["yll"] = health_df["yll_myll"].sum()
            elif "yll_million" in health_df.columns:
                row["yll"] = health_df["yll_million"].sum()
            elif "total_yll" in health_df.columns:
                row["yll"] = health_df["total_yll"].sum()
            else:
                row["yll"] = np.nan
        else:
            row["yll"] = np.nan

        outputs.append(row)

    return pd.DataFrame(outputs)


def compute_sobol_indices(
    outputs: pd.DataFrame,
    problem: dict,
    output_columns: list[str],
) -> pd.DataFrame:
    """Compute Sobol sensitivity indices for model outputs.

    Parameters
    ----------
    outputs : pd.DataFrame
        Scenario outputs with one row per sample
    problem : dict
        SALib problem definition with 'num_vars', 'names', 'bounds'
    output_columns : list[str]
        Output columns to analyze

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: output, parameter, S1, S1_conf, ST, ST_conf
    """
    results = []

    for col in output_columns:
        y = outputs[col].values

        # Skip if any NaN values
        if np.isnan(y).any():
            logger.warning("Skipping output '%s' due to NaN values", col)
            continue

        # Compute Sobol indices
        si = salib_analyze.analyze(
            problem,
            y,
            calc_second_order=False,
            print_to_console=False,
        )

        for i, param_name in enumerate(problem["names"]):
            results.append(
                {
                    "output": col,
                    "parameter": param_name,
                    "S1": si["S1"][i],
                    "S1_conf": si["S1_conf"][i],
                    "ST": si["ST"][i],
                    "ST_conf": si["ST_conf"][i],
                }
            )

    return pd.DataFrame(results)


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])

    # Get parameters from snakemake
    analysis_dir = Path(snakemake.params.analysis_dir)
    scenario_prefix = snakemake.params.scenario_prefix
    scenario_names = list(snakemake.params.scenario_names)
    base_samples = snakemake.params.base_samples
    parameter_bounds = snakemake.params.parameter_bounds

    # Build SALib problem definition
    param_names = list(parameter_bounds.keys())
    bounds = [
        [parameter_bounds[k]["min"], parameter_bounds[k]["max"]] for k in param_names
    ]

    problem = {
        "num_vars": len(param_names),
        "names": param_names,
        "bounds": bounds,
    }

    # Sobol sampling generates N*(D+2) samples for D parameters (without second-order)
    n_vars = len(param_names)
    expected_samples = base_samples * (n_vars + 2)
    if len(scenario_names) != expected_samples:
        raise ValueError(
            f"Sobol sample count mismatch: generator implies {expected_samples} samples "
            f"(N={base_samples}, D={n_vars}), but found {len(scenario_names)} scenarios "
            f"with prefix '{scenario_prefix}'"
        )

    logger.info(
        "Loading outputs from %d Sobol scenarios (prefix: '%s')",
        len(scenario_names),
        scenario_prefix,
    )

    # Load scenario outputs
    outputs = load_scenario_outputs(analysis_dir, scenario_names)
    logger.info("Loaded outputs for %d scenarios", len(outputs))

    # Compute Sobol indices
    output_columns = ["total_cost", "ghg_emissions", "land_use", "yll"]
    available_columns = [c for c in output_columns if c in outputs.columns]

    if not available_columns:
        raise ValueError("No valid output columns found for sensitivity analysis")

    indices = compute_sobol_indices(outputs, problem, available_columns)

    # Write results
    output_path = Path(snakemake.output.indices)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indices.to_csv(output_path, index=False)
    logger.info("Wrote Sobol indices to %s", output_path)

    # Log summary
    for output in available_columns:
        output_indices = indices[indices["output"] == output]
        logger.info("Sensitivity indices for %s:", output)
        for _, row in output_indices.iterrows():
            logger.info(
                "  %s: S1=%.3f (%.3f), ST=%.3f (%.3f)",
                row["parameter"],
                row["S1"],
                row["S1_conf"],
                row["ST"],
                row["ST_conf"],
            )


if __name__ == "__main__":
    main()
