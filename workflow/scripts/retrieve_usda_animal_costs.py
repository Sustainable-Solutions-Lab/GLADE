# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve USDA production cost data for animal products and compute inflation-adjusted
averages in base year dollars per tonne of product.

This script downloads CSV files from USDA ERS Milk Cost of Production data,
extracting production costs while excluding feed costs that are modeled endogenously.

For detailed documentation on cost modeling and data sources, see
docs/livestock.rst (Production Costs section) and docs/data_sources.rst.

Inputs
- snakemake.input.sources: CSV with columns (product, url, notes)
- snakemake.input.cpi: CSV with CPI-U annual averages (year, cpi_u)
- snakemake.params.base_year: base year for inflation adjustment

Output
- snakemake.output.costs: CSV with columns:
    product,cost_per_mt_usd_{base_year}

Notes
- Costs included: Labor, veterinary, energy, housing, depreciation, interest
- Costs explicitly EXCLUDED: Feed costs (modeled endogenously), land rent
- Inflation-adjusted using US CPI-U from BLS
- Costs converted from per-cwt to per-tonne-product

USDA Milk Data Structure:
- Format: CSV with columns: Commodity, Category, Item, Units, Size, Region, Country, Year, Value, Survey base year
- Units: dollars per hundredweight sold ($/cwt)
- Categories: "Operating costs" and "Allocated overhead"
- Region: Filter to "U.S. total" for national averages

Conversion:
- 1 cwt (hundredweight) = 100 lbs = 45.36 kg
- Cost per Mt = Cost per cwt * 22.05
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Conversion factor: cwt to Mt
CWT_TO_KG = 45.3592  # 1 cwt (hundredweight) = 100 lbs = 45.36 kg
MT_PER_CWT = 1000 / CWT_TO_KG  # For converting $/cwt to $/Mt: ~22.05


def load_cpi_data(cpi_path: str) -> dict[int, float]:
    """Load CPI-U annual averages from CSV.

    Returns dict mapping year -> CPI value.
    """
    df = pd.read_csv(cpi_path)
    if not {"year", "cpi_u"}.issubset(df.columns):
        raise ValueError("CPI CSV must have columns: year, cpi_u")

    cpi_values = df.set_index("year")["cpi_u"].to_dict()
    logger.info(
        f"Loaded CPI data for years {min(cpi_values.keys())}-{max(cpi_values.keys())}"
    )
    return cpi_values


def inflate_to_base_year(
    value: float, year: int, base_year: int, cpi_values: dict[int, float]
) -> float:
    """Adjust nominal USD value to base year dollars using CPI."""
    if year not in cpi_values:
        raise ValueError(f"No CPI data for year {year}")
    if base_year not in cpi_values:
        raise ValueError(f"No CPI data for base year {base_year}")
    return value * (cpi_values[base_year] / cpi_values[year])


