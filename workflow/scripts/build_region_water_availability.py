"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

MONTH_LENGTHS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)
MONTH_ENDS = np.cumsum(MONTH_LENGTHS)
DAYS_IN_YEAR = float(MONTH_ENDS[-1])


def _month_index_for_day(day: float) -> int:
    """Return month index (0-based) for day in [0, 365)."""
    # add tiny epsilon to avoid edge cases at month boundaries
    return int(np.searchsorted(MONTH_ENDS, day + 1e-9))


def compute_month_overlaps(start_day: float, length_days: float) -> np.ndarray:
    """Return array of day overlaps per month for given season.

    start_day is 1-indexed (GAEZ convention). length_days can exceed 365; values
    above one year are capped at 365 to avoid infinite wrap.
    """
    if not np.isfinite(start_day) or not np.isfinite(length_days):
        return np.zeros(12)
    if length_days <= 0:
        return np.zeros(12)

    start = (float(start_day) - 1.0) % DAYS_IN_YEAR
    remaining = min(float(length_days), DAYS_IN_YEAR)
    overlaps = np.zeros(12)
    position = start

    while remaining > 1e-6:
        if position >= DAYS_IN_YEAR:
            position -= DAYS_IN_YEAR
        month_idx = _month_index_for_day(position)
        month_end = MONTH_ENDS[month_idx]
        available = month_end - position
        used = min(available, remaining)
        overlaps[month_idx] += used
        remaining -= used
        position = (position + used) % DAYS_IN_YEAR

    return overlaps


def build_basin_region_shares(
    basins_path: str,
    regions: gpd.GeoDataFrame,
) -> pd.DataFrame:
    basins = gpd.read_file(basins_path)[["BASIN_ID", "geometry"]]
    if basins.crs != regions.crs:
        regions = regions.to_crs(basins.crs)

    # Project to equal-area for accurate area calculation
    area_crs = "EPSG:6933"
    basins_eq = basins.to_crs(area_crs)
    regions_eq = regions.to_crs(area_crs)

    basin_area = basins_eq.set_index("BASIN_ID").geometry.area / 1e6  # km²

    intersections = gpd.overlay(regions_eq, basins_eq, how="intersection")
    if intersections.empty:
        return pd.DataFrame(columns=["region", "basin_id", "share", "area_km2"])

    intersections["area_km2"] = intersections.geometry.area / 1e6
    shares = intersections.groupby(["region", "BASIN_ID"], as_index=False)[
        "area_km2"
    ].sum()
    shares = shares.rename(columns={"BASIN_ID": "basin_id"})
    shares["share"] = shares.apply(
        lambda row: row["area_km2"] / basin_area.at[row["basin_id"]], axis=1
    )
    shares = shares[shares["share"] > 1e-6]
    return shares


def _zeros_for_regions(regions: list[str]) -> pd.DataFrame:
    if not regions:
        return pd.DataFrame(columns=["region", "month", "water_available_m3"])
    records = [
        {"region": region, "month": month, "water_available_m3": 0.0}
        for region in regions
        for month in range(1, 13)
    ]
    return pd.DataFrame(records)


def compute_region_monthly_water(
    shares: pd.DataFrame,
    monthly_basin: pd.DataFrame,
    regions: list[str],
) -> pd.DataFrame:
    required = {"basin_id", "month", "blue_water_availability_m3"}
    missing = required - set(monthly_basin.columns)
    if missing:
        raise ValueError(
            "Monthly basin data missing columns: " + ", ".join(sorted(missing))
        )
    df = shares.merge(monthly_basin, on="basin_id", how="inner")
    if df.empty:
        return _zeros_for_regions(regions)
    df["weighted"] = df["share"] * df["blue_water_availability_m3"]
    region_month = (
        df.groupby(["region", "month"], as_index=False)["weighted"]
        .sum()
        .rename(columns={"weighted": "water_available_m3"})
        .sort_values(["region", "month"])
    )
    region_month = region_month.set_index(["region", "month"])
    for region in regions:
        if region not in region_month.index.get_level_values("region"):
            filler = pd.DataFrame(
                {
                    "region": region,
                    "month": range(1, 13),
                    "water_available_m3": 0.0,
                }
            ).set_index(["region", "month"])
            region_month = pd.concat([region_month, filler])
    return region_month.reset_index().sort_values(["region", "month"])


def load_crop_growing_seasons(
    crop_files: Iterable[str],
) -> pd.DataFrame:
    records = []
    for path_str in crop_files:
        path = Path(path_str)
        stem = path.stem
        if "_" not in stem:
            continue
        crop, water_supply = stem.split("_", 1)

        df = pd.read_csv(path)

        pivot = (
            df.pivot(
                index=["region", "resource_class"], columns="variable", values="value"
            )
            .rename_axis(columns=None)
            .reset_index()
        )

        pivot = pivot.dropna(
            subset=[
                "region",
                "suitable_area",
                "growing_season_start_day",
                "growing_season_length_days",
            ]
        )
        pivot = pivot[pivot["suitable_area"] > 0]
        if pivot.empty:
            continue

        pivot["resource_class"] = pivot["resource_class"].astype(int)
        for column in [
            "suitable_area",
            "growing_season_start_day",
            "growing_season_length_days",
        ]:
            pivot[column] = pd.to_numeric(pivot[column], errors="coerce")

        grouped = pivot.groupby("region")
        for region, group in grouped:
            weight = group["suitable_area"].sum()
            if weight <= 0:
                continue
            start = (
                group["growing_season_start_day"] * group["suitable_area"]
            ).sum() / weight
            length = (
                group["growing_season_length_days"] * group["suitable_area"]
            ).sum() / weight
            records.append(
                {
                    "region": region,
                    "crop": crop,
                    "water_supply": water_supply,
                    "total_area": weight,
                    "growing_season_start_day": start,
                    "growing_season_length_days": length,
                }
            )
    if not records:
        return pd.DataFrame(
            columns=[
                "region",
                "crop",
                "water_supply",
                "total_area",
                "growing_season_start_day",
                "growing_season_length_days",
            ]
        )
    out = pd.DataFrame(records)
    return out


