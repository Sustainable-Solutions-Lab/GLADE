# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Solve the model and run analysis in a single process.

Equivalent to running solve_model.py followed by analyze_model.py, but
skips the intermediate .nc write/read cycle.  The in-memory solved
network is passed directly to the analysis extraction functions.

Enabled via ``solving.inline_analysis: true`` in config.
"""

import logging

from workflow.scripts.analysis.analyze_model import run_analysis, write_empty_outputs
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.solve_model.core import _ShadowPriceLogFilter, run_solve


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())

    # Phase 1: Solve
    n = run_solve(snakemake, logger)

    if n is None:
        logger.warning("Solve failed — writing empty analysis outputs.")
        write_empty_outputs(snakemake.output)
        return

    # Phase 2: Analyze (using in-memory solved network)
    output_paths = {
        attr: getattr(snakemake.output, attr)
        for attr in dir(snakemake.output)
        if not attr.startswith("_")
        and isinstance(getattr(snakemake.output, attr), str)
        and getattr(snakemake.output, attr).endswith(".parquet")
    }

    run_analysis(
        n,
        output_paths=output_paths,
        food_groups_path=snakemake.input.food_groups,
        m49_codes_path=snakemake.input.m49,
        risk_breakpoints_path=snakemake.input.health_risk_breakpoints,
        health_cluster_cause_path=snakemake.input.health_cluster_cause,
        health_cause_log_path=snakemake.input.health_cause_log,
        health_clusters_path=snakemake.input.health_clusters,
        population_path=snakemake.input.population,
        tmrel_path=snakemake.input.health_tmrel,
        ghg_price=float(snakemake.params.ghg_price),
        ch4_gwp=float(snakemake.params.ch4_gwp),
        n2o_gwp=float(snakemake.params.n2o_gwp),
        value_per_yll=float(snakemake.params.health_value_per_yll),
        health_risk_factors=list(snakemake.params.health_risk_factors),
        logger=logger,
    )

    logger.info("Solve-and-analyze complete.")


if __name__ == "__main__":
    main()
