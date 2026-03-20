# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Random Forest-based global sensitivity analysis.

Fits Random Forest regressors to model outputs and estimates Sobol
sensitivity indices via Monte Carlo integration using the Saltelli
(2010) pick-freeze scheme. Handles non-smooth, non-linear responses
naturally at the cost of MC estimation noise.

The implementation is parameter-agnostic: parameter names, distributions,
and slice variable designations are all read from the generator spec.
"""

from itertools import product
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.sensitivity_common import (
    load_scenario_outputs,
    reconstruct_samples,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def fit_random_forest(
    x_design: np.ndarray,
    y: np.ndarray,
    n_estimators: int = 500,
    random_state: int = 42,
) -> dict:
    """Fit a Random Forest regressor with OOB validation.

    Parameters
    ----------
    x_design : np.ndarray
        Design matrix, shape (N, D) in physical parameter space.
    y : np.ndarray
        Output values, shape (N,).
    n_estimators : int
        Number of trees in the forest.
    random_state : int
        Random seed for reproducibility.

    Returns
    -------
    dict
        model: fitted RandomForestRegressor
        validation_error: 1 - OOB R² (analogous to LOO error)
        r2: OOB R² score
        n_estimators: number of trees used
        n_samples: number of training samples
    """
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        oob_score=True,
        n_jobs=-1,
        random_state=random_state,
    )
    rf.fit(x_design, y)

    oob_r2 = rf.oob_score_
    validation_error = 1.0 - oob_r2

    return {
        "model": rf,
        "validation_error": validation_error,
        "r2": oob_r2,
        "n_estimators": n_estimators,
        "n_samples": len(y),
    }


def sobol_from_rf(
    model: RandomForestRegressor,
    distribution,
    n_params: int,
    n_mc: int = 2**14,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate Sobol indices via Saltelli pick-freeze Monte Carlo.

    Parameters
    ----------
    model : RandomForestRegressor
        Fitted RF model.
    distribution : chaospy.Distribution
        Joint distribution for the parameters.
    n_params : int
        Number of parameters.
    n_mc : int
        Number of Monte Carlo samples per matrix.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        S1 (first-order) and ST (total-order) indices, each shape (n_params,).
    """
    rng = np.random.default_rng(seed)

    # Sample two independent matrices A, B in unit hypercube
    u_a = rng.random((n_mc, n_params))
    u_b = rng.random((n_mc, n_params))

    # Transform to physical space
    x_a = distribution.inv(u_a.T).T  # (n_mc, n_params)
    x_b = distribution.inv(u_b.T).T

    f_a = model.predict(x_a)
    f_b = model.predict(x_b)

    # Total variance from combined A, B samples
    f_all = np.concatenate([f_a, f_b])
    var_y = np.var(f_all)

    s1 = np.zeros(n_params)
    s_total = np.zeros(n_params)

    if var_y <= 0:
        return s1, s_total

    for i in range(n_params):
        # AB_i: A with column i replaced by B's column i
        x_ab_i = x_a.copy()
        x_ab_i[:, i] = x_b[:, i]
        f_ab_i = model.predict(x_ab_i)

        # Saltelli (2010) estimators
        s1[i] = np.mean(f_b * (f_ab_i - f_a)) / var_y
        s_total[i] = 0.5 * np.mean((f_a - f_ab_i) ** 2) / var_y

    # Clip S1 to [0, 1] (MC noise can produce small negatives)
    np.clip(s1, 0.0, 1.0, out=s1)

    return s1, s_total


