# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for land-use-change carbon coefficient computation."""

import textwrap

from affine import Affine
import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
import pandas as pd
import pytest
from shapely.geometry import box
import xarray as xr

from workflow.scripts.build_luc_carbon_coefficients import (
    CO2_PER_C,
    _correct_subpixel_soc,
    _decompose_agb,
    _ensure_mode_zero,
    _zone_index,
    _zone_parameters,
)

gdal.UseExceptions()
osr.UseExceptions()

_srs = osr.SpatialReference()
_srs.ImportFromEPSG(4326)
WGS84_WKT = _srs.ExportToWkt()

# ---------------------------------------------------------------------------
# Tests: CO2_PER_C constant
# ---------------------------------------------------------------------------


class TestCO2PerC:
    """Tests for the CO2/C conversion constant."""

    def test_value(self):
        """CO2_PER_C equals the molar mass ratio 44/12."""
        assert pytest.approx(44.0 / 12.0) == CO2_PER_C
        assert pytest.approx(3.66667, rel=1e-4) == CO2_PER_C


# ---------------------------------------------------------------------------
# Tests: _zone_index
# ---------------------------------------------------------------------------


class TestZoneIndex:
    """Tests for latitude-based climate zone assignment."""

    def test_equator_is_tropical(self):
        """Latitude 0 (equator) maps to tropical (zone 0)."""
        result = _zone_index(np.array([0.0], dtype=np.float32), width=1)
        assert result[0, 0] == 0

    def test_lat_20_is_tropical(self):
        """Latitude 20 is within the tropics."""
        result = _zone_index(np.array([20.0], dtype=np.float32), width=1)
        assert result[0, 0] == 0

    def test_lat_23_5_is_temperate(self):
        """Latitude 23.5 is at the tropical-temperate boundary (>= 23.5 is temperate)."""
        result = _zone_index(np.array([23.5], dtype=np.float32), width=1)
        assert result[0, 0] == 1

    def test_lat_45_is_temperate(self):
        """Latitude 45 is temperate."""
        result = _zone_index(np.array([45.0], dtype=np.float32), width=1)
        assert result[0, 0] == 1

    def test_lat_50_is_boreal(self):
        """Latitude 50 is at the temperate-boreal boundary (>= 50 is boreal)."""
        result = _zone_index(np.array([50.0], dtype=np.float32), width=1)
        assert result[0, 0] == 2

    def test_lat_70_is_boreal(self):
        """Latitude 70 is well into the boreal zone."""
        result = _zone_index(np.array([70.0], dtype=np.float32), width=1)
        assert result[0, 0] == 2

    def test_southern_hemisphere_tropical(self):
        """Latitude -10 (southern hemisphere) maps to tropical via abs(lat)."""
        result = _zone_index(np.array([-10.0], dtype=np.float32), width=1)
        assert result[0, 0] == 0

    def test_southern_hemisphere_boreal(self):
        """Latitude -55 (southern hemisphere) maps to boreal via abs(lat)."""
        result = _zone_index(np.array([-55.0], dtype=np.float32), width=1)
        assert result[0, 0] == 2

    def test_width_replication(self):
        """Result is replicated across columns when width > 1."""
        lats = np.array([0.0, 45.0, 60.0], dtype=np.float32)
        result = _zone_index(lats, width=3)
        assert result.shape == (3, 3)
        for row in range(3):
            assert np.all(result[row, :] == result[row, 0])
        assert result[0, 0] == 0  # tropical
        assert result[1, 0] == 1  # temperate
        assert result[2, 0] == 2  # boreal

    def test_output_shape(self):
        """Output shape is (len(latitudes), width)."""
        lats = np.array([10.0, 30.0, 55.0, 75.0], dtype=np.float32)
        result = _zone_index(lats, width=5)
        assert result.shape == (4, 5)

    def test_output_dtype(self):
        """Output dtype is int8."""
        result = _zone_index(np.array([0.0], dtype=np.float32), width=1)
        assert result.dtype == np.int8


# ---------------------------------------------------------------------------
# Tests: _zone_parameters
# ---------------------------------------------------------------------------


