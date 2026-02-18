#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate validation crop production map (excluding pasture).

Adapted from workflow/scripts/plotting/plot_crop_production_map.py with
doc figure styling. Pasture/grassland is excluded so that it does not
dominate the visualization; a separate pasture map is provided.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.plot_crop_production_map import (
    CROP_GROUP_COLORS,
    CROP_TO_GROUP,
    _load_land_use_by_region_class_crop,
    _load_potential_area,
    _load_resource_classes,
    _setup_regions,
)

logger = logging.getLogger(__name__)

# Crop groups to exclude from the map (plotted separately)
EXCLUDED_GROUPS = {"Feed crops"}


def _build_grids_excluding_pasture(
    land_use_df: pd.DataFrame,
    class_grid: np.ndarray,
    region_grid: np.ndarray,
    potential_area: pd.Series,
    region_name_to_id: dict[str, int],
) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """Build pixel-level grids excluding pasture/feed crops.

    Returns:
        dominant_group_grid, intensity_grid, area_by_crop
    """
    # Filter out excluded groups
    filtered = land_use_df[
        land_use_df["crop"]
        .map(lambda c: CROP_TO_GROUP.get(c, "Other"))
        .isin(EXCLUDED_GROUPS)
        .__invert__()
    ].copy()

    intensity_grid = np.full(class_grid.shape, np.nan, dtype=np.float32)
    dominant_group_grid = np.full(class_grid.shape, -1, dtype=np.int8)

    group_names = [g for g in CROP_GROUP_COLORS if g not in EXCLUDED_GROUPS]
    group_to_idx = {name: idx for idx, name in enumerate(group_names)}

    grouped = filtered.groupby(["region", "resource_class"])

    for (region, rc), group_df in grouped:
        total_used_ha = group_df["used_ha"].sum()
        if total_used_ha <= 0:
            continue

        crop_areas = group_df.groupby("crop")["used_ha"].sum()
        dominant_crop = crop_areas.idxmax()
        dominant_group = CROP_TO_GROUP.get(dominant_crop, "Other")

        potential_ha = potential_area.get((region, int(rc)), 0.0)
        intensity = min(total_used_ha / potential_ha, 1.0) if potential_ha > 0 else 0.0

        region_id = region_name_to_id.get(region)
        if region_id is not None and dominant_group in group_to_idx:
            mask = (region_grid == region_id) & (class_grid == int(rc))
            intensity_grid[mask] = intensity
            dominant_group_grid[mask] = group_to_idx[dominant_group]

    area_by_crop = filtered.groupby("crop")["used_ha"].sum()

    return dominant_group_grid, intensity_grid, area_by_crop


