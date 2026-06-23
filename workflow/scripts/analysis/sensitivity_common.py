# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for surrogate-based sensitivity analysis.

Three responsibilities:

1. Reconstruct the Sobol design matrix from a generator spec (so all
   callers re-read the same physical-parameter samples deterministically).
2. Load the per-scenario outputs declared in
   ``sensitivity_analysis.outputs`` using a small registry of parquet
   reducers.  Outputs come in two flavours:

   - ``scalar`` (default): one float per scenario, one column in the
     loaded DataFrame.
   - ``vector``: a dict ``{element: float}`` per scenario, expanded into
     one column per element (named ``{spec.name}.{element}``) using the
     union of element keys observed across the scenario set.

3. Resolve the Sobol allowlist (``sensitivity_analysis.sobol.outputs``)
   into the concrete column names downstream consumers iterate over.
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


VECTOR_KEY_SEP = "."  # foods.wheat, feed_categories.ruminant_grain, ...


@dataclass(frozen=True)
class OutputSpec:
    """A single surrogate target declared in ``sensitivity_analysis.outputs``."""

    name: str
    source: str
    reducer: str
    label: str
    units: str
    kind: str  # "scalar" | "vector" | "field"
    reducer_kwargs: dict[str, Any]
    n_components: int | None = None  # PCA rank for "field" outputs


# Keys consumed directly by OutputSpec; everything else in an output entry
# is forwarded verbatim to the reducer as keyword arguments.
_RESERVED_KEYS: frozenset[str] = frozenset(
    {"source", "reducer", "label", "units", "kind", "n_components"}
)


def parse_outputs_spec(cfg: dict) -> list[OutputSpec]:
    """Parse the ``sensitivity_analysis.outputs`` config block into specs.

    Preserves insertion order from the YAML mapping so downstream displays
    (Sobol plot panels, validation parquet rows) follow the config order.
    """
    specs = []
    for name, entry in cfg.items():
        kwargs = {k: v for k, v in entry.items() if k not in _RESERVED_KEYS}
        kind = entry.get("kind", "scalar")
        if kind not in ("scalar", "vector", "field"):
            raise ValueError(
                f"Output '{name}': unknown kind '{kind}' "
                f"(expected 'scalar', 'vector' or 'field')"
            )
        n_components = entry.get("n_components")
        if kind == "field" and not n_components:
            raise ValueError(
                f"Field output '{name}' requires a positive 'n_components' "
                f"(PCA rank); got {n_components!r}"
            )
        specs.append(
            OutputSpec(
                name=name,
                source=entry["source"],
                reducer=entry["reducer"],
                label=entry["label"],
                units=entry["units"],
                kind=kind,
                reducer_kwargs=kwargs,
                n_components=n_components,
            )
        )
    return specs


