"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
import numpy as np
from osgeo import gdal, osr
import rasterio
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
import xarray as xr

# Enable GDAL exceptions for better error messages
gdal.UseExceptions()
osr.UseExceptions()

NO_DATA = -9999.0


def _load_target_grid(
    resource_classes_path: str,
) -> tuple[Affine, CRS, tuple[int, int], dict[str, np.ndarray]]:
    ds = xr.load_dataset(resource_classes_path)
    try:
        transform = Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc
    try:
        crs = CRS.from_wkt(ds.attrs["crs_wkt"])
    except KeyError as exc:
        raise ValueError("resource_classes.nc missing CRS metadata") from exc
    height = int(ds.attrs.get("height", ds.sizes["y"]))
    width = int(ds.attrs.get("width", ds.sizes["x"]))

    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e

    coords = {
        "y": lat.astype(np.float32),
        "x": lon.astype(np.float32),
    }

    return transform, crs, (height, width), coords


def _reproject_to_target(
    src: np.ndarray,
    src_transform,
    src_crs,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
    *,
    resampling: Resampling,
) -> np.ndarray:
    dst = np.full(dst_shape, NO_DATA, dtype=np.float32)
    reproject(
        source=src,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        src_nodata=NO_DATA,
        dst_nodata=NO_DATA,
        resampling=resampling,
    )
    dst[dst == NO_DATA] = np.nan
    return dst


def _load_agb(
    path: str,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
) -> np.ndarray:
    with xr.open_dataset(path) as ds:
        var_name = None
        units = ""
        fill_value = None
        for name, var in ds.data_vars.items():
            if var.ndim >= 2:
                var_name = name
                units = str(var.attrs.get("units", "")).lower()
                fill_value = var.attrs.get("_FillValue")
                if fill_value is None:
                    fill_value = var.encoding.get("_FillValue")
                break
    if var_name is None:
        raise ValueError("AGB dataset contains no 2D data variables")

    dataset_url = f'NETCDF:"{path}":{var_name}'
    with rasterio.open(dataset_url) as src:
        data = src.read()
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    if crs is None:
        raise ValueError("AGB dataset missing CRS information for reprojection")

    arr = data[-1].astype(np.float32) if data.ndim == 3 else data.astype(np.float32)

    fill_candidates = [fill_value, nodata]
    for candidate in fill_candidates:
        if candidate is None:
            continue
        arr[arr == float(candidate)] = np.nan

    arr_tc = arr.copy()
    mask = np.isfinite(arr_tc)
    if "tc" not in units:
        arr_tc[mask] = arr_tc[mask] * 0.47

    reproject_src = np.full(arr_tc.shape, NO_DATA, dtype=np.float32)
    reproject_src[mask] = arr_tc[mask]

    return _reproject_to_target(
        reproject_src,
        transform,
        crs,
        dst_shape,
        dst_transform,
        dst_crs,
        resampling=Resampling.average,
    )


