"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd

MONTH_COLUMNS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

MM3_TO_M3 = 1e6


def _read_excel_availability(path: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Appendix-VII", header=2)
    # Expect first row to contain column group names, second row month names
    if raw.empty:
        raise ValueError("Appendix-VII sheet appears empty")

    month_row = raw.iloc[1]
    base_row = raw.iloc[0]

    base_row.iloc[0]
    base_row.iloc[1]

    months = month_row.iloc[2:14].tolist()
    if any(not isinstance(m, str) for m in months):
        raise ValueError("Could not read month names from Appendix-VII")

    months = [m.strip().title()[:3] for m in months]
    if months != MONTH_COLUMNS:
        raise ValueError("Unexpected month order in Appendix-VII: " + ", ".join(months))

    data = raw.iloc[2:].copy()
    data = data.rename(
        columns={
            base_row.index[0]: "basin_id",
            base_row.index[1]: "basin_name",
        }
    )
    # Rename month columns by exact name mapping
    for idx, month in enumerate(MONTH_COLUMNS, start=2):
        col = raw.columns[idx]
        data = data.rename(columns={col: month})

    avg_col = raw.columns[14]
    data = data.rename(columns={avg_col: "Average"})

    data = data.dropna(subset=["basin_id"]).copy()
    data["basin_id"] = data["basin_id"].astype(int)
    return data[["basin_id", "basin_name", *MONTH_COLUMNS, "Average"]]


if __name__ == "__main__":
    shapefile: str = snakemake.input.shapefile  # type: ignore[name-defined]
    excel_path: str = snakemake.input.excel  # type: ignore[name-defined]
    output_csv: str = snakemake.output[0]  # type: ignore[name-defined]

    basin_gdf = gpd.read_file(shapefile)[
        ["BASIN_ID", "DRAINAGE", "AREA_CALC", "Population"]
    ]
    basin_gdf = basin_gdf.rename(
        columns={
            "BASIN_ID": "basin_id",
            "DRAINAGE": "drainage",
            "AREA_CALC": "area_km2",
            "Population": "population",
        }
    )

    excel_df = _read_excel_availability(excel_path)

    merged = basin_gdf.merge(excel_df, on="basin_id", how="left")
    if merged[MONTH_COLUMNS].isna().any().any():
        missing = merged[merged[MONTH_COLUMNS].isna().any(axis=1)]["basin_id"].tolist()
        raise ValueError(
            "Missing monthly availability for basins: "
            + ", ".join(str(b) for b in missing[:10])
        )

    records = []
    for month_idx, month in enumerate(MONTH_COLUMNS, start=1):
        month_values = merged[["basin_id", "drainage", "area_km2", "population"]].copy()
        month_values["month"] = month_idx
        month_values["blue_water_availability_m3"] = merged[month] * MM3_TO_M3
        records.append(month_values)

    out_df = pd.concat(records, ignore_index=True)
    out_df = out_df.sort_values(["basin_id", "month"]).reset_index(drop=True)

    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_csv, index=False)
