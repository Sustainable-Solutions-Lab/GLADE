#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate boxen plot of consumer-value distributions across countries.

Shows per-(food, country) consumer values (USD/kg) extracted from the
baseline solve as duals of the fixed food-consumption equality
constraints. Each row is one food; bars span the country distribution,
colored by food group.
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

# bnUSD/Mt = (1e9 USD) / (1e9 kg) = USD/kg
BNUSD_PER_MT_TO_USD_PER_KG = 1.0


def main() -> None:
    setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    df = pd.read_csv(snakemake.input.values)  # type: ignore[name-defined]
    group_colors: dict[str, str] = dict(snakemake.params.group_colors)  # type: ignore[name-defined]

    df["value_usd_per_kg"] = df["value_bnusd_per_mt"] * BNUSD_PER_MT_TO_USD_PER_KG

    # Order food groups by median value (desc), foods within each group by
    # within-group median (desc).
    group_median = df.groupby("food_group")["value_usd_per_kg"].median()
    group_order = group_median.sort_values(ascending=False).index.tolist()
    food_median = df.groupby(["food_group", "food"])["value_usd_per_kg"].median()

    ordered_foods: list[str] = []
    food_to_group: dict[str, str] = {}
    for g in group_order:
        foods = food_median.loc[g].sort_values(ascending=False).index.tolist()
        ordered_foods.extend(foods)
        for f in foods:
            food_to_group[f] = g

    palette = {f: group_colors.get(food_to_group[f], "#888888") for f in ordered_foods}

    apply_doc_style()

    n_foods = len(ordered_foods)
    fig_height = max(5, n_foods * 0.28)
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, fig_height))

    sns.boxenplot(
        data=df,
        x="value_usd_per_kg",
        y="food",
        hue="food",
        order=ordered_foods,
        hue_order=ordered_foods,
        palette=palette,
        legend=False,
        orient="h",
        linewidth=0.5,
        width=0.7,
        ax=ax,
        saturation=0.85,
    )

    # Symlog spans the ~3 orders of magnitude across foods while still
    # showing the few foods with negative values.
    ax.set_xscale("symlog", linthresh=0.05)
    ax.set_xlabel("Consumer value (USD / kg)", fontsize=FONT_SIZES["label"])
    ax.set_ylabel("")
    ax.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax.tick_params(axis="x", labelsize=FONT_SIZES["tick"])
    ax.axvline(0, color="#888888", linewidth=0.5)

    # Group separators
    prev_group = None
    for i, food in enumerate(ordered_foods):
        g = food_to_group[food]
        if prev_group is not None and g != prev_group:
            ax.axhline(i - 0.5, color="#cccccc", linewidth=0.5)
        prev_group = g

    # Right-margin group labels
    positions: dict[str, list[int]] = {}
    for i, food in enumerate(ordered_foods):
        positions.setdefault(food_to_group[food], []).append(i)
    for g, idxs in positions.items():
        mid = (idxs[0] + idxs[-1]) / 2
        ax.text(
            1.01,
            mid,
            g,
            transform=ax.get_yaxis_transform(),
            fontsize=FONT_SIZES["annotation"],
            color=group_colors.get(g, "#444444"),
            va="center",
            ha="left",
            fontweight="bold",
        )

    ax.grid(axis="x", alpha=0.3, linewidth=0.5)
    ax.grid(axis="y", visible=False)

    fig.subplots_adjust(right=0.84)

    save_doc_figure(fig, snakemake.output.svg, format="svg")  # type: ignore[name-defined]
    save_doc_figure(fig, snakemake.output.png, format="png", dpi=300)  # type: ignore[name-defined]
    plt.close(fig)
    logger.info("Saved consumer-values distribution plot")


if __name__ == "__main__":
    main()
