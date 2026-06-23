# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared surrogate model module for sensitivity analysis.

Fits one of several surrogate types (PCE, RF, XGBoost, ReLU MLP) to
the scalar outputs of the GSA Sobol design and persists the result as a
self-contained bundle that downstream rules (Sobol index computation,
policy-sweep plots, notebooks) can load and re-use.

All surrogates expose a uniform ``predict(bundle, output, x)`` interface.
Sobol computation is kept surrogate-agnostic: PCE uses its analytical
variance decomposition, all others use Saltelli pick-freeze Monte Carlo.
"""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from itertools import product
import logging
from pathlib import Path
import pickle
from typing import Any, Callable

import chaospy as cp
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LarsCV
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from workflow.scenario_generators import build_joint_distribution

logger = logging.getLogger(__name__)


SUPPORTED_METHODS: tuple[str, ...] = ("pce", "rf", "xgb", "mlp")
# Methods that natively accept a 2-D target and train all outputs with a
# shared structure.  XGBoost uses ``multi_strategy='multi_output_tree'``;
# sklearn's RandomForestRegressor and MLPRegressor accept ``y`` of shape
# ``(n, n_out)``.
_MULTI_OUTPUT_METHODS: tuple[str, ...] = ("xgb", "rf", "mlp")


@dataclass
class SurrogateBundle:
    """Self-contained surrogate for all scalar sensitivity outputs.

    Attributes
    ----------
    method
        Surrogate type: one of ``pce``, ``rf``, ``xgb``, ``mlp``.
    generator_spec
        Full generator spec the surrogate was trained against.  Carries
        parameter names, distribution specs, slice parameters, and
        sampling seed, so a consumer can rebuild the joint distribution.
    param_names
        Ordered parameter names (same order as the design matrix columns).
    output_columns
        Output targets the surrogate was trained on (names declared in
        ``sensitivity_analysis.outputs``).
    models
        Per-output surrogate payload.  Shape depends on the method:

        - ``pce``: ``{coefficients, multi_indices, max_degree, cross_truncation}``
        - ``rf``: fitted :class:`RandomForestRegressor`
        - ``xgb``: fitted :class:`XGBRegressor`
        - ``mlp``: fitted :class:`~sklearn.pipeline.Pipeline`
          (log + standardize + :class:`MLPRegressor`)

    validation
        Per-output dict of fit-quality metrics (keys vary by method).
    n_train, n_test
        Training and holdout sample counts.
    field_decoders
        Per-field PCA decoders (keyed by field-output name).  Empty unless
        the config declares ``kind: field`` outputs.  ``output_columns``
        then contains the per-field PCA *score* columns (the surrogate's
        actual targets) rather than the thousands of raw spatial elements;
        :func:`predict_field` reconstructs the full field from the scores.
    """

    method: str
    generator_spec: dict
    param_names: list[str]
    output_columns: list[str]
    models: dict[str, Any]
    validation: dict[str, dict]
    n_train: int
    n_test: int
    field_decoders: dict[str, "FieldDecoder"] = dataclass_field(default_factory=dict)


@dataclass
class FieldDecoder:
    """PCA decoder reconstructing a high-dimensional spatial field from scores.

    A ``field`` output (e.g. per-region cropland area) is compressed by PCA
    fit on the training rows: the surrogate predicts ``n_components`` score
    columns and the full field is reconstructed as
    ``scores @ components + mean``.  ``keys`` are the field element labels
    (e.g. region ids) in the column order the components were fit on.
    """

    name: str
    keys: list[str]
    score_columns: list[str]
    mean: np.ndarray  # (n_keys,)
    components: np.ndarray  # (n_components, n_keys)
    explained_variance_ratio: np.ndarray  # (n_components,)

    def decode(self, scores: np.ndarray) -> np.ndarray:
        """Reconstruct the field ``(n_samples, n_keys)`` from ``(n_samples, k)`` scores."""
        scores = np.atleast_2d(scores)
        return scores @ self.components + self.mean


@dataclass
class MultiOutputPayload:
    """Bundle payload for methods that train all outputs jointly.

    Wraps a single fitted estimator whose ``predict(x)`` returns a
    ``(n_samples, n_outputs)`` matrix of standardized predictions.  Each
    payload corresponds to one output column (``output_index``) and knows
    how to invert the per-output standardization (mean/std) applied during
    fitting.  Pickle deduplicates the shared ``model`` across per-output
    payloads via its memo table, so the bundle carries one estimator, not
    one per output.
    """

    model: Any
    output_index: int
    target_mean: float
    target_std: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        pred = self.model.predict(x)
        if pred.ndim == 1:
            pred = pred.reshape(-1, 1)
        return pred[:, self.output_index] * self.target_std + self.target_mean


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


def fit_xgboost_multi(
    x_design: np.ndarray,
    y_mat: np.ndarray,
    x_val: np.ndarray | None = None,
    y_val_mat: np.ndarray | None = None,
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
    """Fit a single multi-output XGBoost (shared tree structure).

    Uses ``multi_strategy='multi_output_tree'`` (XGBoost >= 2.0) so all
    outputs share the split structure, with vector-valued leaves.  Early
    stopping, when enabled, uses the mean RMSE across outputs; targets
    should be standardized beforehand so no single output dominates the
    stopping criterion.
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
        multi_strategy="multi_output_tree",
    )
    fit_kwargs = {"verbose": False}
    if x_val is not None and y_val_mat is not None:
        model.set_params(early_stopping_rounds=early_stopping_rounds)
        fit_kwargs["eval_set"] = [(x_val, y_val_mat)]
    model.fit(x_design, y_mat, **fit_kwargs)

    actual_rounds = (
        model.best_iteration + 1 if hasattr(model, "best_iteration") else n_estimators
    )
    return {"model": model, "n_estimators": actual_rounds, "n_samples": len(y_mat)}


