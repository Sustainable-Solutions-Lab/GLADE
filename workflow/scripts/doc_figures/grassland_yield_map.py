#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate grassland yield map for livestock documentation.

Shows managed grassland yields from ISIMIP LPJmL historical simulations.
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from workflow.scripts.doc_figures_config import (
    COLORMAPS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    grassland_yield_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate managed grassland yield map.

    Args:
        grassland_yield_path: Path to ISIMIP grassland yield NetCDF
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

    # Load grassland yield data
    # decode_times=False to avoid issues with non-standard time units
    ds = xr.open_dataset(grassland_yield_path, decode_times=False)

    # Average over time dimension if it exists
    if "time" in ds.dims:
        yield_data = ds["yield-mgr-noirr"].mean(dim="time").values
    else:
        yield_data = ds["yield-mgr-noirr"].values

    # Get coordinates
    lat = ds["lat"].values
    lon = ds["lon"].values

    # Data is already in t/ha/year, no conversion needed
    yield_t_ha = yield_data

    # Mask very low values for better visualization
    yield_t_ha = np.where(yield_t_ha < 0.1, np.nan, yield_t_ha)

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("white")

    # Plot grassland yield raster
    # Calculate extent from coordinates
    extent = [lon.min(), lon.max(), lat.min(), lat.max()]

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
    cbar.set_label(
        "Yield (tonnes dry matter/hectare/year)", fontsize=FONT_SIZES["colorbar_label"]
    )

    # Add data source note
    ax.text(
        0.02,
        0.02,
        "Data: ISIMIP LPJmL historical",
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
        grassland_yield_path=snakemake.input.grassland_yield,
        regions_path=snakemake.input.regions,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
