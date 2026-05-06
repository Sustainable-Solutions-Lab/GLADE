#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate baseline diet by-food documentation figure.

Creates a stacked bar chart showing the global breakdown of each food
group into individual foods (g/person/day), population-weighted.
"""

import colorsys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)

FOOD_GROUP_LABELS = {
    "red_meat": "Red meat",
    "prc_meat": "Processed meat",
    "poultry": "Poultry",
    "dairy": "Dairy",
    "eggs": "Eggs",
    "legumes": "Legumes",
    "nuts_seeds": "Nuts & seeds",
    "whole_grains": "Whole grains",
    "grain": "Refined grains",
    "vegetables": "Vegetables",
    "fruits": "Fruits",
    "starchy_vegetable": "Starchy vegetables",
    "oil": "Oils",
    "sugar": "Sugar",
}

FOOD_GROUP_COLORS = {
    "red_meat": "#c44e52",
    "prc_meat": "#a03030",
    "poultry": "#dd8452",
    "dairy": "#f5e6ab",
    "eggs": "#f0c75e",
    "legumes": "#8c564b",
    "nuts_seeds": "#9b7653",
    "whole_grains": "#d4a574",
    "grain": "#e8d4b8",
    "vegetables": "#55a868",
    "fruits": "#cc79a7",
    "starchy_vegetable": "#937860",
    "oil": "#ccb974",
    "sugar": "#ffffff",
}


def _vary_luminance(hex_color: str, n: int) -> list[str]:
    """Generate n color variants by adjusting lightness around base color."""
    r, g, b = (
        int(hex_color[1:3], 16) / 255,
        int(hex_color[3:5], 16) / 255,
        int(hex_color[5:7], 16) / 255,
    )
    h, base_light, s = colorsys.rgb_to_hls(r, g, b)

    if n == 1:
        return [hex_color]

    # Vary lightness in a range around the base, keeping it in [0.2, 0.9]
    l_min = max(0.25, base_light - 0.25)
    l_max = min(0.85, base_light + 0.25)
    lightnesses = np.linspace(l_min, l_max, n)

    colors = []
    for li in lightnesses:
        ri, gi, bi = colorsys.hls_to_rgb(h, li, s)
        colors.append(f"#{int(ri * 255):02x}{int(gi * 255):02x}{int(bi * 255):02x}")
    return colors


def _perceived_luminance(hex_color: str) -> float:
    """Compute perceived luminance (0=dark, 1=light) for contrast decisions."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    return 0.299 * r + 0.587 * g + 0.114 * b


def _format_food_name(name: str) -> str:
    """Convert food identifier to display name."""
    return name.replace("-", " ").replace("_", " ").title()


def main(
    baseline_diet_path: str,
    population_path: str,
    svg_path: str,
    png_path: str,
) -> None:
    """Generate the by-food baseline diet figure."""
    apply_doc_style()

    # Load data; on-disk column has explicit "_intake" suffix to flag the
    # mass basis (post-loss, post-waste consumer intake).
    diet = pd.read_csv(baseline_diet_path).rename(
        columns={"consumption_g_per_day_intake": "consumption_g_per_day"}
    )
    population = pd.read_csv(population_path)

    # Compute global population-weighted mean consumption per food
    pop_map = population.set_index("iso3")["population"].to_dict()
    diet["population"] = diet["country"].map(pop_map)
    diet = diet.dropna(subset=["population"])

    diet["weighted"] = diet["consumption_g_per_day"] * diet["population"]
    global_by_food = diet.groupby(["food", "food_group"]).agg(
        weighted=("weighted", "sum"),
        total_pop=("population", "sum"),
    )
    global_by_food["mean_g_per_day"] = (
        global_by_food["weighted"] / global_by_food["total_pop"]
    )
    global_by_food = global_by_food["mean_g_per_day"].reset_index()

    # Sort food groups by total consumption (largest first)
    group_totals = (
        global_by_food.groupby("food_group")["mean_g_per_day"]
        .sum()
        .sort_values(ascending=False)
    )
    ordered_groups = group_totals.index.tolist()

    # Filter out groups with negligible total consumption
    ordered_groups = [g for g in ordered_groups if group_totals[g] > 0.1]

    # Plot
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))

    x_pos = np.arange(len(ordered_groups))
    bar_width = 0.7

    for gi, group in enumerate(ordered_groups):
        foods_in_group = global_by_food[global_by_food["food_group"] == group].copy()
        # Sort foods by consumption (largest at bottom of stack)
        foods_in_group = foods_in_group.sort_values("mean_g_per_day", ascending=False)

        base_color = FOOD_GROUP_COLORS.get(group, "#cccccc")
        n_foods = len(foods_in_group)
        food_colors = _vary_luminance(base_color, n_foods)

        bottom = 0.0
        for fi, (_, row) in enumerate(foods_in_group.iterrows()):
            val = row["mean_g_per_day"]
            ax.bar(
                x_pos[gi],
                val,
                bottom=bottom,
                width=bar_width,
                color=food_colors[fi],
                edgecolor="black",
                linewidth=0.3,
            )

            # Annotate food name if segment is tall enough to fit text
            if val >= 15:
                text_color = (
                    "white" if _perceived_luminance(food_colors[fi]) < 0.45 else "black"
                )
                ax.text(
                    x_pos[gi],
                    bottom + val / 2,
                    _format_food_name(row["food"]),
                    ha="center",
                    va="center",
                    fontsize=FONT_SIZES["annotation"],
                    color=text_color,
                )

            bottom += val

    ax.set_xticks(x_pos)
    ax.set_xticklabels(
        [FOOD_GROUP_LABELS.get(g, g.replace("_", " ").title()) for g in ordered_groups],
        fontsize=FONT_SIZES["tick"],
        rotation=35,
        ha="right",
    )
    ax.set_ylabel("Consumption (g/person/day)", fontsize=FONT_SIZES["label"])

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3, linestyle="--")

    plt.tight_layout()
    save_doc_figure(fig, svg_path, format="svg")
    save_doc_figure(fig, png_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        baseline_diet_path=snakemake.input.baseline_diet,  # type: ignore[name-defined]
        population_path=snakemake.input.population,  # type: ignore[name-defined]
        svg_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_path=snakemake.output.png,  # type: ignore[name-defined]
    )
