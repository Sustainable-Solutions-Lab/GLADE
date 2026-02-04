#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate crop yield potential maps for crop production documentation.

Shows spatially-explicit yield potentials for selected crops,
illustrating the GAEZ data that drives the optimization.
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from workflow.scripts.doc_figures_config import (
    COLORMAPS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    yield_raster_path: str,
    regions_path: str,
    conversions_path: str,
    svg_output_path: str,
    png_output_path: str,
    crop_name: str,
):
    """Generate crop yield potential map.

    Args:
        yield_raster_path: Path to GAEZ yield raster
        regions_path: Path to regions GeoJSON file
        conversions_path: Path to yield unit conversions CSV
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
        crop_name: Name of the crop being visualized
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

    # Load yield raster
    with rasterio.open(yield_raster_path) as src:
        yield_data = src.read(1)
        bounds = src.bounds

    # Load conversions (skip comment lines)
    conversions = pd.read_csv(conversions_path, comment="#", index_col="code")
    conversion_factor = (
        conversions.loc[crop_name, "factor_to_t_per_ha"]
        if crop_name in conversions.index
        else 0.001
    )

    # Convert to t/ha (default is kg/ha → t/ha = 0.001)
    yield_t_ha = yield_data * conversion_factor

    # Mask zeros and very low values for better visualization
    yield_t_ha = np.where(yield_t_ha < 0.1, np.nan, yield_t_ha)

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("#f7f9fb")

    # Plot yield raster
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]

    im = ax.imshow(
        yield_t_ha,
        cmap=COLORMAPS["yield"],
        extent=extent,
        origin="upper",
        interpolation="bilinear",
        vmin=0,
        vmax=np.nanpercentile(
            yield_t_ha, 95
        ),  # Cap at 95th percentile for better contrast
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
    cbar.set_label("Yield (tonnes/hectare)", fontsize=FONT_SIZES["colorbar_label"])

    # Add data source note
    ax.text(
        0.02,
        0.02,
        "Data: GAEZ v5",
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
        yield_raster_path=snakemake.input.yield_raster,
        regions_path=snakemake.input.regions,
        conversions_path=snakemake.input.conversions,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
        crop_name=snakemake.wildcards.crop,
    )
