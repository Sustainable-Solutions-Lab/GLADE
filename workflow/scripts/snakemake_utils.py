# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Utilities for Snakemake workflow execution."""

from pathlib import Path
import sys

import pypsa

# Add workflow directory to path for imports
_workflow_dir = Path(__file__).parent.parent
if str(_workflow_dir) not in sys.path:
    sys.path.insert(0, str(_workflow_dir))

from scenario_generators import expand_scenario_defs  # noqa: E402


class FailedSolveError(RuntimeError):
    """Raised when a downstream rule tries to read a solve output that is
    actually the empty placeholder written by solve_model.py when the solve
    failed (infeasible, unbounded, time-limited, solver error).

    The placeholder behaviour is intentional - GSA / scenario sweeps want a
    handful of failed solves to leave the rest of the DAG runnable - but
    individual analysis or plotting rules must surface a clear error rather
    than crash on an unreadable netcdf.
    """


def load_solved_network(path: str | Path) -> pypsa.Network:
    """Load a solved PyPSA network, raising a clear error on failed solves.

    A failed solve writes a zero-byte placeholder at ``path`` (see
    ``workflow/scripts/solve_model.py``); attempting to read that with
    ``pypsa.Network(path)`` produces an opaque netcdf parser traceback.
    Detect the placeholder up front and raise ``FailedSolveError`` with
    enough context for the user to find the offending solve log.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Solved network not found: {p}")
    if p.stat().st_size == 0:
        raise FailedSolveError(
            f"Solved network at {p} is an empty placeholder, which means the "
            "upstream solve failed (infeasible, unbounded, time-limited, or "
            "solver error). Inspect the corresponding solve_model log under "
            "logs/{name}/solve_model_scen-*.log for the IIS / solver message, "
            "then rerun the solve once fixed."
        )
    return pypsa.Network(str(p))


def _recursive_update(target: dict, source: dict) -> dict:
    """Recursively update the target dictionary with the source dictionary.

    Raises if a scenario override would silently replace a nested
    dictionary subtree with ``None`` -- a common YAML mistake (writing
    ``key:`` with no value or ``key: null``) that otherwise surfaces
    deep in solve-time code as cryptic ``TypeError: 'NoneType' is not
    subscriptable``.
    """
    for key, value in source.items():
        if value is None and key in target and isinstance(target[key], dict):
            raise ValueError(
                f"Scenario override sets '{key}' to null but the base "
                "config has a dict at that key; use an empty dict ({}) "
                "to clear the subtree or supply the intended keys."
            )
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            _recursive_update(target[key], value)
        else:
            target[key] = value
    return target


def load_scenarios(config: dict) -> dict:
    """Load scenario definitions from the config's `scenarios` key."""
    raw_defs = config.get("scenarios") or {}
    return expand_scenario_defs(raw_defs)


def apply_scenario_config(config: dict, scenario_name: str) -> None:
    """Apply scenario config overrides in-place.

    Parameters
    ----------
    config : dict
        The Snakemake config dictionary (will be modified in-place)
    scenario_name : str
        The scenario name (e.g., "HG", "HighGHG")
    """
    if not scenario_name:
        return

    scenarios = load_scenarios(config)

    if scenario_name not in scenarios:
        raise ValueError(
            f"Scenario '{scenario_name}' not found in scenario definitions."
        )

    overrides = scenarios[scenario_name]
    _recursive_update(config, overrides)
