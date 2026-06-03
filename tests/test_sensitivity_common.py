# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the sensitivity_common reducer registry, output spec
parsing, and vector-output expansion in load_scenario_outputs."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.analysis.sensitivity_common import (
    expand_vector_column,
    expanded_output_columns,
    load_scenario_outputs,
    parse_outputs_spec,
    sobol_columns,
    vector_output_columns,
)


def _write_pivot_parquet(path: Path, key_col: str, value_col: str, rows: dict):
    pd.DataFrame({key_col: list(rows), value_col: list(rows.values())}).to_parquet(path)


@pytest.fixture
def analysis_dir(tmp_path: Path) -> Path:
    """Three scenarios with overlapping but non-identical food sets."""
    base = tmp_path / "analysis"
    contents = {
        "s0": {"wheat": 100.0, "maize": 50.0},
        "s1": {"wheat": 110.0, "rice": 30.0},
        "s2": {"wheat": 0.0, "maize": 60.0, "rice": 25.0},  # zero-mass food kept
    }
    for scen, foods in contents.items():
        scen_dir = base / f"scen-{scen}"
        scen_dir.mkdir(parents=True)
        _write_pivot_parquet(
            scen_dir / "food_consumption.parquet", "food", "consumption_mt", foods
        )
        # Single-row scalar sidecar for the row_sum reducer.
        pd.DataFrame({"a": [1.0], "b": [2.0]}).to_parquet(
            scen_dir / "objective_breakdown.parquet"
        )
    return base


def _spec(**overrides):
    base = {
        "source": "x",
        "reducer": "row_sum",
        "label": "L",
        "units": "U",
    }
    base.update(overrides)
    return base


def test_parse_outputs_spec_defaults_kind_scalar():
    specs = parse_outputs_spec({"a": _spec()})
    assert len(specs) == 1
    assert specs[0].kind == "scalar"


def test_parse_outputs_spec_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown kind"):
        parse_outputs_spec({"bad": _spec(kind="matrix")})


def test_load_scalar_and_vector_with_union_keys(analysis_dir):
    cfg = {
        "total": _spec(source="objective_breakdown.parquet", reducer="row_sum"),
        "foods": _spec(
            kind="vector",
            source="food_consumption.parquet",
            reducer="pivot_column",
            key_col="food",
            value_col="consumption_mt",
        ),
    }
    specs = parse_outputs_spec(cfg)
    df = load_scenario_outputs(analysis_dir, ["s0", "s1", "s2"], specs)

    # Scalar column present.
    assert df["total"].tolist() == [3.0, 3.0, 3.0]

    # Vector expanded with union-of-keys, sorted, zero-filled.
    expected_food_cols = ["foods.maize", "foods.rice", "foods.wheat"]
    for col in expected_food_cols:
        assert col in df.columns
    np.testing.assert_array_equal(df["foods.wheat"].values, [100.0, 110.0, 0.0])
    np.testing.assert_array_equal(df["foods.rice"].values, [0.0, 30.0, 25.0])
    np.testing.assert_array_equal(df["foods.maize"].values, [50.0, 0.0, 60.0])


def test_load_parallel_matches_serial(analysis_dir):
    cfg = {
        "total": _spec(source="objective_breakdown.parquet", reducer="row_sum"),
        "foods": _spec(
            kind="vector",
            source="food_consumption.parquet",
            reducer="pivot_column",
            key_col="food",
            value_col="consumption_mt",
        ),
    }
    specs = parse_outputs_spec(cfg)
    names = ["s0", "s1", "s2"]
    serial = load_scenario_outputs(analysis_dir, names, specs, n_workers=1)
    parallel = load_scenario_outputs(analysis_dir, names, specs, n_workers=2)
    # Process-pool loading must reproduce the serial result exactly, including
    # row order (pool.map preserves input order).
    pd.testing.assert_frame_equal(serial, parallel)


