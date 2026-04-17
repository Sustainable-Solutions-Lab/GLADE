# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve GDP per capita data from the IMF World Economic Outlook API.

This script fetches GDP per capita (current prices, USD) from the IMF
DataMapper API for specified countries and year. The data is used for
multi-objective health clustering to group countries with similar
economic development levels.

Missing data is handled as follows:
- Missing years: Use nearest available year within ±2 years
- Missing countries: Impute using mean GDP of countries in the same
  UN M49 sub-region

Inputs
- snakemake.input.m49: Path to M49 regional codes CSV
- snakemake.params.countries: List of ISO3 country codes
- snakemake.params.year: Reference year for GDP data

Output
- snakemake.output.gdp: CSV with columns: iso3, gdp_per_capita, year
  Contains one row per requested country (complete data guaranteed)

Notes
- Uses IMF indicator NGDPDPC (GDP per capita, current prices, USD)
- Source: International Monetary Fund, World Economic Outlook Database
- Terms of use: https://www.imf.org/en/about/copyright-and-terms#data
- No API key required
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# IMF DataMapper API base URL
IMF_API_BASE = "https://www.imf.org/external/datamapper/api/v1"

# IMF indicator for GDP per capita (current prices, USD)
INDICATOR = "NGDPDPC"


def load_regional_groupings(m49_path: str) -> dict[str, str]:
    """
    Load UN M49 regional groupings.

    Returns mapping from ISO3 country code to sub-region name.
    """
    df = pd.read_csv(m49_path, sep=";", comment="#", encoding="utf-8-sig")
    # Create mapping: ISO3 -> Sub-region Name
    return dict(zip(df["ISO-alpha3 Code"], df["Sub-region Name"]))


def fetch_gdp_per_capita(
    countries: list[str],
    year: int,
    regional_groupings: dict[str, str],
) -> pd.DataFrame:
    """
    Fetch GDP per capita for specified countries from IMF API.

    Missing data handling:
    - Missing years: Use nearest available year within ±2 years
    - Missing countries: Impute using mean GDP of same UN M49 sub-region

    Parameters
    ----------
    countries : list[str]
        List of ISO3 country codes (e.g., ["USA", "DEU", "CHN"])
    year : int
        Reference year for GDP data
    regional_groupings : dict[str, str]
        Mapping from ISO3 code to UN M49 sub-region name

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: iso3, gdp_per_capita, year, imputed
        Contains exactly one row per requested country (complete data).

    Raises
    ------
    ValueError
        If API returns no valid data
    requests.HTTPError
        If API request fails
    """
    logger.info(
        f"Fetching GDP per capita from IMF API for {len(countries)} countries, year {year}"
    )

    # Fetch all countries at once (API returns all when no countries specified)
    # This avoids URL length limits when requesting many countries
    url = f"{IMF_API_BASE}/{INDICATOR}"

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    data = response.json()

    # Parse response structure:
    # {"values": {"NGDPDPC": {"USA": {"2023": 80412.5, ...}, ...}}}
    if "values" not in data or INDICATOR not in data["values"]:
        raise ValueError(f"No GDP data returned from IMF API for indicator {INDICATOR}")

    indicator_data = data["values"][INDICATOR]

    # First pass: collect direct data and identify missing countries
    records = []
    year_str = str(year)
    missing_countries = []

    for iso3 in countries:
        if iso3 not in indicator_data:
            missing_countries.append(iso3)
            continue

        yearly_values = indicator_data[iso3]

        if year_str in yearly_values and yearly_values[year_str] is not None:
            records.append(
                {
                    "iso3": iso3,
                    "gdp_per_capita": float(yearly_values[year_str]),
                    "year": year,
                    "imputed": False,
                }
            )
        else:
            # Try nearby years if exact year not available
            found = False
            for offset in [1, -1, 2, -2]:
                alt_year = str(year + offset)
                if alt_year in yearly_values and yearly_values[alt_year] is not None:
                    logger.info(f"Using {alt_year} data for {iso3} (no {year} data)")
                    records.append(
                        {
                            "iso3": iso3,
                            "gdp_per_capita": float(yearly_values[alt_year]),
                            "year": year + offset,
                            "imputed": False,
                        }
                    )
                    found = True
                    break
            if not found:
                missing_countries.append(iso3)

    if not records:
        raise ValueError(
            f"No valid GDP data found for any country in year {year} or nearby years"
        )

    df = pd.DataFrame(records)
    logger.info(
        f"Retrieved GDP per capita for {len(df)} of {len(countries)} countries from IMF"
    )

    # Second pass: impute missing countries using regional means
    if missing_countries:
        df = _impute_missing_countries(df, missing_countries, regional_groupings, year)

    # Log summary statistics
    n_imputed = df["imputed"].sum()
    logger.info(
        f"Final dataset: {len(df)} countries "
        f"({n_imputed} imputed from regional means)"
    )
    logger.info(
        f"GDP per capita range: ${df['gdp_per_capita'].min():,.0f} - "
        f"${df['gdp_per_capita'].max():,.0f}"
    )
    logger.info(f"GDP per capita median: ${df['gdp_per_capita'].median():,.0f}")

    return df


def _impute_missing_countries(
    df: pd.DataFrame,
    missing_countries: list[str],
    regional_groupings: dict[str, str],
    year: int,
) -> pd.DataFrame:
    """
    Impute GDP for missing countries using regional means.

    For each missing country, compute the mean GDP per capita of all
    countries in the same UN M49 sub-region. If no regional data is
    available, fall back to global median.
    """
    # Add region column to existing data
    df = df.copy()
    df["region"] = df["iso3"].map(regional_groupings)

    # Compute regional means
    regional_means = df.groupby("region")["gdp_per_capita"].mean()

    # Global median as fallback
    global_median = df["gdp_per_capita"].median()

    imputed_records = []
    for iso3 in missing_countries:
        region = regional_groupings.get(iso3)

        if region and region in regional_means.index:
            gdp_value = regional_means[region]
            logger.info(
                f"Imputed {iso3} GDP from {region} regional mean: ${gdp_value:,.0f}"
            )
        else:
            gdp_value = global_median
            logger.warning(
                f"Imputed {iso3} GDP from global median: ${gdp_value:,.0f} "
                f"(no regional data for {region})"
            )

        imputed_records.append(
            {
                "iso3": iso3,
                "gdp_per_capita": gdp_value,
                "year": year,
                "imputed": True,
            }
        )

    if imputed_records:
        df = pd.concat([df, pd.DataFrame(imputed_records)], ignore_index=True)

    # Drop the temporary region column
    df = df.drop(columns=["region"])

    return df


def main():
    countries: list[str] = list(snakemake.params.countries)  # type: ignore[name-defined]
    year: int = int(snakemake.params.year)  # type: ignore[name-defined]
    m49_path: str = str(snakemake.input.m49)  # type: ignore[name-defined]
    out_path: str = str(snakemake.output.gdp)  # type: ignore[name-defined]

    # Load regional groupings for imputation
    regional_groupings = load_regional_groupings(m49_path)

    df = fetch_gdp_per_capita(countries, year, regional_groupings)
    df.to_csv(out_path, index=False)
    logger.info(f"Wrote GDP per capita data to {out_path}")


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
