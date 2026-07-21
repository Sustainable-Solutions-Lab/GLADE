# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process Huang et al. gridded irrigation water withdrawal data.

Aggregates monthly gridded irrigation water withdrawal, stored as a depth
(mm/month at 0.5 degree resolution), converting it to a volume via grid-cell
area before aggregating to model regions. Produces outputs compatible with the
sustainable water availability data from the Water Footprint Network.

This script produces the availability tables in the shared schema
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

# Physical constants for depth-to-volume conversion. The Huang `withd_irr`
# variable is an irrigation-withdrawal *depth* (mm/month), not a volume, so it
# must be multiplied by grid-cell area (which varies with latitude) to obtain a
# volume before aggregating across cells.
MM_TO_M = 1e-3
EARTH_RADIUS_M = 6_371_000.0

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
    """Load area-weighted crop growing seasons from yield files."""
    crop_files = list(crop_files)
    irrigated_files = [path for path in crop_files if Path(path).stem.endswith("_i")]
    records = [
        record
        for path in irrigated_files
        for record in _load_crop_growing_season_file(path)
    ]
    if not records:
        records = [
            record
            for path in crop_files
            for record in _load_crop_growing_season_file(path)
        ]
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


def _load_crop_growing_season_file(path_str: str) -> list[dict]:
    """Load area-weighted growing seasons from one crop-yield file."""
    path = Path(path_str)
    stem = path.stem
    if "_" not in stem:
        return []
    crop, water_supply = stem.split("_", 1)

    required = {
        "suitable_area",
        "growing_season_start_day",
        "growing_season_length_days",
    }
    df = pd.read_csv(
        path,
        usecols=["region", "resource_class", "variable", "value"],
    )
    df = df[df["variable"].isin(required)]
    if not required.issubset(df["variable"].unique()):
        return []

    pivot = (
        df.pivot(index=["region", "resource_class"], columns="variable", values="value")
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
        return []

    regions = pivot["region"].to_numpy()
    area = pivot["suitable_area"].to_numpy()
    start = pivot["growing_season_start_day"].to_numpy()
    length = pivot["growing_season_length_days"].to_numpy()
    group_starts = np.flatnonzero(np.r_[True, regions[1:] != regions[:-1]])
    group_ends = np.r_[group_starts[1:], len(regions)]

    records = []
    for first, last in zip(group_starts, group_ends, strict=True):
        weight = area[first:last].sum()
        records.append(
            {
                "region": regions[first],
                "crop": crop,
                "water_supply": water_supply,
                "total_area": weight,
                "growing_season_start_day": (start[first:last] * area[first:last]).sum()
                / weight,
                "growing_season_length_days": (
                    length[first:last] * area[first:last]
                ).sum()
                / weight,
            }
        )
    return records


def compute_region_growing_water(
    region_month_water: pd.DataFrame,
    crop_seasons: pd.DataFrame,
    regions: list[str],
) -> pd.DataFrame:
    """Compute growing-season weighted water availability.

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

    for row in irrigated.itertuples(index=False):
        region = row.region
        overlaps = compute_month_overlaps(
            row.growing_season_start_day, row.growing_season_length_days
        )
        if overlaps.sum() <= 0:
            continue
        area = row.total_area
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
        - DataFrame with growing season water (shared availability schema)
    """
    # Load the NetCDF dataset
    ds = xr.open_dataset(nc_path, decode_times=False)

    data = ds["withd_irr"]
    lon = ds["lon"].values
    lat = ds["lat"].values
    time_dim = "month"

    # Huang/H08 stores grid cells as a flat list: lat and lon are
    # parallel 1D arrays of length n_cells, and withd_irr has shape
    # (month, n_cells). Standard NetCDFs with separate lat / lon axes
    # would silently mis-index below (line: monthly_data[lat_idx,
    # lon_idx] = monthly_values.ravel()).
    if lat.ndim != 1 or lon.ndim != 1 or lat.shape != lon.shape:
        raise ValueError(
            "Huang irrigation NetCDF expected to use parallel 1D "
            f"lat/lon arrays of equal length; got lat shape {lat.shape}, "
            f"lon shape {lon.shape}."
        )

    # Load regions
    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]]
    regions_list = regions_gdf["region"].tolist()

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

    # Per-cell area (m2) for converting the withdrawal depth (mm/month) to a
    # volume. Area depends only on latitude:
    #   A = R^2 * dlon_rad * (sin(lat_north) - sin(lat_south)).
    lat_north = np.deg2rad(lat_values + lat_res / 2.0)
    lat_south = np.deg2rad(lat_values - lat_res / 2.0)
    row_area_m2 = (
        EARTH_RADIUS_M**2
        * np.deg2rad(lon_res)
        * (np.sin(lat_north) - np.sin(lat_south))
    )
    cell_area_m2 = np.abs(row_area_m2)[:, np.newaxis]  # (n_lat, 1), broadcasts over lon

    # Extract monthly data for reference year
    # The dataset spans 1971-2010, with monthly data = 480 time steps
    year_start_idx = (reference_year - 1971) * 12

    monthly_rasters = []
    for month in range(1, 13):
        time_idx = year_start_idx + month - 1
        monthly_values = np.asarray(data.isel({time_dim: time_idx}).values, dtype=float)
        monthly_data = np.full(grid_shape, np.nan, dtype=float)
        monthly_data[lat_idx, lon_idx] = monthly_values.ravel()

        # Convert withdrawal depth (mm/month) to volume (m3/month) per cell
        # before aggregating; a coverage-weighted sum of a depth is meaningless.
        monthly_volume_m3 = monthly_data * MM_TO_M * cell_area_m2

        monthly_rasters.append(
            NumPyRasterSource(
                monthly_volume_m3,
                xmin=lon_min - lon_res / 2,
                ymin=lat_min - lat_res / 2,
                xmax=lon_max + lon_res / 2,
                ymax=lat_max + lat_res / 2,
                nodata=np.nan,
                name=f"month_{month}",
                srs_wkt='GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]',
            )
        )

    result = exact_extract(
        monthly_rasters,
        regions_gdf.reset_index(),
        ["sum"],
        include_cols=["region"],
        output="pandas",
    )

    ds.close()

    # Build monthly dataframe
    monthly_df = result.melt(
        id_vars="region", var_name="month", value_name="water_available_m3"
    )
    monthly_df["month"] = (
        monthly_df["month"].str.removesuffix("_sum").str[6:].astype(int)
    )
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
