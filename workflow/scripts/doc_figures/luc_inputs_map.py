#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visualise land-use change carbon input datasets for documentation."""

import cartopy.crs as ccrs
import geopandas as gpd
from matplotlib import colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from workflow.scripts.doc_figures_config import (
    COLORMAPS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)

CO2_PER_C = 44.0 / 12.0


def _nanpercentile_safe(array: np.ndarray, percentile: float, default: float) -> float:
    if np.all(np.isnan(array)):
        return default
    return float(np.nanpercentile(array, percentile))


def _nanmax_safe(array: np.ndarray, default: float) -> float:
    if np.all(np.isnan(array)):
        return default
    return float(np.nanmax(array))


def _load_data(path: str, var: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load a variable from a NetCDF file and return array, latitudes, longitudes."""

    ds = xr.load_dataset(path)
    data = ds[var].astype(np.float32).values
    lat = ds["y"].astype(np.float32).values
    lon = ds["x"].astype(np.float32).values

    data = np.where(np.isfinite(data), data, np.nan)

    # Ensure coordinates are ascending for plotting
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[::-1, :]
    if lon[0] > lon[-1]:
        lon = lon[::-1]
        data = data[:, ::-1]

    return data, lat, lon


def _plot_panel(
    ax,
    data,
    lon,
    lat,
    *,
    cmap,
    title,
    cbar_label,
    vmin=None,
    vmax=None,
):
    """Render a single EqualEarth panel with standard overlays."""

    ax.set_global()
    ax.set_facecolor("white")

    extent = [float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max())]
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    im = ax.imshow(
        data,
        extent=extent,
        transform=ccrs.PlateCarree(),
        origin="lower",
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
    )

    ax.coastlines(linewidth=0.3, color="#666666", alpha=0.4)
    ax.set_title(title, fontsize=FONT_SIZES["title"], pad=6)

    cbar = plt.colorbar(
        im,
        ax=ax,
        orientation="horizontal",
        pad=0.02,
        fraction=0.035,
    )
    cbar.set_label(cbar_label, fontsize=FONT_SIZES["colorbar_label"])
    cbar.ax.tick_params(labelsize=FONT_SIZES["colorbar_tick"])

    return ax


def main(
    lc_masks_path: str,
    agb_path: str,
    soc_path: str,
    regrowth_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
) -> None:
    """Generate figure showcasing LUC carbon input datasets."""

    apply_doc_style()

    # Load region boundaries for context
    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    forest_frac, lat, lon = _load_data(lc_masks_path, "forest_fraction")
    agb_tc, _, _ = _load_data(agb_path, "agb_tc_per_ha")
    soc_tc, _, _ = _load_data(soc_path, "soc_0_30_tc_per_ha")
    regrowth_tc, _, _ = _load_data(regrowth_path, "regrowth_tc_per_ha_yr")

    forest_pct = np.clip(forest_frac * 100.0, 0.0, 100.0)
    agb_tc = np.clip(agb_tc, 0.0, _nanpercentile_safe(agb_tc, 99, default=1.0))
    soc_tc = np.clip(soc_tc, 0.0, _nanpercentile_safe(soc_tc, 99, default=10.0))
    regrowth_co2_raw = regrowth_tc * CO2_PER_C
    regrowth_co2 = np.clip(
        regrowth_co2_raw, 0.0, _nanpercentile_safe(regrowth_co2_raw, 99, default=0.5)
    )

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.6),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    panels = [
        (
            forest_pct,
            COLORMAPS["forest"],
            "Forest fraction (ESA CCI)",
            "Share of area covered by forest (%)",
            0.0,
            100.0,
        ),
        (
            agb_tc,
            COLORMAPS["biomass"],
            "Above-ground biomass (ESA Biomass CCI)",
            "tC per ha",
            0.0,
            _nanmax_safe(agb_tc, default=1.0),
        ),
        (
            soc_tc,
            COLORMAPS["soil"],
            "Soil organic carbon 0-30 cm (SoilGrids)",
            "tC per ha",
            0.0,
            _nanmax_safe(soc_tc, default=10.0),
        ),
        (
            regrowth_co2,
            COLORMAPS["regrowth"],
            "Natural forest regrowth potential",
            "tCO₂ per ha per year",
            0.0,
            _nanmax_safe(regrowth_co2, default=0.5),
        ),
    ]

    for ax, (data, cmap, title, label, vmin, vmax) in zip(axes.flat, panels):
        _plot_panel(
            ax,
            data,
            lon,
            lat,
            cmap=cmap,
            title=title,
            cbar_label=label,
            vmin=vmin,
            vmax=vmax,
        )
        ax.add_geometries(
            regions.geometry,
            crs=ccrs.PlateCarree(),
            facecolor="none",
            edgecolor="black",
            linewidth=0.2,
            alpha=0.3,
        )

    fig.subplots_adjust(
        left=0.04, right=0.96, top=0.94, bottom=0.14, hspace=0.28, wspace=0.18
    )

    fig.text(
        0.02,
        0.02,
        "Data sources: ESA CCI Land Cover, ESA Biomass CCI v6.0, ISRIC SoilGrids 2.0, Cook-Patton & Griscom (2020)",
        fontsize=FONT_SIZES["colorbar_tick"],
    )

    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        lc_masks_path=snakemake.input.lc_masks,  # type: ignore[name-defined]
        agb_path=snakemake.input.agb,  # type: ignore[name-defined]
        soc_path=snakemake.input.soc,  # type: ignore[name-defined]
        regrowth_path=snakemake.input.regrowth,  # type: ignore[name-defined]
        regions_path=snakemake.input.regions,  # type: ignore[name-defined]
        svg_output_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_output_path=snakemake.output.png,  # type: ignore[name-defined]
    )
