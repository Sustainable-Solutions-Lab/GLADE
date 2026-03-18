#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a single PNG frame showing dominant crop production patterns.

Adapted from workflow/scripts/plotting/plot_crop_production_map.py with a
simplified bar chart (one solid bar per crop group, no individual crop labels)
and a subtitle indicating the trade-friction scenario.
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
    EXCLUDED_MAP_GROUPS,
    _build_dominant_group_and_intensity_grids,
    _load_land_use_by_region_class_crop,
    _load_potential_area,
    _load_resource_classes,
    _setup_regions,
    crop_groups_from_config,
)

logger = logging.getLogger(__name__)


def _overlay_livestock(
    ax,
    gdf: gpd.GeoDataFrame,
    animal_df: pd.DataFrame,
    plate,
) -> None:
    """Overlay livestock production dots sized by output and colored by FCR.

    Args:
        ax: Cartopy GeoAxes to draw on.
        gdf: GeoDataFrame with region geometries (must have 'country' column).
        animal_df: DataFrame with columns: country, total_product, fcr.
        plate: PlateCarree CRS for coordinate transforms.
    """
    from matplotlib.colors import Normalize

    # Country centroids (dissolve regions to countries, then centroid)
    countries = gdf.dissolve(by="country", as_index=False)
    countries_proj = countries.to_crs("+proj=cea")
    centroids_proj = countries_proj.geometry.centroid
    centroids_ll = centroids_proj.to_crs(4326)
    countries["lon"] = centroids_ll.x
    countries["lat"] = centroids_ll.y

    merged = countries.merge(animal_df, on="country", how="inner")
    merged = merged[merged["total_product"] > 0.5]  # Skip tiny producers

    # Size scaling: scatter 's' is marker area (points²), so s ∝ value
    # gives visually correct area-proportional circles.
    # Use fixed reference so legend is stable across GIF frames.
    max_size = 150.0  # points² for the reference value
    max_val = 15.0  # Mt protein reference (legend top)
    sizes = merged["total_protein"].values / max_val * max_size

    # FCR colormap: low FCR (efficient) = green, high FCR = red/orange
    fcr_vals = merged["fcr"].values
    fcr_norm = Normalize(vmin=2, vmax=12, clip=True)
    cmap = plt.colormaps["RdYlGn_r"]

    ax.scatter(
        merged["lon"].values,
        merged["lat"].values,
        s=sizes,
        c=fcr_vals,
        cmap=cmap,
        norm=fcr_norm,
        transform=plate,
        edgecolors="white",
        linewidths=0.4,
        alpha=0.8,
        zorder=4,
    )

    # --- Compact graduated-circle legend in the Pacific ---
    legend_vals = [1, 5, 15]  # Mt protein
    legend_sizes = [v / max_val * max_size for v in legend_vals]

    leg_x = 0.92
    leg_y_base = 0.52
    leg_dy = 0.055

    for i, (val, sz) in enumerate(zip(legend_vals, legend_sizes)):
        y = leg_y_base + i * leg_dy
        ax.scatter(
            leg_x,
            y,
            s=sz,
            c="#aaaaaa",
            edgecolors="white",
            linewidths=0.4,
            transform=ax.transAxes,
            zorder=5,
            alpha=0.8,
        )
        ax.text(
            leg_x + 0.035,
            y,
            f"{val}",
            transform=ax.transAxes,
            fontsize=FONT_SIZES["annotation"] - 1,
            va="center",
            ha="left",
            color="#555555",
            zorder=5,
        )

    # Legend title
    ax.text(
        leg_x + 0.01,
        leg_y_base + len(legend_vals) * leg_dy + 0.01,
        "Protein (Mt)",
        transform=ax.transAxes,
        fontsize=FONT_SIZES["annotation"] - 1,
        va="bottom",
        ha="center",
        color="#555555",
        fontweight="bold",
        zorder=5,
    )


