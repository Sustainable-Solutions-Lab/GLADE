#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate documentation figures for GHG and health analysis.

Creates horizontal bar charts showing consumption-weighted global averages
of GHG intensity and health impacts by food group.
"""

import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.doc_figures_config import (
    COLORS,
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)

# Food group display names and colors
FOOD_GROUP_LABELS = {
    "red_meat": "Red meat",
    "poultry": "Poultry",
    "dairy": "Dairy",
    "eggs": "Eggs",
    "fish": "Fish",
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

# Colors roughly matching food categories
FOOD_GROUP_COLORS = {
    "red_meat": "#c44e52",  # Red
    "poultry": "#dd8452",  # Orange
    "dairy": "#f5e6ab",  # Cream
    "eggs": "#f0c75e",  # Yellow
    "fish": "#4c72b0",  # Blue
    "legumes": "#8c564b",  # Brown
    "nuts_seeds": "#9b7653",  # Tan
    "whole_grains": "#d4a574",  # Wheat
    "grain": "#e8d4b8",  # Light wheat
    "vegetables": "#55a868",  # Green
    "fruits": "#cc79a7",  # Pink
    "starchy_vegetable": "#937860",  # Potato brown
    "oil": "#ccb974",  # Golden
    "sugar": "#ffffff",  # White
}


def compute_global_ghg_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Compute consumption-weighted global GHG averages by food group."""
    df = df[df["consumption_mt"] > 0].copy()

    if df.empty:
        return pd.DataFrame(
            columns=["food_group", "consumption_mt", "ghg_kgco2e_per_kg"]
        )

    def weighted_avg(group: pd.DataFrame) -> pd.Series:
        total_consumption = group["consumption_mt"].sum()
        ghg_weighted = (
            group["ghg_kgco2e_per_kg"] * group["consumption_mt"]
        ).sum() / total_consumption
        return pd.Series(
            {
                "consumption_mt": total_consumption,
                "ghg_kgco2e_per_kg": ghg_weighted,
            }
        )

    result = df.groupby("food_group").apply(weighted_avg, include_groups=False)
    return result.reset_index()


def compute_global_health_averages(
    health_df: pd.DataFrame, ghg_df: pd.DataFrame
) -> pd.DataFrame:
    """Compute consumption-weighted global health averages by food group."""
    if health_df.empty:
        return pd.DataFrame(columns=["food_group", "yll_per_kg"])

    # Aggregate consumption to (country, food_group) level from ghg_df
    consumption_by_group = (
        ghg_df.groupby(["country", "food_group"])["consumption_mt"].sum().reset_index()
    )

    # Merge with health data
    merged = health_df.merge(
        consumption_by_group, on=["country", "food_group"], how="left"
    )
    merged["consumption_mt"] = merged["consumption_mt"].fillna(0.0)

    # Filter to positive consumption
    merged = merged[merged["consumption_mt"] > 0].copy()

    if merged.empty:
        return pd.DataFrame(columns=["food_group", "yll_per_kg"])

    # Consumption-weighted average by food group
    def weighted_avg(group: pd.DataFrame) -> pd.Series:
        total_consumption = group["consumption_mt"].sum()
        # yll_per_mt to yll_per_kg: divide by 1e9 (Mt to kg)
        yll_per_kg = group["yll_per_mt"] / 1e9
        yll_weighted = (yll_per_kg * group["consumption_mt"]).sum() / total_consumption
        return pd.Series({"yll_per_kg": yll_weighted})

    result = merged.groupby("food_group").apply(weighted_avg, include_groups=False)
    return result.reset_index()


