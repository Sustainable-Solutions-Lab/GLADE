#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate irrigated land fraction map for crop production documentation.

Shows the fraction of land equipped for irrigation from GAEZ v5.
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import rasterio.enums

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    irrigated_fraction_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate irrigated land fraction map.

    Args:
        irrigated_fraction_path: Path to GAEZ irrigated land fraction raster
        regions_path: Path to regions GeoJSON file
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    # Apply documentation styling
    apply_doc_style()

    # Load regions for overlay
    regions = gpd.read_file(regions_path)

    # Ensure regions CRS is WGS84
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    # Load irrigated fraction raster (downsampled for memory efficiency)
    with rasterio.open(irrigated_fraction_path) as src:
        # Downsample by factor of 10 to reduce memory usage while maintaining detail
        out_shape = (src.height // 10, src.width // 10)
        irrigated_fraction = src.read(
            1, out_shape=out_shape, resampling=rasterio.enums.Resampling.average
        )
        bounds = src.bounds

        # Handle nodata
        if src.nodata is not None:
            irrigated_fraction = np.where(
                irrigated_fraction == src.nodata, np.nan, irrigated_fraction
            )

    # Convert from percentage to fraction
    irrigated_fraction = irrigated_fraction / 100.0

    # Mask very low values and values outside [0, 1]
    irrigated_fraction = np.where(
        (irrigated_fraction < 0.01) | (irrigated_fraction > 1.0),
        np.nan,
        irrigated_fraction,
    )

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("white")

    # Plot irrigated fraction raster
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]

    im = ax.imshow(
        irrigated_fraction,
        cmap="Blues",
        extent=extent,
        origin="upper",
        interpolation="nearest",
        vmin=0,
        vmax=1.0,
        transform=ccrs.PlateCarree(),
    )

    # Overlay region boundaries
    ax.add_geometries(
        regions.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="black",
        linewidth=0.3,
        alpha=0.3,
    )

    # Add coastlines for context
    ax.coastlines(linewidth=0.3, color="#888888", alpha=0.3)

    # Style the map frame
    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, orientation="horizontal", pad=0.05, fraction=0.046)
    cbar.set_label(
        "Fraction of land equipped for irrigation",
        fontsize=FONT_SIZES["colorbar_label"],
    )

    # Add data source note
    ax.text(
        0.02,
        0.02,
        "Data: GAEZ v5 (LR-IRR)",
        transform=ax.transAxes,
        fontsize=FONT_SIZES["colorbar_tick"],
        verticalalignment="bottom",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.7,
            "edgecolor": "none",
        },
    )

    plt.tight_layout()

    # Save SVG and PNG
    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    # Snakemake integration
    main(
        irrigated_fraction_path=snakemake.input.irrigated_fraction,
        regions_path=snakemake.input.regions,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
