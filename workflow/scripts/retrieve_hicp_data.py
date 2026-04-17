# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve HICP (Harmonized Index of Consumer Prices) annual averages for the EU
from Eurostat.

This data is used for EUR inflation adjustment when processing FADN cost data.
HICP is the EU equivalent of CPI and is used to adjust nominal EUR values to
real EUR values for a specified base year.

Inputs
- snakemake.params.start_year: First year to retrieve
- snakemake.params.end_year: Last year to retrieve (typically config['currency_base_year'])

Output
- snakemake.output.hicp: CSV with columns (year, hicp)

Notes
- Uses Eurostat HICP annual average index (2015=100)
- For the EU aggregate (EA - Euro area or EU27_2020 - European Union)
- No fallback: if API fails, the script fails cleanly
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def fetch_hicp_data_eurostat(start_year: int, end_year: int) -> pd.DataFrame:
    """
    Fetch HICP annual averages from Eurostat REST API.

    Uses the prc_hicp_aind dataset (HICP annual average index).
    Returns DataFrame with columns: year, hicp

    Raises exception if API fails or data is unavailable.
    """
    logger.info(f"Fetching HICP data from Eurostat API for {start_year}-{end_year}")

    # Eurostat Statistics API endpoint (JSON-stat format)
    # Dataset: prc_hicp_aind (HICP annual average index)
    # Geo: EU27_2020 (European Union - 27 countries from 2020)
    # COICOP: CP00 (All items HICP)
    # Unit: INX_A_AVG (Annual average index, 2015=100)
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "prc_hicp_aind?geo=EU27_2020&coicop=CP00&unit=INX_A_AVG"
    )

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Parse JSON-stat format
    values = data.get("value", {})
    if not values:
        raise ValueError("No HICP values found in Eurostat response")

    # Extract time dimension
    time_categories = data["dimension"]["time"]["category"]["index"]

    # Map values to years
    # Values are indexed by position in the multidimensional array
    # For this query, the dimensions are [freq, unit, coicop, geo, time]
    # with sizes [1, 1, 1, 1, n_years], so values are indexed 0, 1, 2, ..., n_years-1
    rows = []
    for year_str, time_idx in time_categories.items():
        year = int(year_str)
        if start_year <= year <= end_year and str(time_idx) in values:
            # The value index for single dimensions is just the time index
            hicp = float(values[str(time_idx)])
            rows.append({"year": year, "hicp": hicp})

    if not rows:
        raise ValueError(f"No HICP data found for years {start_year}-{end_year}")

    return pd.DataFrame(rows)


def main():
    start_year: int = int(snakemake.params.start_year)  # type: ignore[name-defined]
    end_year: int = int(snakemake.params.end_year)  # type: ignore[name-defined]
    out_path: str = str(snakemake.output.hicp)  # type: ignore[name-defined]

    # Fetch HICP data from Eurostat (fails cleanly if unavailable)
    df = fetch_hicp_data_eurostat(start_year, end_year)

    df.to_csv(out_path, index=False)
    logger.info(f"Wrote HICP data to {out_path}")


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
