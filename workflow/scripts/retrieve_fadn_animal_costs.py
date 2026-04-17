# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve EU FADN production cost data for animal products and compute
inflation-adjusted averages in base year dollars per tonne of product.

This script processes FADN (Farm Accountancy Data Network) data to extract
livestock production costs, excluding feed costs that are modeled endogenously.

For detailed documentation on cost modeling and data sources, see
docs/livestock.rst (Production Costs section) and docs/data_sources.rst.

Inputs
- snakemake.input.data: FADN NUTS0 data CSV
- snakemake.input.mapping: YAML mapping FADN livestock SE codes to model products
- snakemake.input.hicp: CSV with EU HICP annual averages
- snakemake.input.ppp: CSV with PPP rates (EUR per international $)
- snakemake.input.yields: CSV with FAOSTAT animal yields (Tonnes/Head)
- snakemake.params.animal_products: List of animal products to process
- snakemake.params.base_year: Base year for inflation adjustment

Output
- snakemake.output.costs: CSV with columns:
    product,cost_per_mt_usd_{base_year}

Notes
- Costs included: Labor, veterinary, energy, housing, depreciation, interest
- Costs explicitly EXCLUDED: Feed costs (SE310 - purchased feed), rent
- Inflation-adjusted using EU HICP, converted EUR→USD using PPP rates
- Costs allocated proportionally by livestock output value
- Converted to per-tonne using FADN physical yield (SE126 for milk) or FAOSTAT yields
- Averaged across EU countries and years (2015-2024 where available)
"""

import logging

import pandas as pd
import yaml

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# FADN livestock output value variables (SE2xx codes)
# These will be loaded from the mapping file
LIVESTOCK_OUTPUT_VARS = {}  # Will be populated from mapping
# Mapping from FADN Category (SE Code) to list of Animal Number SE Codes
ANIMAL_NUMBER_VARS = {}  # Will be populated from mapping
# Mapping from FADN Category (SE Code) to LU conversion factor
LU_CONVERSION_FACTORS = {}  # Will be populated from mapping


# Mapping from FADN NUTS0 codes to FAOSTAT country names
NUTS0_TO_FAOSTAT = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "EL": "Greece",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MT": "Malta",
    "NL": "Netherlands",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "UK": "United Kingdom",
}


def load_hicp_data(hicp_path: str) -> dict[int, float]:
    """Load EU HICP annual averages from CSV.

    Returns dict mapping year -> HICP value.
    """
    df = pd.read_csv(hicp_path)
    if not {"year", "hicp"}.issubset(df.columns):
        raise ValueError("HICP CSV must have columns: year, hicp")

    hicp_values = df.set_index("year")["hicp"].to_dict()
    logger.info(
        f"Loaded HICP data for years {min(hicp_values.keys())}-{max(hicp_values.keys())}"
    )
    return hicp_values


def load_ppp_rate(ppp_path: str) -> float:
    """Load PPP conversion rate from CSV.

    Returns PPP rate (EUR per international $).
    """
    df = pd.read_csv(ppp_path)
    if "avg_ppp_eur_per_intl_dollar" not in df.columns:
        raise ValueError("PPP CSV must have column: avg_ppp_eur_per_intl_dollar")

    ppp_rate = float(df["avg_ppp_eur_per_intl_dollar"].iloc[0])
    logger.info(f"Loaded PPP rate: {ppp_rate:.4f} EUR per international $")
    return ppp_rate


def inflate_eur_to_base_year(
    value: float, year: int, base_year: int, hicp_values: dict[int, float]
) -> float:
    """Adjust nominal EUR value to base year EUR using HICP."""
    if year not in hicp_values:
        raise ValueError(f"No HICP data for year {year}")
    if base_year not in hicp_values:
        raise ValueError(f"No HICP data for base year {base_year}")
    return value * (hicp_values[base_year] / hicp_values[year])


def convert_eur_to_intl_dollar(value_eur: float, ppp_rate: float) -> float:
    """Convert EUR to international $ using PPP rate.

    PPP rate is EUR per international $, so to convert EUR to international $:
    international $ = EUR / (EUR per international $)
    """
    return value_eur / ppp_rate


def process_fadn_animal_costs(
    fadn_df: pd.DataFrame,
    yields_df: pd.DataFrame,
    animal_mapping: dict,
    base_year: int,
    ppp_rate: float,
    hicp_values: dict[int, float],
    years: list[int],
    livestock_specific_costs: dict[str, str],
    shared_farm_costs: dict[str, str],
    high_cost_threshold: float,
    grazing_cost_items: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Extract and process FADN livestock cost data, allocating to model products.

    Returns DataFrame with columns: product, cost_per_mt_usd_{base_year}, grazing_cost_per_mt_usd_{base_year}
    """
    if grazing_cost_items is None:
        grazing_cost_items = {}

    # Filter to relevant years
    fadn_df = fadn_df[fadn_df["year"].isin(years)].copy()

    # Remove rows with missing data
    fadn_df = fadn_df[fadn_df["SE025"].notna()].copy()  # SE025 = Total UAA

    if fadn_df.empty:
        logger.warning(f"No FADN data available for years {years}")
        return pd.DataFrame()

    # Create yield lookup: (country, year, product) -> yield
    # Handle duplicate entries if any (shouldn't be)
    yields_lookup = yields_df.set_index(["country", "year", "product"])[
        "yield_t_per_head"
    ].to_dict()

    logger.info(
        f"Processing FADN livestock data: {len(fadn_df)} country-year observations "
        f"from {fadn_df['year'].min()}-{fadn_df['year'].max()}"
    )

    results = []

    for _, row in fadn_df.iterrows():
        year = int(row["year"])
        country_code = row["NUTS"]  # NUTS0 code

        # Map to FAOSTAT country name
        country_name = NUTS0_TO_FAOSTAT.get(country_code)
        if not country_name:
            # logger.debug(f"No FAOSTAT mapping for FADN country {country_code}, skipping yield lookup")
            pass

        # Calculate total livestock output value for allocation
        livestock_outputs = {}
        total_livestock_output = 0.0
        for se_code in LIVESTOCK_OUTPUT_VARS:
            if se_code in row.index and pd.notna(row[se_code]):
                output_value = float(row[se_code])
                if output_value > 0:
                    livestock_outputs[se_code] = output_value
                    total_livestock_output += output_value

        if total_livestock_output == 0:
            continue

        # Calculate output shares relative to Total Farm Output (SE131)
        # SE131 includes crops, livestock, and other output.
        if "SE131" not in row or pd.isna(row["SE131"]) or float(row["SE131"]) == 0:
            logger.debug(f"No Total Output (SE131) for {country_code} {year}, skipping")
            continue

        total_farm_output = float(row["SE131"])

        # Calculate specific and shared costs
        specific_costs_total = sum(
            float(row.get(se_code, 0) or 0) for se_code in livestock_specific_costs
        )
        shared_costs_total = sum(
            float(row.get(se_code, 0) or 0) for se_code in shared_farm_costs
        )
        grazing_costs_total = sum(
            float(row.get(se_code, 0) or 0) for se_code in grazing_cost_items
        )

        # Calculate total livestock output (sum of categories) to allocate specific costs
        # Specific costs (SE330) are allocated within livestock categories only.
        if total_livestock_output == 0:
            continue

        # For each category, calculate cost per head and then cost per tonne
        for se_code, output_val in livestock_outputs.items():
            if se_code not in animal_mapping:
                continue

            # Share of livestock output (for specific costs and grazing costs)
            share_of_livestock = output_val / total_livestock_output

            # Share of total farm output (for shared costs)
            share_of_farm = output_val / total_farm_output

            # Allocated cost = (Specific * Share_Livestock) + (Shared * Share_Farm)
            allocated_cost_eur = (specific_costs_total * share_of_livestock) + (
                shared_costs_total * share_of_farm
            )

            allocated_grazing_cost_eur = grazing_costs_total * share_of_livestock

            mapping_info = animal_mapping[se_code]
            products = mapping_info["products"]
            animal_num_vars = ANIMAL_NUMBER_VARS.get(se_code, [])
            lu_factor = LU_CONVERSION_FACTORS.get(se_code, 1.0)

            # Get number of animals (LU) for this category
            num_animals_lu = 0.0
            for var in animal_num_vars:
                if var in row and pd.notna(row[var]):
                    num_animals_lu += float(row[var])

            if num_animals_lu <= 0:
                continue

            # Cost per LU (EUR)
            cost_per_lu_eur = allocated_cost_eur / num_animals_lu
            grazing_cost_per_lu_eur = allocated_grazing_cost_eur / num_animals_lu

            # Convert to Cost per Head (EUR) using LU factor
            # Heads = LU / Factor
            # Cost/Head = Cost/LU * Factor
            cost_per_head_eur = cost_per_lu_eur * lu_factor
            grazing_cost_per_head_eur = grazing_cost_per_lu_eur * lu_factor

            # Inflate to base year and convert to USD (PPP)
            cost_per_head_eur_base = inflate_eur_to_base_year(
                cost_per_head_eur, year, base_year, hicp_values
            )
            cost_per_head_usd = convert_eur_to_intl_dollar(
                cost_per_head_eur_base, ppp_rate
            )

            grazing_cost_per_head_eur_base = inflate_eur_to_base_year(
                grazing_cost_per_head_eur, year, base_year, hicp_values
            )
            grazing_cost_per_head_usd = convert_eur_to_intl_dollar(
                grazing_cost_per_head_eur_base, ppp_rate
            )

            # Determine yield for each product in this category
            for product in products:
                yield_t_per_head = None

                # Fallback to FAOSTAT lookup
                if country_name:
                    key = (country_name, year, product)
                    if key in yields_lookup:
                        yield_t_per_head = float(yields_lookup[key])

                # If we still don't have yield, we can't calculate cost per tonne
                if yield_t_per_head is None or yield_t_per_head <= 0:
                    continue

                cost_per_mt = cost_per_head_usd / yield_t_per_head
                grazing_cost_per_mt = grazing_cost_per_head_usd / yield_t_per_head

                if cost_per_mt > high_cost_threshold and product != "eggs":
                    logger.warning(
                        f"High cost for {product} in {country_code} {year}: ${cost_per_mt:.2f}/Mt. "
                        f"Cost/LU: {cost_per_lu_eur:.2f}, Yield: {yield_t_per_head:.4f}, LU Factor: {lu_factor}"
                    )

                results.append(
                    {
                        "product": product,
                        "fadn_category": se_code,
                        "country": country_code,
                        "year": year,
                        "cost_per_mt": cost_per_mt,
                        "grazing_cost_per_mt": grazing_cost_per_mt,
                    }
                )

    if not results:
        logger.warning("No valid FADN livestock cost data after processing")
        return pd.DataFrame()

    results_df = pd.DataFrame(results)

    # Average across countries and years
    product_costs = (
        results_df.groupby("product")
        .agg(
            cost_per_mt=("cost_per_mt", "mean"),
            grazing_cost_per_mt=("grazing_cost_per_mt", "mean"),
            n_obs=("year", "count"),
            n_countries=("country", "nunique"),
        )
        .reset_index()
    )

    product_costs = product_costs.rename(
        columns={
            "cost_per_mt": f"cost_per_mt_usd_{base_year}",
            "grazing_cost_per_mt": f"grazing_cost_per_mt_usd_{base_year}",
        }
    )

    for _, row in product_costs.iterrows():
        logger.info(
            f"  {row['product']}: "
            f"${row[f'cost_per_mt_usd_{base_year}']:.2f}/Mt "
            f"(Grazing: ${row[f'grazing_cost_per_mt_usd_{base_year}']:.2f}/Mt) "
            f"({row['n_countries']} countries, {row['n_obs']} obs)"
        )

    return product_costs


