# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Calculate emissions from livestock manure management.

Computes CH4 and N2O emissions per kg dry matter feed intake for different
animal products and feed categories, accounting for:
- Volatile solids (VS) excretion based on digestibility and ash content
- Manure management system (MMS) distributions from GLEAM
- IPCC methane conversion factors and producing capacity
- IPCC N2O emission factors by MMS type

For N2O, we differentiate between:
- Pasture deposition (using EF3PRP: 0.02 for cattle, 0.01 for others)
- Managed systems (using storage EFs + application EF)

The key improvement is proper handling of Livestock Production Systems (LPS):
- Ruminant feed → Mixed LPS (mixed confined/pasture systems)
- Monogastrics → appropriate LPS (Broiler/Layer/Industrial/etc.)

NOTE: Currently averages MCF values across climate zones. This will be refined
later when climate zone data is added to modeling regions.
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Urinary energy excretion fractions (as fraction of gross energy intake)
URINARY_FRACTIONS = {
    "ruminant": 0.04,
    "pig": 0.02,
    "chicken": 0.0,
}

# Conversion factor from m³ CH₄ to kg CH₄
M3_CH4_TO_KG = 0.67

# IPCC N2O emission factors
# EF3PRP: Pasture/range/paddock deposition (IPCC 2019, Table 11.1)
EF3PRP_CATTLE = 0.02  # kg N2O-N per kg N for cattle/buffalo
EF3PRP_OTHER = 0.01  # kg N2O-N per kg N for sheep/goats/pigs/poultry

# EF1: Application of organic amendments (IPCC 2019, Table 11.1)
EF1_APPLICATION = 0.006  # kg N2O-N per kg N applied (wet climate default)

# N recovery rate for managed manure (fraction of excreted N available for application)
# Accounts for losses during collection, storage, and handling
MANURE_N_RECOVERY = 0.75

# Map animal products to urinary fraction categories
PRODUCT_TO_URINARY_CATEGORY = {
    "meat-cattle": "ruminant",
    "dairy": "ruminant",
    "meat-pig": "pig",
    "meat-chicken": "chicken",
    "eggs": "chicken",
    "dairy-buffalo": "ruminant",
    "meat-sheep": "ruminant",
}

# Map products to EF3PRP category (cattle vs other)
PRODUCT_TO_EF3PRP = {
    "meat-cattle": EF3PRP_CATTLE,
    "dairy": EF3PRP_CATTLE,
    "dairy-buffalo": EF3PRP_CATTLE,
    "meat-sheep": EF3PRP_OTHER,
    "meat-pig": EF3PRP_OTHER,
    "meat-chicken": EF3PRP_OTHER,
    "eggs": EF3PRP_OTHER,
}

# Map GLEAM animal names to our animal products
GLEAM_TO_PRODUCT = {
    "Cattle": ["meat-cattle", "dairy"],
    "Buffalo": ["dairy-buffalo"],
    "Pigs": ["meat-pig"],
    "Chickens": ["meat-chicken", "eggs"],
    "Sheep": ["meat-sheep"],
}

# Map monogastric products to their primary LPS
MONOGASTRIC_LPS_MAPPING = {
    "meat-pig": ["Industrial", "Intermediate"],  # Average these
    "meat-chicken": ["Broiler"],
    "eggs": ["Layer"],
}


def calculate_volatile_solids(
    feed_categories: pd.DataFrame,
    product: str,
) -> pd.DataFrame:
    """Calculate VS excretion per kg DM feed intake for each feed category.

    Uses adapted IPCC equation 10.24:
    VS = (1 - digestibility + urinary_fraction) * (1 - ash_content)

    Parameters
    ----------
    feed_categories : pd.DataFrame
        Feed category properties including digestibility and ash_content_pct_dm
    product : str
        Animal product name (e.g., "meat-cattle", "meat-pig")

    Returns
    -------
    pd.DataFrame
        Feed categories with added VS_kg_per_kg_DMI column
    """
    # Get urinary fraction for this product
    urinary_category = PRODUCT_TO_URINARY_CATEGORY[product]
    urinary_fraction = URINARY_FRACTIONS[urinary_category]

    # Calculate VS (kg VS per kg DM intake)
    # ash_content_pct_dm is in percent, convert to fraction
    df = feed_categories.copy()
    df["VS_kg_per_kg_DMI"] = (1 - df["digestibility"] + urinary_fraction) * (
        1 - df["ash_content_pct_dm"] / 100
    )

    return df