def fit_random_forest_multi(
    x_design: np.ndarray,
    y_mat: np.ndarray,
    n_estimators: int = 500,
    n_jobs: int = 1,
    random_state: int = 42,
) -> dict:
    """Fit a multi-output Random Forest regressor with OOB predictions.

    sklearn's :class:`RandomForestRegressor` accepts a 2-D ``y`` natively;
    each tree regresses all outputs jointly and splits use the averaged
    impurity across outputs.
    """
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        oob_score=True,
        n_jobs=n_jobs,
        random_state=random_state,
    )
    rf.fit(x_design, y_mat)
    return {
        "model": rf,
        "oob_prediction": rf.oob_prediction_,
        "n_estimators": n_estimators,
        "n_samples": len(y_mat),
    }


class LogColumns(BaseEstimator, TransformerMixin):
    """Natural-log transform a fixed subset of input columns.

    First stage of the MLP input pipeline: log-uniform parameters (e.g.
    ``value_per_yll``, ``ghg_price``) span several orders of magnitude, so
    feeding them raw makes the post-standardization feature heavy-tailed
    and the response strongly nonlinear in raw space.  Logging them first
    turns a log-uniform marginal into a uniform one and the typically
    log-linear response into a near-linear one.  Tree methods are invariant
    to monotone rescalings so they skip this; the MLP is not.
    """

    def __init__(self, log_indices: tuple[int, ...] = ()):
        self.log_indices = list(log_indices)

    def fit(self, x, y=None):
        return self

    def transform(self, x):
        x = np.asarray(x, dtype=float).copy()
        for j in self.log_indices:
            x[:, j] = np.log(x[:, j])
        return x


class AveragingEnsemble:
    """Average the (standardized) predictions of several fitted estimators.

    Fitting K MLPs that differ only in random seed and averaging their
    predictions both smooths and de-noises the surrogate: a single ReLU net is
    piecewise-linear and need not be monotone, and each member places its kinks
    (and its residual wiggle) differently, so the mean is markedly smoother and
    lower-variance.  ``predict`` returns the mean ``(n_samples, n_outputs)``
    matrix, so an ensemble is a drop-in replacement for a single estimator in
    :class:`MultiOutputPayload`.
    """

    def __init__(self, models: list):
        self.models = list(models)

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.mean([m.predict(x) for m in self.models], axis=0)


