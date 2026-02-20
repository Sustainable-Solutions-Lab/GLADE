# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM feed baseline preparation."""

from typing import ClassVar

import pandas as pd
import pytest

from workflow.scripts.animal_utils import SPECIES_PRODUCTS
from workflow.scripts.prepare_gleam_feed_baseline import (
    MONOGASTRIC_FEED_MAPPING,
    PRODUCT_COMPOSITION,
    ROUGHAGE_COMPONENT_MAPPING,
    RUMINANT_FEED_MAPPING,
    SYSTEM_PRODUCT_MAP,
    compute_country_shares,
    compute_fcr_lookup,
    compute_product_shares,
    decompose_roughage,
)

# ---------------------------------------------------------------------------
# Tests: constant consistency
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify internal consistency of module-level constants."""

    def test_system_product_map_products_in_species_products(self):
        """Every product in SYSTEM_PRODUCT_MAP appears in SPECIES_PRODUCTS."""
        all_sp_products = {p for prods in SPECIES_PRODUCTS.values() for p in prods}
        for key, products in SYSTEM_PRODUCT_MAP.items():
            for p in products:
                assert p in all_sp_products, (
                    f"Product '{p}' from SYSTEM_PRODUCT_MAP[{key}] "
                    f"not in SPECIES_PRODUCTS"
                )

    def test_system_product_map_species_in_species_products(self):
        """Every species in SYSTEM_PRODUCT_MAP keys is in SPECIES_PRODUCTS."""
        for species, _system in SYSTEM_PRODUCT_MAP:
            assert species in SPECIES_PRODUCTS

    def test_product_composition_covers_ruminant_products(self):
        """All ruminant products have a composition table assignment."""
        ruminant_species = {"Cattle & buffaloes", "Small Ruminants"}
        ruminant_products = set()
        for species, products in SPECIES_PRODUCTS.items():
            if species in ruminant_species:
                ruminant_products.update(products)
        for p in ruminant_products:
            assert (
                p in PRODUCT_COMPOSITION
            ), f"Ruminant product '{p}' missing from PRODUCT_COMPOSITION"

    def test_all_feed_mappings_produce_valid_categories(self):
        """Ruminant and monogastric feed mappings produce prefixed categories."""
        for cat in RUMINANT_FEED_MAPPING.values():
            assert cat.startswith("ruminant_")
        for cat in MONOGASTRIC_FEED_MAPPING.values():
            assert cat.startswith("monogastric_")

    def test_roughage_components_produce_ruminant_categories(self):
        """Roughage component mappings produce ruminant_* categories."""
        for cat in ROUGHAGE_COMPONENT_MAPPING.values():
            assert cat.startswith("ruminant_")


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
            ("dairy", "NAO"): 10.0,  # MJ/kg
            ("meat-cattle", "NAO"): 200.0,  # MJ/kg
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
# Tests: decompose_roughage
# ---------------------------------------------------------------------------


class TestDecomposeRoughage:
    """Tests for roughage decomposition using composition tables."""

    @pytest.fixture()
    def simple_comp(self):
        """Composition table with two roughage components in one region."""
        return pd.DataFrame(
            {"R1": [60.0, 20.0, 10.0, 10.0]},
            index=["Fresh grass", "Hay", "Crop residues", "Grains"],
        )

    def test_zero_roughage(self, simple_comp):
        """Zero roughage returns empty dict and zero leaves."""
        assert decompose_roughage(0.0, "R1", simple_comp) == ({}, 0.0)

    def test_negative_roughage(self, simple_comp):
        """Negative roughage returns empty dict and zero leaves."""
        assert decompose_roughage(-1.0, "R1", simple_comp) == ({}, 0.0)

    def test_basic_decomposition(self, simple_comp):
        """Roughage is split by composition percentages."""
        result, leaves = decompose_roughage(100.0, "R1", simple_comp)
        # Only roughage components used: grass 60%, hay 20%, crop residues 10%
        # Total from roughage components = 90% -> normalized to 100
        assert "ruminant_grassland" in result  # Fresh grass + Hay
        assert "ruminant_roughage" in result  # Crop residues
        # "Grains" is a concentrate component, not in ROUGHAGE_COMPONENT_MAPPING
        assert sum(result.values()) == pytest.approx(100.0)
        # No Leaves in simple_comp -> leaves == 0
        assert leaves == pytest.approx(0.0)

    def test_unknown_region_returns_empty(self, simple_comp):
        """Unknown GLEAM region yields empty decomposition."""
        result, leaves = decompose_roughage(100.0, "UNKNOWN", simple_comp)
        assert result == {}
        assert leaves == pytest.approx(0.0)

    def test_all_categories_are_ruminant(self, simple_comp):
        """Decomposition only produces ruminant_* feed categories."""
        result, _ = decompose_roughage(50.0, "R1", simple_comp)
        for cat in result:
            assert cat.startswith("ruminant_")

    def test_total_preserved(self):
        """Decomposed amounts sum to the input roughage total."""
        comp = pd.DataFrame(
            {"R1": [40.0, 30.0, 5.0, 15.0, 2.0, 3.0]},
            index=[
                "Fresh grass",
                "Hay",
                "Legumes and silage",
                "Crop residues",
                "Sugarcane tops",
                "Leaves",
            ],
        )
        result, _ = decompose_roughage(200.0, "R1", comp)
        assert sum(result.values()) == pytest.approx(200.0)

    def test_leaves_tracked_separately(self):
        """Leaves portion is tracked separately from other roughage."""
        comp = pd.DataFrame(
            {"R1": [40.0, 20.0, 5.0, 10.0, 2.0, 8.0]},
            index=[
                "Fresh grass",
                "Hay",
                "Legumes and silage",
                "Crop residues",
                "Sugarcane tops",
                "Leaves",
            ],
        )
        result, leaves = decompose_roughage(100.0, "R1", comp)
        assert leaves > 0
        # Leaves are included in ruminant_roughage but must be smaller
        assert leaves < result["ruminant_roughage"]
        # Total still preserved
        assert sum(result.values()) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Tests: compute_country_shares
# ---------------------------------------------------------------------------


class TestComputeCountryShares:
    """Tests for country share computation."""

    def test_shares_sum_to_one_per_group(self):
        """Within each (species, OECD/Non-OECD) group, shares sum to 1.0."""
        fao = pd.DataFrame(
            {
                "country": ["USA", "DEU", "IND", "BRA"],
                "product": ["dairy", "dairy", "dairy", "dairy"],
                "production_tonnes": [100, 50, 200, 150],
            }
        )
        oecd = {"USA": True, "DEU": True, "IND": False, "BRA": False}
        shares = compute_country_shares(fao, oecd)
        for (species, region), group in shares.groupby(["species", "region"]):
            assert group["share"].sum() == pytest.approx(
                1.0
            ), f"Shares don't sum to 1 for {species}/{region}"

    def test_single_country_gets_full_share(self):
        """A sole producer in its OECD group gets share 1.0."""
        fao = pd.DataFrame(
            {
                "country": ["USA"],
                "product": ["meat-pig"],
                "production_tonnes": [500],
            }
        )
        shares = compute_country_shares(fao, {"USA": True})
        assert len(shares) == 1
        assert shares.iloc[0]["share"] == pytest.approx(1.0)

    def test_products_aggregated_to_species(self):
        """Multiple products for the same species are summed."""
        fao = pd.DataFrame(
            {
                "country": ["USA", "USA"],
                "product": ["dairy", "meat-cattle"],
                "production_tonnes": [1000, 500],
            }
        )
        shares = compute_country_shares(fao, {"USA": True})
        # Both map to "Cattle & buffaloes" -> single species row
        cattle_rows = shares[shares["species"] == "Cattle & buffaloes"]
        assert len(cattle_rows) == 1


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
        # Proxies
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
