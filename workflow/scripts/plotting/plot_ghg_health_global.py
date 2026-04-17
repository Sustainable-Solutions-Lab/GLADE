# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot consumption-weighted global average GHG and YLL impacts by food group."""

import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

logger = logging.getLogger(__name__)


def compute_global_ghg_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Compute consumption-weighted global GHG averages by food group.

    Input DataFrame has columns: country, food, food_group, consumption_mt,
    ghg_kgco2e_per_kg

    Returns DataFrame with columns: food_group, consumption_mt, ghg_kgco2e_per_kg
    """
    # Filter to rows with positive consumption
    df = df[df["consumption_mt"] > 0].copy()

    if df.empty:
        return pd.DataFrame(
            columns=["food_group", "consumption_mt", "ghg_kgco2e_per_kg"]
        )

    # Consumption-weighted average by food group
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
    """Compute consumption-weighted global health averages by food group.

    Uses consumption from ghg_df (aggregated to food_group) to weight health impacts.

    Input health_df has columns: country, food_group, yll_per_mt
    Input ghg_df has columns: country, food, food_group, consumption_mt, ghg_kgco2e_per_kg

    Returns DataFrame with columns: food_group, yll_per_kg
    """
    if health_df.empty:
        return pd.DataFrame(columns=["food_group", "yll_per_kg"])

    # First aggregate consumption to (country, food_group) level from ghg_df
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
    output_path: Path,
    group_colors: dict[str, str] | None = None,
) -> None:
    """Create horizontal bar chart of GHG intensity by food group."""
    if df.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        ax.axis("off")
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    # Sort by GHG intensity descending
    df = df.sort_values("ghg_kgco2e_per_kg", ascending=True)

    fig, ax = plt.subplots(figsize=(8, 6))

    groups = df["food_group"].tolist()
    colors = categorical_colors(groups, overrides=group_colors)

    ax.barh(
        range(len(groups)),
        df["ghg_kgco2e_per_kg"],
        color=[colors[g] for g in groups],
        edgecolor="black",
        linewidth=0.4,
    )

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(groups)
    ax.set_xlabel("GHG intensity (kg CO$_2$eq / kg food)")
    ax.set_title("Global average GHG intensity by food group\n(consumption-weighted)")
    ax.grid(axis="x", alpha=0.3, linestyle="--")
    ax.axvline(0, color="black", linewidth=0.8)

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote GHG bar chart to %s", output_path)


def plot_yll_bar(
    df: pd.DataFrame,
    output_path: Path,
    group_colors: dict[str, str] | None = None,
) -> None:
    """Create horizontal bar chart of YLL impact by food group."""
    if df.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        ax.axis("off")
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    # Sort by absolute YLL impact (largest absolute at top)
    df = df.copy()
    df["abs_yll"] = df["yll_per_kg"].abs()
    df = df.sort_values("abs_yll", ascending=True)

    fig, ax = plt.subplots(figsize=(8, 6))

    groups = df["food_group"].tolist()
    colors = categorical_colors(groups, overrides=group_colors)

    bar_colors = [colors[g] for g in groups]

    ax.barh(
        range(len(groups)),
        df["yll_per_kg"],
        color=bar_colors,
        edgecolor="black",
        linewidth=0.4,
    )

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(groups)
    ax.set_xlabel("Health impact (YLL / kg food)")
    ax.set_title(
        "Global average health impact by food group\n"
        "(consumption-weighted; negative = protective)"
    )
    ax.grid(axis="x", alpha=0.3, linestyle="--")
    ax.axvline(0, color="black", linewidth=0.8)

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote YLL bar chart to %s", output_path)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    # Load GHG intensity data (at food level)
    ghg_df = pd.read_parquet(snakemake.input.ghg_intensity)
    logger.info("Loaded %d rows from GHG intensity", len(ghg_df))

    # Load health marginals data (at food_group level)
    health_df = pd.read_parquet(snakemake.input.health_marginals)
    logger.info("Loaded %d rows from health marginals", len(health_df))

    # Compute global GHG averages (aggregates food to food_group)
    global_ghg = compute_global_ghg_averages(ghg_df)
    logger.info("Computed GHG averages for %d food groups", len(global_ghg))

    # Compute global health averages
    global_health = compute_global_health_averages(health_df, ghg_df)
    logger.info("Computed health averages for %d food groups", len(global_health))

    # Get color overrides
    group_colors = getattr(snakemake.params, "group_colors", {}) or {}

    # Create plots
    plot_ghg_bar(global_ghg, Path(snakemake.output.ghg_pdf), group_colors)
    plot_yll_bar(global_health, Path(snakemake.output.yll_pdf), group_colors)
