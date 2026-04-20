# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fit a surrogate model over the GSA Sobol design.

Consumes the scalar scenario outputs produced by the analysis rules and
writes a :class:`SurrogateBundle` pickle plus a flat validation parquet.
The surrogate method is selected via the ``{method}`` wildcard.  Downstream
rules (Sobol computation, uncertainty plots) load the pickle and do not
refit.
"""

from pathlib import Path

import numpy as np

from workflow.scripts.analysis.sensitivity_common import (
    load_scenario_outputs,
    reconstruct_samples,
)
from workflow.scripts.analysis.surrogate import (
    OUTPUT_COLUMNS,
    fit_bundle,
    save_bundle,
    validation_dataframe,
)
from workflow.scripts.logging_config import setup_script_logging


def run(snakemake) -> None:
    logger = setup_script_logging(snakemake.log[0])

    n_threads = snakemake.threads
    from threadpoolctl import threadpool_limits

    threadpool_limits(limits=n_threads)
    logger.info("Thread limit set to %d", n_threads)

    method = snakemake.wildcards.method
    scenario_names = list(snakemake.params.scenario_names)
    generator_spec = dict(snakemake.params.generator_spec)
    method_config = dict(snakemake.params.method_config)
    holdout_fraction = float(snakemake.params.holdout_fraction)

    # Derive the scenario-analysis directory from a scenario input:
    # inputs look like <results>/{name}/analysis/scen-<scenario>/<file>.parquet
    analysis_dir = Path(snakemake.input[0]).parents[1]
    logger.info("Using analysis directory: %s", analysis_dir)

    # Reconstruct the Sobol design, select the rows for scenarios we have.
    x_design_full = reconstruct_samples(generator_spec)
    prefix = generator_spec["name"].removesuffix("{sample_id}")
    sample_indices = np.array([int(s.removeprefix(prefix)) for s in scenario_names])
    x_design = x_design_full[sample_indices]
    logger.info(
        "Using %d/%d scenarios (%.0f%% available)",
        len(scenario_names),
        x_design_full.shape[0],
        100 * len(scenario_names) / x_design_full.shape[0],
    )

    outputs_df = load_scenario_outputs(analysis_dir, scenario_names)
    logger.info("Loaded outputs for %d scenarios", len(outputs_df))

    # Drop scenarios with failed solves (any NaN across the output columns).
    output_columns = list(OUTPUT_COLUMNS)
    existing_cols = [c for c in output_columns if c in outputs_df.columns]
    failed_mask = outputs_df[existing_cols].isna().any(axis=1)
    n_failed = int(failed_mask.sum())
    if n_failed > 0:
        failed_scenarios = outputs_df.loc[failed_mask, "scenario"].tolist()
        logger.warning(
            "Dropping %d failed scenarios (empty outputs): %s",
            n_failed,
            failed_scenarios,
        )
        outputs_df = outputs_df[~failed_mask].reset_index(drop=True)
        x_design = x_design[~failed_mask.values]

    available_columns = [
        c
        for c in output_columns
        if c in outputs_df.columns and not outputs_df[c].isna().any()
    ]
    if not available_columns:
        raise ValueError("No valid output columns found for sensitivity analysis")
    logger.info("Analyzing outputs: %s", available_columns)

    bundle = fit_bundle(
        method=method,
        x_design=x_design,
        outputs_df=outputs_df,
        available_columns=available_columns,
        generator_spec=generator_spec,
        method_config=method_config,
        holdout_fraction=holdout_fraction,
        n_threads=n_threads,
    )

    surrogate_path = Path(snakemake.output.surrogate)
    save_bundle(bundle, surrogate_path)
    logger.info("Wrote surrogate bundle to %s", surrogate_path)

    validation_path = Path(snakemake.output.validation)
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_dataframe(bundle).to_parquet(validation_path)
    logger.info("Wrote validation metrics to %s", validation_path)


if __name__ == "__main__":
    run(snakemake)
