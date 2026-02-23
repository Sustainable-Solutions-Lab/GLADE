"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Prepare GLEAM 2.0 feed baseline estimates by country and product.

Disaggregates global feed intake from Mottet et al. (2017) SI Table 2 to
individual countries using FAO production shares, splits multi-product
systems using FCR-weighted shares from Wirsenius (2000), decomposes ruminant
roughage using regional composition tables (SI 4-5), maps to model feed
categories, and scales to the configured reference year.

Output: CSV with columns (country, product, feed_category, feed_use_mt_dm,
exogenous_mt_dm).  The exogenous_mt_dm column tracks feed demand that the
model cannot produce endogenously (tree leaves/browse, swill).
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.animal_utils import SPECIES_PRODUCTS, load_faostat_qcl
from workflow.scripts.build_feed_to_animal_products import (
    calculate_ruminant_me_requirements,
    get_monogastric_me_requirements,
)
from workflow.scripts.faostat_bulk import filter_bulk
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# (Species, System) -> model products served by each GLEAM production system.
# Most systems map to a single product; cattle grazing/mixed and poultry
# backyard serve multiple products and require FCR-weighted splitting.
SYSTEM_PRODUCT_MAP = {
    ("Cattle & buffaloes", "Feedlots*"): ["meat-cattle"],
    ("Cattle & buffaloes", "Grazing"): ["dairy", "dairy-buffalo", "meat-cattle"],
    ("Cattle & buffaloes", "Mixed"): ["dairy", "dairy-buffalo", "meat-cattle"],
    ("Small Ruminants", "Grazing"): ["meat-sheep"],
    ("Small Ruminants", "Mixed"): ["meat-sheep"],
    ("Poultry", "Layers"): ["eggs"],
    ("Poultry", "Broilers"): ["meat-chicken"],
    ("Poultry", "Backyard"): ["eggs", "meat-chicken"],
    ("Pigs", "Backyard"): ["meat-pig"],
    ("Pigs", "Intermediate"): ["meat-pig"],
    ("Pigs", "Industrial"): ["meat-pig"],
}

# Which composition table to use per ruminant product for roughage
# decomposition. Dairy products use dairy cattle composition (SI4); meat
# products use beef cattle composition (SI5).
PRODUCT_COMPOSITION = {
    "dairy": "dairy",
    "dairy-buffalo": "dairy",
    "meat-cattle": "beef",
    "meat-sheep": "beef",
}

# SI2 column → representative model feed item for ruminants.
# Categories resolved at runtime from ruminant_feed_mapping.csv.
SI2_TO_RUMINANT_FEED_ITEM = {
    "Cereal grains": "maize",
    "Brans spent brewer and biofuel grains": "wheat-bran",
    "Soybean cakes": "sunflower-meal",
    "Other oil seed cakes": "rapeseed-meal",
    "Other edible": "sugarbeet",
    "Other non-edible": "barley",
}

# SI2 column → representative model feed item for monogastrics.
# Categories resolved at runtime from monogastric_feed_mapping.csv.
SI2_TO_MONOGASTRIC_FEED_ITEM = {
    "Cereal grains": "maize",
    "2nd grade grain": "maize",
    "Soybean cakes": "sunflower-meal",
    "Other oil seed cakes": "rapeseed-meal",
    "Other edible": "cassava",
    "Brans spent brewer and biofuel grains": "wheat-bran",
    "Other non-edible": "wheat-bran",
}

# SI Table 4/5 composition components → representative model feed items.
# None = exogenous (no model production route).
ROUGHAGE_COMPONENT_TO_FEED_ITEM = {
    "Fresh grass": "grassland",
    "Hay": "grassland",
    "Legumes and silage": "alfalfa",
    "Crop residues": "wheat-straw",
    "Sugarcane tops": "sugarcane-tops",
    "Leaves": None,  # exogenous — no model production route
}


# Species that are classified as ruminants (vs. monogastrics)
RUMINANT_SPECIES = {"Cattle & buffaloes", "Small Ruminants"}


