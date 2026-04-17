"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject
import xarray as xr

from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    raster_bounds,
    scale_fraction,
)


def _build_dummy_raster(transform: Affine, width: int, height: int):
    class _DummyRaster:
        def __init__(self, transform: Affine, width: int, height: int) -> None:
            self.transform = transform
            self.shape = (height, width)
            xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            self.bounds = (xmin, ymin, xmax, ymax)

    return _DummyRaster(transform, width, height)


def _transform_from_attrs(ds: xr.Dataset) -> Affine:
    try:
        return Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:  # pragma: no cover - sanity guard
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc


def _load_irrigated_share(
    path: str,
    target_shape: tuple[int, int],
    target_transform: Affine,
    *,
    target_crs,
) -> np.ndarray:
    """Load or resample irrigated-share raster to the target grid."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        src_transform = src.transform
        src_crs = src.crs
        nodata = src.nodata

    if nodata is not None:
        arr[arr == np.float32(nodata)] = np.nan

    needs_resample = (
        arr.shape != target_shape
        or src_transform != target_transform
        or (src_crs is not None and src_crs != target_crs)
    )

    if needs_resample:
        if src_crs is None:
            raise ValueError("Irrigated share raster missing CRS information")
        dst = np.full(target_shape, np.nan, dtype=np.float32)
        reproject(
            source=arr,
            destination=dst,
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=target_transform,
            dst_crs=target_crs,
            resampling=Resampling.average,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )
        arr = dst

    return scale_fraction(arr)


if __name__ == "__main__":
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    lc_masks_path: str = snakemake.input.lc_masks  # type: ignore[name-defined]
    irrigated_share_path: str = snakemake.input.irrigated_share  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    classes_ds = xr.load_dataset(classes_path)
    region_id = classes_ds["region_id"].astype(np.int32).values
    resource_class = classes_ds["resource_class"].astype(np.int16).values
    transform = _transform_from_attrs(classes_ds)
    height, width = region_id.shape

    lc_ds = xr.load_dataset(lc_masks_path)
    cropland_frac = lc_ds["cropland_fraction"].astype(np.float32).values
    if cropland_frac.shape != region_id.shape:
        raise ValueError(
            "Cropland fraction grid does not match the resource_classes grid"
        )

    np.copyto(cropland_frac, 0.0, where=~np.isfinite(cropland_frac))
    np.clip(cropland_frac, 0.0, 1.0, out=cropland_frac)

    dummy_raster = _build_dummy_raster(transform, width, height)
    cell_area = calculate_all_cell_areas(dummy_raster)
    cropland_area = cropland_frac * cell_area

    target_crs = CRS.from_wkt(classes_ds.attrs["crs_wkt"])
    irrigated_share = _load_irrigated_share(
        irrigated_share_path, cropland_area.shape, transform, target_crs=target_crs
    )
    np.copyto(irrigated_share, 0.0, where=~np.isfinite(irrigated_share))
    irrigated_share = np.clip(irrigated_share, 0.0, 1.0, out=irrigated_share)

    irrigated_area = cropland_area * irrigated_share
    rainfed_area = cropland_area - irrigated_area
    np.clip(rainfed_area, 0.0, None, out=rainfed_area)

    valid = (
        np.isfinite(region_id)
        & np.isfinite(resource_class)
        & (region_id >= 0)
        & (resource_class >= 0)
        & ((irrigated_area > 0.0) | (rainfed_area > 0.0) | (cropland_area > 0.0))
    )
    if not np.any(valid):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            columns=["region", "resource_class", "water_supply", "area_ha"]
        ).to_csv(output_path, index=False)
        raise SystemExit

    region_vals = region_id[valid].astype(np.int32, copy=False)
    class_vals = resource_class[valid].astype(np.int32, copy=False)
    irrigated_vals = irrigated_area[valid].astype(np.float64, copy=False)
    rainfed_vals = rainfed_area[valid].astype(np.float64, copy=False)

    regions_gdf = gpd.read_file(regions_path)
    if "region" not in regions_gdf.columns:
        raise ValueError("regions.geojson must contain a 'region' column")
    region_lookup = (
        regions_gdf.reset_index().set_index("index")["region"].astype(str).to_dict()
    )

    frames: list[pd.DataFrame] = []
    for water_supply, values in (("i", irrigated_vals), ("r", rainfed_vals)):
        positive = values > 0.0
        if not np.any(positive):
            continue
        df = (
            pd.DataFrame(
                {
                    "region_id": region_vals[positive],
                    "resource_class": class_vals[positive],
                    "area_ha": values[positive],
                }
            )
            .groupby(["region_id", "resource_class"], as_index=False)["area_ha"]
            .sum()
        )
        df["region"] = df["region_id"].map(region_lookup)
        missing = df["region"].isna()
        if missing.any():
            missing_ids = sorted(df.loc[missing, "region_id"].unique().tolist())
            raise ValueError(
                "Region IDs in resource_classes.nc missing from regions.geojson: "
                + ", ".join(str(mid) for mid in missing_ids)
            )
        df["water_supply"] = water_supply
        frames.append(df[["region", "resource_class", "water_supply", "area_ha"]])

    if frames:
        result = (
            pd.concat(frames, ignore_index=True)
            .sort_values(["region", "water_supply", "resource_class"])
            .reset_index(drop=True)
        )
    else:
        result = pd.DataFrame(
            columns=["region", "resource_class", "water_supply", "area_ha"]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
