# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resample LUIcube grassland GeoTIFFs onto the model grid.

Combines GL-owl and GL-notrees land-use classes and computes derived
variables (NPP_act, grassland_fraction, grazing_intensity).

When source and destination grids are exactly aligned (integer resolution
ratio, same CRS/extent), uses a fast block-sum aggregator. Otherwise falls
back to ``rasterio.vrt.WarpedVRT`` with ``Resampling.sum``.

Output variables:
    area_km2          : grassland area [km²] per grid cell
    hanpp_harv_tc_yr  : harvested HANPP [tC/yr] per grid cell
    npp_act_tc_yr     : actual NPP (HANPP_harv + NPP_eco) [tC/yr] per grid cell
    grassland_fraction: fraction of grid cell covered by grassland [0-1]
    grazing_intensity : fraction of NPP harvested (HANPP_harv / NPP_act) [0-1]
"""

import os
from pathlib import Path

from affine import Affine
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
import xarray as xr

from workflow.scripts.raster_utils import calculate_all_cell_areas, raster_bounds

# Let GDAL use multiple threads for warping and increase block cache
os.environ.setdefault("GDAL_NUM_THREADS", "4")
os.environ.setdefault("GDAL_CACHEMAX", "2048")


def _bounds_from_transform(
    transform: Affine, width: int, height: int
) -> tuple[float, float, float, float]:
    """Return (left, bottom, right, top) bounds for an Affine grid."""
    left = transform.c
    top = transform.f
    right = left + width * transform.a
    bottom = top + height * transform.e
    return left, bottom, right, top


def _is_grid_aligned(
    src: rasterio.DatasetReader,
    dst_transform: Affine,
    dst_crs: CRS,
    dst_height: int,
    dst_width: int,
) -> tuple[bool, int, int]:
    """Check whether source grid can be summed by exact integer aggregation."""
    if src.crs != dst_crs:
        return False, 0, 0

    eps = 1e-9
    # Require north-up grids without rotation.
    if (
        abs(src.transform.b) > eps
        or abs(src.transform.d) > eps
        or abs(dst_transform.b) > eps
        or abs(dst_transform.d) > eps
    ):
        return False, 0, 0

    src_dx = float(src.transform.a)
    src_dy = abs(float(src.transform.e))
    dst_dx = float(dst_transform.a)
    dst_dy = abs(float(dst_transform.e))
    if src_dx <= 0.0 or src_dy <= 0.0 or dst_dx <= 0.0 or dst_dy <= 0.0:
        return False, 0, 0

    fx = int(round(dst_dx / src_dx))
    fy = int(round(dst_dy / src_dy))
    if fx < 1 or fy < 1:
        return False, 0, 0

    if abs(dst_dx - fx * src_dx) > eps or abs(dst_dy - fy * src_dy) > eps:
        return False, 0, 0

    if src.width != dst_width * fx or src.height != dst_height * fy:
        return False, 0, 0

    src_bounds = _bounds_from_transform(src.transform, src.width, src.height)
    dst_bounds = _bounds_from_transform(dst_transform, dst_width, dst_height)
    if any(abs(s - d) > 1e-6 for s, d in zip(src_bounds, dst_bounds, strict=False)):
        return False, 0, 0

    return True, fy, fx


def _aligned_block_sum(
    src: rasterio.DatasetReader,
    dst_height: int,
    dst_width: int,
    fy: int,
    fx: int,
) -> np.ndarray:
    """Aggregate aligned fine grid to coarse grid by exact block sums."""
    out = np.zeros((dst_height, dst_width), dtype=np.float64)
    src_nodata = src.nodata

    # Read strips of source rows to keep memory bounded.
    chunk_out_rows = 64
    for dst_row0 in range(0, dst_height, chunk_out_rows):
        out_rows = min(chunk_out_rows, dst_height - dst_row0)
        src_row0 = dst_row0 * fy
        src_rows = out_rows * fy

        window = Window(0, src_row0, src.width, src_rows)
        block = src.read(1, window=window, out_dtype=np.float32)
        if src_nodata is not None:
            np.copyto(block, 0.0, where=block == src_nodata)

        out[dst_row0 : dst_row0 + out_rows] = block.reshape(
            out_rows, fy, dst_width, fx
        ).sum(axis=(1, 3), dtype=np.float64)

    return out


def _warp_sum(
    src_path: str,
    dst_transform: Affine,
    dst_crs: CRS,
    dst_height: int,
    dst_width: int,
) -> np.ndarray:
    """Resample a GeoTIFF to the target grid using sum resampling.

    Uses fast aligned block-sums when possible, otherwise ``WarpedVRT``.
    """
    with rasterio.open(src_path) as src:
        aligned, fy, fx = _is_grid_aligned(
            src, dst_transform, dst_crs, dst_height, dst_width
        )
        if aligned:
            arr = _aligned_block_sum(src, dst_height, dst_width, fy, fx)
        else:
            with WarpedVRT(
                src,
                crs=dst_crs,
                transform=dst_transform,
                height=dst_height,
                width=dst_width,
                resampling=Resampling.sum,
            ) as vrt:
                arr = vrt.read(1, out_dtype=np.float64)
    return arr


class _DummyRaster:
    """Minimal raster-like object for calculate_all_cell_areas."""

    def __init__(self, transform: Affine, width: int, height: int) -> None:
        self.transform = transform
        self.shape = (height, width)
        xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
        self.bounds = (xmin, ymin, xmax, ymax)


def main() -> None:
    grid_path: str = snakemake.input.grid  # type: ignore[name-defined]
    owl_area_path: str = snakemake.input.owl_area  # type: ignore[name-defined]
    owl_hanpp_path: str = snakemake.input.owl_hanpp  # type: ignore[name-defined]
    owl_nppeco_path: str = snakemake.input.owl_nppeco  # type: ignore[name-defined]
    notrees_area_path: str = snakemake.input.notrees_area  # type: ignore[name-defined]
    notrees_hanpp_path: str = snakemake.input.notrees_hanpp  # type: ignore[name-defined]
    notrees_nppeco_path: str = snakemake.input.notrees_nppeco  # type: ignore[name-defined]
    output_path: str = snakemake.output[0]  # type: ignore[name-defined]

    # Load target grid
    grid_ds = xr.load_dataset(grid_path)
    dst_transform = Affine.from_gdal(*grid_ds.attrs["transform"])
    dst_crs = CRS.from_wkt(grid_ds.attrs["crs_wkt"])
    dst_height = int(grid_ds.attrs["height"])
    dst_width = int(grid_ds.attrs["width"])
    y_coords = grid_ds["y"]
    x_coords = grid_ds["x"]

    # Resample each raster and sum GL-owl + GL-notrees at target resolution.
    warp_args = (dst_transform, dst_crs, dst_height, dst_width)
    variables = {}
    for var_name, owl_path, notrees_path in [
        ("area", owl_area_path, notrees_area_path),
        ("hanpp", owl_hanpp_path, notrees_hanpp_path),
        ("nppeco", owl_nppeco_path, notrees_nppeco_path),
    ]:
        owl_reproj = _warp_sum(owl_path, *warp_args)
        notrees_reproj = _warp_sum(notrees_path, *warp_args)
        variables[var_name] = owl_reproj + notrees_reproj

    area_km2 = variables["area"]
    hanpp_harv = variables["hanpp"]
    npp_eco = variables["nppeco"]

    # Derived: actual NPP = HANPP_harv + NPP_eco
    npp_act = hanpp_harv + npp_eco

    # Derived: grazing intensity = HANPP_harv / NPP_act, clipped to [0, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        grazing_intensity = np.where(npp_act > 0, hanpp_harv / npp_act, 0.0)
    grazing_intensity = np.clip(grazing_intensity, 0.0, 1.0)

    # Compute grassland_fraction = area_km2 * 100 / cell_area_ha
    dummy = _DummyRaster(dst_transform, dst_width, dst_height)
    cell_area_ha = calculate_all_cell_areas(dummy)
    with np.errstate(divide="ignore", invalid="ignore"):
        grassland_fraction = np.where(
            cell_area_ha > 0, (area_km2 * 100.0) / cell_area_ha, 0.0
        )
    grassland_fraction = np.clip(grassland_fraction, 0.0, 1.0)

    # Build output dataset
    ds_out = xr.Dataset(
        {
            "area_km2": (("y", "x"), area_km2.astype(np.float32)),
            "hanpp_harv_tc_yr": (("y", "x"), hanpp_harv.astype(np.float32)),
            "npp_act_tc_yr": (("y", "x"), npp_act.astype(np.float32)),
            "grassland_fraction": (("y", "x"), grassland_fraction.astype(np.float32)),
            "grazing_intensity": (("y", "x"), grazing_intensity.astype(np.float32)),
        },
        coords={"y": y_coords, "x": x_coords},
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ds_out.to_netcdf(output_path)


if __name__ == "__main__":
    main()
