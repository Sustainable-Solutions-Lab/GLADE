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


def _build_design(n: int = 256) -> tuple[np.ndarray, pd.DataFrame]:
    """Deterministic design + scalar outputs matching OUTPUT_COLUMNS shape."""
    rng = np.random.default_rng(0)
    x = rng.random((n, 2))
    x[:, 1] = 2 * x[:, 1] - 1  # map to [-1, 1] matching GEN_SPEC

    # Two distinct response surfaces, one smooth, one with interactions, so a
    # failure to route predict() through the right bundle entry shows up.
    y_smooth = 3 * x[:, 0] + 2 * x[:, 1]
    y_interact = 1.0 + x[:, 0] ** 2 + 0.5 * x[:, 0] * x[:, 1]

    df = pd.DataFrame(
        {
            "scenario": [f"synth_{i}" for i in range(n)],
            # fit_bundle expects the same column set as OUTPUT_COLUMNS; stub the
            # other two so the NaN-dropping logic doesn't remove everything.
            "total_cost": y_smooth,
            "ghg_emissions": y_smooth,
            "land_use": y_interact,
            "yll": y_interact,
        }
    )
    return x, df


@pytest.mark.parametrize(
    "method, method_config",
    [
        ("pce", {"method_options": {"max_degree": 3, "cross_truncation": 0.8}}),
        ("rf", {"method_options": {"n_estimators": 64}}),
        ("mars", {"method_options": {"max_terms": 20, "max_degree": 2}}),
        ("xgb", {"method_options": {"n_estimators": 200, "max_depth": 3}}),
    ],
)
def test_bundle_save_load_predict_parity(tmp_path: Path, method, method_config):
    """Round-trip a bundle through pickle and check predict() parity."""
    x, outputs_df = _build_design()
    bundle = fit_bundle(
        method=method,
        x_design=x,
        outputs_df=outputs_df,
        available_columns=["total_cost", "ghg_emissions", "land_use", "yll"],
        generator_spec=GEN_SPEC,
        method_config=method_config,
        holdout_fraction=0.2,
        n_threads=1,
    )
    assert isinstance(bundle, SurrogateBundle)
    assert bundle.method == method
    assert set(bundle.output_columns) == {
        "total_cost",
        "ghg_emissions",
        "land_use",
        "yll",
    }
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
