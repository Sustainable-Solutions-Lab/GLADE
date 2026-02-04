#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate basin water availability map for crop production documentation.

Shows yearly average blue water availability by river basin (GRDC 405 basins).
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
    basin_shapefile_path: str,
    water_data_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate basin water availability map.

    Args:
        basin_shapefile_path: Path to basin shapefile
        water_data_path: Path to blue water availability CSV
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    # Apply documentation styling
    apply_doc_style()

    # Load basin shapefile
    basins = gpd.read_file(basin_shapefile_path)

    # Ensure CRS is WGS84
    if basins.crs is None:
        basins = basins.set_crs(4326, allow_override=True)
    else:
        basins = basins.to_crs(4326)

    # Load water availability data
    water_data = pd.read_csv(water_data_path)

    # Calculate yearly average water availability per basin
    yearly_avg = (
        water_data.groupby("basin_id")
        .agg({"blue_water_availability_m3": "sum", "area_km2": "first"})
        .reset_index()
    )
    yearly_avg.columns = ["basin_id", "annual_water_m3", "area_km2"]

    # Convert to mm/year for better spatial comparison
    # mm = m³ / (km² * 1e6 m²/km²) * 1000 mm/m = m³ / (km² * 1000)
    yearly_avg["annual_water_mm"] = yearly_avg["annual_water_m3"] / (
        yearly_avg["area_km2"] * 1000
    )

    # Merge with basins (assuming basin shapefile has a matching ID field)
    # Common field names: BASIN_ID, BasinID, ID, FID
    # Let's check what the shapefile has
    basin_id_col = None
    for col in ["BASIN_ID", "BasinID", "ID", "FID", "basin_id"]:
        if col in basins.columns:
            basin_id_col = col
            break

    if basin_id_col is None:
        # Fallback: use index + 1
        basins["basin_id"] = basins.index + 1
        basin_id_col = "basin_id"

    basins = basins.merge(
        yearly_avg, left_on=basin_id_col, right_on="basin_id", how="left"
    )

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("white")

    # Plot basin water availability
    basins.plot(
        column="annual_water_mm",
        ax=ax,
        cmap=COLORMAPS["water"],
        edgecolor="white",
        linewidth=0.2,
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
        "Annual Blue Water Availability by Basin", fontsize=FONT_SIZES["title"], pad=10
    )

    # Add colorbar
    sm = plt.cm.ScalarMappable(
        cmap=COLORMAPS["water"],
        norm=plt.Normalize(
            vmin=basins["annual_water_mm"].min(),
            vmax=basins["annual_water_mm"].quantile(0.95),  # Cap at 95th percentile
        ),
    )
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, orientation="horizontal", pad=0.05, fraction=0.046)
    cbar.set_label(
        "Annual Water Availability (mm/year)", fontsize=FONT_SIZES["colorbar_label"]
    )

    # Add data source note
    ax.text(
        0.02,
        0.02,
        "Data: Water Footprint Network (GRDC 405 basins)",
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
        basin_shapefile_path=snakemake.input.basin_shapefile,
        water_data_path=snakemake.input.water_data,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
