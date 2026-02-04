# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for reading FAOSTAT bulk CSV downloads.

FAOSTAT bulk CSVs have fixed columns:
    Area Code, Area Code (M49), Area, Item Code, Item, Element Code,
    Element, Year, Unit, Value, Flag

All functions operate on these standardised column names.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _int_str(x: object) -> str:
    """Convert a (possibly float) code value to an integer string.

    FAOSTAT bulk CSVs store codes as integers, but pandas may read them
    as float64 when NaN values are present (e.g. ``5513.0``).  This
    helper normalises any numeric-like value to ``"5513"``.
    """
    try:
        return str(int(float(x)))
    except (ValueError, TypeError):
        return str(x).strip()


def load_bulk_csv(path: str | object) -> pd.DataFrame:
    """Read a FAOSTAT bulk CSV (latin-1 encoded, comma-separated)."""
    return pd.read_csv(str(path), encoding="latin-1", low_memory=False)


def load_m49_to_iso3(m49_csv_path: str | object) -> dict[str, str]:
    """Build a mapping from M49 numeric code (str) to ISO3 alpha code.

    Uses the project's ``data/M49-codes.csv`` (semicolon-separated, with
    comment lines starting with ``#``).
    """
    df = pd.read_csv(str(m49_csv_path), sep=";", encoding="utf-8-sig", comment="#")
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        iso3 = row["ISO-alpha3 Code"]
        m49 = row["M49 Code"]
        if pd.notna(iso3) and pd.notna(m49):
            # Normalise M49 to a plain integer string (no leading zeros, no .0)
            mapping[str(int(float(m49)))] = str(iso3)
    return mapping


def add_iso3_column(df: pd.DataFrame, m49_to_iso3: dict[str, str]) -> pd.DataFrame:
    """Add an ``iso3`` column by mapping ``Area Code (M49)`` through *m49_to_iso3*."""
    m49_col = "Area Code (M49)"
    if m49_col not in df.columns:
        raise KeyError(
            f"Expected column '{m49_col}' in bulk CSV; got {df.columns.tolist()}"
        )
    # Strip surrounding quotes that some FAOSTAT CSVs include, then
    # normalise to plain integer strings (e.g. "'004" -> "4") to match
    # the keys produced by load_m49_to_iso3().
    raw = df[m49_col].astype(str).str.strip().str.strip("'\"")
    normalised = raw.map(lambda v: _int_str(v) if v not in ("", "nan") else v)
    df = df.copy()
    df["iso3"] = normalised.map(m49_to_iso3)
    return df


def get_element_map(df: pd.DataFrame) -> dict[str, str]:
    """Return ``{element_label: element_code}`` from a bulk CSV."""
    sub = df[["Element Code", "Element"]].drop_duplicates()
    return dict(zip(sub["Element"].str.strip(), sub["Element Code"].map(_int_str)))


def get_item_map(df: pd.DataFrame) -> dict[str, str]:
    """Return ``{item_label: item_code}`` from a bulk CSV."""
    sub = df[["Item Code", "Item"]].drop_duplicates()
    return dict(zip(sub["Item"].str.strip(), sub["Item Code"].map(_int_str)))


def filter_bulk(
    df: pd.DataFrame,
    *,
    element_codes: list[str] | None = None,
    item_codes: list[str] | None = None,
    years: list[int | str] | None = None,
    iso3_codes: list[str] | None = None,
) -> pd.DataFrame:
    """Filter a bulk CSV and coerce ``Value`` to numeric.

    All filter arguments are optional; when *None* the corresponding column
    is not filtered.
    """
    mask = pd.Series(True, index=df.index)
    if element_codes is not None:
        codes = {_int_str(c) for c in element_codes}
        mask &= df["Element Code"].map(_int_str).isin(codes)
    if item_codes is not None:
        codes = {_int_str(c) for c in item_codes}
        mask &= df["Item Code"].map(_int_str).isin(codes)
    if years is not None:
        year_set = {_int_str(y) for y in years}
        mask &= df["Year"].map(_int_str).isin(year_set)
    if iso3_codes is not None:
        if "iso3" not in df.columns:
            raise KeyError(
                "DataFrame has no 'iso3' column; call add_iso3_column() first"
            )
        mask &= df["iso3"].isin(iso3_codes)

    result = df.loc[mask].copy()
    result["Value"] = pd.to_numeric(result["Value"], errors="coerce")
    return result
