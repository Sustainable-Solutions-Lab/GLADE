# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve CPI-U (Consumer Price Index for All Urban Consumers) annual averages
from the Bureau of Labor Statistics (BLS) Public Data API.

This data is used for inflation adjustment throughout the workflow to convert
nominal USD values to real USD values for a specified base year. The CPI data
is stored in a shared location (processing/shared/cpi_annual.csv) and can be
reused by any workflow script requiring inflation adjustment.

For detailed documentation on CPI data sourcing and usage, see:
- docs/data_sources.rst (BLS Consumer Price Index section)
- docs/crop_production.rst (Production Costs section)

Inputs
- snakemake.params.start_year: First year to retrieve
- snakemake.params.end_year: Last year to retrieve (typically config['currency_base_year'])

Output
- snakemake.output.cpi: CSV with columns (year, cpi_u)

Notes
- Uses BLS series CUUR0000SA0 (CPI-U, All items, U.S. city average, not seasonally adjusted)
- Computes annual averages from monthly data
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def fetch_cpi_data(start_year: int, end_year: int) -> pd.DataFrame:
    """Fetch CPI-U annual averages from BLS API.

    Uses series CUUR0000SA0 (CPI-U, All items, U.S. city average, not seasonally adjusted).
    Returns DataFrame with columns: year, cpi_u

    Source: Bureau of Labor Statistics Public Data API
    """
    # BLS API v2.0 - no registration required for limited requests
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    # Series ID: CUUR0000SA0 = CPI-U, All items, U.S. city average
    series_id = "CUUR0000SA0"

    payload = {
        "seriesid": [series_id],
        "startyear": str(start_year),
        "endyear": str(end_year),
    }

    logger.info(f"Fetching CPI-U data from BLS API for years {start_year}-{end_year}")

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data["status"] != "REQUEST_SUCCEEDED":
        raise RuntimeError(
            f"BLS API request failed: {data.get('message', 'Unknown error')}"
        )

    # Parse response and compute annual averages
    series_data = data["Results"]["series"][0]["data"]
    yearly_values = {}

    for entry in series_data:
        year = int(entry["year"])
        value = float(entry["value"])

        if year not in yearly_values:
            yearly_values[year] = []
        yearly_values[year].append(value)

    # Compute annual averages (monthly data -> annual average)
    rows = []
    for year in sorted(yearly_values.keys()):
        cpi_u = sum(yearly_values[year]) / len(yearly_values[year])
        rows.append({"year": year, "cpi_u": cpi_u})
        logger.info(f"  {year}: CPI-U = {cpi_u:.2f}")

    return pd.DataFrame(rows)


def main():
    start_year: int = int(snakemake.params.start_year)  # type: ignore[name-defined]
    end_year: int = int(snakemake.params.end_year)  # type: ignore[name-defined]
    out_path: str = str(snakemake.output.cpi)  # type: ignore[name-defined]

    df = fetch_cpi_data(start_year, end_year)
    df.to_csv(out_path, index=False)
    logger.info(f"Wrote CPI data to {out_path}")


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
