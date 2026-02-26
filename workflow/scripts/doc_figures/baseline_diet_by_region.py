#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate baseline diet by-region documentation figure.

Creates a horizontal stacked bar chart showing food group composition
(g/person/day) by UN M49 macro-region, population-weighted.
"""

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

# Display order for food groups in stacked bars (bottom to top)
FOOD_GROUP_ORDER = [
    "grain",
    "whole_grains",
    "starchy_vegetable",
    "vegetables",
    "fruits",
    "legumes",
    "nuts_seeds",
    "dairy",
    "eggs",
    "poultry",
    "red_meat",
    "prc_meat",
    "oil",
    "sugar",
]

# Region display order (top to bottom in the horizontal bar chart)
REGION_ORDER = ["Africa", "Americas", "Asia", "Europe", "Oceania"]


def main(
    baseline_diet_path: str,
    population_path: str,
    m49_codes_path: str,
    svg_path: str,
    png_path: str,
) -> None:
    """Generate the by-region baseline diet figure."""
    apply_doc_style()

    # Load data
    diet = pd.read_csv(baseline_diet_path)
    population = pd.read_csv(population_path)
    m49 = pd.read_csv(m49_codes_path, sep=";", comment="#")

    # Build ISO3 → macro-region mapping
    iso3_to_region = (
        m49[["ISO-alpha3 Code", "Region Name"]]
        .dropna(subset=["ISO-alpha3 Code"])
        .drop_duplicates(subset=["ISO-alpha3 Code"])
        .set_index("ISO-alpha3 Code")["Region Name"]
        .to_dict()
    )

    # Aggregate diet to food-group level per country
    group_diet = (
        diet.groupby(["country", "food_group"])["consumption_g_per_day"]
        .sum()
        .reset_index()
    )

    # Add region and population
    group_diet["region"] = group_diet["country"].map(iso3_to_region)
    pop_map = population.set_index("iso3")["population"].to_dict()
    group_diet["population"] = group_diet["country"].map(pop_map)

    # Drop rows without region or population mapping
    group_diet = group_diet.dropna(subset=["region", "population"])

    # Compute population-weighted mean per region and food group
    group_diet["weighted_consumption"] = (
        group_diet["consumption_g_per_day"] * group_diet["population"]
    )
    region_totals = group_diet.groupby(["region", "food_group"]).agg(
        weighted_consumption=("weighted_consumption", "sum"),
        total_population=("population", "sum"),
    )
    region_totals["mean_g_per_day"] = (
        region_totals["weighted_consumption"] / region_totals["total_population"]
    )
    region_totals = region_totals["mean_g_per_day"].unstack(fill_value=0)

    # Order food groups and regions
    groups_present = [g for g in FOOD_GROUP_ORDER if g in region_totals.columns]
    region_totals = region_totals[groups_present]
    regions = [r for r in REGION_ORDER if r in region_totals.index]
    region_totals = region_totals.loc[regions]

    # Plot horizontal stacked bar chart
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.35))

    y_pos = np.arange(len(regions))
    left = np.zeros(len(regions))

    for group in groups_present:
        values = region_totals[group].values
        color = FOOD_GROUP_COLORS.get(group, "#cccccc")
        label = FOOD_GROUP_LABELS.get(group, group.replace("_", " ").title())
        ax.barh(
            y_pos,
            values,
            left=left,
            color=color,
            edgecolor="black",
            linewidth=0.3,
            label=label,
        )
        left += values

    ax.set_yticks(y_pos)
    ax.set_yticklabels(regions, fontsize=FONT_SIZES["tick"])
    ax.set_xlabel("Consumption (g/person/day)", fontsize=FONT_SIZES["label"])
    ax.invert_yaxis()  # Top region first

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", alpha=0.3, linestyle="--")

    # Legend outside plot area
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1),
        fontsize=FONT_SIZES["legend"],
        frameon=False,
    )

    plt.tight_layout()
    save_doc_figure(fig, svg_path, format="svg")
    save_doc_figure(fig, png_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        baseline_diet_path=snakemake.input.baseline_diet,  # type: ignore[name-defined]
        population_path=snakemake.input.population,  # type: ignore[name-defined]
        m49_codes_path=snakemake.input.m49_codes,  # type: ignore[name-defined]
        svg_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_path=snakemake.output.png,  # type: ignore[name-defined]
    )
