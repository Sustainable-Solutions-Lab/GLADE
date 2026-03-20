#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot food consumption by health cluster using pie charts on a map."""

from collections.abc import Iterable, Mapping, Sequence
import logging
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib

matplotlib.use("pdf")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

logger = logging.getLogger(__name__)

PLATE_CARREE = ccrs.PlateCarree()


def _load_country_group_mass(food_group_consumption_path: str) -> pd.DataFrame:
    """Load country-level consumption by food group from pre-extracted statistics.

    Returns DataFrame with countries as rows, food groups as columns, values in Mt.
    """
    df = pd.read_parquet(food_group_consumption_path)
    if df.empty:
        return pd.DataFrame()

    # Pivot to wide format: rows=country, columns=food_group, values=consumption_mt
    pivot = df.pivot_table(
        index="country",
        columns="food_group",
        values="consumption_mt",
        aggfunc="sum",
        fill_value=0.0,
    )
    pivot.index.name = "iso3"
    return pivot.sort_index(axis=0).sort_index(axis=1)


def _aggregate_cluster_group_mass(
    country_group: pd.DataFrame,
    iso_to_cluster: Mapping[str, int],
) -> pd.DataFrame:
    """Aggregate country-level consumption to health clusters."""
    if country_group.empty:
        return pd.DataFrame()

    data: dict[tuple[int, str], float] = {}
    for iso, row in country_group.iterrows():
        cluster = iso_to_cluster.get(str(iso).upper())
        if cluster is None:
            continue
        for group, value in row.items():
            if value <= 0.0 or not np.isfinite(value):
                continue
            key = (int(cluster), str(group))
            data[key] = data.get(key, 0.0) + float(value)

    if not data:
        return pd.DataFrame()

    series = pd.Series(data, dtype=float)
    df = series.unstack(fill_value=0.0).sort_index(axis=0).sort_index(axis=1)
    df.index.name = "health_cluster"
    return df


def _get_cluster_population(
    population_path: str, iso_to_cluster: Mapping[str, int]
) -> pd.Series:
    """Compute population by health cluster from country population data."""
    pop_df = pd.read_csv(population_path)
    pop_df["iso3"] = pop_df["iso3"].str.upper()
    pop_df["cluster"] = pop_df["iso3"].map(iso_to_cluster)
    cluster_pop = pop_df.groupby("cluster")["population"].sum()
    return cluster_pop


def _colors_for_groups(
    groups: Sequence[str], overrides: Mapping[str, str] | None = None
) -> dict[str, str]:
    return categorical_colors(groups, overrides)


