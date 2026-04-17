"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import numpy as np
from osgeo import gdal, osr
import rasterio
import xarray as xr

# Enable GDAL exceptions for better error messages
gdal.UseExceptions()
osr.UseExceptions()


def _extract_grid(yield_raster_path: str) -> xr.Dataset:
    with rasterio.open(yield_raster_path) as src:
        transform = src.transform
        crs = src.crs
        if crs is None:
            raise ValueError("yield raster is missing CRS information")
        height = src.height
        width = src.width

    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e

    ds = xr.Dataset(
        coords={
            "y": ("y", lat.astype(np.float32)),
            "x": ("x", lon.astype(np.float32)),
        },
        attrs={
            "transform": transform.to_gdal(),
            "crs_wkt": crs.to_wkt(),
            "height": height,
            "width": width,
        },
    )
    return ds


def main() -> None:
    yield_raster: str = snakemake.input.yield_raster  # type: ignore[name-defined]
    output_path: str = snakemake.output.grid  # type: ignore[name-defined]

    ds = _extract_grid(yield_raster)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(output_file)


if __name__ == "__main__":
    main()
