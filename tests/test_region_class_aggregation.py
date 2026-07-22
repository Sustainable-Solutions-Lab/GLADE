# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import CRS
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box
import xarray as xr

from workflow.scripts.build_harvested_area import _extract_harvested_area
from workflow.scripts.build_region_class_cell_mapping import build_cell_mapping
from workflow.scripts.region_class_aggregation import (
    load_cell_mapping,
    validate_raster_grid,
    weighted_mean_by_group,
    weighted_sum_by_group,
)


@pytest.fixture
def cell_mapping(tmp_path: Path):
    classes_path = tmp_path / "classes.nc"
    regions_path = tmp_path / "regions.geojson"
    mapping_path = tmp_path / "mapping.npz"

    classes = xr.Dataset(
        {"resource_class": (("y", "x"), np.array([[0, 1, -1], [0, 1, -1]]))},
        attrs={
            "transform": np.array([0.0, 1.0, 0.0, 2.0, 0.0, -1.0]),
            "crs_wkt": CRS.from_epsg(4326).to_wkt(),
        },
    )
    classes.to_netcdf(classes_path)
    regions = gpd.GeoDataFrame(
        {"region": ["r0", "r1"]},
        geometry=[box(0.0, 0.0, 1.3, 2.0), box(1.3, 0.0, 3.0, 2.0)],
        crs="EPSG:4326",
    )
    regions.to_file(regions_path)

    build_cell_mapping(str(classes_path), str(regions_path), str(mapping_path))
    return load_cell_mapping(str(mapping_path))


def test_cell_mapping_preserves_partial_region_coverage(cell_mapping):
    values = np.array([[2.0, 10.0, 100.0], [4.0, 20.0, 200.0]])

    means = weighted_mean_by_group(values, cell_mapping)
    sums = weighted_sum_by_group(values, cell_mapping)

    np.testing.assert_allclose(means, [3.0, 15.0, np.nan, 15.0], equal_nan=True)
    np.testing.assert_allclose(sums, [6.0, 9.0, 0.0, 21.0])
    assert cell_mapping.coverage.dtype == np.float64


def test_group_aggregation_matches_exactextract_empty_group_semantics(cell_mapping):
    values = np.full(cell_mapping.shape, np.nan)

    means = weighted_mean_by_group(values, cell_mapping)
    sums = weighted_sum_by_group(values, cell_mapping)

    assert np.isnan(means).all()
    np.testing.assert_array_equal(sums, np.zeros(cell_mapping.n_groups))


def test_harvested_area_extraction_preserves_class_major_order(cell_mapping):
    values = np.array([[2.0, 10.0, np.nan], [4.0, 20.0, np.nan]])

    result = _extract_harvested_area(values, cell_mapping)

    assert result[["region", "resource_class"]].to_records(index=False).tolist() == [
        ("r0", 0),
        ("r1", 0),
        ("r0", 1),
        ("r1", 1),
    ]
    np.testing.assert_allclose(result["value"], [6.0, 0.0, 9.0, 21.0])


def test_raster_grid_validation_rejects_shifted_transform(cell_mapping):
    values = np.zeros(cell_mapping.shape, dtype=np.float32)
    with (
        MemoryFile() as memory_file,
        memory_file.open(
            driver="GTiff",
            height=2,
            width=3,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(0.1, 2.0, 1.0, 1.0),
        ) as source,
        pytest.raises(ValueError, match="transform"),
    ):
        validate_raster_grid(values, source, cell_mapping)


def test_cell_mapping_load_preserves_float64_coverage(tmp_path: Path):
    mapping_path = tmp_path / "mapping.npz"
    expected = np.array([0.30000000000000004], dtype=np.float64)
    np.savez(
        mapping_path,
        cell_ids=np.array([0], dtype=np.int32),
        coverage=expected,
        group_ids=np.array([0], dtype=np.int32),
        regions=np.array(["r0"]),
        n_classes=np.array(1, dtype=np.int32),
        height=np.array(1, dtype=np.int32),
        width=np.array(1, dtype=np.int32),
        transform=np.array([0.0, 1.0, 0.0, 1.0, 0.0, -1.0]),
        crs_wkt=np.array(CRS.from_epsg(4326).to_wkt()),
    )

    mapping = load_cell_mapping(str(mapping_path))

    np.testing.assert_array_equal(mapping.coverage, expected)
    assert mapping.coverage.dtype == np.float64
