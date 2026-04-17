#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from pathlib import Path

from affine import Affine
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath

matplotlib.use("pdf")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from pyproj import Geod
from rasterio.transform import array_bounds
import xarray as xr

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Crop to group mapping for simplified visualization
# Groups to exclude from the map raster but still show in the bar chart.
# Feed crops (pasture/forage) typically dominate land use and overwhelm
# the map, so they are shown only in the bar chart.
EXCLUDED_MAP_GROUPS = {"Feed crops"}


def crop_groups_from_config(
    config: dict,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build crop-to-group and group-color mappings from ``plotting.crop_groups``.

    Returns:
        crop_to_group: Mapping from crop name to group display name.
        crop_group_colors: Ordered mapping from group display name to hex color.
    """
    crop_to_group: dict[str, str] = {}
    crop_group_colors: dict[str, str] = {}
    for group_name, group_def in config["plotting"]["crop_groups"].items():
        crop_group_colors[group_name] = group_def["color"]
        for crop in group_def["crops"]:
            crop_to_group[crop] = group_name
    return crop_to_group, crop_group_colors


def _load_land_use_by_region_class_crop(path: str) -> pd.DataFrame:
    """Load land use from analysis output, aggregated by region/resource_class/crop.

    Returns DataFrame with columns: region, resource_class, crop, used_ha
    """
    df = pd.read_parquet(path)
    if df.empty:
        return pd.DataFrame(columns=["region", "resource_class", "crop", "used_ha"])

    # Aggregate by region, resource_class, crop (sum over water_supply and country)
    df = df.groupby(["region", "resource_class", "crop"], as_index=False)[
        "area_mha"
    ].sum()

    # Convert Mha to ha
    df["used_ha"] = df["area_mha"] * 1e6
    df = df.drop(columns=["area_mha"])

    # Filter to positive values
    df = df[df["used_ha"] > 0]

    return df


def _load_resource_classes(path: str) -> dict:
    """Load resource class grid, region grid, extent, and cell areas.

    Returns dict with keys: class_grid, region_grid, extent, cell_areas_ha, shape
    """
    ds = xr.open_dataset(path)
    class_grid = ds["resource_class"].values.astype(np.int16)
    region_grid = ds["region_id"].values.astype(np.int32)
    transform_gdal = ds.attrs["transform"]
    transform = Affine.from_gdal(*transform_gdal)
    height, width = class_grid.shape
    lon_min, lat_min, lon_max, lat_max = array_bounds(height, width, transform)
    extent = (lon_min, lon_max, lat_min, lat_max)

    # Compute cell areas per row (varies by latitude)
    pixel_width_deg = abs(transform_gdal[1])
    pixel_height_deg = abs(transform_gdal[5])
    top = transform_gdal[3]
    bottom = top + height * transform_gdal[5]

    lats = np.linspace(
        top - pixel_height_deg / 2, bottom + pixel_height_deg / 2, height
    )
    geod = Geod(ellps="WGS84")
    cell_areas_ha_1d = np.zeros(height, dtype=np.float32)

    for i, lat in enumerate(lats):
        lat_top = lat + pixel_height_deg / 2
        lat_bottom = lat - pixel_height_deg / 2
        lon_left = lon_min
        lon_right = lon_min + pixel_width_deg
        lons = [lon_left, lon_right, lon_right, lon_left, lon_left]
        lats_poly = [lat_bottom, lat_bottom, lat_top, lat_top, lat_bottom]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats_poly)
        cell_areas_ha_1d[i] = abs(area_m2) / 10000.0

    # Broadcast to 2D
    cell_areas_ha = np.repeat(cell_areas_ha_1d[:, np.newaxis], width, axis=1)

    ds.close()
    return {
        "class_grid": class_grid,
        "region_grid": region_grid,
        "extent": extent,
        "cell_areas_ha": cell_areas_ha,
        "shape": (height, width),
    }


def _load_potential_area(
    land_area_by_class_path: str,
    land_grazing_only_path: str,
) -> pd.Series:
    """Load potential cropland + grassland area by (region, resource_class).

    Combines:
    - Potential cropland: sum of rainfed + irrigated from land_area_by_class.csv
    - Potential grassland (marginal pasture): from land_grazing_only_by_class.csv

    Returns:
        Series indexed by (region, resource_class) with area in hectares.
    """
    # Load potential cropland (rainfed + irrigated)
    cropland_df = pd.read_csv(land_area_by_class_path)
    cropland_by_rc = cropland_df.groupby(["region", "resource_class"])["area_ha"].sum()

    # Load potential grassland (marginal pasture)
    grassland_df = pd.read_csv(land_grazing_only_path)
    grassland_by_rc = grassland_df.set_index(["region", "resource_class"])["area_ha"]

    # Combine: align indices and sum
    all_indices = cropland_by_rc.index.union(grassland_by_rc.index)
    potential_area = cropland_by_rc.reindex(
        all_indices, fill_value=0.0
    ) + grassland_by_rc.reindex(all_indices, fill_value=0.0)
    return potential_area


def _build_dominant_group_and_intensity_grids(
    land_use_df: pd.DataFrame,
    class_grid: np.ndarray,
    region_grid: np.ndarray,
    potential_area: pd.Series,
    region_name_to_id: dict[str, int],
    crop_to_group: dict[str, str],
    crop_group_colors: dict[str, str],
    excluded_map_groups: set[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, set[str]], pd.Series]:
    """Build pixel-level dominant crop group and intensity grids.

    Args:
        land_use_df: DataFrame with columns [region, resource_class, crop, used_ha]
        class_grid: 2D array of resource class IDs
        region_grid: 2D array of region IDs
        potential_area: Series indexed by (region, resource_class) with potential
            cropland + grassland area in hectares
        region_name_to_id: Mapping from region names to integer IDs
        crop_to_group: Mapping from crop name to group display name.
        crop_group_colors: Ordered mapping from group display name to hex color.
        excluded_map_groups: Groups to exclude from the map raster (dominant group
            and intensity grids) but still include in crops_by_group and area_by_crop
            for the bar chart.

    Returns:
        dominant_group_grid: 2D array of group indices (-1 for no data)
        intensity_grid: 2D array of utilization fractions (0-1)
        crops_by_group: dict mapping group names to sets of crops present
        area_by_crop: Series with total area (ha) per crop
    """
    if excluded_map_groups is None:
        excluded_map_groups = set()

    # Initialize output grids
    intensity_grid = np.full(class_grid.shape, np.nan, dtype=np.float32)
    dominant_group_grid = np.full(class_grid.shape, -1, dtype=np.int8)

    # Build group name to index mapping
    group_names = list(crop_group_colors.keys())
    group_to_idx = {name: idx for idx, name in enumerate(group_names)}

    # Track which crops appear in each group
    crops_by_group: dict[str, set[str]] = {g: set() for g in group_names}

    # Aggregate land use by (region, resource_class) and find dominant crop
    grouped = land_use_df.groupby(["region", "resource_class"])

    for (region, rc), group_df in grouped:
        total_used_ha = group_df["used_ha"].sum()
        if total_used_ha <= 0:
            continue

        # Track crops present (all groups, including excluded)
        crop_areas = group_df.groupby("crop")["used_ha"].sum()
        for crop in crop_areas.index:
            crop_group = crop_to_group.get(crop, "Other")
            if crop_group in crops_by_group:
                crops_by_group[crop_group].add(crop)

        # For map grids, filter out excluded groups
        map_crop_areas = {
            crop: area
            for crop, area in crop_areas.items()
            if crop_to_group.get(crop, "Other") not in excluded_map_groups
        }
        if not map_crop_areas:
            continue

        map_used_ha = sum(map_crop_areas.values())
        dominant_crop = max(map_crop_areas, key=map_crop_areas.get)
        dominant_group = crop_to_group.get(dominant_crop, "Other")

        # Compute intensity using potential area (cropland + grassland)
        potential_ha = potential_area.get((region, int(rc)), 0.0)
        intensity = min(map_used_ha / potential_ha, 1.0) if potential_ha > 0 else 0.0

        # Assign to pixels
        region_id = region_name_to_id.get(region)
        if region_id is not None and dominant_group in group_to_idx:
            mask = (region_grid == region_id) & (class_grid == int(rc))
            intensity_grid[mask] = intensity
            dominant_group_grid[mask] = group_to_idx[dominant_group]

    # Compute total area by crop across all regions/classes (all groups)
    area_by_crop = land_use_df.groupby("crop")["used_ha"].sum()

    return dominant_group_grid, intensity_grid, crops_by_group, area_by_crop


def _setup_regions(regions_path: str) -> gpd.GeoDataFrame:
    """Load and prepare regions GeoDataFrame."""
    gdf = gpd.read_file(regions_path)
    if gdf.crs is None:
        logger.warning("Regions GeoDataFrame missing CRS; assuming EPSG:4326")
        gdf = gdf.set_crs(4326, allow_override=True)
    else:
        gdf = gdf.to_crs(4326)

    if "region" not in gdf.columns:
        raise ValueError("Regions GeoDataFrame must contain a 'region' column")

    gdf = gdf.set_index("region", drop=False)
    return gdf


def _plot_gridcell_intensity(
    dominant_group_grid: np.ndarray,
    intensity_grid: np.ndarray,
    extent: tuple,
    gdf: gpd.GeoDataFrame,
    crops_by_group: dict[str, set[str]],
    area_by_crop: pd.Series,
    crop_to_group: dict[str, str],
    crop_group_colors: dict[str, str],
    output_path: str,
    title: str = "Dominant Crop and Land Use Intensity",
) -> None:
    """Plot dominant crop group with intensity at gridcell resolution.

    Args:
        dominant_group_grid: 2D array of group indices (-1 for no data)
        intensity_grid: 2D array of intensity values (0-1)
        extent: (lon_min, lon_max, lat_min, lat_max)
        gdf: GeoDataFrame with region boundaries
        crops_by_group: dict mapping group names to sets of crops present
        area_by_crop: Series with total area (ha) per crop
        crop_to_group: Mapping from crop name to group display name.
        crop_group_colors: Ordered mapping from group display name to hex color.
        output_path: Path to save PDF
        title: Figure title
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(
        figsize=(13, 6.5),
        dpi=150,
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#ffffff")
    ax.set_global()
    plate = ccrs.PlateCarree()

    # Build RGBA image from dominant group and intensity
    group_names = list(crop_group_colors.keys())
    height, width = dominant_group_grid.shape
    rgba = np.ones((height, width, 4), dtype=np.float32)  # Start with white, alpha=1

    for idx, group_name in enumerate(group_names):
        color = crop_group_colors[group_name]
        # Convert to RGB if needed
        if isinstance(color, str):
            color = mcolors.to_rgb(color)
        mask = dominant_group_grid == idx
        if not np.any(mask):
            continue

        # Get intensity for these pixels
        intensities = intensity_grid[mask]

        # Set RGB from group color, alpha from intensity
        rgba[mask, 0] = color[0]
        rgba[mask, 1] = color[1]
        rgba[mask, 2] = color[2]
        rgba[mask, 3] = np.clip(intensities, 0.05, 1.0)  # Min alpha for visibility

    # Set pixels with no data to fully transparent
    no_data_mask = dominant_group_grid < 0
    rgba[no_data_mask, 3] = 0.0

    # Add unmodeled land areas with light gray and white hatching (Greenland, Antarctica, etc.)
    # Hatch color follows edgecolor, so we add two layers: fill and hatching
    ax.add_feature(
        cfeature.LAND,
        facecolor="#f0f0f0",
        edgecolor="none",
        zorder=0,
    )
    # Add hatching layer with white edge (hatch color follows edgecolor)
    # More slashes = tighter spacing
    ax.add_feature(
        cfeature.LAND,
        facecolor="none",
        edgecolor="#ffffff",
        hatch="//////",
        linewidth=0.3,
        zorder=0.5,
    )

    # Add modeled regions with white fill (on top of gray land, covering hatching)
    ax.add_geometries(
        gdf.geometry,
        crs=plate,
        facecolor="#ffffff",
        edgecolor="none",
        zorder=1,
    )

    ax.imshow(
        rgba,
        origin="upper",
        extent=extent,
        transform=plate,
        interpolation="nearest",
        zorder=2,
    )

    # Add region boundaries in subtle grey
    ax.add_geometries(
        gdf.geometry,
        crs=plate,
        facecolor="none",
        edgecolor="#999999",
        linewidth=0.2,
        zorder=3,
    )

    # Style spines
    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#cccccc")
        else:
            spine.set_visible(False)

    # Add gridlines
    gl = ax.gridlines(
        draw_labels=True,
        crs=plate,
        linewidth=0.35,
        color="#888888",
        alpha=0.45,
        linestyle="--",
    )
    gl.xlocator = mticker.FixedLocator(np.arange(-180, 181, 30))
    gl.ylocator = mticker.FixedLocator(np.arange(-60, 61, 15))
    gl.xformatter = LongitudeFormatter(number_format=".0f")
    gl.yformatter = LatitudeFormatter(number_format=".0f")
    gl.xlabel_style = {"size": 6, "color": "#555555"}
    gl.ylabel_style = {"size": 6, "color": "#555555"}
    gl.top_labels = False
    gl.right_labels = False

    # Force layout computation to get accurate axes position for inset placement
    fig.canvas.draw()
    map_pos = ax.get_position()

    # Build inset stacked bar chart showing land use breakdown by crop group
    # Compute area by group and crop
    group_data = []
    for group_name in group_names:
        crops_in_group = crops_by_group.get(group_name, set())
        if not crops_in_group:
            continue
        # Get areas for crops in this group, sorted by area (largest first)
        crop_areas = []
        for crop in crops_in_group:
            area_ha = area_by_crop.get(crop, 0.0)
            if area_ha > 0:
                crop_areas.append((crop, area_ha))
        if not crop_areas:
            continue
        crop_areas.sort(key=lambda x: -x[1])  # Sort by area descending
        total_area = sum(a for _, a in crop_areas)
        group_data.append((group_name, total_area, crop_areas))

    # Sort groups by total area (largest first = top row)
    group_data.sort(key=lambda x: -x[1])

    if group_data:
        # Determine inset width by converting a target longitude to figure coordinates.
        # The inset right edge should stop before South America.
        target_lon = -100  # degrees West

        # Transform from lon/lat to projection coordinates, then to figure coordinates
        # Use lat=0 (equator) as reference point for the x-coordinate transformation
        proj_coords = ax.projection.transform_point(target_lon, 0, plate)
        display_coords = ax.transData.transform(proj_coords)
        fig_coords = fig.transFigure.inverted().transform(display_coords)

        inset_x = map_pos.x0
        inset_y = map_pos.y0
        inset_width = fig_coords[0] - inset_x
        inset_height = 0.42  # Fixed height as fraction of figure

        # Add white background behind inset to cover map gridline labels
        # Convert 1mm to figure fraction based on actual figure size
        fig_w_inches, fig_h_inches = fig.get_size_inches()
        mm_to_fig_x = 1 / (fig_w_inches * 25.4)
        mm_to_fig_y = 1 / (fig_h_inches * 25.4)
        bg_padding_left = 0.03  # Extra padding to cover latitude labels
        bg_padding_right = 1 * mm_to_fig_x  # 1mm padding
        bg_padding_bottom = 0.06  # Extra padding to cover longitude labels
        bg_padding_top = 1 * mm_to_fig_y  # 1mm padding
        inset_bg_ax = fig.add_axes(
            [
                inset_x - bg_padding_left,
                inset_y - bg_padding_bottom,
                inset_width + bg_padding_left + bg_padding_right,
                inset_height + bg_padding_bottom + bg_padding_top,
            ]
        )
        inset_bg_ax.set_facecolor("#ffffff")
        inset_bg_ax.patch.set_alpha(1.0)
        inset_bg_ax.set_zorder(9)
        inset_bg_ax.set_xticks([])
        inset_bg_ax.set_yticks([])
        for spine in inset_bg_ax.spines.values():
            spine.set_visible(False)

        inset_ax = fig.add_axes(
            [
                inset_x,
                inset_y,
                inset_width,
                inset_height,
            ]
        )
        inset_ax.set_facecolor("#ffffff")
        inset_ax.patch.set_alpha(1.0)
        inset_ax.set_zorder(10)

        n_groups = len(group_data)
        bar_height = 0.5
        row_spacing = 1.0
        y_positions = np.arange(n_groups)[::-1] * row_spacing

        # Find max total area for x-axis scale
        max_area_mha = max(g[1] for g in group_data) / 1e6

        # Minimum segment width for labeling (as fraction of max area)
        min_label_frac = 0.025
        min_labels_per_group = 3

        # Setup for text width measurement
        fontsize = 5
        font_props = FontProperties(size=fontsize, family="monospace")
        x_margin_factor = 1.22
        x_range = max_area_mha * x_margin_factor

        # Calculate conversion factor from points to data coordinates
        fig_width_points = fig_w_inches * 72
        inset_width_points = fig_width_points * inset_width
        points_to_data = x_range / inset_width_points

        def get_text_width_data(text: str) -> float:
            """Get text width in data coordinates using TextPath."""
            tp = TextPath((0, 0), text, prop=font_props)
            bbox = tp.get_extents()
            return bbox.width * points_to_data

        for i, (group_name, _total_area, crop_areas) in enumerate(group_data):
            y = y_positions[i]
            base_color = crop_group_colors[group_name]
            if isinstance(base_color, str):
                base_color = mcolors.to_rgb(base_color)

            left = 0.0
            for _crop, area_ha in crop_areas:
                area_mha = area_ha / 1e6

                inset_ax.barh(
                    y,
                    area_mha,
                    height=bar_height,
                    left=left,
                    color=base_color,
                    edgecolor="white",
                    linewidth=1.0,
                )

                left += area_mha

            # Add horizontal labels with smart overlap handling
            label_y = y + bar_height / 2 + 0.08
            label_x_nudge = points_to_data * 2.0
            last_label_right_x = 0.0

            n_crops_in_group = len(crop_areas)
            guaranteed_labels = min(n_crops_in_group, min_labels_per_group)

            left = 0.0
            for j, (crop, area_ha) in enumerate(crop_areas):
                area_mha = area_ha / 1e6
                seg_frac = area_mha / max_area_mha

                if seg_frac >= min_label_frac or j < guaranteed_labels:
                    desired_x = left + label_x_nudge

                    if desired_x < last_label_right_x:
                        label_x = last_label_right_x
                        label_text = ", " + crop
                    else:
                        label_x = desired_x
                        label_text = crop

                    inset_ax.text(
                        label_x,
                        label_y,
                        label_text,
                        ha="left",
                        va="bottom",
                        fontsize=fontsize,
                        fontfamily="monospace",
                        color="#333333",
                    )

                    label_width = get_text_width_data(label_text)
                    padding = points_to_data * 1.0
                    last_label_right_x = label_x + label_width + padding

                left += area_mha

        # Style the inset axes
        inset_ax.set_yticks(y_positions)
        inset_ax.set_yticklabels([g[0] for g in group_data], fontsize=6)
        inset_ax.set_xlabel("Land use (Mha)", fontsize=6)
        inset_ax.tick_params(axis="x", labelsize=5)
        inset_ax.tick_params(axis="y", length=0)

        inset_ax.set_xlim(0, x_range)
        y_max = y_positions[0] + bar_height / 2 + 0.9
        y_min = y_positions[-1] - bar_height / 2 - 0.3
        inset_ax.set_ylim(y_min, y_max)

        inset_ax.xaxis.grid(True, linestyle="-", alpha=0.3, linewidth=0.5)
        inset_ax.set_axisbelow(True)

        for spine in inset_ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_color("#cccccc")

    # Add intensity colorbar
    cmap_colors = np.zeros((256, 4))
    cmap_colors[:, 0] = 0.4  # R (gray)
    cmap_colors[:, 1] = 0.4  # G (gray)
    cmap_colors[:, 2] = 0.4  # B (gray)
    cmap_colors[:, 3] = np.linspace(0, 1, 256)  # Alpha gradient
    intensity_cmap = mcolors.ListedColormap(cmap_colors)

    sm = plt.cm.ScalarMappable(
        cmap=intensity_cmap, norm=mcolors.Normalize(vmin=0, vmax=100)
    )
    sm.set_array([])

    # Add colorbar with white background, positioned at bottom-center of map
    fig_w_inches, fig_h_inches = fig.get_size_inches()
    mm_to_fig_x = 1 / (fig_w_inches * 25.4)
    mm_to_fig_y = 1 / (fig_h_inches * 25.4)
    cbar_padding_x = 1 * mm_to_fig_x  # 1mm padding around bordered box
    cbar_padding_y = 1 * mm_to_fig_y
    cbar_box_width = 0.26
    cbar_box_height = 0.08
    cbar_box_x = map_pos.x0 + (map_pos.width - cbar_box_width) / 2 + 0.10
    cbar_box_y = map_pos.y0

    # White background layer (behind bordered box, to cover map labels)
    cbar_bg_ax = fig.add_axes(
        [
            cbar_box_x - cbar_padding_x,
            cbar_box_y - cbar_padding_y,
            cbar_box_width + 2 * cbar_padding_x,
            cbar_box_height + 2 * cbar_padding_y,
        ]
    )
    cbar_bg_ax.set_facecolor("#ffffff")
    cbar_bg_ax.patch.set_alpha(1.0)
    cbar_bg_ax.set_zorder(8)
    cbar_bg_ax.set_xticks([])
    cbar_bg_ax.set_yticks([])
    for spine in cbar_bg_ax.spines.values():
        spine.set_visible(False)

    # Bordered box layer
    cbar_border_ax = fig.add_axes(
        [cbar_box_x, cbar_box_y, cbar_box_width, cbar_box_height]
    )
    cbar_border_ax.set_facecolor("#ffffff")
    cbar_border_ax.patch.set_alpha(1.0)
    cbar_border_ax.set_zorder(9)
    cbar_border_ax.set_xticks([])
    cbar_border_ax.set_yticks([])
    for spine in cbar_border_ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.5)
        spine.set_color("#cccccc")

    cbar_width = 0.18
    cbar_height = 0.018
    cbar_x = cbar_box_x + (cbar_box_width - cbar_width) / 2
    cbar_y = cbar_box_y + 0.05
    cbar_ax = fig.add_axes([cbar_x, cbar_y, cbar_width, cbar_height])
    cbar_ax.set_zorder(10)
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_ticks([0, 50, 100])
    cbar.set_ticklabels(["0%", "50%", "100%"])
    cbar.ax.tick_params(labelsize=6, length=2, color="#cccccc")
    cbar.set_label(
        "Utilization of potential cropland and grassland (excl. protected/unsuitable)",
        fontsize=6,
    )
    cbar.outline.set_linewidth(0.5)
    cbar.outline.set_edgecolor("#cccccc")

    # Add annotation for unmodeled regions at bottom right of map area
    fig.text(
        map_pos.x1,
        map_pos.y0,
        "Gray hatched areas not modeled",
        ha="right",
        va="bottom",
        fontsize=6,
        color="#666666",
        style="italic",
    )

    ax.set_title(title, fontsize=8)
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)

    logger.info("Saved gridcell intensity map to %s", out)


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    resource_classes_path: str = snakemake.input.resource_classes  # type: ignore[name-defined]
    land_area_by_class_path: str = snakemake.input.land_area_by_class  # type: ignore[name-defined]
    land_grazing_only_path: str = snakemake.input.land_grazing_only  # type: ignore[name-defined]
    land_use_path: str = snakemake.input.land_use  # type: ignore[name-defined]
    output_pdf: str = snakemake.output.pdf  # type: ignore[name-defined]

    crop_to_group, crop_group_colors = crop_groups_from_config(
        snakemake.config  # type: ignore[name-defined]
    )

    gdf = _setup_regions(regions_path)
    region_name_to_id = {region: idx for idx, region in enumerate(gdf["region"])}

    rc_data = _load_resource_classes(resource_classes_path)
    potential_area = _load_potential_area(
        land_area_by_class_path, land_grazing_only_path
    )
    land_use_by_rc_crop = _load_land_use_by_region_class_crop(land_use_path)

    if not land_use_by_rc_crop.empty:
        dominant_group_grid, intensity_grid, crops_by_group, area_by_crop = (
            _build_dominant_group_and_intensity_grids(
                land_use_by_rc_crop,
                rc_data["class_grid"],
                rc_data["region_grid"],
                potential_area,
                region_name_to_id,
                crop_to_group,
                crop_group_colors,
                excluded_map_groups=EXCLUDED_MAP_GROUPS,
            )
        )
        _plot_gridcell_intensity(
            dominant_group_grid,
            intensity_grid,
            rc_data["extent"],
            gdf,
            crops_by_group,
            area_by_crop,
            crop_to_group,
            crop_group_colors,
            output_pdf,
            title="Dominant Crop and Land Use Intensity",
        )
    else:
        logger.warning("No land use data by resource class; skipping gridcell plot")


if __name__ == "__main__":
    main()
