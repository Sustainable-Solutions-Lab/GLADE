#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate trade network map for food processing documentation.

Shows trade hubs and connections (country-to-hub and hub-to-hub links).
"""

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from sklearn.cluster import KMeans

from workflow.scripts.doc_figures_config import (
    COLORS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def compute_hubs_and_assignments(
    regions_gdf: gpd.GeoDataFrame, n_hubs: int
) -> tuple[np.ndarray, dict[str, int], gpd.GeoDataFrame]:
    """Compute hub positions and country assignments.

    Args:
        regions_gdf: GeoDataFrame with regions
        n_hubs: Number of hubs to create

    Returns:
        Tuple of (hub_positions_lonlat, country_to_hub_dict, country_centroids_gdf)
    """
    # Ensure WGS84
    if regions_gdf.crs is None:
        regions_gdf = regions_gdf.set_crs(4326, allow_override=True)
    else:
        regions_gdf = regions_gdf.to_crs(4326)

    # Convert to Equal Earth projection (EPSG:6933) for k-means
    gdf_ee = regions_gdf.to_crs(6933)

    # Compute region centroids
    cent = gdf_ee.geometry.centroid
    X = np.column_stack([cent.x.values, cent.y.values])

    # Adjust hub count if needed
    k = min(max(1, n_hubs), len(X))

    # Run k-means clustering (same parameters as build_model.py)
    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    km.fit_predict(X)
    centers_ee = km.cluster_centers_

    # Convert hub positions back to lon/lat
    from pyproj import Transformer

    transformer = Transformer.from_crs(6933, 4326, always_xy=True)
    hub_lons, hub_lats = transformer.transform(centers_ee[:, 0], centers_ee[:, 1])
    hub_positions_lonlat = np.column_stack([hub_lons, hub_lats])

    # Dissolve regions by country and compute country centroids
    gdf_countries_ee = gdf_ee.dissolve(by="country", as_index=True)
    ccent = gdf_countries_ee.geometry.centroid
    C = np.column_stack([ccent.x.values, ccent.y.values])

    # Assign each country to nearest hub
    dch = ((C[:, None, :] - centers_ee[None, :, :]) ** 2).sum(axis=2) ** 0.5
    nearest_hub_idx = dch.argmin(axis=1)

    country_index = gdf_countries_ee.index.to_list()
    country_to_hub = {c: int(h) for c, h in zip(country_index, nearest_hub_idx)}

    # Convert country centroids back to lon/lat
    country_cent_lons, country_cent_lats = transformer.transform(
        ccent.x.values, ccent.y.values
    )
    country_centroids_gdf = gpd.GeoDataFrame(
        {
            "country": country_index,
            "lon": country_cent_lons,
            "lat": country_cent_lats,
        },
        geometry=gpd.points_from_xy(country_cent_lons, country_cent_lats),
        crs=4326,
    )

    return hub_positions_lonlat, country_to_hub, country_centroids_gdf


def main(
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
    n_hubs: int,
):
    """Generate trade network map.

    Args:
        regions_path: Path to regions GeoJSON file
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
        n_hubs: Number of trade hubs to create
    """
    # Apply documentation styling
    apply_doc_style()

    # Load regions
    regions = gpd.read_file(regions_path)

    # Compute hub positions and country assignments
    hub_positions, country_to_hub, country_centroids = compute_hubs_and_assignments(
        regions, n_hubs
    )

    # Create figure with EqualEarth projection
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    ax.set_global()
    ax.set_facecolor("white")

    # Add coastlines and region boundaries for context
    ax.coastlines(linewidth=0.3, color="#888888", alpha=0.3)
    regions_wgs84 = (
        regions.to_crs(4326) if regions.crs is not None else regions.set_crs(4326)
    )
    ax.add_geometries(
        regions_wgs84.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="black",
        linewidth=0.2,
        alpha=0.2,
    )

    # Define green color for trade network
    trade_color = COLORS["primary"]  # Brand green

    # Create geodesic calculator for computing great circle paths
    from pyproj import Geod

    geod = Geod(ellps="WGS84")

    # Helper function to create smooth great circle paths
    def great_circle_points(lon1, lat1, lon2, lat2, n_points=100):
        """Generate intermediate points along a great circle using geodesic.

        Returns list of (lons, lats) tuples - one tuple per segment.
        Segments are split at antimeridian crossings.
        """
        # Use pyproj to compute actual geodesic path
        lonlats = geod.npts(lon1, lat1, lon2, lat2, n_points)
        if lonlats:
            lons, lats = zip(*lonlats)
            # Add start and end points
            lons = [lon1, *list(lons), lon2]
            lats = [lat1, *list(lats), lat2]
        else:
            # For very short distances, just use endpoints
            lons = [lon1, lon2]
            lats = [lat1, lat2]

        # Split at antimeridian crossings (detect jumps > 180 degrees)
        segments = []
        current_lons = [lons[0]]
        current_lats = [lats[0]]

        for i in range(1, len(lons)):
            lon_diff = abs(lons[i] - lons[i - 1])

            # If jump is large (crossing antimeridian), start new segment
            if lon_diff > 180:
                segments.append((current_lons, current_lats))
                current_lons = [lons[i]]
                current_lats = [lats[i]]
            else:
                current_lons.append(lons[i])
                current_lats.append(lats[i])

        # Add final segment
        if current_lons:
            segments.append((current_lons, current_lats))

        return segments

    # Draw country-to-hub links as great circles
    for _idx, row in country_centroids.iterrows():
        country = row["country"]
        hub_idx = country_to_hub[country]
        country_lon, country_lat = row["lon"], row["lat"]
        hub_lon, hub_lat = hub_positions[hub_idx]

        # Create smooth great circle with many intermediate points (returns segments)
        segments = great_circle_points(country_lon, country_lat, hub_lon, hub_lat)
        for lons, lats in segments:
            ax.plot(
                lons,
                lats,
                color=trade_color,
                linewidth=0.5,
                alpha=0.4,
                transform=ccrs.PlateCarree(),
            )

    # Draw hub-to-hub links as great circles
    n_hubs_actual = len(hub_positions)
    for i in range(n_hubs_actual):
        for j in range(i + 1, n_hubs_actual):  # Only upper triangle to avoid duplicates
            hub_i_lon, hub_i_lat = hub_positions[i]
            hub_j_lon, hub_j_lat = hub_positions[j]

            # Create smooth great circle with many intermediate points (returns segments)
            segments = great_circle_points(hub_i_lon, hub_i_lat, hub_j_lon, hub_j_lat)
            for lons, lats in segments:
                ax.plot(
                    lons,
                    lats,
                    color=trade_color,
                    linewidth=0.8,
                    alpha=0.6,
                    transform=ccrs.PlateCarree(),
                )

    # Draw hub markers (green circles)
    ax.scatter(
        hub_positions[:, 0],
        hub_positions[:, 1],
        s=80,
        c=trade_color,
        edgecolors="white",
        linewidths=1.5,
        alpha=0.9,
        zorder=10,
        transform=ccrs.PlateCarree(),
    )

    # Style the map frame
    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    # Add legend
    from matplotlib.lines import Line2D

    legend_elements = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="w",
            markerfacecolor=trade_color,
            markersize=8,
            label="Trade hub",
        ),
        Line2D(
            [0],
            [0],
            color=trade_color,
            linewidth=0.8,
            alpha=0.6,
            label="Hub-to-hub link",
        ),
        Line2D(
            [0],
            [0],
            color=trade_color,
            linewidth=0.5,
            alpha=0.4,
            label="Country-to-hub link",
        ),
    ]
    ax.legend(
        handles=legend_elements,
        loc="lower left",
        frameon=True,
        fancybox=False,
        shadow=False,
        fontsize=FONT_SIZES["colorbar_tick"],
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
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
        n_hubs=snakemake.params.n_hubs,
    )