def average_mcf_over_climate_zones(
    mcf_data: pd.DataFrame,
) -> pd.DataFrame:
    """Average MCF values across climate zones for each manure management system.

    NOTE: This is a temporary simplification. Will be refined when climate zone
    data is added to modeling regions.

    Parameters
    ----------
    mcf_data : pd.DataFrame
        MCF data with columns: manure management system, climate zone, methane conversion factor

    Returns
    -------
    pd.DataFrame
        Averaged MCF by manure management system
    """
    # Average over climate zones
    mcf_avg = (
        mcf_data.groupby("manure management system")["methane conversion factor"]
        .mean()
        .reset_index()
    )

    logger.info("Averaged MCF over climate zones (temporary approach)")
    logger.info("  Will be refined with regional climate data in future")

    return mcf_avg


def get_mms_fractions_for_product(
    mms_fractions: pd.DataFrame,
    product: str,
) -> pd.DataFrame:
    """Get manure management system fractions for a given animal product.

    Averages over all livestock production systems (LPS) types for the given
    GLEAM animal category that maps to this product.

    Parameters
    ----------
    mms_fractions : pd.DataFrame
        GLEAM manure management system fractions
    product : str
        Animal product name

    Returns
    -------
    pd.DataFrame
        MMS fractions (long format) for this product
    """
    # Find which GLEAM animal(s) correspond to this product
    gleam_animals = [
        gleam_animal
        for gleam_animal, products in GLEAM_TO_PRODUCT.items()
        if product in products
    ]

    if not gleam_animals:
        raise ValueError(f"No GLEAM animal mapping found for product: {product}")

    # Filter to relevant animals and average over LPS types
    df = mms_fractions[mms_fractions["animal"].isin(gleam_animals)]

    # Get all MMS columns (everything except area, animal, lps)
    mms_columns = [col for col in df.columns if col not in ["area", "animal", "lps"]]

    # Average over LPS types
    avg_fractions = df[mms_columns].mean()

    # Convert to long format
    result = pd.DataFrame(
        {
            "manure management system": avg_fractions.index,
            "fraction": avg_fractions.values / 100,  # Convert from percent to fraction
        }
    )

    return result


def get_mms_fractions_for_lps(
    mms_fractions: pd.DataFrame,
    product: str,
    lps_types: list[str],
) -> pd.DataFrame:
    """Get MMS fractions for specific Livestock Production System types.

    Unlike get_mms_fractions_for_product, this does NOT average across all LPS
    types. Instead, it filters to the specified LPS types and averages only
    those (useful when a product spans multiple similar LPS like pigs).

    Parameters
    ----------
    mms_fractions : pd.DataFrame
        GLEAM manure management system fractions
    product : str
        Animal product name
    lps_types : list[str]
        LPS types to include (e.g., ["Grassland"], ["Mixed"], ["Industrial"])

    Returns
    -------
    pd.DataFrame
        MMS fractions (long format) for this product and LPS combination
    """
    # Find which GLEAM animal(s) correspond to this product
    gleam_animals = [
        gleam_animal
        for gleam_animal, products in GLEAM_TO_PRODUCT.items()
        if product in products
    ]

    if not gleam_animals:
        raise ValueError(f"No GLEAM animal mapping found for product: {product}")

    # Filter to relevant animals AND LPS types
    df = mms_fractions[
        (mms_fractions["animal"].isin(gleam_animals))
        & (mms_fractions["lps"].isin(lps_types))
    ]

    if df.empty:
        raise ValueError(
            f"No MMS data for product={product}, lps={lps_types}. "
            f"Available LPS: {mms_fractions[mms_fractions['animal'].isin(gleam_animals)]['lps'].unique().tolist()}"
        )

    # Get all MMS columns (everything except area, animal, lps)
    mms_columns = [col for col in df.columns if col not in ["area", "animal", "lps"]]

    # Average over the selected LPS types (if multiple)
    avg_fractions = df[mms_columns].mean()

    # Convert to long format
    result = pd.DataFrame(
        {
            "manure management system": avg_fractions.index,
            "fraction": avg_fractions.values / 100,  # Convert from percent to fraction
        }
    )

    return result


