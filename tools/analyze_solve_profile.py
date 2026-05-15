#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Analyze a cProfile dump from solve_model.

Run::

    pixi run python tools/analyze_solve_profile.py path/to/model_scen-X.prof

Reports:
  * Top functions by cumulative and total (self) time
  * Aggregate time spent in linopy.* and pypsa.*
  * Cumulative time of high-level checkpoints
    (create_model, add_*_constraints, solve, etc.)
"""

import argparse
from collections import defaultdict
from pathlib import Path
import pstats
import sys


def aggregate_by_package(stats: pstats.Stats) -> dict[str, tuple[float, float, int]]:
    """Sum tottime and cumtime by package prefix."""
    buckets: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for func, (_cc, _nc, tt, ct, _callers) in stats.stats.items():
        filename, _line, _name = func
        if filename in ("~", ""):
            bucket = "<builtin>"
        else:
            parts = Path(filename).parts
            # Map to top-level package directory under site-packages
            try:
                sp_idx = parts.index("site-packages")
                bucket = parts[sp_idx + 1] if sp_idx + 1 < len(parts) else "<sp-root>"
            except ValueError:
                if "linopy" in filename:
                    bucket = "linopy"
                elif "pypsa" in filename:
                    bucket = "pypsa"
                elif "xarray" in filename:
                    bucket = "xarray"
                elif "/workflow/" in filename or filename.startswith("workflow"):
                    bucket = "workflow"
                else:
                    bucket = "<other>"
        b = buckets[bucket]
        b[0] += tt
        b[1] += ct  # not strictly additive, but useful as upper bound
        b[2] += 1
    return {k: (v[0], v[1], v[2]) for k, v in buckets.items()}


def find_checkpoint_times(stats: pstats.Stats, names: list[str]) -> dict[str, float]:
    """Return cumtime for each function name (matches first occurrence)."""
    found: dict[str, float] = {}
    for func, (_cc, _nc, _tt, ct, _callers) in stats.stats.items():
        _filename, _line, fname = func
        if fname in names and fname not in found:
            found[fname] = ct
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", type=Path)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    if not args.profile.is_file():
        print(f"profile not found: {args.profile}", file=sys.stderr)
        sys.exit(1)

    stats = pstats.Stats(str(args.profile))
    stats.strip_dirs()

    total_runtime = stats.total_tt
    print(f"Profile: {args.profile}")
    print(f"Total cprofile runtime (tottime sum): {total_runtime:.2f} s\n")

    print("=" * 80)
    print(f"Top {args.top} by cumulative time")
    print("=" * 80)
    stats.sort_stats("cumulative")
    stats.print_stats(args.top)

    print("=" * 80)
    print(f"Top {args.top} by self (tot) time")
    print("=" * 80)
    stats.sort_stats("tottime")
    stats.print_stats(args.top)

    print("=" * 80)
    print("Aggregate self time by package")
    print("=" * 80)
    by_pkg = aggregate_by_package(stats)
    rows = sorted(by_pkg.items(), key=lambda kv: -kv[1][0])
    print(f"{'package':<30} {'tottime [s]':>12} {'n_funcs':>10}")
    for pkg, (tt, _ct, n) in rows[:20]:
        print(f"{pkg:<30} {tt:>12.3f} {n:>10}")

    print("\n" + "=" * 80)
    print("Checkpoint cumulative times (high-level phases)")
    print("=" * 80)
    checkpoints = [
        "run_solve",
        "_run_solve",
        "create_model",
        "_run",  # linopy solver call wrapper
        "solve",
        "to_gurobipy",
        "add_constraints",
        "_extract_p_set_duals",
        "assign_solution",
        "assign_duals",
        "post_processing",
        "add_health_objective",
        "add_production_stability_constraints",
        "add_diet_stability_constraints",
        "add_residue_feed_constraints",
        "add_within_group_ratio_constraints",
        "add_macronutrient_constraints",
        "add_food_group_constraints",
        "add_animal_growth_cap_constraints",
        "add_crop_growth_cap_constraints",
        "add_bounded_subsidy_constraints",
        "_apply_forage_calibration",
    ]
    found = find_checkpoint_times(stats, checkpoints)
    for name in checkpoints:
        if name in found:
            print(f"  {name:<48} {found[name]:>8.2f} s")
        else:
            print(f"  {name:<48} <not found>")


if __name__ == "__main__":
    main()
