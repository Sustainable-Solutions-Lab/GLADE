"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Prepare FAO animal production statistics for validation constraints.

Reads country-level production of major animal products (dairy, beef, pork,
poultry, eggs) from a FAOSTAT QCL bulk CSV to establish production targets
for validation mode.

For poultry, multiple FAOSTAT species are aggregated into the model's
``meat-chicken`` product so demand for "other poultry" can be projected onto
the modeled poultry commodity.

Output weight basis
-------------------
- Meats (``meat-cattle``, ``meat-pig``, ``meat-chicken``, ``meat-sheep``):
  fresh retail weight (boneless, trimmed). FAOSTAT QCL reports primary
  meat in carcass weight equivalent ("with the bone, fresh or chilled");
  this script multiplies by ``carcass_to_retail_meat`` from config to
  convert to retail mass.
- Dairy (``dairy``, ``dairy-buffalo``): raw whole milk mass (fresh).
- Eggs: whole eggs in shell (fresh).

The output column is named ``production_mt_fresh_retail`` to make this
basis explicit. It is the same basis the ``animal_production`` link
expects on its bus1 output before the link applies the
``(1-loss_fraction) * (1-waste_fraction)`` FLW multiplier.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.animal_utils import load_faostat_qcl
from workflow.scripts.faostat_bulk import filter_bulk
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def main() -> None:
    qcl_csv = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    production_year = int(snakemake.params.production_year)  # type: ignore[name-defined]
    countries = list(snakemake.params.countries)  # type: ignore[name-defined]
    carcass_to_retail = dict(  # type: ignore[name-defined]
        snakemake.params.carcass_to_retail_meat
    )
    faostat_items: dict[str, list[str]] = dict(snakemake.params.faostat_items)  # type: ignore[name-defined]

    # Load bulk CSV, extract metadata, and add ISO3 column
    bulk, item_map = load_faostat_qcl(qcl_csv, m49_codes)

    element_code = int(snakemake.params.qcl_element_code)  # type: ignore[name-defined]
    logger.info("Using FAOSTAT element 'Production' (code %s)", element_code)

    # Map FAOSTAT items to codes
    faostat_to_model: dict[int, str] = {}  # faostat_item_code -> model_product
    item_codes: list[int] = []

    for model_product, fao_items in faostat_items.items():
        for faostat_item in fao_items:
            if faostat_item not in item_map:
                logger.warning(
                    "FAOSTAT item '%s' not found for product '%s'; skipping",
                    faostat_item,
                    model_product,
                )
                continue
            item_code = item_map[faostat_item]
            item_codes.append(item_code)
            faostat_to_model[item_code] = model_product
            logger.info(
                "Mapped '%s' -> FAOSTAT item '%s' (code %s)",
                model_product,
                faostat_item,
                item_code,
            )

    if not item_codes:
        raise RuntimeError("No FAOSTAT items could be mapped")

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
        country=df["iso3"].astype(str).str.strip(),
        year=df["Year"].astype(int),
    )
    df["product"] = df["Item Code"].map(faostat_to_model)
    df = df[df["product"].notna() & df["Value"].notna() & (df["Value"] >= 0)]

    if df.empty:
        raise RuntimeError(
            "No FAOSTAT production records matched the configured animal products"
        )

    # Egg unit handling:
    # - Preferred/expected: tonnes ("t"), no conversion required.
    # - Legacy possibility: thousand eggs ("1000 No"), convert to tonnes
    #   assuming 60 g/egg.
    egg_mask_raw = df["product"] == "eggs"
    if egg_mask_raw.any():
        if "Unit" not in df.columns:
            raise RuntimeError("FAOSTAT production data has no 'Unit' column")

        egg_units = (
            df.loc[egg_mask_raw, "Unit"].astype(str).str.strip().str.lower().unique()
        )
        logger.info(
            "Egg production units in source data: %s",
            ", ".join(sorted(egg_units)),
        )

        egg_unit_series = df["Unit"].astype(str).str.strip().str.lower()
        thousand_eggs_mask = (
            egg_mask_raw
            & egg_unit_series.str.contains("1000", na=False)
            & egg_unit_series.str.contains("no", na=False)
        )
        tonnes_mask = egg_mask_raw & (egg_unit_series == "t")
        unknown_mask = egg_mask_raw & ~(thousand_eggs_mask | tonnes_mask)

        if unknown_mask.any():
            unknown_units = sorted(egg_unit_series.loc[unknown_mask].unique())
            raise RuntimeError(
                "Unexpected FAOSTAT egg unit(s): "
                + ", ".join(unknown_units)
                + ". Expected 't' or '1000 No'."
            )

        if thousand_eggs_mask.any():
            # egg_thousands * 1000 eggs/thousand * 60g/egg / 1000 g/kg / 1000 kg/tonne
            # = egg_thousands * 0.06 tonnes
            df.loc[thousand_eggs_mask, "Value"] = (
                df.loc[thousand_eggs_mask, "Value"] * 0.06
            )
            logger.info(
                "Converted %d egg records from '1000 No' to tonnes (60 g/egg)",
                int(thousand_eggs_mask.sum()),
            )

    result = (
        df.groupby(["country", "product", "year"], as_index=False)["Value"]
        .sum()
        .rename(columns={"Value": "production_tonnes"})
        .sort_values(["country", "product"])
    )

    # Convert tonnes to Mt for consistency with model units
    result["production_mt_fresh_retail"] = result["production_tonnes"] * 1e-6
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
        result.loc[meat_mask, "production_mt_fresh_retail"] = (
            result.loc[meat_mask, "production_mt_fresh_retail"]
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
        total_mt = prod_data["production_mt_fresh_retail"].sum()
        n_countries = len(prod_data)
        logger.info(
            "  %s: %.2f Mt across %d countries",
            product,
            total_mt,
            n_countries,
        )

    # Check for countries with missing products and fill with zeros
    all_products = list(faostat_items.keys())
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
                        "production_mt_fresh_retail": 0.0,
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