def output_display(cfg: dict) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Return ``(order, labels, units)`` for the Sobol plotting scripts."""
    order = list(cfg.keys())
    labels = {k: v["label"] for k, v in cfg.items()}
    units = {k: v["units"] for k, v in cfg.items()}
    return order, labels, units


def expand_vector_column(spec_name: str, key: str) -> str:
    """Canonical column name for one element of a vector output."""
    return f"{spec_name}{VECTOR_KEY_SEP}{key}"


def sobol_columns(
    sobol_cfg: dict, specs: list[OutputSpec], available: list[str]
) -> list[str]:
    """Resolve the Sobol allowlist into expanded output-column names.

    ``sobol_cfg["outputs"]`` lists OutputSpec names.  Scalar names map to
    themselves; vector names expand to all of their per-element columns
    that are actually present in ``available`` (the bundle's column set).
    """
    by_name = {spec.name: spec for spec in specs}
    allow: list[str] = []
    available_set = set(available)
    for name in sobol_cfg["outputs"]:
        if name not in by_name:
            raise ValueError(
                f"sobol.outputs references unknown output '{name}'; "
                f"known: {sorted(by_name)}"
            )
        spec = by_name[name]
        if spec.kind == "scalar":
            if name in available_set:
                allow.append(name)
        else:
            prefix = f"{name}{VECTOR_KEY_SEP}"
            allow.extend(c for c in available if c.startswith(prefix))
    return allow


REDUCERS: dict[str, Callable[..., float | dict[str, float]]] = {}


def register(name: str) -> Callable[[Callable], Callable]:
    """Decorator: register ``fn`` as a reducer under ``name``.

    Reducers take the scenario parquet path as their first positional
    argument and any number of keyword arguments forwarded from the
    output's config entry.  Scalar reducers return a single ``float``;
    vector reducers return a ``dict[str, float]``.  Either may return
    ``np.nan`` / ``{}`` when the file is missing or lacks the expected
    columns, so failed solves are visible to the scenario-dropping step
    in build_surrogate.
    """

    def decorator(fn):
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


@register("pivot_row")
def _pivot_row(path: Path) -> dict[str, float]:
    """Expand each column of a single-row parquet into a vector element.

    Vector reducer for wide-format outputs like ``objective_breakdown.parquet``,
    where every column is a separate category and there's exactly one row.
    Returns ``{}`` for missing/empty parquets so the scenario stays observable.
    """
    if not path.exists() or path.stat().st_size == 0:
        return {}
    table = pq.read_table(path)
    if table.num_rows == 0:
        return {}
    # Skip ``__index_level_*__`` artefacts that pandas writes when no
    # index name is set, so the surrogate target list stays clean.
    return {
        name: float(table.column(i)[0].as_py())
        for i, name in enumerate(table.column_names)
        if not name.startswith("__")
    }


@register("pivot_column")
def _pivot_column(path: Path, *, key_col: str, value_col: str) -> dict[str, float]:
    """Group ``value_col`` by ``key_col``, summing duplicate keys.

    Vector reducer.  Used to extract per-(food, country) or per-category
    series and reduce them across the non-key dimension.  Returns ``{}``
    for missing/empty parquets so the scenario is still observable
    (vector columns will be zero-filled at expansion time).
    """
    if not path.exists() or path.stat().st_size == 0:
        return {}
    schema = pq.read_schema(path)
    if key_col not in schema.names or value_col not in schema.names:
        return {}
    table = pq.read_table(path, columns=[key_col, value_col])
    if table.num_rows == 0:
        return {}
    keys = table.column(key_col).to_pylist()
    values = table.column(value_col).to_numpy()
    out: dict[str, float] = {}
    for k, v in zip(keys, values):
        out[str(k)] = out.get(str(k), 0.0) + float(v)
    return out


@register("region_field")
def _region_field(
    path: Path,
    *,
    value_col: str,
    key_col: str = "region",
    include_col: str | None = None,
    include_value: str | None = None,
    exclude_col: str | None = None,
    exclude_value: str | None = None,
) -> dict[str, float]:
    """Group ``value_col`` by ``key_col`` into a spatial field, with optional
    row filtering.

    Vector-style reducer intended for high-dimensional spatial outputs (e.g.
    per-region cropland or grazing area from ``land_use.parquet``).  Keep rows
    where ``include_col == include_value`` (if given) and drop rows where
    ``exclude_col == exclude_value`` (if given), then sum ``value_col`` per
    ``key_col``.  Returns ``{}`` for missing/empty parquets.  The full field is
    PCA-compressed at surrogate-fit time (see ``surrogate.fit_bundle``); a
    ``field`` OutputSpec must set ``n_components``.
    """
    if not path.exists() or path.stat().st_size == 0:
        return {}
    schema = pq.read_schema(path)
    needed = [key_col, value_col]
    if include_col:
        needed.append(include_col)
    if exclude_col:
        needed.append(exclude_col)
    if any(c not in schema.names for c in needed):
        return {}
    table = pq.read_table(path, columns=sorted(set(needed)))
    if table.num_rows == 0:
        return {}
    df = table.to_pandas()
    if include_col is not None:
        df = df[df[include_col] == include_value]
    if exclude_col is not None:
        df = df[df[exclude_col] != exclude_value]
    if df.empty:
        return {}
    grouped = df.groupby(key_col)[value_col].sum()
    return {str(k): float(v) for k, v in grouped.items()}


# ---------------------------------------------------------------------------
# Scenario output loading.
# ---------------------------------------------------------------------------


def _reduce(spec: OutputSpec, path: Path) -> float | dict[str, float]:
    return REDUCERS[spec.reducer](path, **spec.reducer_kwargs)


def _reduce_scenario(
    scenario_name: str, analysis_dir: Path, specs: list[OutputSpec]
) -> list:
    """Reduce every spec for a single scenario (one process-pool task).

    Returns the per-spec reductions in ``specs`` order so the caller can
    fan them back into per-spec columns.
    """
    scen_dir = analysis_dir / f"scen-{scenario_name}"
    return [_reduce(spec, scen_dir / spec.source) for spec in specs]


def load_scenario_outputs(
    analysis_dir: Path,
    scenario_names: list[str],
    specs: list[OutputSpec],
    n_workers: int = 1,
) -> pd.DataFrame:
    """Extract each spec's value(s) from every scenario.

    Scalar specs contribute one column; vector specs contribute one
    column per element observed across the scenario set, named
    ``{spec.name}.{element}``, with absent elements zero-filled.  Failed
    or empty scalar reductions become ``NaN`` so the caller can drop the
    affected scenarios before fitting.

    Loading reads a handful of small parquet files per scenario and is
    metadata/IOPS-bound on the cluster's parallel filesystem.  Set
    ``n_workers > 1`` to fan the per-scenario reductions over a process
    pool; on Sherlock this cuts the (cold-cache) load of an 8k-sample
    design from minutes to under a minute.
    """
    n = len(scenario_names)
    raw: dict[str, list] = {spec.name: [] for spec in specs}
    if n_workers > 1 and n > 1:
        import concurrent.futures as cf
        from functools import partial
        import multiprocessing as mp

        # ``forkserver`` forks workers from a clean single-threaded server
        # process, avoiding the deadlock risk of forking a parent that
        # already holds pyarrow/BLAS thread pools.
        worker = partial(_reduce_scenario, analysis_dir=analysis_dir, specs=specs)
        with cf.ProcessPoolExecutor(
            max_workers=n_workers, mp_context=mp.get_context("forkserver")
        ) as pool:
            per_scenario = pool.map(worker, scenario_names, chunksize=16)
            for reductions in per_scenario:
                for spec, value in zip(specs, reductions):
                    raw[spec.name].append(value)
    else:
        for scenario_name in scenario_names:
            for spec, value in zip(
                specs, _reduce_scenario(scenario_name, analysis_dir, specs)
            ):
                raw[spec.name].append(value)

    columns: dict[str, list] = {"scenario": list(scenario_names)}
    for spec in specs:
        if spec.kind == "scalar":
            columns[spec.name] = raw[spec.name]
            continue
        # Vector: union-of-keys reindex with zero fill.
        keys: list[str] = []
        seen: set[str] = set()
        for d in raw[spec.name]:
            for k in d:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        keys.sort()
        for k in keys:
            col = expand_vector_column(spec.name, k)
            columns[col] = [d.get(k, 0.0) for d in raw[spec.name]]
    return pd.DataFrame(columns, index=range(n))


def expanded_output_columns(
    specs: list[OutputSpec], outputs_df: pd.DataFrame
) -> list[str]:
    """Return the ordered list of value columns produced by ``load_scenario_outputs``.

    Preserves spec order; within a vector spec, elements follow their
    sorted order (matching the loader).
    """
    cols: list[str] = []
    for spec in specs:
        if spec.kind == "scalar":
            cols.append(spec.name)
        else:
            prefix = f"{spec.name}{VECTOR_KEY_SEP}"
            cols.extend(c for c in outputs_df.columns if c.startswith(prefix))
    return cols


def vector_output_columns(
    specs: list[OutputSpec], outputs_df: pd.DataFrame
) -> set[str]:
    """Subset of ``expanded_output_columns`` originating from vector specs."""
    out: set[str] = set()
    for spec in specs:
        if spec.kind != "vector":
            continue
        prefix = f"{spec.name}{VECTOR_KEY_SEP}"
        out.update(c for c in outputs_df.columns if c.startswith(prefix))
    return out


def field_output_columns(specs: list[OutputSpec], outputs_df: pd.DataFrame) -> set[str]:
    """Subset of ``expanded_output_columns`` originating from field specs.

    These are the raw per-element spatial columns; the surrogate does not
    train on them directly but PCA-compresses each field's matrix and trains
    on the resulting scores (see ``surrogate.fit_bundle``).
    """
    out: set[str] = set()
    for spec in specs:
        if spec.kind != "field":
            continue
        prefix = f"{spec.name}{VECTOR_KEY_SEP}"
        out.update(c for c in outputs_df.columns if c.startswith(prefix))
    return out


def field_columns_by_spec(
    specs: list[OutputSpec], outputs_df: pd.DataFrame
) -> dict[str, list[str]]:
    """Ordered per-element columns for each field spec, keyed by spec name.

    Element columns follow their sorted order (matching the loader), so the
    column order is a stable key index a fitted PCA decoder can rely on.
    """
    by_spec: dict[str, list[str]] = {}
    for spec in specs:
        if spec.kind != "field":
            continue
        prefix = f"{spec.name}{VECTOR_KEY_SEP}"
        cols = [c for c in outputs_df.columns if c.startswith(prefix)]
        if cols:
            by_spec[spec.name] = cols
    return by_spec
