# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for build_cropland_baseline_from_gaez._load_and_align_raster.

The original implementation silently called rasterio.reproject with
Resampling.sum across CRS reprojection, which is not area-conserving
for extensive quantities like harvested area in kha. The fix replaces
the silent miscalculation with an explicit NotImplementedError so the
pipeline fails loudly if anyone ever changes the target grid's CRS or
transform away from the RES06 native grid.
"""

from affine import Affine
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS

from workflow.scripts.build_cropland_baseline_from_gaez import (
    _load_and_align_raster,
)


def _write_test_raster(path, *, transform, crs, shape=(4, 4)):
    data = np.ones(shape, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=shape[0],
        width=shape[1],
        count=1,
        dtype=np.float32,
        transform=transform,
        crs=crs,
    ) as dst:
        dst.write(data, 1)


def test_load_align_no_resample_passes_through(tmp_path):
    """When source and target grids match, no reprojection is attempted."""
    transform = Affine(0.1, 0.0, -1.0, 0.0, -0.1, 1.0)
    crs = CRS.from_epsg(4326)
    raster_path = tmp_path / "src.tif"
    _write_test_raster(raster_path, transform=transform, crs=crs)

    arr = _load_and_align_raster(
        str(raster_path),
        target_shape=(4, 4),
        target_transform=transform,
        target_crs=crs,
    )
    assert arr.shape == (4, 4)
    assert np.allclose(arr, 1.0)


def test_load_align_raises_on_shape_mismatch(tmp_path):
    """Mismatched shape would require reprojection; must raise."""
    transform = Affine(0.1, 0.0, -1.0, 0.0, -0.1, 1.0)
    crs = CRS.from_epsg(4326)
    raster_path = tmp_path / "src.tif"
    _write_test_raster(raster_path, transform=transform, crs=crs, shape=(4, 4))

    with pytest.raises(NotImplementedError, match="not area-conserving"):
        _load_and_align_raster(
            str(raster_path),
            target_shape=(2, 2),
            target_transform=transform,
            target_crs=crs,
        )


def test_load_align_raises_on_crs_change(tmp_path):
    """Different CRS forces a non-area-conserving reprojection; must raise."""
    transform = Affine(0.1, 0.0, -1.0, 0.0, -0.1, 1.0)
    src_crs = CRS.from_epsg(4326)
    target_crs = CRS.from_epsg(3857)
    raster_path = tmp_path / "src.tif"
    _write_test_raster(raster_path, transform=transform, crs=src_crs)

    with pytest.raises(NotImplementedError, match="not area-conserving"):
        _load_and_align_raster(
            str(raster_path),
            target_shape=(4, 4),
            target_transform=transform,
            target_crs=target_crs,
        )
