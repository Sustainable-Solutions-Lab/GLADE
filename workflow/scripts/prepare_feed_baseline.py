"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Prepare GLEAM 3.0 feed baseline estimates by country and product.

Uses country-level feed intake data from FAO's GLEAM 3.0 model (229 countries,
reference year 2015) combined with pre-computed feed fractions to produce
model-ready feed baselines. Splits multi-product systems using FCR-weighted
shares from GLEAM3-derived ME requirements, and scales to the configured
reference year using FAOSTAT production data.

Output: CSV with columns (country, product, feed_category, feed_use_mt_dm,
exogenous_mt_dm).  The exogenous_mt_dm column tracks feed demand that the
model cannot produce endogenously (synthetic amino acids, minerals, etc.).
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.animal_utils import load_faostat_qcl
from workflow.scripts.faostat_bulk import filter_bulk
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

RUMINANT_ANIMALS = {"Cattle", "Buffalo", "Sheep", "Goats"}

# GLEAM3 production Item → product-type tag, used to match GLEAM3 production
# rows to model products when computing FCR-weighted shares in multi-product
# systems.  The tag is matched against the model products for each animal
# (from the config-provided system product map).
_GLEAM3_ITEM_TYPE = {
    "Meat": "meat",
    "Milk": "dairy",
    "Eggs": "eggs",
}


def _flatten_system_product_map(
    nested: dict[str, dict[str, list[str]]],
) -> dict[tuple[str, str], list[str]]:
    """Convert nested config {Animal: {LPS: [products]}} to flat {(Animal, LPS): [products]}."""
    return {
        (animal, lps): products
        for animal, systems in nested.items()
        for lps, products in systems.items()
    }


def _build_item_to_product(
    system_product_map: dict[tuple[str, str], list[str]],
) -> dict[tuple[str, str], str]:
    """Derive (Animal, GLEAM3-Item) → model product mapping from the system product map.

    For each animal, collects the unique products across all its LPS systems,
    then matches GLEAM3 production items (Meat/Milk/Eggs) to model products
    by type prefix (meat-*, dairy*, eggs).
    """
    # Collect unique products per animal
    animal_products: dict[str, set[str]] = {}
    for (animal, _lps), products in system_product_map.items():
        animal_products.setdefault(animal, set()).update(products)

    mapping: dict[tuple[str, str], str] = {}
    for animal, products in animal_products.items():
        for item, tag in _GLEAM3_ITEM_TYPE.items():
            matches = [p for p in products if p.startswith(tag)]
            if len(matches) == 1:
                mapping[(animal, item)] = matches[0]
            elif len(matches) > 1:
                raise ValueError(
                    f"Ambiguous GLEAM3 item mapping: {animal}/{item} matches "
                    f"multiple products {matches}"
                )
    return mapping


def load_fao_production(
    bulk: pd.DataFrame,
    item_map: dict[str, str],
    year: int,
    countries: list[str],
    faostat_items: dict[str, list[str]],
) -> pd.DataFrame:
    """Load FAO production data at model product level.

    Returns DataFrame with columns: country, product, production_tonnes.
    """
    all_records = []
    for product, fao_items in faostat_items.items():
        item_codes = []
        for item_name in fao_items:
            if item_name in item_map:
                item_codes.append(str(item_map[item_name]))
            else:
                logger.warning(
                    "FAOSTAT item '%s' not found for product '%s'",
                    item_name,
                    product,
                )

        if not item_codes:
            logger.warning("No FAOSTAT items found for product '%s'", product)
            continue

        df = filter_bulk(
            bulk,
            element_codes=["5510"],
            item_codes=item_codes,
            years=[year],
            iso3_codes=countries,
        )
        df = df.dropna(subset=["Value"])

        if df.empty:
            logger.warning(
                "No FAO production data for product '%s' in year %d",
                product,
                year,
            )
            continue

        country_prod = (
            df.groupby("iso3")["Value"]
            .sum()
            .reset_index()
            .rename(columns={"iso3": "country", "Value": "production_tonnes"})
        )
        country_prod["product"] = product
        all_records.append(country_prod)

    if not all_records:
        raise RuntimeError(f"No FAO production data loaded for year {year}")

    return pd.concat(all_records, ignore_index=True)