def fit_mlp_multi(
    x_design: np.ndarray,
    y_mat: np.ndarray,
    log_indices: list[int],
    hidden_layer_sizes: tuple[int, ...] = (256, 128, 64),
    solver: str = "adam",
    alpha: float = 1e-4,
    max_iter: int = 3000,
    learning_rate_init: float = 1e-3,
    n_iter_no_change: int = 40,
    activation: str = "relu",
    ensemble_size: int = 1,
    random_state: int = 42,
) -> dict:
    """Fit a multi-output MLP (or a seed-averaged ensemble) for all outputs.

    Each member is a :class:`~sklearn.pipeline.Pipeline` that logs the
    log-uniform input columns, standardizes all inputs, and regresses the
    (caller-standardized) ``y_mat`` with an :class:`~sklearn.neural_network.
    MLPRegressor`.  A ``relu`` MLP is a continuous piecewise-linear map (smooth,
    non-staircased gradients unlike the tree surrogates); ``tanh`` makes it
    C-infinity smooth at some cost to sharp-response accuracy.  ``predict``
    returns a ``(n_samples, n_outputs)`` matrix in the standardized target
    space, matching the other multi-output methods.

    ``ensemble_size > 1`` fits that many members with consecutive seeds and
    wraps them in an :class:`AveragingEnsemble`; the averaged response is
    smoother and less prone to non-monotone wiggle than any single net.

    ``solver='adam'`` is the default: it scales to the full ~14k-sample
    design and, with ``early_stopping`` on a held-out 10%, outperforms
    full-batch lbfgs (which converges to poorer multi-output minima here).
    ``early_stopping``/``n_iter_no_change`` apply only to the stochastic
    solvers (ignored by lbfgs).
    """
    if ensemble_size < 1:
        raise ValueError(f"ensemble_size must be >= 1, got {ensemble_size}")

    def make_member(seed: int) -> Pipeline:
        mlp_kwargs: dict[str, Any] = {
            "hidden_layer_sizes": hidden_layer_sizes,
            "activation": activation,
            "solver": solver,
            "alpha": alpha,
            "max_iter": max_iter,
            "random_state": seed,
        }
        if solver in ("adam", "sgd"):
            mlp_kwargs.update(
                learning_rate_init=learning_rate_init,
                early_stopping=True,
                n_iter_no_change=n_iter_no_change,
                validation_fraction=0.1,
            )
        return Pipeline(
            [
                ("log", LogColumns(tuple(log_indices))),
                ("scaler", StandardScaler()),
                ("mlp", MLPRegressor(**mlp_kwargs)),
            ]
        )

    members = [make_member(random_state + k) for k in range(ensemble_size)]
    for m in members:
        m.fit(x_design, y_mat)

    model = members[0] if ensemble_size == 1 else AveragingEnsemble(members)
    n_iter = int(np.mean([m.named_steps["mlp"].n_iter_ for m in members]))
    return {
        "model": model,
        "n_iter": n_iter,
        "n_samples": len(y_mat),
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
    vector_columns: set[str] | None = None,
    field_specs: dict[str, dict] | None = None,
) -> SurrogateBundle:
    """Fit a :class:`SurrogateBundle` for all available output columns.

    The caller is expected to have already dropped NaN-bearing scenarios
    from ``x_design`` and ``outputs_df``.  Train/test split is done here
    so all methods see the same partition.

    ``vector_columns``, if supplied, names columns originating from
    vector outputs.  Vector outputs are only supported by the
    multi-output methods (``xgb``, ``rf``, ``mlp``); requesting ``pce``
    on a bundle that contains any vector column raises
    :class:`NotImplementedError`.

    ``field_specs`` maps each ``kind: field`` output name to
    ``{"columns": [...], "n_components": k}``.  Each field's raw spatial
    columns (already present in ``outputs_df``) are PCA-compressed -- the
    PCA is fit on the training rows only and the surrogate is trained to
    predict the ``k`` score columns instead of the thousands of raw
    elements.  Like vector outputs, fields require a multi-output method.
    """
    if method not in SUPPORTED_METHODS:
        raise ValueError(
            f"Unknown surrogate method '{method}'. Supported: {SUPPORTED_METHODS}"
        )

    vector_columns = vector_columns or set()
    field_specs = field_specs or {}
    if (vector_columns or field_specs) and method not in _MULTI_OUTPUT_METHODS:
        kinds = "vector" if vector_columns else "field"
        raise NotImplementedError(
            f"Method '{method}' does not support {kinds} outputs; "
            f"use one of {_MULTI_OUTPUT_METHODS} or remove the vector/field "
            f"specs from sensitivity_analysis.outputs."
        )

    joint_dist, param_names = build_joint_distribution(generator_spec)
    method_options = dict(method_config.get("method_options", {}))

    # Input columns the MLP should log-transform first (log-uniform params).
    params_spec = generator_spec["parameters"]
    log_indices = [
        i
        for i, name in enumerate(param_names)
        if params_spec[name].get("distribution") == "log_uniform"
    ]

    n_total = len(x_design)
    n_holdout = int(n_total * holdout_fraction)
    n_train = n_total - n_holdout

    # PCA-compress field outputs (fit on training rows only).  Each field's
    # raw spatial columns are replaced, as surrogate targets, by its PCA
    # score columns; the raw columns are retained in ``outputs_df`` for
    # reconstruction validation below.
    field_decoders: dict[str, FieldDecoder] = {}
    train_columns = list(available_columns)
    work_df = outputs_df
    if field_specs:
        score_data: dict[str, np.ndarray] = {}
        for fname, fspec in field_specs.items():
            decoder, scores_all = _fit_field_pca(
                fname, fspec["columns"], outputs_df, n_train, fspec["n_components"]
            )
            field_decoders[fname] = decoder
            for j, sc in enumerate(decoder.score_columns):
                score_data[sc] = scores_all[:, j]
            train_columns.extend(decoder.score_columns)
            logger.info(
                "Field '%s': PCA %d elements -> %d components "
                "(%.4f cumulative explained variance)",
                fname,
                len(decoder.keys),
                len(decoder.score_columns),
                float(decoder.explained_variance_ratio.sum()),
            )
        # Append all PCA score columns in one concat. A chained ``assign`` would
        # insert the (potentially hundreds of) score columns one at a time into
        # an already very wide frame, re-fragmenting and copying it each time
        # (O(columns^2)); concatenating once is linear.
        scores_df = pd.DataFrame(score_data, index=outputs_df.index)
        work_df = pd.concat([outputs_df, scores_df], axis=1)

    x_train = x_design[:n_train]
    x_test = x_design[n_train:] if n_holdout > 0 else None
    outputs_train = work_df.iloc[:n_train]
    outputs_test = work_df.iloc[n_train:] if n_holdout > 0 else None

    logger.info(
        "Bundle fit (%s): %d train, %d holdout, %d targets (%d fields)",
        method,
        n_train,
        n_holdout,
        len(train_columns),
        len(field_decoders),
    )

    models: dict[str, Any] = {}
    validation: dict[str, dict] = {}

    # Field PCA-score targets, so _fit_multi_output can down-weight them
    # relative to the scalar/vector outputs (carbon-dial loss balancing).
    score_columns = {sc for d in field_decoders.values() for sc in d.score_columns}

    if method in _MULTI_OUTPUT_METHODS:
        # Only the MLP path applies scalar/field loss balancing (it is the dial
        # surrogate); xgb/rf train unweighted.
        priority_weight = (
            float(method_options["scalar_loss_weight"]) if method == "mlp" else 1.0
        )
        models, validation = _fit_multi_output(
            method,
            x_train,
            x_test,
            outputs_train,
            outputs_test,
            train_columns,
            method_options,
            n_threads,
            log_indices,
            priority_weight=priority_weight,
            field_score_columns=score_columns,
        )
    else:
        for col in available_columns:
            y_train = outputs_train[col].values
            y_test = outputs_test[col].values if outputs_test is not None else None

            if method == "pce":
                payload, val = _fit_pce_one(
                    x_train,
                    y_train,
                    x_test,
                    y_test,
                    joint_dist,
                    method_options,
                    n_threads,
                )
            else:
                raise AssertionError(f"unreachable method {method!r}")

            models[col] = payload
            validation[col] = val

    for col, val in validation.items():
        val["output"] = col
        val["n_train"] = n_train
        val["n_test"] = n_holdout
        val["method"] = method

        # Vector elements (e.g. minor foods) and PCA score columns often have
        # tiny mass / noisy higher-order modes; only flag genuinely poor
        # scalar fits at the warning level, log the rest more quietly.
        quiet = col in vector_columns or col in score_columns
        threshold = 0.25 if quiet else 0.1
        if val["validation_error"] > threshold:
            level = logger.info if quiet else logger.warning
            level(
                "High validation error (%.3f) for '%s' (%s)",
                val["validation_error"],
                col,
                method,
            )

    # Field reconstruction validation: predict the score columns on the
    # holdout, decode to the full field, and score against the true field.
    for fname, decoder in field_decoders.items():
        cols = field_specs[fname]["columns"]
        if x_test is not None:
            true_field = outputs_df[cols].iloc[n_train:].values.astype(float)
            x_eval = x_test
        else:
            true_field = outputs_df[cols].iloc[:n_train].values.astype(float)
            x_eval = x_train
        scores_pred = np.column_stack(
            [models[sc].predict(x_eval) for sc in decoder.score_columns]
        )
        field_pred = decoder.decode(scores_pred)
        ss_res = float(np.sum((true_field - field_pred) ** 2))
        # Baseline is the train-fitted PCA mean field (not the holdout's own
        # mean): this scores reconstruction against predicting the mean field,
        # so it is comparable across the train/holdout split.
        ss_tot = float(np.sum((true_field - decoder.mean) ** 2))
        recon_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        pc1 = decoder.score_columns[0]
        pc1_r2 = validation[pc1].get("r2_test") or validation[pc1].get("r2_train")
        validation[fname] = {
            "output": fname,
            "kind": "field",
            "n_components": len(decoder.score_columns),
            "n_elements": len(decoder.keys),
            "explained_variance": float(decoder.explained_variance_ratio.sum()),
            "field_recon_r2": recon_r2,
            "validation_error": 1.0 - recon_r2,
            "pc1_r2_test": float(pc1_r2) if pc1_r2 is not None else None,
            "n_train": n_train,
            "n_test": n_holdout,
            "method": method,
        }
        logger.info(
            "Field '%s' reconstruction R2 (holdout)=%.4f [%d PCs, %.4f explained var]",
            fname,
            recon_r2,
            len(decoder.score_columns),
            float(decoder.explained_variance_ratio.sum()),
        )

    return SurrogateBundle(
        method=method,
        generator_spec=dict(generator_spec),
        param_names=param_names,
        output_columns=list(train_columns),
        models=models,
        validation=validation,
        n_train=n_train,
        n_test=n_holdout,
        field_decoders=field_decoders,
    )


