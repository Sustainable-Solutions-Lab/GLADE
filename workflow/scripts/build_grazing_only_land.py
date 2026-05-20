"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
import xarray as xr

from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    raster_bounds,
    scale_fraction,
)


def _load_transform(ds: xr.Dataset) -> Affine:
    try:
        return Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:  # pragma: no cover
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc


def _max_suitability(raster_paths: list[str]) -> tuple[np.ndarray, Affine, CRS | None]:
    arr: np.ndarray | None = None
    transform: Affine | None = None
    crs = None

    for path in raster_paths:
        with rasterio.open(path) as src:
            data = src.read(1, masked=False).astype(np.float32)
            data = scale_fraction(data)
            if arr is None:
                arr = data
                transform = src.transform
                crs = src.crs
            else:
                if src.transform != transform:
                    raise ValueError(
                        "GAEZ suitability rasters must share the same transform"
                    )
                np.maximum(arr, data, out=arr)

    if arr is None or transform is None:
        raise ValueError("No suitability rasters provided")
    return arr, transform, crs


def _build_dummy_raster(transform: Affine, width: int, height: int):
    class _Dummy:
        def __init__(self, transform: Affine, width: int, height: int) -> None:
            self.transform = transform
            self.shape = (height, width)
            xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            self.bounds = (xmin, ymin, xmax, ymax)

    return _Dummy(transform, width, height)


def _aggregate_by_region_class(
    array: np.ndarray,
    transform: Affine,
    regions_gdf: gpd.GeoDataFrame,
    class_labels: np.ndarray,
    region_id: np.ndarray,
    crs_wkt: str | None,
) -> pd.DataFrame:
    xmin, ymin, xmax, ymax = raster_bounds(transform, array.shape[1], array.shape[0])
    regions_for_extract = regions_gdf.reset_index()

    out: list[pd.DataFrame] = []
    valid_cells = (
        np.isfinite(array)
        & np.isfinite(region_id)
        & np.isfinite(class_labels)
        & (region_id >= 0)
        & (class_labels >= 0)
    )
    if not np.any(valid_cells):
        return pd.DataFrame(columns=["region", "resource_class", "area_ha"])

    class_ids = np.unique(class_labels[valid_cells].astype(int))
    for cls in class_ids:
        mask = class_labels == cls
        if not np.any(mask):
            continue
        work = np.full(array.shape, np.nan, dtype=np.float32)
        np.copyto(work, array, where=mask)
        src = NumPyRasterSource(
            work,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            nodata=np.nan,
            srs_wkt=crs_wkt,
        )
        stats = exact_extract(
            src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        if stats.empty:
            continue
        stats = stats.rename(columns={"sum": "area_ha"})
        stats["resource_class"] = int(cls)
        out.append(stats)

    if not out:
        return pd.DataFrame(columns=["region", "resource_class", "area_ha"])
    return pd.concat(out, ignore_index=True)


def _safe_fraction(data: np.ndarray) -> np.ndarray:
    out = np.nan_to_num(data.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(out, 0.0, 1.0, out=out)


if __name__ == "__main__":
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    lc_masks_path: str = snakemake.input.lc_masks  # type: ignore[name-defined]
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    suitability_paths: list[str] = list(snakemake.input.suitability)  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    classes_ds = xr.load_dataset(classes_path)
    region_id = classes_ds["region_id"].astype(np.int32).values
    resource_class = classes_ds["resource_class"].astype(np.int16).values
    transform = _load_transform(classes_ds)
    height, width = region_id.shape
    crs_wkt = classes_ds.attrs.get("crs_wkt")

    # Grassland fraction and grazing intensity from LUIcube
    luicube_ds = xr.load_dataset(luicube_path)
    grass_frac = _safe_fraction(
        luicube_ds["grassland_fraction"].astype(np.float32).values
    )
    grazing_intensity = _safe_fraction(
        luicube_ds["grazing_intensity"].astype(np.float32).values
    )

    # Cropland and forest fractions from ESA CCI land cover
    lc_ds = xr.load_dataset(lc_masks_path)
    crop_frac = _safe_fraction(lc_ds["cropland_fraction"].astype(np.float32).values)
    forest_frac = _safe_fraction(lc_ds["forest_fraction"].astype(np.float32).values)
    if grass_frac.shape != (height, width):
        raise ValueError("Grassland fraction grid does not match resource_classes grid")

    suitability, suit_transform, suit_crs = _max_suitability(suitability_paths)
    suitability = np.nan_to_num(suitability, nan=0.0, posinf=0.0, neginf=0.0)
    suitability = np.clip(suitability, 0.0, 1.0, out=suitability)
    if suitability.shape != (height, width) or suit_transform != transform:
        raise ValueError(
            "GAEZ suitability grid does not align with resource_classes grid"
        )
    if suit_crs and crs_wkt and suit_crs.to_wkt() != crs_wkt:
        raise ValueError(
            "GAEZ suitability grid CRS does not match resource_classes CRS"
        )

    convertible = np.clip(crop_frac + forest_frac, 0.0, 1.0)
    suitability_gap = np.clip(suitability - convertible, 0.0, 1.0)
    grass_candidate = np.clip(grass_frac - suitability_gap, 0.0, 1.0)
    max_unsuited = np.clip(1.0 - suitability, 0.0, 1.0)
    grazing_only_frac = np.minimum(grass_candidate, max_unsuited)

    # GI-weight to get the managed portion of grazing-only grassland;
    # the unmanaged remainder is natural land (savanna, steppe) and is
    # accounted for in LUC `natural_frac`, not as pasture supply.
    grazing_only_managed_frac = grazing_only_frac * grazing_intensity

    dummy_raster = _build_dummy_raster(transform, width, height)
    cell_area = calculate_all_cell_areas(dummy_raster, repeat=True)
    grazing_area = grazing_only_managed_frac * cell_area

    regions_gdf = gpd.read_file(regions_path)
    df = _aggregate_by_region_class(
        grazing_area,
        transform,
        regions_gdf,
        resource_class,
        region_id,
        crs_wkt,
    )
    df = df[df["area_ha"] > 0]
    df.sort_values(["region", "resource_class"], inplace=True, ignore_index=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
