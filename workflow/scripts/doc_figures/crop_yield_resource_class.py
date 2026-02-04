#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate resource class yield comparison maps for crop production documentation.

Shows side-by-side comparison of average yields in different resource classes,
illustrating how yield potential varies by land quality.
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
    crop_yields_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
    crop_name: str,
    resource_class_1: int = 1,
    resource_class_2: int = 2,
):
    """Generate resource class yield comparison maps.

    Args:
        crop_yields_path: Path to crop yields CSV file
        regions_path: Path to regions GeoJSON file
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
        crop_name: Name of the crop being visualized
        resource_class_1: First resource class to show (default: 1)
        resource_class_2: Second resource class to show (default: 2)
    """
    # Apply documentation styling
    apply_doc_style()

    # Load regions
    regions = gpd.read_file(regions_path)

    # Ensure regions CRS is WGS84
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    # Load crop yields
    yields_long = pd.read_csv(crop_yields_path)
    yields = (
        yields_long.pivot(
            index=["region", "resource_class"], columns="variable", values="value"
        )
        .rename_axis(columns=None)
        .reset_index()
    )
    yields["resource_class"] = yields["resource_class"].astype(int)
    yields["yield"] = pd.to_numeric(yields["yield"], errors="coerce")

    # Filter for the two resource classes
    yields_class_1 = yields[yields["resource_class"] == resource_class_1][
        ["region", "yield"]
    ].rename(columns={"yield": "yield_class_1"})

    yields_class_2 = yields[yields["resource_class"] == resource_class_2][
        ["region", "yield"]
    ].rename(columns={"yield": "yield_class_2"})

    # Merge with regions
    regions = regions.merge(
        yields_class_1, left_on="region", right_on="region", how="left"
    )
    regions = regions.merge(
        yields_class_2, left_on="region", right_on="region", how="left"
    )

    # Create figure with two subplots side by side
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    # Determine shared color scale (use same vmin/vmax for both)
    vmin = min(regions["yield_class_1"].min(), regions["yield_class_2"].min())
    vmax = max(regions["yield_class_1"].max(), regions["yield_class_2"].max())

    # Plot resource class 1
    ax1.set_global()
    ax1.set_facecolor("#f7f9fb")

    regions.plot(
        column="yield_class_1",
        ax=ax1,
        cmap=COLORMAPS["yield"],
        edgecolor="white",
        linewidth=0.3,
        alpha=0.85,
        vmin=vmin,
        vmax=vmax,
        transform=ccrs.PlateCarree(),
        legend=False,
        missing_kwds={"color": "#e0e0e0", "alpha": 0.3},
    )

    ax1.coastlines(linewidth=0.3, color="#888888", alpha=0.3)

    # Style the map frame
    for name, spine in ax1.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    crop_display_name = crop_name.replace("-", " ").title()
    ax1.set_title(
        f"{crop_display_name} - Resource Class {resource_class_1}",
        fontsize=FONT_SIZES["title"],
        pad=10,
    )

    # Plot resource class 2
    ax2.set_global()
    ax2.set_facecolor("#f7f9fb")

    regions.plot(
        column="yield_class_2",
        ax=ax2,
        cmap=COLORMAPS["yield"],
        edgecolor="white",
        linewidth=0.3,
        alpha=0.85,
        vmin=vmin,
        vmax=vmax,
        transform=ccrs.PlateCarree(),
        legend=False,
        missing_kwds={"color": "#e0e0e0", "alpha": 0.3},
    )

    ax2.coastlines(linewidth=0.3, color="#888888", alpha=0.3)

    # Style the map frame
    for name, spine in ax2.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    ax2.set_title(
        f"{crop_display_name} - Resource Class {resource_class_2}",
        fontsize=FONT_SIZES["title"],
        pad=10,
    )

    # Add shared colorbar at bottom
    fig.subplots_adjust(bottom=0.15)
    cbar_ax = fig.add_axes([0.25, 0.08, 0.5, 0.02])
    sm = plt.cm.ScalarMappable(
        cmap=COLORMAPS["yield"],
        norm=plt.Normalize(vmin=vmin, vmax=vmax),
    )
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_label(
        "Average Yield (tonnes/hectare)", fontsize=FONT_SIZES["colorbar_label"]
    )

    plt.tight_layout()

    # Save SVG and PNG
    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    # Snakemake integration
    main(
        crop_yields_path=snakemake.input.crop_yields,
        regions_path=snakemake.input.regions,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
        crop_name=snakemake.wildcards.crop,
        resource_class_1=snakemake.params.get("resource_class_1", 1),
        resource_class_2=snakemake.params.get("resource_class_2", 2),
    )