def _fit_field_pca(
    name: str,
    columns: list[str],
    outputs_df: pd.DataFrame,
    n_train: int,
    n_components: int,
) -> tuple[FieldDecoder, np.ndarray]:
    """Fit a PCA decoder for one field on the training rows and score all rows.

    Returns the :class:`FieldDecoder` and the ``(n_total, k)`` score matrix
    (scores for every row, computed with the train-fitted PCA).
    """
    field_matrix = outputs_df[columns].values.astype(float)
    n_keys = field_matrix.shape[1]
    k = min(int(n_components), n_keys, n_train)
    if k < int(n_components):
        logger.warning(
            "Field '%s': requested %d components capped to %d "
            "(n_elements=%d, n_train=%d)",
            name,
            n_components,
            k,
            n_keys,
            n_train,
        )
    pca = PCA(n_components=k, random_state=0)
    pca.fit(field_matrix[:n_train])
    scores_all = pca.transform(field_matrix)
    keys = [c[len(name) + 1 :] for c in columns]  # strip "{name}." prefix
    score_columns = [f"{name}.pc{i:02d}" for i in range(k)]
    decoder = FieldDecoder(
        name=name,
        keys=keys,
        score_columns=score_columns,
        mean=pca.mean_.copy(),
        components=pca.components_.copy(),
        explained_variance_ratio=pca.explained_variance_ratio_.copy(),
    )
    return decoder, scores_all


