#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate feed efficiency calibration multiplier box plot.

Shows the distribution of calibration multipliers across countries,
grouped by feed category.  A reference line at 1.0 marks "no adjustment".
"""

import logging

import matplotlib

matplotlib.use("Agg")

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

    # Aggregate to mean multiplier per (country, feed_category) to collapse
    # the product dimension, then drop entries with no adjustment.
    agg = cal.groupby(["country", "feed_category"], as_index=False)["multiplier"].mean()
    agg = agg[agg["multiplier"] != 1.0]

    apply_doc_style()
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.45))

    if agg.empty:
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
        categories = sorted(agg["feed_category"].unique())
        data = [
            agg.loc[agg["feed_category"] == cat, "multiplier"].values
            for cat in categories
        ]

        bp = ax.boxplot(
            data,
            vert=False,
            tick_labels=categories,
            patch_artist=True,
            widths=0.6,
            medianprops={"color": "black", "linewidth": 1.5},
        )
        for box in bp["boxes"]:
            box.set_facecolor("#81b29a")
            box.set_alpha(0.7)

        ax.axvline(
            1.0, color="grey", linestyle="--", linewidth=0.8, label="No adjustment"
        )
        ax.set_xlabel("Calibration multiplier", fontsize=FONT_SIZES["label"])
        ax.tick_params(axis="both", labelsize=FONT_SIZES["tick"])
        ax.grid(axis="x", alpha=0.3)
        ax.legend(fontsize=FONT_SIZES["legend"], loc="lower right")

    fig.subplots_adjust(left=0.25)

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved feed efficiency calibration plot")


if __name__ == "__main__":
    main()
