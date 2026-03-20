# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for notebook sensitivity utilities."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "notebooks" / "sensitivity_utils.py"
SPEC = importlib.util.spec_from_file_location(
    "notebooks_sensitivity_utils", MODULE_PATH
)
sensitivity_utils = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sensitivity_utils)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a minimal results tree for notebook loader tests."""
    (tmp_path / "results" / "demo" / "analysis" / "scen-s1").mkdir(parents=True)
    (tmp_path / "results" / "demo" / "analysis" / "scen-s2").mkdir(parents=True)
    return tmp_path


def test_load_net_emissions_by_gas_reads_analysis_schema(project_root: Path) -> None:
    """By-gas loader should read the exact analysis schema."""
    pq_path = (
        project_root
        / "results"
        / "demo"
        / "analysis"
        / "scen-s1"
        / "net_emissions.parquet"
    )
    pd.DataFrame(
        {
            "gas": ["co2", "ch4", "n2o"],
            "source": [
                "Land Use Change",
                "Enteric fermentation",
                "Synthetic fertilizer application",
            ],
            "mtco2eq": [1000.0, 500.0, 250.0],
        }
    ).to_parquet(pq_path)

    scenarios = [(10.0, "s1", Path("unused.nc"))]
    result = sensitivity_utils.load_net_emissions_by_gas(
        scenarios, project_root, "demo", "ghg_price"
    )

    assert result.index.name == "ghg_price"
    assert result.loc[10.0, sensitivity_utils.GAS_DISPLAY["co2"]] == pytest.approx(1.0)
    assert result.loc[10.0, sensitivity_utils.GAS_DISPLAY["ch4"]] == pytest.approx(0.5)
    assert result.loc[10.0, sensitivity_utils.GAS_DISPLAY["n2o"]] == pytest.approx(0.25)


def test_load_emissions_by_source_rejects_wrong_schema(project_root: Path) -> None:
    """Per-source loader should fail fast on non-analysis schemas."""
    pq_path = (
        project_root
        / "results"
        / "demo"
        / "analysis"
        / "scen-s2"
        / "net_emissions.parquet"
    )
    pd.DataFrame(
        {
            "gas": ["co2", "co2", "ch4"],
            "source": [
                "Land Use Change",
                "Carbon sequestration",
                "Enteric fermentation",
            ],
            "net_mtco2eq": [2000.0, -500.0, 750.0],
        }
    ).to_parquet(pq_path)

    scenarios = [(20.0, "s2", Path("unused.nc"))]
    with pytest.raises(ValueError, match="Expected columns"):
        sensitivity_utils.load_emissions_by_source(
            scenarios, project_root, "demo", "ghg_price"
        )


def test_filter_scenarios_by_suffix_keeps_default_only() -> None:
    """Default suffix should exclude production-stability variants."""
    scenarios = [
        (5.0, "ghg_5", Path("a.nc")),
        (5.0, "ghg_5_l1_0p1", Path("b.nc")),
        (5.0, "ghg_5_l1_10", Path("c.nc")),
    ]

    result = sensitivity_utils.filter_scenarios_by_suffix(scenarios)

    assert result == [(5.0, "ghg_5", Path("a.nc"))]


def test_filter_combined_scenarios_by_suffix_keeps_default_only() -> None:
    """Default suffix should exclude combined production-stability variants."""
    scenarios = [
        (5.0, 50.0, "ghg_yll_5", Path("a.nc")),
        (5.0, 50.0, "ghg_yll_5_l1_0p1", Path("b.nc")),
        (5.0, 50.0, "ghg_yll_5_l1_10", Path("c.nc")),
    ]

    result = sensitivity_utils.filter_combined_scenarios_by_suffix(scenarios)

    assert result == [(5.0, 50.0, "ghg_yll_5", Path("a.nc"))]
