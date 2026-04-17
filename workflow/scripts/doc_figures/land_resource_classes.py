#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate resource class stratification map for land use documentation.

Shows how land within each region is stratified by yield potential,
capturing heterogeneity without excessive computational burden.
Focuses on a single example region (California) to illustrate the concept.
"""

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)


def main(
    classes_path: str, regions_path: str, svg_output_path: str, png_output_path: str
):
    """Generate resource class distribution map for California region.

    Args:
        classes_path: Path to resource classes NetCDF file
        regions_path: Path to regions GeoJSON file
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    # Apply documentation styling
    apply_doc_style()

    # Load data
    classes = xr.open_dataset(classes_path)
    regions = gpd.read_file(regions_path)

    # Get the geospatial transform to convert pixel coords to lon/lat
    from rasterio.transform import Affine

    transform = Affine.from_gdal(*classes.attrs["transform"])

    # Calculate actual lon/lat coordinates from the transform
    # The x and y in the dataset are just pixel indices, not coordinates
    height, width = classes["resource_class"].shape
    transform.c + width * transform.a  # Right edge
    transform.f + height * transform.e  # Bottom edge (e is negative)

    # Ensure regions CRS is WGS84
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    # Find the region containing California (USA)
    # Look for regions that contain USA and are in the California longitude/latitude range
    ca_regions = regions[
        (regions["country"] == "USA")
        & (regions.geometry.centroid.x > -125)
        & (regions.geometry.centroid.x < -114)
        & (regions.geometry.centroid.y > 32)
        & (regions.geometry.centroid.y < 42)
    ]

    if len(ca_regions) == 0:
        # Fallback: just find any USA region
        ca_regions = regions[regions["country"] == "USA"].iloc[:1]

    example_region = ca_regions.iloc[0]
    region_bounds = example_region.geometry.bounds  # (minx, miny, maxx, maxy)

    # Extract resource class raster
    rc_data = classes["resource_class"].values

    # Also get the region_id raster to mask by region
    region_ids = classes["region_id"].values

    # Get the region ID for the example region
    example_region_id = int(example_region["region"].replace("region", ""))

    # Mask to only show data within the example region
    # First mask invalid values, then mask pixels outside the region
    rc_masked = np.ma.masked_where(
        (rc_data < 0) | np.isnan(rc_data) | (region_ids != example_region_id), rc_data
    )

    # Get unique classes
    valid_data = rc_data[(rc_data >= 0) & ~np.isnan(rc_data)]
    if valid_data.size == 0:
        raise ValueError("No valid resource class data found")

    unique_classes = np.unique(valid_data)
    n_classes = len(unique_classes)
    max_class = int(unique_classes.max())

    # Create categorical colormap - NOT reversed (higher class = better = lighter/greener)
    cmap = plt.colormaps["YlGnBu"]
    span = max(n_classes - 1, 1)
    colors = [
        cmap(0.3 + 0.5 * (i / span)) if n_classes > 1 else cmap(0.45)
        for i in range(n_classes)
    ]
    colors_hex = [mcolors.to_hex(c) for c in colors]  # Don't reverse!
    cmap_categorical = mcolors.ListedColormap(colors_hex)
    bounds = np.arange(-0.5, max_class + 1.5, 1.0)
    norm = mcolors.BoundaryNorm(bounds, cmap_categorical.N)

    # Set up stereographic projection centered on the region
    center_lon = (region_bounds[0] + region_bounds[2]) / 2
    center_lat = (region_bounds[1] + region_bounds[3]) / 2

    # Create figure (half-width for documentation)
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.85),
        subplot_kw={
            "projection": ccrs.Stereographic(
                central_longitude=center_lon, central_latitude=center_lat
            )
        },
    )

    # Set extent to region bounds with some padding
    padding = 2  # degrees
    ax.set_extent(
        [
            region_bounds[0] - padding,
            region_bounds[2] + padding,
            region_bounds[1] - padding,
            region_bounds[3] + padding,
        ],
        crs=ccrs.PlateCarree(),
    )

    # Plot resource classes as raster using imshow
    # Get extent from the actual lon/lat coordinates (not pixel indices)
    # Calculate from transform since classes.x and classes.y are pixel indices
    extent = [
        transform.c,  # lon_min
        transform.c + width * transform.a,  # lon_max
        transform.f + height * transform.e,  # lat_min (e is negative)
        transform.f,  # lat_max
    ]

    im = ax.imshow(
        rc_masked,
        cmap=cmap_categorical,
        norm=norm,
        extent=extent,
        origin="upper",
        interpolation="nearest",
        transform=ccrs.PlateCarree(),
    )

    # Highlight the example region boundary
    ax.add_geometries(
        [example_region.geometry],
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="#d62728",
        linewidth=2,
        alpha=0.9,
    )

    # Add nearby region boundaries for context
    nearby_regions = regions.cx[
        region_bounds[0] - padding : region_bounds[2] + padding,
        region_bounds[1] - padding : region_bounds[3] + padding,
    ]
    ax.add_geometries(
        nearby_regions.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="#666666",
        linewidth=0.5,
        alpha=0.4,
    )

    # Add coastlines and features
    ax.coastlines(linewidth=0.8, color="#333333", alpha=0.6)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor="#888888", alpha=0.5)

    # Style the map frame
    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(1)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    # Add categorical colorbar
    cbar = plt.colorbar(
        im,
        ax=ax,
        orientation="horizontal",
        pad=0.08,
        fraction=0.046,
        ticks=np.arange(max_class + 1),
    )
    cbar.set_label(
        "Resource Class (higher = better yield potential)",
        fontsize=FONT_SIZES["colorbar_label"],
    )
    # Set tick labels to show class numbers
    cbar.ax.set_xticklabels([f"{int(i)}" for i in range(max_class + 1)])

    plt.tight_layout()

    # Save SVG and PNG
    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    # Snakemake integration
    main(
        classes_path=snakemake.input.classes,
        regions_path=snakemake.input.regions,
        svg_output_path=snakemake.output.svg,
        png_output_path=snakemake.output.png,
    )
