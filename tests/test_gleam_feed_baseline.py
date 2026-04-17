# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for GLEAM 3.0 feed baseline preparation."""

from typing import ClassVar

import pandas as pd
import pytest

from workflow.scripts.prepare_feed_baseline import (
    RUMINANT_ANIMALS,
    _build_item_to_product,
    _compute_all_product_shares,
    _flatten_system_product_map,
    _validate_fraction_table,
    _validate_intake_fraction_coverage,
    compute_fcr_lookup,
)

# ---------------------------------------------------------------------------
# Fixture: default config system product map
# ---------------------------------------------------------------------------

# Mirrors config/default.yaml animal_products.gleam3_system_product_map
_DEFAULT_SYSTEM_PRODUCT_MAP_NESTED = {
    "Cattle": {
        "Grassland": ["dairy", "meat-cattle"],
        "Mixed": ["dairy", "meat-cattle"],
        "Feedlots": ["meat-cattle"],
    },
    "Buffalo": {
        "Grassland": ["dairy-buffalo", "meat-cattle"],
        "Mixed": ["dairy-buffalo", "meat-cattle"],
    },
    "Sheep": {"Grassland": ["dairy", "meat-sheep"], "Mixed": ["dairy", "meat-sheep"]},
    "Goats": {"Grassland": ["dairy", "meat-sheep"], "Mixed": ["dairy", "meat-sheep"]},
    "Chicken": {
        "Broiler": ["meat-chicken"],
        "Layer": ["eggs"],
        "Backyard": ["eggs", "meat-chicken"],
    },
    "Pigs": {
        "Backyard": ["meat-pig"],
        "Intermediate": ["meat-pig"],
        "Industrial": ["meat-pig"],
    },
}

_DEFAULT_SYSTEM_PRODUCT_MAP = _flatten_system_product_map(
    _DEFAULT_SYSTEM_PRODUCT_MAP_NESTED
)

# ---------------------------------------------------------------------------
# Tests: _flatten_system_product_map and _build_item_to_product
# ---------------------------------------------------------------------------


class TestSystemProductMap:
    """Tests for config-derived system product map helpers."""

    def test_flatten_round_trips(self):
        """Flattened map has (Animal, LPS) tuple keys."""
        flat = _DEFAULT_SYSTEM_PRODUCT_MAP
        assert ("Cattle", "Grassland") in flat
        assert flat[("Cattle", "Feedlots")] == ["meat-cattle"]

    def test_all_included_products_mapped(self):
        """Every product from the default include list appears in the map."""
        include = [
            "meat-cattle",
            "meat-pig",
            "meat-chicken",
            "dairy",
            "eggs",
            "dairy-buffalo",
            "meat-sheep",
        ]
        mapped = {p for prods in _DEFAULT_SYSTEM_PRODUCT_MAP.values() for p in prods}
        for p in include:
            assert p in mapped, f"Product '{p}' not in system product map"

    def test_feedlots_single_product(self):
        """Feedlots should map to a single product (meat-cattle)."""
        assert _DEFAULT_SYSTEM_PRODUCT_MAP[("Cattle", "Feedlots")] == ["meat-cattle"]

    def test_sheep_goat_systems_include_dairy(self):
        """Sheep and goat systems should include dairy product (proxy)."""
        for animal in ("Sheep", "Goats"):
            for lps in ("Grassland", "Mixed"):
                products = _DEFAULT_SYSTEM_PRODUCT_MAP[(animal, lps)]
                assert (
                    "dairy" in products
                ), f"({animal}, {lps}) should include 'dairy' product"
                assert "meat-sheep" in products

    def test_build_item_to_product(self):
        """Derived item-to-product mapping matches expected values."""
        itp = _build_item_to_product(_DEFAULT_SYSTEM_PRODUCT_MAP)
        assert itp[("Cattle", "Milk")] == "dairy"
        assert itp[("Cattle", "Meat")] == "meat-cattle"
        assert itp[("Buffalo", "Milk")] == "dairy-buffalo"
        assert itp[("Sheep", "Milk")] == "dairy"
        assert itp[("Goats", "Milk")] == "dairy"
        assert itp[("Sheep", "Meat")] == "meat-sheep"
        assert itp[("Goats", "Meat")] == "meat-sheep"
        assert itp[("Chicken", "Eggs")] == "eggs"

    def test_ruminant_animals_in_map(self):
        """Every RUMINANT_ANIMALS member appears as an animal in the map."""
        animals_in_map = {a for a, _lps in _DEFAULT_SYSTEM_PRODUCT_MAP}
        for animal in RUMINANT_ANIMALS:
            assert animal in animals_in_map


# ---------------------------------------------------------------------------
# Tests: _compute_all_product_shares
# ---------------------------------------------------------------------------

