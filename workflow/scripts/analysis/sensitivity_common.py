# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for sensitivity analysis methods (PCE, RF, etc.).

Functions extracted here are used by both the PCE and RF sensitivity
analysis scripts.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats.qmc import Sobol

from workflow.scenario_generators import build_joint_distribution


def reconstruct_samples(generator_spec: dict) -> np.ndarray:
    """Regenerate the Sobol design matrix from the generator spec.

    Deterministic given the same seed and sample count.

    Parameters
    ----------
    generator_spec : dict
        Generator specification with parameters, samples, and seed.

    Returns
    -------
    np.ndarray
        N x D matrix in physical parameter space.
    """
    param_names = list(generator_spec["parameters"].keys())
    d = len(param_names)
    n_samples = generator_spec["samples"]
    seed = generator_spec.get("seed", 42)

    joint_dist, _ = build_joint_distribution(generator_spec)

    sampler = Sobol(d, scramble=True, seed=seed)
    unit_samples = sampler.random(n_samples)
    physical_samples = joint_dist.inv(unit_samples.T)  # shape (d, n_samples)
    return physical_samples.T  # shape (n_samples, d)


def load_scenario_outputs(
    analysis_dir: Path,
    scenario_names: list[str],
) -> pd.DataFrame:
    """Load and aggregate outputs from sensitivity scenarios.

    Parameters
    ----------
    analysis_dir : Path
        Base analysis directory (results/{name}/analysis/)
    scenario_names : list[str]
        Ordered scenario names matching sample generation order.

    Returns
    -------
    pd.DataFrame
        DataFrame with one row per scenario and columns for each output metric.
    """
    outputs = []

    for scenario_name in scenario_names:
        scenario_dir = analysis_dir / f"scen-{scenario_name}"
        row = {"scenario": scenario_name}

        # Load objective breakdown for total cost
        obj_path = scenario_dir / "objective_breakdown.csv"
        if obj_path.exists():
            obj_df = pd.read_csv(obj_path)
            row["total_cost"] = obj_df.iloc[0].sum()
        else:
            row["total_cost"] = np.nan

        # Load net emissions
        ghg_path = scenario_dir / "net_emissions.csv"
        if ghg_path.exists():
            ghg_df = pd.read_csv(ghg_path)
            total_row = ghg_df[ghg_df["gas"] == "total"]
            if not total_row.empty:
                row["ghg_emissions"] = total_row["net_mtco2eq"].iloc[0]
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
