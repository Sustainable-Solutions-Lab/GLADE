"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

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

import numpy as np
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

# GLEAM3 roughage intake categories.  When the roughage re-split is enabled,
# these three are pooled per (country, animal, LPS) and redistributed across
# model feed categories using the Mottet (2017) region x species composition,
# instead of GLEAM 3.0's availability-based grass/residue split.
ROUGHAGE_GLEAM3_CATEGORIES = frozenset(
    {"Grass and leaves", "Crop residues", "Fodder crop"}
)

# (GLEAM Animal, model product) -> Mottet species row in roughage_composition.csv.
RUMINANT_SPECIES_MAP = {
    ("Cattle", "dairy"): "dairy_cattle",
    ("Cattle", "meat-cattle"): "beef_cattle",
    ("Buffalo", "dairy-buffalo"): "dairy_buffalo",
    ("Buffalo", "meat-cattle"): "meat_buffalo",
    ("Sheep", "dairy"): "dairy_small_ruminant",
    ("Sheep", "meat-sheep"): "meat_small_ruminant",
    ("Goats", "dairy"): "dairy_small_ruminant",
    ("Goats", "meat-sheep"): "meat_small_ruminant",
}

# Roughage components -> (model feed category, exogenous flag).
_FORAGE_COMPONENTS = ("fresh_grass", "hay", "legumes_silage")
_ROUGHAGE_COMPONENTS = ("crop_residues", "sugarcane_tops")
_BROWSE_COMPONENTS = ("tree_leaves",)
_ALL_COMPONENTS = _FORAGE_COMPONENTS + _ROUGHAGE_COMPONENTS + _BROWSE_COMPONENTS


def _load_roughage_composition(
    path: str,
) -> tuple[dict[tuple[str, str], dict[str, float]], dict[str, dict[str, float]]]:
    """Load the Mottet roughage composition, renormalized within roughage.

    Returns ``(by_region_species, region_default)``; each value maps component
    name -> fraction summing to 1.0 over the six roughage components.
    ``region_default`` is the per-region mean across species, used as a
    fallback when a (region, species) row is absent (a species not present in
    that region).
    """
    df = pd.read_csv(path, comment="#")
    wide = pd.pivot_table(
        df,
        index=["region", "species"],
        columns="component",
        values="share",
        fill_value=0.0,
    ).reindex(columns=list(_ALL_COMPONENTS), fill_value=0.0)

    def _renorm(row: pd.Series) -> dict[str, float] | None:
        total = float(row.sum())
        if total <= 0:
            return None
        return {c: float(row[c]) / total for c in _ALL_COMPONENTS}

    by_rs: dict[tuple[str, str], dict[str, float]] = {}
    for (region, species), row in wide.iterrows():
        shares = _renorm(row)
        if shares is not None:
            by_rs[(region, species)] = shares
    region_default: dict[str, dict[str, float]] = {}
    for region, sub in wide.groupby(level="region"):
        shares = _renorm(sub.mean(axis=0))
        if shares is not None:
            region_default[region] = shares
    return by_rs, region_default


