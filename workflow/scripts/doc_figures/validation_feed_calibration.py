#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate feed efficiency calibration multiplier strip plot.

Shows individual country calibration multipliers grouped by feed category,
with dot size proportional to production volume.  The most significant
data points are labelled with ISO3 country codes.
"""

import logging

import matplotlib

matplotlib.use("Agg")

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

    cal = pd.read_csv(snakemake.input.calibration, comment="#")  # type: ignore[name-defined]
    prod = pd.read_csv(snakemake.input.production)  # type: ignore[name-defined]

    # Merge production volumes onto calibration rows.
    merged = cal.merge(
        prod[["country", "product", "production_mt"]],
        on=["country", "product"],
        how="left",
    )
    merged["production_mt"] = merged["production_mt"].fillna(0)

    # Aggregate to (country, feed_category): production-weighted mean
    # multiplier with total production for sizing.
    merged["weighted"] = merged["multiplier"] * merged["production_mt"]
    agg = (
        merged.groupby(["country", "feed_category"])
        .agg(
            weighted_sum=("weighted", "sum"),
            prod_sum=("production_mt", "sum"),
            mean_mult=("multiplier", "mean"),
        )
        .reset_index()
    )
    agg["multiplier"] = np.where(
        agg["prod_sum"] > 0,
        agg["weighted_sum"] / agg["prod_sum"],
        agg["mean_mult"],
    )
    agg["production_mt"] = agg["prod_sum"]

    # Count adjusted vs total countries per category (before filtering).
    n_total = agg.groupby("feed_category")["country"].nunique()
    n_adjusted = (
        agg[agg["multiplier"] != 1.0].groupby("feed_category")["country"].nunique()
    )

    # Keep only entries that received an adjustment.
    plot_data = agg[agg["multiplier"] != 1.0].copy()

    apply_doc_style()

    categories = sorted(agg["feed_category"].unique())
    y_spacing = 1.4
    cat_to_y = {cat: i * y_spacing for i, cat in enumerate(categories)}

    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.55))

    if plot_data.empty:
        ax.text(
            0.5,
            0.5,
            "All multipliers are 1.0 (no calibration needed)",
            ha="center",
            va="center",
            fontsize=FONT_SIZES["label"],
        )
        ax.axis("off")
    else:
        # Dot sizes: linear in area (area ∝ production).
        min_size, max_size = 8, 250
        prod_max = plot_data["production_mt"].max()
        if prod_max > 0:
            plot_data["size"] = min_size + (max_size - min_size) * (
                plot_data["production_mt"] / prod_max
            )
        else:
            plot_data["size"] = min_size

        # Jitter y-positions to reduce overlap.
        rng = np.random.default_rng(42)
        plot_data["y"] = plot_data["feed_category"].map(cat_to_y) + rng.uniform(
            -0.3, 0.3, len(plot_data)
        )

        # Scatter strip plot.
        ax.scatter(
            plot_data["multiplier"],
            plot_data["y"],
            s=plot_data["size"],
            c="#81b29a",
            alpha=0.55,
            edgecolors="#3b745f",
            linewidths=0.4,
            zorder=3,
        )

        # Reference line with direct annotation (no legend).
        ax.axvline(1.0, color="grey", linestyle="--", linewidth=0.8, zorder=1)
        ax.text(
            1.0,
            cat_to_y[categories[-1]] + 0.58,
            "no adjustment",
            ha="center",
            va="bottom",
            fontsize=FONT_SIZES["annotation"],
            color="grey",
            fontstyle="italic",
        )

        # Label top producers per category.  Place labels left of points
        # near the right edge, right of points otherwise; stagger vertically
        # within each category to reduce overlap.
        n_labels = 3
        vert_offsets = [5, -8, -3]
        for cat in categories:
            cat_data = plot_data[plot_data["feed_category"] == cat]
            if cat_data.empty:
                continue
            top = cat_data.nlargest(min(n_labels, len(cat_data)), "production_mt")
            for i, (_, row) in enumerate(top.iterrows()):
                dy = vert_offsets[i % len(vert_offsets)]
                if row["multiplier"] > 1.75:
                    dx, ha = -8, "right"
                else:
                    dx, ha = 6, "left"
                ax.annotate(
                    row["country"],
                    (row["multiplier"], row["y"]),
                    textcoords="offset points",
                    xytext=(dx, dy),
                    ha=ha,
                    fontsize=FONT_SIZES["annotation"] - 0.5,
                    color="#333",
                    zorder=4,
                )

        # Axes — extend x-range slightly so right-edge dots aren't clipped.
        xmin, xmax = ax.get_xlim()
        ax.set_xlim(xmin, xmax + 0.05)
        y_positions = [cat_to_y[cat] for cat in categories]
        ax.set_yticks(y_positions)
        ax.set_yticklabels(categories)
        ax.set_xlabel("Calibration multiplier", fontsize=FONT_SIZES["label"])
        ax.tick_params(axis="y", length=0, labelsize=FONT_SIZES["tick"])
        ax.tick_params(axis="x", labelsize=FONT_SIZES["tick"])
        ax.set_ylim(-0.6, y_positions[-1] + 0.6)

        # Alternating background bands instead of grid lines.
        ax.yaxis.grid(False)
        ax.xaxis.grid(False)
        half = y_spacing / 2
        for i, cat in enumerate(categories):
            if i % 2 == 0:
                ax.axhspan(
                    cat_to_y[cat] - half,
                    cat_to_y[cat] + half,
                    color="#f5f5f5",
                    zorder=0,
                )

        # Right-side: #adjusted / #total per category.
        ax2 = ax.twinx()
        ax2.grid(False)
        ax2.set_ylim(ax.get_ylim())
        ax2.set_yticks(y_positions)
        labels = [
            f"{n_adjusted.get(cat, 0)}/{n_total.get(cat, 0)}" for cat in categories
        ]
        ax2.set_yticklabels(labels)
        right_tick_pad = 4
        ax2.tick_params(
            axis="y",
            length=0,
            pad=right_tick_pad,
            labelsize=FONT_SIZES["annotation"],
            colors="#666",
        )
        # Small header just above the topmost right tick label.
        # Reuse the tick-label transform so x alignment exactly matches
        # the right-side adjusted/total annotations.
        tick_label_trans, _, tick_label_ha = ax2.get_yaxis_text2_transform(
            right_tick_pad
        )
        ax2.text(
            1.0,
            y_positions[-1] + y_spacing * 0.45,
            "countries\nadjusted",
            transform=tick_label_trans,
            ha=tick_label_ha,
            va="bottom",
            fontsize=FONT_SIZES["annotation"] - 1,
            color="#999",
            fontstyle="italic",
            linespacing=1.1,
        )
        ax2.spines["right"].set_visible(False)

        # --- Bubble size legend (nested circles, bottom-aligned) ---
        def _prod_to_size(p):
            return min_size + (max_size - min_size) * (p / prod_max)

        ref_values = [5, 50, 200]
        ref_values = [v for v in ref_values if v <= prod_max * 1.1]

        from matplotlib.patches import Circle
        from mpl_toolkits.axes_grid1.inset_locator import inset_axes

        leg_ax = inset_axes(
            ax,
            width="15%",
            height="30%",
            bbox_to_anchor=(0.08, -0.05, 1, 1),
            bbox_transform=ax.transAxes,
            loc="lower left",
        )
        leg_ax.set_aspect("equal")
        leg_ax.axis("off")

        # Convert scatter size (points²) to a radius in data units.
        # We pick an arbitrary scale factor so the circles look right
        # inside the inset; the exact mapping doesn't matter since we
        # only need consistent relative sizes.
        scale = 0.15
        radii = {v: np.sqrt(_prod_to_size(v)) * scale for v in ref_values}
        max_r = radii[ref_values[-1]]
        cx = 0.0  # circle centre x

        for v in ref_values:
            r = radii[v]
            # Bottom-align: centre y = r (so bottom edge is at y=0).
            circ = Circle(
                (cx, r),
                r,
                fill=False,
                edgecolor="#3b745f",
                linewidth=0.6,
                zorder=3,
            )
            leg_ax.add_patch(circ)
            # Dashed leader from top of circle to the label column.
            top_y = 2 * r
            label_x = max_r * 1.4
            leg_ax.plot(
                [cx, label_x],
                [top_y, top_y],
                color="#aaa",
                linewidth=0.4,
                linestyle=":",
                zorder=2,
            )
            leg_ax.text(
                label_x + 0.1,
                top_y,
                f"{v} Mt",
                fontsize=FONT_SIZES["annotation"] - 1,
                color="#555",
                va="center",
            )

        leg_ax.text(
            cx,
            max_r * 2 + max_r * 0.35,
            "Production",
            fontsize=FONT_SIZES["annotation"] - 1,
            color="#555",
            ha="center",
            va="bottom",
        )
        pad = max_r * 0.15
        leg_ax.set_xlim(-max_r - pad, max_r * 4.0)
        leg_ax.set_ylim(-pad, max_r * 2 + pad + max_r * 0.6)

    fig.subplots_adjust(left=0.22, right=0.90)

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved feed efficiency calibration plot")


if __name__ == "__main__":
    main()
