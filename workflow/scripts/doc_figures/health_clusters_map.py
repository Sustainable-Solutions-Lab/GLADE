#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate health cluster map for documentation.

Shows countries colored by their health cluster assignment, illustrating
the multi-objective clustering approach that balances geographic proximity,
GDP per capita similarity, and population balance.
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    regions_path: str,
    clusters_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate health cluster map.

    Args:
        regions_path: Path to regions GeoJSON file
        clusters_path: Path to country clusters CSV file
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    apply_doc_style()

    # Load regions and dissolve by country
    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    countries = regions.dissolve(by="country", as_index=False)

    # Load cluster assignments
    clusters = pd.read_csv(clusters_path)
    cluster_map = dict(zip(clusters["country_iso3"], clusters["health_cluster"]))

    # Add cluster column to countries GeoDataFrame
    countries["health_cluster"] = countries["country"].map(cluster_map)

    # Filter to countries with cluster assignments
    countries = countries[countries["health_cluster"].notna()].copy()
    countries["health_cluster"] = countries["health_cluster"].astype(int)

    # Get number of unique clusters
    n_clusters = countries["health_cluster"].nunique()

    # Create categorical colormap using tab20 for good separation
    # Cycle through colors if more than 20 clusters
    base_cmap = plt.colormaps["tab20"]
    colors = [base_cmap(i % 20) for i in range(n_clusters)]
    cluster_ids = sorted(countries["health_cluster"].unique())
    color_map = dict(zip(cluster_ids, colors))
    countries["color"] = countries["health_cluster"].map(color_map)

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("#f7f9fb")

    # Plot each country with its cluster color
    for _, row in countries.iterrows():
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=row["color"],
            edgecolor="white",
            linewidth=0.3,
            alpha=0.8,
        )

    # Add coastlines for context (very subtle)
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

    # Add text annotation with cluster count
    ax.text(
        0.02,
        0.98,
        f"{n_clusters} health clusters\n{len(countries)} countries",
        transform=ax.transAxes,
        fontsize=FONT_SIZES["annotation"],
        verticalalignment="top",
        bbox={
            "boxstyle": "round,pad=0.5",
            "facecolor": "white",
            "alpha": 0.8,
            "edgecolor": "none",
        },
    )

    # Save SVG and PNG
    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        regions_path=snakemake.input.regions,
        clusters_path=snakemake.input.clusters,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
