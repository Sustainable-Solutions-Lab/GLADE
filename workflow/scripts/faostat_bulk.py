# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for reading FAOSTAT bulk Parquet downloads.

FAOSTAT bulk files have fixed columns:
    Area Code, Area Code (M49), Area, Item Code, Item, Element Code,
    Element, Year, Unit, Value, Flag

Code columns (Area Code, Item Code, Element Code, Year) are stored as
nullable integers in the Parquet files, so no string normalisation is
needed.

All functions operate on these standardised column names.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Proxy mapping for countries missing from FAOSTAT FBS data.
# Maps ISO3 codes to ordered list of fallback countries with similar
# dietary patterns.
FBS_COUNTRY_FALLBACKS: dict[str, list[str]] = {
    "ASM": ["WSM", "USA"],  # American Samoa -> Samoa / USA
    "BEN": ["TGO", "BFA", "NGA"],  # Benin -> Togo / Burkina Faso / Nigeria
    "BRN": ["MYS", "SGP"],  # Brunei -> Malaysia
    "BTN": ["NPL", "IND"],  # Bhutan -> Nepal
    "CAF": ["TCD", "CMR", "COG"],  # Central African Republic
    "CUB": ["DOM", "JAM"],  # Cuba -> Dominican Republic / Jamaica
    "ERI": ["ETH"],  # Eritrea -> Ethiopia
    "GNQ": ["GAB", "CMR"],  # Eq. Guinea -> Gabon
    "GUF": ["GUY", "SUR", "FRA"],  # Fr. Guiana
    "PRI": ["USA", "DOM"],  # Puerto Rico
    "PSE": ["JOR", "ISR"],  # Palestine
    "SDN": ["EGY", "ETH"],  # Sudan -> Egypt / Ethiopia
    "SSD": ["SDN", "ETH"],  # South Sudan
    "SOM": ["ETH"],  # Somalia
    "TWN": ["CHN"],  # Taiwan
    "XKX": ["SRB", "ALB"],  # Kosovo
    "ESH": ["MAR", "MRT"],  # Western Sahara
    "JPN": ["KOR", "CHN"],  # Japan -> South Korea / China
    "MLI": ["SEN", "BFA", "NER"],  # Mali
    "BDI": ["RWA", "TZA"],  # Burundi
    "COD": ["COG", "AGO"],  # DR Congo
    "SYR": ["JOR", "LBN"],  # Syria
    "TCD": ["SDN", "NER", "CMR"],  # Chad
    "TGO": ["GHA", "BFA"],  # Togo -> Ghana / Burkina Faso
    "VEN": ["COL", "BRA"],  # Venezuela
    "YEM": ["OMN", "SAU"],  # Yemen
}


def load_bulk(path: str | object) -> pd.DataFrame:
    """Read a FAOSTAT bulk Parquet file."""
    return pd.read_parquet(str(path))


def load_m49_to_iso3(m49_csv_path: str | object) -> dict[int, str]:
    """Build a mapping from M49 numeric code (int) to ISO3 alpha code.

    Uses the project's ``data/curated/M49-codes.csv`` (semicolon-separated, with
    comment lines starting with ``#``).
    """
    df = pd.read_csv(str(m49_csv_path), sep=";", encoding="utf-8-sig", comment="#")
    valid = df.dropna(subset=["ISO-alpha3 Code", "M49 Code"])
    keys = valid["M49 Code"].apply(lambda x: int(float(x)))
    values = valid["ISO-alpha3 Code"].astype(str)
    return dict(zip(keys, values))


def add_iso3_column(df: pd.DataFrame, m49_to_iso3: dict[int, str]) -> pd.DataFrame:
    """Add an ``iso3`` column by mapping ``Area Code (M49)`` through *m49_to_iso3*."""
    m49_col = "Area Code (M49)"
    if m49_col not in df.columns:
        raise KeyError(
            f"Expected column '{m49_col}' in bulk data; got {df.columns.tolist()}"
        )
    df = df.copy()
    df["iso3"] = df[m49_col].map(m49_to_iso3)
    return df


def get_element_map(df: pd.DataFrame) -> dict[str, int]:
    """Return ``{element_label: element_code}`` from a bulk DataFrame.

    .. warning::
       When multiple element codes share the same label (e.g. QCL has
       "Production" for both code 5510 and 5513), only the last code
       survives.  A warning is logged for each collision.
    """
    sub = df[["Element Code", "Element"]].drop_duplicates()
    labels = sub["Element"].str.strip()
    codes = sub["Element Code"]
    result = dict(zip(labels, codes))
    if len(result) < len(sub):
        for label in labels[labels.duplicated(keep=False)].unique():
            conflicting = codes[labels == label].unique()
            logger.warning(
                "Element label %r maps to multiple codes: %s; keeping last",
                label,
                ", ".join(str(c) for c in conflicting),
            )
    return result


def get_item_map(df: pd.DataFrame) -> dict[str, int]:
    """Return ``{item_label: item_code}`` from a bulk DataFrame.

    .. warning::
       When multiple item codes share the same label, only the last code
       survives.  A warning is logged for each collision.
    """
    sub = df[["Item Code", "Item"]].drop_duplicates()
    labels = sub["Item"].str.strip()
    codes = sub["Item Code"]
    result = dict(zip(labels, codes))
    if len(result) < len(sub):
        for label in labels[labels.duplicated(keep=False)].unique():
            conflicting = codes[labels == label].unique()
            logger.warning(
                "Item label %r maps to multiple codes: %s; keeping last",
                label,
                ", ".join(str(c) for c in conflicting),
            )
    return result


def filter_bulk(
    df: pd.DataFrame,
    *,
    element_codes: list[int] | None = None,
    item_codes: list[int] | None = None,
    years: list[int] | None = None,
    iso3_codes: list[str] | None = None,
) -> pd.DataFrame:
    """Filter a bulk DataFrame and coerce ``Value`` to numeric.

    All filter arguments are optional; when *None* the corresponding column
    is not filtered.
    """
    mask = pd.Series(True, index=df.index)
    if element_codes is not None:
        mask &= df["Element Code"].isin(element_codes)
    if item_codes is not None:
        mask &= df["Item Code"].isin(item_codes)
    if years is not None:
        mask &= df["Year"].isin(years)
    if iso3_codes is not None:
        if "iso3" not in df.columns:
            raise KeyError(
                "DataFrame has no 'iso3' column; call add_iso3_column() first"
            )
        mask &= df["iso3"].isin(iso3_codes)

    result = df.loc[mask].copy()
    result["Value"] = pd.to_numeric(result["Value"], errors="coerce")
    return result