def _prepare_cluster_geodata(
    regions_path: str,
    iso_to_cluster: Mapping[str, int],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        logger.warning("Regions GeoDataFrame missing CRS; assuming EPSG:4326")
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    if "country" not in regions.columns:
        raise ValueError("Regions GeoDataFrame must contain a 'country' column")

    regions = regions.assign(country=lambda df: df["country"].str.upper())
    regions["health_cluster"] = regions["country"].map(iso_to_cluster)
    cluster_regions = regions.dropna(subset=["health_cluster"])
    if cluster_regions.empty:
        return regions, gpd.GeoDataFrame(columns=["health_cluster"], crs=regions.crs)

    cluster_regions = cluster_regions.assign(
        health_cluster=lambda df: df["health_cluster"].astype(int)
    )
    dissolved = (
        cluster_regions.dissolve(by="health_cluster", as_index=False)
        .set_index("health_cluster", drop=False)
        .sort_index()
    )
    dissolved_eq = dissolved.to_crs("+proj=eqearth")
    return dissolved, dissolved_eq


def _draw_pie(
    ax: plt.Axes,
    x: float,
    y: float,
    sizes: Iterable[float],
    colors: Iterable[str],
    radius: float,
) -> None:
    sizes_list = list(sizes)
    total = float(sum(sizes_list))
    if radius <= 0 or not np.isfinite(total) or total <= 0:
        return
    colors_list = list(colors)
    cumulative = np.cumsum([0.0] + [max(s, 0.0) for s in sizes_list])
    norm = cumulative[-1]
    if norm <= 0:
        return
    angles = cumulative / norm * 360.0
    for idx, size in enumerate(sizes_list):
        if size <= 0:
            continue
        wedge = mpatches.Wedge(
            center=(x, y),
            r=radius,
            theta1=angles[idx],
            theta2=angles[idx + 1],
            facecolor=colors_list[idx],
            edgecolor="white",
            linewidth=0.4,
            alpha=0.9,
            zorder=5,
        )
        ax.add_patch(wedge)
    outline = mpatches.Circle(
        (x, y),
        radius,
        facecolor="none",
        edgecolor="#444444",
        linewidth=0.35,
        alpha=0.7,
        zorder=6,
    )
    ax.add_patch(outline)


def _plot_cluster_pies(
    cluster_data: pd.DataFrame,
    cluster_gdf: gpd.GeoDataFrame,
    cluster_gdf_eq: gpd.GeoDataFrame,
    colors: Mapping[str, str],
    output_path: Path,
    *,
    radius_scale_factor: float = 0.025,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(
        figsize=(13, 6.5),
        dpi=150,
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#f7f9fb")
    ax.set_global()

    has_geometry = cluster_gdf is not None and not cluster_gdf.empty
    has_geometry_eq = cluster_gdf_eq is not None and not cluster_gdf_eq.empty

    if has_geometry:
        ax.add_geometries(
            cluster_gdf.geometry,
            crs=PLATE_CARREE,
            facecolor="#e4edf4",
            edgecolor="#666666",
            linewidth=0.2,
            zorder=1,
        )

    filtered = cluster_data.copy()
    if not filtered.empty:
        if has_geometry:
            missing_clusters = sorted(set(filtered.index).difference(cluster_gdf.index))
            if missing_clusters:
                logger.warning(
                    "Dropping %d clusters without geometry: %s",
                    len(missing_clusters),
                    ", ".join(map(str, missing_clusters[:10])),
                )
            filtered = filtered.loc[
                cluster_gdf.index.intersection(filtered.index)
            ].fillna(0.0)
        group_totals = filtered.sum(axis=0).sort_values(ascending=False)
        filtered = filtered.loc[:, group_totals[group_totals > 0].index]

    if filtered.empty:
        ax.text(
            0.5,
            0.5,
            "No food consumption data",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=12,
            color="#555555",
        )
        ax.set_title("Food Consumption by Health Cluster")
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    totals = filtered.sum(axis=1)
    if totals.max() <= 0:
        ax.text(
            0.5,
            0.5,
            "No positive totals to plot",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=12,
            color="#555555",
        )
        ax.set_title("Food Consumption by Health Cluster")
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    if not has_geometry_eq:
        raise ValueError("Cluster geometries required for pie placement")

    centroid_points = cluster_gdf_eq.geometry.representative_point()
    xmin, ymin, xmax, ymax = cluster_gdf_eq.total_bounds
    width = xmax - xmin
    height = ymax - ymin
    scale_extent = max(width, height)
    if not np.isfinite(scale_extent) or scale_extent <= 0:
        scale_extent = 1.0
    radius_scale = radius_scale_factor * scale_extent
    radii = np.sqrt(totals / totals.max()) * radius_scale

    ordered_groups = filtered.columns.tolist()
    color_list = [colors[group] for group in ordered_groups]

    for cluster in filtered.index:
        point = centroid_points.loc[cluster]
        radius = float(radii.get(cluster, 0.0))
        values = filtered.loc[cluster, ordered_groups].tolist()
        _draw_pie(ax, point.x, point.y, values, color_list, radius)

    handles = [
        mpatches.Patch(facecolor=colors[group], label=group) for group in ordered_groups
    ]
    legend1 = ax.legend(
        handles,
        [f"{g}" for g in ordered_groups],
        title="Food groups",
        loc="lower left",
        bbox_to_anchor=(0.03, 0.02),
        fontsize=8,
        title_fontsize=9,
        frameon=True,
        borderpad=0.8,
        labelspacing=0.6,
        handlelength=1.2,
    )
    legend1._legend_box.align = "left"
    ax.add_artist(legend1)

    ref_fracs = np.array([0.25, 0.5, 1.0])
    ref_vals = np.unique(totals.max() * ref_fracs)
    marker_scale = 900.0
    size_handles = [
        ax.scatter(
            [],
            [],
            s=float((val / totals.max()) * marker_scale),
            facecolors="#d0d7de",
            edgecolors="#555555",
            linewidths=0.6,
            alpha=0.8,
        )
        for val in ref_vals
    ]
    size_labels = [f"{val:,.1f} g/person/day" for val in ref_vals]
    legend2 = ax.legend(
        size_handles,
        size_labels,
        title="Pie size ∝ total",
        loc="lower left",
        bbox_to_anchor=(0.38, 0.02),
        fontsize=8,
        title_fontsize=9,
        frameon=True,
        scatterpoints=1,
        borderpad=0.8,
        labelspacing=1.2,
    )
    legend2._legend_box.align = "left"
    ax.add_artist(legend2)

    gridlines = ax.gridlines(
        draw_labels=True,
        crs=PLATE_CARREE,
        linewidth=0.35,
        color="#888888",
        alpha=0.4,
        linestyle="--",
    )
    gridlines.xlocator = mticker.FixedLocator(np.arange(-180, 181, 30))
    gridlines.ylocator = mticker.FixedLocator(np.arange(-60, 61, 15))
    gridlines.xformatter = LongitudeFormatter(number_format=".0f")
    gridlines.yformatter = LatitudeFormatter(number_format=".0f")
    gridlines.xlabel_style = {"size": 8, "color": "#555555"}
    gridlines.ylabel_style = {"size": 8, "color": "#555555"}
    gridlines.top_labels = False
    gridlines.right_labels = False

    ax.set_title("Food Consumption by Health Cluster (g/person/day)")
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:  # pragma: no cover
        raise RuntimeError("This script must be run via Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    food_group_consumption_path = snakemake.input.food_group_consumption
    population_path = snakemake.input.population
    clusters_path = snakemake.input.clusters
    regions_path = snakemake.input.regions
    output_pdf = Path(snakemake.output.pdf)
    output_csv = Path(snakemake.output.csv)

    logger.info("Loading food group consumption from %s", food_group_consumption_path)
    country_group = _load_country_group_mass(food_group_consumption_path)
    logger.info("Found %d countries with food group data", country_group.shape[0])

    cluster_df = pd.read_csv(clusters_path)
    if (
        "country_iso3" not in cluster_df.columns
        or "health_cluster" not in cluster_df.columns
    ):
        raise ValueError(
            "Cluster table must contain 'country_iso3' and 'health_cluster' columns"
        )
    iso_to_cluster = (
        cluster_df.assign(country_iso3=lambda df: df["country_iso3"].str.upper())
        .set_index("country_iso3")["health_cluster"]
        .astype(int)
        .to_dict()
    )

    cluster_mass = _aggregate_cluster_group_mass(country_group, iso_to_cluster)
    logger.info("Aggregated to %d clusters", cluster_mass.shape[0])

    # Get cluster population from population CSV
    population_series = _get_cluster_population(population_path, iso_to_cluster)
    missing_population = sorted(
        cluster
        for cluster in cluster_mass.index
        if population_series.get(int(cluster), 0.0) <= 0.0
    )
    if missing_population:
        raise ValueError(
            f"Missing population for clusters: {', '.join(map(str, missing_population))}"
        )

    consumption = cluster_mass.mul(GRAMS_PER_MEGATONNE).div(DAYS_PER_YEAR)
    consumption = consumption.div(population_series, axis=0)

    consumption = consumption.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    consumption = consumption.loc[(consumption.sum(axis=1) > 0)]

    if consumption.empty:
        logger.warning("No positive consumption found after conversion")

    groups = consumption.sum(axis=0).sort_values(ascending=False).index.tolist()
    group_colors = getattr(snakemake.params, "group_colors", {}) or {}
    colors = _colors_for_groups(groups, group_colors)

    cluster_gdf, cluster_gdf_eq = _prepare_cluster_geodata(regions_path, iso_to_cluster)
    _plot_cluster_pies(consumption, cluster_gdf, cluster_gdf_eq, colors, output_pdf)

    consumption.sort_index(axis=0).sort_index(axis=1).to_csv(output_csv, index=True)
    logger.info("Saved cluster consumption data to %s", output_csv)
    logger.info("Saved map to %s", output_pdf)


if __name__ == "__main__":
    main()
