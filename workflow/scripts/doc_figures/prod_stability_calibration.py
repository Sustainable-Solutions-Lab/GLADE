#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Illustrate the production-stability L1 calibration.

Three-panel figure showing the 5x5 log-spaced grid sweep used to pick
the pair of L1 penalty costs that simultaneously bring land-use and
animal-feed deviations from the observed baseline to ~5%.

The figure uses representative mock deviation values that mirror the
magnitudes from an actual grid sweep (see ``notebooks/
prod_stability_calibration.ipynb``). The motivating figure is intended
to communicate the calibration idea; the exact values written to
``data/curated/calibration/prod_stability_l1.yaml`` come from the
calibration workflow, not from this figure.
"""

import logging

import matplotlib

matplotlib.use("Agg")

from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt
import numpy as np

from workflow.scripts.doc_figures_config import (
    COLORS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Log-spaced grid on each axis (bn USD per Mha / per Mt DM).
CROP_COSTS = np.logspace(-2, 0, 5)
ANIMAL_COSTS = np.logspace(-2, 0, 5)

# Representative land-use deviation (% of baseline total). Rows: crop_cost;
# cols: animal_cost. These values mirror an actual calibration sweep so
# that the 5% contour lands in a realistic place.
LAND_DEV_PCT = np.array(
    [
        [54.1, 50.0, 46.1, 45.7, 45.8],
        [27.3, 24.1, 22.1, 22.2, 22.1],
        [6.4, 5.7, 5.7, 5.6, 5.7],
        [2.7, 2.6, 2.7, 2.7, 2.7],
        [2.4, 2.3, 2.4, 2.4, 2.4],
    ]
)

# Representative animal feed deviation (% of baseline total).
FEED_DEV_PCT = np.array(
    [
        [26.3, 13.6, 3.3, 2.6, 2.5],
        [21.7, 9.5, 2.7, 2.5, 2.4],
        [13.5, 5.2, 2.7, 2.5, 2.4],
        [11.7, 4.8, 2.6, 2.5, 2.4],
        [10.4, 4.7, 2.7, 2.5, 2.4],
    ]
)

TARGET_PCT = 5.0


def _log_edges(values: np.ndarray) -> np.ndarray:
    """Return log-midpoint edges for pcolormesh with log-scaled axes."""
    log_v = np.log(values)
    mid = (log_v[:-1] + log_v[1:]) / 2
    edges = np.concatenate([[2 * log_v[0] - mid[0]], mid, [2 * log_v[-1] - mid[-1]]])
    return np.exp(edges)


def _interp_cost(costs: np.ndarray, devs: np.ndarray, target: float) -> float:
    """Log-linear interpolation for the cost at which dev crosses target."""
    order = np.argsort(devs)
    lo, hi = devs[order[0]], devs[order[-1]]
    if not lo <= target <= hi:
        return float("nan")
    return float(
        np.exp(
            np.interp(
                np.log(target),
                np.log(devs[order]),
                np.log(costs[order]),
            )
        )
    )


def _interp_along(xs: np.ndarray, ys: np.ndarray, x: float) -> float:
    """Log-linear interpolation of y(x) on positive values."""
    finite = np.isfinite(ys) & np.isfinite(xs) & (ys > 0)
    if not finite.any() or not np.isfinite(x) or x <= 0:
        return float("nan")
    order = np.argsort(xs[finite])
    return float(
        np.exp(
            np.interp(
                np.log(x),
                np.log(xs[finite][order]),
                np.log(ys[finite][order]),
            )
        )
    )


def compute_intersection(
    land_grid: np.ndarray,
    feed_grid: np.ndarray,
    crop_costs: np.ndarray,
    animal_costs: np.ndarray,
    target: float = TARGET_PCT,
) -> tuple[float, float]:
    """Fixed-point iteration to find the (crop_cost, animal_cost) pair
    where both deviations equal ``target``.
    """
    # For each crop_cost row, the animal_cost at which feed dev crosses target.
    feed5 = np.array(
        [
            _interp_cost(animal_costs, land_grid_row, target)
            for land_grid_row in feed_grid
        ]
    )
    # For each animal_cost column, the crop_cost at which land dev crosses target.
    land5 = np.array(
        [
            _interp_cost(crop_costs, land_grid[:, j], target)
            for j in range(land_grid.shape[1])
        ]
    )

    cc = float(crop_costs[np.nanargmin(np.abs(feed5 - np.nanmedian(feed5)))])
    for _ in range(50):
        ac = _interp_along(crop_costs, feed5, cc)
        cc_new = _interp_along(animal_costs, land5, ac)
        if not np.isfinite(cc_new):
            break
        if abs(np.log(cc_new) - np.log(cc)) < 1e-5:
            cc = cc_new
            break
        cc = cc_new
    ac = _interp_along(crop_costs, feed5, cc)
    return cc, ac


def _format_tick(v: float) -> str:
    return f"{v:g}" if v >= 1 else f"{v:.3g}"


def _plot_heatmap(
    ax,
    grid: np.ndarray,
    crop_costs: np.ndarray,
    animal_costs: np.ndarray,
    title: str,
    contour_color: str,
) -> object:
    """Render a deviation heatmap with the target contour overlaid."""
    xx, yy = np.meshgrid(animal_costs, crop_costs)
    xe, ye = _log_edges(animal_costs), _log_edges(crop_costs)

    vmin = max(grid.min(), 0.1)
    vmax = max(grid.max(), 1.0)
    mesh = ax.pcolormesh(
        xe,
        ye,
        grid,
        norm=LogNorm(vmin=vmin, vmax=vmax),
        cmap="viridis",
        shading="flat",
    )
    mid_log = np.exp((np.log(vmin) + np.log(vmax)) / 2)
    for i, cc in enumerate(crop_costs):
        for j, ac in enumerate(animal_costs):
            val = grid[i, j]
            color = "white" if val < mid_log else "black"
            ax.text(
                ac,
                cc,
                f"{val:.1f}",
                ha="center",
                va="center",
                fontsize=FONT_SIZES["annotation"],
                color=color,
            )

    cs = ax.contour(
        xx, yy, grid, levels=[TARGET_PCT], colors=contour_color, linewidths=1.5
    )
    for seg in cs.allsegs[0]:
        if len(seg) < 2:
            continue
        mid = seg[len(seg) // 2]
        ax.text(
            mid[0],
            mid[1],
            f"{TARGET_PCT:g}%",
            color=contour_color,
            fontsize=FONT_SIZES["annotation"],
            ha="center",
            va="center",
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "boxstyle": "round,pad=0.15",
                "alpha": 0.9,
            },
        )
        break

    _format_axes(ax, crop_costs, animal_costs, title)
    return mesh


def _format_axes(ax, crop_costs: np.ndarray, animal_costs: np.ndarray, title: str):
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(animal_costs)
    ax.set_xticklabels([_format_tick(v) for v in animal_costs])
    ax.set_yticks(crop_costs)
    ax.set_yticklabels([_format_tick(v) for v in crop_costs])
    ax.set_xlabel(r"$\ell^a_1$  [bn USD / Mt DM]", fontsize=FONT_SIZES["label"])
    ax.set_ylabel(r"$\ell^c_1$  [bn USD / Mha]", fontsize=FONT_SIZES["label"])
    ax.set_title(title, fontsize=FONT_SIZES["title"])
    ax.tick_params(labelsize=FONT_SIZES["tick"])
    ax.minorticks_off()
    ax.grid(False)


def _plot_intersection(
    ax,
    land_grid: np.ndarray,
    feed_grid: np.ndarray,
    crop_costs: np.ndarray,
    animal_costs: np.ndarray,
    intersection: tuple[float, float],
) -> None:
    """Overlay the two target contours with the intersection marked."""
    xx, yy = np.meshgrid(animal_costs, crop_costs)

    # Light shaded region indicating the calibration search area.
    xe, ye = _log_edges(animal_costs), _log_edges(crop_costs)
    ax.pcolormesh(
        xe,
        ye,
        np.ones_like(land_grid),
        cmap="Greys",
        vmin=0,
        vmax=3,
        shading="flat",
    )

    land_cs = ax.contour(
        xx,
        yy,
        land_grid,
        levels=[TARGET_PCT],
        colors=COLORS["primary"],
        linewidths=2.0,
    )
    feed_cs = ax.contour(
        xx,
        yy,
        feed_grid,
        levels=[TARGET_PCT],
        colors=COLORS["accent"],
        linewidths=2.0,
    )

    # Manual legend via proxy artists (contour sets don't register with ax.legend).
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color=COLORS["primary"], lw=2.0, label="Land-use 5% contour"),
        Line2D(
            [0], [0], color=COLORS["accent"], lw=2.0, label="Animal feed 5% contour"
        ),
        Line2D(
            [0],
            [0],
            marker="*",
            color="none",
            markerfacecolor="black",
            markeredgecolor="black",
            markersize=10,
            label="Calibrated intersection",
        ),
    ]

    cc, ac = intersection
    ax.plot(ac, cc, marker="*", color="black", markersize=14, zorder=10)
    ax.annotate(
        rf"$(\ell^c_1, \ell^a_1) = ({cc:.2f}, {ac:.3f})$",
        xy=(ac, cc),
        xytext=(12, 12),
        textcoords="offset points",
        fontsize=FONT_SIZES["annotation"],
        bbox={
            "facecolor": "white",
            "edgecolor": "#cccccc",
            "boxstyle": "round,pad=0.25",
        },
    )

    ax.legend(
        handles=handles,
        fontsize=FONT_SIZES["legend"],
        loc="lower left",
        frameon=True,
        facecolor="white",
        edgecolor="#cccccc",
    )

    _format_axes(ax, crop_costs, animal_costs, "Contour intersection")

    # Silence unused-variable warnings from the typed contour references.
    _ = land_cs, feed_cs


def main() -> None:
    setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    apply_doc_style()

    cc, ac = compute_intersection(LAND_DEV_PCT, FEED_DEV_PCT, CROP_COSTS, ANIMAL_COSTS)
    logger.info("Illustrative intersection: crop_cost=%.3f, animal_cost=%.3f", cc, ac)

    fig, axes = plt.subplots(
        1, 3, figsize=(FIGURE_WIDTH, FIGURE_WIDTH / 2.7), constrained_layout=True
    )

    mesh_land = _plot_heatmap(
        axes[0],
        LAND_DEV_PCT,
        CROP_COSTS,
        ANIMAL_COSTS,
        "Land-use deviation [%]",
        contour_color=COLORS["accent"],
    )
    mesh_feed = _plot_heatmap(
        axes[1],
        FEED_DEV_PCT,
        CROP_COSTS,
        ANIMAL_COSTS,
        "Animal feed deviation [%]",
        contour_color=COLORS["accent"],
    )
    _plot_intersection(
        axes[2], LAND_DEV_PCT, FEED_DEV_PCT, CROP_COSTS, ANIMAL_COSTS, (cc, ac)
    )

    for ax, mesh in zip(axes[:2], [mesh_land, mesh_feed]):
        cb = fig.colorbar(mesh, ax=ax, shrink=0.85, pad=0.04)
        cb.ax.tick_params(labelsize=FONT_SIZES["colorbar_tick"])
        cb.set_label("% of baseline", fontsize=FONT_SIZES["colorbar_label"])

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved prod_stability_calibration figure")


if __name__ == "__main__":
    main()
