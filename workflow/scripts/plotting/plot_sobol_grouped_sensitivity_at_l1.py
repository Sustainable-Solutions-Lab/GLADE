# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot grouped conditional Sobol sensitivity shares at a fixed L1 cost.

Aggregates first-order Sobol shares within parameter groups (summing
S1_cond across parameters in each group) and plots stacked area charts.

Note: summing first-order indices within a group is an approximation
that excludes within-group interaction terms. The true group Sobol
index would also include S_{ij} for i,j in the group. This
approximation is reasonable when within-group interactions are small.
"""

from math import ceil
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.patches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

METADATA_COLUMNS = {
    "output",
    "parameter",
    "S1_cond",
    "ST_cond",
    "conditional_variance",
}
POLICY_COLUMNS = {"ghg_price", "value_per_yll"}
L1_COLUMN = "prod_stability_cost"
OUTPUT_ORDER = ["total_cost", "ghg_emissions", "land_use", "yll"]
OUTPUT_LABELS = {
    "total_cost": "Total Cost",
    "ghg_emissions": "GHG Emissions",
    "land_use": "Land Use",
    "yll": "Years of Life Lost",
}
X_LABELS = {
    "value_per_yll": "Value per YLL (USD per YLL)",
    "ghg_price": "GHG Price (USD per tCO2e)",
}


def _validation_quality(error: float) -> str:
    if error < 0.01:
        return "excellent"
    if error < 0.05:
        return "very good"
    if error < 0.1:
        return "acceptable"
    if error < 0.2:
        return "weak"
    return "poor"


def _ordered_outputs(available: list[str]) -> list[str]:
    ordered = [name for name in OUTPUT_ORDER if name in available]
    for name in sorted(available):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _assign_groups(
    df: pd.DataFrame, parameter_groups: dict[str, list[str]]
) -> pd.DataFrame:
    """Replace individual parameter names with group names and aggregate."""
    param_to_group = {}
    for group_name, members in parameter_groups.items():
        for member in members:
            param_to_group[member] = group_name

    df = df.copy()
    df["parameter"] = df["parameter"].map(param_to_group)
    # Drop parameters not in any group
    df = df.dropna(subset=["parameter"])
    return df


def _plot_for_x(
    df: pd.DataFrame,
    x_column: str,
    metric_column: str,
    error_by_output: dict[str, float],
    l1_value: float,
    output_pdf: Path,
) -> None:
    # Sum S1 within groups for each (output, x_value, group)
    aggregated = (
        df.groupby(["output", x_column, "parameter"], as_index=False)[metric_column]
        .sum()
        .sort_values(["output", x_column, "parameter"])
    )
    # Then average over the other policy axis (already summed within groups)
    aggregated = (
        aggregated.groupby(["output", x_column, "parameter"], as_index=False)[
            metric_column
        ]
        .mean()
        .sort_values(["output", x_column, "parameter"])
    )

    outputs = _ordered_outputs(aggregated["output"].unique().tolist())
    groups = (
        aggregated.groupby("parameter")[metric_column]
        .mean()
        .sort_values(ascending=False)
        .index.tolist()
    )
    colors = categorical_colors(groups)

    n_outputs = len(outputs)
    n_cols = 2 if n_outputs > 1 else 1
    n_rows = ceil(n_outputs / n_cols)
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(6.8 * n_cols, 3.8 * n_rows),
        sharey=True,
        squeeze=False,
    )

    for ax in axes.flat[n_outputs:]:
        ax.axis("off")

    used_groups: list[str] = []
    for i, output in enumerate(outputs):
        ax = axes.flat[i]
        sub = aggregated[aggregated["output"] == output]
        pivot = (
            sub.pivot(index=x_column, columns="parameter", values=metric_column)
            .fillna(0.0)
            .sort_index()
        )

        output_groups = [g for g in groups if g in pivot.columns]
        if not output_groups:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.axis("off")
            continue

        for group in output_groups:
            if group not in used_groups:
                used_groups.append(group)

        x = pivot.index.to_numpy(dtype=float)
        y_arrays = [pivot[group].to_numpy(dtype=float) for group in output_groups]
        ax.stackplot(
            x,
            y_arrays,
            colors=[colors[group] for group in output_groups],
            linewidth=0.0,
            alpha=0.95,
        )
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        ax.set_ylim(0.0, 1.0)
        ax.grid(axis="y", alpha=0.3)
        err_value = error_by_output.get(output)
        err_suffix = (
            ""
            if err_value is None
            else f"\nerr={err_value:.3f} ({_validation_quality(float(err_value))})"
        )
        ax.set_title(f"{OUTPUT_LABELS.get(output, output)}{err_suffix}")
        ax.set_xlabel(X_LABELS.get(x_column, x_column))
        if i % n_cols == 0:
            ax.set_ylabel("Explained Variability Fraction (S1)")

    legend_handles = [
        matplotlib.patches.Patch(color=colors[group], label=group)
        for group in used_groups
    ]
    if legend_handles:
        fig.legend(
            handles=legend_handles,
            loc="center left",
            bbox_to_anchor=(1.0, 0.5),
            frameon=False,
        )
        fig.tight_layout(rect=(0, 0.05, 0.86, 1))
    else:
        fig.tight_layout(rect=(0, 0.05, 1, 1))

    fig.text(
        0.01,
        0.01,
        f"Grouped first-order Sobol shares (sum within group) at L1 cost = {l1_value}.",
        fontsize=8,
        alpha=0.8,
    )
    fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:  # pragma: no cover - Snakemake injects this variable
        raise RuntimeError("This script must be run from Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    input_path = Path(snakemake.input.conditional_joint_indices)  # type: ignore[attr-defined]
    validation_path = Path(snakemake.input.validation)  # type: ignore[attr-defined]
    output_value_per_yll_pdf = Path(snakemake.output.value_per_yll_pdf)  # type: ignore[attr-defined]
    output_ghg_price_pdf = Path(snakemake.output.ghg_price_pdf)  # type: ignore[attr-defined]
    metric_column = str(snakemake.params.metric)  # type: ignore[attr-defined]
    l1_value = float(snakemake.params.l1_value)  # type: ignore[attr-defined]
    parameter_groups = dict(snakemake.params.parameter_groups)  # type: ignore[attr-defined]

    if not input_path.exists():
        raise FileNotFoundError(f"Missing conditional joint indices file: {input_path}")
    if not validation_path.exists():
        raise FileNotFoundError(f"Missing validation file: {validation_path}")

    df = pd.read_parquet(input_path)
    validation_df = pd.read_parquet(validation_path)
    if df.empty:
        raise ValueError(f"Conditional joint indices file is empty: {input_path}")

    # Filter to nearest L1 cost grid point (skip if column absent)
    if L1_COLUMN in df.columns:
        nearest = df[L1_COLUMN].unique()
        target = min(nearest, key=lambda v: abs(v - l1_value))
        df = df[df[L1_COLUMN] == target].copy()
        logger.info("Filtered to %s = %s (requested %s)", L1_COLUMN, target, l1_value)

    # Assign parameter groups
    df = _assign_groups(df, parameter_groups)
    logger.info(
        "Grouped parameters into %d groups: %s",
        len(parameter_groups),
        list(parameter_groups.keys()),
    )

    error_by_output = (
        validation_df.dropna(subset=["output", "validation_error"])
        .set_index("output")["validation_error"]
        .astype(float)
        .to_dict()
    )

    for policy_col in POLICY_COLUMNS:
        if policy_col not in df.columns:
            raise ValueError(f"Missing required column '{policy_col}' in joint CSV")

    output_value_per_yll_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_ghg_price_pdf.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Creating grouped plot vs value_per_yll at L1=%s", l1_value)
    _plot_for_x(
        df,
        "value_per_yll",
        metric_column,
        error_by_output,
        l1_value,
        output_value_per_yll_pdf,
    )
    logger.info("Wrote %s", output_value_per_yll_pdf)

    logger.info("Creating grouped plot vs ghg_price at L1=%s", l1_value)
    _plot_for_x(
        df,
        "ghg_price",
        metric_column,
        error_by_output,
        l1_value,
        output_ghg_price_pdf,
    )
    logger.info("Wrote %s", output_ghg_price_pdf)


if __name__ == "__main__":
    main()
