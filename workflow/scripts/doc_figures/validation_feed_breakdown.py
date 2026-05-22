#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate validation feed breakdown figure for documentation.

Shows dry-matter feed use by animal type and supply source as stacked
horizontal bars with doc figure styling. Reads from the
``feed_by_source.parquet`` produced by ``extract_statistics``.
"""

import logging

import matplotlib
import pandas as pd

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.plotting.plot_feed_breakdown import (
    SOURCE_COLOR_OVERRIDES,
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
        sources = list(wide.columns)
        colors = categorical_colors(sources, overrides=SOURCE_COLOR_OVERRIDES)
        left = wide.iloc[:, 0] * 0

        for src in sources:
            values = wide[src]
            ax.barh(
                wide.index,
                values,
                left=left,
                color=colors[src],
                edgecolor="white",
                linewidth=0.5,
                label=src,
            )
            left = left + values

        ax.set_xlabel("Feed use (Mt DM)", fontsize=FONT_SIZES["label"])
        ax.tick_params(axis="both", labelsize=FONT_SIZES["tick"])
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.3)
        ax.legend(
            title="Feed source",
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

    feed_by_source = pd.read_parquet(snakemake.input.feed_by_source)
    wide = _pivot_for_plot(feed_by_source)

    _plot(
        wide,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
