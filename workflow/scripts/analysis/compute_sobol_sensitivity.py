# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute Sobol sensitivity indices from a persisted surrogate bundle.

The surrogate (PCE, RF, MARS, or XGBoost) is built in the ``build_surrogate``
rule; this script loads it and emits three parquet files: global Sobol
indices, conditional indices per slice parameter, and joint conditional
indices across all slice parameters.
"""

from pathlib import Path

import pandas as pd

from workflow.scenario_generators import build_joint_distribution
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
    logger.info(
        "Loaded %s surrogate for outputs %s",
        bundle.method,
        bundle.output_columns,
    )

    method_config = dict(snakemake.params.method_config)
    slice_grid = dict(snakemake.params.slice_grid)
    method_options = dict(method_config.get("method_options", {}))

    distribution, _ = build_joint_distribution(bundle.generator_spec)

    global_rows, conditional_rows, conditional_joint_rows = sobol_rows_from_bundle(
        bundle,
        distribution,
        method_options,
        slice_grid,
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