def _plot(
    dominant_group_grid: np.ndarray,
    intensity_grid: np.ndarray,
    extent: tuple,
    gdf: gpd.GeoDataFrame,
    area_by_crop: pd.Series,
    output_svg: str,
    output_png: str,
) -> None:
    apply_doc_style()

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#ffffff")
    ax.set_global()
    plate = ccrs.PlateCarree()

    group_names = [g for g in CROP_GROUP_COLORS if g not in EXCLUDED_GROUPS]
    height, width = dominant_group_grid.shape
    rgba = np.ones((height, width, 4), dtype=np.float32)

    for idx, group_name in enumerate(group_names):
        color = CROP_GROUP_COLORS[group_name]
        if isinstance(color, str):
            color = mcolors.to_rgb(color)
        mask = dominant_group_grid == idx
        if not np.any(mask):
            continue
        intensities = intensity_grid[mask]
        rgba[mask, 0] = color[0]
        rgba[mask, 1] = color[1]
        rgba[mask, 2] = color[2]
        rgba[mask, 3] = np.clip(intensities, 0.05, 1.0)

    no_data_mask = dominant_group_grid < 0
    rgba[no_data_mask, 3] = 0.0

    # Unmodeled land
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0", edgecolor="none", zorder=0)
    ax.add_feature(
        cfeature.LAND,
        facecolor="none",
        edgecolor="#ffffff",
        hatch="//////",
        linewidth=0.3,
        zorder=0.5,
    )
    ax.add_geometries(
        gdf.geometry, crs=plate, facecolor="#ffffff", edgecolor="none", zorder=1
    )

    ax.imshow(
        rgba,
        origin="upper",
        extent=extent,
        transform=plate,
        interpolation="nearest",
        zorder=2,
    )

    ax.add_geometries(
        gdf.geometry,
        crs=plate,
        facecolor="none",
        edgecolor="#999999",
        linewidth=0.2,
        zorder=3,
    )

    for spine_name, spine in ax.spines.items():
        if spine_name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#cccccc")
        else:
            spine.set_visible(False)

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
    gl.xlabel_style = {"size": FONT_SIZES["annotation"], "color": "#555555"}
    gl.ylabel_style = {"size": FONT_SIZES["annotation"], "color": "#555555"}
    gl.top_labels = False
    gl.bottom_labels = False
    gl.right_labels = False

    # Force layout for inset positioning
    fig.canvas.draw()
    map_pos = ax.get_position()

    # Inset bar chart by crop group
    group_areas = {}
    for crop, area_ha in area_by_crop.items():
        group = CROP_TO_GROUP.get(crop, "Other")
        if group in CROP_GROUP_COLORS and group not in EXCLUDED_GROUPS:
            group_areas[group] = group_areas.get(group, 0.0) + area_ha

    group_data = sorted(
        [(g, a) for g, a in group_areas.items() if a > 0], key=lambda x: -x[1]
    )

    if group_data:
        target_lon = -100
        proj_coords = ax.projection.transform_point(target_lon, 0, plate)
        display_coords = ax.transData.transform(proj_coords)
        fig_coords = fig.transFigure.inverted().transform(display_coords)

        inset_x = map_pos.x0
        inset_y = map_pos.y0
        inset_width = fig_coords[0] - inset_x
        inset_height = 0.42

        fig_w_inches, fig_h_inches = fig.get_size_inches()
        mm_to_fig_x = 1 / (fig_w_inches * 25.4)
        mm_to_fig_y = 1 / (fig_h_inches * 25.4)

        inset_bg_ax = fig.add_axes(
            [
                inset_x - 0.03,
                inset_y - 0.06,
                inset_width + 0.03 + mm_to_fig_x,
                inset_height + 0.06 + mm_to_fig_y,
            ]
        )
        inset_bg_ax.set_facecolor("#ffffff")
        inset_bg_ax.patch.set_alpha(1.0)
        inset_bg_ax.set_zorder(9)
        inset_bg_ax.set_xticks([])
        inset_bg_ax.set_yticks([])
        for spine in inset_bg_ax.spines.values():
            spine.set_visible(False)

        inset_ax = fig.add_axes([inset_x, inset_y, inset_width, inset_height])
        inset_ax.set_facecolor("#ffffff")
        inset_ax.patch.set_alpha(1.0)
        inset_ax.set_zorder(10)

        n_groups = len(group_data)
        bar_height = 0.5
        row_spacing = 1.0
        y_positions = np.arange(n_groups)[::-1] * row_spacing
        max_area_mha = max(a for _, a in group_data) / 1e6

        for i, (group_name, total_area) in enumerate(group_data):
            y = y_positions[i]
            color = CROP_GROUP_COLORS[group_name]
            if isinstance(color, str):
                color = mcolors.to_rgb(color)
            inset_ax.barh(
                y,
                total_area / 1e6,
                height=bar_height,
                color=color,
                edgecolor="white",
                linewidth=1.0,
            )

        inset_ax.set_yticks(y_positions)
        inset_ax.set_yticklabels(
            [g[0] for g in group_data], fontsize=FONT_SIZES["tick"]
        )
        inset_ax.set_xlabel("Land use (Mha)", fontsize=FONT_SIZES["label"])
        inset_ax.tick_params(axis="x", labelsize=FONT_SIZES["tick"])
        inset_ax.tick_params(axis="y", length=0)
        inset_ax.set_xlim(0, max_area_mha * 1.22)
        y_max = y_positions[0] + bar_height / 2 + 0.9
        y_min = y_positions[-1] - bar_height / 2 - 0.3
        inset_ax.set_ylim(y_min, y_max)
        inset_ax.xaxis.grid(True, linestyle="-", alpha=0.3, linewidth=0.5)
        inset_ax.set_axisbelow(True)
        for spine in inset_ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_color("#cccccc")

    # Intensity colorbar
    cmap_colors = np.zeros((256, 4))
    cmap_colors[:, 0] = 0.4
    cmap_colors[:, 1] = 0.4
    cmap_colors[:, 2] = 0.4
    cmap_colors[:, 3] = np.linspace(0, 1, 256)
    intensity_cmap = mcolors.ListedColormap(cmap_colors)

    sm = plt.cm.ScalarMappable(
        cmap=intensity_cmap, norm=mcolors.Normalize(vmin=0, vmax=100)
    )
    sm.set_array([])

    fig_w_inches, fig_h_inches = fig.get_size_inches()
    mm_to_fig_x = 1 / (fig_w_inches * 25.4)
    mm_to_fig_y = 1 / (fig_h_inches * 25.4)
    cbar_box_width = 0.26
    cbar_box_height = 0.08
    cbar_box_x = map_pos.x0 + (map_pos.width - cbar_box_width) / 2
    cbar_box_y = map_pos.y0

    cbar_bg_ax = fig.add_axes(
        [
            cbar_box_x - mm_to_fig_x,
            cbar_box_y - mm_to_fig_y,
            cbar_box_width + 2 * mm_to_fig_x,
            cbar_box_height + 2 * mm_to_fig_y,
        ]
    )
    cbar_bg_ax.set_facecolor("#ffffff")
    cbar_bg_ax.patch.set_alpha(1.0)
    cbar_bg_ax.set_zorder(8)
    cbar_bg_ax.set_xticks([])
    cbar_bg_ax.set_yticks([])
    for spine in cbar_bg_ax.spines.values():
        spine.set_visible(False)

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
    cbar.ax.tick_params(
        labelsize=FONT_SIZES["colorbar_tick"], length=2, color="#cccccc"
    )
    cbar.set_label(
        "Cropland utilization",
        fontsize=FONT_SIZES["colorbar_tick"],
    )
    cbar.outline.set_linewidth(0.5)
    cbar.outline.set_edgecolor("#cccccc")

    fig.text(
        map_pos.x1,
        map_pos.y0,
        "Gray hatched areas not modeled",
        ha="right",
        va="bottom",
        fontsize=FONT_SIZES["annotation"],
        color="#666666",
        style="italic",
    )

    save_doc_figure(fig, output_svg, format="svg")
    save_doc_figure(fig, output_png, format="png", dpi=300)
    plt.close(fig)
    logger.info("Saved validation crop production map")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    gdf = _setup_regions(snakemake.input.regions)  # type: ignore[name-defined]
    region_name_to_id = {region: idx for idx, region in enumerate(gdf["region"])}

    rc_data = _load_resource_classes(snakemake.input.resource_classes)  # type: ignore[name-defined]
    potential_area = _load_potential_area(
        snakemake.input.land_area_by_class,  # type: ignore[name-defined]
        snakemake.input.land_grazing_only,  # type: ignore[name-defined]
    )
    land_use = _load_land_use_by_region_class_crop(snakemake.input.land_use)  # type: ignore[name-defined]

    if land_use.empty:
        logger.warning("No land use data; skipping validation crop production map")
        return

    dominant_group_grid, intensity_grid, area_by_crop = _build_grids_excluding_pasture(
        land_use,
        rc_data["class_grid"],
        rc_data["region_grid"],
        potential_area,
        region_name_to_id,
    )

    _plot(
        dominant_group_grid,
        intensity_grid,
        rc_data["extent"],
        gdf,
        area_by_crop,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
