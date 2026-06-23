# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for PCA-compressed spatial ``field`` surrogate outputs.

A field output (e.g. per-region cropland area) is a high-dimensional
spatial vector that the surrogate compresses with PCA: it predicts a few
score columns and reconstructs the dense field via a stored decoder.  These
tests use a synthetic low-rank field whose scores are smooth functions of
the design parameters, so a correct pipeline must reconstruct it well.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from workflow.scripts.analysis.sensitivity_common import (
    REDUCERS,
    field_columns_by_spec,
    parse_outputs_spec,
)
from workflow.scripts.analysis.surrogate import (
    field_element_keys,
    fit_bundle,
    load_bundle,
    predict_field,
    save_bundle,
)

GEN_SPEC = {
    "name": "synth_{sample_id}",
    "mode": "sensitivity",
    "samples": 600,
    "seed": 11,
    "slice_parameters": [],
    "parameters": {
        "a": {"lower": 0.0, "upper": 1.0},
        "b": {"lower": -1.0, "upper": 1.0},
    },
    "template": {},
}

N_REGIONS = 60
FIELD = "cropland"


def _build_design(n: int = 600, noise: float = 0.0):
    """Design + a synthetic rank-4 spatial field with smooth score functions.

    field = base + S(params) @ B, with S four smooth functions of (a, b) and
    B fixed random spatial bases.  Exactly low-rank (+ optional noise), so a
    correct PCA+surrogate pipeline reconstructs it to high R2.
    """
    rng = np.random.default_rng(0)
    x = rng.random((n, 2))
    x[:, 1] = 2 * x[:, 1] - 1  # b in [-1, 1]
    a, b = x[:, 0], x[:, 1]

    scores = np.column_stack([a, b, a * b, a**2])  # (n, 4) smooth in params
    bases = rng.normal(size=(4, N_REGIONS))
    base = rng.uniform(1.0, 5.0, size=N_REGIONS)
    field = base + scores @ bases
    if noise:
        field = field + rng.normal(scale=noise, size=field.shape)

    cols = {f"{FIELD}.r{j:02d}": field[:, j] for j in range(N_REGIONS)}
    df = pd.DataFrame(
        {
            "scenario": [f"synth_{i}" for i in range(n)],
            "total_cost": 1000.0 * (3 * a + 2 * b),  # a scalar output too
            **cols,
        }
    )
    return x, df


def _field_specs(df):
    field_cols = [c for c in df.columns if c.startswith(f"{FIELD}.")]
    return {FIELD: {"columns": sorted(field_cols), "n_components": 6}}


@pytest.mark.parametrize("method", ["mlp", "xgb", "rf"])
def test_field_reconstruction(method):
    """Surrogate should reconstruct the low-rank field to high holdout R2."""
    x, df = _build_design()
    cfg = {"method_options": {}}
    if method == "xgb":
        cfg["method_options"] = {"n_estimators": 300, "max_depth": 3}
    elif method == "rf":
        cfg["method_options"] = {"n_estimators": 128}
    bundle = fit_bundle(
        method=method,
        x_design=x,
        outputs_df=df,
        available_columns=["total_cost"],
        generator_spec=GEN_SPEC,
        method_config=cfg,
        holdout_fraction=0.2,
        n_threads=1,
        field_specs=_field_specs(df),
    )

    # decoder present and score columns are the surrogate targets
    assert FIELD in bundle.field_decoders
    dec = bundle.field_decoders[FIELD]
    assert len(dec.score_columns) == 6
    assert all(sc in bundle.output_columns for sc in dec.score_columns)
    # raw field columns must NOT be individual surrogate targets
    assert not any(c.startswith(f"{FIELD}.r") for c in bundle.output_columns)
    # PCA captures (near) all variance of a rank-4 field with 6 components
    assert dec.explained_variance_ratio.sum() > 0.999

    # holdout reconstruction quality recorded in validation
    recon = bundle.validation[FIELD]["field_recon_r2"]
    assert recon > 0.9, f"{method} field recon R2 too low: {recon}"

    # predict_field returns the dense field with matching shape + keys
    pred = predict_field(bundle, FIELD, x[:5])
    assert pred.shape == (5, N_REGIONS)
    keys = field_element_keys(bundle, FIELD)
    assert keys == [f"r{j:02d}" for j in range(N_REGIONS)]


