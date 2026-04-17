# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the objective breakdown extraction module."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.analysis.extract_objective_breakdown import (
    extract_objective_breakdown,
)


def _make_solved_network() -> pypsa.Network:
    """Build a minimal solved PyPSA network covering all cost categories.

    Returns a network with hand-set dispatch, marginal costs, and
    ``n._objective`` matching the sum of all cost components.
    """
    n = pypsa.Network()
    n.set_snapshots(["now"])

    # -- Buses (minimal; just need to exist for component wiring) --
    bus_names = [
        "land:cropland:r1_c1_r",
        "crop:wheat:USA",
        "land:existing:r1_c1_r",
        "land:used:r1_c1_r",
        "trade:hub0",
        "feed:ruminant_grain:USA",
        "crop:wheat:milling_in",
        "food:bread:USA",
        "group:cereals:USA",
        "feed:forage:USA",
        "food:beef:USA",
        "biomass:wheat:USA",
        "biomass:bus:USA",
        "emission:co2",
        "emission:ghg",
        "fertilizer:USA",
        "fertilizer:bus",
        "nutrient:protein:USA",
        "water:r1",
        "spared:r1_c1_r",
        "land:new:r1_c1_r",
        "health:cluster001",
        "land:slack:r1_c1_r",
        "land:supply:r1_c1_r",
        "sink:biomass:USA",
    ]
    n.buses.add(bus_names)

    # -- Links (carrier-based categorization) --
    link_data = {
        # (name, bus0, bus1, carrier, marginal_cost, dispatch)
        "produce:wheat_r:r1_c1": (
            "land:cropland:r1_c1_r",
            "crop:wheat:USA",
            "crop_production",
            10.0,
            1.0,
        ),
        "use:existing_land:r1_c1_r": (
            "land:existing:r1_c1_r",
            "land:used:r1_c1_r",
            "land_use",
            0.0,
            1.0,
        ),
        "trade:wheat:USA_to_hub0": (
            "crop:wheat:USA",
            "trade:hub0",
            "trade_crop",
            2.0,
            1.0,
        ),
        "convert:wheat_to_ruminant_grain:USA": (
            "crop:wheat:USA",
            "feed:ruminant_grain:USA",
            "feed_conversion",
            0.5,
            1.0,
        ),
        "pathway:milling:USA": (
            "crop:wheat:milling_in",
            "food:bread:USA",
            "food_processing",
            1.0,
            1.0,
        ),
        "consume:bread:USA": (
            "food:bread:USA",
            "group:cereals:USA",
            "food_consumption",
            3.0,
            1.0,
        ),
        "animal:beef_grassfed:USA": (
            "feed:forage:USA",
            "food:beef:USA",
            "animal_production",
            5.0,
            1.0,
        ),
        "biomass:crop_wheat:USA": (
            "crop:wheat:USA",
            "biomass:bus:USA",
            "biomass_crop",
            0.0,
            1.0,
        ),
        "aggregate:co2_to_ghg": (
            "emission:co2",
            "emission:ghg",
            "emission_aggregation",
            0.0,
            1.0,
        ),
        "distribute:fertilizer:USA": (
            "fertilizer:USA",
            "fertilizer:bus",
            "fertilizer_distribution",
            0.0,
            1.0,
        ),
    }

    link_names = list(link_data.keys())
    n.links.add(
        link_names,
        bus0=[link_data[k][0] for k in link_names],
        bus1=[link_data[k][1] for k in link_names],
        carrier=[link_data[k][2] for k in link_names],
        marginal_cost=[link_data[k][3] for k in link_names],
        p_nom_opt=1.0,
        capital_cost=0.0,
    )
    link_dispatch = {k: [link_data[k][4]] for k in link_names}
    n.c["Link"].dynamic["p0"] = pd.DataFrame(link_dispatch, index=n.snapshots)

    # -- Generators (name-pattern categorization) --
    gen_data = {
        # (name, bus, carrier, marginal_cost, dispatch)
        "sink:biomass:USA": ("sink:biomass:USA", "biomass_export", -0.5, 2.0),
        "supply:fertilizer": ("fertilizer:bus", "fertilizer", 4.0, 1.0),
        "supply:land_existing_cropland:r1_c1_r": (
            "land:supply:r1_c1_r",
            "land_supply",
            0.1,
            1.0,
        ),
        "slack:land_slack:r1_c1_r": (
            "land:slack:r1_c1_r",
            "land_slack",
            100.0,
            0.01,
        ),
    }
    gen_names = list(gen_data.keys())
    n.generators.add(
        gen_names,
        bus=[gen_data[k][0] for k in gen_names],
        carrier=[gen_data[k][1] for k in gen_names],
        marginal_cost=[gen_data[k][2] for k in gen_names],
        p_nom_opt=1.0,
        capital_cost=0.0,
    )
    gen_dispatch = {k: [gen_data[k][3]] for k in gen_names}
    n.c["Generator"].dynamic["p"] = pd.DataFrame(gen_dispatch, index=n.snapshots)

    # -- Stores --
    # Stores with marginal_cost_storage (cost proportional to level e)
    store_mcs_data = {
        # (name, bus, carrier, marginal_cost_storage, e_level)
        "store:emission:ghg": ("emission:ghg", "ghg", 20.0, 1.0),
        "store:yll:heart:cluster001": ("health:cluster001", "yll_heart", 5.0, 1.0),
        "store:group:cereals:USA": ("group:cereals:USA", "group_cereals", -2.0, 1.0),
    }
    # Stores with zero cost
    store_zero_data = {
        "store:nutrient:protein:USA": ("nutrient:protein:USA", "protein"),
        "store:water:r1": ("water:r1", "water"),
        "store:fertilizer:USA": ("fertilizer:USA", "fertilizer"),
        "store:spared:r1_c1_r": ("spared:r1_c1_r", "spared_land"),
    }

    all_store_names = list(store_mcs_data.keys()) + list(store_zero_data.keys())
    all_store_buses = [store_mcs_data[k][0] for k in store_mcs_data] + [
        store_zero_data[k][0] for k in store_zero_data
    ]
    all_store_carriers = [store_mcs_data[k][1] for k in store_mcs_data] + [
        store_zero_data[k][1] for k in store_zero_data
    ]
    all_store_mcs = [store_mcs_data[k][2] for k in store_mcs_data] + [0.0] * len(
        store_zero_data
    )

    n.stores.add(
        all_store_names,
        bus=all_store_buses,
        carrier=all_store_carriers,
        marginal_cost_storage=all_store_mcs,
        e_nom_opt=0.0,
        capital_cost=0.0,
    )

    # Dynamic state: e for marginal_cost_storage, p for marginal_cost
    store_e = {
        k: [store_mcs_data[k][3]] if k in store_mcs_data else [0.0]
        for k in all_store_names
    }
    n.c["Store"].dynamic["e"] = pd.DataFrame(store_e, index=n.snapshots)
    # Also set p to zero (needed for marginal_cost path)
    store_p = {k: [0.0] for k in all_store_names}
    n.c["Store"].dynamic["p"] = pd.DataFrame(store_p, index=n.snapshots)

    # -- Compute expected total and set objective --
    # Links: marginal_cost * dispatch
    link_cost = sum(link_data[k][3] * link_data[k][4] for k in link_names)
    # Generators: marginal_cost * dispatch
    gen_cost = sum(gen_data[k][2] * gen_data[k][3] for k in gen_names)
    # Stores: marginal_cost_storage * e_level
    store_cost = sum(
        store_mcs_data[k][2] * store_mcs_data[k][3] for k in store_mcs_data
    )

    n._objective = link_cost + gen_cost + store_cost
    n._meta = {}

    return n


