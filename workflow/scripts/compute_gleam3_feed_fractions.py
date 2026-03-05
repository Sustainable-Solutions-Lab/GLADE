"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Compute fractions mapping GLEAM 3.0 aggregate feed categories to model feed
categories, using FAOSTAT crop production and foods.csv byproduct rates.

Most GLEAM3 categories map 1:1 to model categories (constant fractions).
Two categories — By-products and Other edible — require country-varying
fractions estimated from crop production volumes.

Output: CSV with columns (gleam3_category, animal_type, country,
model_feed_category, fraction, exogenous).

Fractions sum to 1.0 within each (gleam3_category, animal_type, country) group.
Constant fractions use country='_global'.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _build_constant_fractions() -> pd.DataFrame:
    """Build the constant (non-country-varying) fraction mappings."""
    rows = [
        ("Grains", "ruminant", "ruminant_grain", 1.0, False),
        ("Grains", "monogastric", "monogastric_grain", 1.0, False),
        ("Oil seed cakes", "ruminant", "ruminant_protein", 1.0, False),
        ("Oil seed cakes", "monogastric", "monogastric_protein", 1.0, False),
        ("Crop residues", "ruminant", "ruminant_roughage", 1.0, False),
        ("Crop residues", "monogastric", "monogastric_low_quality", 1.0, False),
        # Grass and leaves: 100% forage for ruminants; the grassland forage
        # calibration mechanism detects forage shortfall and creates exogenous
        # supply for the leaves/browse residual.
        ("Grass and leaves", "ruminant", "ruminant_forage", 1.0, False),
        ("Grass and leaves", "monogastric", "monogastric_low_quality", 1.0, False),
        ("Fodder crop", "ruminant", "ruminant_forage", 1.0, False),
        # Other non-edible (180 Mt DM, ~14% of monogastric feed): entirely
        # exogenous (synthetic amino acids, minerals, limestone, fishmeal).
        ("Other non-edible", "monogastric", "monogastric_low_quality", 1.0, True),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "gleam3_category",
            "animal_type",
            "model_feed_category",
            "fraction",
            "exogenous",
        ],
    ).assign(country="_global")


def _estimate_byproduct_volumes(
    crop_production: pd.DataFrame,
    foods: pd.DataFrame,
    countries: list[str],
) -> pd.DataFrame:
    """Estimate country-level byproduct volumes from crop production.

    Returns DataFrame with columns: country, byproduct, volume.
    Byproducts: wheat-bran, ddgs, molasses.
    """
    # Extraction rates for bran-type byproducts from foods.csv
    bran_rates = {}
    for food_name in ["wheat-bran", "rice-bran", "barley-bran", "oat-bran"]:
        match = foods[foods["food"] == food_name]
        if not match.empty:
            bran_rates[match.iloc[0]["crop"]] = match.iloc[0]["factor"]

    # DDGS from maize ethanol
    ddgs_match = foods[foods["food"] == "ddgs"]
    ddgs_rate = ddgs_match.iloc[0]["factor"] if not ddgs_match.empty else 0.32

    # Molasses from sugarcane
    molasses_match = foods[foods["food"] == "molasses"]
    molasses_rate = (
        molasses_match.iloc[0]["factor"] if not molasses_match.empty else 0.13
    )

    records = []
    for country in countries:
        group = crop_production[crop_production["country"] == country]
        crop_prod = dict(zip(group["crop"], group["production_tonnes"]))

        # Bran volume: sum of crop * extraction_rate across bran-producing crops
        vol_bran = sum(
            crop_prod.get(crop, 0.0) * rate for crop, rate in bran_rates.items()
        )
        # DDGS from maize
        vol_ddgs = crop_prod.get("maize", 0.0) * ddgs_rate
        # Molasses from sugarcane
        vol_molasses = crop_prod.get("sugarcane", 0.0) * molasses_rate

        records.append({"country": country, "byproduct": "bran", "volume": vol_bran})
        records.append({"country": country, "byproduct": "ddgs", "volume": vol_ddgs})
        records.append(
            {"country": country, "byproduct": "molasses", "volume": vol_molasses}
        )

    return pd.DataFrame(records)


