#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate validation pasture/grassland intensity map.

Shows grassland utilization intensity across modeled regions, plotted
separately from the crop production map to avoid pasture dominating
the visualization.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.plot_crop_production_map import (
    _load_land_use_by_region_class_crop,
    _load_potential_area,
    _load_resource_classes,
    _setup_regions,
    crop_groups_from_config,
)

logger = logging.getLogger(__name__)


def _build_pasture_intensity_grid(
    land_use_df,
    class_grid,
    region_grid,
    potential_area,
    region_name_to_id,
    pasture_crops: set[str],
):
    """Build pixel-level pasture intensity grid.

    Returns intensity_grid (2D, 0-1) and total pasture area (ha).
    """
    pasture = land_use_df[land_use_df["crop"].isin(pasture_crops)].copy()
    intensity_grid = np.full(class_grid.shape, np.nan, dtype=np.float32)
    has_data = np.zeros(class_grid.shape, dtype=bool)
    total_area_ha = 0.0

    grouped = pasture.groupby(["region", "resource_class"])
    for (region, rc), group_df in grouped:
        used_ha = group_df["used_ha"].sum()
        if used_ha <= 0:
            continue
        total_area_ha += used_ha

        potential_ha = potential_area.get((region, int(rc)), 0.0)
        intensity = min(used_ha / potential_ha, 1.0) if potential_ha > 0 else 0.0

        region_id = region_name_to_id.get(region)
        if region_id is not None:
            mask = (region_grid == region_id) & (class_grid == int(rc))
            intensity_grid[mask] = intensity
            has_data[mask] = True

    return intensity_grid, has_data, total_area_ha


def _plot(
    intensity_grid,
    has_data,
    extent,
    gdf,
    total_area_ha,
    output_svg,
    output_png,
):
    apply_doc_style()

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#ffffff")
    ax.set_global()
    plate = ccrs.PlateCarree()

    # Build RGBA image — green tones for pasture
    height, width = intensity_grid.shape
    base_color = mcolors.to_rgb("#4f9d69")  # Same as "Grass & leaves" color
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[has_data, 0] = base_color[0]
    rgba[has_data, 1] = base_color[1]
    rgba[has_data, 2] = base_color[2]
    rgba[has_data, 3] = np.clip(intensity_grid[has_data], 0.05, 1.0)
    rgba[~has_data, 3] = 0.0

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

    fig.canvas.draw()
    map_pos = ax.get_position()

    # Intensity colorbar
    cmap_colors = np.zeros((256, 4))
    cmap_colors[:, 0] = base_color[0]
    cmap_colors[:, 1] = base_color[1]
    cmap_colors[:, 2] = base_color[2]
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
        "Pasture utilization intensity",
        fontsize=FONT_SIZES["colorbar_tick"],
    )
    cbar.outline.set_linewidth(0.5)
    cbar.outline.set_edgecolor("#cccccc")

    # Total area annotation
    total_mha = total_area_ha / 1e6
    ax.text(
        0,
        -70,
        f"Total pasture area: {total_mha:.0f} Mha",
        transform=plate,
        ha="center",
        va="center",
        fontsize=FONT_SIZES["label"],
        color="#444444",
        zorder=5,
    )

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
    logger.info("Saved validation pasture map")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    crop_to_group, _crop_group_colors = crop_groups_from_config(
        snakemake.config  # type: ignore[name-defined]
    )
    pasture_crops = {c for c, g in crop_to_group.items() if g == "Feed crops"}

    gdf = _setup_regions(snakemake.input.regions)  # type: ignore[name-defined]
    region_name_to_id = {region: idx for idx, region in enumerate(gdf["region"])}

    rc_data = _load_resource_classes(snakemake.input.resource_classes)  # type: ignore[name-defined]
    potential_area = _load_potential_area(
        snakemake.input.land_area_by_class,  # type: ignore[name-defined]
        snakemake.input.land_grazing_only,  # type: ignore[name-defined]
    )
    land_use = _load_land_use_by_region_class_crop(snakemake.input.land_use)  # type: ignore[name-defined]

    if land_use.empty:
        logger.warning("No land use data; skipping pasture map")
        return

    intensity_grid, has_data, total_area_ha = _build_pasture_intensity_grid(
        land_use,
        rc_data["class_grid"],
        rc_data["region_grid"],
        potential_area,
        region_name_to_id,
        pasture_crops,
    )

    _plot(
        intensity_grid,
        has_data,
        rc_data["extent"],
        gdf,
        total_area_ha,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
