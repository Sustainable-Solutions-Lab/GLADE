# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for land-use-change carbon coefficient computation."""

import textwrap

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box
import xarray as xr

from workflow.scripts.build_luc_carbon_coefficients import (
    CO2_PER_C,
    _ensure_mode_zero,
    _zone_index,
    _zone_parameters,
)

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
            zone,bgb_ratio_nat,soc_depth_factor
            tropical,0.24,1.5
            temperate,0.26,1.4
            boreal,0.39,1.2
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
            zone,param_a
            tropical,1.0
            temperate,2.0
            boreal,3.0
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        result = _zone_parameters(str(csv_path))
        assert result["param_a"].dtype == np.float32

    def test_ordered_by_zone_order(self, tmp_path):
        """Output follows ZONE_ORDER even if CSV rows are in a different order."""
        csv_content = textwrap.dedent("""\
            zone,val
            boreal,3.0
            tropical,1.0
            temperate,2.0
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
            zone,bgb_ratio_nat
            tropical,0.24
            temperate,0.26
        """)
        csv_path = tmp_path / "zone_params.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError, match="boreal"):
            _zone_parameters(str(csv_path))

    def test_comments_ignored(self, tmp_path):
        """Lines starting with # are treated as comments and ignored."""
        csv_content = textwrap.dedent("""\
            # This is a comment
            zone,val
            tropical,10.0
            temperate,20.0
            boreal,30.0
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
        natural_frac:   [[0.4, 0.2],  [0.8, 0.6]]  (clipped 1 - crop - grass)
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
            "crs_wkt": "EPSG:4326",
        },
    )
    classes_path = tmp_path / "classes.nc"
    classes_ds.to_netcdf(str(classes_path))

    # AGB: uniform 10 tC/ha (below threshold -> spared credits active)
    agb_arr = np.full((height, width), 10.0, dtype=np.float32)
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
    lc_ds = xr.Dataset(
        {
            "cropland_fraction": (("y", "x"), cropland_frac),
            "grassland_fraction": (("y", "x"), grassland_frac),
        },
        coords={"y": lat, "x": lon},
    )
    lc_path = tmp_path / "lc_masks.nc"
    lc_ds.to_netcdf(str(lc_path))

    # Zone parameters (all cells are tropical at lat < 23.5)
    zone_csv = textwrap.dedent("""\
        zone,bgb_ratio_nat,soc_depth_factor,agb_crop_tc_per_ha,bgb_ratio_ag_crop,agb_past_tc_per_ha,bgb_ratio_ag_past,soc_factor_crop,soc_factor_past
        tropical,0.25,1.5,5.0,0.2,8.0,0.4,0.7,0.9
        temperate,0.26,1.4,5.0,0.2,8.0,0.4,0.7,0.9
        boreal,0.39,1.2,5.0,0.2,8.0,0.4,0.7,0.9
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


class TestWeightedAggregation:
    """Tests for land-cover-weighted LEF aggregation."""

    def test_output_has_four_use_types(self, tmp_path):
        """Output CSV has cropland, pasture, spared_cropland, spared_grassland."""
        paths = _make_synthetic_inputs(tmp_path)

        pulses_out = str(tmp_path / "pulses.nc")
        annual_out = str(tmp_path / "annualized.nc")
        coeffs_out = str(tmp_path / "coefficients.csv")

        # Build a mock snakemake object
        class _NS:
            pass

        snakemake_mock = _NS()
        snakemake_mock.input = _NS()
        snakemake_mock.output = _NS()
        snakemake_mock.params = _NS()

        for k, v in paths.items():
            setattr(snakemake_mock.input, k, v)
        snakemake_mock.output.pulses = pulses_out
        snakemake_mock.output.annualized = annual_out
        snakemake_mock.output.coefficients = coeffs_out
        snakemake_mock.params.horizon_years = 25
        snakemake_mock.params.managed_flux_mode = "zero"
        snakemake_mock.params.agb_threshold = 20.0

        # Run the script by injecting the mock
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

        coeffs = pd.read_csv(coeffs_out)
        use_types = set(coeffs["use"].unique())
        assert use_types == {
            "cropland",
            "pasture",
            "spared_cropland",
            "spared_grassland",
        }

    def test_spared_lefs_are_negative(self, tmp_path):
        """Spared LEFs should be negative (sequestration credits)."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs_out = str(tmp_path / "coefficients.csv")

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
        snakemake_mock.output.coefficients = coeffs_out
        snakemake_mock.params.horizon_years = 25
        snakemake_mock.params.managed_flux_mode = "zero"
        snakemake_mock.params.agb_threshold = 20.0

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

        coeffs = pd.read_csv(coeffs_out)
        for use in ("spared_cropland", "spared_grassland"):
            rows = coeffs[coeffs["use"] == use]
            assert not rows.empty, f"No rows for use={use}"
            assert (
                rows["LEF_tCO2_per_ha_yr"] < 0
            ).all(), f"Expected negative LEFs for {use}"

    def test_conversion_lefs_are_positive(self, tmp_path):
        """Conversion LEFs (cropland, pasture) should be positive."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs_out = str(tmp_path / "coefficients.csv")

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
        snakemake_mock.output.coefficients = coeffs_out
        snakemake_mock.params.horizon_years = 25
        snakemake_mock.params.managed_flux_mode = "zero"
        snakemake_mock.params.agb_threshold = 20.0

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

        coeffs = pd.read_csv(coeffs_out)
        for use in ("cropland", "pasture"):
            rows = coeffs[coeffs["use"] == use]
            assert not rows.empty, f"No rows for use={use}"
            assert (
                rows["LEF_tCO2_per_ha_yr"] > 0
            ).all(), f"Expected positive LEFs for {use}"

    def test_water_options_correct(self, tmp_path):
        """Cropland and spared_cropland have r+i; pasture and spared_grassland only r."""
        paths = _make_synthetic_inputs(tmp_path)
        coeffs_out = str(tmp_path / "coefficients.csv")

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
        snakemake_mock.output.coefficients = coeffs_out
        snakemake_mock.params.horizon_years = 25
        snakemake_mock.params.managed_flux_mode = "zero"
        snakemake_mock.params.agb_threshold = 20.0

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

        coeffs = pd.read_csv(coeffs_out)
        for use in ("cropland", "spared_cropland"):
            waters = set(coeffs[coeffs["use"] == use]["water"].unique())
            assert waters == {"r", "i"}, f"Expected r+i for {use}, got {waters}"
        for use in ("pasture", "spared_grassland"):
            waters = set(coeffs[coeffs["use"] == use]["water"].unique())
            assert waters == {"r"}, f"Expected only r for {use}, got {waters}"
