"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Prepare GLEAM 2.0 feed baseline estimates by country and product.

Disaggregates global feed intake from Mottet et al. (2017) SI Table 2 to
individual countries using FAO production shares, splits multi-product
systems using FCR-weighted shares from Wirsenius (2000), decomposes ruminant
roughage using regional composition tables (SI 4-5), maps to model feed
categories, and scales to the configured reference year.

Output: CSV with columns (country, product, feed_category, feed_use_mt_dm).
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

# GLEAM SI Table 2 feed types -> model feed categories for monogastrics.
MONOGASTRIC_FEED_MAPPING = {
    "Cereal grains": "monogastric_grain",
    "2nd grade grain": "monogastric_grain",
    "Soybean cakes": "monogastric_protein",
    "Other oil seed cakes": "monogastric_protein",
    "Other edible": "monogastric_grain",
    "Brans spent brewer and biofuel grains": "monogastric_low_quality",
    "Other non-edible": "monogastric_low_quality",
}

# GLEAM SI Table 2 feed types -> model feed categories for ruminants.
# "Roughages" is decomposed further using SI Tables 4-5.
RUMINANT_FEED_MAPPING = {
    "Cereal grains": "ruminant_grain",
    "Brans spent brewer and biofuel grains": "ruminant_grain",
    "Soybean cakes": "ruminant_protein",
    "Other oil seed cakes": "ruminant_protein",
    "Other edible": "ruminant_grain",
    "Other non-edible": "ruminant_grain",
}

# Mapping from SI Table 4/5 composition components to model feed categories.
ROUGHAGE_COMPONENT_MAPPING = {
    "Fresh grass": "ruminant_grassland",
    "Hay": "ruminant_grassland",
    "Legumes and silage": "ruminant_forage",
    "Crop residues": "ruminant_roughage",
    "Sugarcane tops": "ruminant_roughage",
    "Leaves": "ruminant_roughage",
}

# Composition components that are by-products/concentrates (not roughage)
# but appear in SI4/5 tables. These are mapped to ruminant feed categories
# and used to account for concentrate portions in the roughage decomposition.
CONCENTRATE_COMPONENT_MAPPING = {
    "Bran": "ruminant_grain",
    "Oilseed meals": "ruminant_protein",
    "Wet distillers grain": "ruminant_grain",
    "Grains": "ruminant_grain",
    "Molasses": "ruminant_grain",
    "Pulp": "ruminant_protein",
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


def decompose_roughage(
    roughage_mt: float,
    gleam_region: str,
    composition: pd.DataFrame,
) -> dict[str, float]:
    """Decompose a roughage total using regional composition percentages.

    Uses the specified composition table (dairy or beef cattle). Only applies
    the roughage portion of the composition (Fresh grass, Hay, Legumes and
    silage, Crop residues, Sugarcane tops, Leaves).

    Returns dict mapping model feed category to Mt DM.
    """
    if roughage_mt <= 0:
        return {}

    result: dict[str, float] = {}

    for component, feed_cat in ROUGHAGE_COMPONENT_MAPPING.items():
        pct = 0.0
        if component in composition.index and gleam_region in composition.columns:
            pct = composition.loc[component, gleam_region]
        if pct > 0:
            result[feed_cat] = result.get(feed_cat, 0) + roughage_mt * pct / 100.0

    # Normalize: ensure decomposed amounts sum to the roughage total.
    # Any residual is distributed proportionally across existing categories.
    decomposed_total = sum(result.values())
    if decomposed_total > 0 and abs(decomposed_total - roughage_mt) > 0.01:
        scale = roughage_mt / decomposed_total
        result = {k: v * scale for k, v in result.items()}

    return result


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

    records: list[dict] = []
    for _, si2_row in si2_data.iterrows():
        region = si2_row["Region"]
        species = si2_row["Species"]
        system = si2_row["System"]

        products = SYSTEM_PRODUCT_MAP.get((species, system))
        if products is None:
            logger.warning("No product mapping for (%s, %s); skipping", species, system)
            continue

        is_ruminant = species in RUMINANT_SPECIES

        # Get countries in this OECD/Non-OECD group with their shares
        region_shares = shares[
            (shares["region"] == region) & (shares["species"] == species)
        ]

        for _, share_row in region_shares.iterrows():
            country = share_row["country"]
            share = share_row["share"]

            if share <= 0:
                continue

            # Compute product shares for multi-product systems
            product_shares = compute_product_shares(
                products,
                country,
                fao_2010,
                fcr_lookup,
                country_to_wirsenius.get(country),
            )

            for product, prod_share in product_shares.items():
                if prod_share <= 0:
                    continue

                for feed_type in feed_cols:
                    if feed_type not in si2_row.index:
                        continue
                    amount = si2_row[feed_type] * share * prod_share

                    if amount <= 0:
                        continue

                    if feed_type == "Roughages" and is_ruminant:
                        gleam_region = country_to_gleam.get(country)
                        if gleam_region is None:
                            logger.warning(
                                "No GLEAM region for country %s; "
                                "assigning roughage to ruminant_roughage",
                                country,
                            )
                            records.append(
                                {
                                    "country": country,
                                    "product": product,
                                    "feed_category": "ruminant_roughage",
                                    "feed_use_mt_dm": amount,
                                }
                            )
                            continue

                        comp_type = PRODUCT_COMPOSITION.get(product)
                        if comp_type is None:
                            records.append(
                                {
                                    "country": country,
                                    "product": product,
                                    "feed_category": "ruminant_roughage",
                                    "feed_use_mt_dm": amount,
                                }
                            )
                            continue

                        composition = dairy_comp if comp_type == "dairy" else beef_comp
                        decomposed = decompose_roughage(
                            amount, gleam_region, composition
                        )
                        for feed_cat, cat_amount in decomposed.items():
                            if cat_amount > 0:
                                records.append(
                                    {
                                        "country": country,
                                        "product": product,
                                        "feed_category": feed_cat,
                                        "feed_use_mt_dm": cat_amount,
                                    }
                                )

                    elif feed_type == "Swill":
                        continue

                    elif feed_type == "Roughages" and not is_ruminant:
                        # Monogastric roughages are rare (pig backyard);
                        # assign to monogastric_low_quality
                        records.append(
                            {
                                "country": country,
                                "product": product,
                                "feed_category": "monogastric_low_quality",
                                "feed_use_mt_dm": amount,
                            }
                        )

                    else:
                        if is_ruminant:
                            feed_cat = RUMINANT_FEED_MAPPING.get(feed_type)
                        else:
                            feed_cat = MONOGASTRIC_FEED_MAPPING.get(feed_type)

                        if feed_cat is None:
                            logger.warning(
                                "No mapping for feed type '%s' (species: %s)",
                                feed_type,
                                species,
                            )
                            continue

                        records.append(
                            {
                                "country": country,
                                "product": product,
                                "feed_category": feed_cat,
                                "feed_use_mt_dm": amount,
                            }
                        )

    if not records:
        raise RuntimeError("No feed baseline records generated")

    result = pd.DataFrame(records)

    # Aggregate duplicate (country, product, feed_category) entries
    result = result.groupby(["country", "product", "feed_category"], as_index=False)[
        "feed_use_mt_dm"
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
        # Subtract swill from expected total
        expected_total -= si2_row.get("Swill", 0) * avg_scale

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

    result = result.drop(columns=["species", "region"])

    # Remove zero/tiny entries
    result = result[result["feed_use_mt_dm"] > 1e-6]
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
    logger.info(
        "Saved %d records (%d countries) to %s",
        len(result),
        n_countries,
        output_path,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
