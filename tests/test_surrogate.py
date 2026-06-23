# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the shared surrogate module.

Covers save/load round-trips and ``predict()`` parity for each
supported method, using a small synthetic design so the tests run
quickly.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.analysis.surrogate import (
    SurrogateBundle,
    fit_bundle,
    load_bundle,
    predict,
    save_bundle,
)

GEN_SPEC = {
    "name": "synth_{sample_id}",
    "mode": "sensitivity",
    "samples": 256,
    "seed": 7,
    "slice_parameters": [],
    "parameters": {
        "a": {"lower": 0.0, "upper": 1.0},
        "b": {"lower": -1.0, "upper": 1.0},
    },
    "template": {},
}


_COLUMNS = ["total_cost", "co2", "ch4", "n2o", "land_use", "yll"]

# fit_bundle indexes method_options directly (config is assumed complete), so
# tests spell out the full option set per method, mirroring config/default.yaml.
# n_estimators is varied per test; the rest are shared.
_XGB_OPTS = {
    "max_depth": 3,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "early_stopping_rounds": 50,
}


def _build_design(n: int = 256) -> tuple[np.ndarray, pd.DataFrame]:
    """Deterministic design + scalar outputs matching OUTPUT_COLUMNS shape."""
    rng = np.random.default_rng(0)
    x = rng.random((n, 2))
    x[:, 1] = 2 * x[:, 1] - 1  # map to [-1, 1] matching GEN_SPEC

    # Two distinct response surfaces, one smooth, one with interactions, plus
    # heterogeneously scaled outputs, so a failure to route predict() through
    # the right bundle entry (or to rescale correctly) shows up.
    y_smooth = 3 * x[:, 0] + 2 * x[:, 1]
    y_interact = 1.0 + x[:, 0] ** 2 + 0.5 * x[:, 0] * x[:, 1]

    df = pd.DataFrame(
        {
            "scenario": [f"synth_{i}" for i in range(n)],
            "total_cost": 1000.0 * y_smooth,
            "co2": -50.0 * y_smooth,
            "ch4": 5.0 * y_interact,
            "n2o": 3.0 * y_interact,
            "land_use": 100.0 * y_interact,
            "yll": 10.0 * y_smooth,
        }
    )
    return x, df


@pytest.mark.parametrize(
    "method, method_config",
    [
        ("pce", {"method_options": {"max_degree": 3, "cross_truncation": 0.8}}),
        ("rf", {"method_options": {"n_estimators": 64}}),
        ("xgb", {"method_options": {"n_estimators": 200, **_XGB_OPTS}}),
    ],
)
def test_bundle_save_load_predict_parity(tmp_path: Path, method, method_config):
    """Round-trip a bundle through pickle and check predict() parity."""
    x, outputs_df = _build_design()
    bundle = fit_bundle(
        method=method,
        x_design=x,
        outputs_df=outputs_df,
        available_columns=_COLUMNS,
        generator_spec=GEN_SPEC,
        method_config=method_config,
        holdout_fraction=0.2,
        n_threads=1,
    )
    assert isinstance(bundle, SurrogateBundle)
    assert bundle.method == method
    assert set(bundle.output_columns) == set(_COLUMNS)
    assert bundle.n_train + bundle.n_test == len(x)

    path = tmp_path / f"surrogate_{method}.pkl"
    save_bundle(bundle, path)
    restored = load_bundle(path)

    assert restored.method == bundle.method
    assert restored.param_names == bundle.param_names

    # Predict parity at a fresh design, each output.
    rng = np.random.default_rng(1)
    x_eval = rng.random((32, 2))
    x_eval[:, 1] = 2 * x_eval[:, 1] - 1

    for col in bundle.output_columns:
        y1 = predict(bundle, col, x_eval)
        y2 = predict(restored, col, x_eval)
        np.testing.assert_allclose(y1, y2, rtol=1e-10, atol=1e-10)


def test_load_rejects_non_bundle(tmp_path: Path):
    import pickle

    path = tmp_path / "not_a_bundle.pkl"
    with path.open("wb") as f:
        pickle.dump({"not": "a bundle"}, f)
    with pytest.raises(TypeError, match="Expected SurrogateBundle"):
        load_bundle(path)


def test_unknown_method_raises():
    x, outputs_df = _build_design(n=64)
    with pytest.raises(ValueError, match="Unknown surrogate method"):
        fit_bundle(
            method="nope",
            x_design=x,
            outputs_df=outputs_df,
            available_columns=["total_cost"],
            generator_spec=GEN_SPEC,
            method_config={"method_options": {}},
            holdout_fraction=0.0,
            n_threads=1,
        )


def test_vector_outputs_blocked_for_scalar_only_methods():
    """PCE must reject any vector-derived columns."""
    x, outputs_df = _build_design(n=64)
    outputs_df["foods.wheat"] = outputs_df["total_cost"] * 0.001
    with pytest.raises(NotImplementedError, match="does not support vector outputs"):
        fit_bundle(
            method="pce",
            x_design=x,
            outputs_df=outputs_df,
            available_columns=[*_COLUMNS, "foods.wheat"],
            generator_spec=GEN_SPEC,
            method_config={
                "method_options": {"max_degree": 1, "cross_truncation": 0.5}
            },
            holdout_fraction=0.0,
            n_threads=1,
            vector_columns={"foods.wheat"},
        )


def test_xgb_handles_vector_outputs():
    """A vector-derived column fits cleanly through the multi-output path."""
    x, outputs_df = _build_design(n=128)
    # Two correlated "foods" with different scales.
    outputs_df["foods.wheat"] = 200.0 * x[:, 0] + 50.0
    outputs_df["foods.rice"] = -30.0 * x[:, 1] + 10.0
    cols = [*_COLUMNS, "foods.wheat", "foods.rice"]

    bundle = fit_bundle(
        method="xgb",
        x_design=x,
        outputs_df=outputs_df,
        available_columns=cols,
        generator_spec=GEN_SPEC,
        method_config={"method_options": {"n_estimators": 100, **_XGB_OPTS}},
        holdout_fraction=0.2,
        n_threads=1,
        vector_columns={"foods.wheat", "foods.rice"},
    )
    assert set(bundle.output_columns) == set(cols)
    # Predict shape / scale: foods.wheat is in the 50-250 range.
    y = predict(bundle, "foods.wheat", x[:8])
    assert y.shape == (8,)
    assert 0 < y.mean() < 500
