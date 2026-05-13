#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compute average fraction of potential yield achieved by actual yield per country.

Definition: fraction = actual / potential, masked where potential <= 0 or NaN.

Inputs (via Snakemake):
 - potential_yield: GeoTIFF potential yield
 - actual_yield:    GeoTIFF actual yield
 - regions:         GeoJSON of model regions; will be dissolved by 'country'

Output:
 - CSV with columns: country, fraction_achieved

Notes:
 - Uses exactextract to compute polygon means over the raster ratio.
 - Reprojects actual to the potential raster grid before computing the ratio.
 - Countries are derived by dissolving regions by the 'country' column.
"""

import logging
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import exact_extract  # noqa: E402
from exactextract.raster import NumPyRasterSource  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.warp import reproject  # noqa: E402

from workflow.scripts.logging_config import setup_script_logging  # noqa: E402

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def _read_raster(path: str) -> tuple[np.ndarray, Affine, str, float | None]:
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32", copy=False)
        nodata = src.nodata
        if nodata is not None:
            arr = np.where(arr == nodata, np.nan, arr)
        crs = src.crs.to_string() if src.crs else "EPSG:4326"
        return arr, src.transform, crs, nodata


def _reproject_to(
    src_arr: np.ndarray,
    src_transform: Affine,
    src_crs: str,
    dst_transform: Affine,
    dst_crs: str,
    dst_shape: tuple[int, int],
    resampling: Resampling = Resampling.average,
) -> np.ndarray:
    dst = np.full(dst_shape, np.nan, dtype="float32")
    reproject(
        source=src_arr,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=resampling,
        dst_nodata=np.nan,
    )
    return dst


def compute_fraction_mean_by_country(
    potential_path: str, actual_path: str, regions_path: str
) -> pd.DataFrame:
    # Read rasters and align
    pot, pot_transform, pot_crs, _ = _read_raster(potential_path)
    act, act_transform, act_crs, _ = _read_raster(actual_path)

    # Convert potential yeilds from kg/ha to t/ha
    pot /= 1000.0

    if (
        (act_crs != pot_crs)
        or (act.shape != pot.shape)
        or (act_transform != pot_transform)
    ):
        logger.info("Reprojecting actual yield to potential grid for ratio computation")
        act = _reproject_to(
            act,
            act_transform,
            act_crs,
            pot_transform,
            pot_crs,
            pot.shape,
            resampling=Resampling.average,
        )

    # Compute fraction = actual / potential; mask invalid
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = act / pot
        fraction = np.where(np.isfinite(pot) & (pot > 0), fraction, np.nan).astype(
            "float32"
        )

    # Load regions and dissolve to countries
    gdf = gpd.read_file(regions_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)

    # Ensure a 'country' column is present
    if "country" not in gdf.columns:
        if "GID_0" in gdf.columns:
            gdf = gdf.rename(columns={"GID_0": "country"})
        else:
            raise ValueError("Regions file must contain a 'country' or 'GID_0' column")

    # Optionally filter to configured countries if provided
    countries = snakemake.params.countries
    if countries:
        gdf = gdf[gdf["country"].isin(countries)]

    # Dissolve to country geometries and project to raster CRS
    gdf_country = gdf[["country", "geometry"]].dissolve(by="country", as_index=False)
    gdf_country = gdf_country.to_crs(pot_crs)

    # Compute per-country mean using exactextract with a NumPyRasterSource
    # to avoid GDAL datatype edge-cases.
    height, width = fraction.shape
    xmin = pot_transform.c
    ymax = pot_transform.f
    xmax = xmin + width * pot_transform.a
    ymin = ymax + height * pot_transform.e
    crs_wkt = CRS.from_user_input(pot_crs).to_wkt() if pot_crs else None

    raster = NumPyRasterSource(
        fraction,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        nodata=np.nan,
        srs_wkt=crs_wkt,
    )
    regions_for_extract = gdf_country[["country", "geometry"]].copy()
    stats_df = exact_extract(
        raster,
        regions_for_extract,
        ["mean"],
        include_cols=["country"],
        output="pandas",
    )
    if stats_df is None or len(stats_df) == 0:
        out = gdf_country[["country"]].copy()
        out["fraction_achieved"] = np.nan
    else:
        out = (
            gdf_country[["country"]]
            .merge(
                stats_df.rename(columns={"mean": "fraction_achieved"}),
                on="country",
                how="left",
            )
            .drop_duplicates(subset=["country"])
        )
    out = out.sort_values("country").reset_index(drop=True)
    return out


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    df = compute_fraction_mean_by_country(
        snakemake.input.potential_yield,
        snakemake.input.actual_yield,
        snakemake.input.regions,
    )

    out_path = Path(snakemake.output.csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
