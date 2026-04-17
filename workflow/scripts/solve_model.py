# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Snakemake entry point for solving the model and exporting to netcdf.

All solve logic lives in ``workflow.scripts.solve_model.core``; this thin
wrapper calls :func:`run_solve` and writes the result to disk.
"""

import logging
from pathlib import Path

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.solve_model.core import _ShadowPriceLogFilter, run_solve


def _run_solve() -> None:
    """Solve the model and export to netcdf (Snakemake entry point)."""
    _logger = setup_script_logging(snakemake.log[0])
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())

    n = run_solve(snakemake, _logger)

    if n is None:
        # Write empty file so downstream rules see a (failed) output.
        Path(snakemake.output.network).touch()
        return

    netcdf_config = snakemake.params.netcdf
    n.export_to_netcdf(
        snakemake.output.network,
        compression=netcdf_config["compression"],
        float32=netcdf_config["float32"],
    )


if __name__ == "__main__":
    import os

    profile_enabled = os.environ.get("PROFILE_SOLVE", "0") == "1"

    if profile_enabled:
        import cProfile
        import pstats

        # Run with profiling
        profile_path = Path(snakemake.output.network).with_suffix(".prof")
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            _run_solve()
        finally:
            profiler.disable()
            # Save raw profile for later analysis (e.g., snakeviz)
            profiler.dump_stats(str(profile_path))

            # Print summary stats to log
            stats = pstats.Stats(profiler)
            stats.strip_dirs()
            stats.sort_stats("cumulative")

            # Print top 50 functions by cumulative time
            print("\n" + "=" * 80)
            print("PROFILING RESULTS - Top 50 by cumulative time")
            print("=" * 80)
            stats.print_stats(50)

            print("\n" + "=" * 80)
            print("PROFILING RESULTS - Top 50 by total time (self)")
            print("=" * 80)
            stats.sort_stats("tottime")
            stats.print_stats(50)

            print(f"\nFull profile saved to: {profile_path}")
            print("Analyze with: pixi run python -m snakeviz " + str(profile_path))
    else:
        _run_solve()
