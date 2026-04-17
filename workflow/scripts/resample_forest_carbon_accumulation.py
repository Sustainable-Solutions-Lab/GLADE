"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
from pathlib import Path

from affine import Affine
import numpy as np
from osgeo import gdal, osr
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
import xarray as xr

NO_DATA = -9999.0


# Enable GDAL exceptions for better error messages
gdal.UseExceptions()
osr.UseExceptions()

# Enable GDAL multi-threading for better performance
os.environ["GDAL_NUM_THREADS"] = "ALL_CPUS"
os.environ["GDAL_CACHEMAX"] = "512"


def _load_target_grid(
    grid_path: str,
) -> tuple[Affine, CRS, tuple[int, int], dict[str, np.ndarray], dict[str, object]]:
    ds = xr.load_dataset(grid_path)
    try:
        transform_attr = tuple(ds.attrs["transform"])
    except KeyError as exc:
        raise ValueError("grid definition missing affine transform metadata") from exc
    target_transform = Affine.from_gdal(*transform_attr)
    try:
        crs = CRS.from_wkt(ds.attrs["crs_wkt"])
    except KeyError as exc:
        raise ValueError("grid definition missing CRS metadata") from exc

    height = int(ds.sizes["y"])
    width = int(ds.sizes["x"])
    coords = {
        "y": ds["y"].astype(np.float32).values,
        "x": ds["x"].astype(np.float32).values,
    }
    attrs = {
        "transform": transform_attr,
        "crs_wkt": ds.attrs["crs_wkt"],
        "height": ds.attrs.get("height", height),
        "width": ds.attrs.get("width", width),
    }
    return target_transform, crs, (height, width), coords, attrs


def main() -> None:
    grid_path: str = snakemake.input.grid  # type: ignore[name-defined]
    regrowth_path: str = snakemake.input.regrowth_raw  # type: ignore[name-defined]
    output_path: str = snakemake.output.regrowth  # type: ignore[name-defined]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    target_transform, target_crs, target_shape, coords, attr_template = (
        _load_target_grid(grid_path)
    )

    # Use WarpedVRT for efficient resampling - much faster than reproject()
    # for large files as it uses GDAL's optimized warping internally
    with rasterio.open(regrowth_path) as src:
        if src.crs is None:
            raise ValueError("regrowth raster missing CRS information")

        # Create a virtual warped dataset with the target resolution
        # Using bilinear resampling for performance (much faster than average)
        with WarpedVRT(
            src,
            crs=target_crs,
            transform=target_transform,
            height=target_shape[0],
            width=target_shape[1],
            resampling=Resampling.bilinear,
            src_nodata=src.nodata,
            nodata=NO_DATA,
        ) as vrt:
            # Read the entire warped dataset at once
            # This is efficient because GDAL handles the resampling internally
            dst = vrt.read(1, out_dtype=np.float32)

    dst[dst == NO_DATA] = np.nan

    ds_out = xr.Dataset(
        {
            "regrowth_tc_per_ha_yr": (("y", "x"), dst.astype(np.float32)),
        },
        coords={"y": coords["y"], "x": coords["x"]},
        attrs=attr_template,
    )

    encoding = {
        "regrowth_tc_per_ha_yr": {"zlib": True, "complevel": 4, "dtype": "float32"}
    }
    ds_out.to_netcdf(output_path, encoding=encoding)


if __name__ == "__main__":
    main()
