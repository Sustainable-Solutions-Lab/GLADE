#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot crop production map with dominant hub-to-hub trade flows overlaid.

Combines the gridcell-level crop production intensity map with arrows
showing the largest trade flows between hubs. Arrows are coloured by
commodity group (same scheme as crops, plus extra categories for
processed-food and feed trade) and sized by volume.
"""

import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib

matplotlib.use("pdf")
import matplotlib.colors as mcolors
from matplotlib.patches import Polygon
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from pyproj import Transformer
import pypsa
from sklearn.cluster import KMeans

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.plot_crop_production_map import (
    _build_dominant_group_and_intensity_grids,
    _load_land_use_by_region_class_crop,
    _load_potential_area,
    _load_resource_classes,
    _setup_regions,
    crop_groups_from_config,
)
from workflow.scripts.snakemake_utils import load_solved_network

logger = logging.getLogger(__name__)

# Number of largest hub-to-hub flows to display (configurable at top of script).
N_TOP_FLOWS = 15

# Arrow line-width range (points).
_LW_MIN, _LW_MAX = 1.0, 5.5

# Bend offset (km) applied to the right of each arrow so that opposing
# flows between the same hub pair do not overlap.
_BEND_KM = 400

# Trade-flow arrow colour overrides.  Crop groups that need more saturated
# or distinct colours for thin arrows get explicit hex values here;
# everything else falls through to the config crop_group_colors.
_TRADE_COLOR_OVERRIDES = {
    "Cereals": "#d4a017",  # saturated gold
    "Roots & tubers": "#c44e52",  # muted red - distinct from gold
}

# Non-crop trade categories (food groups, feed) with fixed colours.
_NON_CROP_TRADE_COLORS = {
    "Grain": "#C49C94",
    "Whole grains": "#8C564B",
    "Dairy": "#9EDAE5",
    "Red meat": "#D62728",
    "Poultry": "#FF9896",
    "Eggs": "#FFE377",
    "Starchy vegetables": "#F28E2C",
    "Oil": "#FFBE7D",
    "Sugar": "#E377C2",
    "Animal feed": "#8c6d31",
}


def _build_trade_category_colors(crop_group_colors: dict[str, str]) -> dict[str, str]:
    """Build trade category colour mapping from crop group colours + overrides."""
    colors = {}
    for group, color in crop_group_colors.items():
        colors[group] = _TRADE_COLOR_OVERRIDES.get(group, color)
    colors.update(_NON_CROP_TRADE_COLORS)
    return colors


# Display names for internal food group identifiers.
_FOOD_GROUP_DISPLAY = {
    "grain": "Grain",
    "whole_grains": "Whole grains",
    "fruits": "Fruits",
    "vegetables": "Vegetables",
    "legumes": "Legumes",
    "nuts_seeds": "Oilseeds",
    "starchy_vegetable": "Starchy vegetables",
    "oil": "Oil",
    "red_meat": "Red meat",
    "poultry": "Poultry",
    "dairy": "Dairy",
    "eggs": "Eggs",
    "sugar": "Sugar",
}


# ---------------------------------------------------------------------------
# Hub position helpers
# ---------------------------------------------------------------------------


def _infer_n_hubs(n: pypsa.Network, hub_prefix: str) -> int:
    """Count distinct hub indices from bus names starting with *hub_prefix*."""
    hub_buses = n.buses.static.index[n.buses.static.index.str.startswith(hub_prefix)]
    indices = set()
    for bus in hub_buses:
        # e.g. "hub:crop:3_wheat" -> "3"
        idx_item = bus.split(":")[2]
        indices.add(int(idx_item.split("_")[0]))
    return len(indices)


def _run_hub_kmeans(
    regions_gdf: gpd.GeoDataFrame, n_hubs: int
) -> tuple[np.ndarray, np.ndarray]:
    """Run KMeans hub clustering (matches build_model/trade.py).

    Returns (hub_positions_lonlat, cluster_labels).
    """
    gdf_ee = regions_gdf.to_crs(6933)
    cent = gdf_ee.geometry.centroid
    coords = np.column_stack([cent.x.values, cent.y.values])
    k = min(max(1, n_hubs), len(coords))
    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    labels = km.fit_predict(coords)
    centers_ee = km.cluster_centers_
    transformer = Transformer.from_crs(6933, 4326, always_xy=True)
    lons, lats = transformer.transform(centers_ee[:, 0], centers_ee[:, 1])
    return np.column_stack([lons, lats]), labels


def _compute_hub_positions(regions_gdf: gpd.GeoDataFrame, n_hubs: int) -> np.ndarray:
    """Derive visually representative hub positions.

    Instead of using raw KMeans centers (which can land in the ocean when
    a cluster contains far-flung disconnected regions), we dissolve the
    regions by cluster and take the centroid of the largest contiguous
    polygon in each cluster.
    """
    from shapely.geometry import MultiPolygon

    _, labels = _run_hub_kmeans(regions_gdf, n_hubs)
    gdf = regions_gdf.copy()
    gdf["_hub_cluster"] = labels
    dissolved = gdf.dissolve(by="_hub_cluster").to_crs(4326)

    positions = np.empty((n_hubs, 2))
    for cluster_idx in range(n_hubs):
        geom = dissolved.loc[cluster_idx, "geometry"]
        if isinstance(geom, MultiPolygon):
            largest = max(geom.geoms, key=lambda g: g.area)
        else:
            largest = geom
        c = largest.centroid
        positions[cluster_idx] = [c.x, c.y]
    return positions


def _compute_hub_regions(
    regions_gdf: gpd.GeoDataFrame, n_hubs: int
) -> gpd.GeoDataFrame:
    """Dissolve regions by hub cluster assignment.

    Returns a GeoDataFrame (in WGS84) with one row per hub catchment area.
    """
    _, labels = _run_hub_kmeans(regions_gdf, n_hubs)
    gdf = regions_gdf.copy()
    gdf["_hub_cluster"] = labels
    dissolved = gdf.dissolve(by="_hub_cluster")
    return dissolved.to_crs(4326) if dissolved.crs != 4326 else dissolved


# ---------------------------------------------------------------------------
# Trade flow extraction
# ---------------------------------------------------------------------------


def _extract_hub_flows(
    n: pypsa.Network,
    carrier: str,
    item_column: str,
    hub_prefix: str,
    category_fn,
) -> pd.DataFrame:
    """Return a DataFrame of hub-to-hub flows aggregated by category.

    Columns: hub_from, hub_to, category, flow_mt, hub_type
    """
    links = n.links.static
    trade = links[links["carrier"] == carrier]
    hub_mask = trade["bus0"].str.startswith(hub_prefix) & trade["bus1"].str.startswith(
        hub_prefix
    )
    hub_links = trade[hub_mask]
    if hub_links.empty:
        return pd.DataFrame(
            columns=["hub_from", "hub_to", "category", "flow_mt", "hub_type"]
        )

    snapshot = "now" if "now" in n.snapshots else n.snapshots[0]
    p0 = n.links.dynamic.p0.loc[snapshot]

    rows = []
    for name, row in hub_links.iterrows():
        flow = float(p0.get(name, 0.0))
        if flow < 0.1:  # Skip negligible flows (< 0.1 Mt)
            continue
        hub_from = int(row["bus0"].split(":")[2].split("_")[0])
        hub_to = int(row["bus1"].split(":")[2].split("_")[0])
        item = row[item_column]
        cat = category_fn(item)
        rows.append(
            {
                "hub_from": hub_from,
                "hub_to": hub_to,
                "category": cat,
                "flow_mt": flow,
                "hub_type": carrier,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["hub_from", "hub_to", "category", "flow_mt", "hub_type"]
        )

    df = pd.DataFrame(rows)
    # Aggregate by (hub_from, hub_to, category)
    df = df.groupby(["hub_from", "hub_to", "category", "hub_type"], as_index=False)[
        "flow_mt"
    ].sum()
    return df


def _get_top_trade_flows(
    n: pypsa.Network,
    regions_gdf: gpd.GeoDataFrame,
    n_top: int,
    crop_to_group: dict[str, str],
) -> pd.DataFrame:
    """Return the *n_top* largest hub-to-hub aggregated trade flows.

    All trade types share the same hub positions, so we infer *n_hubs*
    from whichever prefix has buses and compute positions once.

    Returned DataFrame has columns:
        lon_from, lat_from, lon_to, lat_to, category, flow_mt
    """
    # Build food-to-group mapping from food_consumption links in the network
    consume = n.links.static[n.links.static["carrier"] == "food_consumption"]
    food_to_group: dict[str, str] = {}
    if "food_group" in consume.columns:
        for food, group in zip(consume["food"], consume["food_group"]):
            if pd.notna(food) and pd.notna(group):
                food_to_group[str(food)] = _FOOD_GROUP_DISPLAY.get(
                    str(group), str(group)
                )

    trade_specs = [
        (
            "trade_crop",
            "crop",
            "hub:crop:",
            lambda item: crop_to_group.get(item, "Other"),
        ),
        (
            "trade_food",
            "food",
            "hub:food:",
            lambda item: food_to_group.get(item, "Other"),
        ),
        ("trade_feed", "feed_category", "hub:feed:", lambda _: "Animal feed"),
    ]

    # Infer n_hubs from the first prefix that has buses
    n_hubs = 0
    for _, _, prefix, _ in trade_specs:
        n_hubs = _infer_n_hubs(n, prefix)
        if n_hubs > 0:
            break
    if n_hubs == 0:
        return pd.DataFrame(
            columns=[
                "lon_from",
                "lat_from",
                "lon_to",
                "lat_to",
                "category",
                "flow_mt",
            ]
        )

    positions = _compute_hub_positions(regions_gdf, n_hubs)

    all_flows = []
    for carrier, item_col, prefix, cat_fn in trade_specs:
        df = _extract_hub_flows(n, carrier, item_col, prefix, cat_fn)
        if df.empty:
            continue
        # Attach coordinates
        df["lon_from"] = df["hub_from"].map(lambda i, p=positions: p[i, 0])
        df["lat_from"] = df["hub_from"].map(lambda i, p=positions: p[i, 1])
        df["lon_to"] = df["hub_to"].map(lambda i, p=positions: p[i, 0])
        df["lat_to"] = df["hub_to"].map(lambda i, p=positions: p[i, 1])
        all_flows.append(df)

    if not all_flows:
        return pd.DataFrame(
            columns=[
                "lon_from",
                "lat_from",
                "lon_to",
                "lat_to",
                "category",
                "flow_mt",
            ]
        )

    combined = pd.concat(all_flows, ignore_index=True)
    combined = combined.sort_values("flow_mt", ascending=False).head(n_top)
    return combined


# ---------------------------------------------------------------------------
# Projected-space path helper
# ---------------------------------------------------------------------------

# Shared projection instance (must match the axes projection).
_PROJ = ccrs.EqualEarth()
_PLATE = ccrs.PlateCarree()


def _projected_path(
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
    n_points: int = 60,
    bend_km: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Linearly interpolate in EqualEarth space with an optional rightward bend.

    Returns (xs, ys) arrays in EqualEarth projected coordinates.
    """
    x1, y1 = _PROJ.transform_point(lon1, lat1, _PLATE)
    x2, y2 = _PROJ.transform_point(lon2, lat2, _PLATE)

    t = np.linspace(0, 1, n_points + 2)
    xs = x1 + t * (x2 - x1)
    ys = y1 + t * (y2 - y1)

    if bend_km != 0:
        dx, dy = x2 - x1, y2 - y1
        length = np.hypot(dx, dy)
        if length > 0:
            # Right perpendicular in projected space (90 deg clockwise)
            perp_x = dy / length
            perp_y = -dx / length
            offset = bend_km * 1000.0 * np.sin(np.pi * t)
            xs = xs + perp_x * offset
            ys = ys + perp_y * offset

    return xs, ys


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def _plot_map(
    dominant_group_grid: np.ndarray,
    intensity_grid: np.ndarray,
    extent: tuple,
    gdf: gpd.GeoDataFrame,
    trade_flows: pd.DataFrame,
    crop_group_colors: dict[str, str],
    trade_category_colors: dict[str, str],
    output_path: str,
    hub_regions: gpd.GeoDataFrame | None = None,
) -> None:
    """Render the combined crop-production + trade-flow map."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    plate = ccrs.PlateCarree()
    fig, ax = plt.subplots(
        figsize=(13, 6.5),
        dpi=150,
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#ffffff")
    ax.set_global()

    # --- Base crop production layer (identical to plot_crop_production_map) ---

    group_names = list(crop_group_colors.keys())
    h, w = dominant_group_grid.shape
    rgba = np.ones((h, w, 4), dtype=np.float32)

    for idx, gname in enumerate(group_names):
        color = crop_group_colors[gname]
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

    rgba[dominant_group_grid < 0, 3] = 0.0

    # Unmodeled land (gray + hatching)
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0", edgecolor="none", zorder=0)
    ax.add_feature(
        cfeature.LAND,
        facecolor="none",
        edgecolor="#ffffff",
        hatch="//////",
        linewidth=0.3,
        zorder=0.5,
    )
    # White fill for modeled regions
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

    # Hub catchment area outlines
    if hub_regions is not None:
        ax.add_geometries(
            hub_regions.geometry,
            crs=plate,
            facecolor="none",
            edgecolor="#555555",
            linewidth=0.6,
            linestyle=(0, (3, 3)),  # dotted
            zorder=3.5,
        )

    # Hub position markers
    if not trade_flows.empty:
        seen: set[tuple[float, float]] = set()
        for _, row in trade_flows.iterrows():
            for suffix in ["from", "to"]:
                pt = (row[f"lon_{suffix}"], row[f"lat_{suffix}"])
                if pt not in seen:
                    seen.add(pt)
                    px, py = _PROJ.transform_point(pt[0], pt[1], _PLATE)
                    ax.plot(
                        px,
                        py,
                        "o",
                        color="#444444",
                        markersize=2,
                        alpha=0.7,
                        transform=_PROJ,
                        zorder=10,
                    )

    # --- Trade flow arrows ---

    if not trade_flows.empty:
        # Sort ascending so large flows draw on top of small ones
        trade_flows = trade_flows.sort_values("flow_mt", ascending=True)
        flows = trade_flows["flow_mt"].values
        max_flow = flows.max()
        min_flow = flows.min()

        def _lw(f):
            if max_flow <= min_flow:
                return (_LW_MIN + _LW_MAX) / 2
            t = (np.sqrt(f) - np.sqrt(min_flow)) / (
                np.sqrt(max_flow) - np.sqrt(min_flow)
            )
            return _LW_MIN + t * (_LW_MAX - _LW_MIN)

        for _, row in trade_flows.iterrows():
            cat = row["category"]
            color = trade_category_colors.get(cat, "#888888")
            color_rgb = mcolors.to_rgb(color) if isinstance(color, str) else color[:3]

            lw = _lw(row["flow_mt"])
            xs, ys = _projected_path(
                row["lon_from"],
                row["lat_from"],
                row["lon_to"],
                row["lat_to"],
                bend_km=_BEND_KM,
            )
            proj = _PROJ

            # White border
            ax.plot(
                xs,
                ys,
                color="white",
                linewidth=lw + 1.4,
                alpha=0.9,
                solid_capstyle="round",
                transform=proj,
                zorder=3.8,
            )
            # Coloured path
            ax.plot(
                xs,
                ys,
                color=color_rgb,
                linewidth=lw,
                alpha=0.88,
                solid_capstyle="round",
                transform=proj,
                zorder=4,
            )

            # Arrowhead: filled triangle in projected space
            tip_x, tip_y = xs[-1], ys[-1]
            dx = xs[-1] - xs[-4]
            dy = ys[-1] - ys[-4]
            heading = np.arctan2(dx, dy)
            head_len = (150 + lw * 80) * 1000
            head_hw = head_len * 0.35
            # Base point behind the tip
            base_x = tip_x - head_len * np.sin(heading)
            base_y = tip_y - head_len * np.cos(heading)
            # Wing points perpendicular to heading
            l_x = base_x + head_hw * np.cos(heading)
            l_y = base_y - head_hw * np.sin(heading)
            r_x = base_x - head_hw * np.cos(heading)
            r_y = base_y + head_hw * np.sin(heading)
            tri = Polygon(
                [(tip_x, tip_y), (l_x, l_y), (r_x, r_y)],
                closed=True,
                fc=(*color_rgb, 0.88),
                ec="white",
                lw=0.8,
                transform=proj,
                zorder=5,
            )
            ax.add_patch(tri)

    # --- Spines & gridlines ---
    for name, spine in ax.spines.items():
        if name == "geo":
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
    gl.xlabel_style = {"size": 6, "color": "#555555"}
    gl.ylabel_style = {"size": 6, "color": "#555555"}
    gl.top_labels = False
    gl.right_labels = False

    # --- Trade legend ---
    if not trade_flows.empty:
        from matplotlib.lines import Line2D

        # Determine which categories are actually present
        active_cats = trade_flows["category"].unique()
        ordered = [
            *crop_group_colors.keys(),
            *_FOOD_GROUP_DISPLAY.values(),
            "Animal feed",
        ]
        legend_cats = [c for c in ordered if c in active_cats]

        # Category colour entries
        cat_handles = []
        for cat in legend_cats:
            c = trade_category_colors.get(cat, "#888888")
            if isinstance(c, str):
                c = mcolors.to_rgb(c)
            cat_handles.append(
                Line2D(
                    [0],
                    [0],
                    color=c,
                    linewidth=2.0,
                    alpha=0.80,
                    solid_capstyle="round",
                    label=cat,
                )
            )

        # Volume reference lines at nice round values
        max_mt = trade_flows["flow_mt"].max()
        min_mt = trade_flows["flow_mt"].min()
        # Pick 2 reference values: a large and a small
        top_ref = int(max_mt // 10) * 10
        if top_ref < max_mt * 0.5:
            top_ref = int(max_mt)
        bot_ref = max(1, int(round(min_mt)))
        if bot_ref >= top_ref:
            bot_ref = max(1, top_ref // 4)

        vol_handles = []
        for ref_mt in [top_ref, bot_ref]:
            t = (
                (np.sqrt(ref_mt) - np.sqrt(min_mt))
                / (np.sqrt(max_mt) - np.sqrt(min_mt))
                if max_mt > min_mt
                else 0.5
            )
            lw = _LW_MIN + max(0, min(1, t)) * (_LW_MAX - _LW_MIN)
            vol_handles.append(
                Line2D(
                    [0],
                    [0],
                    color="#555555",
                    linewidth=lw,
                    alpha=0.75,
                    solid_capstyle="round",
                    label=f"{ref_mt} Mt",
                )
            )

        ax.legend(
            handles=cat_handles + vol_handles,
            loc="upper left",
            bbox_to_anchor=(0.65, 0.32),
            fontsize=5.5,
            frameon=True,
            fancybox=False,
            edgecolor="#cccccc",
            framealpha=0.95,
            borderpad=0.6,
            handlelength=1.8,
            title="Trade flows",
            title_fontsize=6,
        )

    # --- Annotation ---
    fig.canvas.draw()
    map_pos = ax.get_position()
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

    ax.set_title("Crop Production and Dominant Trade Flows", fontsize=8)
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Saved crop + trade map to %s", out)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    resource_classes_path: str = snakemake.input.resource_classes  # type: ignore[name-defined]
    land_area_by_class_path: str = snakemake.input.land_area_by_class  # type: ignore[name-defined]
    land_grazing_only_path: str = snakemake.input.land_grazing_only  # type: ignore[name-defined]
    land_use_path: str = snakemake.input.land_use  # type: ignore[name-defined]
    network_path: str = snakemake.input.network  # type: ignore[name-defined]
    output_pdf: str = snakemake.output.pdf  # type: ignore[name-defined]

    crop_to_group, crop_group_colors = crop_groups_from_config(
        snakemake.config  # type: ignore[name-defined]
    )
    trade_category_colors = _build_trade_category_colors(crop_group_colors)

    gdf = _setup_regions(regions_path)
    region_name_to_id = {region: idx for idx, region in enumerate(gdf["region"])}

    rc_data = _load_resource_classes(resource_classes_path)
    potential_area = _load_potential_area(
        land_area_by_class_path, land_grazing_only_path
    )
    land_use = _load_land_use_by_region_class_crop(land_use_path)

    if land_use.empty:
        logger.warning("No land use data; skipping plot")
        return

    dominant_group_grid, intensity_grid, _crops_by_group, _area_by_crop = (
        _build_dominant_group_and_intensity_grids(
            land_use,
            rc_data["class_grid"],
            rc_data["region_grid"],
            potential_area,
            region_name_to_id,
            crop_to_group,
            crop_group_colors,
        )
    )

    # Load solved network and extract trade flows
    n = load_solved_network(network_path)
    trade_flows = _get_top_trade_flows(n, gdf, N_TOP_FLOWS, crop_to_group)
    logger.info(
        "Selected %d trade flows (max %.1f Mt, min %.1f Mt)",
        len(trade_flows),
        trade_flows["flow_mt"].max() if len(trade_flows) else 0,
        trade_flows["flow_mt"].min() if len(trade_flows) else 0,
    )

    # Compute hub catchment regions for dotted outline overlay
    n_hubs = _infer_n_hubs(n, "hub:crop:")
    hub_regions = _compute_hub_regions(gdf, n_hubs) if n_hubs > 0 else None

    _plot_map(
        dominant_group_grid,
        intensity_grid,
        rc_data["extent"],
        gdf,
        trade_flows,
        crop_group_colors,
        trade_category_colors,
        output_pdf,
        hub_regions=hub_regions,
    )


if __name__ == "__main__":
    main()
