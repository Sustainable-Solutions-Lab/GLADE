#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot objective breakdown from pre-computed analysis data."""

from collections.abc import Iterable
import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Human-readable labels for category columns
CATEGORY_LABELS = {
    "crop_production": "Crop production",
    "trade": "Trade",
    "fertilizer": "Fertilizer",
    "processing": "Processing",
    "consumption": "Consumption",
    "animal_production": "Animal production",
    "feed_conversion": "Feed conversion",
    "consumer_values": "Consumer values",
    "biomass_exports": "Biomass exports",
    "biomass_routing": "Biomass routing",
    "health_burden": "Health",
    "ghg_cost": "GHG cost",
    "slack_penalties": "Slack penalties",
    "resource_supply": "Resource supply",
    "nutrient_tracking": "Nutrient tracking",
    "emissions_aggregation": "Emissions aggregation",
    "land_use": "Land use",
    "water": "Water",
    "production_stability": "Production stability",
}


def load_objective_breakdown(csv_path: Path) -> pd.Series:
    """Load objective breakdown from analysis CSV.

    The analysis CSV has a single row with category columns.
    Returns a Series with human-readable category names as index.
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        return pd.Series(dtype=float)

    # Convert single row to Series
    row = df.iloc[0]

    # Rename columns to human-readable labels
    result = {}
    for col, value in row.items():
        label = CATEGORY_LABELS.get(col, col.replace("_", " ").title())
        result[label] = float(value)

    return pd.Series(result)


def choose_scale(values: Iterable[float]) -> tuple[float, str]:
    """Choose appropriate scale for cost values in billion USD."""
    max_val = max((abs(v) for v in values), default=1.0)
    if max_val >= 1e9:
        return 1e9, "quintillion USD"
    if max_val >= 1e6:
        return 1e6, "quadrillion USD"
    if max_val >= 1e3:
        return 1e3, "trillion USD"
    return 1.0, "billion USD"


def plot_cost_breakdown(series: pd.Series, output_path: Path) -> None:
    """Create bar chart of cost breakdown by category."""
    if series.empty:
        logger.warning("No cost data available for plotting")
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.axis("off")
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    # Sort by absolute value
    series = series.sort_values(key=np.abs, ascending=False)

    scale, label = choose_scale(series.values)
    values = series / scale

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    bars = ax.bar(series.index, values, color="#4e79a7")

    ax.set_ylabel(f"Cost ({label})")
    ax.set_title("Objective Breakdown by Category")
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    for bar, raw in zip(bars, series.values):
        height = bar.get_height()
        ax.annotate(
            f"{raw / scale:,.2f}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])
    input_csv = Path(snakemake.input.objective_breakdown)  # type: ignore[name-defined]
    output_pdf = Path(snakemake.output.breakdown_pdf)  # type: ignore[name-defined]
    output_csv = Path(snakemake.output.breakdown_csv)  # type: ignore[name-defined]

    logger.info("Loading objective breakdown from %s", input_csv)
    costs = load_objective_breakdown(input_csv)

    # Filter out negligible costs
    costs = costs[costs.abs() > 1e-9]

    # Sort by absolute value
    costs = costs.sort_values(key=np.abs, ascending=False)

    logger.info(
        "Loaded %d cost categories, total: %.4f bn USD", len(costs), costs.sum()
    )

    # Write CSV in format expected by downstream scripts (index=category, total_bnusd column)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame({"total_bnusd": costs})
    out_df.index.name = "category"
    out_df.to_csv(output_csv)
    logger.info("Wrote objective breakdown to %s", output_csv)

    # Create plot
    plot_cost_breakdown(costs, output_pdf)
    logger.info("Wrote objective breakdown plot to %s", output_pdf)


if __name__ == "__main__":
    main()
