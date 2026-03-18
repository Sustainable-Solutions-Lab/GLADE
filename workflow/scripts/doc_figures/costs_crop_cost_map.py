#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate choropleth map of median crop production costs per country.

Shows the median post-calibration crop cost (USD/ha) across all crops
for each country, giving a spatial overview of production cost levels.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import geopandas as gpd
from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    costs = pd.read_csv(snakemake.input.costs, comment="#")  # type: ignore[name-defined]
    regions = gpd.read_file(snakemake.input.regions)  # type: ignore[name-defined]

    base_year = int(snakemake.params.base_year)  # type: ignore[name-defined]
    cost_col = f"cost_usd_{base_year}_per_ha"

    # Median cost across all crops per country
    median_cost = costs.groupby("country")[cost_col].median().rename("median_cost")

    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    countries = regions.dissolve(by="country", as_index=False)
    countries = countries.merge(
        median_cost, left_on="country", right_index=True, how="left"
    )

    has_cost = countries["median_cost"].notna() & (countries["median_cost"] > 0)

    apply_doc_style()

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_global()
    ax.set_facecolor("#f7f9fb")

    # Log-scale colormap: costs span ~10-10000 USD/ha
    vmin, vmax = 30, 3000
    norm = LogNorm(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.colormaps["YlOrRd"]

    for _, row in countries[has_cost].iterrows():
        fc = cmap(norm(row["median_cost"]))
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=fc,
            edgecolor="white",
            linewidth=0.3,
            alpha=0.85,
        )

    # Countries without cost data: light grey
    for _, row in countries[~has_cost].iterrows():
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor="#e0e0e0",
            edgecolor="white",
            linewidth=0.3,
            alpha=0.5,
        )

    ax.coastlines(linewidth=0.3, color="#888888", alpha=0.3)

    for name, spine in ax.spines.items():
        if name == "geo":
            spine.set_visible(True)
            spine.set_linewidth(0.5)
            spine.set_edgecolor("#555555")
            spine.set_alpha(0.7)
        else:
            spine.set_visible(False)

    # Colorbar
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, orientation="horizontal", pad=0.05, fraction=0.046)
    cbar.set_label(
        f"Median crop cost (USD {base_year}/ha)",
        fontsize=FONT_SIZES["colorbar_label"],
    )
    # Log-scale tick labels
    ticks = [30, 100, 300, 1000, 3000]
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([str(t) for t in ticks])

    global_median = np.median(median_cost.dropna().values)
    ax.text(
        0.02,
        0.02,
        f"Global median: ${global_median:,.0f}/ha\n" f"Data: FAOSTAT producer prices",
        transform=ax.transAxes,
        fontsize=FONT_SIZES["colorbar_tick"],
        verticalalignment="bottom",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.7,
            "edgecolor": "none",
        },
    )

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved crop cost map")


if __name__ == "__main__":
    main()
