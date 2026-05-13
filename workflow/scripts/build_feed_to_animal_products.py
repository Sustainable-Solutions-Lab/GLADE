# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generate feed-to-animal-product conversion efficiencies.

Uses GLEAM3-derived per-country ME requirements (MJ per kg product at
carcass/farm-gate level) combined with feed category energy values to
calculate feed conversion efficiencies (tonnes product per tonne feed DM).

UNIT CONVERSIONS:
1. Feed inputs: DRY MATTER (tonnes DM)
2. Animal product outputs: FRESH WEIGHT, RETAIL MEAT (tonnes fresh weight)
3. GLEAM3 ME requirements are per kg CARCASS/farm-gate weight
4. We apply carcass-to-retail conversion to get retail meat weight

The approach:
1. Load pre-computed per-country ME requirements from GLEAM3-derived CSV
2. Convert to retail meat using carcass_to_retail factors
3. Cross-join with feed categories to compute efficiency:
   efficiency = feed_energy_MJ_per_kg_DM / product_energy_requirement_MJ_per_kg

References:
- GLEAM 3.0 (FAO 2022): Feed intake and production data
- GLEAM (2022): Feed energy content values
- USDA/FAO: Carcass-to-retail conversion factors
"""

import logging

import pandas as pd

from workflow.scripts.diet.basis import conversion_factor
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def calculate_feed_efficiencies(
    me_requirements: pd.DataFrame,
    feed_categories: pd.DataFrame,
    animal_type: str,
) -> pd.DataFrame:
    """
    Calculate feed conversion efficiencies from ME requirements and feed energy values.

    efficiency = ME_content_feed / ME_requirement_product
    (in tonnes product per tonne feed DM)

    Parameters
    ----------
    me_requirements : pd.DataFrame
        Product ME requirements with columns: animal_product, country, ME_MJ_per_kg
    feed_categories : pd.DataFrame
        Feed category properties with columns: category, ME_MJ_per_kg_DM
    animal_type : str
        Either "ruminant" or "monogastric"

    Returns
    -------
    pd.DataFrame
        With columns: country, product, feed_category, efficiency
    """
    cross = me_requirements.merge(feed_categories, how="cross")
    cross["feed_category"] = animal_type + "_" + cross["category"]
    cross["efficiency"] = cross["ME_MJ_per_kg_DM"] / cross["ME_MJ_per_kg"]

    df = cross.rename(columns={"animal_product": "product"})[
        ["country", "product", "feed_category", "efficiency"]
    ]
    logger.info(
        "Calculated %d feed conversion efficiencies for %s",
        len(df),
        animal_type,
    )
    return df


def build_feed_to_animal_products(
    me_requirements_file: str,
    ruminant_categories_file: str,
    monogastric_categories_file: str,
    output_file: str,
    weight_conversion: dict[str, dict[str, float]],
) -> None:
    """
    Generate feed-to-animal-product conversion table from GLEAM3 ME requirements.

    Parameters
    ----------
    me_requirements_file : str
        Path to GLEAM3-derived per-country ME requirements CSV
    ruminant_categories_file : str
        Path to ruminant feed categories CSV
    monogastric_categories_file : str
        Path to monogastric feed categories CSV
    output_file : str
        Path to output feed_to_animal_products.csv
    weight_conversion : dict[str, dict[str, float]]
        Shared mass-basis conversion tables. Meat products use the
        ``carcass_to_fresh`` table to land per-kg ME requirements on the
        retail/fresh basis the food bus uses; non-meat products
        (eggs, dairy, dairy-buffalo) pass through with factor 1.0.
    """
    # Load data
    me_reqs = pd.read_csv(me_requirements_file, comment="#")
    ruminant_cats = pd.read_csv(ruminant_categories_file)
    monogastric_cats = pd.read_csv(monogastric_categories_file)

    logger.info("Loaded ME requirements: %d rows", len(me_reqs))
    logger.info("Loaded ruminant categories: %d", len(ruminant_cats))
    logger.info("Loaded monogastric categories: %d", len(monogastric_cats))

    # Apply carcass-to-retail conversion: ME_retail = ME_carcass / factor.
    # conversion_factor returns 1.0 for products not in carcass_to_fresh
    # (eggs, dairy, dairy-buffalo are already on retail/fresh basis).
    me_reqs["ME_MJ_per_kg"] = me_reqs.apply(
        lambda row: row["ME_MJ_per_kg"]
        / conversion_factor(
            "carcass", "fresh", row["animal_product"], weight_conversion
        ),
        axis=1,
    )
    invalid_me = ~me_reqs["ME_MJ_per_kg"].gt(0) | ~me_reqs["ME_MJ_per_kg"].notna()
    if invalid_me.any():
        invalid_rows = me_reqs.loc[
            invalid_me, ["animal_product", "country", "ME_MJ_per_kg"]
        ]
        raise ValueError(
            "Feed conversion requires strictly positive ME requirements; "
            f"found invalid rows:\n{invalid_rows.to_string(index=False)}"
        )
    carcass_to_fresh = weight_conversion.get("carcass_to_fresh", {})
    if carcass_to_fresh:
        logger.info("Carcass-to-retail conversion factors:")
        for product, factor in carcass_to_fresh.items():
            logger.info("  %s: %.2f", product, factor)

    # Split ME requirements by animal type for feed category matching
    ruminant_products = {"dairy", "dairy-buffalo", "meat-cattle", "meat-sheep"}
    ruminant_me = me_reqs[me_reqs["animal_product"].isin(ruminant_products)]
    monogastric_me = me_reqs[~me_reqs["animal_product"].isin(ruminant_products)]

    # Calculate feed conversion efficiencies
    ruminant_eff = calculate_feed_efficiencies(ruminant_me, ruminant_cats, "ruminant")
    monogastric_eff = calculate_feed_efficiencies(
        monogastric_me, monogastric_cats, "monogastric"
    )

    # Combine
    all_eff = pd.concat([ruminant_eff, monogastric_eff], ignore_index=True)

    # Sort and write
    all_eff = all_eff.sort_values(["country", "product", "feed_category"])

    output_cols = ["country", "product", "feed_category", "efficiency"]
    all_eff[output_cols].to_csv(output_file, index=False)

    logger.info("Wrote %d feed conversion entries to %s", len(all_eff), output_file)

    logger.info("\nSummary by product:")
    for product in all_eff["product"].unique():
        product_data = all_eff[all_eff["product"] == product]
        logger.info(
            "  %s: %d entries (%.3f-%.3f t/t)",
            product,
            len(product_data),
            product_data["efficiency"].min(),
            product_data["efficiency"].max(),
        )


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    build_feed_to_animal_products(
        me_requirements_file=snakemake.input.me_requirements,
        ruminant_categories_file=snakemake.input.ruminant_categories,
        monogastric_categories_file=snakemake.input.monogastric_categories,
        output_file=snakemake.output[0],
        weight_conversion=dict(snakemake.params.weight_conversion),
    )
