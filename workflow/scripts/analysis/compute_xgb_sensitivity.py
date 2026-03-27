# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""XGBoost-based global sensitivity analysis.

Fits gradient-boosted tree regressors (XGBoost) to model outputs and
estimates Sobol sensitivity indices via Monte Carlo integration using
the Saltelli (2010) pick-freeze scheme.

XGBoost typically provides better fit quality than Random Forests due to
sequential error correction while maintaining the tree ensemble's
natural handling of non-smooth response surfaces.  Shallow trees with
many boosting rounds and subsampling give the best bias-variance
trade-off for surrogate modelling.

The Sobol estimation routines are shared with the RF method (they are
model-agnostic, requiring only a ``.predict()`` method).
"""

from itertools import product
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.compute_rf_sensitivity import (
    conditional_sobol_rf_batch,
    sobol_from_rf,
)
from workflow.scripts.analysis.sensitivity_common import (
    load_scenario_outputs,
    reconstruct_samples,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def fit_xgboost(
    x_design: np.ndarray,
    y: np.ndarray,
    x_val: np.ndarray | None = None,
    y_val: np.ndarray | None = None,
    n_estimators: int = 5000,
    max_depth: int = 4,
    learning_rate: float = 0.02,
    subsample: float = 0.8,
    colsample_bytree: float = 0.8,
    min_child_weight: int = 5,
    early_stopping_rounds: int = 50,
    n_jobs: int = 1,
    random_state: int = 42,
) -> dict:
    """Fit an XGBoost regressor with optional early stopping.

    Parameters
    ----------
    x_design : np.ndarray
        Design matrix, shape (N, D).
    y : np.ndarray
        Output values, shape (N,).
    x_val, y_val : np.ndarray, optional
        Validation set for early stopping. If not provided, trains for
        the full ``n_estimators`` rounds.
    n_estimators : int
        Maximum number of boosting rounds.
    max_depth : int
        Maximum tree depth. Shallow (3-5) works best for surrogates.
    learning_rate : float
        Step size shrinkage.
    subsample : float
        Row subsampling ratio per tree.
    colsample_bytree : float
        Column subsampling ratio per tree.
    min_child_weight : int
        Minimum sum of instance weight in a child.
    early_stopping_rounds : int
        Stop if validation score doesn't improve for this many rounds.
    n_jobs : int
        Number of parallel threads.
    random_state : int
        Random seed.

    Returns
    -------
    dict
        model: fitted XGBRegressor
        validation_error: 1 - best validation R² (or training error if
            no validation set)
        r2: training R²
        n_estimators: actual number of boosting rounds used
        n_samples: number of training samples
    """
    model = XGBRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
        min_child_weight=min_child_weight,
        n_jobs=n_jobs,
        random_state=random_state,
        tree_method="hist",
    )

    fit_kwargs = {"verbose": False}
    if x_val is not None and y_val is not None:
        model.set_params(early_stopping_rounds=early_stopping_rounds)
        fit_kwargs["eval_set"] = [(x_val, y_val)]

    model.fit(x_design, y, **fit_kwargs)

    # Training R²
    y_pred_train = model.predict(x_design)
    ss_res = np.sum((y - y_pred_train) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2_train = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    actual_rounds = (
        model.best_iteration + 1 if hasattr(model, "best_iteration") else n_estimators
    )

    return {
        "model": model,
        "r2": r2_train,
        "n_estimators": actual_rounds,
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

    # Read XGBoost hyperparameters from method config
    method_config = dict(snakemake.params.method_config)
    method_options = method_config.get("method_options", {})
    n_estimators = method_options.get("n_estimators", 5000)
    max_depth = method_options.get("max_depth", 4)
    learning_rate = method_options.get("learning_rate", 0.02)
    subsample = method_options.get("subsample", 0.8)
    colsample_bytree = method_options.get("colsample_bytree", 0.8)
    min_child_weight = method_options.get("min_child_weight", 5)
    early_stopping_rounds = method_options.get("early_stopping_rounds", 50)
    n_mc_global = method_options.get("n_mc_global", 2**14)
    n_mc_conditional = method_options.get("n_mc_conditional", 2**13)

    logger.info(
        "XGBoost sensitivity analysis: %d parameters, %d samples, %d slice parameters",
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

    # Fit XGBoost and compute indices for each output
    global_rows = []
    validation_rows = []
    conditional_rows = []
    conditional_joint_rows = []

    for col in available_columns:
        y_train = outputs_train[col].values

        # Fit XGBoost with early stopping on holdout set
        xgb_result = fit_xgboost(
            x_train,
            y_train,
            x_val=x_test,
            y_val=outputs_test[col].values if outputs_test is not None else None,
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            min_child_weight=min_child_weight,
            early_stopping_rounds=early_stopping_rounds,
            n_jobs=n_threads,
        )

        # Compute holdout error if we have a test set
        if x_test is not None and outputs_test is not None:
            y_test = outputs_test[col].values
            y_pred_test = xgb_result["model"].predict(x_test)
            ss_res_test = np.sum((y_test - y_pred_test) ** 2)
            ss_tot_test = np.sum((y_test - np.mean(y_test)) ** 2)
            r2_test = 1 - ss_res_test / ss_tot_test if ss_tot_test > 0 else 0.0
            holdout_error = 1 - r2_test
        else:
            r2_test = None
            holdout_error = None

        validation_error = (
            holdout_error if holdout_error is not None else (1.0 - xgb_result["r2"])
        )

        logger.info(
            "XGBoost for %s: R²_train=%.4f, R²_test=%s, %d rounds",
            col,
            xgb_result["r2"],
            f"{r2_test:.4f}" if r2_test is not None else "N/A",
            xgb_result["n_estimators"],
        )

        if validation_error > 0.1:
            logger.warning(
                "High validation error (%.3f) for output '%s' -- XGBoost may be inaccurate",
                validation_error,
                col,
            )

        # Validation metrics
        validation_rows.append(
            {
                "output": col,
                "validation_error": validation_error,
                "r2_train": xgb_result["r2"],
                "r2_test": r2_test,
                "n_train": n_train,
                "n_test": n_holdout,
                "method": "xgb",
                "n_estimators": xgb_result["n_estimators"],
            }
        )

        # Global Sobol indices
        s1, s_total = sobol_from_rf(
            xgb_result["model"],
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
                    xgb_result["model"],
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
                xgb_result["model"],
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
