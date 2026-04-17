#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visualise multiple-cropping zones and regional potential."""

from collections.abc import Sequence

import cartopy.crs as ccrs
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from pyproj import Geod
import rasterio
import rasterio.enums

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)

ZONE_LABELS = {
    1: "No cropping",
    2: "Single cropping",
    3: "Limited double",
    4: "Double (no rice)",
    5: "Double + rice",
    6: "Double rice",
    7: "Triple cropping",
    8: "Triple rice",
}

# Zones that allow more than one sequential crop (ignoring relay-only nuances)
MULTI_CROPPING_ZONES: tuple[int, ...] = (3, 4, 5, 6, 7, 8)


def _calculate_all_cell_areas(src: rasterio.DatasetReader) -> np.ndarray:
    """Return per-pixel area in hectares for a geographic raster."""

    pixel_width_deg = abs(src.transform.a)
    pixel_height_deg = abs(src.transform.e)
    rows, cols = src.height, src.width
    left, bottom, _right, top = src.bounds

    lats = np.linspace(top - pixel_height_deg / 2, bottom + pixel_height_deg / 2, rows)
    geod = Geod(ellps="WGS84")
    areas_ha = np.zeros(rows, dtype=np.float32)

    for idx, lat in enumerate(lats):
        lat_top = lat + pixel_height_deg / 2
        lat_bottom = lat - pixel_height_deg / 2
        lon_left = left
        lon_right = left + pixel_width_deg
        lons = [lon_left, lon_right, lon_right, lon_left, lon_left]
        lats_poly = [lat_bottom, lat_bottom, lat_top, lat_top, lat_bottom]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats_poly)
        areas_ha[idx] = abs(area_m2) / 10000.0

    return np.repeat(areas_ha[:, np.newaxis], cols, axis=1)


def _load_zone_data(zone_raster_path: str) -> dict[str, object]:
    """Load zone raster, compute cell areas, and prepare displays."""

    with rasterio.open(zone_raster_path) as src:
        zone_full = src.read(1).astype(float)
        nodata = src.nodata
        bounds = src.bounds
        crs = src.crs

        # Downsample for display to keep figure lightweight
        downscale_factor = 4
        display = src.read(
            1,
            out_shape=(
                src.height // downscale_factor,
                src.width // downscale_factor,
            ),
            resampling=rasterio.enums.Resampling.nearest,
        ).astype(float)

        cell_area_ha = _calculate_all_cell_areas(src)  # hectares

    if nodata is not None:
        zone_full = np.where(zone_full == nodata, np.nan, zone_full)
        display = np.where(display == nodata, np.nan, display)

    return {
        "zone_full": zone_full,
        "zone_display": display,
        "cell_area_ha": cell_area_ha,
        "bounds": bounds,
        "crs": crs,
    }


def _compute_region_fraction(
    zones: np.ndarray,
    cell_area: np.ndarray,
    bounds,
    crs,
    regions: gpd.GeoDataFrame,
    multi_zone_codes: Sequence[int],
) -> gpd.GeoDataFrame:
    """Aggregate fraction of area with multiple-cropping zones per region."""

    # Valid land pixels: zone codes > 0 (exclude oceans/masked)
    valid_mask = np.isfinite(zones) & (zones > 0)
    total_area = np.where(valid_mask, cell_area, 0.0)

    multi_mask = np.isin(zones, multi_zone_codes)
    multi_area = np.where(multi_mask, cell_area, 0.0)

    # Prepare raster sources for exact extraction
    kwargs = {
        "xmin": bounds.left,
        "ymin": bounds.bottom,
        "xmax": bounds.right,
        "ymax": bounds.top,
        "srs_wkt": crs.to_wkt() if crs else None,
        "nodata": np.nan,
    }
    total_source = NumPyRasterSource(total_area, **kwargs)
    multi_source = NumPyRasterSource(multi_area, **kwargs)

    regions_for_extract = regions.reset_index()

    total_stats = exact_extract(
        total_source,
        regions_for_extract,
        ["sum"],
        include_cols=["region"],
        output="pandas",
    ).rename(columns={"sum": "total_area_ha"})
    multi_stats = exact_extract(
        multi_source,
        regions_for_extract,
        ["sum"],
        include_cols=["region"],
        output="pandas",
    ).rename(columns={"sum": "multi_area_ha"})

    merged = total_stats.merge(multi_stats, on="region", how="left")
    merged["multi_area_ha"] = merged["multi_area_ha"].fillna(0.0)
    merged["total_area_ha"] = merged["total_area_ha"].replace(0, np.nan)
    merged["fraction"] = merged["multi_area_ha"] / merged["total_area_ha"]
    merged["fraction"] = merged["fraction"].replace([np.inf, -np.inf], np.nan)
    merged["fraction"] = merged["fraction"].clip(lower=0.0, upper=1.0)

    return regions.merge(merged[["region", "fraction"]], on="region", how="left")