def _apply_roughage_resplit(
    intakes: pd.DataFrame,
    comp_by_rs: dict[tuple[str, str], dict[str, float]],
    region_default: dict[str, dict[str, float]],
    country_region: dict[str, str],
) -> pd.DataFrame:
    """Re-split ruminant roughage intake into model feed categories.

    Pools the GLEAM3 roughage categories per (country, animal, LPS, product)
    and redistributes the pooled total across model feed categories using the
    Mottet region x species composition: forage = fresh grass + hay +
    legumes/silage; roughage = crop residues + sugarcane tops; browse = tree
    leaves (exogenous).  GLEAM 3.0 per-system roughage totals are preserved
    exactly (component fractions sum to 1.0).  Returns rows with the same
    columns the fraction-merge path produces.
    """
    rough = intakes[
        (intakes["animal_type"] == "ruminant")
        & (intakes["feed_category"].isin(ROUGHAGE_GLEAM3_CATEGORIES))
    ]
    if rough.empty:
        return pd.DataFrame()

    # Pool the three roughage categories per (country, animal, LPS, product).
    # intake_mt is the category intake (constant across the products of a
    # system); product_share splits it.  Summing the (<=3) category rows gives
    # the system's total roughage intake for that product row.
    pooled = rough.groupby(
        ["ISO3", "Animal", "LPS", "product", "animal_type"], as_index=False
    ).agg(
        roughage_intake=("intake_mt", "sum"), product_share=("product_share", "first")
    )

    routing = (
        [(c, "ruminant_forage", False) for c in _FORAGE_COMPONENTS]
        + [(c, "ruminant_roughage", False) for c in _ROUGHAGE_COMPONENTS]
        + [(c, "ruminant_roughage", True) for c in _BROWSE_COMPONENTS]
    )

    records = []
    for row in pooled.itertuples(index=False):
        region = country_region.get(row.ISO3)
        if region is None:
            raise ValueError(
                f"Country {row.ISO3!r} missing from country_mottet_region map"
            )
        species = RUMINANT_SPECIES_MAP.get((row.Animal, row.product))
        if species is None:
            raise ValueError(
                f"No Mottet species mapping for ruminant ({row.Animal!r}, "
                f"{row.product!r})"
            )
        comp = comp_by_rs.get((region, species)) or region_default.get(region)
        if comp is None:
            raise ValueError(f"No roughage composition for region {region!r}")
        agg: dict[tuple[str, bool], float] = {}
        for component, model_cat, exo in routing:
            agg[(model_cat, exo)] = agg.get((model_cat, exo), 0.0) + comp[component]
        for (model_cat, exo), frac in agg.items():
            records.append(
                {
                    "ISO3": row.ISO3,
                    "Animal": row.Animal,
                    "LPS": row.LPS,
                    "feed_category": "Roughage (resplit)",
                    "intake_mt": row.roughage_intake,
                    "product": row.product,
                    "animal_type": row.animal_type,
                    "product_share": row.product_share,
                    "model_feed_category": model_cat,
                    "fraction": frac,
                    "exogenous": exo,
                }
            )
    return pd.DataFrame.from_records(records)


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
                item_codes.append(item_map[item_name])
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
            element_codes=[5510],
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
    return dict(
        zip(
            zip(me_df["animal_product"], me_df["country"]),
            me_df["ME_MJ_per_kg"],
        )
    )


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


