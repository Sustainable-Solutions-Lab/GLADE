# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared surrogate model module for sensitivity analysis.

Fits one of several surrogate types (PCE, RF, MARS, XGBoost) to the
scalar outputs of the GSA Sobol design and persists the result as a
self-contained bundle that downstream rules (Sobol index computation,
policy-sweep plots, notebooks) can load and re-use.

All surrogates expose a uniform ``predict(bundle, output, x)`` interface.
Sobol computation is kept surrogate-agnostic: PCE uses its analytical
variance decomposition, all others use Saltelli pick-freeze Monte Carlo.
"""

from dataclasses import dataclass
from itertools import product
import logging
from pathlib import Path
import pickle
from typing import Any, Callable

import chaospy as cp
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LarsCV
from xgboost import XGBRegressor

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.mars import Earth

logger = logging.getLogger(__name__)


OUTPUT_COLUMNS: tuple[str, ...] = ("total_cost", "ghg_emissions", "land_use", "yll")
SUPPORTED_METHODS: tuple[str, ...] = ("pce", "rf", "mars", "xgb")


@dataclass
class SurrogateBundle:
    """Self-contained surrogate for all scalar sensitivity outputs.

    Attributes
    ----------
    method
        Surrogate type: one of ``pce``, ``rf``, ``mars``, ``xgb``.
    generator_spec
        Full generator spec the surrogate was trained against.  Carries
        parameter names, distribution specs, slice parameters, and
        sampling seed, so a consumer can rebuild the joint distribution.
    param_names
        Ordered parameter names (same order as the design matrix columns).
    output_columns
        Output targets the surrogate was trained on (subset of
        :data:`OUTPUT_COLUMNS`).
    models
        Per-output surrogate payload.  Shape depends on the method:

        - ``pce``: ``{coefficients, multi_indices, max_degree, cross_truncation}``
        - ``rf``: fitted :class:`RandomForestRegressor`
        - ``xgb``: fitted :class:`XGBRegressor`
        - ``mars``: ``{earth: Earth, log_transform: bool}``

    validation
        Per-output dict of fit-quality metrics (keys vary by method).
    n_train, n_test
        Training and holdout sample counts.
    """

    method: str
    generator_spec: dict
    param_names: list[str]
    output_columns: list[str]
    models: dict[str, Any]
    validation: dict[str, dict]
    n_train: int
    n_test: int


# ---------------------------------------------------------------------------
# Fit helpers (per-method).  Lifted from the previous compute_*_sensitivity.py
# modules so both Snakemake rules and notebooks reach them through a single
# import path.
# ---------------------------------------------------------------------------


def fit_pce(
    x_design: np.ndarray,
    y: np.ndarray,
    distribution: cp.Distribution,
    max_degree: int,
    cross_truncation: float,
    n_jobs: int = 1,
) -> dict:
    """Fit a sparse PCE using LARS with cross-validation.

    Returns
    -------
    dict
        coefficients, multi_indices, expansion, loo_error, r2, n_terms,
        n_active_terms.
    """
    n_samples, _ = x_design.shape

    expansion = cp.generate_expansion(
        order=max_degree,
        dist=distribution,
        cross_truncation=cross_truncation,
        normed=True,
    )
    basis_matrix = np.array([poly(*x_design.T) for poly in expansion]).T
    n_basis = basis_matrix.shape[1]

    lars = LarsCV(cv=min(5, n_samples), fit_intercept=False, n_jobs=n_jobs)
    lars.fit(basis_matrix, y)
    coefficients = lars.coef_.copy()

    y_pred = basis_matrix @ coefficients
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    active_mask = coefficients != 0
    if np.any(active_mask):
        a_active = basis_matrix[:, active_mask]
        try:
            r = np.linalg.solve(a_active.T @ a_active, a_active.T)
            h_diag = np.einsum("ij,ji->i", a_active, r)
            loo_residuals = (y - y_pred) / (1 - h_diag)
            loo_mse = np.mean(loo_residuals**2)
            loo_error = loo_mse / np.var(y) if np.var(y) > 0 else float("inf")
        except np.linalg.LinAlgError:
            loo_error = float("inf")
    else:
        loo_error = float("inf")

    multi_indices = [tuple(int(e) for e in poly.exponents[-1]) for poly in expansion]

    return {
        "coefficients": coefficients,
        "multi_indices": multi_indices,
        "expansion": expansion,
        "loo_error": loo_error,
        "r2": r2,
        "n_terms": n_basis,
        "n_active_terms": int(np.sum(active_mask)),
    }


def fit_random_forest(
    x_design: np.ndarray,
    y: np.ndarray,
    n_estimators: int = 500,
    n_jobs: int = 1,
    random_state: int = 42,
) -> dict:
    """Fit a Random Forest regressor with OOB validation."""
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        oob_score=True,
        n_jobs=n_jobs,
        random_state=random_state,
    )
    rf.fit(x_design, y)
    oob_r2 = rf.oob_score_
    return {
        "model": rf,
        "validation_error": 1.0 - oob_r2,
        "r2": oob_r2,
        "n_estimators": n_estimators,
        "n_samples": len(y),
    }


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
    """Fit an XGBoost regressor with optional early stopping."""
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


def fit_mars(
    x_design: np.ndarray,
    y: np.ndarray,
    max_terms: int = 50,
    max_degree: int = 2,
    penalty: float = 3.0,
    n_knots: int = 25,
) -> dict:
    """Fit a MARS regressor with GCV-based model selection."""
    model = Earth(
        max_terms=max_terms,
        max_degree=max_degree,
        penalty=penalty,
        n_knots=n_knots,
    )
    model.fit(x_design, y)
    return {
        "model": model,
        "validation_error": model.gcv_,
        "r2": model.score(x_design, y),
        "n_basis": len(model.basis_),
        "n_samples": len(y),
    }


# ---------------------------------------------------------------------------
# Bundle construction: train/test split + per-method dispatch.
# ---------------------------------------------------------------------------


def fit_bundle(
    method: str,
    x_design: np.ndarray,
    outputs_df: pd.DataFrame,
    available_columns: list[str],
    generator_spec: dict,
    method_config: dict,
    holdout_fraction: float,
    n_threads: int = 1,
) -> SurrogateBundle:
    """Fit a :class:`SurrogateBundle` for all available output columns.

    The caller is expected to have already dropped NaN-bearing scenarios
    from ``x_design`` and ``outputs_df``.  Train/test split is done here
    so all methods see the same partition.
    """
    if method not in SUPPORTED_METHODS:
        raise ValueError(
            f"Unknown surrogate method '{method}'. " f"Supported: {SUPPORTED_METHODS}"
        )

    joint_dist, param_names = build_joint_distribution(generator_spec)
    method_options = dict(method_config.get("method_options", {}))

    n_total = len(x_design)
    n_holdout = int(n_total * holdout_fraction)
    n_train = n_total - n_holdout
    x_train = x_design[:n_train]
    x_test = x_design[n_train:] if n_holdout > 0 else None
    outputs_train = outputs_df.iloc[:n_train]
    outputs_test = outputs_df.iloc[n_train:] if n_holdout > 0 else None

    logger.info(
        "Bundle fit (%s): %d train, %d holdout, %d outputs",
        method,
        n_train,
        n_holdout,
        len(available_columns),
    )

    models: dict[str, Any] = {}
    validation: dict[str, dict] = {}

    for col in available_columns:
        y_train = outputs_train[col].values
        y_test = outputs_test[col].values if outputs_test is not None else None

        if method == "pce":
            payload, val = _fit_pce_one(
                x_train, y_train, x_test, y_test, joint_dist, method_options, n_threads
            )
        elif method == "rf":
            payload, val = _fit_rf_one(
                x_train, y_train, x_test, y_test, method_options, n_threads
            )
        elif method == "xgb":
            payload, val = _fit_xgb_one(
                x_train, y_train, x_test, y_test, method_options, n_threads
            )
        elif method == "mars":
            payload, val = _fit_mars_one(
                x_train, y_train, x_test, y_test, col, method_options
            )
        else:
            raise AssertionError(f"unreachable method {method!r}")

        val["output"] = col
        val["n_train"] = n_train
        val["n_test"] = n_holdout
        val["method"] = method

        models[col] = payload
        validation[col] = val

        if val["validation_error"] > 0.1:
            logger.warning(
                "High validation error (%.3f) for '%s' (%s) -- surrogate may be inaccurate",
                val["validation_error"],
                col,
                method,
            )

    return SurrogateBundle(
        method=method,
        generator_spec=dict(generator_spec),
        param_names=param_names,
        output_columns=list(available_columns),
        models=models,
        validation=validation,
        n_train=n_train,
        n_test=n_holdout,
    )


def _fit_pce_one(x_train, y_train, x_test, y_test, joint_dist, opts, n_jobs):
    max_degree = opts.get("max_degree", 3)
    cross_truncation = opts.get("cross_truncation", 0.5)
    result = fit_pce(
        x_train, y_train, joint_dist, max_degree, cross_truncation, n_jobs=n_jobs
    )

    if x_test is not None and y_test is not None:
        basis_test = np.array([poly(*x_test.T) for poly in result["expansion"]]).T
        y_pred = basis_test @ result["coefficients"]
        ss_res = np.sum((y_test - y_pred) ** 2)
        ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
        r2_test = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        holdout_error = 1 - r2_test
    else:
        r2_test = None
        holdout_error = None

    validation_error = (
        holdout_error if holdout_error is not None else result["loo_error"]
    )

    payload = {
        "coefficients": result["coefficients"],
        "multi_indices": result["multi_indices"],
        "max_degree": max_degree,
        "cross_truncation": cross_truncation,
    }
    val = {
        "validation_error": validation_error,
        "loo_error": result["loo_error"],
        "r2_train": result["r2"],
        "r2_test": r2_test,
        "n_terms": result["n_terms"],
        "n_active_terms": result["n_active_terms"],
        "max_degree": max_degree,
    }
    return payload, val


def _fit_rf_one(x_train, y_train, x_test, y_test, opts, n_jobs):
    n_estimators = opts.get("n_estimators", 500)
    result = fit_random_forest(
        x_train, y_train, n_estimators=n_estimators, n_jobs=n_jobs
    )
    if x_test is not None and y_test is not None:
        y_pred = result["model"].predict(x_test)
        ss_res = np.sum((y_test - y_pred) ** 2)
        ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
        r2_test = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        holdout_error = 1 - r2_test
    else:
        r2_test = None
        holdout_error = None

    oob_error = result["validation_error"]
    validation_error = holdout_error if holdout_error is not None else oob_error

    val = {
        "validation_error": validation_error,
        "oob_error": oob_error,
        "r2_train": result["r2"],
        "r2_test": r2_test,
        "n_estimators": result["n_estimators"],
    }
    return result["model"], val


def _fit_xgb_one(x_train, y_train, x_test, y_test, opts, n_jobs):
    result = fit_xgboost(
        x_train,
        y_train,
        x_val=x_test,
        y_val=y_test,
        n_estimators=opts.get("n_estimators", 5000),
        max_depth=opts.get("max_depth", 4),
        learning_rate=opts.get("learning_rate", 0.02),
        subsample=opts.get("subsample", 0.8),
        colsample_bytree=opts.get("colsample_bytree", 0.8),
        min_child_weight=opts.get("min_child_weight", 5),
        early_stopping_rounds=opts.get("early_stopping_rounds", 50),
        n_jobs=n_jobs,
    )
    if x_test is not None and y_test is not None:
        y_pred = result["model"].predict(x_test)
        ss_res = np.sum((y_test - y_pred) ** 2)
        ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
        r2_test = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        holdout_error = 1 - r2_test
    else:
        r2_test = None
        holdout_error = None

    validation_error = (
        holdout_error if holdout_error is not None else (1.0 - result["r2"])
    )

    val = {
        "validation_error": validation_error,
        "r2_train": result["r2"],
        "r2_test": r2_test,
        "n_estimators": result["n_estimators"],
    }
    return result["model"], val


def _fit_mars_one(x_train, y_train, x_test, y_test, col, opts):
    log_transform_outputs = set(opts.get("log_transform", []))
    use_log = col in log_transform_outputs

    y_train_fit = np.log1p(y_train) if use_log else y_train
    result = fit_mars(
        x_train,
        y_train_fit,
        max_terms=opts.get("max_terms", 50),
        max_degree=opts.get("max_degree", 2),
        penalty=opts.get("penalty", 3.0),
        n_knots=opts.get("n_knots", 25),
    )

    earth_model = result["model"]
    if x_test is not None and y_test is not None:
        raw_pred = earth_model.predict(x_test)
        y_pred = np.expm1(raw_pred) if use_log else raw_pred
        ss_res = np.sum((y_test - y_pred) ** 2)
        ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
        r2_test = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        holdout_error = 1 - r2_test
    else:
        r2_test = None
        holdout_error = None

    gcv_error = result["validation_error"]
    validation_error = holdout_error if holdout_error is not None else gcv_error

    payload = {"earth": earth_model, "log_transform": use_log}
    val = {
        "validation_error": validation_error,
        "gcv": gcv_error,
        "r2_train": result["r2"],
        "r2_test": r2_test,
        "n_basis": result["n_basis"],
    }
    return payload, val


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------


def save_bundle(bundle: SurrogateBundle, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_bundle(path: Path) -> SurrogateBundle:
    with Path(path).open("rb") as f:
        bundle = pickle.load(f)
    if not isinstance(bundle, SurrogateBundle):
        raise TypeError(
            f"Expected SurrogateBundle at {path}, got {type(bundle).__name__}"
        )
    return bundle


def validation_dataframe(bundle: SurrogateBundle) -> pd.DataFrame:
    """Flatten bundle.validation into a parquet-friendly DataFrame."""
    return pd.DataFrame(list(bundle.validation.values()))


# ---------------------------------------------------------------------------
# Prediction.
# ---------------------------------------------------------------------------


def predict(bundle: SurrogateBundle, output: str, x: np.ndarray) -> np.ndarray:
    """Predict ``output`` at physical-space design matrix ``x``.

    Returns an array of shape ``(len(x),)`` in the original output space
    (log-transformed MARS is back-transformed automatically).
    """
    if output not in bundle.models:
        raise KeyError(
            f"Output '{output}' not present in bundle (has {list(bundle.models)})"
        )
    model = bundle.models[output]
    method = bundle.method

    if method == "pce":
        distribution, _ = build_joint_distribution(bundle.generator_spec)
        expansion = cp.generate_expansion(
            order=model["max_degree"],
            dist=distribution,
            cross_truncation=model["cross_truncation"],
            normed=True,
        )
        basis = np.array([poly(*x.T) for poly in expansion]).T
        return basis @ model["coefficients"]
    elif method in ("rf", "xgb"):
        return model.predict(x)
    elif method == "mars":
        raw = model["earth"].predict(x)
        return np.expm1(raw) if model["log_transform"] else raw
    else:
        raise AssertionError(f"unreachable method {method!r}")


def predictor(
    bundle: SurrogateBundle, output: str
) -> Callable[[np.ndarray], np.ndarray]:
    """Return a bound ``x -> y`` callable for ``output``.

    Rebuilds the PCE expansion once (if applicable) so repeated calls
    across a Monte Carlo grid don't pay the expansion-construction cost.
    """
    model = bundle.models[output]
    method = bundle.method

    if method == "pce":
        distribution, _ = build_joint_distribution(bundle.generator_spec)
        expansion = cp.generate_expansion(
            order=model["max_degree"],
            dist=distribution,
            cross_truncation=model["cross_truncation"],
            normed=True,
        )
        coefs = model["coefficients"]

        def _predict(x: np.ndarray) -> np.ndarray:
            basis = np.array([poly(*x.T) for poly in expansion]).T
            return basis @ coefs

        return _predict
    elif method in ("rf", "xgb"):
        return model.predict
    elif method == "mars":
        earth = model["earth"]
        if model["log_transform"]:
            return lambda x: np.expm1(earth.predict(x))
        return earth.predict
    else:
        raise AssertionError(f"unreachable method {method!r}")


# ---------------------------------------------------------------------------
# Sobol index computation (global + conditional).
# ---------------------------------------------------------------------------


def sobol_from_pce(
    coefficients: np.ndarray,
    multi_indices: list[tuple],
    n_params: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute Sobol indices analytically from PCE coefficients."""
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
        for i in active:
            s_total[i] += c2
        if len(active) == 1:
            s1[active[0]] += c2

    s1 /= total_var
    s_total /= total_var
    return s1, s_total