def _compute_byproduct_fractions(
    byproduct_volumes: pd.DataFrame,
    rum_item_to_cat: dict[str, str],
    mono_item_to_cat: dict[str, str],
    countries: list[str],
) -> pd.DataFrame:
    """Compute per-country By-products fractions for ruminants and monogastrics."""
    # Map each byproduct to its model feed category
    # bran (wheat/rice/barley/oat) → ruminant: grain, monogastric: low_quality
    # ddgs → ruminant: forage, monogastric: protein
    # molasses → ruminant: grain, monogastric: grain
    rum_cats = {
        "bran": f"ruminant_{rum_item_to_cat['wheat-bran']}",
        "ddgs": f"ruminant_{rum_item_to_cat['ddgs']}",
        "molasses": f"ruminant_{rum_item_to_cat['molasses']}",
    }
    mono_cats = {
        "bran": f"monogastric_{mono_item_to_cat['wheat-bran']}",
        "ddgs": f"monogastric_{mono_item_to_cat['ddgs']}",
        "molasses": f"monogastric_{mono_item_to_cat['molasses']}",
    }

    all_countries = pd.Index(sorted(set(countries)))
    results = []
    for animal_type, cat_map in [("ruminant", rum_cats), ("monogastric", mono_cats)]:
        # Aggregate volumes per model_feed_category per country
        vol = byproduct_volumes.copy()
        vol["model_feed_category"] = vol["byproduct"].map(cat_map)
        all_categories = pd.Index(sorted(set(vol["model_feed_category"])))
        full_index = pd.MultiIndex.from_product(
            [all_countries, all_categories], names=["country", "model_feed_category"]
        )
        agg = (
            vol.groupby(["country", "model_feed_category"])["volume"]
            .sum()
            .reindex(full_index, fill_value=0.0)
            .reset_index()
        )

        country_totals = agg.groupby("country")["volume"].transform("sum")
        agg["fraction"] = 0.0
        nonzero = country_totals > 0
        agg.loc[nonzero, "fraction"] = (
            agg.loc[nonzero, "volume"] / country_totals[nonzero]
        )

        # Countries with zero totals fall back to global fractions.
        global_totals = agg.groupby("model_feed_category", as_index=False)[
            "volume"
        ].sum()
        global_sum = global_totals["volume"].sum()
        if global_sum > 0:
            global_frac_map = (
                global_totals.set_index("model_feed_category")["volume"] / global_sum
            ).to_dict()
        else:
            uniform = 1.0 / len(all_categories) if len(all_categories) > 0 else 0.0
            global_frac_map = dict.fromkeys(all_categories, uniform)

        zero_countries = agg.loc[~nonzero, "country"].unique()
        if len(zero_countries) > 0:
            zero_mask = agg["country"].isin(zero_countries)
            agg.loc[zero_mask, "fraction"] = agg.loc[
                zero_mask, "model_feed_category"
            ].map(global_frac_map)

        agg["gleam3_category"] = "By-products"
        agg["animal_type"] = animal_type
        agg["exogenous"] = False
        results.append(
            agg[
                [
                    "gleam3_category",
                    "animal_type",
                    "country",
                    "model_feed_category",
                    "fraction",
                    "exogenous",
                ]
            ]
        )

    return pd.concat(results, ignore_index=True)


def _estimate_other_edible_volumes(
    crop_production: pd.DataFrame,
    countries: list[str],
) -> pd.DataFrame:
    """Estimate country-level Other edible component volumes.

    Components: cassava, banana, soybean (whole), dry-pea/cowpea (pulses).
    """
    records = []
    for country in countries:
        group = crop_production[crop_production["country"] == country]
        crop_prod = dict(zip(group["crop"], group["production_tonnes"]))
        records.append(
            {
                "country": country,
                "component": "cassava",
                "volume": crop_prod.get("cassava", 0.0),
            }
        )
        records.append(
            {
                "country": country,
                "component": "banana",
                "volume": crop_prod.get("banana", 0.0),
            }
        )
        records.append(
            {
                "country": country,
                "component": "soybean",
                "volume": crop_prod.get("soybean", 0.0),
            }
        )
        # Pulses: sum dry-pea and cowpea
        pulse_vol = crop_prod.get("dry-pea", 0.0) + crop_prod.get("cowpea", 0.0)
        records.append({"country": country, "component": "pulses", "volume": pulse_vol})
    return pd.DataFrame(records)


