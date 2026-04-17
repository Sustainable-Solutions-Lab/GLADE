# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve PPP (Purchasing Power Parity) conversion rates from the World Bank API.

This script fetches PPP conversion factors for the Eurozone from the World Bank
World Development Indicators database. PPP rates are used to convert EUR costs
to international $ (equivalent to USD in purchasing power terms), accounting for
price level differences between regions rather than using nominal exchange rates.

The script calculates an average PPP rate across major Eurozone economies and
across a configurable time period (default: 2015-2023).

Inputs
- snakemake.params.start_year: First year for averaging period
- snakemake.params.end_year: Last year for averaging period

Output
- snakemake.output.ppp: CSV with single row containing:
    avg_ppp_eur_per_intl_dollar (EUR per international $)

Notes
- Uses World Bank indicator PA.NUS.PPP (PPP conversion factor, GDP)
- Eurozone countries included: DEU, FRA, ITA, ESP, NLD, BEL, AUT, IRL, PRT, GRC, FIN
- Returns average PPP rate across countries and years
- No API key required for World Bank API
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Major Eurozone economies for representative PPP calculation
EUROZONE_COUNTRIES = [
    "DEU",  # Germany
    "FRA",  # France
    "ITA",  # Italy
    "ESP",  # Spain
    "NLD",  # Netherlands
    "BEL",  # Belgium
    "AUT",  # Austria
    "IRL",  # Ireland
    "PRT",  # Portugal
    "GRC",  # Greece
    "FIN",  # Finland
]


def fetch_eurozone_ppp(start_year: int, end_year: int) -> float:
    """
    Fetch PPP conversion factors for Eurozone countries from World Bank API.

    Returns average PPP rate (EUR per international $) across countries and years.
    Raises exception if API fails or insufficient data is available.
    """
    logger.info(
        f"Fetching PPP rates from World Bank API for {len(EUROZONE_COUNTRIES)} "
        f"Eurozone countries, years {start_year}-{end_year}"
    )

    # World Bank API endpoint
    # Indicator: PA.NUS.PPP = PPP conversion factor, GDP (LCU per international $)
    countries_str = ";".join(EUROZONE_COUNTRIES)
    url = (
        f"https://api.worldbank.org/v2/country/{countries_str}/indicator/PA.NUS.PPP"
        f"?date={start_year}:{end_year}&format=json&per_page=1000"
    )

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    if len(data) < 2 or not data[1]:
        raise ValueError("No PPP data returned from World Bank API")

    # Parse response
    records = []
    for item in data[1]:
        if item["value"] is not None:
            records.append(
                {
                    "country": item["countryiso3code"],
                    "country_name": item["country"]["value"],
                    "year": int(item["date"]),
                    "ppp": float(item["value"]),
                }
            )

    if not records:
        raise ValueError("No valid PPP data found in World Bank response")

    df = pd.DataFrame(records)
    logger.info(f"Fetched {len(df)} country-year observations")

    # Log sample data for verification
    if not df.empty:
        latest_year = df["year"].max()
        sample = df[df["year"] == latest_year].sort_values("country")
        logger.info(f"Sample PPP rates for {latest_year}:")
        for _, row in sample.iterrows():
            logger.info(f"  {row['country_name']:20s}: {row['ppp']:.4f}")

    # Calculate average across all countries and years
    avg_ppp = df["ppp"].mean()

    # Also show yearly averages for transparency
    yearly_avg = df.groupby("year")["ppp"].mean()
    logger.info("Yearly average PPP rates (EUR per international $):")
    for year in sorted(yearly_avg.index):
        logger.info(f"  {year}: {yearly_avg[year]:.4f}")

    logger.info(
        f"Overall average PPP rate {start_year}-{end_year}: {avg_ppp:.4f} EUR per international $"
    )
    logger.info(f"This implies: 1 EUR = {1/avg_ppp:.4f} international $")

    return avg_ppp


def main():
    start_year: int = int(snakemake.params.start_year)  # type: ignore[name-defined]
    end_year: int = int(snakemake.params.end_year)  # type: ignore[name-defined]
    out_path: str = str(snakemake.output.ppp)  # type: ignore[name-defined]

    # Fetch PPP rate
    avg_ppp = fetch_eurozone_ppp(start_year, end_year)

    # Write single-row CSV with the average PPP rate
    result_df = pd.DataFrame(
        [
            {
                "avg_ppp_eur_per_intl_dollar": avg_ppp,
                "start_year": start_year,
                "end_year": end_year,
            }
        ]
    )

    result_df.to_csv(out_path, index=False)
    logger.info(f"Wrote PPP rate to {out_path}")


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
