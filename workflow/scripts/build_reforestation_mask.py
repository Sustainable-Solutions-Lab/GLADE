# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build a binary reforestation-eligibility mask from Hayek et al. (2024).

Uses biome codes and potential vegetation carbon (pvC) from the Hayek
et al. (2024, PNAS) "Carbon opportunity areas in global beef pastures"
dataset to identify pixels where forest could plausibly regrow if
grassland/pasture were spared.

Classification (following Hayek et al.):
- Biome 1-8 (forest/woodland types): eligible (mask = 1)
- Biome 9 (savanna) with pvC >= threshold: closed savanna, eligible
- Biome 9 with pvC < threshold: open savanna, not eligible (mask = 0)
- Biome 10-15 (grassland/shrubland/desert/tundra): not eligible
- NoData / outside coverage: eligible (conservative; don't clip where
  we lack data)
"""

import os
from pathlib import Path

from affine import Affine
import numpy as np
from osgeo import gdal, osr
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
import xarray as xr

NO_DATA = -9999.0

gdal.UseExceptions()
osr.UseExceptions()

os.environ["GDAL_NUM_THREADS"] = "ALL_CPUS"
os.environ["GDAL_CACHEMAX"] = "512"

# Biome codes from the Hayek dataset (Earthstat / West et al. 2010).
# 1-8 are forest/woodland biomes; 9 is savanna (split by pvC threshold);
# 10+ are grassland, shrubland, tundra, desert, polar.
_MAX_FOREST_BIOME = 8
_SAVANNA_BIOME = 9


def _load_target_grid(
    grid_path: str,
) -> tuple[Affine, CRS, tuple[int, int], dict[str, np.ndarray], dict[str, object]]:
    ds = xr.load_dataset(grid_path)
    target_transform = Affine.from_gdal(*ds.attrs["transform"])
    crs = CRS.from_wkt(ds.attrs["crs_wkt"])
    height = int(ds.sizes["y"])
    width = int(ds.sizes["x"])
    coords = {
        "y": ds["y"].astype(np.float32).values,
        "x": ds["x"].astype(np.float32).values,
    }
    attrs = {
        "transform": tuple(ds.attrs["transform"]),
        "crs_wkt": ds.attrs["crs_wkt"],
        "height": ds.attrs.get("height", height),
        "width": ds.attrs.get("width", width),
    }
    return target_transform, crs, (height, width), coords, attrs


def _read_band(
    path: str,
    band: int,
    target_transform: Affine,
    target_crs: CRS,
    target_shape: tuple[int, int],
    resampling: Resampling,
) -> np.ndarray:
    """Read a single band from a GeoTIFF and warp to target grid."""
    with (
        rasterio.open(path) as src,
        WarpedVRT(
            src,
            crs=target_crs,
            transform=target_transform,
            height=target_shape[0],
            width=target_shape[1],
            resampling=resampling,
            src_nodata=src.nodata,
            nodata=NO_DATA,
        ) as vrt,
    ):
        return vrt.read(band, out_dtype=np.float32)


def main() -> None:
    grid_path: str = snakemake.input.grid  # type: ignore[name-defined]
    geospatial_path: str = snakemake.input.geospatial  # type: ignore[name-defined]
    pvc_path: str = snakemake.input.pvc  # type: ignore[name-defined]
    output_path: str = snakemake.output.mask  # type: ignore[name-defined]
    savanna_pvc_threshold: float = float(
        snakemake.params.savanna_pvc_threshold  # type: ignore[name-defined]
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    target_transform, target_crs, target_shape, coords, attr_template = (
        _load_target_grid(grid_path)
    )

    # Band 1 of Geospatial.tif = biome code (integer 1-15)
    biome = _read_band(
        geospatial_path,
        1,
        target_transform,
        target_crs,
        target_shape,
        Resampling.nearest,
    )

    # Band 1 of pvC_stack.tif = potential vegetation carbon (MgC/ha),
    # Erb et al. (2016) scenario A (median estimate)
    pvc = _read_band(
        pvc_path,
        1,
        target_transform,
        target_crs,
        target_shape,
        Resampling.bilinear,
    )

    # Build mask
    has_data = biome != NO_DATA
    biome_int = np.where(has_data, biome, 0).astype(np.int16)
    pvc_clean = np.where(pvc == NO_DATA, np.nan, pvc)

    is_forest_biome = (biome_int >= 1) & (biome_int <= _MAX_FOREST_BIOME)
    is_closed_savanna = (biome_int == _SAVANNA_BIOME) & (
        np.nan_to_num(pvc_clean, nan=0.0) >= savanna_pvc_threshold
    )

    # Conservative default: where we have no biome data, allow regrowth
    mask = np.where(
        has_data,
        (is_forest_biome | is_closed_savanna).astype(np.uint8),
        np.uint8(1),
    )

    ds_out = xr.Dataset(
        {"reforestation_mask": (("y", "x"), mask)},
        coords={"y": coords["y"], "x": coords["x"]},
        attrs=attr_template,
    )
    ds_out.to_netcdf(
        output_path,
        encoding={
            "reforestation_mask": {"zlib": True, "complevel": 4, "dtype": "uint8"}
        },
    )


if __name__ == "__main__":
    main()