def load_si_table_2(path: str) -> pd.DataFrame:
    """Load SI Table 2 (global feed intake by region/species/system).

    Returns a DataFrame with columns:
        Region, Species, System, and one column per feed type (Mt DM).
    """
    df = pd.read_csv(path, comment="#")
    # Replace empty strings with 0
    feed_cols = [c for c in df.columns if c not in ("Region", "Species", "System")]
    for col in feed_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def load_composition_table(path: str) -> pd.DataFrame:
    """Load a ruminant composition table (SI 4, 5, etc.).

    Returns a DataFrame indexed by 'Feed component' with GLEAM region columns.
    Values are percentages of total DM intake.
    """
    df = pd.read_csv(path, comment="#", index_col=0)
    # Convert all values to numeric, missing = 0
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def load_fao_production(
    bulk: pd.DataFrame,
    item_map: dict[str, str],
    year: int,
    countries: list[str],
    faostat_items: dict[str, list[str]],
) -> pd.DataFrame:
    """Load FAO production data at model product level.

    Uses *faostat_items* (``{product: [fao_item_name, …]}``) to map
    FAOSTAT QCL items to model products.

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

        # Element code 5510 = Production (tonnes)
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

        # Aggregate across items per country
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


def compute_country_shares(
    fao_prod: pd.DataFrame,
    oecd_status: dict[str, bool],
) -> pd.DataFrame:
    """Compute each country's share of production within its OECD/Non-OECD group.

    Groups product-level production into species using SPECIES_PRODUCTS,
    then computes shares within OECD/Non-OECD groups per species.

    Returns DataFrame with: country, species, region (OECD/Non OECD), share.
    """
    # Build product -> species mapping
    product_to_species = {}
    for species, products in SPECIES_PRODUCTS.items():
        for product in products:
            product_to_species[product] = species

    # Sum production per country per species
    fao_prod = fao_prod.copy()
    fao_prod["species"] = fao_prod["product"].map(product_to_species)
    species_prod = (
        fao_prod.groupby(["country", "species"])["production_tonnes"]
        .sum()
        .reset_index()
    )

    species_prod["region"] = species_prod["country"].map(
        lambda c: "OECD" if oecd_status.get(c, False) else "Non OECD"
    )

    # Compute group totals
    group_totals = (
        species_prod.groupby(["species", "region"])["production_tonnes"]
        .sum()
        .reset_index()
        .rename(columns={"production_tonnes": "group_total"})
    )

    species_prod = species_prod.merge(group_totals, on=["species", "region"])
    species_prod["share"] = (
        species_prod["production_tonnes"] / species_prod["group_total"]
    )
    species_prod.loc[species_prod["group_total"] == 0, "share"] = 0

    return species_prod[["country", "species", "region", "share"]]


def compute_fcr_lookup(
    wirsenius: pd.DataFrame,
    k_m: float,
    k_g: float,
    k_l: float,
    feed_proxy_map: dict[str, str],
    products: list[str],
) -> dict[tuple[str, str], float]:
    """Compute feed conversion ratios (ME MJ per kg product) at carcass level.

    Uses carcass_to_retail=1.0 for all products so FCRs are at the same
    basis as FAO production data (carcass/farm-gate weight).

    Returns dict mapping (product, wirsenius_region) to ME MJ/kg.
    """
    all_products = list(products)
    unity = {p: 1.0 for p in all_products}

    ruminant_me = calculate_ruminant_me_requirements(
        wirsenius, k_m, k_g, k_l, unity, feed_proxy_map
    )
    monogastric_me = get_monogastric_me_requirements(wirsenius, unity)

    me_all = pd.concat([ruminant_me, monogastric_me], ignore_index=True)

    return {
        (row["animal_product"], row["region"]): row["ME_MJ_per_kg"]
        for _, row in me_all.iterrows()
    }


def compute_product_shares(
    products: list[str],
    country: str,
    fao_prod: pd.DataFrame,
    fcr_lookup: dict[tuple[str, str], float],
    wirsenius_region: str | None,
) -> dict[str, float]:
    """Compute FCR-weighted product shares for splitting feed within a system.

    share_i = prod_i * FCR_i / sum(prod_j * FCR_j) per country,
    where FCR is total ME per kg product at carcass/farm-gate level.

    Falls back to equal shares if no Wirsenius region or no production data.
    """
    if len(products) == 1:
        return {products[0]: 1.0}

    if wirsenius_region is None:
        return {p: 1.0 / len(products) for p in products}

    country_fao = fao_prod[fao_prod["country"] == country]

    weighted = {}
    for p in products:
        prod_match = country_fao.loc[country_fao["product"] == p, "production_tonnes"]
        prod = prod_match.sum() if not prod_match.empty else 0.0
        fcr = fcr_lookup.get((p, wirsenius_region), 0.0)
        weighted[p] = prod * fcr

    total = sum(weighted.values())
    if total <= 0:
        return {p: 1.0 / len(products) for p in products}

    return {p: w / total for p, w in weighted.items()}


def resolve_roughage_categories(
    rum_item_to_cat: dict[str, str],
) -> dict[str, str]:
    """Build roughage component → prefixed feed category from CSV-derived lookup.

    Resolves each component's representative feed item through *rum_item_to_cat*
    (loaded from ``ruminant_feed_mapping.csv``).  Leaves (None feed item) fall
    back to ``ruminant_roughage``.
    """
    result = {}
    for component, feed_item in ROUGHAGE_COMPONENT_TO_FEED_ITEM.items():
        if feed_item is None:
            result[component] = "ruminant_roughage"
        else:
            cat = rum_item_to_cat.get(feed_item)
            if cat is None:
                raise ValueError(
                    f"Feed item '{feed_item}' from ROUGHAGE_COMPONENT_TO_FEED_ITEM "
                    f"not found in ruminant feed mapping CSV"
                )
            result[component] = f"ruminant_{cat}"
    return result


def decompose_roughage(
    roughage_mt: float,
    gleam_region: str,
    composition: pd.DataFrame,
    component_to_category: dict[str, str],
) -> tuple[dict[str, float], float]:
    """Decompose a roughage total using regional composition percentages.

    Uses the specified composition table (dairy or beef cattle). Only applies
    the roughage portion of the composition (Fresh grass, Hay, Legumes and
    silage, Crop residues, Sugarcane tops, Leaves).

    *component_to_category* maps roughage component names to prefixed model
    feed categories.

    Returns a tuple of (dict mapping model feed category to Mt DM, leaves Mt DM).
    The leaves amount is the portion of ruminant_roughage attributable to tree
    leaves/browse, tracked separately for exogenous supply since the model has
    no endogenous production route for leaves.
    """
    if roughage_mt <= 0:
        return {}, 0.0

    result: dict[str, float] = {}
    leaves_raw = 0.0

    for component, feed_cat in component_to_category.items():
        pct = 0.0
        if component in composition.index and gleam_region in composition.columns:
            pct = composition.loc[component, gleam_region]
        if pct > 0:
            amount = roughage_mt * pct / 100.0
            result[feed_cat] = result.get(feed_cat, 0) + amount
            if component == "Leaves":
                leaves_raw = amount

    # Normalize: ensure decomposed amounts sum to the roughage total.
    # Any residual is distributed proportionally across existing categories.
    decomposed_total = sum(result.values())
    if decomposed_total > 0 and abs(decomposed_total - roughage_mt) > 0.01:
        scale = roughage_mt / decomposed_total
        result = {k: v * scale for k, v in result.items()}
        leaves_raw *= scale

    return result, leaves_raw


def main() -> None:
    si_table_2_path = snakemake.input.si_table_2  # type: ignore[name-defined]
    si_table_4_path = snakemake.input.si_table_4  # type: ignore[name-defined]
    si_table_5_path = snakemake.input.si_table_5  # type: ignore[name-defined]
    oecd_status_path = snakemake.input.oecd_status  # type: ignore[name-defined]
    gleam_regions_path = snakemake.input.gleam_regions  # type: ignore[name-defined]
    wirsenius_path = snakemake.input.wirsenius  # type: ignore[name-defined]
    country_wirsenius_region_path = snakemake.input.country_wirsenius_region  # type: ignore[name-defined]
    qcl_csv_path = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes_path = snakemake.input.m49_codes  # type: ignore[name-defined]
    ruminant_mapping_path = snakemake.input.ruminant_feed_mapping  # type: ignore[name-defined]
    monogastric_mapping_path = snakemake.input.monogastric_feed_mapping  # type: ignore[name-defined]
    feed_efficiencies_path = snakemake.input.feed_to_animal_products  # type: ignore[name-defined]
    faostat_production_path = snakemake.input.faostat_animal_production  # type: ignore[name-defined]
    calibration_path = getattr(snakemake.input, "calibration", None)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    reference_year = int(snakemake.params.reference_year)  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    net_to_me = snakemake.params.net_to_me_conversion  # type: ignore[name-defined]
    feed_proxy_map = dict(snakemake.params.feed_proxy_map)  # type: ignore[name-defined]
    faostat_items: dict[str, list[str]] = dict(snakemake.params.faostat_items)  # type: ignore[name-defined]

    # -- Load input data ------------------------------------------------
    logger.info("Loading SI Table 2 (global feed intake)")
    si2 = load_si_table_2(si_table_2_path)

    logger.info("Loading composition tables")
    dairy_comp = load_composition_table(si_table_4_path)
    beef_comp = load_composition_table(si_table_5_path)

    logger.info("Loading OECD status")
    oecd_df = pd.read_csv(oecd_status_path, comment="#")
    oecd_members = set(oecd_df["country"].tolist())
    oecd_status = {c: c in oecd_members for c in countries}

    logger.info("Loading GLEAM region mapping")
    gleam_region_df = pd.read_csv(
        gleam_regions_path, comment="#", keep_default_na=False
    )
    country_to_gleam = dict(
        zip(gleam_region_df["country"], gleam_region_df["gleam_region"])
    )

    logger.info("Loading Wirsenius data and region mapping")
    wirsenius = pd.read_csv(wirsenius_path, comment="#")
    wirsenius_region_df = pd.read_csv(country_wirsenius_region_path, comment="#")
    country_to_wirsenius = dict(
        zip(
            wirsenius_region_df["country"],
            wirsenius_region_df["wirsenius_region"],
        )
    )

    bulk, item_map = load_faostat_qcl(qcl_csv_path, m49_codes_path)

    logger.info("Loading feed category mappings")
    rum_mapping = pd.read_csv(ruminant_mapping_path, comment="#")
    mono_mapping = pd.read_csv(monogastric_mapping_path, comment="#")
    rum_item_to_cat = dict(zip(rum_mapping["feed_item"], rum_mapping["category"]))
    mono_item_to_cat = dict(zip(mono_mapping["feed_item"], mono_mapping["category"]))

    # -- Step 1: Load FAO production at product level ------------------
    logger.info("Loading FAO 2010 production for disaggregation")
    fao_2010 = load_fao_production(bulk, item_map, 2010, countries, faostat_items)

    logger.info("Loading FAO %d production for scaling", reference_year)
    fao_ref = load_fao_production(
        bulk, item_map, reference_year, countries, faostat_items
    )

    # -- Step 2: Compute species-level country shares ------------------
    logger.info("Computing country production shares (2010)")
    shares = compute_country_shares(fao_2010, oecd_status)

    # -- Step 3: Compute FCR lookup ------------------------------------
    logger.info("Computing FCR lookup from Wirsenius data")
    fcr_lookup = compute_fcr_lookup(
        wirsenius,
        net_to_me["k_m"],
        net_to_me["k_g"],
        net_to_me["k_l"],
        feed_proxy_map,
        list(faostat_items.keys()),
    )

    # -- Step 4: Disaggregate to countries and products ----------------
    logger.info("Disaggregating feed intake to countries and products")

    feed_cols = [
        "Roughages",
        "Swill",
        "Cereal grains",
        "2nd grade grain",
        "Brans spent brewer and biofuel grains",
        "Soybean cakes",
        "Other oil seed cakes",
        "Other edible",
        "Other non-edible",
    ]

    # Filter out World summary rows
    si2_data = si2[~si2["Region"].str.contains("World", na=False)].copy()

    # -- Step A: Melt SI2 to long format --------------------------------
    si2_long = si2_data.melt(
        id_vars=["Region", "Species", "System"],
        value_vars=feed_cols,
        var_name="feed_type",
        value_name="global_mt_dm",
    )
    si2_long = si2_long[si2_long["global_mt_dm"] > 0]

    # -- Step B: Expand to products via SYSTEM_PRODUCT_MAP ----------------
    system_prods = pd.DataFrame(
        [
            {"Species": sp, "System": sys, "product": p}
            for (sp, sys), prods in SYSTEM_PRODUCT_MAP.items()
            for p in prods
        ]
    )
    si2_long = si2_long.merge(system_prods, on=["Species", "System"], how="inner")
    si2_long["is_ruminant"] = si2_long["Species"].isin(RUMINANT_SPECIES)

    # -- Step C: Merge country shares ------------------------------------
    si2_long = si2_long.merge(
        shares.rename(
            columns={"region": "Region", "species": "Species", "share": "country_share"}
        ),
        on=["Region", "Species"],
        how="inner",
    )
    si2_long = si2_long[si2_long["country_share"] > 0]

    # -- Step D: Pre-compute and merge product shares --------------------
    unique_countries = si2_long["country"].unique()
    prod_share_records = []
    for (species, system), products in SYSTEM_PRODUCT_MAP.items():
        if len(products) == 1:
            continue  # single-product systems get share 1.0 via fillna
        for country in unique_countries:
            p_shares = compute_product_shares(
                products,
                country,
                fao_2010,
                fcr_lookup,
                country_to_wirsenius.get(country),
            )
            for p, s in p_shares.items():
                prod_share_records.append(
                    {
                        "Species": species,
                        "System": system,
                        "country": country,
                        "product": p,
                        "product_share": s,
                    }
                )
    if prod_share_records:
        prod_shares_df = pd.DataFrame(prod_share_records)
        si2_long = si2_long.merge(
            prod_shares_df,
            on=["Species", "System", "country", "product"],
            how="left",
        )
        si2_long["product_share"] = si2_long["product_share"].fillna(1.0)
    else:
        si2_long["product_share"] = 1.0

    # -- Step E: Compute amounts -----------------------------------------
    si2_long["amount"] = (
        si2_long["global_mt_dm"] * si2_long["country_share"] * si2_long["product_share"]
    )
    si2_long = si2_long[si2_long["amount"] > 0]

    # -- Step F: Branch into four sub-pipelines --------------------------
    result_parts = []

    # F1: Roughage (ruminant) — decompose via composition tables
    f1_mask = (si2_long["feed_type"] == "Roughages") & si2_long["is_ruminant"]
    f1 = si2_long[f1_mask].copy()

    if not f1.empty:
        f1["gleam_region"] = f1["country"].map(country_to_gleam)
        f1["comp_type"] = f1["product"].map(PRODUCT_COMPOSITION)

        # Rows without GLEAM region or composition type → ruminant_roughage
        f1_fallback_mask = f1["gleam_region"].isna() | f1["comp_type"].isna()
        if f1_fallback_mask.any():
            for _, row in f1[f1_fallback_mask].iterrows():
                if pd.isna(row["gleam_region"]):
                    logger.warning(
                        "No GLEAM region for country %s; "
                        "assigning roughage to ruminant_roughage",
                        row["country"],
                    )
            f1_fb = f1.loc[f1_fallback_mask, ["country", "product", "amount"]].copy()
            f1_fb["feed_category"] = "ruminant_roughage"
            f1_fb = f1_fb.rename(columns={"amount": "feed_use_mt_dm"})
            f1_fb["exogenous_mt_dm"] = 0.0
            result_parts.append(f1_fb)

        # Normal rows: vectorized roughage decomposition
        f1_normal = f1[~f1_fallback_mask].copy()
        if not f1_normal.empty:
            # Build composition fraction table (long format)
            comp_records = []
            roughage_categories = resolve_roughage_categories(rum_item_to_cat)
            for comp_type_name, comp_df in [("dairy", dairy_comp), ("beef", beef_comp)]:
                for component in ROUGHAGE_COMPONENT_TO_FEED_ITEM:
                    for gleam_region in comp_df.columns:
                        pct = 0.0
                        if component in comp_df.index:
                            pct = comp_df.loc[component, gleam_region]
                        comp_records.append(
                            {
                                "comp_type": comp_type_name,
                                "gleam_region": gleam_region,
                                "component": component,
                                "pct": pct,
                            }
                        )
            comp_fractions = pd.DataFrame(comp_records)
            # Normalize so roughage components sum to 1.0
            comp_totals = comp_fractions.groupby(["comp_type", "gleam_region"])[
                "pct"
            ].transform("sum")
            comp_fractions["norm_fraction"] = 0.0
            nonzero_ct = comp_totals > 0
            comp_fractions.loc[nonzero_ct, "norm_fraction"] = (
                comp_fractions.loc[nonzero_ct, "pct"] / comp_totals[nonzero_ct]
            )

            f1_expanded = f1_normal.merge(
                comp_fractions, on=["comp_type", "gleam_region"], how="left"
            )
            f1_expanded["norm_fraction"] = f1_expanded["norm_fraction"].fillna(0)
            f1_expanded["feed_use_mt_dm"] = (
                f1_expanded["amount"] * f1_expanded["norm_fraction"]
            )
            f1_expanded["feed_category"] = f1_expanded["component"].map(
                roughage_categories
            )
            # Exogenous: Leaves amount
            f1_expanded["exogenous_mt_dm"] = 0.0
            leaves_mask = f1_expanded["component"] == "Leaves"
            f1_expanded.loc[leaves_mask, "exogenous_mt_dm"] = f1_expanded.loc[
                leaves_mask, "feed_use_mt_dm"
            ]
            f1_expanded = f1_expanded[f1_expanded["feed_use_mt_dm"] > 0]
            result_parts.append(
                f1_expanded[
                    [
                        "country",
                        "product",
                        "feed_category",
                        "feed_use_mt_dm",
                        "exogenous_mt_dm",
                    ]
                ]
            )

    # F2: Roughage (monogastric) → monogastric_low_quality
    f2_mask = (si2_long["feed_type"] == "Roughages") & ~si2_long["is_ruminant"]
    f2 = si2_long[f2_mask].copy()
    if not f2.empty:
        f2["feed_category"] = "monogastric_low_quality"
        f2["feed_use_mt_dm"] = f2["amount"]
        f2["exogenous_mt_dm"] = 0.0
        result_parts.append(
            f2[
                [
                    "country",
                    "product",
                    "feed_category",
                    "feed_use_mt_dm",
                    "exogenous_mt_dm",
                ]
            ]
        )

    # F3: Swill → exogenous
    f3_mask = si2_long["feed_type"] == "Swill"
    f3 = si2_long[f3_mask].copy()
    if not f3.empty:
        f3["feed_category"] = f3["is_ruminant"].map(
            {True: "ruminant_grain", False: "monogastric_low_quality"}
        )
        f3["feed_use_mt_dm"] = f3["amount"]
        f3["exogenous_mt_dm"] = f3["amount"]
        result_parts.append(
            f3[
                [
                    "country",
                    "product",
                    "feed_category",
                    "feed_use_mt_dm",
                    "exogenous_mt_dm",
                ]
            ]
        )

    # F4: Other feed types → category via mapping CSVs
    f4_mask = ~(
        (si2_long["feed_type"] == "Roughages") | (si2_long["feed_type"] == "Swill")
    )
    f4 = si2_long[f4_mask].copy()
    if not f4.empty:
        f4_rum = f4[f4["is_ruminant"]].copy()
        f4_mono = f4[~f4["is_ruminant"]].copy()

        if not f4_rum.empty:
            f4_rum["feed_item"] = f4_rum["feed_type"].map(SI2_TO_RUMINANT_FEED_ITEM)
            f4_rum["category"] = f4_rum["feed_item"].map(rum_item_to_cat)
            f4_rum["feed_category"] = "ruminant_" + f4_rum["category"].astype(str)
            unmapped = f4_rum["feed_item"].isna() | f4_rum["category"].isna()
            if unmapped.any():
                for ft in f4_rum.loc[unmapped, "feed_type"].unique():
                    logger.warning("No ruminant mapping for feed type '%s'", ft)
            f4_rum = f4_rum[~unmapped]

        if not f4_mono.empty:
            f4_mono["feed_item"] = f4_mono["feed_type"].map(
                SI2_TO_MONOGASTRIC_FEED_ITEM
            )
            f4_mono["category"] = f4_mono["feed_item"].map(mono_item_to_cat)
            f4_mono["feed_category"] = "monogastric_" + f4_mono["category"].astype(str)
            unmapped = f4_mono["feed_item"].isna() | f4_mono["category"].isna()
            if unmapped.any():
                for ft in f4_mono.loc[unmapped, "feed_type"].unique():
                    logger.warning("No monogastric mapping for feed type '%s'", ft)
            f4_mono = f4_mono[~unmapped]

        f4 = pd.concat([f4_rum, f4_mono])
        if not f4.empty:
            f4["feed_use_mt_dm"] = f4["amount"]
            f4["exogenous_mt_dm"] = 0.0
            result_parts.append(
                f4[
                    [
                        "country",
                        "product",
                        "feed_category",
                        "feed_use_mt_dm",
                        "exogenous_mt_dm",
                    ]
                ]
            )

    # -- Combine and aggregate -------------------------------------------
    if not result_parts:
        raise RuntimeError("No feed baseline records generated")

    result = pd.concat(result_parts, ignore_index=True)
    result = result.groupby(["country", "product", "feed_category"], as_index=False)[
        ["feed_use_mt_dm", "exogenous_mt_dm"]
    ].sum()

    # -- Step 5: Scale to reference year -------------------------------
    logger.info("Scaling from 2010 to reference year %d", reference_year)

    # Compute scaling factors per country and product
    prod_2010 = (
        fao_2010.groupby(["country", "product"])["production_tonnes"]
        .sum()
        .reset_index()
        .rename(columns={"production_tonnes": "prod_2010"})
    )
    prod_ref = (
        fao_ref.groupby(["country", "product"])["production_tonnes"]
        .sum()
        .reset_index()
        .rename(columns={"production_tonnes": "prod_ref"})
    )

    scale_df = prod_2010.merge(prod_ref, on=["country", "product"], how="left")
    scale_df["prod_ref"] = scale_df["prod_ref"].fillna(0)
    scale_df["scale"] = 1.0
    nonzero = scale_df["prod_2010"] > 0
    scale_df.loc[nonzero, "scale"] = (
        scale_df.loc[nonzero, "prod_ref"] / scale_df.loc[nonzero, "prod_2010"]
    )

    result = result.merge(
        scale_df[["country", "product", "scale"]],
        on=["country", "product"],
        how="left",
    )
    result["scale"] = result["scale"].fillna(1.0)
    result["feed_use_mt_dm"] = result["feed_use_mt_dm"] * result["scale"]
    result["exogenous_mt_dm"] = result["exogenous_mt_dm"] * result["scale"]
    result = result.drop(columns=["scale"])

    # -- Step 6: Normalize ---------------------------------------------
    # Ensure country-level totals per OECD/Non-OECD group and species sum
    # to scaled global totals (prevent leakage from countries missing data).
    logger.info("Normalizing to preserve group totals")

    product_to_species = {}
    for species, prods in SPECIES_PRODUCTS.items():
        for p in prods:
            product_to_species[p] = species

    result["species"] = result["product"].map(product_to_species)
    result["region"] = result["country"].map(
        lambda c: "OECD" if oecd_status.get(c, False) else "Non OECD"
    )

    # SI2 totals by Region and Species (summed across systems)
    si2_totals = si2_data.groupby(["Region", "Species"])[feed_cols].sum()

    for (region, species), _ in si2_totals.iterrows():
        group_mask = (result["region"] == region) & (result["species"] == species)
        if not group_mask.any():
            continue

        actual_total = result.loc[group_mask, "feed_use_mt_dm"].sum()

        # Compute expected scaled total using production-weighted average
        species_prods = SPECIES_PRODUCTS[species]
        group_countries = result.loc[group_mask, "country"].unique()

        group_scale = scale_df[
            (scale_df["country"].isin(group_countries))
            & (scale_df["product"].isin(species_prods))
        ]

        if group_scale.empty or group_scale["prod_2010"].sum() == 0:
            continue

        avg_scale = group_scale["prod_ref"].sum() / group_scale["prod_2010"].sum()

        si2_row = si2_totals.loc[(region, species)]
        expected_total = si2_row[feed_cols].sum() * avg_scale

        if actual_total > 0 and expected_total > 0:
            correction = expected_total / actual_total
            if abs(correction - 1.0) > 0.01:
                logger.info(
                    "  %s / %s: correction factor %.3f",
                    region,
                    species,
                    correction,
                )
                result.loc[group_mask, "feed_use_mt_dm"] *= correction
                result.loc[group_mask, "exogenous_mt_dm"] *= correction

    result = result.drop(columns=["species", "region"])

    # -- Step 7: Production-based scaling ----------------------------------
    # Scale feed per (country, product) so that implied production
    # (feed x efficiency) matches FAOSTAT production. This preserves the
    # GLEAM-derived feed *composition* (category splits) while correcting
    # the absolute level to observed production data.
    logger.info("Applying production-based feed scaling")

    feed_eff = pd.read_csv(feed_efficiencies_path)
    faostat_prod = pd.read_csv(faostat_production_path)

    # Apply calibration multipliers to feed efficiencies if provided
    if calibration_path:
        cal = pd.read_csv(calibration_path)
        feed_eff = feed_eff.merge(
            cal[["country", "product", "feed_category", "multiplier"]],
            on=["country", "product", "feed_category"],
            how="left",
        )
        feed_eff["multiplier"] = feed_eff["multiplier"].fillna(1.0)
        n_cal = int((feed_eff["multiplier"] != 1.0).sum())
        logger.info(
            "Applied calibration to %d/%d feed efficiencies (median mult %.3f)",
            n_cal,
            len(feed_eff),
            feed_eff.loc[feed_eff["multiplier"] != 1.0, "multiplier"].median()
            if n_cal
            else 1.0,
        )
        feed_eff["efficiency"] *= feed_eff["multiplier"]
        feed_eff = feed_eff.drop(columns=["multiplier"])

    # Compute implied production per (country, product) from current feed
    # and efficiency values: sum over categories of feed x efficiency.
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

    # Merge with FAOSTAT production (Mt, retail weight)
    implied = implied.merge(
        faostat_prod[["country", "product", "production_mt"]],
        on=["country", "product"],
        how="left",
    )
    implied["production_mt"] = implied["production_mt"].fillna(0)

    # Compute scale factors
    implied["scale_factor"] = 1.0
    # Case: FAOSTAT production = 0 → set feed to 0
    zero_prod = implied["production_mt"] == 0
    implied.loc[zero_prod, "scale_factor"] = 0.0
    # Case: implied > 0 and FAOSTAT > 0 → scale
    has_implied = implied["implied_prod"] > 0
    scalable = has_implied & ~zero_prod
    implied.loc[scalable, "scale_factor"] = (
        implied.loc[scalable, "production_mt"] / implied.loc[scalable, "implied_prod"]
    )
    # Case: implied = 0 but FAOSTAT > 0 → can't create feed from nothing
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

    # Log scale factors, flagging extreme values
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

    # Apply scale factors
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

    # Expand to all valid (country, product, feed_category) combinations,
    # filling 0 where GLEAM has no data.  This ensures every model link gets
    # an explicit baseline rather than relying on implicit defaults.
    ruminant_products = [p for sp in RUMINANT_SPECIES for p in SPECIES_PRODUCTS[sp]]
    all_products = [p for prods in SPECIES_PRODUCTS.values() for p in prods]
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
