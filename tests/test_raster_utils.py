# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for raster utility functions."""

import numpy as np
from pyproj import Geod
import pytest

from workflow.scripts.raster_utils import raster_bounds, scale_fraction

# ---------------------------------------------------------------------------
# Tests: scale_fraction
# ---------------------------------------------------------------------------


class TestScaleFraction:
    """Tests for auto-detecting scaling and normalizing arrays to [0, 1]."""

    def test_already_in_zero_one_range(self):
        """Array in [0, 1] range is returned unchanged."""
        arr = np.array([0.0, 0.5, 1.0], dtype=np.float32)
        result = scale_fraction(arr)
        np.testing.assert_array_almost_equal(result, [0.0, 0.5, 1.0])

    def test_percent_range_scaled_to_fraction(self):
        """Array in [0, 100] range is divided by 100."""
        arr = np.array([0, 50, 100], dtype=np.float32)
        result = scale_fraction(arr)
        np.testing.assert_array_almost_equal(result, [0.0, 0.5, 1.0])

    def test_ten_thousand_range_scaled_to_fraction(self):
        """Array in [0, 10000] range is divided by 10000."""
        arr = np.array([0, 5000, 10000], dtype=np.float32)
        result = scale_fraction(arr)
        np.testing.assert_array_almost_equal(result, [0.0, 0.5, 1.0])

    def test_inf_becomes_nan(self):
        """Infinite values are converted to NaN; finite values are scaled."""
        arr = np.array([0.0, 50.0, np.inf], dtype=np.float32)
        result = scale_fraction(arr)
        # max of finite values is 50 which is > 1.5 and <= 100 => divide by 100
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert np.isnan(result[2])

    def test_all_nan_stays_all_nan(self):
        """Array of all NaN values remains all NaN."""
        arr = np.array([np.nan, np.nan, np.nan], dtype=np.float32)
        result = scale_fraction(arr)
        assert np.all(np.isnan(result))

    def test_integer_array_converted_to_float(self):
        """Integer input is converted to float and scaled."""
        arr = np.array([0, 50, 100], dtype=np.int32)
        result = scale_fraction(arr)
        assert result.dtype == np.float32
        np.testing.assert_array_almost_equal(result, [0.0, 0.5, 1.0])

    def test_negative_values_clipped_to_zero(self):
        """Negative values are clipped to 0 after scaling."""
        arr = np.array([-10.0, 0.0, 50.0, 100.0], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.0)
        assert result[2] == pytest.approx(0.5)
        assert result[3] == pytest.approx(1.0)

    def test_values_slightly_above_one_clipped(self):
        """Values above 1.0 but below 1.5 are clipped to 1.0 (no scaling)."""
        arr = np.array([0.0, 0.5, 1.3], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(1.0)

    def test_nan_mixed_with_valid_percent_values(self):
        """NaN values pass through while valid values are scaled."""
        arr = np.array([np.nan, 0.0, 50.0, 100.0], dtype=np.float32)
        result = scale_fraction(arr)
        assert np.isnan(result[0])
        assert result[1] == pytest.approx(0.0)
        assert result[2] == pytest.approx(0.5)
        assert result[3] == pytest.approx(1.0)

    def test_negative_inf_becomes_nan(self):
        """Negative infinity is converted to NaN."""
        arr = np.array([-np.inf, 50.0], dtype=np.float32)
        result = scale_fraction(arr)
        assert np.isnan(result[0])
        assert result[1] == pytest.approx(0.5)

    def test_boundary_max_at_1_5(self):
        """Max exactly at 1.5 triggers no scaling (1.5 <= 1.5, not > 1.5)."""
        arr = np.array([0.0, 1.5], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(1.0)  # clipped

    def test_boundary_max_just_above_1_5(self):
        """Max just above 1.5 triggers percent scaling (divide by 100)."""
        arr = np.array([0.0, 1.6], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.016)

    def test_boundary_max_at_100(self):
        """Max at exactly 100 triggers percent scaling (100 <= 100)."""
        arr = np.array([0.0, 100.0], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(1.0)

    def test_boundary_max_just_above_100(self):
        """Max above 100 triggers ten-thousand scaling."""
        arr = np.array([0.0, 101.0], dtype=np.float32)
        result = scale_fraction(arr)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(101.0 / 10000.0)


# ---------------------------------------------------------------------------
# Tests: raster_bounds
# ---------------------------------------------------------------------------


class _MockTransform:
    """Minimal mock for an affine transform with a, c, e, f attributes."""

    def __init__(self, a: float, c: float, e: float, f: float):
        self.a = a
        self.c = c
        self.e = e
        self.f = f


class TestRasterBounds:
    """Tests for computing bounding boxes from raster transforms."""

    def test_global_geographic_raster(self):
        """Standard 1-degree global raster: (-180, -90, 180, 90)."""
        transform = _MockTransform(a=1.0, c=-180.0, e=-1.0, f=90.0)
        xmin, ymin, xmax, ymax = raster_bounds(transform, width=360, height=180)
        assert xmin == pytest.approx(-180.0)
        assert ymin == pytest.approx(-90.0)
        assert xmax == pytest.approx(180.0)
        assert ymax == pytest.approx(90.0)

    def test_smaller_region(self):
        """Smaller 0.5-degree raster covering (0, 45) to (10, 50)."""
        transform = _MockTransform(a=0.5, c=0.0, e=-0.5, f=50.0)
        xmin, ymin, xmax, ymax = raster_bounds(transform, width=20, height=10)
        assert xmin == pytest.approx(0.0)
        assert ymin == pytest.approx(45.0)
        assert xmax == pytest.approx(10.0)
        assert ymax == pytest.approx(50.0)

    def test_fine_resolution(self):
        """Fine resolution raster (5 arc-minute ~ 0.0833 degrees)."""
        res = 5.0 / 60.0  # 5 arc-minutes
        transform = _MockTransform(a=res, c=-10.0, e=-res, f=60.0)
        xmin, ymin, xmax, ymax = raster_bounds(transform, width=120, height=60)
        assert xmin == pytest.approx(-10.0)
        assert xmax == pytest.approx(-10.0 + 120 * res)
        assert ymax == pytest.approx(60.0)
        assert ymin == pytest.approx(60.0 - 60 * res)

    def test_single_pixel(self):
        """Single pixel raster."""
        transform = _MockTransform(a=1.0, c=10.0, e=-1.0, f=50.0)
        xmin, ymin, xmax, ymax = raster_bounds(transform, width=1, height=1)
        assert xmin == pytest.approx(10.0)
        assert ymin == pytest.approx(49.0)
        assert xmax == pytest.approx(11.0)
        assert ymax == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tests: calculate_all_cell_areas (sanity checks via pyproj.Geod)
# ---------------------------------------------------------------------------


class TestCellAreasSanityCheck:
    """Ballpark sanity checks for geodesic cell area calculations.

    Rather than mocking a full rasterio DatasetReader, these tests verify
    the underlying geodesic math using pyproj.Geod directly, mirroring
    the approach used inside calculate_all_cell_areas.
    """

    @staticmethod
    def _cell_area_ha(lat: float, pixel_deg: float = 1.0) -> float:
        """Compute geodesic area of a single cell in hectares.

        Uses the same polygon-based approach as calculate_all_cell_areas.
        """
        geod = Geod(ellps="WGS84")
        lat_top = lat + pixel_deg / 2
        lat_bottom = lat - pixel_deg / 2
        lon_left = 0.0
        lon_right = pixel_deg
        lons = [lon_left, lon_right, lon_right, lon_left, lon_left]
        lats = [lat_bottom, lat_bottom, lat_top, lat_top, lat_bottom]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats)
        return abs(area_m2) / 10000.0

    def test_equator_cell_area(self):
        """A 1x1 degree cell at the equator is approximately 12,321 km2."""
        area_ha = self._cell_area_ha(lat=0.0, pixel_deg=1.0)
        area_km2 = area_ha / 100.0
        # Expected: ~12,321 km2; allow 5% tolerance
        assert area_km2 == pytest.approx(12321, rel=0.05)

    def test_sixty_degrees_cell_area(self):
        """A 1x1 degree cell at 60N is approximately half the equator area."""
        area_equator = self._cell_area_ha(lat=0.0, pixel_deg=1.0)
        area_60 = self._cell_area_ha(lat=60.0, pixel_deg=1.0)
        ratio = area_60 / area_equator
        # At 60 degrees, cos(60) = 0.5, so area should be roughly half
        assert ratio == pytest.approx(0.5, abs=0.05)

    def test_high_latitude_smaller_than_equator(self):
        """Cell area decreases monotonically with latitude."""
        area_0 = self._cell_area_ha(lat=0.0)
        area_30 = self._cell_area_ha(lat=30.0)
        area_60 = self._cell_area_ha(lat=60.0)
        area_80 = self._cell_area_ha(lat=80.0)
        assert area_0 > area_30 > area_60 > area_80

    def test_symmetric_hemispheres(self):
        """Cell areas are the same for symmetric latitudes in N and S."""
        area_north = self._cell_area_ha(lat=45.0)
        area_south = self._cell_area_ha(lat=-45.0)
        assert area_north == pytest.approx(area_south, rel=1e-6)
