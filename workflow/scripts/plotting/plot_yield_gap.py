#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Plot a world map of the yield gap per grid cell.

Inputs (from Snakemake rule):
 - potential_yield: GeoTIFF with potential yield
 - actual_yield:    GeoTIFF with actual yield

Output:
 - PDF map at snakemake.output.pdf

Notes:
 - Computes gap = actual - potential. Negative values (actual < potential)
   indicate a yield gap and are shown in blue; positive values in red.
 - Reprojects data to an Equal Earth projection for display.
 - Uses a blue-red divergent colormap ("RdBu").
"""

import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine, array_bounds
from rasterio.warp import calculate_default_transform, reproject

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _read_raster(path: str) -> tuple[np.ndarray, Affine, str, float | None]:
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32", copy=False)
        nodata = src.nodata
        if nodata is not None:
            arr = np.where(arr == nodata, np.nan, arr)
        return (
            arr,
            src.transform,
            src.crs.to_string() if src.crs else "EPSG:4326",
            nodata,
        )


def _reproject_like(
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


def _bounds_from_transform(
    transform: Affine, width: int, height: int
) -> tuple[float, float, float, float]:
    # Returns (left, bottom, right, top)
    xmin, ymin, xmax, ymax = array_bounds(height, width, transform)
    return float(xmin), float(ymin), float(xmax), float(ymax)


def plot_yield_gap(
    potential_path: str, actual_path: str, output_path: str, crop: str | None = None
) -> None:
    logger.info("Reading rasters")
    pot, pot_transform, pot_crs, _ = _read_raster(potential_path)
    act, act_transform, act_crs, _ = _read_raster(actual_path)

    # Convert from kg/ha to t/ha for potential yields
    pot /= 1000.0

    # Reproject 'act' onto the grid of 'pot' if needed
    if (
        (act_crs != pot_crs)
        or (act.shape != pot.shape)
        or (act_transform != pot_transform)
    ):
        logger.info("Reprojecting actual yield to potential grid for difference")
        act_on_pot = np.full_like(pot, np.nan, dtype="float32")
        reproject(
            source=act,
            destination=act_on_pot,
            src_transform=act_transform,
            src_crs=act_crs,
            dst_transform=pot_transform,
            dst_crs=pot_crs,
            resampling=Resampling.average,
            dst_nodata=np.nan,
        )
        act = act_on_pot

    # Compute gap = actual - potential
    gap = act - pot

    # Reproject gap to Equal Earth for display
    ee_crs = "+proj=eqearth"
    logger.info("Projecting to Equal Earth for plotting")
    left, bottom, right, top = _bounds_from_transform(
        pot_transform, pot.shape[1], pot.shape[0]
    )
    transform_ee, width_ee, height_ee = calculate_default_transform(
        pot_crs, ee_crs, pot.shape[1], pot.shape[0], left, bottom, right, top
    )
    gap_ee = _reproject_like(
        gap,
        pot_transform,
        pot_crs,
        transform_ee,
        ee_crs,
        (height_ee, width_ee),
        resampling=Resampling.average,
    )

    # Color limits: symmetric around 0 using robust percentiles
    valid = gap_ee[np.isfinite(gap_ee)]
    if valid.size:
        vmax = float(np.percentile(np.abs(valid), 98))
        if vmax <= 0:
            vmax = float(np.nanmax(np.abs(valid))) or 1.0
    else:
        vmax = 1.0
    vmin = -vmax

    # Prepare output
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Plot
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    xmin, ymin, xmax, ymax = array_bounds(height_ee, width_ee, transform_ee)
    extent = (xmin, xmax, ymin, ymax)
    cmap = plt.colormaps["RdBu"]  # blue for low (negative), red for high (positive)
    cmap = cmap.copy()
    cmap.set_bad(color="#f0f0f0")

    im = ax.imshow(
        gap_ee,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        origin="upper",
        extent=(extent[0], extent[1], extent[2], extent[3]),
        interpolation="nearest",
    )
    ax.set_axis_off()

    title = "Yield gap (actual - potential)"
    if crop:
        title += f" — {crop}"
    ax.set_title(title)

    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("Difference [t/ha]")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Saved yield gap map to %s", output_path)


if __name__ == "__main__":
    setup_script_logging(snakemake.log[0])
    # Snakemake interface
    plot_yield_gap(
        potential_path=snakemake.input.potential_yield,
        actual_path=snakemake.input.actual_yield,
        output_path=snakemake.output.pdf,
        crop=snakemake.wildcards.crop,
    )