class TestZoneParameters:
    """Tests for loading zone parameter CSV files."""

    def test_valid_csv(self, tmp_path):
        """A valid CSV with all three zones returns a dict of numpy arrays."""
        csv_content = textwrap.dedent("""\
            zone,parameter,value,reference
            tropical,bgb_ratio_nat,0.24,ref1
            temperate,bgb_ratio_nat,0.26,ref1
            boreal,bgb_ratio_nat,0.39,ref1
            tropical,soc_depth_factor,1.5,ref2
            temperate,soc_depth_factor,1.4,ref2
            boreal,soc_depth_factor,1.2,ref2
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        result = _zone_parameters(str(csv_path))

        assert "bgb_ratio_nat" in result
        assert "soc_depth_factor" in result
        assert len(result["bgb_ratio_nat"]) == 3
        assert result["bgb_ratio_nat"][0] == pytest.approx(0.24)  # tropical
        assert result["bgb_ratio_nat"][1] == pytest.approx(0.26)  # temperate
        assert result["bgb_ratio_nat"][2] == pytest.approx(0.39)  # boreal
        assert result["soc_depth_factor"][0] == pytest.approx(1.5)
        assert result["soc_depth_factor"][1] == pytest.approx(1.4)
        assert result["soc_depth_factor"][2] == pytest.approx(1.2)

    def test_returns_float32_arrays(self, tmp_path):
        """Returned arrays have float32 dtype."""
        csv_content = textwrap.dedent("""\
            zone,parameter,value,reference
            tropical,param_a,1.0,ref
            temperate,param_a,2.0,ref
            boreal,param_a,3.0,ref
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        result = _zone_parameters(str(csv_path))
        assert result["param_a"].dtype == np.float32

    def test_ordered_by_zone_order(self, tmp_path):
        """Output follows ZONE_ORDER even if CSV rows are in a different order."""
        csv_content = textwrap.dedent("""\
            zone,parameter,value,reference
            boreal,val,3.0,ref
            tropical,val,1.0,ref
            temperate,val,2.0,ref
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        result = _zone_parameters(str(csv_path))
        assert result["val"][0] == pytest.approx(1.0)  # tropical
        assert result["val"][1] == pytest.approx(2.0)  # temperate
        assert result["val"][2] == pytest.approx(3.0)  # boreal

    def test_missing_zone_raises(self, tmp_path):
        """A CSV missing one of the required zones raises ValueError."""
        csv_content = textwrap.dedent("""\
            zone,parameter,value,reference
            tropical,bgb_ratio_nat,0.24,ref
            temperate,bgb_ratio_nat,0.26,ref
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError, match="boreal"):
            _zone_parameters(str(csv_path))

    def test_comments_ignored(self, tmp_path):
        """Lines starting with # are treated as comments and ignored."""
        csv_content = textwrap.dedent("""\
            # This is a comment
            zone,parameter,value,reference
            tropical,val,10.0,ref
            temperate,val,20.0,ref
            boreal,val,30.0,ref
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        result = _zone_parameters(str(csv_path))
        assert result["val"][0] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Tests: _ensure_mode_zero
# ---------------------------------------------------------------------------


class TestEnsureModeZero:
    """Tests for the managed flux mode guard."""

    def test_zero_lowercase(self):
        """'zero' is accepted without error."""
        _ensure_mode_zero("zero")

    def test_zero_uppercase(self):
        """'ZERO' is accepted (case insensitive)."""
        _ensure_mode_zero("ZERO")

    def test_zero_mixed_case(self):
        """'Zero' is accepted (case insensitive)."""
        _ensure_mode_zero("Zero")

    def test_linear_raises(self):
        """'linear' raises ValueError."""
        with pytest.raises(ValueError, match="linear"):
            _ensure_mode_zero("linear")

    def test_empty_string_raises(self):
        """An empty string raises ValueError."""
        with pytest.raises(ValueError):
            _ensure_mode_zero("")


# ---------------------------------------------------------------------------
# Tests: Carbon stock and LEF arithmetic
# ---------------------------------------------------------------------------


class TestCarbonStockArithmetic:
    """Standalone tests verifying the carbon stock and LEF computation logic.

    These test the mathematical relationships used in the main function
    with simple hand-computable numbers, without requiring xarray or
    exactextract.
    """

    def test_natural_carbon_stock(self):
        """Natural carbon stock = AGB + AGB*bgb_ratio_nat + SOC*soc_depth_factor."""
        agb = 100.0
        bgb_ratio_nat = 0.25
        soc_0_30 = 50.0
        soc_depth_factor = 1.5

        s_nat = agb + agb * bgb_ratio_nat + soc_0_30 * soc_depth_factor
        assert s_nat == pytest.approx(200.0)

    def test_agricultural_cropland_stock(self):
        """Ag cropland stock uses zone-specific parameters."""
        agb_crop = 5.0
        bgb_ratio_crop = 0.2
        soc_0_30 = 50.0
        soc_depth_factor = 1.5
        soc_factor_crop = 0.7

        soc_nat = soc_0_30 * soc_depth_factor
        s_ag_crop = agb_crop + agb_crop * bgb_ratio_crop + soc_nat * soc_factor_crop
        assert s_ag_crop == pytest.approx(58.5)

    def test_pulse_emission_cropland(self):
        """Pulse emission = (s_nat - s_ag_crop) * CO2_PER_C."""
        agb = 100.0
        bgb_ratio_nat = 0.25
        soc_0_30 = 50.0
        soc_depth_factor = 1.5
        agb_crop = 5.0
        bgb_ratio_crop = 0.2
        soc_factor_crop = 0.7

        s_nat = agb + agb * bgb_ratio_nat + soc_0_30 * soc_depth_factor
        soc_nat = soc_0_30 * soc_depth_factor
        s_ag_crop = agb_crop + agb_crop * bgb_ratio_crop + soc_nat * soc_factor_crop

        p_crop = (s_nat - s_ag_crop) * CO2_PER_C
        assert p_crop == pytest.approx(141.5 * 44.0 / 12.0)

    def test_annualized_lef_cropland(self):
        """Annualized LEF = pulse / horizon_years."""
        p_crop = 518.0
        horizon_years = 20

        lef_crop = p_crop / horizon_years
        assert lef_crop == pytest.approx(25.9)

    def test_full_pipeline_cropland(self):
        """Full pipeline from AGB/SOC to annualized LEF with concrete numbers."""
        agb = 100.0
        soc_0_30 = 50.0
        horizon_years = 20

        bgb_ratio_nat = 0.25
        soc_depth_factor = 1.5
        agb_crop = 5.0
        bgb_ratio_crop = 0.2
        soc_factor_crop = 0.7

        soc_nat = soc_0_30 * soc_depth_factor
        bgb_nat = agb * bgb_ratio_nat
        s_nat = agb + bgb_nat + soc_nat

        bgb_crop = agb_crop * bgb_ratio_crop
        s_ag_crop = agb_crop + bgb_crop + soc_nat * soc_factor_crop

        p_crop = (s_nat - s_ag_crop) * CO2_PER_C
        lef_crop = p_crop / horizon_years

        expected_pulse = 141.5 * (44.0 / 12.0)
        expected_lef = expected_pulse / 20.0

        assert s_nat == pytest.approx(200.0)
        assert s_ag_crop == pytest.approx(58.5)
        assert p_crop == pytest.approx(expected_pulse)
        assert lef_crop == pytest.approx(expected_lef)

    def test_full_pipeline_pasture(self):
        """Full pipeline for pasture land use."""
        agb = 100.0
        soc_0_30 = 50.0
        horizon_years = 20

        bgb_ratio_nat = 0.25
        soc_depth_factor = 1.5
        agb_past = 8.0
        bgb_ratio_past = 0.4
        soc_factor_past = 0.9

        soc_nat = soc_0_30 * soc_depth_factor
        s_nat = agb + agb * bgb_ratio_nat + soc_nat

        s_ag_past = agb_past + agb_past * bgb_ratio_past + soc_nat * soc_factor_past

        p_past = (s_nat - s_ag_past) * CO2_PER_C
        lef_past = p_past / horizon_years

        expected_pulse = (200.0 - 78.7) * (44.0 / 12.0)
        expected_lef = expected_pulse / 20.0

        assert s_ag_past == pytest.approx(78.7)
        assert p_past == pytest.approx(expected_pulse)
        assert lef_past == pytest.approx(expected_lef)

    def test_zero_agb_still_has_soc_emissions(self):
        """Even with zero AGB, SOC differences produce emissions."""
        agb = 0.0
        soc_0_30 = 60.0
        horizon_years = 30

        bgb_ratio_nat = 0.3
        soc_depth_factor = 1.4
        agb_crop = 5.0
        bgb_ratio_crop = 0.2
        soc_factor_crop = 0.6

        soc_nat = soc_0_30 * soc_depth_factor
        s_nat = agb + agb * bgb_ratio_nat + soc_nat
        s_ag_crop = agb_crop + agb_crop * bgb_ratio_crop + soc_nat * soc_factor_crop

        p_crop = (s_nat - s_ag_crop) * CO2_PER_C
        lef_crop = p_crop / horizon_years

        assert s_nat == pytest.approx(84.0)
        assert s_ag_crop == pytest.approx(56.4)
        assert p_crop == pytest.approx(27.6 * 44.0 / 12.0)
        assert lef_crop == pytest.approx(p_crop / 30.0)
        assert lef_crop > 0


# ---------------------------------------------------------------------------
# Tests: Sub-pixel stock correction
# ---------------------------------------------------------------------------


class TestCorrectSubpixelSOC:
    """Tests for _correct_subpixel_soc."""

    def test_pure_natural_pixel_unchanged(self):
        """A pixel with no agriculture returns observed SOC."""
        soc = np.array([[50.0]], dtype=np.float32)
        crop_f = np.array([[0.0]], dtype=np.float32)
        grass_f = np.array([[0.0]], dtype=np.float32)
        nonag_f = np.array([[1.0]], dtype=np.float32)
        soc_fc = np.array([[0.7]], dtype=np.float32)
        soc_fp = np.array([[0.9]], dtype=np.float32)

        soc_c = _correct_subpixel_soc(
            soc,
            crop_f,
            grass_f,
            nonag_f,
            soc_fc,
            soc_fp,
        )
        assert soc_c[0, 0] == pytest.approx(50.0)

    def test_mixed_pixel_soc_scales_up(self):
        """SOC should increase for a pixel with depleted agricultural soil.

        observed = soc_nat * (0.4 + 0.3*0.7 + 0.3*0.9) = soc_nat * 0.88
        So soc_nat = 50 / 0.88 = 56.818...
        """
        soc = np.array([[50.0]], dtype=np.float32)
        crop_f = np.array([[0.3]], dtype=np.float32)
        grass_f = np.array([[0.3]], dtype=np.float32)
        nonag_f = np.array([[0.4]], dtype=np.float32)
        soc_fc = np.array([[0.7]], dtype=np.float32)
        soc_fp = np.array([[0.9]], dtype=np.float32)

        soc_c = _correct_subpixel_soc(
            soc,
            crop_f,
            grass_f,
            nonag_f,
            soc_fc,
            soc_fp,
        )
        expected = 50.0 / (0.4 + 0.3 * 0.7 + 0.3 * 0.9)
        assert soc_c[0, 0] == pytest.approx(expected, rel=1e-4)

    def test_zero_nonag_soc_correction(self):
        """When nonag_frac is zero, SOC correction still applies via soc_denom."""
        soc = np.array([[40.0]], dtype=np.float32)
        crop_f = np.array([[0.5]], dtype=np.float32)
        grass_f = np.array([[0.5]], dtype=np.float32)
        nonag_f = np.array([[0.0]], dtype=np.float32)
        soc_fc = np.array([[0.7]], dtype=np.float32)
        soc_fp = np.array([[0.9]], dtype=np.float32)

        soc_c = _correct_subpixel_soc(
            soc,
            crop_f,
            grass_f,
            nonag_f,
            soc_fc,
            soc_fp,
        )
        # soc_denom = 0 + 0.5*0.7 + 0.5*0.9 = 0.8 > 0, so correction applies
        assert soc_c[0, 0] == pytest.approx(40.0 / 0.8, rel=1e-4)


class TestDecomposeAGB:
    """Tests for _decompose_agb."""

    def test_pure_forest_pixel(self):
        """A pixel that is entirely forest returns observed AGB as forest AGB."""
        agb_obs = np.array([[100.0]], dtype=np.float32)
        crop_f = np.array([[0.0]], dtype=np.float32)
        grass_f = np.array([[0.0]], dtype=np.float32)
        forest_f = np.array([[1.0]], dtype=np.float32)
        nonforest_f = np.array([[0.0]], dtype=np.float32)
        agb_crop = np.array([[0.0]], dtype=np.float32)
        agb_past = np.array([[5.0]], dtype=np.float32)
        agb_nf_zone = np.array([[10.0]], dtype=np.float32)

        agb_forest, agb_nf = _decompose_agb(
            agb_obs,
            crop_f,
            grass_f,
            forest_f,
            nonforest_f,
            agb_crop,
            agb_past,
            agb_nf_zone,
        )
        assert agb_forest[0, 0] == pytest.approx(100.0)
        assert agb_nf[0, 0] == pytest.approx(0.0)

    def test_mixed_forest_nonforest_pixel(self):
        """AGB decomposition for a pixel with forest and shrubland.

        Pixel: 30% crop (AGB=0), 20% grass (AGB=5), 30% forest, 20% shrub (zone AGB=10).
        Observed = 0.3*0 + 0.2*5 + 0.3*agb_forest + 0.2*10 = 1 + 0.3*F + 2 = 3 + 0.3*F
        If observed = 48, then 0.3*F = 45, so F = 150.
        """
        agb_obs = np.array([[48.0]], dtype=np.float32)
        crop_f = np.array([[0.3]], dtype=np.float32)
        grass_f = np.array([[0.2]], dtype=np.float32)
        forest_f = np.array([[0.3]], dtype=np.float32)
        nonforest_f = np.array([[0.2]], dtype=np.float32)
        agb_crop = np.array([[0.0]], dtype=np.float32)
        agb_past = np.array([[5.0]], dtype=np.float32)
        agb_nf_zone = np.array([[10.0]], dtype=np.float32)

        agb_forest, agb_nf = _decompose_agb(
            agb_obs,
            crop_f,
            grass_f,
            forest_f,
            nonforest_f,
            agb_crop,
            agb_past,
            agb_nf_zone,
        )
        assert agb_forest[0, 0] == pytest.approx(150.0, rel=1e-4)
        assert agb_nf[0, 0] == pytest.approx(10.0)

    def test_no_forest_pixel(self):
        """A pixel with no forest returns zero forest AGB."""
        agb_obs = np.array([[10.0]], dtype=np.float32)
        crop_f = np.array([[0.5]], dtype=np.float32)
        grass_f = np.array([[0.2]], dtype=np.float32)
        forest_f = np.array([[0.0]], dtype=np.float32)
        nonforest_f = np.array([[0.3]], dtype=np.float32)
        agb_crop = np.array([[0.0]], dtype=np.float32)
        agb_past = np.array([[5.0]], dtype=np.float32)
        agb_nf_zone = np.array([[10.0]], dtype=np.float32)

        agb_forest, agb_nf = _decompose_agb(
            agb_obs,
            crop_f,
            grass_f,
            forest_f,
            nonforest_f,
            agb_crop,
            agb_past,
            agb_nf_zone,
        )
        assert agb_forest[0, 0] == pytest.approx(0.0)
        assert agb_nf[0, 0] == pytest.approx(10.0)

    def test_forest_agb_clipped_to_zero(self):
        """If residual AGB is negative (noise), forest AGB clips to 0."""
        # observed AGB is very low, but nonforest zone AGB explains most of it
        agb_obs = np.array([[3.0]], dtype=np.float32)
        crop_f = np.array([[0.0]], dtype=np.float32)
        grass_f = np.array([[0.0]], dtype=np.float32)
        forest_f = np.array([[0.3]], dtype=np.float32)
        nonforest_f = np.array([[0.7]], dtype=np.float32)
        agb_crop = np.array([[0.0]], dtype=np.float32)
        agb_past = np.array([[0.0]], dtype=np.float32)
        agb_nf_zone = np.array([[10.0]], dtype=np.float32)

        agb_forest, _ = _decompose_agb(
            agb_obs,
            crop_f,
            grass_f,
            forest_f,
            nonforest_f,
            agb_crop,
            agb_past,
            agb_nf_zone,
        )
        # (3.0 - 0.7*10) / 0.3 = (3 - 7) / 0.3 = -13.3 → clipped to 0
        assert agb_forest[0, 0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: Land-cover-weighted aggregation
# ---------------------------------------------------------------------------


def _make_synthetic_inputs(tmp_path, *, height=2, width=2):
    """Create minimal synthetic datasets for aggregation tests.

    Builds a 2x2 grid with one region covering the full extent and one
    resource class (class 0).  Returns paths to all required inputs.

    Grid layout (each cell 1°x1°, origin at lon=0, lat=1):

        (row=0, col=0)  (row=0, col=1)
        (row=1, col=0)  (row=1, col=1)

    Land-cover fractions:
        cropland_frac:  [[0.6, 0.0],  [0.0, 0.4]]
        grassland_frac: [[0.0, 0.8],  [0.2, 0.0]]
        nonag_frac:     [[0.4, 0.2],  [0.8, 0.6]]  (clipped 1 - crop - grass)
    """
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, float(height))
    lon = np.array([0.5, 1.5], dtype=np.float32)
    lat = np.array([1.5, 0.5], dtype=np.float32)  # row 0 = lat 1.5, row 1 = lat 0.5

    # Resource classes: all class 0
    rc = np.zeros((height, width), dtype=np.int16)
    classes_ds = xr.Dataset(
        {"resource_class": (("y", "x"), rc)},
        coords={"y": lat, "x": lon},
        attrs={
            "transform": list(transform.to_gdal()),
            "height": height,
            "width": width,
            "crs_wkt": WGS84_WKT,
        },
    )
    classes_path = tmp_path / "classes.nc"
    classes_ds.to_netcdf(str(classes_path))

    # AGB: uniform 80 tC/ha — high enough for forest AGB to exceed nonforest zone AGB
    agb_arr = np.full((height, width), 80.0, dtype=np.float32)
    agb_ds = xr.Dataset(
        {"agb_tc_per_ha": (("y", "x"), agb_arr)},
        coords={"y": lat, "x": lon},
    )
    agb_path = tmp_path / "agb.nc"
    agb_ds.to_netcdf(str(agb_path))

    # SOC: uniform 50 tC/ha
    soc_arr = np.full((height, width), 50.0, dtype=np.float32)
    soc_ds = xr.Dataset(
        {"soc_0_30_tc_per_ha": (("y", "x"), soc_arr)},
        coords={"y": lat, "x": lon},
    )
    soc_path = tmp_path / "soc.nc"
    soc_ds.to_netcdf(str(soc_path))

    # Regrowth: uniform 5 tC/ha/yr
    regrowth_arr = np.full((height, width), 5.0, dtype=np.float32)
    regrowth_ds = xr.Dataset(
        {"regrowth_tc_per_ha_yr": (("y", "x"), regrowth_arr)},
        coords={"y": lat, "x": lon},
    )
    regrowth_path = tmp_path / "regrowth.nc"
    regrowth_ds.to_netcdf(str(regrowth_path))

    # Land-cover masks
    cropland_frac = np.array([[0.6, 0.0], [0.0, 0.4]], dtype=np.float32)
    grassland_frac = np.array([[0.0, 0.8], [0.2, 0.0]], dtype=np.float32)
    # pasture_fraction ≤ grassland_fraction at each pixel (managed subset)
    pasture_frac = np.array([[0.0, 0.5], [0.1, 0.0]], dtype=np.float32)
    # forest_fraction: portion of nonag land that is forest
    # nonag = 1 - crop - grass = [[0.4, 0.2], [0.8, 0.6]]
    # forest ≤ nonag
    forest_frac = np.array([[0.3, 0.1], [0.5, 0.3]], dtype=np.float32)
    lc_ds = xr.Dataset(
        {
            "cropland_fraction": (("y", "x"), cropland_frac),
            "grassland_fraction": (("y", "x"), grassland_frac),
            "pasture_fraction": (("y", "x"), pasture_frac),
            "forest_fraction": (("y", "x"), forest_frac),
        },
        coords={"y": lat, "x": lon},
    )
    lc_path = tmp_path / "lc_masks.nc"
    lc_ds.to_netcdf(str(lc_path))

    # Zone parameters (all cells are tropical at lat < 23.5)
    zone_csv = textwrap.dedent("""\
        zone,parameter,value,reference
        tropical,bgb_ratio_nat,0.25,ref
        temperate,bgb_ratio_nat,0.26,ref
        boreal,bgb_ratio_nat,0.39,ref
        tropical,soc_depth_factor,1.5,ref
        temperate,soc_depth_factor,1.4,ref
        boreal,soc_depth_factor,1.2,ref
        tropical,agb_crop_tc_per_ha,5.0,ref
        temperate,agb_crop_tc_per_ha,5.0,ref
        boreal,agb_crop_tc_per_ha,5.0,ref
        tropical,bgb_ratio_ag_crop,0.2,ref
        temperate,bgb_ratio_ag_crop,0.2,ref
        boreal,bgb_ratio_ag_crop,0.2,ref
        tropical,agb_past_tc_per_ha,8.0,ref
        temperate,agb_past_tc_per_ha,8.0,ref
        boreal,agb_past_tc_per_ha,8.0,ref
        tropical,bgb_ratio_ag_past,0.4,ref
        temperate,bgb_ratio_ag_past,0.4,ref
        boreal,bgb_ratio_ag_past,0.4,ref
        tropical,soc_factor_crop,0.7,ref
        temperate,soc_factor_crop,0.7,ref
        boreal,soc_factor_crop,0.7,ref
        tropical,soc_factor_past,0.9,ref
        temperate,soc_factor_past,0.9,ref
        boreal,soc_factor_past,0.9,ref
        tropical,agb_nonforest_tc_per_ha,20.0,ref
        temperate,agb_nonforest_tc_per_ha,10.0,ref
        boreal,agb_nonforest_tc_per_ha,8.0,ref
        tropical,bgb_ratio_nonforest,0.40,ref
        temperate,bgb_ratio_nonforest,0.46,ref
        boreal,bgb_ratio_nonforest,0.50,ref
    """)
    zone_path = tmp_path / "zone_params.csv"
    zone_path.write_text(zone_csv)

    # Region GeoDataFrame: one region covering the full grid
    region_gdf = gpd.GeoDataFrame(
        {"region": ["test_region"]},
        geometry=[box(0, 0, 2, 2)],
        crs="EPSG:4326",
    )
    regions_path = tmp_path / "regions.geojson"
    region_gdf.to_file(str(regions_path), driver="GeoJSON")

    return {
        "classes": str(classes_path),
        "regions": str(regions_path),
        "agb": str(agb_path),
        "soc": str(soc_path),
        "regrowth": str(regrowth_path),
        "lc_masks": str(lc_path),
        "zone_parameters": str(zone_path),
    }


def _run_main_with_mock(paths, tmp_path):
    """Helper to run main() with mock snakemake, returning the coefficients CSV."""

    class _NS:
        pass

    snakemake_mock = _NS()
    snakemake_mock.input = _NS()
    snakemake_mock.output = _NS()
    snakemake_mock.params = _NS()

    for k, v in paths.items():
        setattr(snakemake_mock.input, k, v)
    snakemake_mock.output.pulses = str(tmp_path / "pulses.nc")
    snakemake_mock.output.annualized = str(tmp_path / "annualized.nc")
    coeffs_out = str(tmp_path / "coefficients.csv")
    snakemake_mock.output.coefficients = coeffs_out
    snakemake_mock.params.horizon_years = 25
    snakemake_mock.params.managed_flux_mode = "zero"

    import workflow.scripts.build_luc_carbon_coefficients as mod

    orig = mod.__dict__.get("snakemake")
    try:
        mod.__dict__["snakemake"] = snakemake_mock
        mod.main()
    finally:
        if orig is None:
            mod.__dict__.pop("snakemake", None)
        else:
            mod.__dict__["snakemake"] = orig

    return pd.read_csv(coeffs_out)


class TestWeightedAggregation:
    """Tests for land-cover-weighted LEF aggregation."""

    def test_output_has_six_use_types(self, tmp_path):
        """Output CSV has forest/nonforest split for cropland and pasture."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        use_types = set(coeffs["use"].unique())
        assert use_types == {
            "cropland_forest",
            "cropland_nonforest",
            "pasture_forest",
            "pasture_nonforest",
            "spared_cropland",
            "spared_grassland",
        }

    def test_spared_lefs_are_negative(self, tmp_path):
        """Spared LEFs should be negative (sequestration credits)."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        for use in ("spared_cropland", "spared_grassland"):
            rows = coeffs[coeffs["use"] == use]
            assert not rows.empty, f"No rows for use={use}"
            assert (
                rows["LEF_tCO2_per_ha_yr"] < 0
            ).all(), f"Expected negative LEFs for {use}"

    def test_conversion_lefs_are_positive(self, tmp_path):
        """Forest and nonforest conversion LEFs should be positive."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        for use in (
            "cropland_forest",
            "cropland_nonforest",
            "pasture_forest",
            "pasture_nonforest",
        ):
            rows = coeffs[coeffs["use"] == use]
            assert not rows.empty, f"No rows for use={use}"
            assert (
                rows["LEF_tCO2_per_ha_yr"] > 0
            ).all(), f"Expected positive LEFs for {use}"

    def test_forest_lefs_exceed_nonforest(self, tmp_path):
        """Forest conversion LEFs should be higher than non-forest LEFs."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        for dest in ("cropland", "pasture"):
            forest_lef = coeffs[coeffs["use"] == f"{dest}_forest"][
                "LEF_tCO2_per_ha_yr"
            ].mean()
            nonforest_lef = coeffs[coeffs["use"] == f"{dest}_nonforest"][
                "LEF_tCO2_per_ha_yr"
            ].mean()
            assert forest_lef > nonforest_lef, (
                f"Expected forest LEF > nonforest LEF for {dest}, "
                f"got {forest_lef:.2f} <= {nonforest_lef:.2f}"
            )

    def test_conversion_shares_valid(self, tmp_path):
        """Conversion shares should be between 0 and 1."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        assert "conversion_share" in coeffs.columns
        for use in (
            "cropland_forest",
            "cropland_nonforest",
            "pasture_forest",
            "pasture_nonforest",
        ):
            rows = coeffs[coeffs["use"] == use]
            assert (rows["conversion_share"] >= 0).all()
            assert (rows["conversion_share"] <= 1).all()

    def test_spared_conversion_shares_are_one(self, tmp_path):
        """Spared use types should have conversion_share = 1."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        for use in ("spared_cropland", "spared_grassland"):
            rows = coeffs[coeffs["use"] == use]
            assert not rows.empty, f"No rows for use={use}"
            np.testing.assert_allclose(rows["conversion_share"].values, 1.0, atol=1e-6)

    def test_water_options_correct(self, tmp_path):
        """Cropland uses have r+i; pasture uses and spared_grassland have only r."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs = _run_main_with_mock(paths, tmp_path)
        for use in ("cropland_forest", "cropland_nonforest", "spared_cropland"):
            waters = set(coeffs[coeffs["use"] == use]["water"].unique())
            assert waters == {"r", "i"}, f"Expected r+i for {use}, got {waters}"
        for use in (
            "pasture_forest",
            "pasture_nonforest",
            "spared_grassland",
        ):
            waters = set(coeffs[coeffs["use"] == use]["water"].unique())
            assert waters == {"r"}, f"Expected only r for {use}, got {waters}"
