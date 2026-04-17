# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare system costs across solved objectives."""

from pathlib import Path

import matplotlib
import matplotlib.patches
import numpy as np
import pandas as pd

matplotlib.use("pdf")
import matplotlib.pyplot as plt

from workflow.scripts.logging_config import setup_script_logging

MIN_ABS_COST_BNUSD = 5.0


def _categorize_cost_term(term: str) -> str:
    """Categorize cost terms into health, emissions, or other."""
    if term == "Health" or term.startswith("Health ("):
        return "health"
    if "GHG" in term or "emissions" in term.lower():
        return "emissions"
    return "other"


def _assign_colors(terms: list[str]) -> dict[str, str]:
    """Assign colors to cost terms grouped by health, emissions, and other.

    Three hue groups with different shades:
    - Health: shades of red/pink
    - Emissions: shades of green
    - Other: shades of blue/gray
    """
    # Base color palettes for each group
    health_colors = ["#d62728", "#ff7f0e", "#e377c2", "#ff9896", "#ffbb78"]
    emissions_colors = ["#2ca02c", "#98df8a", "#8c564b", "#c5b0d5"]
    other_colors = ["#1f77b4", "#aec7e8", "#17becf", "#9edae5", "#7f7f7f", "#c7c7c7"]

    # Group terms by category
    health_terms = []
    emissions_terms = []
    other_terms = []

    for term in terms:
        cat = _categorize_cost_term(term)
        if cat == "health":
            health_terms.append(term)
        elif cat == "emissions":
            emissions_terms.append(term)
        else:
            other_terms.append(term)

    # Assign colors
    colors = {}
    for i, term in enumerate(health_terms):
        colors[term] = health_colors[i % len(health_colors)]
    for i, term in enumerate(emissions_terms):
        colors[term] = emissions_colors[i % len(emissions_colors)]
    for i, term in enumerate(other_terms):
        colors[term] = other_colors[i % len(other_colors)]

    return colors


def _ordered_terms(cost_df: pd.DataFrame) -> list[str]:
    """Order cost terms by total magnitude across all scenarios."""
    return cost_df.sum(axis=0).sort_values(ascending=False, key=np.abs).index.tolist()


def _filter_small_terms(cost_df: pd.DataFrame, min_abs_cost: float) -> pd.DataFrame:
    """Drop cost terms that never reach the minimum absolute cost."""
    if cost_df.empty:
        return cost_df

    keep_terms = cost_df.abs().max(axis=0) >= min_abs_cost
    return cost_df.loc[:, keep_terms]


def _plot_comparison(
    cost_df: pd.DataFrame,
    labels: list[str],
    colors: dict[str, str],
    output_pdf: Path,
) -> None:
    """Create stacked bar plot comparing system costs across scenarios."""
    if cost_df.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No cost data", ha="center", va="center")
        ax.axis("off")
        fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    ordered_terms = _ordered_terms(cost_df)
    bar_height = 0.7
    fig_height = max(4.0, 1.0 + bar_height * len(labels))
    fig, ax = plt.subplots(figsize=(10, fig_height))

    for idx, label in enumerate(labels):
        series = (
            cost_df.loc[label] if label in cost_df.index else pd.Series(dtype=float)
        )

        # Stack positive values to the right
        left_positive = 0.0
        for term in ordered_terms:
            value = float(series.get(term, 0.0))
            if value > 0.0:
                ax.barh(
                    idx,
                    value,
                    left=left_positive,
                    height=bar_height,
                    color=colors[term],
                )
                left_positive += value

        # Stack negative values to the left
        left_negative = 0.0
        for term in ordered_terms:
            value = float(series.get(term, 0.0))
            if value < 0.0:
                ax.barh(
                    idx,
                    value,
                    left=left_negative,
                    height=bar_height,
                    color=colors[term],
                )
                left_negative += value

    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_ylim(-0.5, len(labels) - 0.5)
    ax.set_xlabel("System cost (billion USD)")
    ax.set_title("System Cost Comparison")
    ax.grid(axis="x", alpha=0.3)
    ax.axvline(x=0, color="black", linewidth=0.8, alpha=0.5)

    # Add note about excluded slack costs
    ax.text(
        0.98,
        0.02,
        "Note: Slack penalties excluded from this plot",
        transform=ax.transAxes,
        fontsize=8,
        va="bottom",
        ha="right",
        style="italic",
        alpha=0.7,
    )

    # Create legend grouped by category
    legend_handles = []
    legend_labels = []

    # Group terms by category for legend
    for _, category_check in [
        ("Health", lambda t: _categorize_cost_term(t) == "health"),
        ("Emissions", lambda t: _categorize_cost_term(t) == "emissions"),
        ("Other", lambda t: _categorize_cost_term(t) == "other"),
    ]:
        category_terms = [t for t in ordered_terms if category_check(t)]
        if category_terms:
            for term in category_terms:
                legend_handles.append(
                    matplotlib.patches.Patch(color=colors[term], label=term)
                )
                legend_labels.append(term)

    if legend_handles:
        fig.legend(
            legend_handles[::-1],
            legend_labels[::-1],
            loc="center left",
            bbox_to_anchor=(1.0, 0.5),
            fontsize=8,
        )
        fig.tight_layout(rect=(0, 0, 0.90, 1))
    else:
        fig.tight_layout()

    fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)


