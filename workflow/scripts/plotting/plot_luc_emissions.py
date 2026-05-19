# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot land use change emissions by country (bar) and resource class (map)."""

import logging
from pathlib import Path

from affine import Affine
import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
from rasterio.transform import array_bounds
import xarray as xr

matplotlib.use("pdf")

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.snakemake_utils import load_solved_network

logger = logging.getLogger(__name__)


def _extract_luc_emissions_by_country(
    n: pypsa.Network, snapshot: str, region_to_country: dict[str, str]
) -> pd.DataFrame:
    """Extract LUC emissions by country from expansion and sparing links.

    Returns DataFrame with columns: expansion_co2, sequestration_co2, net_co2
    Index is country code (ISO3).
    Units: MtCO2/yr
    """
    links_static = n.links.static
    p0 = n.links.dynamic.p0.loc[snapshot]

    expansion: dict[str, float] = {}
    sequestration: dict[str, float] = {}

    # Land conversion links (positive emissions from expansion)
    lc_mask = links_static["carrier"].isin(["land_conversion", "new_to_pasture"])
    lc_links = links_static[lc_mask]
    for link in lc_links.index:
        region = lc_links.at[link, "region"]
        if pd.isna(region):
            continue
        country = region_to_country.get(str(region))
        if country is None:
            continue
        flow = float(p0.get(link, 0.0))
        eff2 = float(lc_links.at[link, "efficiency2"])
        emission = flow * eff2  # MtCO2/yr (positive)
        expansion[country] = expansion.get(country, 0.0) + emission

    # Spare land links (negative emissions from sequestration)
    sl_mask = links_static["carrier"].isin(["spare_land", "spare_existing_grassland"])
    sl_links = links_static[sl_mask]
    for link in sl_links.index:
        region = sl_links.at[link, "region"]
        if pd.isna(region):
            continue
        country = region_to_country.get(str(region))
        if country is None:
            continue
        flow = float(p0.get(link, 0.0))
        eff2 = float(sl_links.at[link, "efficiency2"])
        emission = flow * eff2  # MtCO2/yr (negative)
        sequestration[country] = sequestration.get(country, 0.0) + emission

    # Build DataFrame
    countries = sorted(set(expansion) | set(sequestration))
    df = pd.DataFrame(index=pd.Index(countries, name="country"))
    df["expansion_co2"] = pd.Series(expansion)
    df["sequestration_co2"] = pd.Series(sequestration)
    df = df.fillna(0.0).infer_objects(copy=False)
    df["net_co2"] = df["expansion_co2"] + df["sequestration_co2"]

    return df


def _extract_luc_intensity_by_cell(
    n: pypsa.Network, snapshot: str
) -> dict[tuple[int, int], float]:
    """Extract LUC emissions intensity by (region_id, resource_class).

    Returns dict mapping (region_id, resource_class) -> tCO2/ha/yr.
    Aggregates over water supplies (irrigated + rainfed).
    """
    links_static = n.links.static
    p0 = n.links.dynamic.p0.loc[snapshot]

    # Collect emissions and land area by (region_id, resource_class)
    emissions: dict[tuple[int, int], float] = {}
    land_area: dict[tuple[int, int], float] = {}

    def _process_links(carrier: str) -> None:
        mask = links_static["carrier"] == carrier
        links = links_static[mask]
        for link in links.index:
            region = links.at[link, "region"]
            rc = links.at[link, "resource_class"]
            if pd.isna(region) or pd.isna(rc):
                continue
            # Extract region_id from region name (e.g., "region0042" -> 42)
            region_id = int(str(region).replace("region", ""))
            rc_int = int(rc)
            key = (region_id, rc_int)

            flow = float(p0.get(link, 0.0))  # Mha
            eff2 = float(links.at[link, "efficiency2"])
            emission = flow * eff2  # MtCO2/yr

            emissions[key] = emissions.get(key, 0.0) + emission
            land_area[key] = land_area.get(key, 0.0) + abs(flow)

    _process_links("land_conversion")
    _process_links("new_to_pasture")
    _process_links("spare_land")
    _process_links("spare_existing_grassland")

    # Compute intensity: MtCO2/yr / Mha = tCO2/ha/yr
    intensity: dict[tuple[int, int], float] = {}
    for key in emissions:
        area = land_area.get(key, 0.0)
        if area > 1e-9:  # Avoid division by zero
            intensity[key] = emissions[key] / area
        # If no land change, intensity is undefined (not included)

    return intensity