def _fit_pce_one(x_train, y_train, x_test, y_test, joint_dist, opts, n_jobs):
    max_degree = opts["max_degree"]
    cross_truncation = opts["cross_truncation"]
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


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def _fit_multi_output(
    method: str,
    x_train: np.ndarray,
    x_test: np.ndarray | None,
    outputs_train: pd.DataFrame,
    outputs_test: pd.DataFrame | None,
    available_columns: list[str],
    opts: dict,
    n_jobs: int,
    log_indices: list[int],
    priority_weight: float = 1.0,
    field_score_columns: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, dict]]:
    """Fit a single shared-structure estimator for all outputs.

    Standardizes each output column to mean 0, std 1 before fitting so the
    shared-tree objective (and RMSE-based early stopping for XGBoost) is
    not dominated by the output with the largest absolute scale.  Each
    per-output payload is a :class:`MultiOutputPayload` holding the shared
    estimator plus the column index and per-output mean/std.  Validation
    metrics are computed per output on the original (unstandardized) scale.

    ``priority_weight`` (> 1) up-weights every NON-field-score target (the
    scalar/vector outputs) relative to the field PCA-score targets in the
    shared squared-error objective.  Because the estimator minimizes total
    SE over standardized columns, scaling a column by ``sqrt(w)`` multiplies
    its loss contribution by ``w``; folding ``1/sqrt(w)`` back into the
    payload's ``target_std`` makes prediction invert it transparently.  This
    is how the carbon-dial bundle keeps emission/diet/cost fits sharp despite
    co-training with the hundreds of field-score targets that would otherwise
    dominate the loss (ch4 holdout R2 ~0.64 at w=1 -> ~0.99 at w=15).
    """
    field_score_columns = field_score_columns or set()
    y_train = outputs_train[available_columns].values.astype(float)
    y_test = (
        outputs_test[available_columns].values.astype(float)
        if outputs_test is not None
        else None
    )
    target_mean = y_train.mean(axis=0)
    target_std = y_train.std(axis=0)
    # Constant columns would lead to divide-by-zero; fall back to unit scale.
    safe_std = np.where(target_std > 0, target_std, 1.0)
    # Per-target loss weight applied in standardized space: sqrt(w) on the
    # priority (non-field-score) columns, 1 on the field-score columns.
    sqrt_w = np.where(
        [c not in field_score_columns for c in available_columns],
        np.sqrt(priority_weight),
        1.0,
    )
    y_train_std = (y_train - target_mean) / safe_std * sqrt_w
    y_test_std = (
        (y_test - target_mean) / safe_std * sqrt_w if y_test is not None else None
    )
    # The estimator predicts in the weighted-standardized space; dividing the
    # per-output std by sqrt(w) makes MultiOutputPayload.predict invert both
    # the standardization and the weighting in one multiply.
    payload_std = safe_std / sqrt_w

    if method == "xgb":
        fit_result = fit_xgboost_multi(
            x_train,
            y_train_std,
            x_val=x_test,
            y_val_mat=y_test_std,
            n_estimators=opts["n_estimators"],
            max_depth=opts["max_depth"],
            learning_rate=opts["learning_rate"],
            subsample=opts["subsample"],
            colsample_bytree=opts["colsample_bytree"],
            min_child_weight=opts["min_child_weight"],
            early_stopping_rounds=opts["early_stopping_rounds"],
            n_jobs=n_jobs,
        )
        shared_model = fit_result["model"]
        extra_val: dict[str, Any] = {"n_estimators": fit_result["n_estimators"]}
    elif method == "rf":
        fit_result = fit_random_forest_multi(
            x_train,
            y_train_std,
            n_estimators=opts["n_estimators"],
            n_jobs=n_jobs,
        )
        shared_model = fit_result["model"]
        extra_val = {"n_estimators": fit_result["n_estimators"]}
    elif method == "mlp":
        fit_result = fit_mlp_multi(
            x_train,
            y_train_std,
            log_indices,
            hidden_layer_sizes=tuple(opts["hidden_layer_sizes"]),
            solver=opts["solver"],
            alpha=opts["alpha"],
            max_iter=opts["max_iter"],
            learning_rate_init=opts["learning_rate_init"],
            n_iter_no_change=opts["n_iter_no_change"],
            activation=opts["activation"],
            ensemble_size=opts["ensemble_size"],
        )
        shared_model = fit_result["model"]
        extra_val = {"n_iter": fit_result["n_iter"]}
    else:
        raise AssertionError(f"unsupported multi-output method {method!r}")

    models: dict[str, Any] = {}
    validation: dict[str, dict] = {}
    for j, col in enumerate(available_columns):
        payload = MultiOutputPayload(
            model=shared_model,
            output_index=j,
            target_mean=float(target_mean[j]),
            target_std=float(payload_std[j]),
        )
        y_train_col = y_train[:, j]
        y_pred_train = payload.predict(x_train)
        r2_train = _r2(y_train_col, y_pred_train)

        if x_test is not None and y_test is not None:
            y_test_col = y_test[:, j]
            y_pred_test = payload.predict(x_test)
            r2_test = _r2(y_test_col, y_pred_test)
            validation_error = 1.0 - r2_test
        else:
            r2_test = None
            validation_error = 1.0 - r2_train

        val = {
            "validation_error": validation_error,
            "r2_train": r2_train,
            "r2_test": r2_test,
            **extra_val,
        }
        if method == "rf":
            oob_pred_col = shared_model.oob_prediction_[:, j]
            val["oob_error"] = 1.0 - _r2(
                y_train_col, oob_pred_col * payload_std[j] + target_mean[j]
            )
        models[col] = payload
        validation[col] = val

    return models, validation


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------


