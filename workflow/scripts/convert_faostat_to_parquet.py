# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Convert a FAOSTAT bulk CSV (inside a zip) to Parquet with clean dtypes.

Normalises integer code columns (Area Code, Item Code, Element Code, Year,
Year Code) so downstream consumers never need to deal with float-encoded
integers.  Also strips surrounding quotes from ``Area Code (M49)`` (e.g.
``'004'`` → ``4``).
"""

from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Columns that should be stored as integers.  Use pandas nullable Int64 to
# handle any rows where these columns contain NaN.
_CODE_COLUMNS = ["Area Code", "Item Code", "Element Code", "Year", "Year Code"]


def _to_nullable_int(series: pd.Series) -> pd.Series:
    """Convert a series to nullable Int64, handling float intermediates."""
    numeric = pd.to_numeric(series, errors="coerce")
    mask = numeric.isna()
    # Fill NaN with 0 to allow safe int conversion, then restore NaN via mask
    filled = numeric.fillna(0).astype(np.int64)
    result = filled.astype("Int64")
    result[mask] = pd.NA
    return result


def convert(zip_path: str, parquet_path: str) -> None:
    """Extract CSV from *zip_path*, normalise, and write to *parquet_path*."""
    # Extract CSV to a temp file (unzip -p streams to stdout)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=True) as tmp:
        with open(tmp.name, "wb") as out:
            subprocess.run(
                ["unzip", "-p", zip_path],
                stdout=out,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        df = pd.read_csv(tmp.name, encoding="latin-1", low_memory=False)

    # Normalise code columns to nullable integers
    for col in _CODE_COLUMNS:
        if col in df.columns:
            df[col] = _to_nullable_int(df[col])

    # Clean Area Code (M49): strip quotes and convert to int
    m49_col = "Area Code (M49)"
    if m49_col in df.columns:
        raw = df[m49_col].astype(str).str.strip().str.strip("'\"")
        df[m49_col] = _to_nullable_int(raw)

    Path(parquet_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_path, index=False)


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    convert(
        zip_path=str(snakemake.input[0]),  # type: ignore[name-defined]
        parquet_path=str(snakemake.output[0]),  # type: ignore[name-defined]
    )
