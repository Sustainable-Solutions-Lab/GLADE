# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the FBS baseline-diet source (diet.source: fbs)."""

import pandas as pd
import pytest

from workflow.scripts.diet.fbs_intake import (
    DAIRY_ITEM_CODES,
    OIL_GROUP_ITEM_CODE,
    build_fbs_group_intake,
    build_item_attribution,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def food_groups_df():
    return pd.DataFrame(
        {
            "food": [
                "flour-white",
                "flour-wholemeal",
                "rice-white",
                "rice-brown",
                "foxtail-millet",
                "pearl-millet",
                "dairy",
                "dairy-buffalo",
                "palm-oil",
                "olive-oil",
                "tomato",
                "onion",
                "cabbage",
                "carrot",
                "plantain",
                "banana",
                "coffee-green",
            ],
            "group": [
                "grain",
                "whole_grains",
                "grain",
                "whole_grains",
                "whole_grains",
                "whole_grains",
                "dairy",
                "dairy",
                "oil",
                "oil",
                "vegetables",
                "vegetables",
                "vegetables",
                "vegetables",
                "starchy_vegetable",
                "fruits",
                "stimulants",
            ],
        }
    )


@pytest.fixture
def food_item_map_df():
    return pd.DataFrame(
        {
            "food": [
                "flour-white",
                "flour-wholemeal",
                "rice-white",
                "rice-brown",
                "foxtail-millet",
                "pearl-millet",
                "dairy",
                "dairy-buffalo",
                "palm-oil",
                "olive-oil",
                "tomato",
                "onion",
                "plantain",
                "banana",
                "coffee-green",
            ],
            "item_code": [
                2511,
                2511,
                2807,
                2807,
                2517,
                2517,
                2848,
                2848,
                2577,
                2580,
                2601,
                2602,
                2616,
                2615,
                2630,
            ],
        }
    )


@pytest.fixture
def nutrition_df(food_groups_df):
    # Includes pool-recipient foods (citrus, potato, ...) that are not in
    # the fixture food_groups: pool densities only need nutrition entries.
    kcal_per_100g = {
        "flour-white": 364.0,
        "flour-wholemeal": 332.5,
        "rice-white": 365.0,
        "rice-brown": 366.6,
        "foxtail-millet": 378.1,
        "pearl-millet": 378.1,
        "dairy": 60.7,
        "dairy-buffalo": 96.6,
        "palm-oil": 884.1,
        "olive-oil": 884.1,
        "tomato": 17.7,
        "onion": 39.7,
        "cabbage": 24.6,
        "carrot": 41.4,
        "plantain": 60.0,
        "banana": 88.7,
        "coffee-green": 0.5,
        "citrus": 47.1,
        "mango": 59.8,
        "watermelon": 30.3,
        "apple": 52.1,
        "potato": 77.0,
        "sweet-potato": 85.8,
        "yam": 118.1,
        "cassava": 159.4,
    }
    return pd.DataFrame(
        {
            "food": list(kcal_per_100g),
            "nutrient": "cal",
            "value": list(kcal_per_100g.values()),
        }
    )


FOOD_GROUPS_INCLUDED = [
    "grain",
    "whole_grains",
    "dairy",
    "oil",
    "vegetables",
    "starchy_vegetable",
    "fruits",
    "stimulants",
]

WHOLE_GRAIN_SHARES = {"flour-wholemeal": 0.2, "rice-brown": 0.1}


def _kcal_supply(cells: dict[int, float], country: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "country": country,
            "item_code": list(cells),
            "kcal_per_capita_day": list(cells.values()),
        }
    )


def _flat_waste(
    countries: list[str], groups: list[str], waste: float = 0.0
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"country": c, "food_group": g, "waste_fraction": waste}
            for c in countries
            for g in groups
        ]
    )