def _compute_all_product_shares(
    multi_product_systems: dict[tuple[str, str], list[str]],
    unique_countries,
    gleam3_prod: pd.DataFrame,
    fao_2015: pd.DataFrame,
    fcr_lookup: dict[tuple[str, str], float],
    item_to_product: dict[tuple[str, str], str],
) -> pd.DataFrame:
    """Vectorized computation of FCR-weighted product shares for multi-product systems.

    Returns DataFrame with columns: Animal, LPS, ISO3, product, product_share.
    """

    # Build the full cross-product of (animal, lps, country, product)
    expand_rows = []
    for (animal, lps), products in multi_product_systems.items():
        for p in products:
            expand_rows.append({"Animal": animal, "LPS": lps, "product": p})
    expand_df = pd.DataFrame(expand_rows)
    countries_df = pd.DataFrame({"ISO3": unique_countries})
    cross = expand_df.merge(countries_df, how="cross")

    # Build reverse mapping: (animal, product) → GLEAM3 item
    product_to_item = {}
    for (a, item), prod in item_to_product.items():
        product_to_item[(a, prod)] = item

    # --- GLEAM3 production lookup (vectorized) ---
    # Pre-aggregate GLEAM3 production by (ISO3, Animal, LPS, Item)
    g_agg = (
        gleam3_prod.groupby(["ISO3", "Animal", "LPS", "Item"], as_index=False)["Total"]
        .sum()
        .rename(columns={"Total": "gleam3_prod_val"})
    )

    # Map each (animal, product) → GLEAM3 Item
    cross["gleam3_item"] = [
        product_to_item.get((a, p)) for a, p in zip(cross["Animal"], cross["product"])
    ]

    # Merge GLEAM3 production
    cross = cross.merge(
        g_agg,
        left_on=["ISO3", "Animal", "LPS", "gleam3_item"],
        right_on=["ISO3", "Animal", "LPS", "Item"],
        how="left",
    )
    cross["gleam3_prod_val"] = cross["gleam3_prod_val"].astype(float).fillna(0.0)

    # Map FCR values
    cross["fcr"] = [
        fcr_lookup.get((p, c), 0.0) for p, c in zip(cross["product"], cross["ISO3"])
    ]

    # Compute weighted = gleam3_prod_val * fcr
    cross["weighted"] = cross["gleam3_prod_val"] * cross["fcr"]

    # Compute group totals for GLEAM3-based shares
    group_cols = ["Animal", "LPS", "ISO3"]
    group_total = cross.groupby(group_cols, as_index=False)["weighted"].sum()
    group_total = group_total.rename(columns={"weighted": "total_weighted"})
    cross = cross.merge(group_total, on=group_cols, how="left")

    # Where GLEAM3 total > 0, compute share directly
    has_gleam = cross["total_weighted"] > 0
    cross.loc[has_gleam, "product_share"] = (
        cross.loc[has_gleam, "weighted"] / cross.loc[has_gleam, "total_weighted"]
    )

    # --- FAOSTAT fallback for rows where GLEAM3 total == 0 ---
    needs_fallback = cross[~has_gleam]
    if not needs_fallback.empty:
        # Merge FAOSTAT production
        fao_lookup = fao_2015.groupby(["country", "product"], as_index=False)[
            "production_tonnes"
        ].sum()
        fb = needs_fallback[[*group_cols, "product"]].merge(
            fao_lookup,
            left_on=["ISO3", "product"],
            right_on=["country", "product"],
            how="left",
        )
        fb["production_tonnes"] = fb["production_tonnes"].astype(float).fillna(0.0)
        fb["fcr"] = [
            fcr_lookup.get((p, c), 0.0) for p, c in zip(fb["product"], fb["ISO3"])
        ]
        fb["weighted_fao"] = fb["production_tonnes"] * fb["fcr"]
        fb_total = fb.groupby(group_cols, as_index=False)["weighted_fao"].sum()
        fb_total = fb_total.rename(columns={"weighted_fao": "total_fao"})
        fb = fb.merge(fb_total, on=group_cols, how="left")

        has_fao = fb["total_fao"] > 0
        fb["product_share"] = np.nan
        fb.loc[has_fao, "product_share"] = (
            fb.loc[has_fao, "weighted_fao"] / fb.loc[has_fao, "total_fao"]
        )

        # Equal shares where neither source has data
        for (animal, lps), products in multi_product_systems.items():
            equal_share = 1.0 / len(products)
            mask = ~has_fao & (fb["Animal"] == animal) & (fb["LPS"] == lps)
            fb.loc[mask, "product_share"] = equal_share

        # Write fallback shares back
        fallback_idx = cross.index[~has_gleam]
        cross.loc[fallback_idx, "product_share"] = fb["product_share"].values

    return cross[["Animal", "LPS", "ISO3", "product", "product_share"]]


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
    roughage_composition_path = snakemake.input.roughage_composition  # type: ignore[name-defined]
    country_region_path = snakemake.input.country_mottet_region  # type: ignore[name-defined]

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
    # Items can appear in multiple categories with shares (see
    # workflow/scripts/categorize_feeds.py::apply_category_overrides);
    # collect the unique category set per animal_type, not a 1:1 dict.
    rum_categories = set(rum_mapping["category"].unique())
    mono_categories = set(mono_mapping["category"].unique())

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

    if multi_product_systems:
        prod_shares_df = _compute_all_product_shares(
            multi_product_systems,
            intakes["ISO3"].unique(),
            gleam3_prod,
            fao_2015,
            fcr_lookup,
            item_to_product,
        )
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

    # Re-split ruminant roughage via Mottet (2017) composition. The three
    # ruminant roughage GLEAM3 categories are removed from the fraction-merge
    # path (compute_gleam3_feed_fractions emits no rows for them) and replaced
    # by pre-resolved (model_feed_category, fraction, exogenous) rows that
    # preserve the GLEAM 3.0 per-system roughage total.
    comp_by_rs, region_default = _load_roughage_composition(roughage_composition_path)
    country_region = (
        pd.read_csv(country_region_path, comment="#")
        .set_index("country")["mottet_region"]
        .to_dict()
    )
    missing_regions = sorted(set(countries) - set(country_region))
    if missing_regions:
        raise ValueError(
            "country_mottet_region map missing countries: " + ", ".join(missing_regions)
        )
    resplit_rows = _apply_roughage_resplit(
        intakes, comp_by_rs, region_default, country_region
    )
    intakes = intakes[
        ~(
            (intakes["animal_type"] == "ruminant")
            & (intakes["feed_category"].isin(ROUGHAGE_GLEAM3_CATEGORIES))
        )
    ].copy()
    logger.info(
        "Roughage re-split: %d rows replacing pooled ruminant roughage",
        len(resplit_rows),
    )

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
    frames = [intakes_global[frac_cols], intakes_country[frac_cols]]
    if not resplit_rows.empty:
        frames.append(resplit_rows[frac_cols])
    combined = pd.concat(frames, ignore_index=True)
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
        faostat_prod[["country", "product", "production_mt_fresh_retail"]],
        on=["country", "product"],
        how="left",
    )
    implied["production_mt_fresh_retail"] = implied[
        "production_mt_fresh_retail"
    ].fillna(0)

    implied["scale_factor"] = 1.0
    zero_prod = implied["production_mt_fresh_retail"] == 0
    implied.loc[zero_prod, "scale_factor"] = 0.0
    has_implied = implied["implied_prod"] > 0
    scalable = has_implied & ~zero_prod
    implied.loc[scalable, "scale_factor"] = (
        implied.loc[scalable, "production_mt_fresh_retail"]
        / implied.loc[scalable, "implied_prod"]
    )
    no_feed = ~has_implied & ~zero_prod
    if no_feed.any():
        for c, p, pm in zip(
            implied.loc[no_feed, "country"],
            implied.loc[no_feed, "product"],
            implied.loc[no_feed, "production_mt_fresh_retail"],
        ):
            logger.warning(
                "  %s/%s: FAOSTAT production %.3f Mt but no GLEAM feed; skipping",
                c,
                p,
                pm,
            )
        implied.loc[no_feed, "scale_factor"] = 1.0

    # Log extreme scale factors
    notable = implied[
        scalable & ((implied["scale_factor"] > 2.0) | (implied["scale_factor"] < 0.5))
    ]
    for c, p, sf, ip, pm in zip(
        notable["country"],
        notable["product"],
        notable["scale_factor"],
        notable["implied_prod"],
        notable["production_mt_fresh_retail"],
    ):
        flag = ""
        if sf > 3.0:
            flag = " [EXTREME HIGH]"
        elif sf < 0.3:
            flag = " [EXTREME LOW]"
        logger.info(
            "  %s/%s: scale=%.3f (implied=%.3f Mt, FAOSTAT=%.3f Mt)%s",
            c,
            p,
            sf,
            ip,
            pm,
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

    ruminant_feed_cats = sorted({f"ruminant_{c}" for c in rum_categories})
    monogastric_feed_cats = sorted({f"monogastric_{c}" for c in mono_categories})

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
