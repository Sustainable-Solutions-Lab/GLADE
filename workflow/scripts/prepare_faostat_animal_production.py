"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Prepare FAO animal production statistics for validation constraints.

Reads country-level production of major animal products (dairy, beef, pork,
poultry, eggs) from a FAOSTAT QCL bulk CSV to establish production targets
for validation mode.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    get_item_map,
    int_str,
    load_bulk_csv,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Mapping from model product names to FAOSTAT item names
ANIMAL_PRODUCT_MAPPING = {
    "dairy": "Raw milk of cattle",
    "meat-cattle": "Meat of cattle with the bone, fresh or chilled",
    "meat-pig": "Meat of pig with the bone, fresh or chilled",
    "meat-chicken": "Meat of chickens, fresh or chilled",
    "eggs": "Hen eggs in shell, fresh",
    "dairy-buffalo": "Raw milk of buffalo",
    "meat-sheep": "Meat of sheep, fresh or chilled",
}

# Additional milk sources to include in dairy totals (not modeled explicitly,
# but added to production targets so cattle/buffalo stand in for all milk)
ADDITIONAL_DAIRY_SOURCES = {
    "Raw milk of goats": "dairy",
    "Raw milk of sheep": "dairy",
    "Raw milk of camel": "dairy",
}


def main() -> None:
    qcl_csv = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    production_year = int(snakemake.params.production_year)  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    carcass_to_retail = dict(  # type: ignore[name-defined]
        snakemake.params.carcass_to_retail_meat
    )

    # Load bulk CSV and extract metadata
    logger.info("Loading FAOSTAT QCL bulk CSV")
    bulk = load_bulk_csv(qcl_csv)
    item_map = get_item_map(bulk)

    element_code = str(snakemake.params.qcl_element_code)  # type: ignore[name-defined]
    logger.info("Using FAOSTAT element 'Production' (code %s)", element_code)

    # Map FAOSTAT items to codes
    faostat_to_model: dict[str, str] = {}  # faostat_item_code -> model_product
    item_codes: list[str] = []

    for model_product, faostat_item in ANIMAL_PRODUCT_MAPPING.items():
        if faostat_item not in item_map:
            logger.warning(
                "FAOSTAT item '%s' not found for product '%s'; skipping",
                faostat_item,
                model_product,
            )
            continue
        item_code = item_map[faostat_item]
        item_codes.append(str(item_code))
        faostat_to_model[str(item_code)] = model_product
        logger.info(
            "Mapped '%s' -> FAOSTAT item '%s' (code %s)",
            model_product,
            faostat_item,
            item_code,
        )

    # Add additional dairy sources (goat, sheep, camel milk -> dairy)
    for faostat_item, model_product in ADDITIONAL_DAIRY_SOURCES.items():
        if faostat_item not in item_map:
            logger.warning(
                "FAOSTAT item '%s' not found; skipping additional dairy source",
                faostat_item,
            )
            continue
        item_code = item_map[faostat_item]
        item_codes.append(str(item_code))
        faostat_to_model[str(item_code)] = model_product
        logger.info(
            "Mapped additional dairy source '%s' -> '%s' (code %s)",
            faostat_item,
            model_product,
            item_code,
        )

    if not item_codes:
        raise RuntimeError("No FAOSTAT items could be mapped")

    # Add ISO3 column from M49 codes
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    # Filter bulk data
    logger.info(
        "Filtering FAOSTAT production data for year %s (%d animal products)",
        production_year,
        len(item_codes),
    )
    df = filter_bulk(
        bulk,
        element_codes=[element_code],
        item_codes=item_codes,
        years=[production_year],
        iso3_codes=countries,
    )

    if df.empty:
        raise RuntimeError(
            "FAOSTAT returned no production data for the requested selection"
        )

    df = df.dropna(subset=["Value"])
    if df.empty:
        raise RuntimeError("FAOSTAT production data contains no numeric values")

    # Check and filter by unit
    if "Unit" in df.columns:
        units_series = df["Unit"].astype(str).str.lower()
        logger.info(
            "FAOSTAT returned units: %s",
            ", ".join(sorted(units_series.unique())),
        )

    # Map rows to model products and aggregate
    df = df.assign(
        item_code=df["Item Code"].map(int_str),
        country=df["iso3"].astype(str).str.strip(),
        year=pd.to_numeric(df["Year"], errors="coerce").astype(int),
    )
    df["product"] = df["item_code"].map(faostat_to_model)
    df = df[df["product"].notna() & df["Value"].notna() & (df["Value"] >= 0)]

    if df.empty:
        raise RuntimeError(
            "No FAOSTAT production records matched the configured animal products"
        )

    result = (
        df.groupby(["country", "product", "year"], as_index=False)["Value"]
        .sum()
        .rename(columns={"Value": "production_tonnes"})
        .sort_values(["country", "product"])
    )

    # Convert eggs from number to tonnes (approximate: 60g per egg)
    # FAOSTAT reports eggs in "1000 no" units (thousands of eggs)
    egg_mask = result["product"] == "eggs"
    if egg_mask.any():
        # egg_thousands * 1000 eggs/thousand * 60g/egg / 1000 g/kg / 1000 kg/tonne
        # = egg_thousands * 0.06 tonnes
        result.loc[egg_mask, "production_tonnes"] = (
            result.loc[egg_mask, "production_tonnes"] * 0.06
        )
        logger.info(
            "Converted %d egg records from thousands to tonnes (assuming 60g/egg)",
            egg_mask.sum(),
        )

    # Convert tonnes to Mt for consistency with model units
    result["production_mt"] = result["production_tonnes"] * 1e-6
    result = result.drop(columns=["production_tonnes"])

    # Convert carcass-weight meat production to retail-weight using config factors
    meat_mask = result["product"].astype(str).str.startswith("meat-")
    if meat_mask.any():
        result["carcass_to_retail"] = (
            result["product"].map(carcass_to_retail).astype(float)
        )
        missing = meat_mask & result["carcass_to_retail"].isna()
        if missing.any():
            missing_products = sorted(result.loc[missing, "product"].unique())
            logger.warning(
                "Missing carcass-to-retail factors for meat products: %s",
                ", ".join(missing_products),
            )
            result.loc[missing, "carcass_to_retail"] = 1.0
        result.loc[meat_mask, "production_mt"] = (
            result.loc[meat_mask, "production_mt"]
            * result.loc[meat_mask, "carcass_to_retail"]
        )
        logger.info(
            "Applied carcass-to-retail conversion for %d meat rows",
            int(meat_mask.sum()),
        )
        result = result.drop(columns=["carcass_to_retail"])

    # Log summary statistics
    logger.info("Retrieved country-level production data:")
    for product in result["product"].unique():
        prod_data = result[result["product"] == product]
        total_mt = prod_data["production_mt"].sum()
        n_countries = len(prod_data)
        logger.info(
            "  %s: %.2f Mt across %d countries",
            product,
            total_mt,
            n_countries,
        )

    # Check for countries with missing products and fill with zeros
    all_products = list(ANIMAL_PRODUCT_MAPPING.keys())
    missing_records = []
    for country in countries:
        existing = set(result[result["country"] == country]["product"])
        for product in all_products:
            if product not in existing:
                missing_records.append(
                    {
                        "country": country,
                        "product": product,
                        "year": production_year,
                        "production_mt": 0.0,
                    }
                )

    if missing_records:
        logger.info(
            "Added %d zero-production records for missing country-product combinations",
            len(missing_records),
        )
        result = pd.concat([result, pd.DataFrame(missing_records)], ignore_index=True)
        result = result.sort_values(["country", "product"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    logger.info(
        "Saved %d country-level animal production records to %s",
        len(result),
        output_path,
    )


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
