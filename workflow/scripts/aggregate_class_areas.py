"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
import xarray as xr

from workflow.scripts.raster_utils import calculate_all_cell_areas, scale_fraction


def read_raster_float(path: str):
    src = rasterio.open(path)
    arr = src.read(1, masked=False).astype(np.float32)
    if src.nodata is not None:
        nodata = np.float32(src.nodata)
        mask = arr == nodata
        if np.any(mask):
            arr[mask] = np.nan
    return arr, src


def load_scaled_fraction(
    path: str,
    *,
    target_shape: tuple[int, int] | None = None,
    target_transform=None,
    target_crs=None,
) -> np.ndarray:
    with rasterio.open(path) as src:
        needs_resample = False
        if target_shape is not None:
            if src.shape != target_shape:
                needs_resample = True
            if target_transform is not None and src.transform != target_transform:
                needs_resample = True
            if target_crs is not None and src.crs != target_crs:
                needs_resample = True

        if needs_resample:
            if target_transform is None or target_crs is None:
                raise ValueError(
                    "target_transform and target_crs required for resampling"
                )
            arr = np.full(target_shape, np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=arr,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=Resampling.average,
                src_nodata=src.nodata,
                dst_nodata=np.nan,
            )
        else:
            arr = src.read(1, masked=False).astype(np.float32)
            if src.nodata is not None:
                nodata = np.float32(src.nodata)
                mask = arr == nodata
                if np.any(mask):
                    arr[mask] = np.nan
        return scale_fraction(arr)


def raster_bounds(transform, width: int, height: int):
    xmin = transform.c
    ymax = transform.f
    xmax = xmin + width * transform.a
    ymin = ymax + height * transform.e
    return xmin, ymin, xmax, ymax


if __name__ == "__main__":
    # Inputs
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    classes_nc: str = snakemake.input.classes  # type: ignore[name-defined]
    # Suitability/area inputs as lists of file paths
    sr_files: list[str] = list(snakemake.input.sr)  # type: ignore[attr-defined]
    si_files: list[str] = list(snakemake.input.si)  # type: ignore[attr-defined]
    irrigated_share_path: str | None = getattr(snakemake.input, "irrigated_share", None)  # type: ignore[attr-defined]

    irrigated_area_source: str = snakemake.params.irrigated_area_source  # type: ignore[name-defined]

    # Load classes
    ds = xr.load_dataset(classes_nc)
    classes = ds["resource_class"].values.astype(np.int16)

    # Reference grid parameters from a suitability raster (rainfed)
    # Use first rainfed suitability file as reference
    if not sr_files:
        raise ValueError("No rainfed suitability files provided")
    sr0, src0 = read_raster_float(sr_files[0])
    try:
        height, width = sr0.shape
        transform = src0.transform
        crs = src0.crs
        xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
        crs_wkt = crs.to_wkt() if crs else None
        cell_area_rows = calculate_all_cell_areas(src0, repeat=False)
    finally:
        src0.close()

    # Regions
    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)
    regions_for_extract = regions_gdf.reset_index()

    # Cell areas
    cell_area_rows = cell_area_rows.astype(np.float32, copy=False)

    # Build max suitability per pixel across crops for each ws
    def max_suitability(
        files: list[str], *, base: np.ndarray | None = None
    ) -> np.ndarray:
        it = iter(files)
        result = base
        if result is None:
            try:
                first = next(it)
            except StopIteration:
                return np.zeros((height, width), dtype=np.float32)
            result = load_scaled_fraction(first)
        for path in it:
            np.maximum(result, load_scaled_fraction(path), out=result)
        return result

    # Compute land area limits based on configuration
    sr_base = scale_fraction(sr0)
    del sr0
    sr_max = (
        max_suitability(sr_files[1:], base=sr_base) if len(sr_files) > 1 else sr_base
    )
    np.multiply(sr_max, cell_area_rows[:, np.newaxis], out=sr_max)
    area_r = sr_max

    # Aggregate rainfed area before computing irrigated to reduce peak memory.
    def aggregate_area(area: np.ndarray, ws: str) -> pd.DataFrame:
        out = []
        valid_mask = classes >= 0
        if not np.any(valid_mask):
            return pd.DataFrame(
                columns=["region", "resource_class", "water_supply", "area_ha"]
            )
        class_ids = np.unique(classes[valid_mask])
        work_arr = np.empty_like(area, dtype=np.float32)
        for cls in class_ids:
            mask = classes == cls
            if not np.any(mask):
                continue
            work_arr.fill(np.nan)
            work_arr[mask] = area[mask]
            a_src = NumPyRasterSource(
                work_arr,
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                nodata=np.nan,
                srs_wkt=crs_wkt,
            )
            a_stats = exact_extract(
                a_src,
                regions_for_extract,
                ["sum"],
                include_cols=["region"],
                output="pandas",
            )
            if a_stats.empty:
                continue
            a_stats = a_stats.rename(columns={"sum": "area_ha"})
            a_stats["resource_class"] = cls
            a_stats["water_supply"] = ws
            out.append(a_stats)
        if not out:
            return pd.DataFrame(
                columns=["region", "resource_class", "water_supply", "area_ha"]
            )
        return pd.concat(out, ignore_index=True)

    df_r = aggregate_area(area_r, "r")
    del area_r

    if irrigated_area_source == "potential":
        area_i = max_suitability(si_files)
        if area_i.size:
            np.multiply(area_i, cell_area_rows[:, np.newaxis], out=area_i)
    else:  # "current"
        area_i = load_scaled_fraction(
            irrigated_share_path,
            target_shape=(height, width),
            target_transform=transform,
            target_crs=crs,
        )
        if area_i.size:
            np.multiply(area_i, cell_area_rows[:, np.newaxis], out=area_i)

    df_i = aggregate_area(area_i, "i")
    del area_i
    out_df = pd.concat([df_r, df_i], ignore_index=True)
    out_df = out_df.set_index(["region", "water_supply", "resource_class"]).sort_index()

    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path)
