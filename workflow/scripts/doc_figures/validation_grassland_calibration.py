#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate grassland forage calibration map for documentation.

Shows yield_correction by country as a choropleth with a diverging colormap
centred at 1.0.  Countries with exogenous_forage_mt_dm > 0 are marked with
hatching.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import geopandas as gpd
from matplotlib.colors import TwoSlopeNorm
import matplotlib.pyplot as plt
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

    cal = pd.read_csv(snakemake.input.calibration, comment="#")  # type: ignore[name-defined]
    regions = gpd.read_file(snakemake.input.regions)  # type: ignore[name-defined]

    if regions.crs is None:
        regions = regions.set_crs(4326, allow_override=True)
    else:
        regions = regions.to_crs(4326)

    countries = regions.dissolve(by="country", as_index=False)

    # Merge calibration data
    countries = countries.merge(cal, left_on="country", right_on="country", how="left")

    # Countries without calibration data: no grassland, leave as NaN
    has_cal = countries["yield_correction"].notna()

    apply_doc_style()

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )
    ax.set_global()
    ax.set_facecolor("#f7f9fb")

    # Diverging colormap centred at 1.0
    vmin = 0.0
    vmax = max(1.2, cal["yield_correction"].max() if not cal.empty else 1.2)
    norm = TwoSlopeNorm(vcenter=1.0, vmin=vmin, vmax=vmax)
    cmap = plt.colormaps["RdYlGn"]

    # Plot countries with calibration data
    for _, row in countries[has_cal].iterrows():
        fc = cmap(norm(row["yield_correction"]))
        hatch = "..." if row.get("exogenous_forage_mt_dm", 0) > 0 else None
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=fc,
            edgecolor="white",
            linewidth=0.3,
            alpha=0.85,
            hatch=hatch,
        )

    # Countries without calibration: light grey
    for _, row in countries[~has_cal].iterrows():
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
    cbar.set_label("Yield correction factor", fontsize=FONT_SIZES["colorbar_label"])

    # Hatching legend
    n_exogenous = int(
        (countries[has_cal]["exogenous_forage_mt_dm"] > 0).sum()
        if "exogenous_forage_mt_dm" in countries.columns
        else 0
    )
    n_reduced = int((countries[has_cal]["yield_correction"] < 1.0).sum())
    ax.text(
        0.02,
        0.02,
        f"{n_reduced} countries with reduced yields\n"
        f"{n_exogenous} countries with exogenous forage (hatched)",
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
    logger.info("Saved grassland forage calibration map")


if __name__ == "__main__":
    main()
