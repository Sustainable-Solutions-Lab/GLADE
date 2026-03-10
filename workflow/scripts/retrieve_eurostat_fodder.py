# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve fodder crop production and area data from Eurostat (apro_cpsh1 dataset).

Fetches production and harvested area data for:
- G0000: Total green fodder
- G2100: Lucerne (alfalfa)
- G3000: Green maize (silage maize)

Averages over a configurable year range and maps Eurostat 2-letter country
codes to ISO3 using M49-codes.csv.

Output: CSV with columns (country, crop_code, production_1000t, area_1000ha)
"""

import logging

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Eurostat crop codes for fodder production
EUROSTAT_CROP_CODES = {
    "G0000": "total_green",
    "G2100": "lucerne",
    "G3000": "green_maize",
}

# EU/EFTA countries (Eurostat 2-letter codes)
EUROSTAT_GEO_CODES = [
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "EL",
    "ES",
    "FI",
    "FR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
    # EFTA
    "CH",
    "IS",
    "LI",
    "NO",
    # UK (historical data)
    "UK",
]


def _fetch_eurostat_apro_cpsh1(
    year_range: list[int], strucpro: str, value_column: str
) -> pd.DataFrame:
    """Fetch a single variable from the Eurostat apro_cpsh1 dataset.

    Parameters
    ----------
    year_range : [start, end]
    strucpro : Eurostat structural production code, e.g. ``"PR_HU_EU"``
        (production in 1000 t) or ``"AR"`` (harvested area in 1000 ha).
    value_column : Name for the value column in the returned DataFrame.

    Returns
    -------
    DataFrame with columns: geo, crop_code, year, *value_column*
    """
    start_year, end_year = year_range
    logger.info(
        "Fetching Eurostat apro_cpsh1 (%s) for %d-%d",
        strucpro,
        start_year,
        end_year,
    )

    base_url = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "apro_cpsh1"
    )
    params = {
        "crops": list(EUROSTAT_CROP_CODES.keys()),
        "geo": EUROSTAT_GEO_CODES,
        "time": [str(y) for y in range(start_year, end_year + 1)],
        "strucpro": strucpro,
    }

    response = requests.get(base_url, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()

    values = data.get("value", {})
    if not values:
        raise ValueError(
            f"No values found in Eurostat response for strucpro={strucpro}"
        )

    # Parse dimension indices
    dims = data["dimension"]
    dim_order = list(data["id"])
    dim_sizes = [data["size"][i] for i in range(len(dim_order))]

    # Build index maps for each dimension
    dim_indices = {}
    for dim_name in dim_order:
        categories = dims[dim_name]["category"]["index"]
        dim_indices[dim_name] = {v: k for k, v in categories.items()}

    rows = []
    for flat_idx_str, value in values.items():
        flat_idx = int(flat_idx_str)

        # Decode flat index to per-dimension indices
        indices = {}
        remainder = flat_idx
        for i in range(len(dim_order) - 1, -1, -1):
            indices[dim_order[i]] = remainder % dim_sizes[i]
            remainder //= dim_sizes[i]

        geo = dim_indices["geo"].get(indices["geo"], "")
        crop_code = dim_indices["crops"].get(indices["crops"], "")
        year_str = dim_indices["time"].get(indices["time"], "")

        if not year_str or not geo or not crop_code:
            continue

        rows.append(
            {
                "geo": geo,
                "crop_code": crop_code,
                "year": int(year_str),
                value_column: float(value),
            }
        )

    if not rows:
        raise ValueError(
            f"No data parsed from Eurostat response for strucpro={strucpro}"
        )

    return pd.DataFrame(rows)


def fetch_eurostat_fodder(year_range: list[int]) -> pd.DataFrame:
    """Fetch fodder production and area from Eurostat REST API (apro_cpsh1).

    Returns DataFrame with columns: geo, crop_code, year, production_1000t, area_1000ha
    """
    prod = _fetch_eurostat_apro_cpsh1(year_range, "PR_HU_EU", "production_1000t")
    area = _fetch_eurostat_apro_cpsh1(year_range, "AR", "area_1000ha")
    merged = prod.merge(area, on=["geo", "crop_code", "year"], how="outer")
    return merged


def average_over_years(df: pd.DataFrame) -> pd.DataFrame:
    """Average production and area over years per country-crop pair."""
    value_cols = [c for c in ("production_1000t", "area_1000ha") if c in df.columns]
    return df.groupby(["geo", "crop_code"])[value_cols].mean().reset_index()


def map_to_iso3(df: pd.DataFrame, m49_path: str) -> pd.DataFrame:
    """Map Eurostat 2-letter geo codes to ISO3 country codes."""
    m49 = pd.read_csv(m49_path, sep=";", comment="#")
    # Eurostat uses EL for Greece (ISO: GR)
    geo_to_iso2 = {g: g for g in df["geo"].unique()}
    geo_to_iso2["EL"] = "GR"
    geo_to_iso2["UK"] = "GB"

    iso2_to_iso3 = dict(
        zip(m49["ISO-alpha2 Code"].str.strip(), m49["ISO-alpha3 Code"].str.strip())
    )

    df = df.copy()
    df["iso2"] = df["geo"].map(geo_to_iso2)
    df["country"] = df["iso2"].map(iso2_to_iso3)

    unmapped = df[df["country"].isna()]["geo"].unique()
    if len(unmapped) > 0:
        logger.warning("Could not map Eurostat geo codes to ISO3: %s", unmapped)

    df = df.dropna(subset=["country"])
    out_cols = ["country", "crop_code"]
    for col in ("production_1000t", "area_1000ha"):
        if col in df.columns:
            out_cols.append(col)
    return df[out_cols]


def main():
    year_range = list(snakemake.params.baseline_year_range)  # type: ignore[name-defined]
    m49_path = str(snakemake.input.m49_codes)  # type: ignore[name-defined]
    out_path = str(snakemake.output[0])  # type: ignore[name-defined]

    df = fetch_eurostat_fodder(year_range)
    df = average_over_years(df)
    df = map_to_iso3(df, m49_path)

    df.to_csv(out_path, index=False)
    logger.info("Wrote Eurostat fodder production to %s (%d rows)", out_path, len(df))


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