def _write_csv(cost_df: pd.DataFrame, labels: list[str], output_csv: Path) -> None:
    """Write cost comparison data to CSV."""
    records = []
    for label in labels:
        row = cost_df.loc[label] if label in cost_df.index else pd.Series(dtype=float)
        for term in cost_df.columns:
            records.append(
                {
                    "scenario": label,
                    "term": term,
                    "cost_bnusd": float(row.get(term, 0.0)),
                }
            )

    pd.DataFrame.from_records(records).to_csv(output_csv, index=False)


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:
        raise RuntimeError("This script must be run from Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    breakdown_csvs = [Path(p) for p in snakemake.input.breakdowns]  # type: ignore[attr-defined]
    comparison_labels = list(snakemake.params.wildcards)  # type: ignore[attr-defined]
    output_pdf = Path(snakemake.output.pdf)  # type: ignore[attr-defined]
    output_csv = Path(snakemake.output.csv)  # type: ignore[attr-defined]

    if len(breakdown_csvs) != len(comparison_labels):
        raise ValueError(
            "Number of breakdown inputs must match number of comparison labels"
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    # Load cost data from breakdown CSVs
    cost_data: dict[str, pd.Series] = {}

    for label, csv_path in zip(comparison_labels, breakdown_csvs):
        logger.info("Loading cost breakdown from %s", csv_path)
        df = pd.read_csv(csv_path, index_col=0)

        # Use total_bnusd column
        if "total_bnusd" not in df.columns:
            raise ValueError(f"Expected 'total_bnusd' column in {csv_path}")

        # Filter out slack penalties
        costs = df["total_bnusd"]
        costs = costs[~costs.index.str.contains("Slack", case=False, na=False)]

        # Group all health costs together
        health_total = 0.0
        other_costs = {}
        for term, value in costs.items():
            if term.startswith("Health ("):
                health_total += value
            else:
                other_costs[term] = value

        # Create series with grouped health costs
        if health_total != 0.0:
            other_costs["Health"] = health_total

        cost_data[label] = pd.Series(other_costs)

    # Create DataFrame with all scenarios
    cost_df = pd.DataFrame(cost_data).T.fillna(0.0) if cost_data else pd.DataFrame()

    # Filter out categories below threshold across all scenarios
    cost_df = _filter_small_terms(cost_df, MIN_ABS_COST_BNUSD)

    # Assign colors to cost terms
    all_terms = _ordered_terms(cost_df) if not cost_df.empty else []
    term_colors = _assign_colors(all_terms)

    _plot_comparison(cost_df, comparison_labels, term_colors, output_pdf)
    _write_csv(cost_df, comparison_labels, output_csv)

    logger.info("System cost comparison saved to %s and %s", output_pdf, output_csv)


if __name__ == "__main__":
    main()