def test_field_save_load_roundtrip(tmp_path: Path):
    """Decoder + predict_field survive a pickle round-trip exactly."""
    x, df = _build_design()
    bundle = fit_bundle(
        method="mlp",
        x_design=x,
        outputs_df=df,
        available_columns=["total_cost"],
        generator_spec=GEN_SPEC,
        method_config={"method_options": {}},
        holdout_fraction=0.2,
        field_specs=_field_specs(df),
    )
    p = tmp_path / "bundle.pkl"
    save_bundle(bundle, p)
    reloaded = load_bundle(p)
    assert FIELD in reloaded.field_decoders
    np.testing.assert_allclose(
        predict_field(bundle, FIELD, x[:10]),
        predict_field(reloaded, FIELD, x[:10]),
    )


def test_field_requires_multi_output_method():
    """pce/mars cannot do fields (PCA scores are multi-output)."""
    x, df = _build_design()
    with pytest.raises(NotImplementedError):
        fit_bundle(
            method="pce",
            x_design=x,
            outputs_df=df,
            available_columns=["total_cost"],
            generator_spec=GEN_SPEC,
            method_config={"method_options": {}},
            holdout_fraction=0.2,
            field_specs=_field_specs(df),
        )


def test_region_field_reducer(tmp_path: Path):
    """region_field filters rows then groups value_col by key_col."""
    df = pd.DataFrame(
        {
            "crop": ["wheat", "wheat", "grassland", "maize", "grassland"],
            "region": ["r0", "r1", "r0", "r1", "r1"],
            "area_mha": [1.0, 2.0, 10.0, 3.0, 20.0],
        }
    )
    path = tmp_path / "land_use.parquet"
    pq.write_table(pa.Table.from_pandas(df), path)
    reducer = REDUCERS["region_field"]

    cropland = reducer(
        path,
        value_col="area_mha",
        key_col="region",
        exclude_col="crop",
        exclude_value="grassland",
    )
    assert cropland == {"r0": 1.0, "r1": 5.0}  # 2 wheat + 3 maize in r1

    grazing = reducer(
        path,
        value_col="area_mha",
        key_col="region",
        include_col="crop",
        include_value="grassland",
    )
    assert grazing == {"r0": 10.0, "r1": 20.0}

    # missing file -> empty
    assert reducer(tmp_path / "nope.parquet", value_col="area_mha") == {}


def test_parse_field_spec_requires_n_components():
    """A field output without n_components is a config error."""
    cfg = {
        "cropland": {
            "kind": "field",
            "source": "land_use.parquet",
            "reducer": "region_field",
            "value_col": "area_mha",
            "label": "Cropland",
            "units": "Mha",
        }
    }
    with pytest.raises(ValueError, match="n_components"):
        parse_outputs_spec(cfg)

    cfg["cropland"]["n_components"] = 10
    specs = parse_outputs_spec(cfg)
    assert specs[0].kind == "field"
    assert specs[0].n_components == 10
    # reducer kwargs exclude reserved keys
    assert "n_components" not in specs[0].reducer_kwargs
    assert specs[0].reducer_kwargs["value_col"] == "area_mha"


def test_field_columns_by_spec():
    """field_columns_by_spec maps a field spec to its sorted element columns."""
    _, df = _build_design(n=10)
    specs = parse_outputs_spec(
        {
            FIELD: {
                "kind": "field",
                "source": "land_use.parquet",
                "reducer": "region_field",
                "value_col": "area_mha",
                "n_components": 6,
                "label": "Cropland",
                "units": "Mha",
            }
        }
    )
    by_spec = field_columns_by_spec(specs, df)
    assert set(by_spec) == {FIELD}
    assert by_spec[FIELD] == sorted(c for c in df.columns if c.startswith(f"{FIELD}."))
