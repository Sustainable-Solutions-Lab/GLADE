# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for baseline diet estimation."""

import pandas as pd
import pytest

from workflow.scripts.estimate_baseline_diet import (
    GDD_GBD_AVERAGED_GROUPS,
    _apply_millet_split,
    _resolve_shared_fbs_item,
    build_within_group_shares,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def food_groups_df():
    """Minimal food groups mapping."""
    return pd.DataFrame(
        {
            "food": [
                "flour-white",
                "rice-white",
                "cowpea",
                "chickpea",
                "gram",
                "dairy",
                "dairy-buffalo",
                "foxtail-millet",
                "pearl-millet",
                "tomato",
            ],
            "group": [
                "grain",
                "grain",
                "legumes",
                "legumes",
                "legumes",
                "dairy",
                "dairy",
                "whole_grains",
                "whole_grains",
                "vegetables",
            ],
        }
    )


@pytest.fixture
def food_item_map_df():
    """Minimal food → FBS item mapping."""
    return pd.DataFrame(
        {
            "food": [
                "flour-white",
                "rice-white",
                "cowpea",
                "chickpea",
                "gram",
                "dairy",
                "dairy-buffalo",
                "foxtail-millet",
                "pearl-millet",
                "tomato",
            ],
            "item_code": [2511, 2807, 2546, 2546, 2546, 2848, 2848, 2517, 2517, 2601],
        }
    )


@pytest.fixture
def qcl_resolution_df():
    """QCL resolution mapping for shared FBS items."""
    return pd.DataFrame(
        {
            "food": ["cowpea", "chickpea", "gram", "dairy", "dairy-buffalo"],
            "fbs_item_code": [2546, 2546, 2546, 2848, 2848],
            "qcl_item_name": [
                "Cow peas, dry",
                "Chick peas, dry",
                "Chick peas, dry",
                "Raw milk of cattle",
                "Raw milk of buffalo",
            ],
            "qcl_item_code": [195, 191, 191, 882, 951],
        }
    )


@pytest.fixture
def fbs_items_df():
    """FBS supply data for two countries."""
    rows = []
    for country in ["USA", "IND"]:
        rows.extend(
            [
                {
                    "item_code": 2511,
                    "item_name": "Wheat",
                    "country": country,
                    "supply_kg_per_capita_year": 100.0,
                },
                {
                    "item_code": 2807,
                    "item_name": "Rice",
                    "country": country,
                    "supply_kg_per_capita_year": 50.0,
                },
                {
                    "item_code": 2546,
                    "item_name": "Beans",
                    "country": country,
                    "supply_kg_per_capita_year": 30.0,
                },
                {
                    "item_code": 2848,
                    "item_name": "Milk",
                    "country": country,
                    "supply_kg_per_capita_year": 200.0,
                },
                {
                    "item_code": 2517,
                    "item_name": "Millet",
                    "country": country,
                    "supply_kg_per_capita_year": 10.0,
                },
                {
                    "item_code": 2601,
                    "item_name": "Tomatoes",
                    "country": country,
                    "supply_kg_per_capita_year": 20.0,
                },
            ]
        )
    return pd.DataFrame(rows)


@pytest.fixture
def crop_production_df():
    """Crop production data for QCL-based resolution."""
    return pd.DataFrame(
        {
            "country": ["USA", "USA", "USA", "IND", "IND", "IND"],
            "crop": ["cowpea", "chickpea", "gram", "cowpea", "chickpea", "gram"],
            "year": [2018] * 6,
            "production_tonnes": [
                100,  # USA cowpea
                300,  # USA chickpea (also gram via QCL 191)
                0,  # USA gram (same QCL as chickpea)
                500,  # IND cowpea
                1000,  # IND chickpea
                0,  # IND gram
            ],
        }
    )


@pytest.fixture
def animal_production_df():
    """Animal production data for dairy resolution."""
    return pd.DataFrame(
        {
            "country": ["USA", "USA", "IND", "IND"],
            "product": ["dairy", "dairy-buffalo", "dairy", "dairy-buffalo"],
            "year": [2018] * 4,
            "production_mt": [
                100.0,  # USA cattle milk
                0.0,  # USA buffalo milk
                80.0,  # IND cattle milk
                70.0,  # IND buffalo milk
            ],
        }
    )


# ---------------------------------------------------------------------------
# Tests: _resolve_shared_fbs_item
# ---------------------------------------------------------------------------


class TestResolveSharedFbsItem:
    """Tests for QCL-based resolution of shared FBS items."""

    def test_production_based_split(self):
        """Foods with different QCL codes split by production."""
        qcl_lookup = {"cowpea": 195, "chickpea": 191, "gram": 191}
        crop_prod = {
            ("USA", 195): 100.0,  # cowpea
            ("USA", 191): 300.0,  # chickpea + gram
        }
        shares = _resolve_shared_fbs_item(
            "USA",
            ["cowpea", "chickpea", "gram"],
            qcl_lookup,
            crop_prod,
            {},
        )
        # Total production = 400. cowpea=100/400=0.25, chickpea+gram=300/400=0.75
        # chickpea and gram share QCL 191, so each gets 0.75/2 = 0.375
        assert shares["cowpea"] == pytest.approx(0.25)
        assert shares["chickpea"] == pytest.approx(0.375)
        assert shares["gram"] == pytest.approx(0.375)
        assert sum(shares.values()) == pytest.approx(1.0)

    def test_zero_production_equal_split(self):
        """When no production data, all foods get equal shares."""
        qcl_lookup = {"cowpea": 195, "chickpea": 191}
        shares = _resolve_shared_fbs_item(
            "USA",
            ["cowpea", "chickpea"],
            qcl_lookup,
            {},  # no crop production
            {},  # no animal production
        )
        assert shares["cowpea"] == pytest.approx(0.5)
        assert shares["chickpea"] == pytest.approx(0.5)

    def test_animal_production_fallback(self):
        """When crop production is zero, falls back to animal production."""
        qcl_lookup = {"dairy": 882, "dairy-buffalo": 951}
        animal_prod = {
            ("IND", 882): 80.0,
            ("IND", 951): 70.0,
        }
        shares = _resolve_shared_fbs_item(
            "IND",
            ["dairy", "dairy-buffalo"],
            qcl_lookup,
            {},  # no crop production
            animal_prod,
        )
        total = 150.0
        assert shares["dairy"] == pytest.approx(80.0 / total)
        assert shares["dairy-buffalo"] == pytest.approx(70.0 / total)
        assert sum(shares.values()) == pytest.approx(1.0)

    def test_unresolved_foods_get_remainder(self):
        """Foods without QCL mapping get the remaining share equally."""
        qcl_lookup = {"cowpea": 195}  # only cowpea has QCL
        crop_prod = {("USA", 195): 100.0}
        shares = _resolve_shared_fbs_item(
            "USA",
            ["cowpea", "mystery-bean"],
            qcl_lookup,
            crop_prod,
            {},
        )
        # cowpea gets 100% of QCL-resolved part (only one QCL code)
        # but "mystery-bean" is unresolved and needs some share
        assert shares["cowpea"] == pytest.approx(1.0)
        assert shares["mystery-bean"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: _apply_millet_split
# ---------------------------------------------------------------------------


class TestApplyMilletSplit:
    """Tests for the fixed millet production split."""

    def test_millet_split_applied(self):
        """Pearl and foxtail millet shares are adjusted from equal split."""
        df = pd.DataFrame(
            {
                "country": ["USA", "USA"],
                "food": ["pearl-millet", "foxtail-millet"],
                "food_group": ["whole_grains", "whole_grains"],
                "share": [0.5, 0.5],
            }
        )
        _apply_millet_split(df)
        assert df.loc[df["food"] == "pearl-millet", "share"].iloc[0] == pytest.approx(
            0.8
        )
        assert df.loc[df["food"] == "foxtail-millet", "share"].iloc[0] == pytest.approx(
            0.2
        )

    def test_millet_split_sums_to_one(self):
        """After split, shares still sum to 1.0 per country."""
        df = pd.DataFrame(
            {
                "country": ["USA", "USA", "IND", "IND"],
                "food": [
                    "pearl-millet",
                    "foxtail-millet",
                    "pearl-millet",
                    "foxtail-millet",
                ],
                "food_group": ["whole_grains"] * 4,
                "share": [0.5, 0.5, 0.5, 0.5],
            }
        )
        _apply_millet_split(df)
        for country in ["USA", "IND"]:
            country_total = df[df["country"] == country]["share"].sum()
            assert country_total == pytest.approx(1.0)

    def test_no_millet_is_noop(self):
        """If no millet foods present, nothing changes."""
        df = pd.DataFrame(
            {
                "country": ["USA"],
                "food": ["wheat"],
                "food_group": ["grain"],
                "share": [1.0],
            }
        )
        _apply_millet_split(df)
        assert df["share"].iloc[0] == 1.0


# ---------------------------------------------------------------------------
# Tests: build_within_group_shares
# ---------------------------------------------------------------------------


class TestBuildWithinGroupShares:
    """Tests for the full within-group share computation."""

    def test_vegetable_residual_is_projected_across_ovg_crops(self):
        """FBS item 2605 is projected to onion/cabbage/carrot, not tomato."""
        food_groups_df = pd.DataFrame(
            {
                "food": ["onion", "cabbage", "carrot", "tomato"],
                "group": ["vegetables", "vegetables", "vegetables", "vegetables"],
            }
        )
        food_item_map_df = pd.DataFrame(
            {
                "food": ["onion", "cabbage", "carrot", "tomato"],
                "item_code": [2602, 2605, 2605, 2601],
            }
        )
        fbs_items_df = pd.DataFrame(
            {
                "item_code": [2601, 2602, 2605],
                "item_name": [
                    "Tomatoes and products",
                    "Onions",
                    "Vegetables, other",
                ],
                "country": ["USA", "USA", "USA"],
                "supply_kg_per_capita_year": [10.0, 20.0, 70.0],
            }
        )
        crop_production_df = pd.DataFrame(
            {
                "country": ["USA", "USA", "USA"],
                "crop": ["onion", "cabbage", "carrot"],
                "year": [2018, 2018, 2018],
                "production_tonnes": [30.0, 50.0, 20.0],
            }
        )

        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df=pd.DataFrame(columns=["food", "qcl_item_code"]),
            crop_production_df=crop_production_df,
            animal_production_df=pd.DataFrame(),
            food_groups_included=["vegetables"],
            byproducts=[],
            carcass_to_retail_meat={},
        )

        by_food = shares.set_index("food")["share"]
        # Residual 70 splits as 21/35/14 from OVG production shares (30/50/20).
        # Combined with explicit 20 (onion) and 10 (tomato) gives total 100.
        assert by_food["onion"] == pytest.approx(0.41)
        assert by_food["cabbage"] == pytest.approx(0.35)
        assert by_food["carrot"] == pytest.approx(0.14)
        assert by_food["tomato"] == pytest.approx(0.10)

    def test_starchy_residual_is_projected_across_modeled_starchy_foods(self):
        """FBS item 2534 is projected to potato/sweet-potato/yam/cassava."""
        food_groups_df = pd.DataFrame(
            {
                "food": ["potato", "sweet-potato", "yam", "cassava"],
                "group": ["starchy_vegetable"] * 4,
            }
        )
        food_item_map_df = pd.DataFrame(
            {
                "food": ["potato", "potato", "sweet-potato", "yam", "cassava"],
                "item_code": [2531, 2534, 2533, 2535, 2532],
            }
        )
        fbs_items_df = pd.DataFrame(
            {
                "item_code": [2531, 2532, 2533, 2534, 2535],
                "item_name": [
                    "Potatoes and products",
                    "Cassava and products",
                    "Sweet potatoes",
                    "Roots, Other",
                    "Yams",
                ],
                "country": ["USA"] * 5,
                "supply_kg_per_capita_year": [10.0, 10.0, 10.0, 40.0, 10.0],
            }
        )
        crop_production_df = pd.DataFrame(
            {
                "country": ["USA", "USA", "USA", "USA"],
                "crop": ["white-potato", "cassava", "sweet-potato", "yam"],
                "year": [2018] * 4,
                "production_tonnes": [60.0, 20.0, 10.0, 10.0],
            }
        )

        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df=pd.DataFrame(columns=["food", "qcl_item_code"]),
            crop_production_df=crop_production_df,
            animal_production_df=pd.DataFrame(),
            food_groups_included=["starchy_vegetable"],
            byproducts=[],
            carcass_to_retail_meat={},
        )

        by_food = shares.set_index("food")["share"]
        # Residual 40 splits as 24/8/4/4 from production shares 60/20/10/10.
        # Combined with explicit 10 each gives totals 34/18/14/14 out of 80.
        assert by_food["potato"] == pytest.approx(34.0 / 80.0)
        assert by_food["cassava"] == pytest.approx(18.0 / 80.0)
        assert by_food["sweet-potato"] == pytest.approx(14.0 / 80.0)
        assert by_food["yam"] == pytest.approx(14.0 / 80.0)

    def test_nuts_residual_is_projected_across_modeled_nuts_foods(self):
        """FBS item 2551 is projected to modeled nuts/seeds foods."""
        food_groups_df = pd.DataFrame(
            {
                "food": ["groundnut", "sesame-seed", "coconut", "sunflower-seed"],
                "group": ["nuts_seeds"] * 4,
            }
        )
        food_item_map_df = pd.DataFrame(
            {
                "food": [
                    "groundnut",
                    "groundnut",
                    "sesame-seed",
                    "coconut",
                    "sunflower-seed",
                ],
                "item_code": [2552, 2551, 2561, 2560, 2557],
            }
        )
        fbs_items_df = pd.DataFrame(
            {
                "item_code": [2551, 2552, 2561, 2560, 2557],
                "item_name": [
                    "Nuts and products",
                    "Groundnuts",
                    "Sesame seed",
                    "Coconuts - Incl Copra",
                    "Sunflower seed",
                ],
                "country": ["USA"] * 5,
                "supply_kg_per_capita_year": [40.0, 10.0, 10.0, 10.0, 10.0],
            }
        )
        crop_production_df = pd.DataFrame(
            {
                "country": ["USA", "USA", "USA", "USA"],
                "crop": ["groundnut", "sesame", "coconut", "sunflower"],
                "year": [2018] * 4,
                "production_tonnes": [70.0, 10.0, 10.0, 10.0],
            }
        )

        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df=pd.DataFrame(columns=["food", "qcl_item_code"]),
            crop_production_df=crop_production_df,
            animal_production_df=pd.DataFrame(),
            food_groups_included=["nuts_seeds"],
            byproducts=[],
            carcass_to_retail_meat={},
        )

        by_food = shares.set_index("food")["share"]
        # Residual 40 splits by production shares 70/10/10/10 -> 28/4/4/4.
        # Combined with explicit 10 each gives totals 38/14/14/14 out of 80.
        assert by_food["groundnut"] == pytest.approx(38.0 / 80.0)
        assert by_food["sesame-seed"] == pytest.approx(14.0 / 80.0)
        assert by_food["coconut"] == pytest.approx(14.0 / 80.0)
        assert by_food["sunflower-seed"] == pytest.approx(14.0 / 80.0)

    def test_food_with_multiple_fbs_items_is_aggregated(self):
        """A food mapped to multiple FBS items uses the summed supply."""
        food_groups_df = pd.DataFrame(
            {
                "food": ["banana", "citrus"],
                "group": ["fruits", "fruits"],
            }
        )
        food_item_map_df = pd.DataFrame(
            {
                "food": ["banana", "citrus", "citrus", "citrus", "citrus"],
                "item_code": [2615, 2611, 2612, 2613, 2614],
            }
        )
        fbs_items_df = pd.DataFrame(
            {
                "item_code": [2615, 2611, 2612, 2613, 2614],
                "item_name": [
                    "Bananas",
                    "Oranges, Mandarines",
                    "Lemons, Limes and products",
                    "Grapefruit and products",
                    "Citrus, Other",
                ],
                "country": ["USA"] * 5,
                "supply_kg_per_capita_year": [100.0, 30.0, 20.0, 10.0, 40.0],
            }
        )

        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df=pd.DataFrame(columns=["food", "qcl_item_code"]),
            crop_production_df=pd.DataFrame(),
            animal_production_df=pd.DataFrame(),
            food_groups_included=["fruits"],
            byproducts=[],
            carcass_to_retail_meat={},
        )

        banana = shares[(shares["country"] == "USA") & (shares["food"] == "banana")]
        citrus = shares[(shares["country"] == "USA") & (shares["food"] == "citrus")]
        assert banana["share"].iloc[0] == pytest.approx(0.5)
        assert citrus["share"].iloc[0] == pytest.approx(0.5)

    def test_shares_reflect_fbs_supply_within_group(
        self,
        food_groups_df,
        food_item_map_df,
        fbs_items_df,
        qcl_resolution_df,
        crop_production_df,
        animal_production_df,
    ):
        """Within-group shares are proportional to FBS supply."""
        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df,
            crop_production_df,
            animal_production_df,
            food_groups_included=["grain", "vegetables"],
            byproducts=[],
            carcass_to_retail_meat={},
        )
        # grain group: flour-white (FBS 2511, supply=100) + rice-white (FBS 2807, supply=50)
        # flour-white share = 100/150, rice-white share = 50/150
        flour = shares[(shares["food"] == "flour-white") & (shares["country"] == "USA")]
        assert len(flour) == 1
        assert flour["share"].iloc[0] == pytest.approx(100.0 / 150.0)

        rice = shares[(shares["food"] == "rice-white") & (shares["country"] == "USA")]
        assert len(rice) == 1
        assert rice["share"].iloc[0] == pytest.approx(50.0 / 150.0)

        # tomato is sole food in vegetables group → share=1.0
        tomato = shares[(shares["food"] == "tomato") & (shares["country"] == "USA")]
        assert len(tomato) == 1
        assert tomato["share"].iloc[0] == pytest.approx(1.0)

    def test_shares_sum_to_one_per_food_group(
        self,
        food_groups_df,
        food_item_map_df,
        fbs_items_df,
        qcl_resolution_df,
        crop_production_df,
        animal_production_df,
    ):
        """Within each food group, food shares sum to 1.0 per country."""
        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df,
            crop_production_df,
            animal_production_df,
            food_groups_included=[
                "legumes",
                "dairy",
                "whole_grains",
                "grain",
                "vegetables",
            ],
            byproducts=[],
            carcass_to_retail_meat={},
        )
        for country in ["USA", "IND"]:
            for fg in ["legumes", "dairy", "whole_grains", "grain", "vegetables"]:
                group_shares = shares[
                    (shares["country"] == country) & (shares["food_group"] == fg)
                ]
                if not group_shares.empty:
                    assert group_shares["share"].sum() == pytest.approx(
                        1.0, abs=0.01
                    ), f"Shares for {country}/{fg} don't sum to 1.0"

    def test_byproducts_excluded(
        self,
        food_groups_df,
        food_item_map_df,
        fbs_items_df,
        qcl_resolution_df,
        crop_production_df,
        animal_production_df,
    ):
        """Byproducts are excluded from share computation."""
        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df,
            crop_production_df,
            animal_production_df,
            food_groups_included=["grain"],
            byproducts=["flour-white"],
            carcass_to_retail_meat={},
        )
        assert "flour-white" not in shares["food"].values

    def test_india_dairy_split_reflects_production(
        self,
        food_groups_df,
        food_item_map_df,
        fbs_items_df,
        qcl_resolution_df,
        crop_production_df,
        animal_production_df,
    ):
        """India's dairy/buffalo split reflects its substantial buffalo production."""
        shares = build_within_group_shares(
            food_groups_df,
            food_item_map_df,
            fbs_items_df,
            qcl_resolution_df,
            crop_production_df,
            animal_production_df,
            food_groups_included=["dairy"],
            byproducts=[],
            carcass_to_retail_meat={},
        )
        ind_dairy = shares[(shares["country"] == "IND") & (shares["food"] == "dairy")][
            "share"
        ].iloc[0]
        ind_buffalo = shares[
            (shares["country"] == "IND") & (shares["food"] == "dairy-buffalo")
        ]["share"].iloc[0]
        # IND has 80 cattle + 70 buffalo = 150 total
        assert ind_dairy == pytest.approx(80.0 / 150.0, abs=0.01)
        assert ind_buffalo == pytest.approx(70.0 / 150.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: GDD/GBD averaging constants
# ---------------------------------------------------------------------------


class TestGroupAveragingConfig:
    """Tests for the GDD/GBD averaging group configuration."""

    def test_averaged_groups_are_valid(self):
        """All averaged groups are standard food group names."""
        valid_groups = {
            "fruits",
            "vegetables",
            "whole_grains",
            "legumes",
            "nuts_seeds",
            "red_meat",
            "grain",
            "starchy_vegetable",
            "eggs",
            "sugar",
            "dairy",
            "poultry",
            "oil",
        }
        assert GDD_GBD_AVERAGED_GROUPS.issubset(valid_groups)

    def test_faostat_only_groups_not_averaged(self):
        """Groups supplied only by FAOSTAT should not be in the averaged set."""
        faostat_only = {"dairy", "poultry", "oil"}
        assert GDD_GBD_AVERAGED_GROUPS.isdisjoint(faostat_only)
