"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Build exact region/resource-class coverage arrays for the common GAEZ grid.

Inputs
------
``classes``
    NetCDF resource-class raster, including the grid transform and CRS.
``regions``
    GeoJSON optimization regions.

Output
------
``mapping``
    NPZ arrays used by every crop-yield aggregation for this configuration.
"""

from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import exact_extract  # noqa: E402
from exactextract.raster import NumPyRasterSource  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from pyproj import CRS  # noqa: E402
import xarray as xr  # noqa: E402


def build_cell_mapping(classes_path: str, regions_path: str, output_path: str) -> None:
    """Write exact region/class coverage for each relevant raster cell."""
    with xr.open_dataset(classes_path) as classes_ds:
        class_labels = classes_ds["resource_class"].load().values
        transform = np.asarray(classes_ds.attrs["transform"], dtype=float)
        crs_wkt = str(classes_ds.attrs["crs_wkt"])
    height, width = class_labels.shape
    if transform[2] != 0 or transform[4] != 0:
        raise ValueError("Rotated resource-class grids are not supported")

    xmin = transform[0]
    xmax = xmin + width * transform[1]
    ymax = transform[3]
    ymin = ymax + height * transform[5]
    regions = gpd.read_file(regions_path)
    grid_crs = CRS.from_wkt(crs_wkt)
    if regions.crs and regions.crs != grid_crs:
        regions = regions.to_crs(grid_crs)
    regions = regions.reset_index()

    grid = NumPyRasterSource(
        class_labels,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        srs_wkt=crs_wkt,
    )
    extracted = exact_extract(
        grid,
        regions,
        ["cell_id", "coverage"],
        include_cols=["region"],
        output="pandas",
    )

    lengths = np.fromiter(
        (len(cell_ids) for cell_ids in extracted["cell_id"]),
        dtype=np.int64,
    )
    cell_ids = np.concatenate(extracted["cell_id"].to_numpy()).astype(np.int32)
    coverage = np.concatenate(extracted["coverage"].to_numpy()).astype(np.float64)
    region_ids = np.repeat(np.arange(len(extracted), dtype=np.int32), lengths)
    class_ids = class_labels.ravel()[cell_ids]
    valid = class_ids >= 0
    cell_ids = cell_ids[valid]
    coverage = coverage[valid]
    class_ids = class_ids[valid]

    if not np.any(valid):
        raise ValueError("Resource-class grid does not contain any valid classes")
    n_classes = int(class_ids.max()) + 1
    group_ids = (region_ids[valid] * n_classes + class_ids).astype(np.int32)
    region_names = extracted["region"].astype(str).to_numpy(dtype=str)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output_path,
        cell_ids=cell_ids,
        coverage=coverage,
        group_ids=group_ids,
        regions=region_names,
        n_classes=np.array(n_classes, dtype=np.int32),
        height=np.array(height, dtype=np.int32),
        width=np.array(width, dtype=np.int32),
    )


if __name__ == "__main__":
    build_cell_mapping(
        snakemake.input.classes,  # type: ignore[name-defined]
        snakemake.input.regions,  # type: ignore[name-defined]
        snakemake.output.mapping,  # type: ignore[name-defined]
    )