# Minimal GLEAM3 production DataFrame schema:
#   ISO3, Animal, LPS, Item, Element, Total
_EMPTY_GLEAM3_PROD = pd.DataFrame(
    columns=["ISO3", "Animal", "LPS", "Item", "Element", "Total"]
)
_EMPTY_FAO = pd.DataFrame(columns=["country", "product", "production_tonnes"])


def _get_share(result: pd.DataFrame, country: str, product: str) -> float:
    """Extract a single product_share from _compute_all_product_shares output."""
    row = result[(result["ISO3"] == country) & (result["product"] == product)]
    assert len(row) == 1, f"Expected 1 row for {country}/{product}, got {len(row)}"
    return float(row["product_share"].iloc[0])


class TestComputeAllProductShares:
    """Tests for vectorized FCR-weighted product share computation."""

    def test_gleam3_based_shares(self):
        """Shares use GLEAM3 production * FCR when GLEAM3 data is available."""
        systems = {("Cattle", "Grassland"): ["dairy", "meat-cattle"]}
        gleam3_prod = pd.DataFrame(
            {
                "ISO3": ["USA", "USA"],
                "Animal": ["Cattle", "Cattle"],
                "LPS": ["Grassland", "Grassland"],
                "Item": ["Milk", "Meat"],
                "Element": ["Weight", "CarcassWeight"],
                "Total": [5000.0, 1000.0],  # tonnes
            }
        )
        fcr_lookup = {("dairy", "USA"): 10.0, ("meat-cattle", "USA"): 250.0}
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
        }

        result = _compute_all_product_shares(
            systems, ["USA"], gleam3_prod, _EMPTY_FAO, fcr_lookup, item_to_product
        )
        # dairy: 5000 * 10 = 50000; cattle: 1000 * 250 = 250000
        assert _get_share(result, "USA", "dairy") == pytest.approx(50000 / 300000)
        assert _get_share(result, "USA", "meat-cattle") == pytest.approx(
            250000 / 300000
        )

    def test_faostat_fallback_when_no_gleam3(self):
        """Falls back to FAOSTAT production * FCR when GLEAM3 data is missing."""
        systems = {("Cattle", "Grassland"): ["dairy", "meat-cattle"]}
        fao = pd.DataFrame(
            {
                "country": ["USA", "USA"],
                "product": ["dairy", "meat-cattle"],
                "production_tonnes": [1000.0, 500.0],
            }
        )
        fcr_lookup = {("dairy", "USA"): 10.0, ("meat-cattle", "USA"): 200.0}
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
        }

        result = _compute_all_product_shares(
            systems, ["USA"], _EMPTY_GLEAM3_PROD, fao, fcr_lookup, item_to_product
        )
        # dairy: 1000 * 10 = 10000; cattle: 500 * 200 = 100000
        assert _get_share(result, "USA", "dairy") == pytest.approx(10000 / 110000)
        assert _get_share(result, "USA", "meat-cattle") == pytest.approx(
            100000 / 110000
        )

    def test_equal_shares_when_no_data(self):
        """Falls back to equal shares when neither GLEAM3 nor FAOSTAT has data."""
        systems = {("Cattle", "Grassland"): ["dairy", "meat-cattle"]}
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
        }

        result = _compute_all_product_shares(
            systems, ["USA"], _EMPTY_GLEAM3_PROD, _EMPTY_FAO, {}, item_to_product
        )
        assert _get_share(result, "USA", "dairy") == pytest.approx(0.5)
        assert _get_share(result, "USA", "meat-cattle") == pytest.approx(0.5)

    def test_shares_sum_to_one_per_group(self):
        """Shares always sum to 1.0 per (Animal, LPS, country) group."""
        systems = {
            ("Cattle", "Grassland"): ["dairy", "meat-cattle"],
            ("Chicken", "Backyard"): ["eggs", "meat-chicken"],
        }
        gleam3_prod = pd.DataFrame(
            {
                "ISO3": ["USA", "USA", "BRA", "BRA"],
                "Animal": ["Cattle", "Cattle", "Cattle", "Cattle"],
                "LPS": ["Grassland"] * 4,
                "Item": ["Milk", "Meat", "Milk", "Meat"],
                "Element": ["Weight", "CarcassWeight", "Weight", "CarcassWeight"],
                "Total": [3000.0, 800.0, 5000.0, 1200.0],
            }
        )
        fcr_lookup = {
            ("dairy", "USA"): 10.0,
            ("meat-cattle", "USA"): 250.0,
            ("dairy", "BRA"): 10.0,
            ("meat-cattle", "BRA"): 250.0,
            ("eggs", "USA"): 30.0,
            ("meat-chicken", "USA"): 50.0,
            ("eggs", "BRA"): 30.0,
            ("meat-chicken", "BRA"): 50.0,
        }
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
            ("Chicken", "Eggs"): "eggs",
            ("Chicken", "Meat"): "meat-chicken",
        }

        result = _compute_all_product_shares(
            systems,
            ["USA", "BRA"],
            gleam3_prod,
            _EMPTY_FAO,
            fcr_lookup,
            item_to_product,
        )
        for (animal, lps), _ in systems.items():
            for country in ["USA", "BRA"]:
                group = result[
                    (result["Animal"] == animal)
                    & (result["LPS"] == lps)
                    & (result["ISO3"] == country)
                ]
                assert group["product_share"].sum() == pytest.approx(
                    1.0
                ), f"Shares don't sum to 1.0 for {animal}/{lps}/{country}"

    def test_multiple_countries(self):
        """Each country gets independent shares based on its own production."""
        systems = {("Cattle", "Grassland"): ["dairy", "meat-cattle"]}
        gleam3_prod = pd.DataFrame(
            {
                "ISO3": ["USA", "USA", "IND", "IND"],
                "Animal": ["Cattle"] * 4,
                "LPS": ["Grassland"] * 4,
                "Item": ["Milk", "Meat", "Milk", "Meat"],
                "Element": ["Weight", "CarcassWeight", "Weight", "CarcassWeight"],
                # USA: mostly meat; IND: mostly milk
                "Total": [100.0, 5000.0, 8000.0, 100.0],
            }
        )
        fcr_lookup = {
            ("dairy", "USA"): 10.0,
            ("meat-cattle", "USA"): 250.0,
            ("dairy", "IND"): 10.0,
            ("meat-cattle", "IND"): 250.0,
        }
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
        }

        result = _compute_all_product_shares(
            systems,
            ["USA", "IND"],
            gleam3_prod,
            _EMPTY_FAO,
            fcr_lookup,
            item_to_product,
        )
        # USA: meat-heavy → meat-cattle share should dominate
        assert _get_share(result, "USA", "meat-cattle") > 0.9
        # IND: milk-heavy → dairy share should dominate
        assert _get_share(result, "IND", "dairy") > 0.7

    def test_output_columns(self):
        """Output has exactly the expected columns."""
        systems = {("Cattle", "Grassland"): ["dairy", "meat-cattle"]}
        item_to_product = {
            ("Cattle", "Milk"): "dairy",
            ("Cattle", "Meat"): "meat-cattle",
        }

        result = _compute_all_product_shares(
            systems, ["USA"], _EMPTY_GLEAM3_PROD, _EMPTY_FAO, {}, item_to_product
        )
        assert set(result.columns) == {
            "Animal",
            "LPS",
            "ISO3",
            "product",
            "product_share",
        }
        assert not result["product_share"].isna().any()


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
    """Tests for FCR lookup computation from GLEAM3 ME requirements CSV."""

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
    def me_requirements_csv(self, tmp_path):
        """Create a minimal GLEAM3 ME requirements CSV."""
        csv_content = (
            "animal_product,country,ME_MJ_per_kg\n"
            "dairy,USA,10.0\n"
            "meat-cattle,USA,250.0\n"
            "meat-pig,USA,80.0\n"
            "meat-chicken,USA,50.0\n"
            "eggs,USA,30.0\n"
            "dairy-buffalo,USA,12.0\n"
            "meat-sheep,USA,200.0\n"
        )
        path = tmp_path / "me_requirements.csv"
        path.write_text(csv_content)
        return str(path)

    def test_returns_all_products(self, me_requirements_csv):
        """Lookup includes all products from the CSV."""
        lookup = compute_fcr_lookup(me_requirements_csv, self.PRODUCTS)
        products_in_lookup = {p for p, _c in lookup}
        for p in self.PRODUCTS:
            assert p in products_in_lookup, f"Product '{p}' missing from lookup"

    def test_values_match_csv(self, me_requirements_csv):
        """Values match what's in the CSV."""
        lookup = compute_fcr_lookup(me_requirements_csv, self.PRODUCTS)
        assert lookup[("dairy", "USA")] == pytest.approx(10.0)
        assert lookup[("meat-cattle", "USA")] == pytest.approx(250.0)
        assert lookup[("meat-pig", "USA")] == pytest.approx(80.0)

    def test_all_values_positive(self, me_requirements_csv):
        """All FCR values should be positive."""
        lookup = compute_fcr_lookup(me_requirements_csv, self.PRODUCTS)
        for key, val in lookup.items():
            assert val > 0, f"Non-positive FCR for {key}: {val}"

    def test_dairy_fcr_lower_than_beef(self, me_requirements_csv):
        """Dairy (per kg milk) requires much less energy than beef (per kg carcass)."""
        lookup = compute_fcr_lookup(me_requirements_csv, self.PRODUCTS)
        assert lookup[("dairy", "USA")] < lookup[("meat-cattle", "USA")]

    def test_filters_to_requested_products(self, me_requirements_csv):
        """Only requested products appear in lookup."""
        lookup = compute_fcr_lookup(me_requirements_csv, ["dairy", "meat-cattle"])
        products_in_lookup = {p for p, _c in lookup}
        assert products_in_lookup == {"dairy", "meat-cattle"}
