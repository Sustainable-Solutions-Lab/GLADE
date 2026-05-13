# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process Huang et al. gridded irrigation water withdrawal data.

Aggregates monthly gridded irrigation water use (km³/month at 0.5° resolution)
to model regions, producing outputs compatible with the sustainable water
availability data from the Water Footprint Network.

This script produces the same output format as build_region_water_availability.py
so that the two data sources can be used interchangeably.

Reference:
    Huang et al. (2018). Reconstruction of global gridded monthly sectoral
    water withdrawals for 1971-2010 and analysis of their spatiotemporal
    patterns. Hydrology and Earth System Sciences, 22, 2117-2133.
    https://doi.org/10.5194/hess-22-2117-2018
"""

from collections.abc import Iterable
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import exact_extract  # noqa: E402
from exactextract.raster import NumPyRasterSource  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402

# Conversion factors
KM3_TO_M3 = 1e9  # 1 km³ = 1e9 m³

# Month lengths for growing season calculations
MONTH_LENGTHS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)
MONTH_ENDS = np.cumsum(MONTH_LENGTHS)
DAYS_IN_YEAR = float(MONTH_ENDS[-1])


def _month_index_for_day(day: float) -> int:
    """Return month index (0-based) for day in [0, 365)."""
    return int(np.searchsorted(MONTH_ENDS, day + 1e-9))


def compute_month_overlaps(start_day: float, length_days: float) -> np.ndarray:
    """Return array of day overlaps per month for given season."""
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


def aggregate_gridded_to_regions(
    data_array: np.ndarray,
    lon: np.ndarray,
    lat: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Aggregate a gridded array to regions by summation.

    Args:
        data_array: 2D array (lat, lon) with water withdrawal values.
        lon: 1D longitude coordinates.
        lat: 1D latitude coordinates.
        regions_gdf: GeoDataFrame with 'region' column and geometry.

    Returns:
        DataFrame with 'region' and 'value' columns.
    """
    # Determine grid bounds
    lon_res = abs(lon[1] - lon[0]) if len(lon) > 1 else 0.5
    lat_res = abs(lat[1] - lat[0]) if len(lat) > 1 else 0.5

    xmin = float(lon.min()) - lon_res / 2
    xmax = float(lon.max()) + lon_res / 2
    ymin = float(lat.min()) - lat_res / 2
    ymax = float(lat.max()) + lat_res / 2

    # Ensure regions are in WGS84
    if regions_gdf.crs is not None and regions_gdf.crs.to_epsg() != 4326:
        regions_gdf = regions_gdf.to_crs("EPSG:4326")

    # Check if lat is in increasing or decreasing order
    if lat[0] > lat[-1]:
        # lat is decreasing (north to south) - standard orientation
        arr = data_array
    else:
        # lat is increasing (south to north) - flip
        arr = np.flipud(data_array)
        ymin, ymax = ymax, ymin

    # Replace NaN with 0 for summation (no water use in ocean/missing areas)
    arr = np.where(np.isfinite(arr), arr, 0.0).astype(np.float64)

    raster_src = NumPyRasterSource(
        arr,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        nodata=np.nan,
        srs_wkt='GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]',
    )

    # Extract sum per region
    result = exact_extract(
        raster_src,
        regions_gdf.reset_index(),
        ["sum"],
        include_cols=["region"],
        output="pandas",
    )

    return result.rename(columns={"sum": "value"})


def load_crop_growing_seasons(crop_files: Iterable[str]) -> pd.DataFrame:
    """Load and aggregate crop growing seasons from yield files.

    This is a copy of the function from build_region_water_availability.py
    to ensure consistency.
    """
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

        # CROPGRIDS-backed crops (config["cropgrids_crops"]) don't carry
        # GAEZ growing-season rasters and contribute no irrigation demand
        # (they're rainfed-only). Skip them rather than dropna-erroring.
        required = {
            "suitable_area",
            "growing_season_start_day",
            "growing_season_length_days",
        }
        if not required.issubset(pivot.columns):
            continue

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
    return pd.DataFrame(records)


def compute_region_growing_water(
    region_month_water: pd.DataFrame,
    crop_seasons: pd.DataFrame,
    regions: list[str],
) -> pd.DataFrame:
    """Compute growing-season weighted water availability.

    This mirrors the function from build_region_water_availability.py
    but uses 'water_available_m3' column from monthly data.
    """
    if region_month_water.empty:
        return pd.DataFrame(
            {
                "region": regions,
                "annual_water_available_m3": 0.0,
                "growing_season_water_available_m3": 0.0,
                "reference_irrigated_area": 0.0,
            }
        )

    monthly = region_month_water.set_index(["region", "month"])

    annual = (
        region_month_water.groupby("region")["water_available_m3"]
        .sum()
        .reindex(regions, fill_value=0.0)
        .rename("annual_water_available_m3")
    )

    if crop_seasons.empty:
        df = annual.to_frame().reset_index()
        df["growing_season_water_available_m3"] = 0.0
        df["reference_irrigated_area"] = 0.0
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
    # Ensure every region appears
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


