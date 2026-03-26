# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for sensitivity analysis methods (PCE, RF, etc.).

Functions extracted here are used by both the PCE and RF sensitivity
analysis scripts.
"""

from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
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


def _read_column_sum(path: Path, column: str) -> float:
    """Read a single column from a parquet file and return its sum.

    Uses pyarrow directly to avoid materializing a full pandas DataFrame,
    which matters for large files like land_use.parquet (~52k rows, ~12 MB
    decompressed).  Over thousands of scenarios the avoided allocations
    prevent significant memory accumulation from allocator fragmentation.
    """
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    schema = pq.read_schema(path)
    if column not in schema.names:
        return np.nan
    table = pq.read_table(path, columns=[column])
    if table.num_rows == 0:
        return np.nan
    return float(table.column(column).to_numpy().sum())


def _read_row_sum(path: Path) -> float:
    """Read a single-row parquet and return the sum of all columns."""
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    table = pq.read_table(path)
    if table.num_rows == 0:
        return np.nan
    return float(sum(table.column(i)[0].as_py() for i in range(table.num_columns)))


def _read_yll(path: Path) -> float:
    """Read the YLL total, trying column names in priority order."""
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    schema = pq.read_schema(path)
    col_names = schema.names
    for candidate in ("yll_myll", "yll_million", "total_yll"):
        if candidate in col_names:
            return _read_column_sum(path, candidate)
    return np.nan


def load_scenario_outputs(
    analysis_dir: Path,
    scenario_names: list[str],
) -> "pd.DataFrame":
    """Load and aggregate scalar outputs from sensitivity scenarios.

    Reads only the needed columns via pyarrow to avoid materializing full
    DataFrames (land_use.parquet alone is ~12 MB per scenario).

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
    import pandas as pd

    scenarios = np.empty(len(scenario_names), dtype=object)
    total_cost = np.empty(len(scenario_names))
    ghg_emissions = np.empty(len(scenario_names))
    land_use = np.empty(len(scenario_names))
    yll = np.empty(len(scenario_names))

    for i, scenario_name in enumerate(scenario_names):
        d = analysis_dir / f"scen-{scenario_name}"
        scenarios[i] = scenario_name
        total_cost[i] = _read_row_sum(d / "objective_breakdown.parquet")
        ghg_emissions[i] = _read_column_sum(d / "net_emissions.parquet", "mtco2eq")
        land_use[i] = _read_column_sum(d / "land_use.parquet", "area_mha")
        yll[i] = _read_yll(d / "health_totals.parquet")

    return pd.DataFrame(
        {
            "scenario": scenarios,
            "total_cost": total_cost,
            "ghg_emissions": ghg_emissions,
            "land_use": land_use,
            "yll": yll,
        }
    )
