# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot dry-matter feed use by animal and source of supply.

Reads ``feed_by_source.parquet`` (produced by ``extract_statistics``)
and aggregates the per-(animal, feed_category, source) breakdown to
(animal, source) for the stacked horizontal-bar plot. All values are on
a dry-matter basis (the model's feed buses are uniformly DM).
"""

import logging
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("pdf")
import matplotlib.pyplot as plt

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

logger = logging.getLogger(__name__)


# Order used for both legend and bar stacking. Sources not listed here
# are appended at the right of the bar in arbitrary order.
SOURCE_ORDER = [
    "Grassland",
    "Fodder crops",
    "Exog. forage (calibration)",
    "Crop residues",
    "Exog. browse / leaves",
    "Oilseed cakes",
    "Exog. protein (calibration)",
    "Food by-products",
    "Exog. swill",
    "Grains",
    "Exog. (other)",
]

SOURCE_COLOR_OVERRIDES = {
    "Grassland": "#4f9d69",
    "Fodder crops": "#8fbf73",
    "Exog. forage (calibration)": "#c3e6a8",
    "Crop residues": "#8c6b4f",
    "Exog. browse / leaves": "#bfa07a",
    "Oilseed cakes": "#b8de6f",
    "Exog. protein (calibration)": "#d6eaa2",
    "Food by-products": "#7b6ba8",
    "Exog. swill": "#a999c8",
    "Grains": "#d95f02",
    "Exog. (other)": "#999999",
}


def _pivot_for_plot(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to animal x source wide format, ordered by total intake."""
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(
        index="animal",
        columns="source",
        values="mt_dm",
        aggfunc="sum",
        fill_value=0.0,
    )
    totals = wide.sum(axis=1).sort_values(ascending=False)
    wide = wide.loc[totals.index]
    ordered = [c for c in SOURCE_ORDER if c in wide.columns]
    extras = [c for c in wide.columns if c not in ordered]
    return wide[ordered + extras]


def _plot_feed_breakdown(wide: pd.DataFrame, output_pdf: Path) -> None:
    """Render stacked horizontal bars of feed use by source."""
    plt.figure(figsize=(10, 6))
    ax = plt.gca()

    if wide.empty:
        ax.text(0.5, 0.5, "No feed flows in network", ha="center", va="center")
        ax.axis("off")
    else:
        sources = list(wide.columns)
        colors = categorical_colors(sources, overrides=SOURCE_COLOR_OVERRIDES)
        left = pd.Series(0.0, index=wide.index)
        for src in sources:
            values = wide[src]
            ax.barh(
                wide.index,
                values,
                left=left,
                color=colors[src],
                edgecolor="black",
                linewidth=0.4,
                label=src,
            )
            left = left + values
        ax.set_xlabel("Mt of DM")
        ax.set_ylabel("Animal")
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.3)
        ax.legend(
            title="Feed source",
            bbox_to_anchor=(1.02, 1),
            loc="upper left",
            borderaxespad=0,
        )
        plt.tight_layout()

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote feed breakdown plot to %s", output_pdf)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    feed_by_source = pd.read_parquet(snakemake.input.feed_by_source)

    csv_path = Path(snakemake.output.csv)
    pdf_path = Path(snakemake.output.pdf)

    feed_by_source.to_csv(csv_path, index=False)
    logger.info("Wrote feed breakdown table to %s", csv_path)

    wide = _pivot_for_plot(feed_by_source)
    _plot_feed_breakdown(wide, pdf_path)