def _compute_other_edible_fractions(
    other_edible_volumes: pd.DataFrame,
    mono_item_to_cat: dict[str, str],
    countries: list[str],
) -> pd.DataFrame:
    """Compute per-country Other edible fractions for monogastrics."""
    # Map components to model feed categories
    component_cats = {
        "cassava": f"monogastric_{mono_item_to_cat['cassava']}",
        "banana": f"monogastric_{mono_item_to_cat['banana']}",
        "soybean": f"monogastric_{mono_item_to_cat['soybean']}",
        "pulses": f"monogastric_{mono_item_to_cat['dry-pea']}",
    }

    vol = other_edible_volumes.copy()
    vol["model_feed_category"] = vol["component"].map(component_cats)

    all_countries = pd.Index(sorted(set(countries)))
    all_categories = pd.Index(sorted(set(vol["model_feed_category"])))
    full_index = pd.MultiIndex.from_product(
        [all_countries, all_categories], names=["country", "model_feed_category"]
    )
    agg = (
        vol.groupby(["country", "model_feed_category"])["volume"]
        .sum()
        .reindex(full_index, fill_value=0.0)
        .reset_index()
    )
    agg["exogenous"] = False

    # Normalize to fractions per country.
    country_totals = agg.groupby("country")["volume"].transform("sum")
    agg["fraction"] = 0.0
    nonzero = country_totals > 0
    agg.loc[nonzero, "fraction"] = agg.loc[nonzero, "volume"] / country_totals[nonzero]

    # Countries with zero totals fall back to global fractions.
    global_agg = agg.groupby("model_feed_category", as_index=False)["volume"].sum()
    global_sum = global_agg["volume"].sum()
    if global_sum > 0:
        global_frac_map = (
            global_agg.set_index("model_feed_category")["volume"] / global_sum
        ).to_dict()
    else:
        uniform = 1.0 / len(all_categories) if len(all_categories) > 0 else 0.0
        global_frac_map = dict.fromkeys(all_categories, uniform)

    zero_countries = agg.loc[~nonzero, "country"].unique()
    if len(zero_countries) > 0:
        zero_mask = agg["country"].isin(zero_countries)
        agg.loc[zero_mask, "fraction"] = agg.loc[zero_mask, "model_feed_category"].map(
            global_frac_map
        )

    agg["gleam3_category"] = "Other edible"
    agg["animal_type"] = "monogastric"
    return agg[
        [
            "gleam3_category",
            "animal_type",
            "country",
            "model_feed_category",
            "fraction",
            "exogenous",
        ]
    ]


def main() -> None:
    foods_path = snakemake.input.foods  # type: ignore[name-defined]
    crop_production_path = snakemake.input.faostat_crop_production  # type: ignore[name-defined]
    ruminant_mapping_path = snakemake.input.ruminant_feed_mapping  # type: ignore[name-defined]
    monogastric_mapping_path = snakemake.input.monogastric_feed_mapping  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    # Load inputs
    foods = pd.read_csv(foods_path, comment="#")
    crop_production = pd.read_csv(crop_production_path, comment="#")
    rum_mapping = pd.read_csv(ruminant_mapping_path, comment="#")
    mono_mapping = pd.read_csv(monogastric_mapping_path, comment="#")

    rum_item_to_cat = dict(zip(rum_mapping["feed_item"], rum_mapping["category"]))
    mono_item_to_cat = dict(zip(mono_mapping["feed_item"], mono_mapping["category"]))

    # 1. Constant fractions
    constant = _build_constant_fractions()

    # 2. Country-varying By-products fractions
    logger.info("Computing By-products fractions from crop production volumes")
    bp_volumes = _estimate_byproduct_volumes(crop_production, foods, countries)
    bp_fractions = _compute_byproduct_fractions(
        bp_volumes, rum_item_to_cat, mono_item_to_cat, countries
    )

    # 3. Country-varying Other edible fractions
    logger.info("Computing Other edible fractions from crop production volumes")
    oe_volumes = _estimate_other_edible_volumes(crop_production, countries)
    oe_fractions = _compute_other_edible_fractions(
        oe_volumes, mono_item_to_cat, countries
    )

    # Combine
    result = pd.concat([constant, bp_fractions, oe_fractions], ignore_index=True)

    # Validate: fail fast on malformed fractions.
    duplicate_mask = result.duplicated(
        subset=["gleam3_category", "animal_type", "country", "model_feed_category"],
        keep=False,
    )
    if duplicate_mask.any():
        dupes = result.loc[duplicate_mask].sort_values(
            ["gleam3_category", "animal_type", "country", "model_feed_category"]
        )
        raise ValueError(
            "Duplicate fraction rows detected for the same mapping key:\n"
            + dupes.head(20).to_string(index=False)
        )

    if (result["fraction"] < 0).any():
        bad = result[result["fraction"] < 0].head(20)
        raise ValueError("Negative fractions detected:\n" + bad.to_string(index=False))

    sums = result.groupby(
        ["gleam3_category", "animal_type", "country"], as_index=False
    )["fraction"].sum()
    bad_sums = sums[sums["fraction"].sub(1.0).abs() > 1e-6]
    if not bad_sums.empty:
        raise ValueError(
            "Fractions must sum to 1.0 for each "
            "(gleam3_category, animal_type, country). Bad groups:\n"
            + bad_sums.head(20).to_string(index=False)
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    logger.info("Wrote %d fraction records to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