def _intake(
    kcal_cells: dict[int, float],
    food_item_map_df,
    food_groups_df,
    nutrition_df,
    waste: float = 0.0,
    whole_grain_shares: dict[str, float] = WHOLE_GRAIN_SHARES,
) -> pd.Series:
    result = build_fbs_group_intake(
        kcal_supply=_kcal_supply(kcal_cells),
        food_item_map=food_item_map_df,
        food_groups=food_groups_df,
        nutrition=nutrition_df,
        food_loss_waste=_flat_waste(["AAA"], FOOD_GROUPS_INCLUDED, waste),
        countries=["AAA"],
        food_groups_included=FOOD_GROUPS_INCLUDED,
        byproducts=[],
        whole_grain_shares=whole_grain_shares,
    )
    return result.set_index("food_group")["value"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKcalDerivation:
    def test_single_group_item(self, food_item_map_df, food_groups_df, nutrition_df):
        # Tomato: 35.4 kcal/d at 0.177 kcal/g -> 200 g/d.
        totals = _intake({2601: 35.4}, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["vegetables"] == pytest.approx(200.0)

    def test_waste_correction(self, food_item_map_df, food_groups_df, nutrition_df):
        totals = _intake(
            {2601: 35.4}, food_item_map_df, food_groups_df, nutrition_df, waste=0.25
        )
        assert totals["vegetables"] == pytest.approx(150.0)

    def test_shared_item_counted_once(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Millet 2517 maps to two whole_grains foods with equal density;
        # the item's energy must enter the group total exactly once.
        totals = _intake({2517: 378.1}, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["whole_grains"] == pytest.approx(100.0)

    def test_missing_cells_are_zero_supply(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        totals = _intake({2601: 35.4}, food_item_map_df, food_groups_df, nutrition_df)
        assert "grain" not in totals.index


class TestWholeGrainSplit:
    def test_wheat_split_between_groups(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Wheat 2511: 364 kcal/d; 20% to flour-wholemeal (3.325 kcal/g),
        # 80% to flour-white (3.64 kcal/g).
        totals = _intake({2511: 364.0}, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["whole_grains"] == pytest.approx(0.2 * 364.0 / 3.325)
        assert totals["grain"] == pytest.approx(0.8 * 364.0 / 3.64)

    def test_missing_share_raises(self, food_item_map_df, food_groups_df, nutrition_df):
        with pytest.raises(ValueError, match="whole_grain_shares"):
            _intake(
                {2511: 364.0},
                food_item_map_df,
                food_groups_df,
                nutrition_df,
                whole_grain_shares={"rice-brown": 0.1},
            )

    def test_unexpected_cross_group_item_raises(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        item_map = pd.concat(
            [
                food_item_map_df,
                pd.DataFrame({"food": ["tomato"], "item_code": [2511]}),
            ],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="across groups"):
            _intake({2511: 364.0}, item_map, food_groups_df, nutrition_df)


class TestSpecialGroups:
    def test_dairy_folds_butter_and_cream_at_cow_density(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Milk 60.7 + butter 30 + cream 9.3 = 100 kcal/d at 0.607 kcal/g.
        cells = dict(zip(DAIRY_ITEM_CODES, [60.7, 30.0, 9.3]))
        totals = _intake(cells, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["dairy"] == pytest.approx(100.0 / 0.607)

    def test_oil_uses_aggregate_item_only(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Individual oil items are subsumed by the 2914 aggregate and
        # must not double-count.
        cells = {OIL_GROUP_ITEM_CODE: 88.41, 2577: 50.0, 2580: 30.0}
        totals = _intake(cells, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["oil"] == pytest.approx(10.0)

    def test_stimulants_not_emitted(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        totals = _intake(
            {2630: 2.0, 2601: 35.4}, food_item_map_df, food_groups_df, nutrition_df
        )
        assert "stimulants" not in totals.index


class TestPoolItems:
    def test_unmapped_pool_item_at_mean_recipient_density(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Vegetables-other 2605 is not mapped to a food; it lands on the
        # vegetables group at the mean OVG recipient density
        # ((0.397 + 0.246 + 0.414) / 3 kcal/g).
        totals = _intake({2605: 105.7}, food_item_map_df, food_groups_df, nutrition_df)
        mean_density = (0.397 + 0.246 + 0.414) / 3
        assert totals["vegetables"] == pytest.approx(105.7 / mean_density)

    def test_mapped_pool_item_attributed_via_mapping(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        # Plantains 2616 is in the fruits BAN pool but mapped to the
        # plantain food (starchy_vegetable); the mapping wins.
        totals = _intake({2616: 60.0}, food_item_map_df, food_groups_df, nutrition_df)
        assert totals["starchy_vegetable"] == pytest.approx(100.0)
        assert "fruits" not in totals.index


class TestValidation:
    def test_missing_waste_fraction_raises(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        with pytest.raises(ValueError, match="waste fraction"):
            build_fbs_group_intake(
                kcal_supply=_kcal_supply({2601: 35.4}),
                food_item_map=food_item_map_df,
                food_groups=food_groups_df,
                nutrition=nutrition_df,
                food_loss_waste=_flat_waste(["BBB"], FOOD_GROUPS_INCLUDED),
                countries=["AAA"],
                food_groups_included=FOOD_GROUPS_INCLUDED,
                byproducts=[],
                whole_grain_shares=WHOLE_GRAIN_SHARES,
            )

    def test_food_missing_from_food_groups_raises(
        self, food_item_map_df, food_groups_df, nutrition_df
    ):
        item_map = pd.concat(
            [
                food_item_map_df,
                pd.DataFrame({"food": ["mystery-food"], "item_code": [2999]}),
            ],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="missing from"):
            build_item_attribution(
                item_map,
                food_groups_df,
                {"tomato": 0.177},
                FOOD_GROUPS_INCLUDED,
                byproducts=[],
                whole_grain_shares=WHOLE_GRAIN_SHARES,
            )

    def test_uncovered_group_raises(self, food_item_map_df, food_groups_df):
        nutrition = pd.DataFrame(
            {
                "food": ["tomato", "onion", "cabbage", "carrot"],
                "nutrient": "cal",
                "value": [17.7, 39.7, 24.6, 41.4],
            }
        )
        with pytest.raises(ValueError, match="No FBS items attributed"):
            build_fbs_group_intake(
                kcal_supply=_kcal_supply({2601: 35.4}),
                food_item_map=food_item_map_df[food_item_map_df["food"] == "tomato"],
                food_groups=food_groups_df[
                    food_groups_df["group"].isin(["vegetables", "grain"])
                ],
                nutrition=nutrition,
                food_loss_waste=_flat_waste(["AAA"], ["vegetables", "grain"]),
                countries=["AAA"],
                food_groups_included=["vegetables", "grain"],
                byproducts=[],
                whole_grain_shares={},
            )
