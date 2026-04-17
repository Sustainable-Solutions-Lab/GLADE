"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import numpy as np
from osgeo import gdal, osr
from pyproj import Geod
import rasterio

# Enable GDAL exceptions to avoid FutureWarning and get better error messages
gdal.UseExceptions()
osr.UseExceptions()


def calculate_all_cell_areas(
    src: rasterio.DatasetReader, *, repeat: bool = True
) -> np.ndarray:
    """Return per-pixel area in hectares for a geographic (lon/lat) raster.

    Args:
        src: Raster opened with rasterio, expected in lon/lat coordinates.
        repeat: When True (default) repeat the per-row areas across columns,
            yielding a 2D array matching the raster shape. When False, return the
            1D per-row areas without repeating, which is useful when the caller can
            rely on broadcasting to avoid materialising the full 2D matrix.
    """
    pixel_width_deg = abs(src.transform.a)
    pixel_height_deg = abs(src.transform.e)
    rows, cols = src.shape
    left, bottom, _right, top = src.bounds
    lats = np.linspace(top - pixel_height_deg / 2, bottom + pixel_height_deg / 2, rows)
    geod = Geod(ellps="WGS84")
    areas_ha = np.zeros(rows, dtype=np.float32)
    for i, lat in enumerate(lats):
        lat_top = lat + pixel_height_deg / 2
        lat_bottom = lat - pixel_height_deg / 2
        lon_left = left
        lon_right = left + pixel_width_deg
        lons = [lon_left, lon_right, lon_right, lon_left, lon_left]
        lats_poly = [lat_bottom, lat_bottom, lat_top, lat_top, lat_bottom]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats_poly)
        areas_ha[i] = abs(area_m2) / 10000.0
    if repeat:
        return np.repeat(areas_ha[:, np.newaxis], cols, axis=1)
    return areas_ha


def scale_fraction(arr: np.ndarray) -> np.ndarray:
    """Scale array to 0..1 if stored as 0..100 or 0..10000; clip to [0,1]."""
    if not np.issubdtype(arr.dtype, np.floating):
        a = arr.astype(np.float32)
    else:
        a = arr.astype(np.float32, copy=False)
    # Normalise non-finite values to NaN so downstream operations can skip them.
    np.copyto(a, np.nan, where=~np.isfinite(a))
    if np.all(np.isnan(a)):
        return a

    vmax = np.nanmax(a)
    if np.isfinite(vmax) and vmax > 1.5:
        scale = 100.0 if vmax <= 100 else 10000.0
        a /= scale
    return np.clip(a, 0.0, 1.0, out=a)


def raster_bounds(transform, width: int, height: int):
    """Calculate bounding box from raster transform and dimensions."""
    xmin = transform.c
    ymax = transform.f
    xmax = xmin + width * transform.a
    ymin = ymax + height * transform.e
    return xmin, ymin, xmax, ymax


def read_raster_float(path: str):
    """Open raster and return array + source, converting nodata to NaN.

    Returns:
        tuple: (array as float32, rasterio source - caller must close)
    """
    src = rasterio.open(path)
    arr = src.read(1).astype(np.float32)
    if src.nodata is not None:
        arr = np.where(arr == src.nodata, np.nan, arr)
    return arr, src


def load_raster_array(path: str) -> np.ndarray:
    """Load raster as float32 array, converting nodata to NaN."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        if src.nodata is not None:
            arr = np.where(arr == src.nodata, np.nan, arr)
    return arr
