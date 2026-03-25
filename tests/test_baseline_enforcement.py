# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for food-level baseline enforcement and consumer values."""

import pandas as pd
import pypsa
import pytest

from workflow.scripts.extract_consumer_values import extract_consumer_values
from workflow.scripts.solve_model.core import (
    _build_ratios_from_baseline,
    _match_baseline_to_consume_links,
    add_food_incentives_to_objective,
    add_food_slack_generators,
    fix_food_consumption_to_baseline,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_df():
    """Minimal baseline diet DataFrame."""
    return pd.DataFrame(
        {
            "food": [
                "wheat",
                "rice",
                "maize",
                "beef",
                "poultry",
                "lentils",
            ],
            "country": ["USA", "USA", "USA", "USA", "USA", "USA"],
            "food_group": [
                "grain",
                "grain",
                "grain",
                "red_meat",
                "poultry_meat",
                "legumes",
            ],
            "consumption_g_per_day": [150.0, 50.0, 100.0, 75.0, 60.0, 20.0],
        }
    )


@pytest.fixture
def baseline_df_multi_country():
    """Baseline diet with two countries."""
    return pd.DataFrame(
        {
            "food": ["wheat", "rice", "wheat", "rice"],
            "country": ["USA", "USA", "IND", "IND"],
            "food_group": ["grain", "grain", "grain", "grain"],
            "consumption_g_per_day": [150.0, 50.0, 30.0, 170.0],
        }
    )


@pytest.fixture
def food_network():
    """Minimal PyPSA network with food consumption links."""
    n = pypsa.Network()

    n.carriers.add("food_consumption", unit="Mt")

    # Buses for foods and food groups
    n.buses.add(
        [
            "food:wheat:USA",
            "food:rice:USA",
            "food:beef:USA",
            "food:wheat:IND",
            "food:rice:IND",
            "group:grain:USA",
            "group:red_meat:USA",
            "group:grain:IND",
        ],
        carrier=[
            "food_wheat",
            "food_rice",
            "food_beef",
            "food_wheat",
            "food_rice",
            "group_grain",
            "group_red_meat",
            "group_grain",
        ],
    )

    # Food consumption links
    n.links.add(
        [
            "consume:wheat:USA",
            "consume:rice:USA",
            "consume:beef:USA",
            "consume:wheat:IND",
            "consume:rice:IND",
        ],
        bus0=[
            "food:wheat:USA",
            "food:rice:USA",
            "food:beef:USA",
            "food:wheat:IND",
            "food:rice:IND",
        ],
        bus1=[
            "group:grain:USA",
            "group:grain:USA",
            "group:red_meat:USA",
            "group:grain:IND",
            "group:grain:IND",
        ],
        carrier="food_consumption",
        marginal_cost=[0.01, 0.01, 0.01, 0.01, 0.01],
        food=["wheat", "rice", "beef", "wheat", "rice"],
        country=["USA", "USA", "USA", "IND", "IND"],
        food_group=["grain", "grain", "red_meat", "grain", "grain"],
    )

    return n


# ---------------------------------------------------------------------------
# Test _build_ratios_from_baseline
# ---------------------------------------------------------------------------


class TestBuildRatiosFromBaseline:
    def test_ratios_sum_to_one_per_group(self, baseline_df):
        result = _build_ratios_from_baseline(baseline_df)

        grain = result[result["food_group"] == "grain"]
        assert grain["ratio"].sum() == pytest.approx(1.0)

    def test_single_food_group_gets_ratio_one(self, baseline_df):
        result = _build_ratios_from_baseline(baseline_df)

        beef = result[result["food"] == "beef"]
        assert beef["ratio"].values[0] == pytest.approx(1.0)

        lentils = result[result["food"] == "lentils"]
        assert lentils["ratio"].values[0] == pytest.approx(1.0)

    def test_correct_within_group_proportions(self, baseline_df):
        result = _build_ratios_from_baseline(baseline_df)

        grain = result[result["food_group"] == "grain"].set_index("food")
        # wheat=150, rice=50, maize=100 → total=300
        assert grain.at["wheat", "ratio"] == pytest.approx(150 / 300)
        assert grain.at["rice", "ratio"] == pytest.approx(50 / 300)
        assert grain.at["maize", "ratio"] == pytest.approx(100 / 300)

    def test_country_codes_uppercased(self, baseline_df_multi_country):
        result = _build_ratios_from_baseline(baseline_df_multi_country)

        assert (result["country"] == result["country"].str.upper()).all()

    def test_per_country_ratios_independent(self, baseline_df_multi_country):
        result = _build_ratios_from_baseline(baseline_df_multi_country)

        usa = result[result["country"] == "USA"].set_index("food")
        ind = result[result["country"] == "IND"].set_index("food")

        # USA: wheat=150, rice=50
        assert usa.at["wheat", "ratio"] == pytest.approx(0.75)
        assert usa.at["rice", "ratio"] == pytest.approx(0.25)

        # IND: wheat=30, rice=170
        assert ind.at["wheat", "ratio"] == pytest.approx(30 / 200)
        assert ind.at["rice", "ratio"] == pytest.approx(170 / 200)

    def test_zero_consumption_gives_zero_ratio(self):
        df = pd.DataFrame(
            {
                "food": ["wheat", "rice"],
                "country": ["USA", "USA"],
                "food_group": ["grain", "grain"],
                "consumption_g_per_day": [0.0, 0.0],
            }
        )
        result = _build_ratios_from_baseline(df)

        assert (result["ratio"] == 0.0).all()

    def test_output_columns(self, baseline_df):
        result = _build_ratios_from_baseline(baseline_df)

        assert list(result.columns) == ["country", "food_group", "food", "ratio"]


# ---------------------------------------------------------------------------
# Test add_food_incentives_to_objective
# ---------------------------------------------------------------------------


class TestAddFoodIncentivesToObjective:
    def test_applies_adjustment_to_marginal_cost(self, food_network, tmp_path):
        csv_path = tmp_path / "incentives.csv"
        pd.DataFrame(
            {
                "food": ["wheat", "rice"],
                "country": ["USA", "USA"],
                "adjustment_bnusd_per_mt": [0.5, -0.3],
            }
        ).to_csv(csv_path, index=False)

        original_wheat = food_network.links.static.at[
            "consume:wheat:USA", "marginal_cost"
        ]
        original_rice = food_network.links.static.at[
            "consume:rice:USA", "marginal_cost"
        ]

        add_food_incentives_to_objective(food_network, [str(csv_path)])

        assert food_network.links.static.at[
            "consume:wheat:USA", "marginal_cost"
        ] == pytest.approx(original_wheat + 0.5)
        assert food_network.links.static.at[
            "consume:rice:USA", "marginal_cost"
        ] == pytest.approx(original_rice - 0.3)

    def test_does_not_affect_other_links(self, food_network, tmp_path):
        csv_path = tmp_path / "incentives.csv"
        pd.DataFrame(
            {
                "food": ["wheat"],
                "country": ["USA"],
                "adjustment_bnusd_per_mt": [1.0],
            }
        ).to_csv(csv_path, index=False)

        original_beef = food_network.links.static.at[
            "consume:beef:USA", "marginal_cost"
        ]

        add_food_incentives_to_objective(food_network, [str(csv_path)])

        assert food_network.links.static.at[
            "consume:beef:USA", "marginal_cost"
        ] == pytest.approx(original_beef)

    def test_sums_across_multiple_sources(self, food_network, tmp_path):
        csv1 = tmp_path / "inc1.csv"
        csv2 = tmp_path / "inc2.csv"

        pd.DataFrame(
            {
                "food": ["wheat"],
                "country": ["USA"],
                "adjustment_bnusd_per_mt": [0.3],
            }
        ).to_csv(csv1, index=False)

        pd.DataFrame(
            {
                "food": ["wheat"],
                "country": ["USA"],
                "adjustment_bnusd_per_mt": [0.7],
            }
        ).to_csv(csv2, index=False)

        original = food_network.links.static.at["consume:wheat:USA", "marginal_cost"]

        add_food_incentives_to_objective(food_network, [str(csv1), str(csv2)])

        assert food_network.links.static.at[
            "consume:wheat:USA", "marginal_cost"
        ] == pytest.approx(original + 1.0)

    def test_raises_on_empty_paths(self, food_network):
        with pytest.raises(ValueError, match="no sources"):
            add_food_incentives_to_objective(food_network, [])

    def test_raises_on_missing_columns(self, food_network, tmp_path):
        csv_path = tmp_path / "bad.csv"
        pd.DataFrame({"food": ["wheat"], "country": ["USA"]}).to_csv(
            csv_path, index=False
        )

        with pytest.raises(ValueError, match="Missing required columns"):
            add_food_incentives_to_objective(food_network, [str(csv_path)])

    def test_country_case_insensitive(self, food_network, tmp_path):
        csv_path = tmp_path / "incentives.csv"
        pd.DataFrame(
            {
                "food": ["wheat"],
                "country": ["usa"],  # lowercase
                "adjustment_bnusd_per_mt": [0.5],
            }
        ).to_csv(csv_path, index=False)

        original = food_network.links.static.at["consume:wheat:USA", "marginal_cost"]

        add_food_incentives_to_objective(food_network, [str(csv_path)])

        assert food_network.links.static.at[
            "consume:wheat:USA", "marginal_cost"
        ] == pytest.approx(original + 0.5)


# ---------------------------------------------------------------------------
# Test add_food_slack_generators and fix_food_consumption_to_baseline
# ---------------------------------------------------------------------------


class TestFoodSlackGeneratorsAndFixation:
    def test_generators_added_to_food_buses(self, food_network):
        food_network.set_snapshots(["now"])

        baseline_df = pd.DataFrame(
            {
                "food": ["wheat", "rice"],
                "country": ["USA", "USA"],
                "food_group": ["grain", "grain"],
                "consumption_g_per_day": [100.0, 50.0],
            }
        )
        population = {"USA": 1_000_000.0, "IND": 1_000_000.0}
        consume_links = food_network.links.static[
            food_network.links.static["carrier"] == "food_consumption"
        ]
        matched = _match_baseline_to_consume_links(
            baseline_df, consume_links, population
        )
        assert matched is not None

        add_food_slack_generators(food_network, matched, slack_cost=50.0)

        gens = food_network.generators.static
        pos = gens[gens["carrier"] == "slack_positive_food"]
        neg = gens[gens["carrier"] == "slack_negative_food"]
        assert len(pos) == 2
        assert len(neg) == 2
        assert "food" in pos.columns
        assert "food_group" in pos.columns

    def test_p_set_fixed_on_consume_links(self, food_network):
        food_network.set_snapshots(["now"])

        baseline_df = pd.DataFrame(
            {
                "food": ["wheat", "rice"],
                "country": ["USA", "USA"],
                "food_group": ["grain", "grain"],
                "consumption_g_per_day": [100.0, 50.0],
            }
        )
        population = {"USA": 1_000_000.0, "IND": 1_000_000.0}
        consume_links = food_network.links.static[
            food_network.links.static["carrier"] == "food_consumption"
        ]
        matched = _match_baseline_to_consume_links(
            baseline_df, consume_links, population
        )
        assert matched is not None

        fix_food_consumption_to_baseline(food_network, matched)

        p_set = food_network.links.dynamic.p_set
        assert p_set.loc["now", "consume:wheat:USA"] > 0
        assert p_set.loc["now", "consume:rice:USA"] > 0
        # Beef not in baseline, should not have p_set
        assert "consume:beef:USA" not in p_set.columns or pd.isna(
            p_set.loc["now", "consume:beef:USA"]
        )


# ---------------------------------------------------------------------------
# Test extract_consumer_values
# ---------------------------------------------------------------------------


class TestExtractConsumerValues:
    @staticmethod
    def _make_network_with_duals(
        foods: list[str],
        food_groups: list[str],
        countries: list[str],
        mu_values: list[float],
    ) -> pypsa.Network:
        """Build a mock solved network with p_set duals on consume links."""

        n = pypsa.Network()
        n.set_snapshots(["now"])

        link_names = [f"consume:{f}:{c}" for f, c in zip(foods, countries)]
        bus0_names = [f"food:{f}:{c}" for f, c in zip(foods, countries)]
        bus1_names = [f"group:{fg}:{c}" for fg, c in zip(food_groups, countries)]

        for bus in set(bus0_names + bus1_names):
            n.add("Bus", bus)

        n.links.add(
            link_names,
            bus0=bus0_names,
            bus1=bus1_names,
            carrier="food_consumption",
            food=foods,
            food_group=food_groups,
            country=countries,
        )

        # Simulate p_set (targets)
        p_set_df = pd.DataFrame({name: [1.0] for name in link_names}, index=n.snapshots)
        n.links.dynamic["p_set"] = p_set_df

        # Simulate mu_p_set (duals)
        mu_df = pd.DataFrame(
            {name: [mu] for name, mu in zip(link_names, mu_values)},
            index=n.snapshots,
        )
        n.links.dynamic["mu_p_set"] = mu_df

        return n

    def test_extracts_correct_values(self):
        n = self._make_network_with_duals(
            foods=["wheat", "beef"],
            food_groups=["grain", "red_meat"],
            countries=["USA", "USA"],
            mu_values=[1.5, -2.0],
        )

        result = extract_consumer_values(n)

        assert len(result) == 2
        wheat = result[result["food"] == "wheat"].iloc[0]
        assert wheat["value_bnusd_per_mt"] == pytest.approx(1.5)
        assert wheat["adjustment_bnusd_per_mt"] == pytest.approx(-1.5)

        beef = result[result["food"] == "beef"].iloc[0]
        assert beef["value_bnusd_per_mt"] == pytest.approx(-2.0)
        assert beef["adjustment_bnusd_per_mt"] == pytest.approx(2.0)

    def test_output_columns(self):
        n = self._make_network_with_duals(
            foods=["wheat"],
            food_groups=["grain"],
            countries=["USA"],
            mu_values=[1.0],
        )

        result = extract_consumer_values(n)

        expected_cols = {
            "food",
            "food_group",
            "country",
            "value_bnusd_per_mt",
            "adjustment_bnusd_per_mt",
        }
        assert set(result.columns) == expected_cols

    def test_country_uppercased(self):
        n = self._make_network_with_duals(
            foods=["wheat"],
            food_groups=["grain"],
            countries=["usa"],
            mu_values=[1.0],
        )

        result = extract_consumer_values(n)

        assert result["country"].iloc[0] == "USA"

    def test_raises_when_no_p_set_duals(self):
        n = pypsa.Network()

        with pytest.raises(ValueError, match="enforce_baseline_diet"):
            extract_consumer_values(n)

    def test_multi_country(self):
        n = self._make_network_with_duals(
            foods=["wheat", "wheat"],
            food_groups=["grain", "grain"],
            countries=["USA", "IND"],
            mu_values=[1.0, 3.0],
        )

        result = extract_consumer_values(n)

        assert len(result) == 2
        usa = result[result["country"] == "USA"].iloc[0]
        ind = result[result["country"] == "IND"].iloc[0]
        assert usa["value_bnusd_per_mt"] == pytest.approx(1.0)
        assert ind["value_bnusd_per_mt"] == pytest.approx(3.0)
