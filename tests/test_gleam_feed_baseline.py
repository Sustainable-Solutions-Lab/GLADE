# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 feed baseline preparation."""

from typing import ClassVar

import pandas as pd
import pytest

from workflow.scripts.animal_utils import SPECIES_PRODUCTS
from workflow.scripts.prepare_feed_baseline import (
    GLEAM3_SYSTEM_PRODUCT_MAP,
    RUMINANT_ANIMALS,
    _validate_fraction_table,
    _validate_intake_fraction_coverage,
    compute_fcr_lookup,
    compute_product_shares,
)

# ---------------------------------------------------------------------------
# Tests: constant consistency
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify internal consistency of module-level constants."""

    def test_system_product_map_products_in_species_products(self):
        """Every product in GLEAM3_SYSTEM_PRODUCT_MAP appears in SPECIES_PRODUCTS."""
        all_sp_products = {p for prods in SPECIES_PRODUCTS.values() for p in prods}
        for key, products in GLEAM3_SYSTEM_PRODUCT_MAP.items():
            for p in products:
                assert p in all_sp_products, (
                    f"Product '{p}' from GLEAM3_SYSTEM_PRODUCT_MAP[{key}] "
                    f"not in SPECIES_PRODUCTS"
                )

    def test_system_product_map_covers_all_species_products(self):
        """Every product from SPECIES_PRODUCTS appears in at least one system."""
        all_sp_products = {p for prods in SPECIES_PRODUCTS.values() for p in prods}
        mapped_products = {
            p for prods in GLEAM3_SYSTEM_PRODUCT_MAP.values() for p in prods
        }
        for p in all_sp_products:
            assert p in mapped_products, (
                f"Product '{p}' from SPECIES_PRODUCTS not covered by "
                f"GLEAM3_SYSTEM_PRODUCT_MAP"
            )

    def test_ruminant_animals_consistent(self):
        """RUMINANT_ANIMALS matches ruminant species in GLEAM3_SYSTEM_PRODUCT_MAP."""
        ruminant_species = {"Cattle & buffaloes", "Small Ruminants"}
        ruminant_products = {p for sp in ruminant_species for p in SPECIES_PRODUCTS[sp]}
        for (animal, _lps), products in GLEAM3_SYSTEM_PRODUCT_MAP.items():
            for p in products:
                if p in ruminant_products:
                    assert animal in RUMINANT_ANIMALS, (
                        f"Animal '{animal}' produces ruminant product '{p}' "
                        f"but is not in RUMINANT_ANIMALS"
                    )

    def test_feedlots_single_product(self):
        """Feedlots should map to a single product (meat-cattle)."""
        feedlot_products = GLEAM3_SYSTEM_PRODUCT_MAP[("Cattle", "Feedlots")]
        assert feedlot_products == ["meat-cattle"]


# ---------------------------------------------------------------------------
# Tests: compute_product_shares
# ---------------------------------------------------------------------------


class TestComputeProductShares:
    """Tests for FCR-weighted product share computation."""

    def test_single_product_returns_one(self):
        """A single-product list always returns share of 1.0."""
        fao = pd.DataFrame(columns=["country", "product", "production_tonnes"])
        result = compute_product_shares(["meat-pig"], "USA", fao, {}, "R")
        assert result == {"meat-pig": 1.0}

    def test_no_wirsenius_region_gives_equal_shares(self):
        """Fallback to equal split when Wirsenius region is None."""
        fao = pd.DataFrame(columns=["country", "product", "production_tonnes"])
        result = compute_product_shares(["dairy", "meat-cattle"], "USA", fao, {}, None)
        assert result["dairy"] == pytest.approx(0.5)
        assert result["meat-cattle"] == pytest.approx(0.5)

    def test_no_production_gives_equal_shares(self):
        """Fallback to equal split when country has no FAO production."""
        fao = pd.DataFrame(
            {"country": ["CAN"], "product": ["dairy"], "production_tonnes": [100]}
        )
        result = compute_product_shares(
            ["dairy", "meat-cattle"], "USA", fao, {("dairy", "R"): 10.0}, "R"
        )
        assert result["dairy"] == pytest.approx(0.5)
        assert result["meat-cattle"] == pytest.approx(0.5)

    def test_fcr_weighted_shares(self):
        """Shares are proportional to production * FCR."""
        fao = pd.DataFrame(
            {
                "country": ["USA", "USA"],
                "product": ["dairy", "meat-cattle"],
                "production_tonnes": [1000, 500],
            }
        )
        fcr_lookup = {
            ("dairy", "NAO"): 10.0,
            ("meat-cattle", "NAO"): 200.0,
        }
        result = compute_product_shares(
            ["dairy", "meat-cattle"], "USA", fao, fcr_lookup, "NAO"
        )
        # dairy: 1000 * 10 = 10000; cattle: 500 * 200 = 100000
        assert result["dairy"] == pytest.approx(10000 / 110000)
        assert result["meat-cattle"] == pytest.approx(100000 / 110000)

    def test_shares_sum_to_one(self):
        """Shares always sum to 1.0."""
        fao = pd.DataFrame(
            {
                "country": ["USA", "USA", "USA"],
                "product": ["dairy", "dairy-buffalo", "meat-cattle"],
                "production_tonnes": [800, 200, 300],
            }
        )
        fcr_lookup = {
            ("dairy", "R"): 12.0,
            ("dairy-buffalo", "R"): 12.0,
            ("meat-cattle", "R"): 250.0,
        }
        result = compute_product_shares(
            ["dairy", "dairy-buffalo", "meat-cattle"], "USA", fao, fcr_lookup, "R"
        )
        assert sum(result.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tests: feed-fraction validation
# ---------------------------------------------------------------------------


class TestFeedFractionValidation:
    """Tests for feed-fraction table and coverage validation."""

    def test_validate_fraction_table_rejects_bad_sum(self):
        fractions = pd.DataFrame(
            {
                "gleam3_category": ["By-products", "By-products"],
                "animal_type": ["ruminant", "ruminant"],
                "country": ["USA", "USA"],
                "model_feed_category": ["ruminant_grain", "ruminant_forage"],
                "fraction": [0.6, 0.3],
                "exogenous": [False, False],
            }
        )
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            _validate_fraction_table(fractions)

    def test_validate_fraction_coverage_rejects_missing_key(self):
        intakes = pd.DataFrame(
            {
                "ISO3": ["USA"],
                "feed_category": ["By-products"],
                "animal_type": ["ruminant"],
                "intake_mt": [1.0],
            }
        )
        global_fractions = pd.DataFrame(
            {
                "gleam3_category": ["Grains"],
                "animal_type": ["ruminant"],
                "country": ["_global"],
                "model_feed_category": ["ruminant_grain"],
                "fraction": [1.0],
                "exogenous": [False],
            }
        )
        country_fractions = pd.DataFrame(
            columns=[
                "gleam3_category",
                "animal_type",
                "country",
                "model_feed_category",
                "fraction",
                "exogenous",
            ]
        )
        with pytest.raises(ValueError, match="Missing feed-fraction mapping"):
            _validate_intake_fraction_coverage(
                intakes, global_fractions, country_fractions
            )

    def test_validate_fraction_coverage_allows_country_fallback(self):
        intakes = pd.DataFrame(
            {
                "ISO3": ["GUF"],
                "feed_category": ["By-products"],
                "animal_type": ["ruminant"],
                "intake_mt": [1.0],
            }
        )
        global_fractions = pd.DataFrame(
            {
                "gleam3_category": ["Grains"],
                "animal_type": ["ruminant"],
                "country": ["_global"],
                "model_feed_category": ["ruminant_grain"],
                "fraction": [1.0],
                "exogenous": [False],
            }
        )
        country_fractions = pd.DataFrame(
            {
                "gleam3_category": ["By-products", "By-products"],
                "animal_type": ["ruminant", "ruminant"],
                "country": ["GUF", "GUF"],
                "model_feed_category": ["ruminant_grain", "ruminant_forage"],
                "fraction": [0.5, 0.5],
                "exogenous": [False, False],
            }
        )
        out = _validate_intake_fraction_coverage(
            intakes, global_fractions, country_fractions
        )
        assert out["has_country"].all()
        assert not out["has_global"].any()


# ---------------------------------------------------------------------------
# Tests: compute_fcr_lookup
# ---------------------------------------------------------------------------


class TestComputeFcrLookup:
    """Tests for FCR lookup computation."""

    PRODUCTS: ClassVar[list[str]] = [
        "dairy",
        "meat-cattle",
        "meat-pig",
        "meat-chicken",
        "eggs",
        "dairy-buffalo",
        "meat-sheep",
    ]

    @pytest.fixture()
    def wirsenius_data(self):
        """Minimal Wirsenius data with one region."""
        return pd.DataFrame(
            {
                "animal_product": [
                    "dairy",
                    "dairy",
                    "dairy",
                    "meat-cattle",
                    "meat-cattle",
                    "meat-pig",
                    "meat-chicken",
                    "eggs",
                ],
                "region": ["R"] * 8,
                "unit": [
                    "NE_l",
                    "NE_m",
                    "NE_g",
                    "NE_m",
                    "NE_g",
                    "ME",
                    "ME",
                    "ME",
                ],
                "value": [
                    5.0,
                    1.0,
                    0.5,
                    100.0,
                    20.0,
                    80.0,
                    50.0,
                    30.0,
                ],
            }
        )

    def test_returns_all_products(self, wirsenius_data):
        """Lookup includes ruminant and monogastric products."""
        lookup = compute_fcr_lookup(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            feed_proxy_map={"dairy-buffalo": "dairy", "meat-sheep": "meat-cattle"},
            products=self.PRODUCTS,
        )
        products_in_lookup = {p for p, _r in lookup}
        assert "dairy" in products_in_lookup
        assert "meat-cattle" in products_in_lookup
        assert "meat-pig" in products_in_lookup
        assert "eggs" in products_in_lookup
        assert "meat-chicken" in products_in_lookup
        assert "dairy-buffalo" in products_in_lookup
        assert "meat-sheep" in products_in_lookup

    def test_unity_carcass_to_retail(self, wirsenius_data):
        """With unity carcass-to-retail, meat-cattle ME = NE_m/k_m + NE_g/k_g."""
        lookup = compute_fcr_lookup(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            feed_proxy_map={},
            products=self.PRODUCTS,
        )
        expected = 100.0 / 0.6 + 20.0 / 0.4
        assert lookup[("meat-cattle", "R")] == pytest.approx(expected)

    def test_proxy_inherits_source_fcr(self, wirsenius_data):
        """Proxy product inherits source's FCR at unity carcass-to-retail."""
        lookup = compute_fcr_lookup(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            feed_proxy_map={"dairy-buffalo": "dairy"},
            products=self.PRODUCTS,
        )
        assert lookup[("dairy-buffalo", "R")] == pytest.approx(lookup[("dairy", "R")])

    def test_all_values_positive(self, wirsenius_data):
        """All FCR values should be positive."""
        lookup = compute_fcr_lookup(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            feed_proxy_map={"dairy-buffalo": "dairy", "meat-sheep": "meat-cattle"},
            products=self.PRODUCTS,
        )
        for key, val in lookup.items():
            assert val > 0, f"Non-positive FCR for {key}: {val}"

    def test_dairy_fcr_lower_than_beef(self, wirsenius_data):
        """Dairy (per kg milk) requires much less energy than beef (per kg carcass)."""
        lookup = compute_fcr_lookup(
            wirsenius_data,
            k_m=0.6,
            k_g=0.4,
            k_l=0.6,
            feed_proxy_map={},
            products=self.PRODUCTS,
        )
        assert lookup[("dairy", "R")] < lookup[("meat-cattle", "R")]