def test_load_handles_missing_parquet_as_empty_vector(tmp_path: Path):
    base = tmp_path / "analysis"
    (base / "scen-s0").mkdir(parents=True)
    _write_pivot_parquet(
        base / "scen-s0" / "food_consumption.parquet",
        "food",
        "consumption_mt",
        {"x": 1.0},
    )
    (base / "scen-s1").mkdir(parents=True)
    # s1 has no parquet at all — vector reducer returns {} → zero-fill.

    spec = parse_outputs_spec(
        {
            "foods": _spec(
                kind="vector",
                source="food_consumption.parquet",
                reducer="pivot_column",
                key_col="food",
                value_col="consumption_mt",
            )
        }
    )
    df = load_scenario_outputs(base, ["s0", "s1"], spec)
    np.testing.assert_array_equal(df["foods.x"].values, [1.0, 0.0])


def test_expanded_columns_and_vector_columns(analysis_dir):
    cfg = {
        "total": _spec(source="objective_breakdown.parquet", reducer="row_sum"),
        "foods": _spec(
            kind="vector",
            source="food_consumption.parquet",
            reducer="pivot_column",
            key_col="food",
            value_col="consumption_mt",
        ),
    }
    specs = parse_outputs_spec(cfg)
    df = load_scenario_outputs(analysis_dir, ["s0", "s1", "s2"], specs)

    expanded = expanded_output_columns(specs, df)
    assert expanded[0] == "total"  # scalar comes first per spec order
    assert set(expanded[1:]) == {"foods.maize", "foods.rice", "foods.wheat"}

    vec = vector_output_columns(specs, df)
    assert vec == {"foods.maize", "foods.rice", "foods.wheat"}


def test_sobol_columns_resolves_scalar_and_vector(analysis_dir):
    cfg = {
        "total": _spec(source="objective_breakdown.parquet", reducer="row_sum"),
        "foods": _spec(
            kind="vector",
            source="food_consumption.parquet",
            reducer="pivot_column",
            key_col="food",
            value_col="consumption_mt",
        ),
    }
    specs = parse_outputs_spec(cfg)
    df = load_scenario_outputs(analysis_dir, ["s0", "s1", "s2"], specs)
    available = list(df.columns)

    assert sobol_columns({"outputs": ["total"]}, specs, available) == ["total"]

    expanded = sobol_columns({"outputs": ["foods"]}, specs, available)
    assert set(expanded) == {"foods.maize", "foods.rice", "foods.wheat"}


def test_sobol_columns_unknown_name_raises(analysis_dir):
    specs = parse_outputs_spec(
        {"total": _spec(source="objective_breakdown.parquet", reducer="row_sum")}
    )
    with pytest.raises(ValueError, match="unknown output 'nope'"):
        sobol_columns({"outputs": ["nope"]}, specs, ["total"])


def test_expand_vector_column_separator_is_dot():
    assert expand_vector_column("foods", "wheat") == "foods.wheat"


def test_pivot_row_expands_wide_single_row_parquet(tmp_path: Path):
    """``pivot_row`` should turn each column of a 1-row parquet into a
    vector element, skipping pandas' ``__index_level_*__`` artefact."""
    base = tmp_path / "analysis"
    (base / "scen-s0").mkdir(parents=True)
    (base / "scen-s1").mkdir(parents=True)
    pd.DataFrame(
        {"crop_production": [100.0], "trade": [10.0], "health_burden": [5.0]}
    ).to_parquet(base / "scen-s0" / "objective_breakdown.parquet")
    pd.DataFrame({"crop_production": [120.0], "ghg_cost": [-50.0]}).to_parquet(
        base / "scen-s1" / "objective_breakdown.parquet"
    )

    specs = parse_outputs_spec(
        {
            "obj": _spec(
                kind="vector",
                source="objective_breakdown.parquet",
                reducer="pivot_row",
            )
        }
    )
    df = load_scenario_outputs(base, ["s0", "s1"], specs)

    # Union of categories across scenarios, sorted, zero-filled.
    expected = {"obj.crop_production", "obj.ghg_cost", "obj.health_burden", "obj.trade"}
    assert expected.issubset(set(df.columns))
    # ``__index_level_*__`` must not leak through.
    assert not any(c.startswith("obj.__") for c in df.columns)
    np.testing.assert_array_equal(df["obj.crop_production"].values, [100.0, 120.0])
    np.testing.assert_array_equal(df["obj.ghg_cost"].values, [0.0, -50.0])
    np.testing.assert_array_equal(df["obj.health_burden"].values, [5.0, 0.0])
    np.testing.assert_array_equal(df["obj.trade"].values, [10.0, 0.0])