def conditional_sobol_rf(
    model: RandomForestRegressor,
    distribution,
    n_params: int,
    slice_indices: list[int],
    slice_values: list[float],
    n_mc: int = 2**13,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Estimate conditional Sobol indices by fixing slice parameters.

    Parameters
    ----------
    model : RandomForestRegressor
        Fitted RF model.
    distribution : chaospy.Distribution
        Joint distribution for the parameters.
    n_params : int
        Total number of parameters (including slice params).
    slice_indices : list[int]
        Indices of slice parameters to fix.
    slice_values : list[float]
        Values to fix the slice parameters at.
    n_mc : int
        Number of Monte Carlo samples per matrix.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    tuple[np.ndarray, np.ndarray, float]
        S1_cond, ST_cond (shape n_params; slice param entries are 0),
        and conditional_variance.
    """
    non_slice = sorted(set(range(n_params)) - set(slice_indices))
    n_free = len(non_slice)

    rng = np.random.default_rng(seed)

    # Sample A, B for free parameters only, from their marginals
    u_a_free = rng.random((n_mc, n_free))
    u_b_free = rng.random((n_mc, n_free))

    # Transform free parameters via marginal inverse CDF
    x_a_free = np.empty_like(u_a_free)
    x_b_free = np.empty_like(u_b_free)
    for j, orig_idx in enumerate(non_slice):
        marginal = distribution[orig_idx]
        x_a_free[:, j] = marginal.inv(u_a_free[:, j])
        x_b_free[:, j] = marginal.inv(u_b_free[:, j])

    # Embed into full parameter space
    def _embed(x_free):
        x_full = np.empty((x_free.shape[0], n_params))
        for j, orig_idx in enumerate(non_slice):
            x_full[:, orig_idx] = x_free[:, j]
        for s_idx, s_val in zip(slice_indices, slice_values):
            x_full[:, s_idx] = s_val
        return x_full

    x_a = _embed(x_a_free)
    x_b = _embed(x_b_free)

    f_a = model.predict(x_a)
    f_b = model.predict(x_b)

    # Conditional variance
    f_all = np.concatenate([f_a, f_b])
    cond_var = float(np.var(f_all))

    s1_cond = np.zeros(n_params)
    st_cond = np.zeros(n_params)

    if cond_var <= 0:
        return s1_cond, st_cond, cond_var

    # Saltelli estimator on free parameters
    for j, orig_idx in enumerate(non_slice):
        x_ab_free = x_a_free.copy()
        x_ab_free[:, j] = x_b_free[:, j]
        x_ab = _embed(x_ab_free)
        f_ab = model.predict(x_ab)

        s1_cond[orig_idx] = np.mean(f_b * (f_ab - f_a)) / cond_var
        st_cond[orig_idx] = 0.5 * np.mean((f_a - f_ab) ** 2) / cond_var

    np.clip(s1_cond, 0.0, 1.0, out=s1_cond)

    return s1_cond, st_cond, cond_var


def conditional_sobol_rf_batch(
    model: RandomForestRegressor,
    distribution,
    n_params: int,
    slice_indices: list[int],
    slice_value_grid: list[list[float]],
    n_mc: int = 2**13,
    seed: int = 0,
    batch_size: int = 500,
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Batch-estimate conditional Sobol indices across a grid of slice values.

    Reuses the same A/B free-parameter matrices across all grid points
    for efficiency. Processes grid points in batches to reduce the number
    of model.predict() calls.

    Parameters
    ----------
    model : RandomForestRegressor
        Fitted RF model.
    distribution : chaospy.Distribution
        Joint distribution for the parameters.
    n_params : int
        Total number of parameters.
    slice_indices : list[int]
        Indices of slice parameters.
    slice_value_grid : list[list[float]]
        List of slice value vectors; each is a list of length len(slice_indices).
    n_mc : int
        Number of Monte Carlo samples per matrix.
    seed : int
        Random seed.
    batch_size : int
        Number of grid points per predict() call.

    Returns
    -------
    list[tuple[np.ndarray, np.ndarray, float]]
        One (S1_cond, ST_cond, cond_var) tuple per grid point.
    """
    non_slice = sorted(set(range(n_params)) - set(slice_indices))
    n_free = len(non_slice)
    n_grid = len(slice_value_grid)

    rng = np.random.default_rng(seed)

    # Sample A, B once for all grid points
    u_a_free = rng.random((n_mc, n_free))
    u_b_free = rng.random((n_mc, n_free))

    x_a_free = np.empty_like(u_a_free)
    x_b_free = np.empty_like(u_b_free)
    for j, orig_idx in enumerate(non_slice):
        marginal = distribution[orig_idx]
        x_a_free[:, j] = marginal.inv(u_a_free[:, j])
        x_b_free[:, j] = marginal.inv(u_b_free[:, j])

    def _embed_batch(x_free, slice_vals_batch):
        """Embed free params into full space for a batch of grid points.

        Returns shape (len(batch) * n_mc, n_params).
        """
        n_batch = len(slice_vals_batch)
        x_tiled = np.tile(x_free, (n_batch, 1))  # (n_batch * n_mc, n_free)
        x_full = np.empty((n_batch * n_mc, n_params))
        for j, orig_idx in enumerate(non_slice):
            x_full[:, orig_idx] = x_tiled[:, j]
        for k, s_idx in enumerate(slice_indices):
            vals = np.repeat([sv[k] for sv in slice_vals_batch], n_mc)
            x_full[:, s_idx] = vals
        return x_full

    results = [None] * n_grid

    for batch_start in range(0, n_grid, batch_size):
        batch_end = min(batch_start + batch_size, n_grid)
        batch_slice_vals = slice_value_grid[batch_start:batch_end]
        n_batch = len(batch_slice_vals)

        # Predict f_A and f_B for this batch
        x_a_batch = _embed_batch(x_a_free, batch_slice_vals)
        x_b_batch = _embed_batch(x_b_free, batch_slice_vals)
        f_a_batch = model.predict(x_a_batch).reshape(n_batch, n_mc)
        f_b_batch = model.predict(x_b_batch).reshape(n_batch, n_mc)

        # Conditional variance per grid point
        f_all = np.concatenate([f_a_batch, f_b_batch], axis=1)  # (n_batch, 2*n_mc)
        cond_vars = np.var(f_all, axis=1)  # (n_batch,)

        # Sobol indices per free parameter
        s1_batch = np.zeros((n_batch, n_params))
        st_batch = np.zeros((n_batch, n_params))

        for j, orig_idx in enumerate(non_slice):
            # Build AB_i: A with free column j from B
            x_ab_free = x_a_free.copy()
            x_ab_free[:, j] = x_b_free[:, j]
            x_ab_batch = _embed_batch(x_ab_free, batch_slice_vals)
            f_ab_batch = model.predict(x_ab_batch).reshape(n_batch, n_mc)

            # Saltelli estimators (vectorized over grid points)
            with np.errstate(divide="ignore", invalid="ignore"):
                s1_vals = (
                    np.mean(f_b_batch * (f_ab_batch - f_a_batch), axis=1) / cond_vars
                )
                st_vals = (
                    0.5 * np.mean((f_a_batch - f_ab_batch) ** 2, axis=1) / cond_vars
                )
            s1_vals = np.where(cond_vars > 0, s1_vals, 0.0)
            st_vals = np.where(cond_vars > 0, st_vals, 0.0)

            s1_batch[:, orig_idx] = s1_vals
            st_batch[:, orig_idx] = st_vals

        np.clip(s1_batch, 0.0, 1.0, out=s1_batch)

        for i in range(n_batch):
            results[batch_start + i] = (
                s1_batch[i],
                st_batch[i],
                float(cond_vars[i]),
            )

    return results


def run(snakemake) -> None:
    logger = setup_script_logging(snakemake.log[0])

    analysis_dir = Path(snakemake.output.global_indices).parent
    scenario_names = list(snakemake.params.scenario_names)
    generator_spec = dict(snakemake.params.generator_spec)
    slice_grid = dict(snakemake.params.slice_grid)

    logger.info("Using analysis directory: %s", analysis_dir)

    # Build joint distribution and get parameter names
    joint_dist, param_names = build_joint_distribution(generator_spec)
    n_params = len(param_names)

    # Identify slice parameter indices
    slice_param_names = generator_spec.get("slice_parameters", [])
    slice_indices = [param_names.index(sp) for sp in slice_param_names]

    # Read optional RF hyperparameters
    method_options = generator_spec.get("method_options", {})
    n_estimators = method_options.get("n_estimators", 500)
    n_mc_global = method_options.get("n_mc_global", 2**14)
    n_mc_conditional = method_options.get("n_mc_conditional", 2**13)

    logger.info(
        "RF sensitivity analysis: %d parameters, %d samples, %d slice parameters",
        n_params,
        len(scenario_names),
        len(slice_param_names),
    )

    # Reconstruct full design matrix, then select rows for available scenarios.
    x_design_full = reconstruct_samples(generator_spec)
    prefix = generator_spec["name"].removesuffix("{sample_id}")
    sample_indices = np.array([int(s.removeprefix(prefix)) for s in scenario_names])
    x_design = x_design_full[sample_indices]
    logger.info(
        "Using %d/%d scenarios (%.0f%% available)",
        len(scenario_names),
        x_design_full.shape[0],
        100 * len(scenario_names) / x_design_full.shape[0],
    )

    # Load scenario outputs
    outputs_df = load_scenario_outputs(analysis_dir, scenario_names)
    logger.info("Loaded outputs for %d scenarios", len(outputs_df))

    # Drop scenarios with failed solves (any NaN across all output columns)
    output_columns = ["total_cost", "ghg_emissions", "land_use", "yll"]
    existing_output_cols = [c for c in output_columns if c in outputs_df.columns]
    failed_mask = outputs_df[existing_output_cols].isna().any(axis=1)
    n_failed = failed_mask.sum()
    if n_failed > 0:
        failed_scenarios = outputs_df.loc[failed_mask, "scenario"].tolist()
        logger.warning(
            "Dropping %d failed scenarios (empty outputs): %s",
            n_failed,
            failed_scenarios,
        )
        outputs_df = outputs_df[~failed_mask].reset_index(drop=True)
        x_design = x_design[~failed_mask.values]

    # Determine output columns to analyze
    available_columns = [
        c
        for c in output_columns
        if c in outputs_df.columns and not outputs_df[c].isna().any()
    ]

    if not available_columns:
        raise ValueError("No valid output columns found for sensitivity analysis")

    logger.info("Analyzing outputs: %s", available_columns)

    # Fit RF and compute indices for each output
    global_rows = []
    validation_rows = []
    conditional_rows = []
    conditional_joint_rows = []

    for col in available_columns:
        y = outputs_df[col].values

        # Fit Random Forest
        rf_result = fit_random_forest(x_design, y, n_estimators=n_estimators)

        logger.info(
            "RF for %s: OOB R²=%.4f, validation_error=%.4f, %d trees",
            col,
            rf_result["r2"],
            rf_result["validation_error"],
            rf_result["n_estimators"],
        )

        if rf_result["validation_error"] > 0.1:
            logger.warning(
                "High validation error (%.3f) for output '%s' -- RF may be inaccurate",
                rf_result["validation_error"],
                col,
            )

        # Validation metrics
        validation_rows.append(
            {
                "output": col,
                "validation_error": rf_result["validation_error"],
                "r2": rf_result["r2"],
                "n_samples": rf_result["n_samples"],
                "method": "rf",
                "n_estimators": rf_result["n_estimators"],
            }
        )

        # Global Sobol indices
        s1, s_total = sobol_from_rf(
            rf_result["model"],
            joint_dist,
            n_params,
            n_mc=n_mc_global,
        )

        for i, pname in enumerate(param_names):
            global_rows.append(
                {
                    "output": col,
                    "parameter": pname,
                    "S1": s1[i],
                    "ST": s_total[i],
                }
            )

        logger.info("Global Sobol indices for %s:", col)
        for i, pname in enumerate(param_names):
            logger.info("  %s: S1=%.3f, ST=%.3f", pname, s1[i], s_total[i])

        # Conditional Sobol indices (if slice parameters defined)
        if slice_indices and slice_grid:
            for sp_idx, sp_name in zip(slice_indices, slice_param_names):
                for sp_val in slice_grid[sp_name]:
                    s1_c, st_c, cond_var = conditional_sobol_rf(
                        rf_result["model"],
                        joint_dist,
                        n_params,
                        [sp_idx],
                        [sp_val],
                        n_mc=n_mc_conditional,
                    )

                    for i, pname in enumerate(param_names):
                        if i == sp_idx:
                            continue
                        conditional_rows.append(
                            {
                                "output": col,
                                "parameter": pname,
                                "S1_cond": s1_c[i],
                                "ST_cond": st_c[i],
                                "conditional_variance": cond_var,
                                sp_name: sp_val,
                            }
                        )

            # Joint conditioning: use batched computation for efficiency
            joint_value_lists = [slice_grid[sp_name] for sp_name in slice_param_names]
            grid_points = [list(vals) for vals in product(*joint_value_lists)]

            batch_results = conditional_sobol_rf_batch(
                rf_result["model"],
                joint_dist,
                n_params,
                slice_indices,
                grid_points,
                n_mc=n_mc_conditional,
            )

            for joint_values, (s1_c, st_c, cond_var) in zip(grid_points, batch_results):
                slice_value_map = dict(zip(slice_param_names, joint_values))
                for i, pname in enumerate(param_names):
                    if i in slice_indices:
                        continue

                    row = {
                        "output": col,
                        "parameter": pname,
                        "S1_cond": s1_c[i],
                        "ST_cond": st_c[i],
                        "conditional_variance": cond_var,
                    }
                    row.update(slice_value_map)
                    conditional_joint_rows.append(row)

    # Write output files
    global_df = pd.DataFrame(global_rows)
    global_path = Path(snakemake.output.global_indices)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_df.to_parquet(global_path)
    logger.info("Wrote global indices to %s", global_path)

    conditional_df = pd.DataFrame(conditional_rows)
    conditional_path = Path(snakemake.output.conditional_indices)
    conditional_df.to_parquet(conditional_path)
    logger.info("Wrote conditional indices to %s", conditional_path)

    conditional_joint_df = pd.DataFrame(conditional_joint_rows)
    conditional_joint_path = Path(snakemake.output.conditional_joint_indices)
    conditional_joint_df.to_parquet(conditional_joint_path)
    logger.info(
        "Wrote joint conditional indices to %s",
        conditional_joint_path,
    )

    validation_df = pd.DataFrame(validation_rows)
    validation_path = Path(snakemake.output.validation)
    validation_df.to_parquet(validation_path)
    logger.info("Wrote validation metrics to %s", validation_path)


if __name__ == "__main__":
    run(snakemake)