def calculate_n2o_factors_for_feed_category(
    product: str,
    feed_category: str,
    mms_fractions: pd.DataFrame,
    n2o_efs: pd.DataFrame,
) -> dict:
    """Calculate N2O emission factors for a product and feed category.

    Uses MMS-based approach to compute:
    1. Pasture fraction: uses EF3PRP (0.02 cattle, 0.01 others)
    2. Managed fraction: uses storage EF + application EF

    Parameters
    ----------
    product : str
        Animal product name
    feed_category : str
        Feed category (e.g., "ruminant_forage", "ruminant_grain")
    mms_fractions : pd.DataFrame
        GLEAM manure management system fractions
    n2o_efs : pd.DataFrame
        IPCC N2O emission factors by MMS type

    Returns
    -------
    dict
        Dictionary with keys:
        - pasture_fraction: fraction of manure deposited on pasture
        - pasture_n2o_ef: EF3PRP for this product
        - storage_n2o_ef: weighted storage EF for non-pasture systems
        - managed_n2o_ef: total EF for managed pathway (storage + application)
    """
    # Determine LPS based on feed category
    if product in ["meat-cattle", "dairy", "dairy-buffalo", "meat-sheep"]:
        lps_types = ["Mixed"]
    else:
        # Monogastrics: use product-specific LPS mapping
        lps_types = MONOGASTRIC_LPS_MAPPING.get(product, ["Industrial"])

    # Get MMS fractions for this LPS
    mms_frac = get_mms_fractions_for_lps(mms_fractions, product, lps_types)

    # Merge with N2O emission factors
    # Note: GLEAM uses "pasture & paddock", our data file uses same
    mms_with_ef = mms_frac.merge(
        n2o_efs,
        left_on="manure management system",
        right_on="mms_type",
        how="left",
    )

    # Handle missing EFs (shouldn't happen if data file is complete)
    if mms_with_ef["storage_ef"].isna().any():
        missing = mms_with_ef[mms_with_ef["storage_ef"].isna()][
            "manure management system"
        ].tolist()
        logger.warning("Missing N2O EFs for MMS types: %s. Using 0.0", missing)
        mms_with_ef["storage_ef"] = mms_with_ef["storage_ef"].fillna(0.0)
        mms_with_ef["is_pasture"] = mms_with_ef["is_pasture"].fillna(False)

    # Calculate pasture fraction
    pasture_mask = mms_with_ef["is_pasture"] == True  # noqa: E712
    pasture_fraction = mms_with_ef.loc[pasture_mask, "fraction"].sum()

    # Get EF3PRP for this product
    ef3prp = PRODUCT_TO_EF3PRP[product]

    # Calculate weighted storage EF for non-pasture systems
    non_pasture = mms_with_ef[~pasture_mask]
    if non_pasture["fraction"].sum() > 0:
        # Normalize fractions within non-pasture to sum to 1
        non_pasture_total = non_pasture["fraction"].sum()
        storage_n2o_ef = (
            non_pasture["fraction"] * non_pasture["storage_ef"]
        ).sum() / non_pasture_total
    else:
        storage_n2o_ef = 0.0

    # Calculate managed pathway EF (storage + application)
    # For the managed fraction, N goes through storage then application
    # N2O = storage_EF + (recovery_rate * application_EF)
    managed_n2o_ef = storage_n2o_ef + (MANURE_N_RECOVERY * EF1_APPLICATION)

    return {
        "pasture_fraction": pasture_fraction,
        "pasture_n2o_ef": ef3prp,
        "storage_n2o_ef": storage_n2o_ef,
        "managed_n2o_ef": managed_n2o_ef,
    }