def sobol_from_predict(
    predict_fn: Callable[[np.ndarray], np.ndarray],
    distribution: cp.Distribution,
    n_params: int,
    n_mc: int = 2**14,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate Sobol indices via Saltelli pick-freeze Monte Carlo.

    Model-agnostic: only requires a callable mapping a (N, D) design
    matrix in physical space to a (N,) vector of predictions.
    """
    rng = np.random.default_rng(seed)
    u_a = rng.random((n_mc, n_params))
    u_b = rng.random((n_mc, n_params))

    x_a = distribution.inv(u_a.T).T
    x_b = distribution.inv(u_b.T).T

    f_a = predict_fn(x_a)
    f_b = predict_fn(x_b)

    f_all = np.concatenate([f_a, f_b])
    var_y = np.var(f_all)

    s1 = np.zeros(n_params)
    s_total = np.zeros(n_params)
    if var_y <= 0:
        return s1, s_total

    x_ab = x_a.copy()
    for i in range(n_params):
        saved_col = x_ab[:, i].copy()
        x_ab[:, i] = x_b[:, i]
        f_ab_i = predict_fn(x_ab)
        x_ab[:, i] = saved_col
        s1[i] = np.mean(f_b * (f_ab_i - f_a)) / var_y
        s_total[i] = 0.5 * np.mean((f_a - f_ab_i) ** 2) / var_y

    np.clip(s1, 0.0, 1.0, out=s1)
    return s1, s_total


def _precompute_pce_slice_basis(
    distribution: cp.Distribution,
    max_degree: int,
    slice_indices: list[int],
    slice_grid: dict[int, list[float]],
) -> dict[int, dict[float, dict[int, float]]]:
    cache: dict[int, dict[float, dict[int, float]]] = {}
    for s_idx in slice_indices:
        marginal = distribution[s_idx]
        uni_expansion = cp.generate_expansion(
            order=max_degree, dist=marginal, normed=True
        )
        deg_polys = {int(poly.exponents[-1][0]): poly for poly in uni_expansion}
        val_cache: dict[float, dict[int, float]] = {}
        for s_val in slice_grid[s_idx]:
            val_cache[s_val] = {
                deg: float(poly(s_val)) for deg, poly in deg_polys.items()
            }
        cache[s_idx] = val_cache
    return cache


def conditional_sobol_pce(
    coefficients: np.ndarray,
    multi_indices: list[tuple],
    distribution: cp.Distribution,
    n_params: int,
    slice_indices: list[int],
    slice_values: list[float],
    precomputed_basis: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Analytic conditional Sobol indices from a PCE expansion."""
    non_slice = sorted(set(range(n_params)) - set(slice_indices))

    if precomputed_basis is not None:
        slice_basis_values = {
            s_idx: precomputed_basis[s_idx][s_val]
            for s_idx, s_val in zip(slice_indices, slice_values)
        }
    else:
        slice_basis_values = {}
        for s_idx, s_val in zip(slice_indices, slice_values):
            marginal = distribution[s_idx]
            max_deg = max(alpha[s_idx] for alpha in multi_indices)
            uni_expansion = cp.generate_expansion(
                order=max_deg, dist=marginal, normed=True
            )
            vals = {}
            for poly in uni_expansion:
                deg = int(poly.exponents[-1][0])
                vals[deg] = float(poly(s_val))
            slice_basis_values[s_idx] = vals

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

    cond_var = 0.0
    for alpha_non_slice, c_prime in reduced_coefs.items():
        if any(a > 0 for a in alpha_non_slice):
            cond_var += c_prime**2

    s1_cond = np.zeros(n_params)
    st_cond = np.zeros(n_params)
    if cond_var <= 0:
        return s1_cond, st_cond, cond_var

    for alpha_non_slice, c_prime in reduced_coefs.items():
        c2 = c_prime**2
        active_non_slice = [
            non_slice[j] for j, deg in enumerate(alpha_non_slice) if deg > 0
        ]
        if not active_non_slice:
            continue
        for i in active_non_slice:
            st_cond[i] += c2
        if len(active_non_slice) == 1:
            s1_cond[active_non_slice[0]] += c2

    s1_cond /= cond_var
    st_cond /= cond_var
    return s1_cond, st_cond, cond_var


def conditional_sobol_mc(
    predict_fn: Callable[[np.ndarray], np.ndarray],
    distribution: cp.Distribution,
    n_params: int,
    slice_indices: list[int],
    slice_value_grid: list[list[float]],
    n_mc: int = 2**13,
    seed: int = 0,
    batch_size: int = 50,
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Batch-estimate conditional Sobol indices across a grid of slice values.

    Mirrors the RF/XGB/MARS routine: reuses the same A/B free-parameter
    matrices across all grid points, processed in batches to reduce the
    number of predict() calls.
    """
    non_slice = sorted(set(range(n_params)) - set(slice_indices))
    n_free = len(non_slice)
    n_grid = len(slice_value_grid)

    rng = np.random.default_rng(seed)
    u_a_free = rng.random((n_mc, n_free))
    u_b_free = rng.random((n_mc, n_free))

    x_a_free = np.empty_like(u_a_free)
    x_b_free = np.empty_like(u_b_free)
    for j, orig_idx in enumerate(non_slice):
        marginal = distribution[orig_idx]
        x_a_free[:, j] = marginal.inv(u_a_free[:, j])
        x_b_free[:, j] = marginal.inv(u_b_free[:, j])

    def _embed_batch(x_free, slice_vals_batch):
        n_batch = len(slice_vals_batch)
        x_tiled = np.tile(x_free, (n_batch, 1))
        x_full = np.empty((n_batch * n_mc, n_params))
        for j, orig_idx in enumerate(non_slice):
            x_full[:, orig_idx] = x_tiled[:, j]
        for k, s_idx in enumerate(slice_indices):
            vals = np.repeat([sv[k] for sv in slice_vals_batch], n_mc)
            x_full[:, s_idx] = vals
        return x_full

    results: list[tuple[np.ndarray, np.ndarray, float] | None] = [None] * n_grid

    for batch_start in range(0, n_grid, batch_size):
        batch_end = min(batch_start + batch_size, n_grid)
        batch_slice_vals = slice_value_grid[batch_start:batch_end]
        n_batch = len(batch_slice_vals)

        x_a_batch = _embed_batch(x_a_free, batch_slice_vals)
        x_b_batch = _embed_batch(x_b_free, batch_slice_vals)
        f_a_batch = predict_fn(x_a_batch).reshape(n_batch, n_mc)
        f_b_batch = predict_fn(x_b_batch).reshape(n_batch, n_mc)

        f_all = np.concatenate([f_a_batch, f_b_batch], axis=1)
        cond_vars = np.var(f_all, axis=1)

        s1_batch = np.zeros((n_batch, n_params))
        st_batch = np.zeros((n_batch, n_params))

        for j, orig_idx in enumerate(non_slice):
            x_ab_free = x_a_free.copy()
            x_ab_free[:, j] = x_b_free[:, j]
            x_ab_batch = _embed_batch(x_ab_free, batch_slice_vals)
            f_ab_batch = predict_fn(x_ab_batch).reshape(n_batch, n_mc)

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

    return results  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# High-level helpers used by the compute_sobol rule.
# ---------------------------------------------------------------------------


def global_sobol_for_output(
    bundle: SurrogateBundle,
    output: str,
    distribution: cp.Distribution,
    n_mc: int = 2**14,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    n_params = len(bundle.param_names)
    if bundle.method == "pce":
        payload = bundle.models[output]
        return sobol_from_pce(
            payload["coefficients"], payload["multi_indices"], n_params
        )
    return sobol_from_predict(
        predictor(bundle, output), distribution, n_params, n_mc=n_mc, seed=seed
    )


def conditional_sobol_for_output(
    bundle: SurrogateBundle,
    output: str,
    distribution: cp.Distribution,
    slice_indices: list[int],
    slice_value_grid: list[list[float]],
    n_mc: int = 2**13,
    seed: int = 0,
    pce_basis_cache: dict | None = None,
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    n_params = len(bundle.param_names)
    if bundle.method == "pce":
        payload = bundle.models[output]
        return [
            conditional_sobol_pce(
                payload["coefficients"],
                payload["multi_indices"],
                distribution,
                n_params,
                slice_indices,
                list(values),
                precomputed_basis=pce_basis_cache,
            )
            for values in slice_value_grid
        ]
    return conditional_sobol_mc(
        predictor(bundle, output),
        distribution,
        n_params,
        slice_indices,
        slice_value_grid,
        n_mc=n_mc,
        seed=seed,
    )


def build_pce_basis_cache(
    bundle: SurrogateBundle,
    distribution: cp.Distribution,
    slice_indices: list[int],
    slice_grid_by_index: dict[int, list[float]],
) -> dict | None:
    """Return a per-slice-param basis cache for PCE conditional-sobol reuse."""
    if bundle.method != "pce" or not slice_indices:
        return None
    max_degree = max(int(payload["max_degree"]) for payload in bundle.models.values())
    return _precompute_pce_slice_basis(
        distribution, max_degree, slice_indices, slice_grid_by_index
    )


def sobol_rows_from_bundle(
    bundle: SurrogateBundle,
    distribution: cp.Distribution,
    method_options: dict,
    slice_grid: dict[str, list[float]],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compute all Sobol rows (global, conditional, joint-conditional)."""
    param_names = bundle.param_names
    slice_param_names = bundle.generator_spec.get("slice_parameters", [])
    slice_indices = [param_names.index(sp) for sp in slice_param_names]

    n_mc_global = int(method_options.get("n_mc_global", 2**14))
    n_mc_conditional = int(method_options.get("n_mc_conditional", 2**13))

    pce_cache = None
    if slice_indices and slice_grid and bundle.method == "pce":
        idx_grid = {
            param_names.index(sp_name): list(values)
            for sp_name, values in slice_grid.items()
        }
        pce_cache = build_pce_basis_cache(bundle, distribution, slice_indices, idx_grid)

    global_rows: list[dict] = []
    conditional_rows: list[dict] = []
    conditional_joint_rows: list[dict] = []

    for col in bundle.output_columns:
        s1, s_total = global_sobol_for_output(
            bundle, col, distribution, n_mc=n_mc_global
        )
        for i, pname in enumerate(param_names):
            global_rows.append(
                {"output": col, "parameter": pname, "S1": s1[i], "ST": s_total[i]}
            )
        logger.info("Global Sobol for %s (%s):", col, bundle.method)
        for i, pname in enumerate(param_names):
            logger.info("  %s: S1=%.3f, ST=%.3f", pname, s1[i], s_total[i])

        if not (slice_indices and slice_grid):
            continue

        # Individual conditioning per slice parameter.
        for sp_idx, sp_name in zip(slice_indices, slice_param_names):
            grid_values = [[v] for v in slice_grid[sp_name]]
            results = conditional_sobol_for_output(
                bundle,
                col,
                distribution,
                [sp_idx],
                grid_values,
                n_mc=n_mc_conditional,
                pce_basis_cache=pce_cache,
            )
            for sp_val_list, (s1_c, st_c, cond_var) in zip(grid_values, results):
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

        # Joint conditioning across all slice parameters.
        joint_value_lists = [slice_grid[sp_name] for sp_name in slice_param_names]
        grid_points = [list(vals) for vals in product(*joint_value_lists)]
        joint_results = conditional_sobol_for_output(
            bundle,
            col,
            distribution,
            slice_indices,
            grid_points,
            n_mc=n_mc_conditional,
            pce_basis_cache=pce_cache,
        )
        for joint_values, (s1_c, st_c, cond_var) in zip(grid_points, joint_results):
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

    return global_rows, conditional_rows, conditional_joint_rows
