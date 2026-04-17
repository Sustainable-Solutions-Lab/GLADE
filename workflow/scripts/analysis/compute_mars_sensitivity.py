# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""MARS-based global sensitivity analysis.

Fits Multivariate Adaptive Regression Splines (MARS/Earth) to model
outputs and estimates Sobol sensitivity indices via Monte Carlo
integration using the Saltelli (2010) pick-freeze scheme.

MARS is a natural surrogate for LP response surfaces: the piecewise-
linear basis functions can represent kinks from optimal-basis changes
exactly, while producing smoother predictions than tree ensembles.

The Sobol estimation routines are shared with the RF method (they are
model-agnostic, requiring only a ``.predict()`` method).
"""

from itertools import product
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.compute_rf_sensitivity import (
    conditional_sobol_rf_batch,
    sobol_from_rf,
)
from workflow.scripts.analysis.mars import Earth
from workflow.scripts.analysis.sensitivity_common import (
    load_scenario_outputs,
    reconstruct_samples,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


class _LogTransformWrapper:
    """Wrapper that fits in log(1+y) space but predicts in original space.

    The Sobol MC routines call ``.predict()`` on this wrapper, so indices
    are computed on the back-transformed (original-scale) output.
    """

    def __init__(self, model: Earth):
        self.model = model

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.expm1(self.model.predict(X))


def fit_mars(
    x_design: np.ndarray,
    y: np.ndarray,
    max_terms: int = 50,
    max_degree: int = 2,
    penalty: float = 3.0,
    n_knots: int = 25,
) -> dict:
    """Fit a MARS regressor with GCV-based model selection.

    Parameters
    ----------
    x_design : np.ndarray
        Design matrix, shape (N, D) in physical parameter space.
    y : np.ndarray
        Output values, shape (N,).
    max_terms : int
        Maximum basis functions in the forward pass.
    max_degree : int
        Maximum interaction degree (1 = additive, 2 = pairwise).
    penalty : float
        GCV penalty parameter.
    n_knots : int
        Candidate knots per variable.

    Returns
    -------
    dict
        model: fitted Earth instance
        validation_error: GCV-based error estimate
        r2: training R²
        n_basis: number of basis functions after pruning
        n_samples: number of training samples
    """
    model = Earth(
        max_terms=max_terms,
        max_degree=max_degree,
        penalty=penalty,
        n_knots=n_knots,
    )
    model.fit(x_design, y)

    r2 = model.score(x_design, y)

    return {
        "model": model,
        "validation_error": model.gcv_,
        "r2": r2,
        "n_basis": len(model.basis_),
        "n_samples": len(y),
    }


def run(snakemake) -> None:
    logger = setup_script_logging(snakemake.log[0])

    n_threads = snakemake.threads
    from threadpoolctl import threadpool_limits

    threadpool_limits(limits=n_threads)
    logger.info("Thread limit set to %d", n_threads)

    analysis_dir = Path(snakemake.output.global_indices).parent
    scenario_names = list(snakemake.params.scenario_names)
    generator_spec = dict(snakemake.params.generator_spec)
    slice_grid = dict(snakemake.params.slice_grid)
    holdout_fraction = float(snakemake.params.holdout_fraction)

    logger.info("Using analysis directory: %s", analysis_dir)

    # Build joint distribution and get parameter names
    joint_dist, param_names = build_joint_distribution(generator_spec)
    n_params = len(param_names)

    # Identify slice parameter indices
    slice_param_names = generator_spec.get("slice_parameters", [])
    slice_indices = [param_names.index(sp) for sp in slice_param_names]

    # Read MARS hyperparameters from method config
    method_config = dict(snakemake.params.method_config)
    method_options = method_config.get("method_options", {})
    max_terms = method_options.get("max_terms", 50)
    max_degree = method_options.get("max_degree", 2)
    penalty = method_options.get("penalty", 3.0)
    n_knots = method_options.get("n_knots", 25)
    n_mc_global = method_options.get("n_mc_global", 2**14)
    n_mc_conditional = method_options.get("n_mc_conditional", 2**13)
    log_transform_outputs = set(method_options.get("log_transform", []))

    logger.info(
        "MARS sensitivity analysis: %d parameters, %d samples, %d slice parameters",
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

    # Train/test split for holdout validation.
    n_total = len(x_design)
    n_holdout = int(n_total * holdout_fraction)
    n_train = n_total - n_holdout
    x_train = x_design[:n_train]
    x_test = x_design[n_train:] if n_holdout > 0 else None
    outputs_train = outputs_df.iloc[:n_train]
    outputs_test = outputs_df.iloc[n_train:] if n_holdout > 0 else None
    logger.info(
        "Train/test split: %d train, %d holdout (%.0f%%)",
        n_train,
        n_holdout,
        holdout_fraction * 100,
    )

    logger.info("Analyzing outputs: %s", available_columns)

    # Fit MARS and compute indices for each output
    global_rows = []
    validation_rows = []
    conditional_rows = []
    conditional_joint_rows = []

    for col in available_columns:
        y_train_raw = outputs_train[col].values
        use_log = col in log_transform_outputs

        # Apply log transform if configured for this output
        if use_log:
            y_train = np.log1p(y_train_raw)
            logger.info("Using log(1+y) transform for output '%s'", col)
        else:
            y_train = y_train_raw

        # Fit MARS on (possibly transformed) training data
        mars_result = fit_mars(
            x_train,
            y_train,
            max_terms=max_terms,
            max_degree=max_degree,
            penalty=penalty,
            n_knots=n_knots,
        )

        # Build the prediction model: wrap with back-transform if needed
        if use_log:
            sobol_model = _LogTransformWrapper(mars_result["model"])
        else:
            sobol_model = mars_result["model"]

        # Compute holdout error if we have a test set (always in original space)
        if x_test is not None and outputs_test is not None:
            y_test = outputs_test[col].values
            y_pred_test = sobol_model.predict(x_test)
            ss_res_test = np.sum((y_test - y_pred_test) ** 2)
            ss_tot_test = np.sum((y_test - np.mean(y_test)) ** 2)
            r2_test = 1 - ss_res_test / ss_tot_test if ss_tot_test > 0 else 0.0
            holdout_error = 1 - r2_test
        else:
            r2_test = None
            holdout_error = None

        # Use holdout error as the primary validation metric when available,
        # otherwise fall back to GCV
        gcv_error = mars_result["validation_error"]
        validation_error = holdout_error if holdout_error is not None else gcv_error

        logger.info(
            "MARS for %s: R²_train=%.4f, R²_test=%s, %d basis functions%s",
            col,
            mars_result["r2"],
            f"{r2_test:.4f}" if r2_test is not None else "N/A",
            mars_result["n_basis"],
            " (log-transformed)" if use_log else "",
        )

        if validation_error > 0.1:
            logger.warning(
                "High validation error (%.3f) for output '%s' -- MARS may be inaccurate",
                validation_error,
                col,
            )

        # Validation metrics
        validation_rows.append(
            {
                "output": col,
                "validation_error": validation_error,
                "gcv": gcv_error,
                "r2_train": mars_result["r2"],
                "r2_test": r2_test,
                "n_train": n_train,
                "n_test": n_holdout,
                "method": "mars",
                "n_basis": mars_result["n_basis"],
            }
        )

        # Global Sobol indices (reuse RF's model-agnostic Saltelli MC)
        s1, s_total = sobol_from_rf(
            sobol_model,
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
            # Individual conditioning
            for sp_idx, sp_name in zip(slice_indices, slice_param_names):
                grid_values = [[v] for v in slice_grid[sp_name]]
                indiv_results = conditional_sobol_rf_batch(
                    sobol_model,
                    joint_dist,
                    n_params,
                    [sp_idx],
                    grid_values,
                    n_mc=n_mc_conditional,
                )

                for sp_val_list, (s1_c, st_c, cond_var) in zip(
                    grid_values, indiv_results
                ):
                    sp_val = sp_val_list[0]
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

            # Joint conditioning
            joint_value_lists = [slice_grid[sp_name] for sp_name in slice_param_names]
            grid_points = [list(vals) for vals in product(*joint_value_lists)]

            batch_results = conditional_sobol_rf_batch(
                sobol_model,
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
