#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate regional water availability map for crop production documentation.

Shows growing season water availability by optimization region.
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.doc_figures_config import (
    COLORMAPS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    regions_path: str,
    water_data_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate regional water availability map.

    Args:
        regions_path: Path to regions GeoJSON file
        water_data_path: Path to regional water availability CSV
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    # Apply documentation styling
    apply_doc_style()

    # Load regions
    regions = gpd.read_file(regions_path)

    # Ensure CRS is WGS84
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    # Load water availability data
    water_data = pd.read_csv(water_data_path)

    # Calculate region areas in km²
    # Need to project to equal-area projection for accurate area calculation
    regions_area = regions.to_crs("ESRI:54009")  # Mollweide equal-area projection
    regions["area_km2"] = regions_area.geometry.area / 1e6  # Convert m² to km²

    # Merge with regions
    regions = regions.merge(water_data, left_on="region", right_on="region", how="left")

    # Convert to mm for better spatial comparison
    # mm = m³ / (km² * 1e6 m²/km²) * 1000 mm/m = m³ / (km² * 1000)
    regions["growing_season_water_mm"] = regions[
        "growing_season_water_available_m3"
    ] / (regions["area_km2"] * 1000)

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("white")

    # Plot regional water availability
    regions.plot(
        column="growing_season_water_mm",
        ax=ax,
        cmap=COLORMAPS["water"],
        edgecolor="white",
        linewidth=0.3,
        transform=ccrs.PlateCarree(),
        legend=False,
        missing_kwds={"color": "#e0e0e0", "alpha": 0.3},
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

    ax.set_title(
        "Growing Season Water Availability by Region",
        fontsize=FONT_SIZES["title"],
        pad=10,
    )

    # Add colorbar
    sm = plt.cm.ScalarMappable(
        cmap=COLORMAPS["water"],
        norm=plt.Normalize(
            vmin=regions["growing_season_water_mm"].min(),
            vmax=regions["growing_season_water_mm"].quantile(
                0.95
            ),  # Cap at 95th percentile
        ),
    )
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, orientation="horizontal", pad=0.05, fraction=0.046)
    cbar.set_label(
        "Growing Season Water Availability (mm)", fontsize=FONT_SIZES["colorbar_label"]
    )

    # Add data source note
    ax.text(
        0.02,
        0.02,
        "Data: Water Footprint Network, aggregated to regions",
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
        regions_path=snakemake.input.regions,
        water_data_path=snakemake.input.water_data,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
