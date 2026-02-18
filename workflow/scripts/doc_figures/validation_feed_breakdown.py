#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate validation feed breakdown figure for documentation.

Shows dry-matter feed use by animal type and feed category as stacked
horizontal bars with doc figure styling.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pypsa

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.plotting.plot_feed_breakdown import (
    FEED_COLOR_OVERRIDES,
    _extract_feed_use,
    _pivot_for_plot,
)

logger = logging.getLogger(__name__)


def _plot(wide, output_svg, output_png):
    apply_doc_style()

    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))

    if wide.empty:
        ax.text(
            0.5,
            0.5,
            "No feed flows in network",
            ha="center",
            va="center",
            fontsize=FONT_SIZES["label"],
        )
        ax.axis("off")
    else:
        categories = list(wide.columns)
        colors = categorical_colors(categories, overrides=FEED_COLOR_OVERRIDES)
        left = wide.iloc[:, 0] * 0  # Series of zeros

        for cat in categories:
            values = wide[cat]
            ax.barh(
                wide.index,
                values,
                left=left,
                color=colors[cat],
                edgecolor="white",
                linewidth=0.5,
                label=cat,
            )
            left = left + values

        ax.set_xlabel("Feed use (Mt DM)", fontsize=FONT_SIZES["label"])
        ax.tick_params(axis="both", labelsize=FONT_SIZES["tick"])
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.3)
        ax.legend(
            title="Feed category",
            fontsize=FONT_SIZES["legend"],
            title_fontsize=FONT_SIZES["legend"],
            bbox_to_anchor=(0.5, -0.12),
            loc="upper center",
            ncol=3,
            borderaxespad=0,
        )

    save_doc_figure(fig, output_svg, format="svg")
    save_doc_figure(fig, output_png, format="png", dpi=300)
    plt.close(fig)
    logger.info("Saved validation feed breakdown plot")


def main() -> None:
    setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    network = pypsa.Network(snakemake.input.network)

    feed_long = _extract_feed_use(network)
    wide = _pivot_for_plot(feed_long)

    _plot(
        wide,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
