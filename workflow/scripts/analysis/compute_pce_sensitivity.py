# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""PCE-based global sensitivity analysis.

Fits Polynomial Chaos Expansions to model outputs and computes Sobol
sensitivity indices analytically from the expansion coefficients.
Supports conditional analysis by fixing designated slice parameters
to specified values.

The implementation is parameter-agnostic: parameter names, distributions,
and slice variable designations are all read from the generator spec.
"""

from itertools import product
import logging
from pathlib import Path

import chaospy as cp
import numpy as np
import pandas as pd
from sklearn.linear_model import LarsCV

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.sensitivity_common import (
    load_scenario_outputs,
    reconstruct_samples,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def fit_pce(
    x_design: np.ndarray,
    y: np.ndarray,
    distribution: cp.Distribution,
    max_degree: int,
    cross_truncation: float,
) -> dict:
    """Fit a sparse PCE using LARS with cross-validation.

    Parameters
    ----------
    x_design : np.ndarray
        Design matrix, shape (N, D) in physical parameter space.
    y : np.ndarray
        Output values, shape (N,).
    distribution : cp.Distribution
        Joint chaospy distribution for the parameters.
    max_degree : int
        Maximum polynomial degree.
    cross_truncation : float
        Cross-truncation parameter (0 < q <= 1). Lower values give
        sparser multi-index sets favouring lower-order interactions.

    Returns
    -------
    dict
        coefficients: fitted PCE coefficients (sparse)
        multi_indices: exponent tuples for each basis term
        loo_error: relative leave-one-out error
        r2: coefficient of determination on training data
        n_terms: total candidate basis terms
        n_active_terms: number of non-zero coefficients
    """
    n_samples, n_dims = x_design.shape

    # Generate orthonormal polynomial expansion
    expansion = cp.generate_expansion(
        order=max_degree,
        dist=distribution,
        cross_truncation=cross_truncation,
        normed=True,
    )

    # Evaluate basis at sample points -> design matrix
    # chaospy expects shape (n_dims, n_samples)
    basis_matrix = np.array([poly(*x_design.T) for poly in expansion]).T
    n_basis = basis_matrix.shape[1]

    # Fit sparse coefficients via LARS with cross-validation
    lars = LarsCV(cv=min(5, n_samples), fit_intercept=False)
    lars.fit(basis_matrix, y)
    coefficients = lars.coef_.copy()

    # Compute predictions and R^2
    y_pred = basis_matrix @ coefficients
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Compute LOO error using the hat matrix for linear regression
    # H = A (A^T A)^{-1} A^T; LOO_i = (y_i - y_hat_i) / (1 - H_ii)
    # Use only active columns for numerical stability
    active_mask = coefficients != 0
    if np.any(active_mask):
        a_active = basis_matrix[:, active_mask]
        try:
            hat_matrix = a_active @ np.linalg.solve(a_active.T @ a_active, a_active.T)
            h_diag = np.diag(hat_matrix)
            loo_residuals = (y - y_pred) / (1 - h_diag)
            loo_mse = np.mean(loo_residuals**2)
            loo_error = loo_mse / np.var(y) if np.var(y) > 0 else float("inf")
        except np.linalg.LinAlgError:
            loo_error = float("inf")
    else:
        loo_error = float("inf")

    # Extract multi-indices from the expansion.
    # Each orthonormal polynomial contains multiple monomial terms;
    # the leading (last) exponent row identifies which basis function it is.
    multi_indices = []
    for poly in expansion:
        exponents = poly.exponents
        multi_indices.append(tuple(int(e) for e in exponents[-1]))

    return {
        "coefficients": coefficients,
        "multi_indices": multi_indices,
        "expansion": expansion,
        "loo_error": loo_error,
        "r2": r2,
        "n_terms": n_basis,
        "n_active_terms": int(np.sum(active_mask)),
    }


def sobol_from_pce(
    coefficients: np.ndarray,
    multi_indices: list[tuple],
    n_params: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute Sobol indices analytically from PCE coefficients.

    Parameters
    ----------
    coefficients : np.ndarray
        PCE coefficients, shape (M,).
    multi_indices : list[tuple]
        Multi-index exponents for each basis term.
    n_params : int
        Number of parameters.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        S1 (first-order) and ST (total-order) indices, each shape (n_params,).
    """
    # Total variance = sum of c_alpha^2 for alpha != 0
    total_var = 0.0
    for alpha, c in zip(multi_indices, coefficients):
        if any(a > 0 for a in alpha):
            total_var += c**2

    s1 = np.zeros(n_params)
    s_total = np.zeros(n_params)

    if total_var <= 0:
        return s1, s_total

    for alpha, c in zip(multi_indices, coefficients):
        c2 = c**2
        active = [i for i in range(n_params) if alpha[i] > 0]
        if not active:
            continue

        # Total-order: any term where alpha_i > 0
        for i in active:
            s_total[i] += c2

        # First-order: only terms where exactly one alpha_i > 0
        if len(active) == 1:
            s1[active[0]] += c2

    s1 /= total_var
    s_total /= total_var

    return s1, s_total