def _build_zone_colormap() -> tuple[mcolors.ListedColormap, mcolors.BoundaryNorm]:
    base = plt.colormaps["YlGn"]
    colors = base(np.linspace(0.2, 1.0, len(ZONE_LABELS)))
    colors[0] = np.array([0.88, 0.88, 0.88, 1.0])  # no cropping → light grey
    cmap = mcolors.ListedColormap(colors, name="multi_cropping_zones_seq")
    bounds = np.arange(0.5, len(ZONE_LABELS) + 1.5, 1.0)
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    return cmap, norm


def main(
    zone_raster_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
    water_supply: str = "rainfed",
):
    """Generate the multi-cropping potential figure."""

    apply_doc_style()

    data = _load_zone_data(zone_raster_path)
    zones = data["zone_full"]
    zones_display = data["zone_display"]
    cell_area = data["cell_area_ha"]
    bounds = data["bounds"]
    crs = data["crs"]

    # Prepare display raster (mask oceans / missing)
    zones_display = np.where(zones_display <= 0, np.nan, zones_display)

    # Load regions
    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    regions_with_fraction = _compute_region_fraction(
        zones=zones,
        cell_area=cell_area,
        bounds=bounds,
        crs=crs,
        regions=regions,
        multi_zone_codes=MULTI_CROPPING_ZONES,
    )

    cmap_zones, norm_zones = _build_zone_colormap()

    fig, axes = plt.subplots(
        nrows=2,
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.85),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    fig.subplots_adjust(hspace=0.3)

    # Top panel: multi-cropping zones
    ax_top = axes[0]
    ax_top.set_global()
    ax_top.set_facecolor("white")

    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    im = ax_top.imshow(
        zones_display,
        cmap=cmap_zones,
        norm=norm_zones,
        extent=extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
    )

    ax_top.add_geometries(
        regions.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="black",
        linewidth=0.3,
        alpha=0.25,
    )
    ax_top.coastlines(linewidth=0.3, color="#666666", alpha=0.4)
    supply_label = "Rain-fed" if water_supply == "rainfed" else "Irrigated"
    ax_top.set_title(
        f"GAEZ Multi-Cropping Zones ({supply_label})",
        fontsize=FONT_SIZES["title"],
        pad=10,
    )

    cbar_top = fig.colorbar(
        im,
        ax=ax_top,
        orientation="horizontal",
        pad=0.05,
        fraction=0.045,
    )
    ticks = np.arange(1, 1 + len(ZONE_LABELS))
    cbar_top.set_ticks(ticks)
    cbar_top.set_ticklabels([ZONE_LABELS[t] for t in ticks], rotation=20, ha="right")
    cbar_top.ax.tick_params(labelsize=FONT_SIZES["colorbar_tick"])

    # Bottom panel: fraction of land suitable for multiple cropping
    ax_bottom = axes[1]
    ax_bottom.set_global()
    ax_bottom.set_facecolor("white")

    frac_min, frac_max = 0.0, 1.0
    regions_plot = regions_with_fraction.copy()
    regions_plot["fraction"] = regions_plot["fraction"].fillna(0.0)

    regions_plot.plot(
        column="fraction",
        ax=ax_bottom,
        cmap="YlGn",
        vmin=frac_min,
        vmax=frac_max,
        edgecolor="white",
        linewidth=0.3,
        transform=ccrs.PlateCarree(),
        legend=False,
        missing_kwds={"color": "#f0f0f0", "alpha": 0.4},
    )
    ax_bottom.coastlines(linewidth=0.3, color="#666666", alpha=0.4)
    ax_bottom.set_title(
        f"Share of Region with Climate Suitable for Sequential Multi-Cropping ({supply_label})",
        fontsize=FONT_SIZES["title"],
        pad=10,
    )

    sm = plt.cm.ScalarMappable(
        cmap="YlGn", norm=plt.Normalize(vmin=frac_min, vmax=frac_max)
    )
    sm.set_array([])
    cbar_bottom = plt.colorbar(
        sm,
        ax=ax_bottom,
        orientation="horizontal",
        pad=0.05,
        fraction=0.045,
    )
    cbar_bottom.set_label(
        "Fraction of region area", fontsize=FONT_SIZES["colorbar_label"]
    )

    # Save outputs
    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    water_supply = getattr(snakemake.params, "water_supply", "rainfed")  # type: ignore[name-defined]
    main(
        zone_raster_path=snakemake.input.zone_raster,  # type: ignore[name-defined]
        regions_path=snakemake.input.regions,  # type: ignore[name-defined]
        svg_output_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_output_path=snakemake.output.png,  # type: ignore[name-defined]
        water_supply=str(water_supply).lower(),
    )