def compute_fcr_lookup(
    me_requirements_path: str,
    products: list[str],
) -> dict[tuple[str, str], float]:
    """Load ME requirements from GLEAM3-derived CSV.

    Returns dict mapping (product, country) to ME MJ/kg at
    carcass/farm-gate level (no retail conversion).
    """
    me_df = pd.read_csv(me_requirements_path, comment="#")
    me_df = me_df[me_df["animal_product"].isin(products)]
    return {
        (row["animal_product"], row["country"]): row["ME_MJ_per_kg"]
        for _, row in me_df.iterrows()
    }


def compute_product_shares(
    products: list[str],
    country: str,
    fao_prod: pd.DataFrame,
    fcr_lookup: dict[tuple[str, str], float],
) -> dict[str, float]:
    """Compute FCR-weighted product shares for splitting feed within a system.

    share_i = prod_i * FCR_i / sum(prod_j * FCR_j) per country,
    where FCR is total ME per kg product at carcass/farm-gate level.

    Falls back to equal shares if no production data.
    """
    if len(products) == 1:
        return {products[0]: 1.0}

    country_fao = fao_prod[fao_prod["country"] == country]

    weighted = {}
    for p in products:
        prod_match = country_fao.loc[country_fao["product"] == p, "production_tonnes"]
        prod = prod_match.sum() if not prod_match.empty else 0.0
        fcr = fcr_lookup.get((p, country), 0.0)
        weighted[p] = prod * fcr

    total = sum(weighted.values())
    if total <= 0:
        return {p: 1.0 / len(products) for p in products}

    return {p: w / total for p, w in weighted.items()}


def _compute_product_shares_gleam3(
    products: list[str],
    country: str,
    animal: str,
    lps: str,
    gleam3_prod: pd.DataFrame,
    fao_prod: pd.DataFrame,
    fcr_lookup: dict[tuple[str, str], float],
    item_to_product: dict[tuple[str, str], str],
) -> dict[str, float]:
    """Compute product shares using GLEAM3 production data, with FAOSTAT fallback.

    For multi-product systems (e.g. Cattle Grassland -> dairy + meat-cattle),
    uses GLEAM3 per-LPS production to compute FCR-weighted shares.
    Falls back to FAOSTAT-based shares if GLEAM3 data is missing.
    """
    if len(products) == 1:
        return {products[0]: 1.0}

    # Build reverse lookup: model product → GLEAM3 item for this animal
    product_to_item = {
        prod: item
        for (a, item), prod in item_to_product.items()
        if a == animal and prod in products
    }

    # Try GLEAM3 production data first
    lps_prod = gleam3_prod[
        (gleam3_prod["ISO3"] == country)
        & (gleam3_prod["Animal"] == animal)
        & (gleam3_prod["LPS"] == lps)
    ]

    weighted = {}
    for p in products:
        prod_val = 0.0
        g_item = product_to_item.get(p)
        if g_item is not None:
            match = lps_prod[
                (lps_prod["Item"] == g_item)
                & (lps_prod["Element"].isin(["CarcassWeight", "Weight"]))
            ]
            if not match.empty:
                prod_val = match["Total"].sum()

        fcr = fcr_lookup.get((p, country), 0.0)
        weighted[p] = prod_val * fcr

    total = sum(weighted.values())
    if total > 0:
        return {p: w / total for p, w in weighted.items()}

    # Fallback to FAOSTAT-based shares
    return compute_product_shares(products, country, fao_prod, fcr_lookup)