def conditional_sobol(
    coefficients: np.ndarray,
    expansion: list,
    multi_indices: list[tuple],
    distribution: cp.Distribution,
    n_params: int,
    slice_indices: list[int],
    slice_values: list[float],
) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute Sobol indices conditional on slice parameters.

    Analytically conditions the PCE by evaluating the slice parameter
    basis polynomials at the given values and absorbing them into
    the coefficients.

    Parameters
    ----------
    coefficients : np.ndarray
        PCE coefficients.
    expansion : list
        Chaospy polynomial expansion.
    multi_indices : list[tuple]
        Multi-index exponents for each basis term.
    distribution : cp.Distribution
        Joint distribution (used for evaluating marginal basis functions).
    n_params : int
        Total number of parameters (including slice params).
    slice_indices : list[int]
        Indices of slice parameters in the parameter vector.
    slice_values : list[float]
        Values to condition the slice parameters at.

    Returns
    -------
    tuple[np.ndarray, np.ndarray, float]
        S1_cond, ST_cond (shape n_params; slice param entries are 0),
        and conditional_variance.
    """
    # `expansion` is part of the public API and kept for compatibility.
    # The computation below only needs coefficients and multi-indices.
    _ = expansion

    non_slice = sorted(set(range(n_params)) - set(slice_indices))

    # For each multi-index alpha, the conditioning factor is the product
    # of univariate basis evaluations psi_{alpha_j}(x_j*) for each
    # slice parameter j. We extract these by building marginal orthonormal
    # bases and evaluating at the conditioning values.

    # Build marginal distributions for evaluation
    marginals = [distribution[i] for i in range(n_params)]

    # For each slice param, build univariate orthonormal basis and evaluate
    slice_basis_values = {}  # {param_idx: {degree: value}}
    for s_idx, s_val in zip(slice_indices, slice_values):
        marginal = marginals[s_idx]
        # Build univariate expansion up to max degree seen in multi_indices
        max_deg = max(alpha[s_idx] for alpha in multi_indices)
        uni_expansion = cp.generate_expansion(order=max_deg, dist=marginal, normed=True)
        # Evaluate each basis polynomial at the conditioning value
        vals = {}
        for poly in uni_expansion:
            deg = int(poly.exponents[-1][0])
            vals[deg] = float(poly(s_val))
        slice_basis_values[s_idx] = vals

    # Transform and collapse coefficients onto the reduced (non-slice) basis.
    # After conditioning, multiple original terms with the same non-slice
    # multi-index must be summed before squaring for variance decomposition.
    reduced_coefs: dict[tuple[int, ...], float] = {}
    for alpha, c in zip(multi_indices, coefficients):
        factor = 1.0
        for s_idx in slice_indices:
            deg = alpha[s_idx]
            factor *= slice_basis_values[s_idx].get(deg, 0.0)
        alpha_non_slice = tuple(alpha[i] for i in non_slice)
        reduced_coefs[alpha_non_slice] = reduced_coefs.get(
            alpha_non_slice, 0.0
        ) + float(c * factor)

    # Conditional variance over non-slice dimensions.
    cond_var = 0.0
    for alpha_non_slice, c_prime in reduced_coefs.items():
        if any(a > 0 for a in alpha_non_slice):
            cond_var += c_prime**2

    # Conditional Sobol indices
    s1_cond = np.zeros(n_params)
    st_cond = np.zeros(n_params)

    if cond_var <= 0:
        return s1_cond, st_cond, cond_var

    for alpha_non_slice, c_prime in reduced_coefs.items():
        c2 = c_prime**2
        # Active non-slice variables in original index space
        active_non_slice = [
            non_slice[j] for j, deg in enumerate(alpha_non_slice) if deg > 0
        ]
        if not active_non_slice:
            continue

        # Total-order
        for i in active_non_slice:
            st_cond[i] += c2

        # First-order: only terms where exactly one non-slice var is active
        if len(active_non_slice) == 1:
            s1_cond[active_non_slice[0]] += c2

    s1_cond /= cond_var
    st_cond /= cond_var

    return s1_cond, st_cond, cond_var


def run(snakemake) -> None:
    logger = setup_script_logging(snakemake.log[0])

    # Derive analysis directory from resolved output path.
    # This avoids unresolved "<results>" placeholders in params.
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

    # Read PCE hyperparameters
    method_options = generator_spec.get("method_options", {})
    max_degree = method_options.get("max_degree", 3)
    cross_truncation = method_options.get("cross_truncation", 0.5)

    logger.info(
        "PCE sensitivity analysis: %d parameters, %d samples, %d slice parameters, "
        "degree=%d, cross_truncation=%.2f",
        n_params,
        len(scenario_names),
        len(slice_param_names),
        max_degree,
        cross_truncation,
    )

    # Reconstruct full design matrix, then select rows for available scenarios.
    # Scenario names encode their sample index (e.g. "pce_42"), so we extract
    # indices to align the design matrix with the (possibly incomplete) scenario set.
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

    # Fit PCE and compute indices for each output
    global_rows = []
    validation_rows = []
    conditional_rows = []
    conditional_joint_rows = []

    for col in available_columns:
        y = outputs_df[col].values

        # Fit PCE
        pce_result = fit_pce(x_design, y, joint_dist, max_degree, cross_truncation)

        logger.info(
            "PCE for %s: R^2=%.4f, LOO=%.4f, %d/%d active terms",
            col,
            pce_result["r2"],
            pce_result["loo_error"],
            pce_result["n_active_terms"],
            pce_result["n_terms"],
        )

        if pce_result["loo_error"] > 0.1:
            logger.warning(
                "High LOO error (%.3f) for output '%s' -- PCE may be inaccurate",
                pce_result["loo_error"],
                col,
            )

        # Validation metrics
        validation_rows.append(
            {
                "output": col,
                "validation_error": pce_result["loo_error"],
                "r2": pce_result["r2"],
                "n_terms": pce_result["n_terms"],
                "n_active_terms": pce_result["n_active_terms"],
                "n_samples": len(y),
                "method": "pce",
                "max_degree": max_degree,
            }
        )

        # Global Sobol indices
        s1, s_total = sobol_from_pce(
            pce_result["coefficients"],
            pce_result["multi_indices"],
            n_params,
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

        # Conditional Sobol indices (if slice parameters defined).
        # Condition on each slice parameter individually so that the
        # other slice parameters remain free and contribute to
        # explained variability.
        if slice_indices and slice_grid:
            for sp_idx, sp_name in zip(slice_indices, slice_param_names):
                for sp_val in slice_grid[sp_name]:
                    s1_c, st_c, cond_var = conditional_sobol(
                        pce_result["coefficients"],
                        pce_result["expansion"],
                        pce_result["multi_indices"],
                        joint_dist,
                        n_params,
                        [sp_idx],
                        [sp_val],
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

            # Joint conditioning across all slice parameters on a Cartesian
            # grid. This supports 2D sensitivity surfaces over policy axes.
            joint_value_lists = [slice_grid[sp_name] for sp_name in slice_param_names]
            for joint_values in product(*joint_value_lists):
                s1_c, st_c, cond_var = conditional_sobol(
                    pce_result["coefficients"],
                    pce_result["expansion"],
                    pce_result["multi_indices"],
                    joint_dist,
                    n_params,
                    slice_indices,
                    list(joint_values),
                )

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