def save_bundle(bundle: SurrogateBundle, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(
        "Wrote %s bundle (%d outputs, %.1f MB) to %s",
        bundle.method,
        len(bundle.output_columns),
        path.stat().st_size / (1024 * 1024),
        path,
    )


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

    Returns an array of shape ``(len(x),)`` in the original output space.
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
    elif method in ("rf", "xgb", "mlp"):
        return model.predict(x)
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
    elif method in ("rf", "xgb", "mlp"):
        return model.predict
    else:
        raise AssertionError(f"unreachable method {method!r}")


def predict_field(
    bundle: SurrogateBundle, field_name: str, x: np.ndarray
) -> np.ndarray:
    """Predict a full spatial field at design matrix ``x``.

    Predicts the field's PCA score columns through the surrogate, then
    reconstructs the dense field via the stored :class:`FieldDecoder`.
    Returns an array of shape ``(len(x), n_elements)`` in the original field
    units; ``field_element_keys`` gives the matching element (e.g. region)
    labels for the columns.
    """
    if field_name not in bundle.field_decoders:
        raise KeyError(
            f"No field decoder for '{field_name}' "
            f"(have {sorted(bundle.field_decoders)})"
        )
    decoder = bundle.field_decoders[field_name]
    scores = np.column_stack([predict(bundle, sc, x) for sc in decoder.score_columns])
    return decoder.decode(scores)


def field_element_keys(bundle: SurrogateBundle, field_name: str) -> list[str]:
    """Element (e.g. region) labels for the columns of :func:`predict_field`."""
    if field_name not in bundle.field_decoders:
        raise KeyError(
            f"No field decoder for '{field_name}' "
            f"(have {sorted(bundle.field_decoders)})"
        )
    return list(bundle.field_decoders[field_name].keys)


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

    Mirrors the RF/XGB routine: reuses the same A/B free-parameter
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
    sobol_config: dict,
    slice_grid: dict[str, list[float]],
    columns: list[str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compute all Sobol rows (global, conditional, joint-conditional).

    Iterates over ``columns`` if given (e.g. the resolved
    ``sensitivity_analysis.sobol.outputs`` allowlist), otherwise over
    every column in the bundle.  ``sobol_config`` carries the MC sample
    counts (``n_mc_global``, ``n_mc_conditional``).
    """
    param_names = bundle.param_names
    slice_param_names = bundle.generator_spec.get("slice_parameters", [])
    slice_indices = [param_names.index(sp) for sp in slice_param_names]

    n_mc_global = int(sobol_config["n_mc_global"])
    n_mc_conditional = int(sobol_config["n_mc_conditional"])
    output_columns = columns if columns is not None else bundle.output_columns

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

    for col in output_columns:
        if col not in bundle.models:
            raise KeyError(
                f"Sobol output '{col}' missing from bundle (has {len(bundle.models)} columns)"
            )
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
