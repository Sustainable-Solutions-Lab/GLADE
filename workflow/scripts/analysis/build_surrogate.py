# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fit a surrogate model over the GSA Sobol design.

Consumes the per-scenario outputs declared in
``sensitivity_analysis.outputs`` (scalar and vector) and writes a
:class:`SurrogateBundle` pickle plus a flat validation parquet.  The
surrogate method is selected via the ``{method}`` wildcard.  Downstream
rules (Sobol computation, uncertainty plots) load the pickle and do not
refit.
"""

from pathlib import Path

import numpy as np

from workflow.scripts.analysis.sensitivity_common import (
    expanded_output_columns,
    load_scenario_outputs,
    parse_outputs_spec,
    reconstruct_samples,
    vector_output_columns,
)
from workflow.scripts.analysis.surrogate import (
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
    outputs_spec = parse_outputs_spec(dict(snakemake.params.outputs_spec))

    # Derive the scenario-analysis directory from a scenario input:
    # inputs look like <results>/{name}/analysis/scen-<scenario>/<file>.parquet
    analysis_dir = Path(snakemake.input[0]).parents[1]
    logger.info("Using analysis directory: %s", analysis_dir)

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

    outputs_df = load_scenario_outputs(
        analysis_dir, scenario_names, outputs_spec, n_workers=n_threads
    )
    logger.info("Loaded outputs for %d scenarios", len(outputs_df))

    output_columns = expanded_output_columns(outputs_spec, outputs_df)
    vector_columns = vector_output_columns(outputs_spec, outputs_df)
    n_scalar = len(output_columns) - len(vector_columns)
    logger.info(
        "Output columns: %d scalar, %d vector elements", n_scalar, len(vector_columns)
    )

    # Drop scenarios where any scalar output failed.  Vector outputs use
    # zero-fill semantics (an absent food == zero global mass), so a NaN
    # there genuinely signals a broken solve and we treat it identically.
    failed_mask = outputs_df[output_columns].isna().any(axis=1)
    n_failed = int(failed_mask.sum())
    if n_failed > 0:
        failed_scenarios = outputs_df.loc[failed_mask, "scenario"].tolist()
        logger.warning(
            "Dropping %d failed scenarios (NaN outputs): %s",
            n_failed,
            failed_scenarios[:10] + (["..."] if n_failed > 10 else []),
        )
        outputs_df = outputs_df[~failed_mask].reset_index(drop=True)
        x_design = x_design[~failed_mask.values]

    if outputs_df.empty:
        raise ValueError("No scenarios survived NaN filtering; nothing to fit")

    bundle = fit_bundle(
        method=method,
        x_design=x_design,
        outputs_df=outputs_df,
        available_columns=output_columns,
        generator_spec=generator_spec,
        method_config=method_config,
        holdout_fraction=holdout_fraction,
        n_threads=n_threads,
        vector_columns=vector_columns,
    )

    save_bundle(bundle, Path(snakemake.output.surrogate))

    validation_path = Path(snakemake.output.validation)
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_dataframe(bundle).to_parquet(validation_path)
    logger.info("Wrote validation metrics to %s", validation_path)


if __name__ == "__main__":
    run(snakemake)
