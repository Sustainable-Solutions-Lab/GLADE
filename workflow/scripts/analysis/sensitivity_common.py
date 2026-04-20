# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for surrogate-based sensitivity analysis.

Two responsibilities:

1. Reconstruct the Sobol design matrix from a generator spec (so all
   callers re-read the same physical-parameter samples deterministically).
2. Load the per-scenario scalar outputs declared in
   ``sensitivity_analysis.outputs`` using a small registry of parquet
   reducers.  Adding a new scalar output is a config-only edit as long as
   one of the existing reducers fits; adding a new reducer is one function
   plus an ``@register`` decorator.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats.qmc import Sobol

from workflow.scenario_generators import build_joint_distribution

# ---------------------------------------------------------------------------
# Sobol design reconstruction.
# ---------------------------------------------------------------------------


def reconstruct_samples(generator_spec: dict) -> np.ndarray:
    """Regenerate the Sobol design matrix from the generator spec.

    Deterministic given the same seed and sample count.
    """
    param_names = list(generator_spec["parameters"].keys())
    d = len(param_names)
    n_samples = generator_spec["samples"]
    seed = generator_spec.get("seed", 42)

    joint_dist, _ = build_joint_distribution(generator_spec)

    sampler = Sobol(d, scramble=True, seed=seed)
    unit_samples = sampler.random(n_samples)
    physical_samples = joint_dist.inv(unit_samples.T)
    return physical_samples.T


# ---------------------------------------------------------------------------
# Declarative outputs: config parsing + reducer registry.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputSpec:
    """A single surrogate target declared in ``sensitivity_analysis.outputs``."""

    name: str
    source: str
    reducer: str
    label: str
    units: str
    reducer_kwargs: dict[str, Any]


# Keys consumed directly by OutputSpec; everything else in an output entry
# is forwarded verbatim to the reducer as keyword arguments.
_RESERVED_KEYS: frozenset[str] = frozenset({"source", "reducer", "label", "units"})


def parse_outputs_spec(cfg: dict) -> list[OutputSpec]:
    """Parse the ``sensitivity_analysis.outputs`` config block into specs.

    Preserves insertion order from the YAML mapping so downstream displays
    (Sobol plot panels, validation parquet rows) follow the config order.
    """
    specs = []
    for name, entry in cfg.items():
        kwargs = {k: v for k, v in entry.items() if k not in _RESERVED_KEYS}
        specs.append(
            OutputSpec(
                name=name,
                source=entry["source"],
                reducer=entry["reducer"],
                label=entry["label"],
                units=entry["units"],
                reducer_kwargs=kwargs,
            )
        )
    return specs


def output_display(cfg: dict) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Return ``(order, labels, units)`` for the Sobol plotting scripts."""
    order = list(cfg.keys())
    labels = {k: v["label"] for k, v in cfg.items()}
    units = {k: v["units"] for k, v in cfg.items()}
    return order, labels, units


REDUCERS: dict[str, Callable[..., float]] = {}


def register(name: str) -> Callable[[Callable], Callable]:
    """Decorator: register ``fn`` as a reducer under ``name``.

    Reducers take the scenario parquet path as their first positional
    argument and any number of keyword arguments forwarded from the
    output's config entry.  They must return a single float (``np.nan``
    when the file is missing or lacks the expected columns, so failed
    solves are visible to the scenario-dropping step in build_surrogate).
    """

    def decorator(fn: Callable[..., float]) -> Callable[..., float]:
        if name in REDUCERS:
            raise ValueError(f"Reducer {name!r} already registered")
        REDUCERS[name] = fn
        return fn

    return decorator


@register("row_sum")
def _row_sum(path: Path) -> float:
    """Sum of all columns of a single-row parquet (e.g. objective breakdown)."""
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    table = pq.read_table(path)
    if table.num_rows == 0:
        return np.nan
    return float(sum(table.column(i)[0].as_py() for i in range(table.num_columns)))


@register("column_sum")
def _column_sum(path: Path, *, column: str) -> float:
    """Sum of one named column across all rows."""
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    schema = pq.read_schema(path)
    if column not in schema.names:
        return np.nan
    table = pq.read_table(path, columns=[column])
    if table.num_rows == 0:
        return np.nan
    return float(table.column(column).to_numpy().sum())


@register("filter_sum")
def _filter_sum(
    path: Path,
    *,
    filter_col: str,
    filter_value: str,
    value_col: str,
) -> float:
    """Sum of ``value_col`` over rows where ``filter_col == filter_value``."""
    if not path.exists() or path.stat().st_size == 0:
        return np.nan
    schema = pq.read_schema(path)
    if filter_col not in schema.names or value_col not in schema.names:
        return np.nan
    table = pq.read_table(path, columns=[filter_col, value_col])
    if table.num_rows == 0:
        return np.nan
    keys = table.column(filter_col).to_pylist()
    values = table.column(value_col).to_numpy()
    mask = np.fromiter((k == filter_value for k in keys), dtype=bool, count=len(keys))
    return float(values[mask].sum())


# ---------------------------------------------------------------------------
# Scenario output loading.
# ---------------------------------------------------------------------------


def load_scenario_outputs(
    analysis_dir: Path,
    scenario_names: list[str],
    specs: list[OutputSpec],
) -> pd.DataFrame:
    """Extract each spec's scalar from every scenario and return a DataFrame.

    One row per scenario; one column per spec (plus ``scenario``).  Missing
    or schema-less parquets yield ``NaN`` so the caller can drop failed
    solves before fitting.
    """
    n = len(scenario_names)
    columns: dict[str, list] = {
        "scenario": list(scenario_names),
        **{spec.name: [np.nan] * n for spec in specs},
    }
    for i, scenario_name in enumerate(scenario_names):
        scen_dir = analysis_dir / f"scen-{scenario_name}"
        for spec in specs:
            reducer = REDUCERS[spec.reducer]
            columns[spec.name][i] = reducer(
                scen_dir / spec.source, **spec.reducer_kwargs
            )
    return pd.DataFrame(columns)
