# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for fiber demand infrastructure."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.biomass import add_fiber_demand_infrastructure


def _make_network_with_food_buses(countries, food_items):
    """Build a minimal network with food buses for testing."""
    n = pypsa.Network()
    n.set_snapshots(["now"])

    for country in countries:
        for item in food_items:
            n.buses.add(f"food:{item}:{country}", carrier="food")

    return n


class TestAddFiberDemandInfrastructure:
    """Tests for add_fiber_demand_infrastructure()."""

    def test_creates_buses_stores_links(self):
        """Verify fiber buses, stores, and links are created."""
        n = _make_network_with_food_buses(["USA", "CHN"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint", "cotton-lint"],
                "crop": ["cotton", "cotton"],
                "country": ["USA", "CHN"],
                "demand_mt": [1.5, 3.0],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA", "CHN"])

        # Check buses created
        assert "fiber:USA" in n.buses.static.index
        assert "fiber:CHN" in n.buses.static.index

        # Check links created
        assert "fiber:cotton-lint:USA" in n.links.static.index
        assert "fiber:cotton-lint:CHN" in n.links.static.index
        link_usa = n.links.static.loc["fiber:cotton-lint:USA"]
        assert link_usa["bus0"] == "food:cotton-lint:USA"
        assert link_usa["bus1"] == "fiber:USA"
        assert link_usa["carrier"] == "fiber_demand"

        # Check stores created
        assert "store:fiber:cotton-lint:USA" in n.stores.static.index
        assert "store:fiber:cotton-lint:CHN" in n.stores.static.index

    def test_store_demand_and_bounds(self):
        """Verify store e_nom_min matches demand and is extendable."""
        n = _make_network_with_food_buses(["USA"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint"],
                "crop": ["cotton"],
                "country": ["USA"],
                "demand_mt": [2.5],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA"])

        store = n.stores.static.loc["store:fiber:cotton-lint:USA"]
        assert store["e_nom_min"] == pytest.approx(2.5)
        assert store["e_min_pu"] == pytest.approx(1.0)
        assert store["e_max_pu"] == pytest.approx(1.0)  # PyPSA default
        assert store["e_nom_extendable"]

    def test_aggregates_duplicate_entries(self):
        """Multiple baseline rows for the same (source_item, country) are summed."""
        n = _make_network_with_food_buses(["USA"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint", "cotton-lint"],
                "crop": ["cotton", "cotton"],
                "country": ["USA", "USA"],
                "demand_mt": [1.0, 0.5],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA"])

        store = n.stores.static.loc["store:fiber:cotton-lint:USA"]
        assert store["e_nom_min"] == pytest.approx(1.5)

    def test_skips_missing_buses(self):
        """Entries with missing food buses are silently skipped."""
        n = _make_network_with_food_buses(["USA"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint", "cotton-lint"],
                "crop": ["cotton", "cotton"],
                "country": ["USA", "BRA"],  # BRA bus doesn't exist
                "demand_mt": [1.0, 2.0],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA", "BRA"])

        assert "fiber:USA" in n.buses.static.index
        assert "fiber:BRA" not in n.buses.static.index
        assert len(n.stores.static) == 1

    def test_skips_zero_demand(self):
        """Zero or negative demand rows are filtered out."""
        n = _make_network_with_food_buses(["USA"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint", "cotton-lint"],
                "crop": ["cotton", "cotton"],
                "country": ["USA", "USA"],
                "demand_mt": [1.0, -0.5],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA"])

        # The -0.5 row is filtered before aggregation; only 1.0 remains
        # (groupby sums to 0.5 which is > 0, so it should be included)
        # Actually: groupby sum = 1.0 + (-0.5) = 0.5 > 0 -> included
        store = n.stores.static.loc["store:fiber:cotton-lint:USA"]
        assert store["e_nom_min"] == pytest.approx(0.5)

    def test_empty_baseline_warns(self):
        """Empty baseline after filtering logs a warning."""
        n = _make_network_with_food_buses(["USA"], ["cotton-lint"])
        baseline = pd.DataFrame(
            {
                "source_item": ["cotton-lint"],
                "crop": ["cotton"],
                "country": ["BRA"],  # No bus for BRA
                "demand_mt": [1.0],
            }
        )

        add_fiber_demand_infrastructure(n, baseline, ["USA", "BRA"])

        # No stores or links should be created
        assert (
            n.stores.static.empty
            or "fiber_demand" not in n.stores.static["carrier"].values
        )
        fiber_links = n.links.static[n.links.static["carrier"] == "fiber_demand"]
        assert fiber_links.empty
