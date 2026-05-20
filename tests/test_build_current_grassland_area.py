# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for build_current_grassland_area: area must be GI-weighted (managed)."""

from pathlib import Path
import subprocess
import sys

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon
import xarray as xr


def _classes_dataset(transform: Affine, height: int, width: int) -> xr.Dataset:
    rid = np.zeros((height, width), dtype=np.int32)
    rid[:, width // 2 :] = 1
    rc = np.ones((height, width), dtype=np.int16)
    ds = xr.Dataset(
        {
            "region_id": (("y", "x"), rid),
            "resource_class": (("y", "x"), rc),
        }
    )
    ds.attrs["transform"] = transform.to_gdal()
    return ds


def _luicube_dataset(
    transform: Affine, area_km2: np.ndarray, gi: np.ndarray
) -> xr.Dataset:
    ds = xr.Dataset(
        {
            "area_km2": (("y", "x"), area_km2.astype(np.float32)),
            "grazing_intensity": (("y", "x"), gi.astype(np.float32)),
        }
    )
    ds.attrs["transform"] = transform.to_gdal()
    return ds


def _regions_geojson(path: Path) -> None:
    geom_a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    geom_b = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
    gpd.GeoDataFrame(
        {"region": ["REG0", "REG1"]},
        geometry=[geom_a, geom_b],
        crs="EPSG:4326",
    ).to_file(path, driver="GeoJSON")


def _run_script(
    tmp_path: Path, classes: xr.Dataset, luicube: xr.Dataset
) -> pd.DataFrame:
    classes_path = tmp_path / "classes.nc"
    luicube_path = tmp_path / "luicube.nc"
    regions_path = tmp_path / "regions.geojson"
    out_path = tmp_path / "out.csv"
    classes.to_netcdf(classes_path)
    luicube.to_netcdf(luicube_path)
    _regions_geojson(regions_path)

    script = (
        Path(__file__).parent.parent
        / "workflow/scripts/build_current_grassland_area.py"
    )
    code = (
        "import runpy, sys, types\n"
        "sm = types.SimpleNamespace()\n"
        "sm.input = types.SimpleNamespace("
        f"classes='{classes_path}', luicube='{luicube_path}', regions='{regions_path}')\n"
        f"sm.output = ['{out_path}']\n"
        "import builtins\n"
        "builtins.snakemake = sm\n"
        f"runpy.run_path('{script}', run_name='__main__')\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    return pd.read_csv(out_path)


def test_area_ha_is_gi_weighted_managed_area(tmp_path):
    # 2x2 grid; cells in region 0 have GI=0.5, region 1 have GI=1.0.
    transform = Affine(1.0, 0, 0, 0, -1.0, 2.0)
    classes = _classes_dataset(transform, 2, 2)
    area_km2 = np.full((2, 2), 100.0, dtype=np.float32)  # 100 km2 each
    gi = np.array([[0.5, 1.0], [0.5, 1.0]], dtype=np.float32)
    luicube = _luicube_dataset(transform, area_km2, gi)

    df = _run_script(tmp_path, classes, luicube)

    df = df.set_index("region")
    # Region 0: 2 cells x 100 km2 x 100 ha/km2 x GI=0.5 = 10_000 ha managed
    assert df.loc["REG0", "area_ha"] == 10_000
    assert df.loc["REG0", "grazing_intensity"] == 0.5
    # Region 1: 2 cells x 100 km2 x 100 ha/km2 x GI=1.0 = 20_000 ha managed
    assert df.loc["REG1", "area_ha"] == 20_000
    assert df.loc["REG1", "grazing_intensity"] == 1.0


def test_transform_mismatch_raises(tmp_path):
    transform_a = Affine(1.0, 0, 0, 0, -1.0, 2.0)
    transform_b = Affine(2.0, 0, 0, 0, -2.0, 4.0)  # different
    classes = _classes_dataset(transform_a, 2, 2)
    luicube = _luicube_dataset(
        transform_b,
        np.ones((2, 2), dtype=np.float32),
        np.ones((2, 2), dtype=np.float32),
    )

    classes_path = tmp_path / "classes.nc"
    luicube_path = tmp_path / "luicube.nc"
    regions_path = tmp_path / "regions.geojson"
    out_path = tmp_path / "out.csv"
    classes.to_netcdf(classes_path)
    luicube.to_netcdf(luicube_path)
    _regions_geojson(regions_path)

    script = (
        Path(__file__).parent.parent
        / "workflow/scripts/build_current_grassland_area.py"
    )
    code = (
        "import runpy, sys, types\n"
        "sm = types.SimpleNamespace()\n"
        "sm.input = types.SimpleNamespace("
        f"classes='{classes_path}', luicube='{luicube_path}', regions='{regions_path}')\n"
        f"sm.output = ['{out_path}']\n"
        "import builtins\n"
        "builtins.snakemake = sm\n"
        f"runpy.run_path('{script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "transform" in result.stderr
