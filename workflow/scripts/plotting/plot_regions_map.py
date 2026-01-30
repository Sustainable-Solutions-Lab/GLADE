#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib

from workflow.scripts.logging_config import setup_script_logging

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

logger = logging.getLogger(__name__)


def plot_regions_map(regions_path: str, output_path: str) -> None:
    logger.info("Loading regions from %s", regions_path)
    gdf = gpd.read_file(regions_path)

    if gdf.crs is None:
        logger.warning("Input CRS missing; assuming EPSG:4326 (WGS84)")
        gdf = gdf.set_crs(4326, allow_override=True)
    else:
        gdf = gdf.to_crs(4326)

    # Prepare output directory
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(
        figsize=(12, 6),
        dpi=150,
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#f7f9fb")
    ax.set_global()

    ax.add_geometries(
        gdf.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="#cfd8dc",
        edgecolor="#444444",
        linewidth=0.4,
        zorder=1,
    )

    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    gl = ax.gridlines(
        draw_labels=True,
        crs=ccrs.PlateCarree(),
        linewidth=0.35,
        color="#888888",
        alpha=0.45,
        linestyle="--",
    )
    gl.xlocator = mticker.FixedLocator(np.arange(-180, 181, 30))
    gl.ylocator = mticker.FixedLocator(np.arange(-60, 61, 15))
    gl.xformatter = LongitudeFormatter(number_format=".0f")
    gl.yformatter = LatitudeFormatter(number_format=".0f")
    gl.xlabel_style = {"size": 8, "color": "#555555"}
    gl.ylabel_style = {"size": 8, "color": "#555555"}
    gl.top_labels = False
    gl.right_labels = False

    ax.set_xlabel("Longitude", fontsize=8, color="#555555")
    ax.set_ylabel("Latitude", fontsize=8, color="#555555")

    ax.set_title("Regions", fontsize=12)
    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)

    logger.info("Saved regions map to %s", output_path)


if __name__ == "__main__":
    setup_script_logging(snakemake.log[0])
    plot_regions_map(snakemake.input.regions, snakemake.output.pdf)