def _load_soc(
    path: str,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    if crs is None:
        raise ValueError("Soil organic carbon raster missing CRS information")

    if nodata is not None:
        arr[arr == nodata] = np.nan

    src_data = np.full(arr.shape, NO_DATA, dtype=np.float32)
    mask = np.isfinite(arr)
    src_data[mask] = arr[mask]

    return _reproject_to_target(
        src_data,
        transform,
        crs,
        dst_shape,
        dst_transform,
        dst_crs,
        resampling=Resampling.average,
    )


def _load_regrowth(
    path: str,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
) -> np.ndarray:
    if Path(path).suffix == ".nc":
        ds = xr.load_dataset(path)
        if "regrowth_tc_per_ha_yr" not in ds:
            raise ValueError(
                "regrowth preprocessing result missing 'regrowth_tc_per_ha_yr'"
            )
        arr = ds["regrowth_tc_per_ha_yr"].astype(np.float32).values
        if arr.shape != dst_shape:
            raise ValueError(
                "regrowth preprocessing grid does not match resource_classes grid"
            )
        return arr

    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    if crs is None:
        raise ValueError("Regrowth raster missing CRS information")

    if nodata is not None:
        arr[arr == nodata] = np.nan

    src_data = np.full(arr.shape, NO_DATA, dtype=np.float32)
    mask = np.isfinite(arr)
    src_data[mask] = arr[mask]

    return _reproject_to_target(
        src_data,
        transform,
        crs,
        dst_shape,
        dst_transform,
        dst_crs,
        resampling=Resampling.average,
    )


def _prepare_masks(
    forest_frac: np.ndarray,
    cropland_frac: np.ndarray,
    grassland_frac: np.ndarray,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create binary masks from land cover fractions.

    Parameters
    ----------
    forest_frac : np.ndarray
        Forest fraction per cell (0-1).
    cropland_frac : np.ndarray
        Cropland fraction per cell (0-1).
    grassland_frac : np.ndarray
        Grassland fraction per cell (0-1).
    threshold : float
        Minimum fraction (0-1) to set mask bit to 1.

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray]
        Binary masks (uint8) for forest, cropland, grassland.
    """

    def mask_from_fraction(arr: np.ndarray) -> np.ndarray:
        mask = np.zeros(arr.shape, dtype=np.uint8)
        valid = np.isfinite(arr)
        mask[valid & (arr >= threshold)] = 1
        return mask

    return (
        mask_from_fraction(forest_frac),
        mask_from_fraction(cropland_frac),
        mask_from_fraction(grassland_frac),
    )


def main() -> None:
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    land_cover_path: str = snakemake.input.land_cover  # type: ignore[name-defined]
    agb_path: str = snakemake.input.agb  # type: ignore[name-defined]
    soc_path: str = snakemake.input.soc  # type: ignore[name-defined]
    regrowth_path: str = snakemake.input.regrowth  # type: ignore[name-defined]

    output_lc: str = snakemake.output.lc_masks  # type: ignore[name-defined]
    output_agb: str = snakemake.output.agb  # type: ignore[name-defined]
    output_soc: str = snakemake.output.soc  # type: ignore[name-defined]
    output_regrowth: str = snakemake.output.regrowth  # type: ignore[name-defined]

    Path(output_lc).parent.mkdir(parents=True, exist_ok=True)

    target_transform, target_crs, target_shape, coords = _load_target_grid(classes_path)

    lc_ds = xr.load_dataset(land_cover_path)
    required_vars = {"forest_fraction", "cropland_fraction", "grassland_fraction"}
    missing = required_vars.difference(lc_ds.data_vars)
    if missing:
        raise ValueError(
            "land cover preprocessing result missing variables: "
            + ", ".join(sorted(missing))
        )

    forest_frac = lc_ds["forest_fraction"].astype(np.float32).values
    cropland_frac = lc_ds["cropland_fraction"].astype(np.float32).values
    grassland_frac = lc_ds["grassland_fraction"].astype(np.float32).values

    # Compute pasture_fraction = LUIcube grassland_fraction * grazing_intensity.
    # This distinguishes managed pasture from natural grassland (savanna, tundra,
    # steppe) so that only actively grazed pixels contribute to spared-land
    # sequestration potential.
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    luicube_ds = xr.load_dataset(luicube_path)
    luicube_grass_frac = luicube_ds["grassland_fraction"].astype(np.float32).values
    luicube_gi = luicube_ds["grazing_intensity"].astype(np.float32).values
    with np.errstate(invalid="ignore"):
        pasture_frac = np.where(
            np.isfinite(luicube_grass_frac) & np.isfinite(luicube_gi),
            luicube_grass_frac * luicube_gi,
            0.0,
        )
    pasture_frac = np.clip(pasture_frac, 0.0, 1.0).astype(np.float32)

    if forest_frac.shape != target_shape:
        raise ValueError(
            "land cover fractions grid does not match resource_classes grid"
        )
    if pasture_frac.shape != target_shape:
        raise ValueError("LUIcube grassland grid does not match resource_classes grid")

    threshold = float(snakemake.params.get("forest_fraction_threshold", 0.2))  # type: ignore[name-defined]
    forest_mask, cropland_mask, grassland_mask = _prepare_masks(
        forest_frac, cropland_frac, grassland_frac, threshold
    )

    agb_tc = _load_agb(agb_path, target_shape, target_transform, target_crs)
    soc_tc = _load_soc(soc_path, target_shape, target_transform, target_crs)
    regrowth_tc = _load_regrowth(
        regrowth_path, target_shape, target_transform, target_crs
    )

    # Apply reforestation mask: zero out regrowth in biomes where forest
    # cannot plausibly regrow (native grasslands, open savannas, etc.).
    # Mask built from Hayek et al. (2024) biome classification.
    mask_path: str = snakemake.input.reforestation_mask  # type: ignore[name-defined]
    mask_ds = xr.load_dataset(mask_path)
    reforestation_mask = mask_ds["reforestation_mask"].values.astype(bool)
    if reforestation_mask.shape != target_shape:
        raise ValueError("reforestation mask grid does not match resource_classes grid")
    regrowth_tc = np.where(reforestation_mask, regrowth_tc, 0.0)

    lc_ds = xr.Dataset(
        {
            "forest_fraction": (("y", "x"), forest_frac.astype(np.float32)),
            "cropland_fraction": (("y", "x"), cropland_frac.astype(np.float32)),
            "grassland_fraction": (
                ("y", "x"),
                grassland_frac.astype(np.float32),
            ),
            "pasture_fraction": (("y", "x"), pasture_frac),
            "forest_mask": (("y", "x"), forest_mask),
            "cropland_mask": (("y", "x"), cropland_mask),
            "grassland_mask": (("y", "x"), grassland_mask),
        },
        coords=coords,
    )
    lc_encoding = {
        "forest_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "cropland_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "grassland_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "pasture_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "forest_mask": {"zlib": True, "complevel": 4, "dtype": "uint8"},
        "cropland_mask": {"zlib": True, "complevel": 4, "dtype": "uint8"},
        "grassland_mask": {"zlib": True, "complevel": 4, "dtype": "uint8"},
    }
    lc_ds.to_netcdf(output_lc, encoding=lc_encoding)

    agb_ds = xr.Dataset(
        {
            "agb_tc_per_ha": (("y", "x"), agb_tc.astype(np.float32)),
        },
        coords=coords,
    )
    agb_ds.to_netcdf(
        output_agb, encoding={"agb_tc_per_ha": {"zlib": True, "dtype": "float32"}}
    )

    soc_ds = xr.Dataset(
        {
            "soc_0_30_tc_per_ha": (("y", "x"), soc_tc.astype(np.float32)),
        },
        coords=coords,
    )
    soc_ds.to_netcdf(
        output_soc,
        encoding={"soc_0_30_tc_per_ha": {"zlib": True, "dtype": "float32"}},
    )

    regrowth_ds = xr.Dataset(
        {
            "regrowth_tc_per_ha_yr": (
                ("y", "x"),
                regrowth_tc.astype(np.float32),
            ),
        },
        coords=coords,
    )
    regrowth_ds.to_netcdf(
        output_regrowth,
        encoding={"regrowth_tc_per_ha_yr": {"zlib": True, "dtype": "float32"}},
    )


if __name__ == "__main__":
    main()