@pytest.fixture
def solved_network() -> pypsa.Network:
    """Provide a minimal solved network for objective breakdown tests."""
    return _make_solved_network()


# Expected costs per category from the fixture
EXPECTED_COSTS = {
    "crop_production": 10.0,  # produce:wheat link
    "trade": 2.0,  # trade:wheat link
    "feed_conversion": 0.5,  # convert:wheat_to... link
    "processing": 1.0,  # pathway:milling link
    "consumption": 3.0,  # consume:bread link
    "animal_production": 5.0,  # animal:beef link
    "fertilizer": 4.0,  # supply:fertilizer gen
    "biomass_exports": -1.0,  # sink:biomass gen (-0.5 * 2.0)
    "resource_supply": 0.1,  # supply:land gen
    "slack_penalties": 1.0,  # slack:land gen (100 * 0.01)
    "ghg_cost": 20.0,  # store:emission:ghg
    "health_burden": 5.0,  # store:yll:heart
    "consumer_values": -2.0,  # store:group:cereals
}


class TestBasicExtraction:
    def test_returns_dataframe(self, solved_network):
        """The result should be a single-row DataFrame."""
        result = extract_objective_breakdown(solved_network)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    def test_expected_columns(self, solved_network):
        """All expected category columns should be present."""
        result = extract_objective_breakdown(solved_network)
        for col in EXPECTED_COSTS:
            assert col in result.columns, f"Missing column: {col}"

    def test_category_values(self, solved_network):
        """Each category should have the expected cost value."""
        result = extract_objective_breakdown(solved_network)
        for col, expected_val in EXPECTED_COSTS.items():
            actual = result[col].iloc[0]
            assert actual == pytest.approx(
                expected_val, abs=1e-6
            ), f"{col}: expected {expected_val}, got {actual}"