def plot_ghg_bar(
    df: pd.DataFrame,
    svg_path: str,
    png_path: str,
) -> None:
    """Create horizontal bar chart of GHG intensity by food group."""
    apply_doc_style()

    if df.empty:
        fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))
        ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        ax.axis("off")
        save_doc_figure(fig, svg_path, format="svg")
        save_doc_figure(fig, png_path, format="png", dpi=300)
        plt.close(fig)
        return

    # Sort by GHG intensity descending (highest at top)
    df = df.sort_values("ghg_kgco2e_per_kg", ascending=True)

    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))

    groups = df["food_group"].tolist()
    labels = [FOOD_GROUP_LABELS.get(g, g.replace("_", " ").title()) for g in groups]
    colors = [FOOD_GROUP_COLORS.get(g, COLORS["neutral"]) for g in groups]

    bars = ax.barh(
        range(len(groups)),
        df["ghg_kgco2e_per_kg"],
        color=colors,
        edgecolor="black",
        linewidth=0.5,
    )

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(labels, fontsize=FONT_SIZES["tick"])
    ax.set_xlabel(
        r"GHG intensity (kg CO$_2$eq / kg food)", fontsize=FONT_SIZES["label"]
    )
    # Style
    ax.grid(axis="x", alpha=0.3, linestyle="--")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Add value labels
    for bar, val in zip(bars, df["ghg_kgco2e_per_kg"]):
        if val > 0:
            ax.text(
                val + 0.1,
                bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}",
                va="center",
                fontsize=FONT_SIZES["annotation"],
            )

    plt.tight_layout()
    save_doc_figure(fig, svg_path, format="svg")
    save_doc_figure(fig, png_path, format="png", dpi=300)
    plt.close(fig)


def plot_yll_bar(
    df: pd.DataFrame,
    svg_path: str,
    png_path: str,
) -> None:
    """Create horizontal bar chart of YLL impact by food group."""
    apply_doc_style()

    if df.empty:
        fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))
        ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        ax.axis("off")
        save_doc_figure(fig, svg_path, format="svg")
        save_doc_figure(fig, png_path, format="png", dpi=300)
        plt.close(fig)
        return

    # Sort by absolute YLL impact (largest absolute at top)
    df = df.copy()
    df["abs_yll"] = df["yll_per_kg"].abs()
    df = df.sort_values("abs_yll", ascending=True)

    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.5))

    groups = df["food_group"].tolist()
    labels = [FOOD_GROUP_LABELS.get(g, g.replace("_", " ").title()) for g in groups]
    colors = [FOOD_GROUP_COLORS.get(g, COLORS["neutral"]) for g in groups]

    ax.barh(
        range(len(groups)),
        df["yll_per_kg"],
        color=colors,
        edgecolor="black",
        linewidth=0.5,
    )

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(labels, fontsize=FONT_SIZES["tick"])
    ax.set_xlabel("Health impact (YLL / kg food)", fontsize=FONT_SIZES["label"])
    # Style
    ax.grid(axis="x", alpha=0.3, linestyle="--")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    save_doc_figure(fig, svg_path, format="svg")
    save_doc_figure(fig, png_path, format="png", dpi=300)
    plt.close(fig)


def main(
    ghg_intensity_path: str,
    health_marginals_path: str,
    ghg_svg_path: str,
    ghg_png_path: str,
    yll_svg_path: str,
    yll_png_path: str,
) -> None:
    """Generate both GHG and health figures."""
    # Load data
    ghg_df = pd.read_csv(ghg_intensity_path)
    health_df = pd.read_csv(health_marginals_path)

    # Compute global averages
    global_ghg = compute_global_ghg_averages(ghg_df)
    global_health = compute_global_health_averages(health_df, ghg_df)

    # Generate plots
    plot_ghg_bar(global_ghg, ghg_svg_path, ghg_png_path)
    plot_yll_bar(global_health, yll_svg_path, yll_png_path)


if __name__ == "__main__":
    main(
        ghg_intensity_path=snakemake.input.ghg_intensity,  # type: ignore[name-defined]
        health_marginals_path=snakemake.input.health_marginals,  # type: ignore[name-defined]
        ghg_svg_path=snakemake.output.ghg_svg,  # type: ignore[name-defined]
        ghg_png_path=snakemake.output.ghg_png,  # type: ignore[name-defined]
        yll_svg_path=snakemake.output.yll_svg,  # type: ignore[name-defined]
        yll_png_path=snakemake.output.yll_png,  # type: ignore[name-defined]
    )
