#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate boxen plot of crop cost distributions across countries.

Shows the distribution of FAOSTAT-derived production costs (USD/ha) for
each crop, grouped by crop category and colored accordingly. The boxen
(letter-value) plot reveals distributional shape better than standard
box plots for the many-country dataset.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Crop group definitions and colors (matching config/default.yaml plotting.crop_groups)
CROP_GROUPS = [
    (
        "Cereals",
        "#E6AB02",
        [
            "wheat",
            "dryland-rice",
            "wetland-rice",
            "maize",
            "barley",
            "oat",
            "rye",
            "sorghum",
            "buckwheat",
            "foxtail-millet",
            "pearl-millet",
        ],
    ),
    (
        "Legumes",
        "#666666",
        [
            "soybean",
            "dry-pea",
            "chickpea",
            "cowpea",
            "gram",
            "phaseolus-bean",
            "pigeonpea",
        ],
    ),
    (
        "Roots & tubers",
        "#A6761D",
        [
            "white-potato",
            "sweet-potato",
            "cassava",
            "yam",
        ],
    ),
    ("Vegetables", "#1B9E77", ["tomato", "carrot", "onion", "cabbage"]),
    ("Fruits", "#D95F02", ["banana", "watermelon", "mango", "citrus", "coconut"]),
    (
        "Oilseeds",
        "#7570B3",
        [
            "sunflower",
            "rapeseed",
            "groundnut",
            "sesame",
            "oil-palm",
            "olive",
        ],
    ),
    ("Sugar crops", "#E7298A", ["sugarcane", "sugarbeet"]),
    ("Stimulants", "#B15928", ["cocoa", "coffee", "tea"]),
    ("Fiber crops", "#1F78B4", ["cotton"]),
    ("Feed crops", "#66A61E", ["alfalfa", "silage-maize", "biomass-sorghum"]),
]


def main() -> None:
    setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    costs = pd.read_csv(snakemake.input.costs, comment="#")  # type: ignore[name-defined]
    base_year = int(snakemake.params.base_year)  # type: ignore[name-defined]
    cost_col = f"cost_usd_{base_year}_per_ha"

    # Build crop → (group, color, order) mapping
    crop_group = {}
    crop_color = {}
    crop_order = []
    for _group, color, members in CROP_GROUPS:
        for crop in members:
            crop_group[crop] = _group
            crop_color[crop] = color
            crop_order.append(crop)

    # Filter to crops present in data and in our group definitions
    df = costs[costs["crop"].isin(crop_order)].copy()
    df["group"] = df["crop"].map(crop_group)

    # Sort crops by group order, then by median cost within group
    median_by_crop = df.groupby("crop")[cost_col].median()
    group_rank = {crop: i for i, crop in enumerate(crop_order)}
    sort_key = df["crop"].map(
        lambda c: (group_rank.get(c, 999), -median_by_crop.get(c, 0))
    )
    ordered_crops = (
        df[["crop"]]
        .assign(_sort=sort_key)
        .drop_duplicates("crop")
        .sort_values("_sort")["crop"]
        .tolist()
    )

    # Build palette for seaborn
    palette = {crop: crop_color[crop] for crop in ordered_crops}

    apply_doc_style()

    n_crops = len(ordered_crops)
    fig_height = max(5, n_crops * 0.28)
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, fig_height))

    sns.boxenplot(
        data=df,
        x=cost_col,
        y="crop",
        hue="crop",
        order=ordered_crops,
        hue_order=ordered_crops,
        palette=palette,
        legend=False,
        orient="h",
        linewidth=0.5,
        width=0.7,
        ax=ax,
        saturation=0.85,
    )

    ax.set_xscale("log")
    ax.set_xlabel(
        f"Production cost (USD {base_year}/ha)",
        fontsize=FONT_SIZES["label"],
    )
    ax.set_ylabel("")
    ax.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax.tick_params(axis="x", labelsize=FONT_SIZES["tick"])

    # Add subtle group separators
    prev_group = None
    for i, crop in enumerate(ordered_crops):
        g = crop_group[crop]
        if prev_group is not None and g != prev_group:
            ax.axhline(i - 0.5, color="#cccccc", linewidth=0.5, linestyle="-")
        prev_group = g

    # Group labels on the right margin
    group_positions = {}
    for i, crop in enumerate(ordered_crops):
        g = crop_group[crop]
        group_positions.setdefault(g, []).append(i)

    for group_name, positions in group_positions.items():
        mid = (positions[0] + positions[-1]) / 2
        color = crop_color[ordered_crops[positions[0]]]
        ax.text(
            1.01,
            mid,
            group_name,
            transform=ax.get_yaxis_transform(),
            fontsize=FONT_SIZES["annotation"],
            color=color,
            va="center",
            ha="left",
            fontweight="bold",
        )

    # Clean up grid — only vertical for log scale
    ax.grid(axis="x", alpha=0.3, linewidth=0.5)
    ax.grid(axis="y", visible=False)

    # Tighter right margin for group labels
    fig.subplots_adjust(right=0.84)

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved crop cost distribution plot")


if __name__ == "__main__":
    main()
