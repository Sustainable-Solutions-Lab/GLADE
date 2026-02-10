# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Integration tests for the Snakemake workflow.

These tests exercise the full pipeline using the lightweight test
configuration (tests/config/test.yaml) with reduced spatial resolution and
a small crop subset.
"""

from conftest import run_snakemake_target
import pytest


@pytest.mark.integration
def test_workflow_dryrun():
    """Dryrun the full DAG (all scenarios) to validate rule logic.

    This catches missing inputs, broken rule definitions, and invalid
    wildcard patterns without executing anything.  Both 'default' and
    'G' scenarios are exercised via the analyze_all_scenarios target.
    Uses forceall to validate the complete DAG as if running from scratch.
    """
    run_snakemake_target("analyze_all_scenarios", dryrun=True, forceall=True)


@pytest.mark.integration
def test_build_solve_analyze(results_dir):
    """Build, solve, and analyse the default scenario end-to-end.

    Snakemake skips up-to-date outputs automatically, so reruns are
    near-instant when code hasn't changed.
    """
    run_snakemake_target(
        "results/test/analysis/scen-default/crop_production.csv",
        "results/test/analysis/scen-default/ghg_intensity.csv",
        "results/test/analysis/scen-default/ghg_totals.csv",
        "results/test/analysis/scen-default/objective_breakdown.csv",
    )

    assert (results_dir / "solved" / "model_scen-default.nc").exists()
    assert (results_dir / "analysis" / "scen-default" / "crop_production.csv").exists()
    assert (results_dir / "analysis" / "scen-default" / "ghg_intensity.csv").exists()
    assert (results_dir / "analysis" / "scen-default" / "ghg_totals.csv").exists()
    assert (
        results_dir / "analysis" / "scen-default" / "objective_breakdown.csv"
    ).exists()


@pytest.mark.plots
def test_plots(results_dir):
    """Generate a couple of representative plots.

    Depends on the solved model from test_build_solve_analyze, but
    Snakemake handles the dependency automatically.
    """
    run_snakemake_target(
        "results/test/plots/scen-default/consumption_balance.pdf",
        "results/test/plots/scen-default/objective_breakdown.pdf",
    )

    assert (results_dir / "plots" / "scen-default" / "consumption_balance.pdf").exists()
    assert (results_dir / "plots" / "scen-default" / "objective_breakdown.pdf").exists()
