# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute Sobol sensitivity indices from a persisted surrogate bundle.

The surrogate (PCE, RF, MARS, or XGBoost) is built in the
``build_surrogate`` rule; this script loads it and emits three parquet
files: global Sobol indices, conditional indices per slice parameter,
and joint conditional indices across all slice parameters.

Only outputs listed in ``sensitivity_analysis.sobol.outputs`` are
decomposed — vector outputs typically live in the bundle for downstream
prediction but are excluded from Sobol to keep the parquet/plot fan-out
bounded.
"""

from pathlib import Path

import pandas as pd

from workflow.scenario_generators import build_joint_distribution
from workflow.scripts.analysis.sensitivity_common import (
    parse_outputs_spec,
    sobol_columns,
)
from workflow.scripts.analysis.surrogate import (
    load_bundle,
    sobol_rows_from_bundle,
)
from workflow.scripts.logging_config import setup_script_logging


def run(snakemake) -> None:
    logger = setup_script_logging(snakemake.log[0])

    n_threads = snakemake.threads
    from threadpoolctl import threadpool_limits

    threadpool_limits(limits=n_threads)
    logger.info("Thread limit set to %d", n_threads)

    bundle = load_bundle(Path(snakemake.input.surrogate))
    sobol_config = dict(snakemake.params.sobol_config)
    slice_grid = dict(snakemake.params.slice_grid)
    outputs_spec = parse_outputs_spec(dict(snakemake.params.outputs_spec))

    columns = sobol_columns(sobol_config, outputs_spec, bundle.output_columns)
    logger.info(
        "Loaded %s surrogate (%d outputs); computing Sobol for %d allowlisted columns",
        bundle.method,
        len(bundle.output_columns),
        len(columns),
    )

    distribution, _ = build_joint_distribution(bundle.generator_spec)

    global_rows, conditional_rows, conditional_joint_rows = sobol_rows_from_bundle(
        bundle,
        distribution,
        sobol_config,
        slice_grid,
        columns=columns,
    )

    global_path = Path(snakemake.output.global_indices)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(global_rows).to_parquet(global_path)
    logger.info("Wrote global indices to %s", global_path)

    conditional_path = Path(snakemake.output.conditional_indices)
    pd.DataFrame(conditional_rows).to_parquet(conditional_path)
    logger.info("Wrote conditional indices to %s", conditional_path)

    conditional_joint_path = Path(snakemake.output.conditional_joint_indices)
    pd.DataFrame(conditional_joint_rows).to_parquet(conditional_joint_path)
    logger.info("Wrote joint conditional indices to %s", conditional_joint_path)


if __name__ == "__main__":
    run(snakemake)