def _plot_frame(
    dominant_group_grid: np.ndarray,
    intensity_grid: np.ndarray,
    extent: tuple,
    gdf: gpd.GeoDataFrame,
    area_by_crop: pd.Series,
    crop_to_group: dict[str, str],
    crop_group_colors: dict[str, str],
    output_path: str,
    frame_label: str,
    bar_xmax_mha: float,
    animal_by_country: pd.DataFrame | None = None,
) -> None:
    """Plot a single production-pattern frame (map + simplified bar chart).

    Args:
        dominant_group_grid: 2D array of group indices (-1 for no data).
        intensity_grid: 2D array of intensity values (0-1).
        extent: (lon_min, lon_max, lat_min, lat_max).
        gdf: GeoDataFrame with region boundaries.
        area_by_crop: Series with total area (ha) per crop.
        output_path: Path for the output PNG file.
        frame_label: Subtitle text describing this scenario.
        bar_xmax_mha: Fixed x-axis maximum for the bar chart (Mha).
    """
    apply_doc_style()

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#ffffff")
    ax.set_global()
    plate = ccrs.PlateCarree()

    # Build RGBA image from dominant group and intensity
    group_names = list(crop_group_colors.keys())
    height, width = dominant_group_grid.shape
    rgba = np.ones((height, width, 4), dtype=np.float32)

    for idx, group_name in enumerate(group_names):
        color = crop_group_colors[group_name]
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

    # No-data pixels fully transparent
    no_data_mask = dominant_group_grid < 0
    rgba[no_data_mask, 3] = 0.0

    # Unmodeled land with light gray + white hatching
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0", edgecolor="none", zorder=0)
    ax.add_feature(
        cfeature.LAND,
        facecolor="none",
        edgecolor="#ffffff",
        hatch="//////",
        linewidth=0.3,
        zorder=0.5,
    )

    # Modeled regions white fill
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

    # Region boundaries
    ax.add_geometries(
        gdf.geometry,
        crs=plate,
        facecolor="none",
        edgecolor="#999999",
        linewidth=0.2,
        zorder=3,
    )

    # Style spines
    for spine_name, spine in ax.spines.items():
        if spine_name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#cccccc")
        else:
            spine.set_visible(False)

    # Gridlines
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
    gl.right_labels = False

    # Force layout so we can position the inset
    fig.canvas.draw()
    map_pos = ax.get_position()

    # --- Simplified bar chart (one solid bar per crop group) ---
    # Aggregate area by crop group
    group_areas = {}
    for crop, area_ha in area_by_crop.items():
        group = crop_to_group.get(crop, "Other")
        if group in crop_group_colors and group not in EXCLUDED_MAP_GROUPS:
            group_areas[group] = group_areas.get(group, 0.0) + area_ha

    # Sort by area descending
    group_data = sorted(group_areas.items(), key=lambda x: -x[1])
    # Only include groups with positive area
    group_data = [(g, a) for g, a in group_data if a > 0]

    if group_data:
        # Position inset to the left (before South America)
        target_lon = -100
        proj_coords = ax.projection.transform_point(target_lon, 0, plate)
        display_coords = ax.transData.transform(proj_coords)
        fig_coords = fig.transFigure.inverted().transform(display_coords)

        inset_x = map_pos.x0
        inset_y = map_pos.y0
        inset_width = fig_coords[0] - inset_x
        inset_height = 0.42

        # White background behind inset
        fig_w_inches, fig_h_inches = fig.get_size_inches()
        mm_to_fig_x = 1 / (fig_w_inches * 25.4)
        mm_to_fig_y = 1 / (fig_h_inches * 25.4)
        bg_padding_left = 0.03
        bg_padding_right = 1 * mm_to_fig_x
        bg_padding_bottom = 0.06
        bg_padding_top = 1 * mm_to_fig_y
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

        inset_ax = fig.add_axes([inset_x, inset_y, inset_width, inset_height])
        inset_ax.set_facecolor("#ffffff")
        inset_ax.patch.set_alpha(1.0)
        inset_ax.set_zorder(10)

        n_groups = len(group_data)
        bar_height = 0.5
        row_spacing = 1.0
        y_positions = np.arange(n_groups)[::-1] * row_spacing

        for i, (group_name, total_area) in enumerate(group_data):
            y = y_positions[i]
            color = crop_group_colors[group_name]
            if isinstance(color, str):
                color = mcolors.to_rgb(color)
            area_mha = total_area / 1e6
            inset_ax.barh(
                y,
                area_mha,
                height=bar_height,
                color=color,
                edgecolor="white",
                linewidth=1.0,
            )

        # Style inset
        inset_ax.set_yticks(y_positions)
        inset_ax.set_yticklabels(
            [g[0] for g in group_data], fontsize=FONT_SIZES["tick"]
        )
        inset_ax.set_xlabel("Land use (Mha)", fontsize=FONT_SIZES["label"])
        inset_ax.tick_params(axis="x", labelsize=FONT_SIZES["tick"])
        inset_ax.tick_params(axis="y", length=0)

        x_margin_factor = 1.22
        inset_ax.set_xlim(0, bar_xmax_mha * x_margin_factor)
        y_max = y_positions[0] + bar_height / 2 + 0.9
        y_min = y_positions[-1] - bar_height / 2 - 0.3
        inset_ax.set_ylim(y_min, y_max)

        inset_ax.xaxis.grid(True, linestyle="-", alpha=0.3, linewidth=0.5)
        inset_ax.set_axisbelow(True)

        for spine in inset_ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_color("#cccccc")

    # --- Livestock production overlay ---
    if animal_by_country is not None and not animal_by_country.empty:
        _overlay_livestock(ax, gdf, animal_by_country, plate)

    # Unmodeled-regions annotation
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

    # Scenario annotation placed in the Southern Ocean
    ax.text(
        0,
        -70,
        frame_label,
        transform=plate,
        ha="center",
        va="center",
        fontsize=FONT_SIZES["title"],
        fontweight="bold",
        color="#444444",
        zorder=5,
    )

    save_doc_figure(fig, output_path, format="png", dpi=300)
    plt.close(fig)
    logger.info("Saved production pattern frame to %s", output_path)


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    resource_classes_path: str = snakemake.input.resource_classes  # type: ignore[name-defined]
    land_area_by_class_path: str = snakemake.input.land_area_by_class  # type: ignore[name-defined]
    land_grazing_only_path: str = snakemake.input.land_grazing_only  # type: ignore[name-defined]
    land_use_path: str = snakemake.input.land_use  # type: ignore[name-defined]
    network_path: str = snakemake.input.network  # type: ignore[name-defined]
    output_png: str = snakemake.output.png  # type: ignore[name-defined]
    frame_label: str = snakemake.params.frame_label  # type: ignore[name-defined]
    bar_xmax_mha: float = snakemake.params.bar_xmax_mha  # type: ignore[name-defined]

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

    # Load animal production data from solved network
    import pypsa

    n = pypsa.Network(str(network_path))
    animal_links = n.links.static[n.links.static["carrier"] == "animal_production"]
    p0 = n.links.dynamic["p0"]
    if not animal_links.empty:
        # Load protein fractions (g protein / 100g product → fraction)
        nutrition = pd.read_csv("data/curated/nutrition.csv")
        protein_frac = (
            nutrition[nutrition["nutrient"] == "protein"]
            .set_index("food")["value"]
            .astype(float)
            / 100.0
        )

        feed_mt = p0.iloc[0][animal_links.index].values
        product_mt = feed_mt * animal_links["efficiency"].astype(float).values
        products = animal_links["product"].values
        protein_mt = product_mt * np.array([protein_frac.get(p, 0.0) for p in products])
        adf = pd.DataFrame(
            {
                "country": animal_links["country"].values,
                "feed_mt": feed_mt,
                "product_mt": product_mt,
                "protein_mt": protein_mt,
            }
        )
        animal_by_country = adf.groupby("country").agg(
            total_product=("product_mt", "sum"),
            total_protein=("protein_mt", "sum"),
            total_feed=("feed_mt", "sum"),
        )
        animal_by_country["fcr"] = animal_by_country["total_feed"] / animal_by_country[
            "total_product"
        ].where(animal_by_country["total_product"] > 0, 1)
        animal_by_country = animal_by_country.reset_index()
    else:
        animal_by_country = None

    if not land_use_by_rc_crop.empty:
        dominant_group_grid, intensity_grid, _crops_by_group, area_by_crop = (
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
        _plot_frame(
            dominant_group_grid,
            intensity_grid,
            rc_data["extent"],
            gdf,
            area_by_crop,
            crop_to_group,
            crop_group_colors,
            output_png,
            frame_label=frame_label,
            bar_xmax_mha=bar_xmax_mha,
            animal_by_country=animal_by_country,
        )
    else:
        logger.warning("No land use data; skipping frame generation")


if __name__ == "__main__":
    main()