def process_huang_irrigation(
    nc_path: str,
    regions_path: str,
    crop_files: list[str],
    reference_year: int = 2010,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Process Huang et al. irrigation NetCDF to regional water data.

    Args:
        nc_path: Path to the extracted Huang irrigation NetCDF file.
        regions_path: Path to the regions GeoJSON file.
        crop_files: List of crop yield file paths for growing season data.
        reference_year: Year to use for water withdrawal (default: 2010).

    Returns:
        Tuple of:
        - DataFrame with monthly region water (region, month, water_available_m3)
        - DataFrame with growing season water (same format as build_region_water_availability)
    """
    # Load the NetCDF dataset
    ds = xr.open_dataset(nc_path, decode_times=False)

    data = ds["withd_irr"]
    lon = ds["lon"].values
    lat = ds["lat"].values
    time_dim = "month"

    # Load regions
    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]]
    regions_list = regions_gdf["region"].tolist()

    monthly_records = []

    lon_unique = np.sort(np.unique(lon.astype(float)))
    lat_unique = np.sort(np.unique(lat.astype(float)))
    lon_diffs = np.diff(lon_unique)
    lat_diffs = np.diff(lat_unique)
    lon_res = float(lon_diffs[lon_diffs > 0].min())
    lat_res = float(lat_diffs[lat_diffs > 0].min())
    lon_min = float(lon_unique.min())
    lon_max = float(lon_unique.max())
    lat_min = float(lat_unique.min())
    lat_max = float(lat_unique.max())

    lon_values = np.arange(lon_min, lon_max + lon_res * 0.5, lon_res)
    lat_values = np.arange(lat_max, lat_min - lat_res * 0.5, -lat_res)
    lon_idx = np.rint((lon.astype(float) - lon_min) / lon_res).astype(int)
    lat_idx = np.rint((lat_max - lat.astype(float)) / lat_res).astype(int)
    grid_shape = (lat_values.size, lon_values.size)

    # Extract monthly data for reference year
    # The dataset spans 1971-2010, with monthly data = 480 time steps
    year_start_idx = (reference_year - 1971) * 12

    for month in range(1, 13):
        time_idx = year_start_idx + month - 1
        monthly_values = np.asarray(data.isel({time_dim: time_idx}).values, dtype=float)
        monthly_data = np.full(grid_shape, np.nan, dtype=float)
        monthly_data[lat_idx, lon_idx] = monthly_values.ravel()

        # Aggregate to regions
        result = aggregate_gridded_to_regions(
            monthly_data, lon_values, lat_values, regions_gdf
        )

        for _, row in result.iterrows():
            # Convert from km³ to m³
            water_m3 = float(row["value"]) * KM3_TO_M3
            monthly_records.append(
                {
                    "region": row["region"],
                    "month": month,
                    "water_available_m3": water_m3,
                }
            )

    ds.close()

    # Build monthly dataframe
    monthly_df = pd.DataFrame(monthly_records)
    monthly_df = monthly_df.sort_values(["region", "month"]).reset_index(drop=True)

    # Load crop growing seasons and compute growing season water
    crop_seasons = load_crop_growing_seasons(crop_files)
    growing_df = compute_region_growing_water(monthly_df, crop_seasons, regions_list)

    return monthly_df, growing_df


if __name__ == "__main__":
    nc_path: str = snakemake.input.nc  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    crop_files: list[str] = list(snakemake.input.crop_yields)  # type: ignore[name-defined]
    reference_year: int = snakemake.params.reference_year  # type: ignore[name-defined]

    monthly_out: str = snakemake.output.monthly_region  # type: ignore[name-defined]
    growing_out: str = snakemake.output.region_growing  # type: ignore[name-defined]

    monthly_df, growing_df = process_huang_irrigation(
        nc_path, regions_path, crop_files, reference_year
    )

    Path(monthly_out).parent.mkdir(parents=True, exist_ok=True)
    monthly_df.to_csv(monthly_out, index=False)

    Path(growing_out).parent.mkdir(parents=True, exist_ok=True)
    growing_df.to_csv(growing_out, index=False)
