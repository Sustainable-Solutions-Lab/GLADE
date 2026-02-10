# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared test helpers using the Snakemake Python API."""

from pathlib import Path

import pytest
from snakemake.api import SnakemakeApi
from snakemake.settings.types import (
    ConfigSettings,
    DAGSettings,
    OutputSettings,
    ResourceSettings,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAKEFILE = PROJECT_ROOT / "workflow" / "Snakefile"
CONFIG_DEFAULT = PROJECT_ROOT / "config" / "default.yaml"
CONFIG_TEST = PROJECT_ROOT / "tests" / "config" / "test.yaml"
RESULTS_DIR = PROJECT_ROOT / "results" / "test"


def run_snakemake_target(
    *targets: str,
    cores: int = 4,
    dryrun: bool = False,
    forceall: bool = False,
) -> None:
    """Run Snakemake targeting specific output files.

    Uses the Snakemake Python API with the test configuration layered
    on top of the default config.

    Raises on workflow failure (execute_workflow raises internally).
    """
    executor = "dryrun" if dryrun else "local"
    with SnakemakeApi(OutputSettings(dryrun=dryrun, show_failed_logs=True)) as api:
        wf = api.workflow(
            resource_settings=ResourceSettings(cores=cores),
            config_settings=ConfigSettings(
                configfiles=[CONFIG_DEFAULT, CONFIG_TEST],
            ),
            snakefile=SNAKEFILE,
            workdir=PROJECT_ROOT,
        )
        dag = wf.dag(
            dag_settings=DAGSettings(
                targets=frozenset(targets),
                forceall=forceall,
            )
        )
        dag.execute_workflow(executor=executor)


@pytest.fixture(scope="session")
def results_dir() -> Path:
    """Return the path to the test results directory."""
    return RESULTS_DIR