def calculate_manure_ch4_for_product(
    product: str,
    feed_categories: pd.DataFrame,
    b0_data: pd.DataFrame,
    mcf_avg: pd.DataFrame,
    mms_fractions: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate manure CH4 emissions for one animal product across feed categories.

    Parameters
    ----------
    product : str
        Animal product name
    feed_categories : pd.DataFrame
        Feed category properties with VS calculated
    b0_data : pd.DataFrame
        B0 values (maximum CH4 producing capacity) by animal product
    mcf_avg : pd.DataFrame
        Climate-averaged MCF by manure management system
    mms_fractions : pd.DataFrame
        MMS fractions for this product

    Returns
    -------
    pd.DataFrame
        CH4 emissions (kg CH4 per kg DM intake) by feed category
    """
    # Get B0 for this product
    b0 = b0_data.loc[b0_data["animal product"] == product, "B0"].values[0]

    # Calculate VS for each feed category
    vs_data = calculate_volatile_solids(feed_categories, product)

    # Merge MMS fractions with MCF values
    mms_with_mcf = mms_fractions.merge(
        mcf_avg,
        on="manure management system",
        how="left",
    )

    # Calculate weighted average MCF across all manure management systems
    weighted_mcf = (
        mms_with_mcf["fraction"] * mms_with_mcf["methane conversion factor"]
    ).sum()

    # Calculate CH4 emissions: VS * B0 * MCF * conversion_factor
    # Result in kg CH4 per kg DM intake
    vs_data["manure_ch4_kg_per_kg_DMI"] = (
        vs_data["VS_kg_per_kg_DMI"] * b0 * weighted_mcf * M3_CH4_TO_KG
    )

    # Select relevant columns
    result = vs_data[["category", "manure_ch4_kg_per_kg_DMI"]].copy()
    result["product"] = product

    logger.info(
        "  %s: weighted MCF=%.4f, avg CH4=%.4f kg/kg DMI",
        product,
        weighted_mcf,
        result["manure_ch4_kg_per_kg_DMI"].mean(),
    )

    return result


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    # Read inputs
    ruminant_categories = pd.read_csv(snakemake.input.ruminant_feed_categories)
    monogastric_categories = pd.read_csv(snakemake.input.monogastric_feed_categories)
    b0_data = pd.read_csv(snakemake.input.b0_data, comment="#")
    mcf_data = pd.read_csv(snakemake.input.mcf_data, comment="#")
    mms_fractions = pd.read_csv(snakemake.input.mms_fractions, comment="#")
    n2o_efs = pd.read_csv(snakemake.input.n2o_efs, comment="#")

    # Get countries from config
    countries = snakemake.config["countries"]

    # Average MCF over climate zones
    mcf_avg = average_mcf_over_climate_zones(mcf_data)

    logger.info("Calculating manure emissions by product and feed category:")

    # Process each animal product
    ch4_results = []
    n2o_results = []

    for product in b0_data["animal product"].unique():
        # Determine if ruminant or monogastric
        if product in ["meat-cattle", "dairy", "dairy-buffalo", "meat-sheep"]:
            feed_categories = ruminant_categories
            category_prefix = "ruminant_"
        else:
            feed_categories = monogastric_categories
            category_prefix = "monogastric_"

        # Get MMS fractions for this product (averaged over LPS for CH4)
        mms_frac = get_mms_fractions_for_product(mms_fractions, product)

        # Calculate CH4 emissions
        product_ch4 = calculate_manure_ch4_for_product(
            product,
            feed_categories,
            b0_data,
            mcf_avg,
            mms_frac,
        )

        # Add category prefix to match feed_to_animal_products.csv format
        product_ch4["feed_category"] = category_prefix + product_ch4["category"]
        product_ch4 = product_ch4.drop(columns=["category"])

        ch4_results.append(product_ch4)

        # Calculate N2O factors for each feed category (LPS-specific)
        for _, row in feed_categories.iterrows():
            feed_category = category_prefix + row["category"]
            n2o_factors = calculate_n2o_factors_for_feed_category(
                product,
                feed_category,
                mms_fractions,
                n2o_efs,
            )
            n2o_results.append(
                {
                    "product": product,
                    "feed_category": feed_category,
                    "pasture_fraction": n2o_factors["pasture_fraction"],
                    "pasture_n2o_ef": n2o_factors["pasture_n2o_ef"],
                    "storage_n2o_ef": n2o_factors["storage_n2o_ef"],
                    "managed_n2o_ef": n2o_factors["managed_n2o_ef"],
                }
            )

    # Combine CH4 results
    ch4_emissions = pd.concat(ch4_results, ignore_index=True)

    # Create N2O dataframe
    n2o_emissions = pd.DataFrame(n2o_results)

    # Merge CH4 and N2O
    emissions = ch4_emissions.merge(
        n2o_emissions, on=["product", "feed_category"], how="left"
    )

    # Log N2O factor summary
    logger.info("\nN2O factor summary by product and LPS:")
    for product in emissions["product"].unique():
        product_data = emissions[emissions["product"] == product]
        for _, row in product_data.iterrows():
            logger.info(
                "  %s / %s: pasture=%.1f%%, EF_pasture=%.3f, EF_managed=%.4f",
                row["product"],
                row["feed_category"],
                row["pasture_fraction"] * 100,
                row["pasture_n2o_ef"],
                row["managed_n2o_ef"],
            )

    # Expand to all countries (same values for now, will be refined later)
    # NOTE: Emission factors are currently identical across countries
    # Will be differentiated by climate zone and region in future
    country_emissions = []
    for country in countries:
        df = emissions.copy()
        df["country"] = country
        country_emissions.append(df)

    final = pd.concat(country_emissions, ignore_index=True)

    # Reorder columns
    final = final[
        [
            "country",
            "product",
            "feed_category",
            "manure_ch4_kg_per_kg_DMI",
            "pasture_fraction",
            "pasture_n2o_ef",
            "storage_n2o_ef",
            "managed_n2o_ef",
        ]
    ]

    # Write output
    final.to_csv(snakemake.output[0], index=False)

    logger.info("\nManure emission factors calculated")
    logger.info("  Products: %d", len(emissions["product"].unique()))
    logger.info("  Feed categories: %d", len(emissions["feed_category"].unique()))
    logger.info("  Countries: %d", len(countries))
    logger.info("  Total rows: %d", len(final))