def fetch_csv(url: str, timeout_seconds: int = 120) -> pd.DataFrame:
    """Download CSV file from URL and return as DataFrame with robust retries."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    # Configure robust retry strategy
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,  # wait 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    http = requests.Session()
    http.mount("https://", adapter)
    http.mount("http://", adapter)

    try:
        response = http.get(url, timeout=timeout_seconds)  # Increased timeout
        response.raise_for_status()
        # Read directly from response content
        from io import StringIO

        return pd.read_csv(StringIO(response.text))
    except Exception as e:
        logger.error(f"Failed to download from {url} after retries: {e}")
        logger.error(
            "Please manually download the file and check if the URL is accessible."
        )
        raise


def process_livestock_costs(
    df: pd.DataFrame,
    product_name: str,
    base_year: int,
    cpi_values: dict[int, float],
    units_to_mt_factor: float,
    region_filter: str,
    categories_filter: list[str],
    years: list[int],
    include_items: list[str],
    exclude_items: list[str],
    grazing_cost_items: list[str] | None = None,
) -> tuple[float, float]:
    """
    Process USDA livestock cost of production data (dairy, hogs, cattle).

    Returns tuple (production_cost_per_mt, grazing_cost_per_mt) in base year USD.
    Production cost excludes feed. Grazing cost is calculated separately.
    """
    if grazing_cost_items is None:
        grazing_cost_items = []

    logger.info(
        f"Processing {product_name} data from CSV with {len(df)} rows, "
        f"filtering by region '{region_filter}' and categories {categories_filter}"
    )

    # Filter to specific region and relevant years
    df = df[
        (df["Region"] == region_filter)
        & (df["Year"].isin(years))
        & (df["Units"].str.contains("dollars per", na=False))
    ].copy()

    if df.empty:
        logger.warning(
            f"No data for {region_filter} in specified years for {product_name}"
        )
        return 0.0, 0.0

    # Filter to cost categories we want (Operating costs and Allocated overhead)
    df = df[df["Category"].isin(categories_filter)].copy()

    # Sum costs by year
    yearly_costs = []
    yearly_grazing_costs = []

    for year in years:
        year_df = df[df["Year"] == year].copy()
        if year_df.empty:
            continue

        total_cost = 0.0
        grazing_cost = 0.0

        for _, row in year_df.iterrows():
            item = row["Item"]
            value = float(row["Value"])

            # Check for grazing cost first (explicitly defined)
            if any(g_item in item for g_item in grazing_cost_items):
                grazing_cost += value
                # Don't add to general production cost if it's a grazing item
                # (even if it matches include_items, though it usually won't)
                continue

            if any(excl in item for excl in exclude_items):
                continue

            # Check if it's an explicitly included item or a generic "Other" item
            if (
                not any(incl in item for incl in include_items)
                and "Other" not in item
                and "operating costs" not in item
            ):
                continue

            total_cost += value

        if total_cost > 0 or grazing_cost > 0:
            inflated_cost = inflate_to_base_year(
                total_cost, int(year), base_year, cpi_values
            )
            inflated_grazing_cost = inflate_to_base_year(
                grazing_cost, int(year), base_year, cpi_values
            )

            yearly_costs.append(inflated_cost)
            yearly_grazing_costs.append(inflated_grazing_cost)

            logger.info(
                f"  Year {year}: Cost ${total_cost:.2f} (Grazing ${grazing_cost:.2f}) nominal -> "
                f"${inflated_cost:.2f} (Grazing ${inflated_grazing_cost:.2f}) base year"
            )

    if not yearly_costs:
        logger.warning(f"No valid cost data after filtering for {product_name}")
        return 0.0, 0.0

    avg_cost_per_unit = sum(yearly_costs) / len(yearly_costs)
    cost_per_mt = avg_cost_per_unit * units_to_mt_factor

    avg_grazing_cost_per_unit = sum(yearly_grazing_costs) / len(yearly_grazing_costs)
    grazing_cost_per_mt = avg_grazing_cost_per_unit * units_to_mt_factor

    logger.info(
        f"{product_name.capitalize()}: {len(yearly_costs)} years averaged, "
        f"${avg_cost_per_unit:.2f}/unit -> ${cost_per_mt:.2f}/Mt (Grazing: ${grazing_cost_per_mt:.2f}/Mt)"
    )

    return cost_per_mt, grazing_cost_per_mt


def main():
    sources_path: str = snakemake.input.sources  # type: ignore[name-defined]
    cpi_path: str = snakemake.input.cpi  # type: ignore[name-defined]
    base_year: int = int(snakemake.params.base_year)  # type: ignore[name-defined]
    cost_params: dict = snakemake.params.cost_params  # type: ignore[name-defined]
    averaging_period: dict = snakemake.params.averaging_period  # type: ignore[name-defined]
    out_path: str = snakemake.output.costs  # type: ignore[name-defined]

    # Extract params
    years = list(
        range(averaging_period["start_year"], averaging_period["end_year"] + 1)
    )
    include_items = cost_params["include_items"]
    exclude_items = cost_params["exclude_items"]
    grazing_cost_items = cost_params.get("grazing_cost_items", [])
    dressed_weight_kg = cost_params["dressed_weight_kg_per_head"]
    timeout = int(cost_params.get("request_timeout_seconds", 120))

    # Load CPI data for inflation adjustment
    cpi_values = load_cpi_data(cpi_path)

    # Load USDA source URLs
    sources_df = pd.read_csv(sources_path, comment="#")
    if not {"product", "url", "units", "region", "categories"}.issubset(
        sources_df.columns
    ):
        raise ValueError(
            "Sources file must have columns: product, url, units, region, categories"
        )

    results = []

    for _, row in sources_df.iterrows():
        product = row["product"]
        url = row["url"]
        units = row["units"]
        region = row["region"]
        categories = row["categories"].split(";")  # Split categories string into a list

        logger.info(f"Fetching USDA data for {product} from {url}")

        try:
            df = fetch_csv(url, timeout_seconds=timeout)

            # Dynamically determine conversion factor based on units
            if units == "cwt" or units == "cwt_gain":
                # 1 cwt = 45.36 kg. Cost per Mt = Cost per cwt * (1000 / 45.36)
                units_to_mt_factor = MT_PER_CWT
            elif units == "head":
                weight = dressed_weight_kg.get(product)
                if weight:
                    # Convert $/head to $/Mt: Cost/Head * (1000 / weight_per_head)
                    units_to_mt_factor = 1000.0 / float(weight)
                else:
                    logger.warning(
                        f"Unknown dressed weight for '{product}', using 1.0 as conversion factor"
                    )
                    units_to_mt_factor = 1.0  # Placeholder
            else:
                logger.warning(
                    f"Unknown units '{units}' for product '{product}', using 1.0 as conversion factor"
                )
                units_to_mt_factor = 1.0  # Placeholder

            cost_per_mt, grazing_cost_per_mt = process_livestock_costs(
                df,
                product,
                base_year,
                cpi_values,
                units_to_mt_factor,
                region,
                categories,
                years,
                include_items,
                exclude_items,
                grazing_cost_items,
            )

            if cost_per_mt > 0 or grazing_cost_per_mt > 0:
                results.append(
                    {
                        "product": product,
                        f"cost_per_mt_usd_{base_year}": cost_per_mt,
                        f"grazing_cost_per_mt_usd_{base_year}": grazing_cost_per_mt,
                    }
                )

                logger.info(
                    f"{product}: Production ${cost_per_mt:.2f}/Mt, Grazing ${grazing_cost_per_mt:.2f}/Mt"
                )
            else:
                logger.warning(f"No valid cost data for {product}")

        except Exception as e:
            logger.error(f"Failed to process {product}: {e}", exc_info=True)

    # Write output
    if results:
        out_df = pd.DataFrame(results)
        out_df.to_csv(out_path, index=False)
        logger.info(f"Wrote cost data for {len(results)} products to {out_path}")
    else:
        # Write empty file with correct columns
        out_df = pd.DataFrame(
            columns=[
                "product",
                f"cost_per_mt_usd_{base_year}",
                f"grazing_cost_per_mt_usd_{base_year}",
            ]
        )
        out_df.to_csv(out_path, index=False)
        logger.warning("No products processed, wrote empty output file")


if __name__ == "__main__":
    # Configure logging
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