def main():
    fadn_data_path: str = snakemake.input.data  # type: ignore[name-defined]
    mapping_path: str = snakemake.input.mapping  # type: ignore[name-defined]
    hicp_path: str = snakemake.input.hicp  # type: ignore[name-defined]
    ppp_path: str = snakemake.input.ppp  # type: ignore[name-defined]
    yields_path: str = snakemake.input.yields  # type: ignore[name-defined]
    base_year: int = int(snakemake.params.base_year)  # type: ignore[name-defined]
    cost_params: dict = snakemake.params.cost_params  # type: ignore[name-defined]
    averaging_period: dict = snakemake.params.averaging_period  # type: ignore[name-defined]
    out_path: str = snakemake.output.costs  # type: ignore[name-defined]

    # Extract params
    years = list(
        range(averaging_period["start_year"], averaging_period["end_year"] + 1)
    )
    livestock_specific_costs = cost_params["livestock_specific_costs"]
    shared_farm_costs = cost_params["shared_farm_costs"]
    grazing_cost_items = cost_params.get("grazing_cost_items", {})
    high_cost_threshold = float(cost_params["high_cost_threshold_usd_per_mt"])

    # Load mapping
    with open(mapping_path) as f:
        animal_mapping = yaml.safe_load(f)

    global LIVESTOCK_OUTPUT_VARS, ANIMAL_NUMBER_VARS, LU_CONVERSION_FACTORS
    LIVESTOCK_OUTPUT_VARS = {
        se_code: data["name"] for se_code, data in animal_mapping.items()
    }
    ANIMAL_NUMBER_VARS = {
        se_code: data.get("animal_number_se_codes", [])
        for se_code, data in animal_mapping.items()
    }
    LU_CONVERSION_FACTORS = {
        se_code: data.get("lu_conversion_factor", 1.0)
        for se_code, data in animal_mapping.items()
    }

    logger.info(f"Loaded mapping for {len(animal_mapping)} FADN livestock categories")

    logger.info(f"Loading FADN data from {fadn_data_path}")
    fadn_df = pd.read_csv(fadn_data_path)

    logger.info(f"Loading yields from {yields_path}")
    yields_df = pd.read_csv(yields_path)

    hicp_values = load_hicp_data(hicp_path)
    ppp_rate = load_ppp_rate(ppp_path)

    costs_df = process_fadn_animal_costs(
        fadn_df,
        yields_df,
        animal_mapping,
        base_year,
        ppp_rate,
        hicp_values,
        years,
        livestock_specific_costs,
        shared_farm_costs,
        high_cost_threshold,
        grazing_cost_items,
    )

    if costs_df.empty:
        costs_df = pd.DataFrame(
            columns=[
                "product",
                f"cost_per_mt_usd_{base_year}",
                f"grazing_cost_per_mt_usd_{base_year}",
            ]
        )

    costs_df.to_csv(out_path, index=False)
    logger.info(f"Wrote cost data to {out_path}")


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