def _plot_stacked_bar(
    df: pd.DataFrame, output_path: Path, title: str = "LUC Emissions by Country"
) -> None:
    """Create stacked bar chart of LUC emissions sorted by net emissions."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Sort by net emissions (descending)
    df_sorted = df.sort_values("net_co2", ascending=False)

    # Filter out countries with negligible emissions
    threshold = 0.01  # MtCO2/yr
    df_sorted = df_sorted[
        (df_sorted["expansion_co2"].abs() > threshold)
        | (df_sorted["sequestration_co2"].abs() > threshold)
    ]

    n_countries = len(df_sorted)
    if n_countries == 0:
        logger.warning("No countries with significant LUC emissions to plot")
        # Create empty figure
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.text(0.5, 0.5, "No significant LUC emissions", ha="center", va="center")
        fig.savefig(out, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    # Figure sizing: narrow bars for many countries
    bar_width = 0.6
    fig_width = max(12, n_countries * 0.08)
    fig, ax = plt.subplots(figsize=(fig_width, 6), dpi=150)

    x = np.arange(n_countries)

    # Plot expansion (positive, red) and sequestration (negative, blue)
    ax.bar(
        x,
        df_sorted["expansion_co2"],
        width=bar_width,
        color="#d62728",
        edgecolor="none",
        label="Land expansion",
    )
    ax.bar(
        x,
        df_sorted["sequestration_co2"],
        width=bar_width,
        color="#1f77b4",
        edgecolor="none",
        label="Land sparing",
    )

    # Add net emissions line
    ax.plot(
        x,
        df_sorted["net_co2"],
        color="#7f7f7f",
        linewidth=1.5,
        marker="",
        label="Net LUC",
        zorder=10,
    )

    # Zero line
    ax.axhline(y=0, color="#444444", linewidth=0.8, linestyle="-", zorder=5)

    # X-axis labels
    ax.set_xticks(x)
    ax.set_xticklabels(
        df_sorted.index, rotation=90, ha="center", fontsize=6, color="#333333"
    )

    # Labels and title
    ax.set_xlabel("Country", fontsize=10, color="#333333")
    ax.set_ylabel("CO₂ emissions (Mt/yr)", fontsize=10, color="#333333")
    ax.set_title(title, fontsize=12, color="#222222")

    # Legend
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

    # Grid
    ax.yaxis.grid(True, linestyle="--", alpha=0.5, color="#cccccc")
    ax.set_axisbelow(True)

    # Tight layout with room for labels
    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)

    logger.info("Saved LUC stacked bar chart to %s (%d countries)", out, n_countries)


def _plot_raster_map(
    intensity: dict[tuple[int, int], float],
    resource_classes_path: str,
    output_path: Path,
    title: str = "LUC Emissions Intensity",
) -> None:
    """Create raster map of LUC emissions intensity at resource class level."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Load resource classes raster
    ds = xr.open_dataset(resource_classes_path)
    region_grid = ds["region_id"].values.astype(np.int32)
    class_grid = ds["resource_class"].values.astype(np.int8)
    height, width = region_grid.shape

    transform_gdal = ds.attrs["transform"]
    transform = Affine.from_gdal(*transform_gdal)
    extent = array_bounds(height, width, transform)

    # Build intensity raster
    intensity_grid = np.full((height, width), np.nan, dtype=np.float32)

    for (region_id, rc), value in intensity.items():
        mask = (region_grid == region_id) & (class_grid == rc)
        intensity_grid[mask] = value

    # Determine color scale: diverging around zero (red-blue)
    valid_vals = intensity_grid[np.isfinite(intensity_grid)]
    if len(valid_vals) == 0:
        logger.warning("No valid intensity values to plot")
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.text(0.5, 0.5, "No LUC activity", ha="center", va="center")
        fig.savefig(out, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    vmax = max(np.abs(valid_vals).max(), 0.1)
    vmin = -vmax

    # Red-blue diverging colormap (red=positive/expansion, blue=negative/sequestration)
    cmap = plt.cm.RdBu_r
    cmap.set_bad(color="#e0e0e0", alpha=0.0)  # Transparent for NaN

    norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)

    fig, ax = plt.subplots(
        figsize=(13, 6.5), dpi=150, subplot_kw={"projection": ccrs.EqualEarth()}
    )
    plate = ccrs.PlateCarree()
    ax.set_facecolor("#f7f9fb")
    ax.set_global()

    # Plot raster
    img = ax.imshow(
        intensity_grid,
        origin="upper",
        extent=[extent[0], extent[2], extent[1], extent[3]],
        transform=plate,
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
        zorder=2,
    )

    # Colorbar
    cbar_ax = fig.add_axes([0.25, 0.08, 0.5, 0.025])
    cbar = fig.colorbar(img, cax=cbar_ax, orientation="horizontal", extend="both")
    cbar.set_label("LUC emissions intensity (tCO₂/ha/yr)", fontsize=9)
    cbar.ax.tick_params(labelsize=8)

    # Gridlines
    gl = ax.gridlines(draw_labels=False, linewidth=0.4, color="#aaaaaa", alpha=0.5)
    gl.xlocator = plt.MultipleLocator(60)
    gl.ylocator = plt.MultipleLocator(30)

    ax.set_title(title, fontsize=12, color="#222222")

    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)

    logger.info(
        "Saved LUC raster map to %s (intensity range: %.2f to %.2f tCO2/ha/yr)",
        out,
        valid_vals.min(),
        valid_vals.max(),
    )


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    network = load_solved_network(snakemake.input.network)  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    resource_classes_path: str = snakemake.input.resource_classes  # type: ignore[name-defined]
    output_bar = Path(snakemake.output.bar_pdf)  # type: ignore[name-defined]
    output_map = Path(snakemake.output.map_pdf)  # type: ignore[name-defined]
    output_csv = Path(snakemake.output.csv)  # type: ignore[name-defined]

    snapshot = "now" if "now" in network.snapshots else network.snapshots[0]

    # Load regions and build region->country mapping
    logger.info("Loading regions from %s", regions_path)
    gdf_regions = gpd.read_file(regions_path)
    if gdf_regions.crs is None:
        gdf_regions = gdf_regions.set_crs(4326, allow_override=True)
    else:
        gdf_regions = gdf_regions.to_crs(4326)

    if "region" not in gdf_regions.columns or "country" not in gdf_regions.columns:
        raise ValueError(
            "Regions GeoDataFrame must contain 'region' and 'country' columns"
        )

    region_to_country = dict(
        zip(gdf_regions["region"], gdf_regions["country"], strict=True)
    )

    # Extract emissions by country (for bar chart)
    logger.info("Extracting LUC emissions from solved model")
    luc_by_country = _extract_luc_emissions_by_country(
        network, snapshot, region_to_country
    )

    logger.info(
        "Total expansion: %.2f MtCO2/yr, sequestration: %.2f MtCO2/yr, net: %.2f MtCO2/yr",
        luc_by_country["expansion_co2"].sum(),
        luc_by_country["sequestration_co2"].sum(),
        luc_by_country["net_co2"].sum(),
    )

    # Extract intensity by cell (for raster map)
    logger.info("Computing LUC intensity by resource class")
    luc_intensity = _extract_luc_intensity_by_cell(network, snapshot)
    logger.info("Found %d cells with LUC activity", len(luc_intensity))

    # Save CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    luc_by_country.to_csv(output_csv, float_format="%.6g")
    logger.info("Saved LUC emissions table to %s", output_csv)

    # Create plots
    _plot_stacked_bar(luc_by_country, output_bar)
    _plot_raster_map(luc_intensity, resource_classes_path, output_map)


if __name__ == "__main__":
    main()
