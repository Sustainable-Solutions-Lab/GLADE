# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import CRS
import pytest
from shapely.geometry import box
import xarray as xr

from workflow.scripts.build_crop_yield_cell_mapping import build_cell_mapping
from workflow.scripts.crop_yield_aggregation import (
    load_cell_mapping,
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
        geometry=[box(0.0, 0.0, 1.5, 2.0), box(1.5, 0.0, 3.0, 2.0)],
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
    np.testing.assert_allclose(sums, [6.0, 15.0, 0.0, 15.0])


def test_group_aggregation_matches_exactextract_empty_group_semantics(cell_mapping):
    values = np.full(cell_mapping.shape, np.nan)

    means = weighted_mean_by_group(values, cell_mapping)
    sums = weighted_sum_by_group(values, cell_mapping)

    assert np.isnan(means).all()
    np.testing.assert_array_equal(sums, np.zeros(cell_mapping.n_groups))
