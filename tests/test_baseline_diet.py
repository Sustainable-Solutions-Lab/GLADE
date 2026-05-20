# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for baseline diet estimation."""

import pandas as pd
import pytest

from workflow.scripts.estimate_baseline_diet import (
    WITHIN_QCL_FOOD_SPLITS,
    _resolve_shared_fbs_item,
    apply_kcal_normalisation,
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
            "production_mt_fresh_retail": [
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
# Tests: _resolve_shared_fbs_item with WITHIN_QCL_FOOD_SPLITS
# ---------------------------------------------------------------------------


class TestSameQclBucketSplit:
    """Tests for absolute within-bucket splits via food_split_overrides."""

    def test_pearl_foxtail_millet_split(self):
        """When both millets share the QCL "Millet" bucket and there is no
        production data, the override pins them to 0.8 / 0.2."""
        # Both millets have the same QCL code; no per-species production.
        qcl_lookup = {"pearl-millet": 88, "foxtail-millet": 88}
        shares = _resolve_shared_fbs_item(
            "USA",
            ["pearl-millet", "foxtail-millet"],
            qcl_lookup,
            crop_prod_lookup={},  # no production -> falls into equal-bucket path
            animal_prod_lookup={},
            food_split_overrides=WITHIN_QCL_FOOD_SPLITS,
        )
        assert shares["pearl-millet"] == pytest.approx(0.8)
        assert shares["foxtail-millet"] == pytest.approx(0.2)
        assert sum(shares.values()) == pytest.approx(1.0)

    def test_split_with_production_scales_bucket_share(self):
        """Override applies to the within-bucket weights, not the bucket
        share (the bucket share still comes from production)."""
        # Two QCL buckets: millet (88) shared by pearl+foxtail, and another
        # crop (99) for cowpea. Production splits the buckets 30/70.
        qcl_lookup = {
            "pearl-millet": 88,
            "foxtail-millet": 88,
            "cowpea": 99,
        }
        crop_prod = {("USA", 88): 30.0, ("USA", 99): 70.0}
        shares = _resolve_shared_fbs_item(
            "USA",
            ["pearl-millet", "foxtail-millet", "cowpea"],
            qcl_lookup,
            crop_prod,
            animal_prod_lookup={},
            food_split_overrides=WITHIN_QCL_FOOD_SPLITS,
        )
        # Millet bucket has 30% of supply; pearl gets 0.8 of that, foxtail 0.2.
        assert shares["pearl-millet"] == pytest.approx(0.30 * 0.8)
        assert shares["foxtail-millet"] == pytest.approx(0.30 * 0.2)
        assert shares["cowpea"] == pytest.approx(0.70)
        assert sum(shares.values()) == pytest.approx(1.0)


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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
        )

        by_food = shares.set_index("food")["share"]
        # Onion (2602) and Vegetables, Other (2605) are pooled symmetrically
        # so the combined supply 20+70=90 splits across onion/cabbage/carrot
        # by production share (30/50/20): 27/45/18. Tomato (2601) contributes
        # its explicit 10 directly. Group total 100 -> shares 0.27/0.45/0.18/0.10.
        assert by_food["onion"] == pytest.approx(0.27)
        assert by_food["cabbage"] == pytest.approx(0.45)
        assert by_food["carrot"] == pytest.approx(0.18)
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
        # Includes zero-supply entries for pool codes (2616, 2617-2619, 2625)
        # so the pool-code presence validator in build_within_group_shares
        # passes for the fruits group. They do not affect the test outcome.
        fbs_items_df = pd.DataFrame(
            {
                "item_code": [
                    2615,
                    2611,
                    2612,
                    2613,
                    2614,
                    2616,
                    2617,
                    2618,
                    2619,
                    2625,
                ],
                "item_name": [
                    "Bananas",
                    "Oranges, Mandarines",
                    "Lemons, Limes and products",
                    "Grapefruit and products",
                    "Citrus, Other",
                    "Plantains",
                    "Apples",
                    "Pineapples",
                    "Dates",
                    "Fruits, Other",
                ],
                "country": ["USA"] * 10,
                "supply_kg_per_capita_year": [
                    100.0,
                    30.0,
                    20.0,
                    10.0,
                    40.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ],
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
            weight_conversion={"carcass_to_fresh": {}},
            frt_attribution_df=pd.DataFrame(
                columns=["country", "crop", "target_production_tonnes"]
            ),
            edible_portion_by_food={},
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
# kcal normalisation
# ---------------------------------------------------------------------------


class TestApplyKcalNormalisation:
    """Food-level kcal normalisation hits the GDD-IA target even when the
    intra-group food mix is far from the group-mean energy density.

    Regression test: an earlier group-mean implementation undershot the
    rescaling factor for cassava-heavy starchy_vegetable diets, leaving
    Sub-Saharan African countries 8-35% above their GDD-IA target.
    """

    def test_food_level_kpg_hits_target_for_heterogeneous_group(self):
        """Two countries with identical group totals but different
        within-group food mixes should both land on the same kcal target.
        """
        baseline_diet = pd.DataFrame(
            {
                "country": ["A", "A", "A", "B", "B", "B"],
                "food": [
                    "cassava",
                    "potato",
                    "rice-white",
                    "cassava",
                    "potato",
                    "rice-white",
                ],
                "food_group": [
                    "starchy_vegetable",
                    "starchy_vegetable",
                    "grain",
                    "starchy_vegetable",
                    "starchy_vegetable",
                    "grain",
                ],
                # Same group totals (500 g/day starchy, 100 g/day grain) but
                # A is cassava-heavy (high kcal/g) and B is potato-heavy.
                "consumption_g_per_day": [400.0, 100.0, 100.0, 100.0, 400.0, 100.0],
            }
        )
        kpg = {"cassava": 1.59, "potato": 0.77, "rice-white": 3.65}
        target_df = pd.DataFrame(
            {"country": ["A", "B"], "kcal_target_modelled": [600.0, 600.0]}
        )

        result = apply_kcal_normalisation(
            baseline_diet,
            target_df,
            gbd_anchored_groups=set(),
            kcal_per_g_food=kpg,
        )

        for c in ("A", "B"):
            sub = result[result["country"] == c]
            kcal = float((sub["consumption_g_per_day"] * sub["food"].map(kpg)).sum())
            assert kcal == pytest.approx(
                600.0, abs=1e-6
            ), f"country {c} kcal={kcal:.2f} != target 600"

    def test_anchored_groups_are_not_rescaled(self):
        """Foods in GBD-anchored groups (and the refined-grain residual)
        must keep their consumption unchanged; only unanchored foods get
        scaled to close the residual.
        """
        baseline_diet = pd.DataFrame(
            {
                "country": ["A", "A", "A"],
                "food": ["red-meat-item", "cassava", "rice-white"],
                "food_group": ["red_meat", "starchy_vegetable", "grain"],
                "consumption_g_per_day": [50.0, 500.0, 100.0],
            }
        )
        kpg = {"red-meat-item": 2.5, "cassava": 1.59, "rice-white": 3.65}
        target_df = pd.DataFrame({"country": ["A"], "kcal_target_modelled": [1000.0]})

        result = apply_kcal_normalisation(
            baseline_diet,
            target_df,
            gbd_anchored_groups={"red_meat"},
            kcal_per_g_food=kpg,
        )

        red_meat_g = float(
            result.loc[result["food"] == "red-meat-item", "consumption_g_per_day"].iloc[
                0
            ]
        )
        grain_g = float(
            result.loc[result["food"] == "rice-white", "consumption_g_per_day"].iloc[0]
        )
        cassava_g = float(
            result.loc[result["food"] == "cassava", "consumption_g_per_day"].iloc[0]
        )

        assert red_meat_g == pytest.approx(50.0)
        assert grain_g == pytest.approx(100.0)
        # Unanchored cassava is rescaled: anchored kcal = 50*2.5 + 100*3.65 = 490.
        # target_unanchored = 510. pre-scaling cassava kcal = 500*1.59 = 795.
        # factor = 510/795 = 0.6415. post: 500 * 0.6415 = 320.75.
        assert cassava_g == pytest.approx(320.75, rel=1e-3)

    def test_missing_food_raises(self):
        """Foods without a kcal/g entry should raise; the previous code
        silently substituted zero.
        """
        baseline_diet = pd.DataFrame(
            {
                "country": ["A"],
                "food": ["mystery-food"],
                "food_group": ["starchy_vegetable"],
                "consumption_g_per_day": [100.0],
            }
        )
        target_df = pd.DataFrame({"country": ["A"], "kcal_target_modelled": [500.0]})
        with pytest.raises(ValueError, match="mystery-food"):
            apply_kcal_normalisation(
                baseline_diet,
                target_df,
                gbd_anchored_groups=set(),
                kcal_per_g_food={"other-food": 1.0},
            )