def compute_region_growing_water(
    region_month_water: pd.DataFrame,
    crop_seasons: pd.DataFrame,
    regions: list[str],
) -> pd.DataFrame:
    if region_month_water.empty:
        it = []
        for region in regions:
            it.append(
                {
                    "region": region,
                    "annual_water_available_m3": 0.0,
                    "growing_season_water_available_m3": 0.0,
                    "reference_irrigated_area": 0.0,
                }
            )
        return pd.DataFrame(it)

    monthly = region_month_water.set_index(["region", "month"])  # MultiIndex

    annual = (
        region_month_water.groupby("region")["water_available_m3"]
        .sum()
        .reindex(regions, fill_value=0.0)
        .rename("annual_water_available_m3")
    )

    if crop_seasons.empty:
        df = annual.to_frame().reset_index()
        df["growing_season_water_available_m3"] = 0.0
        return df

    irrigated = crop_seasons[crop_seasons["water_supply"] == "i"]
    if irrigated.empty:
        irrigated = crop_seasons

    # Prepare container for month demand fractions per region
    region_month_demand = {
        region: np.zeros(12) for region in crop_seasons["region"].unique()
    }
    region_total_area = dict.fromkeys(crop_seasons["region"].unique(), 0.0)

    for _, row in irrigated.iterrows():
        region = row["region"]
        overlaps = compute_month_overlaps(
            row["growing_season_start_day"], row["growing_season_length_days"]
        )
        if overlaps.sum() <= 0:
            continue
        area = row["total_area"]
        region_total_area[region] = region_total_area.get(region, 0.0) + area
        fraction = overlaps / MONTH_LENGTHS
        region_month_demand[region] = (
            region_month_demand.get(region, np.zeros(12)) + area * fraction
        )

    growing_records = []
    for region, total_area in region_total_area.items():
        demand = region_month_demand.get(region)
        if demand is None or total_area <= 0:
            demand_fraction = np.zeros(12)
        else:
            demand_fraction = np.minimum(1.0, demand / max(total_area, 1e-9))

        # Get region monthly water, fill missing months with 0
        try:
            region_series = (
                monthly.loc[region]["water_available_m3"]
                .reindex(range(1, 13), fill_value=0.0)
                .to_numpy(dtype=float)
            )
        except KeyError:
            region_series = np.zeros(12)

        growing_water = float(np.dot(region_series, demand_fraction))
        growing_records.append(
            {
                "region": region,
                "growing_season_water_available_m3": growing_water,
                "reference_irrigated_area": total_area,
            }
        )

    growing_df = pd.DataFrame(growing_records)
    combined = (
        annual.to_frame().reset_index().merge(growing_df, on="region", how="left")
    )
    combined["growing_season_water_available_m3"] = combined[
        "growing_season_water_available_m3"
    ].fillna(0.0)
    combined["reference_irrigated_area"] = combined["reference_irrigated_area"].fillna(
        0.0
    )
    # Ensure every region appears even if absent in both annual and growing_df
    missing = [region for region in regions if region not in combined["region"].values]
    if missing:
        filler = pd.DataFrame(
            {
                "region": missing,
                "annual_water_available_m3": 0.0,
                "growing_season_water_available_m3": 0.0,
                "reference_irrigated_area": 0.0,
            }
        )
        combined = pd.concat([combined, filler], ignore_index=True, sort=False)
    return combined.sort_values("region").reset_index(drop=True)


if __name__ == "__main__":
    shapefile_path: str = snakemake.input.shapefile  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    monthly_csv: str = snakemake.input.monthly  # type: ignore[name-defined]
    crop_files = list(snakemake.input.crop_yields)  # type: ignore[name-defined]

    monthly_basin_df = pd.read_csv(monthly_csv)
    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]]
    regions_list = regions_gdf["region"].tolist()
    shares_df = build_basin_region_shares(shapefile_path, regions_gdf)
    region_month_df = compute_region_monthly_water(
        shares_df, monthly_basin_df, regions_list
    )

    crop_seasons_df = load_crop_growing_seasons(crop_files)
    region_growing_df = compute_region_growing_water(
        region_month_df, crop_seasons_df, regions_list
    )

    monthly_out = Path(snakemake.output.monthly_region)  # type: ignore[name-defined]
    monthly_out.parent.mkdir(parents=True, exist_ok=True)
    region_month_df.to_csv(monthly_out, index=False)

    growing_out = Path(snakemake.output.region_growing)  # type: ignore[name-defined]
    growing_out.parent.mkdir(parents=True, exist_ok=True)
    region_growing_df.to_csv(growing_out, index=False)