def _validate_fraction_table(fractions: pd.DataFrame) -> None:
    """Validate feed-fraction mappings before applying them."""
    required_columns = {
        "gleam3_category",
        "animal_type",
        "country",
        "model_feed_category",
        "fraction",
        "exogenous",
    }
    missing_cols = required_columns.difference(fractions.columns)
    if missing_cols:
        raise ValueError(
            "Feed fractions file is missing required columns: "
            + ", ".join(sorted(missing_cols))
        )

    key_cols = ["gleam3_category", "animal_type", "country", "model_feed_category"]
    duplicate_mask = fractions.duplicated(subset=key_cols, keep=False)
    if duplicate_mask.any():
        dupes = fractions.loc[duplicate_mask, [*key_cols, "fraction"]].sort_values(
            key_cols
        )
        raise ValueError(
            "Duplicate feed-fraction rows found for the same key:\n"
            + dupes.head(20).to_string(index=False)
        )

    if fractions["fraction"].isna().any():
        bad = fractions[fractions["fraction"].isna()].head(20)
        raise ValueError(
            "Feed fractions contain NaN values:\n" + bad.to_string(index=False)
        )

    if (fractions["fraction"] < 0).any():
        bad = fractions[fractions["fraction"] < 0].head(20)
        raise ValueError(
            "Feed fractions contain negative values:\n" + bad.to_string(index=False)
        )

    sums = fractions.groupby(
        ["gleam3_category", "animal_type", "country"], as_index=False
    )["fraction"].sum()
    bad_sums = sums[sums["fraction"].sub(1.0).abs() > 1e-6]
    if not bad_sums.empty:
        raise ValueError(
            "Feed fractions must sum to 1.0 per "
            "(gleam3_category, animal_type, country). Bad groups:\n"
            + bad_sums.head(20).to_string(index=False)
        )


def _validate_intake_fraction_coverage(
    intakes: pd.DataFrame,
    global_fractions: pd.DataFrame,
    country_fractions: pd.DataFrame,
) -> pd.DataFrame:
    """Validate and annotate which fraction source applies to each intake key."""
    global_keys = global_fractions[["gleam3_category", "animal_type"]].drop_duplicates()
    country_keys = country_fractions[
        ["country", "gleam3_category", "animal_type"]
    ].drop_duplicates()
    country_keys = country_keys.rename(columns={"country": "ISO3"})

    intake_keys = intakes[["ISO3", "feed_category", "animal_type"]].drop_duplicates()
    key_totals = (
        intakes.groupby(["ISO3", "feed_category", "animal_type"], as_index=False)[
            "intake_mt"
        ]
        .sum()
        .rename(columns={"intake_mt": "total_intake_mt"})
    )

    key_coverage = intake_keys.merge(
        global_keys,
        left_on=["feed_category", "animal_type"],
        right_on=["gleam3_category", "animal_type"],
        how="left",
        indicator="global_match",
    )
    key_coverage["has_global"] = key_coverage["global_match"] == "both"
    key_coverage = key_coverage.drop(columns=["gleam3_category", "global_match"])
    key_coverage = key_coverage.merge(
        country_keys,
        left_on=["ISO3", "feed_category", "animal_type"],
        right_on=["ISO3", "gleam3_category", "animal_type"],
        how="left",
        indicator="country_match",
    )
    key_coverage["has_country"] = key_coverage["country_match"] == "both"
    key_coverage = key_coverage.drop(columns=["gleam3_category", "country_match"])

    ambiguous = key_coverage[key_coverage["has_global"] & key_coverage["has_country"]]
    if not ambiguous.empty:
        ambiguous = ambiguous.merge(
            key_totals, on=["ISO3", "feed_category", "animal_type"], how="left"
        )
        raise ValueError(
            "Feed-fraction mapping is ambiguous for some intake keys "
            "(both global and country-specific mappings exist):\n"
            + ambiguous.head(20).to_string(index=False)
        )

    missing = key_coverage[~key_coverage["has_global"] & ~key_coverage["has_country"]]
    if not missing.empty:
        missing = missing.merge(
            key_totals, on=["ISO3", "feed_category", "animal_type"], how="left"
        )
        raise ValueError(
            "Missing feed-fraction mapping for intake keys. "
            "This would drop input data:\n" + missing.head(20).to_string(index=False)
        )

    return intakes.merge(
        key_coverage,
        on=["ISO3", "feed_category", "animal_type"],
        how="left",
    )


