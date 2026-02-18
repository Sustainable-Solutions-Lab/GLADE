#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate validation slack overview figure for documentation.

Shows all slack categories (land, feed, food, water) as vertical bars
with excess (positive) above and shortage (negative) below the x-axis,
mirroring the food group slack plot style.
"""

import logging

import matplotlib

matplotlib.use("Agg")

from matplotlib.patches import Patch
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.plotting.plot_slack_overview import _collect_slack

logger = logging.getLogger(__name__)

# Map raw category labels to base category and direction
_DIRECTION = {
    "Land": ("Land", "excess"),
    "Feed (positive)": ("Feed", "excess"),
    "Feed (negative)": ("Feed", "shortage"),
    "Food (positive)": ("Food", "excess"),
    "Food (negative)": ("Food", "shortage"),
    "Water": ("Water", "excess"),
    "Crop production (min)": ("Crop prod.", "shortage"),
    "Animal production (min)": ("Animal prod.", "shortage"),
}


def _format_qty(qty: float, unit: str) -> str:
    if abs(qty) >= 1:
        return f"{qty:,.0f} {unit}"
    return f"{qty:.2g} {unit}"


def _plot(slack_df: pd.DataFrame, output_svg: str, output_png: str) -> None:
    apply_doc_style()

    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.45))

    if slack_df.empty:
        ax.text(
            0.5,
            0.5,
            "No slack recorded",
            ha="center",
            va="center",
            fontsize=FONT_SIZES["label"],
        )
        ax.axis("off")
        save_doc_figure(fig, output_svg, format="svg")
        save_doc_figure(fig, output_png, format="png", dpi=300)
        plt.close(fig)
        return

    # Aggregate into base categories with excess/shortage
    excess: dict[str, float] = {}
    shortage: dict[str, float] = {}
    qty_excess: dict[str, tuple[float, str]] = {}
    qty_shortage: dict[str, tuple[float, str]] = {}

    for cat, row in slack_df.iterrows():
        base, direction = _DIRECTION.get(cat, (cat, "excess"))
        qty = float(row["quantity"])
        unit = str(row["unit"])
        if direction == "excess":
            excess[base] = excess.get(base, 0.0) + float(row["cost_bnusd"])
            qty_excess[base] = (
                qty_excess.get(base, (0.0, unit))[0] + qty,
                unit,
            )
        else:
            shortage[base] = shortage.get(base, 0.0) + float(row["cost_bnusd"])
            qty_shortage[base] = (
                qty_shortage.get(base, (0.0, unit))[0] + qty,
                unit,
            )

    all_bases = list(dict.fromkeys(list(excess.keys()) + list(shortage.keys())))

    # Sort by total slack (excess + shortage) descending
    all_bases.sort(
        key=lambda b: excess.get(b, 0.0) + shortage.get(b, 0.0), reverse=True
    )

    colors = categorical_colors(all_bases)
    positions = np.arange(len(all_bases))
    bar_width = 0.7

    for i, base in enumerate(all_bases):
        exc = excess.get(base, 0.0)
        sht = shortage.get(base, 0.0)
        if exc > 0.01:
            ax.bar(
                i,
                exc,
                width=bar_width,
                color=colors[base],
                edgecolor="white",
                linewidth=0.8,
            )
            qty, unit = qty_excess[base]
            ax.text(
                i,
                exc,
                _format_qty(qty, unit),
                ha="center",
                va="bottom",
                fontsize=FONT_SIZES["annotation"],
            )
        if sht > 0.01:
            ax.bar(
                i,
                -sht,
                width=bar_width,
                color=colors[base],
                edgecolor="white",
                linewidth=0.8,
                alpha=0.45,
            )
            qty, unit = qty_shortage[base]
            ax.text(
                i,
                -sht,
                _format_qty(qty, unit),
                ha="center",
                va="top",
                fontsize=FONT_SIZES["annotation"],
            )

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(positions)
    ax.set_xticklabels(all_bases, rotation=35, ha="right", fontsize=FONT_SIZES["tick"])
    ax.set_ylabel("Slack cost (bn USD)", fontsize=FONT_SIZES["label"])
    ax.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax.grid(axis="y", alpha=0.3)
    ax.set_xlim(-0.6, len(all_bases) - 0.4)

    handles = [
        Patch(facecolor="gray", alpha=1.0, label="Excess"),
        Patch(facecolor="gray", alpha=0.45, label="Shortage"),
    ]
    ax.legend(handles=handles, fontsize=FONT_SIZES["legend"], loc="upper right")

    save_doc_figure(fig, output_svg, format="svg")
    save_doc_figure(fig, output_png, format="png", dpi=300)
    plt.close(fig)
    logger.info("Saved validation slack overview")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", snakemake.input.network)
    network = pypsa.Network(snakemake.input.network)

    slack_cost = float(snakemake.config["validation"]["slack_marginal_cost"])
    slack_df = _collect_slack(network, slack_cost)

    _plot(
        slack_df,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
