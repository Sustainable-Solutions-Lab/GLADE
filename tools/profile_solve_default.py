#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile-run the opt default scenario directly, skipping IIS computation.

This harness reads the manifest produced by ``tools/export-solve-manifest``,
calls ``run_solve`` under cProfile, and saves the profile next to the output.

Usage:
    pixi run -e gurobi python tools/profile_solve_default.py [manifest.json]
"""

import argparse
import cProfile
import json
import logging
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "manifest",
        nargs="?",
        default="/tmp/opt_manifest.json",
        type=Path,
    )
    ap.add_argument(
        "--scenario-index", type=int, default=0, help="Index into manifest scenarios"
    )
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    entry = manifest["scenarios"][args.scenario_index]
    shared = manifest.get("shared_params")

    out_net = entry["outputs"]["network"]
    Path(out_net).parent.mkdir(parents=True, exist_ok=True)
    Path(entry["log"]).parent.mkdir(parents=True, exist_ok=True)

    from workflow.scripts.logging_config import setup_script_logging
    from workflow.scripts.solve_model.core import _ShadowPriceLogFilter, run_solve
    from workflow.scripts.solve_namespace import build_namespace

    logger = setup_script_logging(entry["log"])
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())

    smk = build_namespace(entry, shared)

    # Stub IIS so an infeasible solve does not burn minutes on diagnostics.
    import linopy.model

    linopy.model.Model.compute_infeasibilities = lambda self: []  # type: ignore[method-assign]

    print(f"Scenario: {entry['scenario']}")
    print(f"Output:   {out_net}")

    prof_path = Path(out_net).with_suffix(".prof")
    profiler = cProfile.Profile()
    t0 = time.perf_counter()
    profiler.enable()
    try:
        n = run_solve(smk, logger)
    finally:
        profiler.disable()
        profiler.dump_stats(str(prof_path))
        print(f"Wallclock: {time.perf_counter() - t0:.2f} s")
        print(f"Profile:   {prof_path}")

    if n is not None:
        # Skip netcdf export; not needed for timing.
        Path(out_net).touch()


if __name__ == "__main__":
    main()