class TestAllCategoriesPresent:
    def test_non_zero_categories_present(self, solved_network):
        """All categories with non-zero costs should appear as columns."""
        result = extract_objective_breakdown(solved_network)
        expected_cols = {k for k, v in EXPECTED_COSTS.items() if abs(v) > 1e-9}
        actual_cols = set(result.columns)
        missing = expected_cols - actual_cols
        assert not missing, f"Missing non-zero categories: {missing}"


class TestObjectiveValidation:
    def test_passes_when_costs_match(self, solved_network):
        """No error when extracted costs match the objective."""
        # Should not raise
        extract_objective_breakdown(solved_network)

    def test_mismatch_raises(self, solved_network):
        """ValueError when objective doesn't match extracted costs."""
        solved_network._objective *= 2.0  # Double the objective
        with pytest.raises(ValueError, match="tolerance"):
            extract_objective_breakdown(solved_network)


class TestUnrecognizedComponents:
    def test_unrecognized_link_carrier_raises(self, solved_network):
        """ValueError for a link with an unknown carrier."""
        n = solved_network
        n.links.add(
            "mystery:link",
            bus0="crop:wheat:USA",
            bus1="food:bread:USA",
            carrier="unknown_carrier",
            marginal_cost=1.0,
        )
        # Extend dynamic data to include the new link
        n.c["Link"].dynamic["p0"]["mystery:link"] = [1.0]
        with pytest.raises(ValueError, match="Unrecognized Link carrier"):
            extract_objective_breakdown(n)

    def test_unrecognized_store_carrier_raises(self, solved_network):
        """ValueError for a store with an unknown carrier."""
        n = solved_network
        n.buses.add("mystery:bus")
        n.stores.add(
            "store:mystery:thing",
            bus="mystery:bus",
            carrier="unknown_store_carrier",
        )
        n.c["Store"].dynamic["e"]["store:mystery:thing"] = [0.0]
        n.c["Store"].dynamic["p"]["store:mystery:thing"] = [0.0]
        with pytest.raises(ValueError, match="Unrecognized Store carrier"):
            extract_objective_breakdown(n)

    def test_unrecognized_generator_raises(self, solved_network):
        """ValueError for a generator with no matching name pattern."""
        n = solved_network
        n.buses.add("mystery:gen_bus")
        n.generators.add(
            "mystery:generator",
            bus="mystery:gen_bus",
            carrier="unknown_gen_carrier",
            marginal_cost=1.0,
        )
        n.c["Generator"].dynamic["p"]["mystery:generator"] = [1.0]
        with pytest.raises(ValueError, match="Unrecognized Generator pattern"):
            extract_objective_breakdown(n)


class TestLinopyMetaCosts:
    def test_production_stability_cost(self, solved_network):
        """production_stability_cost should appear as its own category."""
        n = solved_network
        stability_cost = 3.0
        n._meta["production_stability_cost"] = stability_cost
        n._objective += stability_cost
        result = extract_objective_breakdown(n)
        assert "production_stability" in result.columns
        assert result["production_stability"].iloc[0] == pytest.approx(
            stability_cost, abs=1e-6
        )

    def test_food_utility_cost(self, solved_network):
        """food_utility_cost should add to Consumer values."""
        n = solved_network
        utility_cost = -1.5
        n._meta["food_utility_cost"] = utility_cost
        n._objective += utility_cost
        result = extract_objective_breakdown(n)
        expected = EXPECTED_COSTS["consumer_values"] + utility_cost
        assert result["consumer_values"].iloc[0] == pytest.approx(expected, abs=1e-6)


class TestLandConversion:
    def test_land_conversion_classified_as_land_use(self):
        """A land_conversion link should be categorized as 'Land use'."""
        n = _make_solved_network()
        # Add a land_conversion link with non-zero cost
        n.links.add(
            "convert:new_land_forest:r1_c1_r",
            bus0="land:new:r1_c1_r",
            bus1="land:cropland:r1_c1_r",
            carrier="land_conversion",
            marginal_cost=7.0,
            p_nom_opt=1.0,
            capital_cost=0.0,
        )
        n.c["Link"].dynamic["p0"]["convert:new_land_forest:r1_c1_r"] = [1.0]
        n._objective += 7.0  # Adjust objective for the new cost
        result = extract_objective_breakdown(n)
        assert "land_use" in result.columns
        assert result["land_use"].iloc[0] == pytest.approx(7.0, abs=1e-6)


class TestZeroCostFiltering:
    def test_zero_cost_categories_absent(self, solved_network):
        """Categories with zero total cost should not appear as columns."""
        result = extract_objective_breakdown(solved_network)
        # These links/stores have zero cost in the fixture
        zero_categories = [
            "land_use",
            "biomass_routing",
            "emissions_aggregation",
            "nutrient_tracking",
            "water",
        ]
        for col in zero_categories:
            assert col not in result.columns, f"Zero-cost category present: {col}"