def main() -> None:
    gleam3_intakes_path = snakemake.input.gleam3_intakes  # type: ignore[name-defined]
    gleam3_production_path = snakemake.input.gleam3_production  # type: ignore[name-defined]
    gleam3_feed_fractions_path = snakemake.input.gleam3_feed_fractions  # type: ignore[name-defined]
    me_requirements_path = snakemake.input.me_requirements  # type: ignore[name-defined]
    qcl_csv_path = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes_path = snakemake.input.m49_codes  # type: ignore[name-defined]
    ruminant_mapping_path = snakemake.input.ruminant_feed_mapping  # type: ignore[name-defined]
    monogastric_mapping_path = snakemake.input.monogastric_feed_mapping  # type: ignore[name-defined]
    feed_efficiencies_path = snakemake.input.feed_to_animal_products  # type: ignore[name-defined]
    faostat_production_path = snakemake.input.faostat_animal_production  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    reference_year = int(snakemake.params.reference_year)  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    faostat_items: dict[str, list[str]] = dict(snakemake.params.faostat_items)  # type: ignore[name-defined]
    system_product_map = _flatten_system_product_map(
        dict(snakemake.params.gleam3_system_product_map)  # type: ignore[name-defined]
    )
    item_to_product = _build_item_to_product(system_product_map)

    # -- Phase 1: Load GLEAM3 data ----------------------------------------
    logger.info("Loading GLEAM3 feed intake data")
    intakes = pd.read_csv(gleam3_intakes_path, comment="#")
    # Filter to config countries and convert kg DM/year → Mt DM
    intakes = intakes[intakes["ISO3"].isin(countries)].copy()
    intakes["intake_mt"] = intakes["DM.intake"] * 1e-9

    logger.info("Loading GLEAM3 production data")
    gleam3_prod = pd.read_csv(gleam3_production_path, comment="#")
    # Keep CarcassWeight for Meat, Weight for Milk/Eggs; convert kg → tonnes
    gleam3_prod = gleam3_prod[
        gleam3_prod["Element"].isin(["CarcassWeight", "Weight"])
    ].copy()
    gleam3_prod = gleam3_prod[
        ~((gleam3_prod["Item"] == "Meat") & (gleam3_prod["Element"] == "Weight"))
    ]
    gleam3_prod["Total"] = gleam3_prod["Total"] / 1e3  # kg → tonnes

    logger.info("Loading feed fractions")
    fractions = pd.read_csv(gleam3_feed_fractions_path)
    _validate_fraction_table(fractions)

    logger.info("Loading feed category mappings")
    rum_mapping = pd.read_csv(ruminant_mapping_path, comment="#")
    mono_mapping = pd.read_csv(monogastric_mapping_path, comment="#")
    rum_item_to_cat = dict(zip(rum_mapping["feed_item"], rum_mapping["category"]))
    mono_item_to_cat = dict(zip(mono_mapping["feed_item"], mono_mapping["category"]))

    # Load FAOSTAT for product shares and reference year scaling
    bulk, item_map = load_faostat_qcl(qcl_csv_path, m49_codes_path)
    logger.info("Loading FAO 2015 production for product shares")
    fao_2015 = load_fao_production(bulk, item_map, 2015, countries, faostat_items)

    # -- Phase 2: Compute FCR lookup and product shares -------------------
    logger.info("Computing FCR lookup from GLEAM3 ME requirements")
    fcr_lookup = compute_fcr_lookup(
        me_requirements_path,
        list(faostat_items.keys()),
    )

    # -- Phase 3: Map GLEAM3 intakes → model products ---------------------
    logger.info("Mapping GLEAM3 intakes to model products")

    # Filter to valid (Animal, LPS) combinations
    valid_systems = set(system_product_map.keys())
    intakes["system_key"] = list(zip(intakes["Animal"], intakes["LPS"]))
    intakes = intakes[intakes["system_key"].isin(valid_systems)].copy()
    intakes = intakes[intakes["intake_mt"] > 0]

    # Expand to products via system_product_map
    system_prods = pd.DataFrame(
        [
            {"Animal": a, "LPS": lps, "product": p}
            for (a, lps), prods in system_product_map.items()
            for p in prods
        ]
    )
    intakes = intakes.merge(system_prods, on=["Animal", "LPS"], how="inner")

    # Determine animal type
    is_ruminant = intakes["Animal"].isin(RUMINANT_ANIMALS)
    intakes["animal_type"] = "monogastric"
    intakes.loc[is_ruminant, "animal_type"] = "ruminant"

    # Pre-compute product shares for all multi-product systems
    logger.info("Computing FCR-weighted product shares")
    multi_product_systems = {k: v for k, v in system_product_map.items() if len(v) > 1}
    unique_countries = intakes["ISO3"].unique()

    prod_share_records = []
    for (animal, lps), products in multi_product_systems.items():
        for country in unique_countries:
            p_shares = _compute_product_shares_gleam3(
                products,
                country,
                animal,
                lps,
                gleam3_prod,
                fao_2015,
                fcr_lookup,
                item_to_product,
            )
            for p, s in p_shares.items():
                prod_share_records.append(
                    {
                        "Animal": animal,
                        "LPS": lps,
                        "ISO3": country,
                        "product": p,
                        "product_share": s,
                    }
                )

    if prod_share_records:
        prod_shares_df = pd.DataFrame(prod_share_records)
        intakes = intakes.merge(
            prod_shares_df,
            on=["Animal", "LPS", "ISO3", "product"],
            how="left",
        )
        intakes["product_share"] = intakes["product_share"].fillna(1.0)
    else:
        intakes["product_share"] = 1.0

    # -- Phase 4: Apply feed fractions ------------------------------------
    logger.info("Applying feed fractions to compute model feed categories")

    # Split fractions into global and country-specific
    global_fractions = fractions[fractions["country"] == "_global"].copy()
    country_fractions = fractions[fractions["country"] != "_global"].copy()
    intakes = _validate_intake_fraction_coverage(
        intakes, global_fractions, country_fractions
    )

    # Merge global fractions for keys explicitly assigned global mappings.
    intakes_global = intakes[intakes["has_global"] & ~intakes["has_country"]].merge(
        global_fractions.drop(columns=["country"]),
        left_on=["feed_category", "animal_type"],
        right_on=["gleam3_category", "animal_type"],
        how="inner",
    )

    # Merge country-specific fractions.
    intakes_country = intakes[intakes["has_country"]].merge(
        country_fractions,
        left_on=["feed_category", "animal_type", "ISO3"],
        right_on=["gleam3_category", "animal_type", "country"],
        how="inner",
    )

    # Combine
    frac_cols = [
        "ISO3",
        "Animal",
        "LPS",
        "feed_category",
        "intake_mt",
        "product",
        "animal_type",
        "product_share",
        "model_feed_category",
        "fraction",
        "exogenous",
    ]
    combined = pd.concat(
        [intakes_global[frac_cols], intakes_country[frac_cols]],
        ignore_index=True,
    )
    if combined.empty:
        raise ValueError("No feed baseline records generated after applying fractions.")

    # Compute feed amounts
    combined["feed_use_mt_dm"] = (
        combined["intake_mt"] * combined["product_share"] * combined["fraction"]
    )
    combined["exogenous_mt_dm"] = combined["feed_use_mt_dm"] * combined[
        "exogenous"
    ].astype(float)

    # -- Phase 5: Aggregate by (country, product, feed_category) ----------
    logger.info("Aggregating feed baseline")
    result = (
        combined.groupby(["ISO3", "product", "model_feed_category"], as_index=False)[
            ["feed_use_mt_dm", "exogenous_mt_dm"]
        ]
        .sum()
        .rename(columns={"ISO3": "country", "model_feed_category": "feed_category"})
    )

    global_total_pre_scale = result["feed_use_mt_dm"].sum()
    logger.info(
        "Global feed total before scaling: %.1f Mt DM (%.2f Gt DM)",
        global_total_pre_scale,
        global_total_pre_scale / 1000.0,
    )

    # -- Phase 6: Reference year scaling ----------------------------------
    if reference_year != 2015:
        logger.info("Scaling from 2015 to reference year %d", reference_year)
        fao_ref = load_fao_production(
            bulk, item_map, reference_year, countries, faostat_items
        )

        prod_2015 = (
            fao_2015.groupby(["country", "product"])["production_tonnes"]
            .sum()
            .reset_index()
            .rename(columns={"production_tonnes": "prod_2015"})
        )
        prod_ref = (
            fao_ref.groupby(["country", "product"])["production_tonnes"]
            .sum()
            .reset_index()
            .rename(columns={"production_tonnes": "prod_ref"})
        )

        scale_df = prod_2015.merge(prod_ref, on=["country", "product"], how="left")
        scale_df["prod_ref"] = scale_df["prod_ref"].fillna(0)
        scale_df["scale"] = 1.0
        nonzero = scale_df["prod_2015"] > 0
        scale_df.loc[nonzero, "scale"] = (
            scale_df.loc[nonzero, "prod_ref"] / scale_df.loc[nonzero, "prod_2015"]
        )

        result = result.merge(
            scale_df[["country", "product", "scale"]],
            on=["country", "product"],
            how="left",
        )
        result["scale"] = result["scale"].fillna(1.0)
        result["feed_use_mt_dm"] *= result["scale"]
        result["exogenous_mt_dm"] *= result["scale"]
        result = result.drop(columns=["scale"])
    else:
        logger.info("Reference year is 2015 (GLEAM3 base year); no temporal scaling")

    # -- Phase 7: Production-based scaling --------------------------------
    logger.info("Applying production-based feed scaling")

    feed_eff = pd.read_csv(feed_efficiencies_path)
    faostat_prod = pd.read_csv(faostat_production_path)

    result_with_eff = result.merge(
        feed_eff[["country", "product", "feed_category", "efficiency"]],
        on=["country", "product", "feed_category"],
        how="left",
    )
    result_with_eff["efficiency"] = result_with_eff["efficiency"].fillna(0)
    result_with_eff["implied_prod"] = (
        result_with_eff["feed_use_mt_dm"] * result_with_eff["efficiency"]
    )
    implied = (
        result_with_eff.groupby(["country", "product"])["implied_prod"]
        .sum()
        .reset_index()
    )

    implied = implied.merge(
        faostat_prod[["country", "product", "production_mt"]],
        on=["country", "product"],
        how="left",
    )
    implied["production_mt"] = implied["production_mt"].fillna(0)

    implied["scale_factor"] = 1.0
    zero_prod = implied["production_mt"] == 0
    implied.loc[zero_prod, "scale_factor"] = 0.0
    has_implied = implied["implied_prod"] > 0
    scalable = has_implied & ~zero_prod
    implied.loc[scalable, "scale_factor"] = (
        implied.loc[scalable, "production_mt"] / implied.loc[scalable, "implied_prod"]
    )
    no_feed = ~has_implied & ~zero_prod
    if no_feed.any():
        for _, row in implied[no_feed].iterrows():
            logger.warning(
                "  %s/%s: FAOSTAT production %.3f Mt but no GLEAM feed; skipping",
                row["country"],
                row["product"],
                row["production_mt"],
            )
        implied.loc[no_feed, "scale_factor"] = 1.0

    # Log extreme scale factors
    for _, row in implied[scalable].iterrows():
        sf = row["scale_factor"]
        flag = ""
        if sf > 3.0:
            flag = " [EXTREME HIGH]"
        elif sf < 0.3:
            flag = " [EXTREME LOW]"
        if flag or sf > 2.0 or sf < 0.5:
            logger.info(
                "  %s/%s: scale=%.3f (implied=%.3f Mt, FAOSTAT=%.3f Mt)%s",
                row["country"],
                row["product"],
                sf,
                row["implied_prod"],
                row["production_mt"],
                flag,
            )

    scale_map = implied.set_index(["country", "product"])["scale_factor"]
    result_idx = result.set_index(["country", "product"])
    result_idx["scale_factor"] = scale_map
    result_idx["scale_factor"] = result_idx["scale_factor"].fillna(1.0)
    result_idx["feed_use_mt_dm"] *= result_idx["scale_factor"]
    result_idx["exogenous_mt_dm"] *= result_idx["scale_factor"]
    result = result_idx.drop(columns=["scale_factor"]).reset_index()

    new_global_total = result["feed_use_mt_dm"].sum()
    logger.info(
        "  New global feed total: %.1f Mt DM (%.2f Gt DM)",
        new_global_total,
        new_global_total / 1000.0,
    )

    # -- Phase 8: Expand full index and write output ----------------------
    # Derive product lists from the system product map: ruminant products
    # come from ruminant animals, the rest are monogastric.
    ruminant_products = list(
        dict.fromkeys(
            p
            for (animal, _lps), prods in system_product_map.items()
            if animal in RUMINANT_ANIMALS
            for p in prods
        )
    )
    all_products = list(
        dict.fromkeys(p for prods in system_product_map.values() for p in prods)
    )
    monogastric_products = [p for p in all_products if p not in ruminant_products]

    ruminant_feed_cats = sorted({f"ruminant_{c}" for c in rum_item_to_cat.values()})
    monogastric_feed_cats = sorted(
        {f"monogastric_{c}" for c in mono_item_to_cat.values()}
    )

    product_feed_cats = [
        (p, fc) for p in ruminant_products for fc in ruminant_feed_cats
    ] + [(p, fc) for p in monogastric_products for fc in monogastric_feed_cats]

    full_index = pd.MultiIndex.from_tuples(
        [(c, p, fc) for c in countries for p, fc in product_feed_cats],
        names=["country", "product", "feed_category"],
    )

    result = (
        result.set_index(["country", "product", "feed_category"])[
            ["feed_use_mt_dm", "exogenous_mt_dm"]
        ]
        .reindex(full_index, fill_value=0.0)
        .reset_index()
    )

    result = result.sort_values(["country", "product", "feed_category"])

    # -- Write output --------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)

    # Log summary
    logger.info("Feed baseline summary (Mt DM, reference year %d):", reference_year)
    for product in sorted(result["product"].unique()):
        prod_data = result[result["product"] == product]
        total = prod_data["feed_use_mt_dm"].sum()
        logger.info("  %s: %.1f Mt DM total", product, total)
        for cat in sorted(prod_data["feed_category"].unique()):
            cat_total = prod_data[prod_data["feed_category"] == cat][
                "feed_use_mt_dm"
            ].sum()
            if cat_total > 0:
                logger.info("    %s: %.1f Mt DM", cat, cat_total)

    n_countries = result["country"].nunique()
    n_nonzero = int((result["feed_use_mt_dm"] > 0).sum())
    logger.info(
        "Saved %d records (%d nonzero, %d countries) to %s",
        len(result),
        n_nonzero,
        n_countries,
        output_path,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
