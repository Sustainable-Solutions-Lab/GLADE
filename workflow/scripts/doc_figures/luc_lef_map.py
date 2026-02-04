#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visualise annualised land-use change emission factors (LEFs)."""

import cartopy.crs as ccrs
import geopandas as gpd
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


def _load_lef(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    ds = xr.load_dataset(path)
    lef = ds["LEF_tCO2_per_ha_yr"].astype(np.float32)
    lat = ds["y"].astype(np.float32).values
    lon = ds["x"].astype(np.float32).values
    uses = [str(u) for u in lef.coords["use"].values]

    data = lef.values
    data = np.where(np.isfinite(data), data, np.nan)

    if lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[:, ::-1, :]
    if lon[0] > lon[-1]:
        lon = lon[::-1]
        data = data[:, :, ::-1]

    return data, lat, lon, uses


def _symmetric_limits(arrays: list[np.ndarray], percentile: float = 99.0) -> float:
    finite_vals = np.concatenate(
        [a[np.isfinite(a)] for a in arrays if np.any(np.isfinite(a))]
    )
    if finite_vals.size == 0:
        return 1.0
    limit = float(np.nanpercentile(np.abs(finite_vals), percentile))
    return max(limit, 0.1)


def main(
    annualized_path: str,
    regions_path: str,
    svg_output_path: str,
    png_output_path: str,
) -> None:
    apply_doc_style()

    lef_cube, lat, lon, uses = _load_lef(annualized_path)

    regions = gpd.read_file(regions_path)
    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    use_to_label = {
        "cropland": "Cropland expansion emission factor",
        "spared": "Spared land sequestration factor",
    }

    panels = []
    for use, data in zip(uses, lef_cube):
        if use in use_to_label:
            panels.append((use, data))

    vmax = _symmetric_limits([arr for _, arr in panels])

    ncols = 2
    nrows = 1
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    axes = [axes] if nrows == 1 and ncols == 1 else list(axes)

    extent = [float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max())]

    for ax, (use, data) in zip(axes, panels):
        ax.set_global()
        ax.set_facecolor("white")
        im = ax.imshow(
            data,
            extent=extent,
            transform=ccrs.PlateCarree(),
            origin="lower",
            cmap=COLORMAPS["diverging"],
            vmin=-vmax,
            vmax=vmax,
            interpolation="nearest",
        )
        ax.coastlines(linewidth=0.3, color="#666666", alpha=0.4)
        ax.add_geometries(
            regions.geometry,
            crs=ccrs.PlateCarree(),
            facecolor="none",
            edgecolor="black",
            linewidth=0.2,
            alpha=0.3,
        )
        ax.set_title(
            use_to_label.get(use, use.title()), fontsize=FONT_SIZES["title"], pad=8
        )

    # If there are fewer panels than axes (e.g. last panel missing), hide extras
    for ax in axes[len(panels) :]:
        ax.set_visible(False)

    fig.subplots_adjust(
        left=0.03,
        right=0.97,
        top=0.90,
        bottom=0.18,
        wspace=0.12,
    )

    cbar = fig.colorbar(
        im,
        ax=axes,
        orientation="horizontal",
        fraction=0.045,
        pad=0.08,
    )
    cbar.set_label(
        "Land-use emission factor (tCO₂ per ha per year)",
        fontsize=FONT_SIZES["colorbar_label"],
    )
    cbar.ax.tick_params(labelsize=FONT_SIZES["colorbar_tick"])

    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        annualized_path=snakemake.input.annualized,  # type: ignore[name-defined]
        regions_path=snakemake.input.regions,  # type: ignore[name-defined]
        svg_output_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_output_path=snakemake.output.png,  # type: ignore[name-defined]
    )
