#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Plot a world map of the average country yield fraction across all crops.

Inputs:
 - regions: GeoJSON of model regions with a 'country' column
 - csv: CSV with columns country,fraction_achieved_mean

Output:
 - PDF map at snakemake.output.pdf
"""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("pdf")
from matplotlib.colors import TwoSlopeNorm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging


def plot_map(regions_path: str, csv_path: str, output_path: str) -> None:
    gdf = gpd.read_file(regions_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)

    # Dissolve to country polygons
    country_col = "country" if "country" in gdf.columns else "GID_0"
    if country_col == "GID_0" and "country" not in gdf.columns:
        gdf = gdf.rename(columns={"GID_0": "country"})
        country_col = "country"

    gdf_country = gdf[[country_col, "geometry"]].dissolve(
        by=country_col, as_index=False
    )

    df = pd.read_csv(csv_path)
    if "country" not in df.columns or "fraction_achieved_mean" not in df.columns:
        raise ValueError(
            "Input CSV must contain 'country' and 'fraction_achieved_mean'"
        )

    # Join
    gdf_country = gdf_country.merge(df, on="country", how="left")

    # Project to Equal Earth for display
    gdf_plot = gdf_country.to_crs("+proj=eqearth")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)

    # Color normalization centered at 1.0 (no gap), robust min/max
    vals = gdf_plot["fraction_achieved_mean"].to_numpy(dtype=float)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        vmin, vmax = 0.0, 2.0
    else:
        lo = float(np.percentile(finite, 2))
        hi = float(np.percentile(finite, 98))
        # ensure 1.0 sits within bounds
        vmin, vmax = min(lo, 1.0), max(hi, 1.0)

    norm = TwoSlopeNorm(vmin=vmin, vcenter=1.0, vmax=vmax)
    cmap = plt.colormaps["RdBu_r"].copy()

    # Base
    gdf_plot.plot(
        ax=ax,
        column="fraction_achieved_mean",
        cmap=cmap,
        norm=norm,
        linewidth=0.3,
        edgecolor="#666666",
        missing_kwds={"color": "#f0f0f0", "edgecolor": "#d0d0d0", "hatch": ".."},
    )

    ax.set_axis_off()
    ax.set_title("Average yield fraction achieved (all crops)")
    cbar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(cbar, ax=ax, shrink=0.8, pad=0.02)
    cb.set_label("Actual / Potential (mean across crops)")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])
    plot_map(snakemake.input.regions, snakemake.input.csv, snakemake.output.pdf)
