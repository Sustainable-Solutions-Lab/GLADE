"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
import rasterio
import rasterio.features as rfeatures
import xarray as xr

# Enable GDAL exceptions for better error messages
gdal.UseExceptions()
osr.UseExceptions()


def read_raster_float(path: str):
    src = rasterio.open(path)
    arr = src.read(1).astype(float)
    if src.nodata is not None:
        arr = np.where(arr == src.nodata, np.nan, arr)
    return arr, src


if __name__ == "__main__":
    # Inputs provided by Snakemake
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    # Yield rasters as a list of paths
    yield_paths: list[str] = list(snakemake.input.yields)  # type: ignore[attr-defined]
    quantiles: list[float] = [
        0.0,
        *list(snakemake.params.resource_class_quantiles),
        1.0,
    ]  # type: ignore[name-defined]

    # Read regions and use first raster as reference for grid/CRS
    regions_gdf = gpd.read_file(regions_path)

    # Use the first yield raster as reference
    y0, src0 = read_raster_float(yield_paths[0])
    height, width = y0.shape
    transform = src0.transform
    crs = src0.crs

    # Reproject regions to raster CRS if needed
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)

    # Running maximum of yields in t/ha across all provided rasters
    y_max = (y0 / 1000.0).astype(float)  # kg/ha -> t/ha
    for path in yield_paths[1:]:
        arr, _ = read_raster_float(path)
        arr_tpha = arr / 1000.0
        y_max = np.fmax(y_max, arr_tpha)

    # Rasterize regions to integer ids (0..N-1), -1 outside
    region_shapes = [(geom, idx) for idx, geom in enumerate(regions_gdf.geometry)]
    region_raster = rfeatures.rasterize(
        region_shapes,
        out_shape=(height, width),
        transform=transform,
        fill=-1,
        dtype=np.int32,
    )

    # Build xarray DataArrays
    y_da = xr.DataArray(y_max, dims=("y", "x"))
    reg_da = xr.DataArray(region_raster, dims=("y", "x"))

    # Vectorized per-region quantiles and class assignment
    # Ignore cells with zero/negative potential yield so desert pixels
    # do not collapse the quantile bins.
    positive_y = xr.where((y_da > 0) & np.isfinite(y_da), y_da, np.nan)
    reg_quantiles = positive_y.groupby(reg_da).quantile(quantiles)
    thresholds = reg_quantiles.sel(group=reg_da).reset_coords(drop=True)

    class_da = xr.full_like(y_da, np.nan, dtype=float)
    for ci in range(len(quantiles) - 1):
        lo = thresholds.isel(quantile=ci)
        hi = thresholds.isel(quantile=ci + 1)
        if ci == len(quantiles) - 2:
            sel = (reg_da >= 0) & np.isfinite(y_da) & (y_da >= lo)
        else:
            sel = (reg_da >= 0) & np.isfinite(y_da) & (y_da >= lo) & (y_da < hi)
        class_da = xr.where(sel, float(ci), class_da)

    ds = xr.Dataset(
        {
            "region_id": reg_da.astype(np.int32),
            "resource_class": class_da.fillna(-1).astype(np.int8),
        }
    )
    # Store transform/CRS/bounds as attrs for downstream use
    ds.attrs.update(
        {
            "transform": transform.to_gdal(),
            "crs_wkt": crs.to_wkt() if crs else None,
            "height": int(height),
            "width": int(width),
            "quantiles": tuple(quantiles),
        }
    )

    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path)
