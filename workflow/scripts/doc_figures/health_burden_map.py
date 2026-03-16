#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate choropleth map of baseline diet-related chronic disease burden.

Shows countries colored by diet-attributable years of life lost (YLL) per
100,000 population, aggregated by health cluster.
"""

import cartopy.crs as ccrs
import geopandas as gpd
from matplotlib.colors import BoundaryNorm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.constants import PER_100K
from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    regions_path: str,
    clusters_path: str,
    cluster_cause_baseline_path: str,
    cluster_summary_path: str,
    svg_output_path: str,
    png_output_path: str,
):
    """Generate baseline health burden choropleth.

    Args:
        regions_path: Path to regions GeoJSON file
        clusters_path: Path to country clusters CSV
        cluster_cause_baseline_path: Path to baseline cause CSV
        cluster_summary_path: Path to cluster summary CSV
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    apply_doc_style()

    # Load baseline burden and population per cluster
    cause_df = pd.read_csv(cluster_cause_baseline_path)
    summary_df = pd.read_csv(cluster_summary_path)

    required_cause_cols = {"health_cluster", "yll_attrib_rate_per_100k"}
    missing_cause_cols = required_cause_cols - set(cause_df.columns)
    if missing_cause_cols:
        raise ValueError(
            "cluster_cause_baseline is missing required columns: "
            f"{sorted(missing_cause_cols)}"
        )

    required_summary_cols = {"health_cluster", "reference_population"}
    missing_summary_cols = required_summary_cols - set(summary_df.columns)
    if missing_summary_cols:
        raise ValueError(
            "cluster_summary is missing required columns: "
            f"{sorted(missing_summary_cols)}"
        )

    # Reconstruct absolute YLL per cluster, then convert back to per-100k after
    # aggregation across causes.
    burden = cause_df.groupby("health_cluster", as_index=False)[
        "yll_attrib_rate_per_100k"
    ].sum()
    burden = burden.merge(
        summary_df[["health_cluster", "reference_population"]],
        on="health_cluster",
        how="left",
        validate="one_to_one",
    )
    burden["yll_absolute"] = (
        burden["yll_attrib_rate_per_100k"] / PER_100K * burden["reference_population"]
    )
    burden["yll_per_100k"] = (
        burden["yll_absolute"] / burden["reference_population"] * PER_100K
    )

    cluster_burden = dict(zip(burden["health_cluster"], burden["yll_per_100k"]))

    # Load country-to-cluster mapping
    clusters = pd.read_csv(clusters_path)
    clusters["yll_per_100k"] = clusters["health_cluster"].map(cluster_burden)

    # Load regions and dissolve to countries
    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)
    countries = regions.dissolve(by="country", as_index=False)

    # Join burden data
    countries = countries.merge(
        clusters[["country_iso3", "yll_per_100k"]],
        left_on="country",
        right_on="country_iso3",
        how="left",
    )

    # Set up colormap with round breakpoints
    cmap = plt.colormaps["YlOrRd"]
    max_burden = burden["yll_per_100k"].max()
    upper_bound = max(5000, int(np.ceil(max_burden / 500.0) * 500) + 500)
    bounds = np.arange(500, upper_bound, 500)
    norm = BoundaryNorm(bounds, cmap.N)

    # Create figure
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_global()
    ax.set_facecolor("#f7f9fb")

    # Plot countries without data in light gray
    no_data = countries[countries["yll_per_100k"].isna()]
    for _, row in no_data.iterrows():
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor="#e0e0e0",
            edgecolor="white",
            linewidth=0.3,
        )

    # Plot countries with data
    has_data = countries[countries["yll_per_100k"].notna()]
    for _, row in has_data.iterrows():
        color = cmap(norm(row["yll_per_100k"]))
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor="white",
            linewidth=0.3,
        )

    ax.coastlines(linewidth=0.3, color="#888888", alpha=0.3)

    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    # Colorbar
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(
        sm, ax=ax, orientation="horizontal", fraction=0.046, pad=0.04, shrink=0.6
    )
    cbar.set_label("YLL per 100,000 population", fontsize=FONT_SIZES["label"])
    cbar.ax.tick_params(labelsize=FONT_SIZES["tick"])

    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        regions_path=snakemake.input.regions,
        clusters_path=snakemake.input.clusters,
        cluster_cause_baseline_path=snakemake.input.cluster_cause_baseline,
        cluster_summary_path=snakemake.input.cluster_summary,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
