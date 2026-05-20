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
import xarray as xr

from workflow.scripts.raster_utils import calculate_all_cell_areas, raster_bounds


def _build_dummy_raster(transform: Affine, width: int, height: int):
    class _DummyRaster:
        def __init__(self, transform: Affine, width: int, height: int) -> None:
            self.transform = transform
            self.shape = (height, width)
            xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            # raster_utils.calculate_all_cell_areas expects (left, bottom, right, top)
            self.bounds = (xmin, ymin, xmax, ymax)

    return _DummyRaster(transform, width, height)


if __name__ == "__main__":
    grassland_nc: str = snakemake.input.grassland  # type: ignore[name-defined]
    classes_nc: str = snakemake.input.classes  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]

    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    ds_classes = xr.load_dataset(classes_nc)
    class_labels = ds_classes["resource_class"].values.astype(np.int16)
    if class_labels.ndim != 2:
        raise ValueError("Expected 2D resource_class array")

    transform_tuple = ds_classes.attrs.get("transform")
    if transform_tuple is None:
        raise ValueError("resource_classes.nc missing 'transform' attribute")
    transform = Affine.from_gdal(*transform_tuple)

    height, width = class_labels.shape
    bbox = raster_bounds(transform, width, height)
    crs_wkt = ds_classes.attrs.get("crs_wkt")

    # Target grid centre coordinates (descending latitude to match array layout)
    row_indices = np.arange(height)
    col_indices = np.arange(width)
    target_lat_desc = transform.f + transform.e * (row_indices + 0.5)
    target_lon = transform.c + transform.a * (col_indices + 0.5)
    target_lat_asc = target_lat_desc[::-1]

    ds = xr.open_dataset(grassland_nc, decode_times=False)
    if "yield-mgr-noirr" not in ds.data_vars:
        raise KeyError("Expected 'yield-mgr-noirr' variable in grassland dataset")

    # Sanity-check the dataset provenance against the expected ISIMIP2a /
    # LPJmL GGCMI phase2 release.
    expected_institution = "Potsdam Institute for Climate Impact Research"
    expected_title_prefix = "LPJmL simulations for Ag-GRID GGCMI"
    expected_units = "t ha-1 yr-1"
    institution = str(ds.attrs.get("institution", ""))
    title = str(ds.attrs.get("title", ""))
    units = str(ds["yield-mgr-noirr"].attrs.get("units", ""))
    if (
        institution != expected_institution
        or not title.startswith(expected_title_prefix)
        or units != expected_units
    ):
        raise ValueError(
            "ISIMIP grassland NetCDF provenance does not match the pinned "
            f"vintage. Got institution={institution!r}, title={title!r}, "
            f"units={units!r}."
        )

    grass_yield = ds["yield-mgr-noirr"].astype(float)
    mean_yield = grass_yield.mean(dim="time", skipna=True)

    mean_yield = mean_yield.sortby("lat")
    regridded = mean_yield.interp(lat=target_lat_asc, lon=target_lon)
    yield_grid = np.flip(regridded.values, axis=0)
    yield_grid = np.where(np.isfinite(yield_grid), yield_grid, np.nan)
    if yield_grid.shape != (height, width):
        raise ValueError("Resampled grassland yield grid has unexpected shape")

    dummy_src = _build_dummy_raster(transform, width, height)
    cell_area_ha = calculate_all_cell_areas(dummy_src)

    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and regions_gdf.crs.to_epsg() != 4326:
        regions_gdf = regions_gdf.to_crs("EPSG:4326")
    regions_for_extract = regions_gdf.reset_index()

    xmin, ymin, xmax, ymax = bbox

    data_frames: list[pd.DataFrame] = []
    valid_classes = sorted(
        int(c) for c in np.unique(class_labels) if np.isfinite(c) and c >= 0
    )

    for cls in valid_classes:
        mask = class_labels == cls
        if not np.any(mask):
            continue

        yield_masked = np.where(mask, yield_grid, np.nan)
        area_masked = np.where(mask, cell_area_ha, np.nan)

        yield_src = NumPyRasterSource(
            yield_masked,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            nodata=np.nan,
            srs_wkt=crs_wkt,
        )
        area_src = NumPyRasterSource(
            area_masked,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            nodata=np.nan,
            srs_wkt=crs_wkt,
        )

        yield_stats = exact_extract(
            yield_src,
            regions_for_extract,
            ["mean"],
            include_cols=["region"],
            output="pandas",
        )
        area_stats = exact_extract(
            area_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )

        if yield_stats.empty or area_stats.empty:
            continue

        merged = yield_stats.rename(columns={"mean": "yield"}).merge(
            area_stats.rename(columns={"sum": "suitable_area"}),
            on="region",
            how="inner",
        )
        merged["resource_class"] = cls
        data_frames.append(merged)

    if data_frames:
        out_df = (
            pd.concat(data_frames, ignore_index=True)
            .set_index(["region", "resource_class"])
            .sort_index()
        )
    else:
        out_df = pd.DataFrame(
            columns=["region", "resource_class", "yield", "suitable_area"]
        ).set_index(["region", "resource_class"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path)
