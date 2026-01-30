#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot a map of hydrological basins colored by water value from marginal prices.

Reads water buses, marginal prices from the model. Assumes:
- water storage units in Mm³
- marginal price units in bnUSD per Mm³
- The UI transposes this to USD/m³ via the carrier scaling factor.
"""

import logging
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib

matplotlib.use("pdf")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def plot_water_value_map(
    solved_network_path: Path, regions_path: Path, output_path: Path
) -> None:
    logger.info("Loading solved network from %s", solved_network_path)
    n = pypsa.Network(str(solved_network_path))

    water_buses = n.buses.static[n.buses.static["carrier"] == "water"]
    if water_buses.empty:
        raise ValueError("No water buses found in network")

    marginal_prices = n.buses.dynamic.marginal_price.iloc[0][water_buses.index]

    scale_meta = n.meta.get("carrier_unit_scale", {})
    mm3_per_m3 = float(
        scale_meta.get("water_mm3_per_m3", scale_meta.get("water_km3_per_m3", 1.0))
    )
    if mm3_per_m3 <= 0 or not np.isfinite(mm3_per_m3):
        mm3_per_m3 = 1.0

    marginal_prices_per_m3 = marginal_prices * mm3_per_m3

    water_values = pd.DataFrame(
        {
            "region": water_buses["region"].tolist(),
            # Present to users in USD/m³ by using the carrier scale factor
            "water_value_usd_per_m3": marginal_prices_per_m3.values,
        }
    )

    logger.info("Loading regions from %s", regions_path)
    gdf = gpd.read_file(regions_path)

    if gdf.crs is None:
        logger.warning("Input CRS missing; assuming EPSG:4326 (WGS84)")
        gdf = gdf.set_crs(4326, allow_override=True)
    else:
        gdf = gdf.to_crs(4326)

    if "region" not in gdf.columns:
        raise ValueError("Regions GeoDataFrame must have 'region' column")

    gdf = gdf.merge(water_values, on="region", how="left")

    has_value = gdf["water_value_usd_per_m3"].notna()
    if not has_value.any():
        raise ValueError("No regions matched with water values")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(
        figsize=(12, 6),
        dpi=150,
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_facecolor("#f7f9fb")
    ax.set_global()

    gdf_no_value = gdf[~has_value]
    if not gdf_no_value.empty:
        ax.add_geometries(
            gdf_no_value.geometry,
            crs=ccrs.PlateCarree(),
            facecolor="#e0e0e0",
            edgecolor="#444444",
            linewidth=0.3,
            zorder=1,
        )

    gdf_value = gdf[has_value]
    values = gdf_value["water_value_usd_per_m3"].values
    vmin = np.nanmin(values)
    vmax = np.nanmax(values)

    if vmin == vmax:
        norm = mcolors.Normalize(vmin=vmin - 0.001, vmax=vmax + 0.001)
    else:
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    cmap = plt.colormaps["viridis"]

    for _, row in gdf_value.iterrows():
        value = row["water_value_usd_per_m3"]
        color = cmap(norm(value))
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor="#444444",
            linewidth=0.3,
            zorder=2,
        )

    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    gl = ax.gridlines(
        draw_labels=True,
        crs=ccrs.PlateCarree(),
        linewidth=0.35,
        color="#888888",
        alpha=0.45,
        linestyle="--",
    )
    gl.xlocator = mticker.FixedLocator(np.arange(-180, 181, 30))
    gl.ylocator = mticker.FixedLocator(np.arange(-60, 61, 15))
    gl.xformatter = LongitudeFormatter(number_format=".0f")
    gl.yformatter = LatitudeFormatter(number_format=".0f")
    gl.xlabel_style = {"size": 8, "color": "#555555"}
    gl.ylabel_style = {"size": 8, "color": "#555555"}
    gl.top_labels = False
    gl.right_labels = False

    ax.set_xlabel("Longitude", fontsize=8, color="#555555")
    ax.set_ylabel("Latitude", fontsize=8, color="#555555")

    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.032, pad=0.02)
    cbar.set_label("Water value (USD/m³)")

    ax.set_title("Water Value by Hydrological Basin")
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Saved water value map to %s", output_path)


if __name__ == "__main__":
    setup_script_logging(snakemake.log[0])
    plot_water_value_map(
        Path(snakemake.input.network),  # type: ignore[name-defined]
        Path(snakemake.input.regions),  # type: ignore[name-defined]
        Path(snakemake.output.pdf),  # type: ignore[name-defined]
    )
