"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.env import set_gdal_config
from rasterio.warp import reproject

from workflow.scripts.raster_utils import calculate_all_cell_areas, scale_fraction
from workflow.scripts.region_class_aggregation import (
    load_cell_mapping,
    weighted_sum_by_group,
)


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


if __name__ == "__main__":
    set_gdal_config("GDAL_CACHEMAX", 128 * 1024**2)

    # Inputs
    cell_mapping_path: str = snakemake.input.cell_mapping  # type: ignore[name-defined]
    # Suitability/area inputs as lists of file paths
    sr_files: list[str] = list(snakemake.input.sr)  # type: ignore[attr-defined]
    si_files: list[str] = list(snakemake.input.si)  # type: ignore[attr-defined]
    irrigated_share_path: str | None = getattr(snakemake.input, "irrigated_share", None)  # type: ignore[attr-defined]

    irrigated_area_source: str = snakemake.params.irrigated_area_source  # type: ignore[name-defined]

    cell_mapping = load_cell_mapping(cell_mapping_path)

    # Reference grid parameters from a suitability raster (rainfed)
    # Use first rainfed suitability file as reference
    if not sr_files:
        raise ValueError("No rainfed suitability files provided")
    sr0, src0 = read_raster_float(sr_files[0])
    try:
        height, width = sr0.shape
        transform = src0.transform
        crs = src0.crs
        cell_area_rows = calculate_all_cell_areas(src0, repeat=False)
    finally:
        src0.close()

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

    # Compute land area limits based on configuration. Both rainfed and
    # irrigated frontiers describe the same physical hectares, so we must
    # split them per pixel before aggregating to avoid double-counting:
    # irrigation cannot exceed the rainfed suitability of the cell, and any
    # land the irrigated bucket claims is removed from the rainfed pool.
    sr_base = scale_fraction(sr0)
    del sr0
    sr_max = (
        max_suitability(sr_files[1:], base=sr_base) if len(sr_files) > 1 else sr_base
    )
    np.multiply(sr_max, cell_area_rows[:, np.newaxis], out=sr_max)
    area_r_raw = sr_max

    def aggregate_area(area: np.ndarray, ws: str) -> pd.DataFrame:
        area_ha = weighted_sum_by_group(area, cell_mapping)
        index = pd.MultiIndex.from_product(
            [cell_mapping.regions, range(cell_mapping.n_classes)],
            names=["region", "resource_class"],
        )
        result = pd.DataFrame({"area_ha": area_ha}, index=index).reset_index()
        result["water_supply"] = ws
        return result

    if irrigated_area_source == "potential":
        area_i_raw = max_suitability(si_files)
        if area_i_raw.size:
            np.multiply(area_i_raw, cell_area_rows[:, np.newaxis], out=area_i_raw)
    else:  # "current"
        area_i_raw = load_scaled_fraction(
            irrigated_share_path,
            target_shape=(height, width),
            target_transform=transform,
            target_crs=crs,
        )
        if area_i_raw.size:
            np.multiply(area_i_raw, cell_area_rows[:, np.newaxis], out=area_i_raw)

    # Disjoint split per pixel: irrigation gets min(area_i_raw, area_r_raw),
    # rainfed gets the remainder. This keeps the model's land budget faithful
    # to the underlying physical cell area regardless of how the two
    # suitability rasters overlap.
    np.minimum(area_i_raw, area_r_raw, out=area_i_raw)
    np.subtract(area_r_raw, area_i_raw, out=area_r_raw)
    np.maximum(area_r_raw, 0.0, out=area_r_raw)
    area_i = area_i_raw
    area_r = area_r_raw

    df_r = aggregate_area(area_r, "r")
    del area_r

    df_i = aggregate_area(area_i, "i")
    del area_i
    out_df = pd.concat([df_r, df_i], ignore_index=True)
    out_df = out_df.set_index(["region", "water_supply", "resource_class"]).sort_index()

    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path)
