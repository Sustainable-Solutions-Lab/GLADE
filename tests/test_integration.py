# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
        "results/test/analysis/scen-default/crop_production.parquet",
        "results/test/analysis/scen-default/ghg_attribution.parquet",
        "results/test/analysis/scen-default/ghg_attribution_totals.parquet",
        "results/test/analysis/scen-default/net_emissions.parquet",
        "results/test/analysis/scen-default/objective_breakdown.parquet",
    )

    assert (results_dir / "solved" / "model_scen-default.nc").exists()
    assert (
        results_dir / "analysis" / "scen-default" / "crop_production.parquet"
    ).exists()
    assert (
        results_dir / "analysis" / "scen-default" / "ghg_attribution.parquet"
    ).exists()
    assert (
        results_dir / "analysis" / "scen-default" / "ghg_attribution_totals.parquet"
    ).exists()
    assert (
        results_dir / "analysis" / "scen-default" / "net_emissions.parquet"
    ).exists()
    assert (
        results_dir / "analysis" / "scen-default" / "objective_breakdown.parquet"
    ).exists()

    # Enabled catalog combinations restricted to the test config's crops must
    # yield anchored multi links.
    import pypsa

    n = pypsa.Network(str(results_dir / "solved" / "model_scen-default.nc"))
    links = n.links.static
    multi = links[links["carrier"] == "crop_production_multi"]
    assert not multi.empty, "expected crop_production_multi links"
    assert (multi["baseline_area_mha"] > 0).any(), "multi links lack a baseline anchor"
    # Irrigated multi links must carry per-period water ports (no free
    # irrigation), and at the test config's temporal_resolution the demand must
    # actually be seasonal rather than smeared evenly across the year.
    bus_cols = [c for c in multi.columns if c.startswith("bus") and c[3:].isdigit()]
    irrigated = multi[multi["water_supply"] == "irrigated"]
    assert not irrigated.empty, "expected irrigated multi-cropping links"
    has_water = irrigated[bus_cols].apply(
        lambda row: any(
            isinstance(v, str) and v.startswith("water_field:") for v in row
        ),
        axis=1,
    )
    assert has_water.all(), "irrigated multi links without a water_field port"

    found_nonuniform = False
    for _, row in irrigated.iterrows():
        effs = [
            abs(float(row[f"efficiency{c[3:]}"]))
            for c in bus_cols
            if isinstance(row[c], str)
            and row[c].startswith("water_field:")
            and f"efficiency{c[3:]}" in row
        ]
        if len(effs) >= 2 and max(effs) > 1.2 * (sum(effs) / len(effs)):
            found_nonuniform = True
            break
    assert found_nonuniform, "no irrigated multi link with a seasonal water split"


@pytest.mark.plots
def test_plots(results_dir):
    """Generate a couple of representative plots.

    Depends on the solved model from test_build_solve_analyze, but
    Snakemake handles the dependency automatically.
    """
    run_snakemake_target(
        "results/test/plots/scen-default/food_consumption.pdf",
        "results/test/plots/scen-default/objective_breakdown.pdf",
    )

    assert (results_dir / "plots" / "scen-default" / "food_consumption.pdf").exists()
    assert (results_dir / "plots" / "scen-default" / "objective_breakdown.pdf").exists()
