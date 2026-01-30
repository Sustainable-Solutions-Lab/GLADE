#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot a Plate Carrée map of resource classes by grid cell."""

from collections.abc import Iterable, Sequence
import logging
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import matplotlib

matplotlib.use("pdf")
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from rasterio.transform import Affine, array_bounds
import xarray as xr

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_resource_classes(
    path: Path,
) -> tuple[np.ndarray, Affine, Sequence[float] | None]:
    with xr.open_dataset(path, mode="r") as ds:
        if "resource_class" not in ds:
            raise ValueError("NetCDF must contain a 'resource_class' variable")
        data = ds["resource_class"].values.astype(float)
        transform_vals = ds.attrs.get("transform")
        if transform_vals is None:
            raise ValueError("NetCDF missing 'transform' attribute")
        transform = Affine.from_gdal(*transform_vals)
        crs_wkt = ds.attrs.get("crs_wkt")
        if not crs_wkt:
            raise ValueError("NetCDF missing 'crs_wkt' attribute for CRS")
        if abs(transform.b) > 1e-9 or abs(transform.d) > 1e-9:
            raise ValueError("Resource class grid must not contain rotation terms")
        quantiles_attr = ds.attrs.get("quantiles")
        if quantiles_attr is None:
            quantiles = None
        else:
            quantiles = tuple(float(x) for x in np.atleast_1d(quantiles_attr))
    return data, transform, quantiles


def _subdued_colors(count: int) -> Iterable[str]:
    if count <= 0:
        return []
    cmap = plt.colormaps["YlGnBu"]
    span = max(count - 1, 1)
    colors = [
        cmap(0.3 + 0.5 * (i / span)) if count > 1 else cmap(0.45) for i in range(count)
    ]
    return [mcolors.to_hex(c) for c in reversed(colors)]


def _quantile_labels(quantiles: Sequence[float] | None, count: int) -> list[str]:
    if not quantiles or len(quantiles) < count + 1:
        return [f"Class {i + 1}" for i in range(count)]
    labels: list[str] = []
    for i in range(count):
        lo = float(quantiles[i]) * 100.0
        hi = float(quantiles[i + 1]) * 100.0
        lo_str = f"{lo:.0f}" if abs(lo - round(lo)) < 1e-6 else f"{lo:.1f}"
        hi_str = f"{hi:.0f}" if abs(hi - round(hi)) < 1e-6 else f"{hi:.1f}"
        if i == count - 1 or hi >= 100.0:
            labels.append(f"Class {i + 1} ({lo_str}+%)")
        else:
            labels.append(f"Class {i + 1} ({lo_str}-{hi_str}%)")
    return labels


def plot_resource_classes_map(
    classes_path: Path, regions_path: Path, output_path: Path
) -> None:
    data, transform, quantiles = _load_resource_classes(classes_path)
    valid = data[data >= 0]
    if valid.size == 0:
        raise ValueError("Resource class grid does not contain any classified cells")
    class_count = int(valid.max()) + 1

    masked = np.ma.masked_less(data, 0)
    colors = list(_subdued_colors(class_count))
    cmap = mcolors.ListedColormap(colors)
    bounds = np.arange(-0.5, class_count + 0.5, 1.0)
    norm = mcolors.BoundaryNorm(bounds, cmap.N)

    height, width = masked.shape
    lon_min, lat_min, lon_max, lat_max = array_bounds(height, width, transform)
    extent = (lon_min, lon_max, lat_min, lat_max)

    gdf = gpd.read_file(regions_path)
    if gdf.crs is None:
        logger.warning("Regions input CRS missing; assuming EPSG:4326 (WGS84)")
        gdf = gdf.set_crs(4326, allow_override=True)
    gdf = gdf.to_crs(4326)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(
        figsize=(12, 6),
        dpi=150,
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    ax.set_facecolor("#f7f9fb")

    ax.imshow(
        masked,
        origin="upper",
        extent=extent,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
        zorder=1,
    )
    ax.set_global()

    ax.add_geometries(
        gdf.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="#444444",
        linewidth=0.4,
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
    cbar = fig.colorbar(
        sm, ax=ax, fraction=0.032, pad=0.02, ticks=np.arange(class_count)
    )
    cbar.ax.set_yticklabels(_quantile_labels(quantiles, class_count))
    cbar.set_label("Resource class")

    ax.set_title("Resource Classes by Grid Cell")
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Saved resource class map to %s", output_path)


if __name__ == "__main__":
    setup_script_logging(snakemake.log[0])
    plot_resource_classes_map(
        Path(snakemake.input.classes),  # type: ignore[name-defined]
        Path(snakemake.input.regions),  # type: ignore[name-defined]
        Path(snakemake.output.pdf),  # type: ignore[name-defined]
    )
