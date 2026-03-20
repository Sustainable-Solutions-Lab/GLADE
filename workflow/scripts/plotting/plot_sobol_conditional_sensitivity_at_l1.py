# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot conditional Sobol sensitivity shares at a fixed L1 cost value.

Uses the joint conditional CSV (all slice parameters conditioned
simultaneously), filters to a specific prod_stability_cost grid point,
then produces stacked area charts vs ghg_price and value_per_yll
(averaging over the other policy axis).
"""

from math import ceil
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.patches
import matplotlib.pyplot as plt
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


def _plot_for_x(
    df: pd.DataFrame,
    x_column: str,
    metric_column: str,
    error_by_output: dict[str, float],
    l1_value: float,
    output_pdf: Path,
) -> None:
    aggregated = (
        df.groupby(["output", x_column, "parameter"], as_index=False)[metric_column]
        .mean()
        .sort_values(["output", x_column, "parameter"])
    )

    outputs = _ordered_outputs(aggregated["output"].unique().tolist())
    parameters = (
        aggregated.groupby("parameter")[metric_column]
        .mean()
        .sort_values(ascending=False)
        .index.tolist()
    )
    colors = categorical_colors(parameters)

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

    used_parameters: list[str] = []
    for i, output in enumerate(outputs):
        ax = axes.flat[i]
        sub = aggregated[aggregated["output"] == output]
        pivot = (
            sub.pivot(index=x_column, columns="parameter", values=metric_column)
            .fillna(0.0)
            .sort_index()
        )

        output_parameters = [p for p in parameters if p in pivot.columns]
        if not output_parameters:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.axis("off")
            continue

        for param in output_parameters:
            if param not in used_parameters:
                used_parameters.append(param)

        x = pivot.index.to_numpy(dtype=float)
        y_arrays = [pivot[param].to_numpy(dtype=float) for param in output_parameters]
        ax.stackplot(
            x,
            y_arrays,
            colors=[colors[param] for param in output_parameters],
            linewidth=0.0,
            alpha=0.95,
        )
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
        matplotlib.patches.Patch(color=colors[param], label=param)
        for param in used_parameters
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
        f"Conditional first-order Sobol shares at L1 cost = {l1_value}.",
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

    if not input_path.exists():
        raise FileNotFoundError(f"Missing conditional joint indices file: {input_path}")
    if not validation_path.exists():
        raise FileNotFoundError(f"Missing validation file: {validation_path}")

    df = pd.read_parquet(input_path)
    validation_df = pd.read_parquet(validation_path)
    if df.empty:
        raise ValueError(f"Conditional joint indices file is empty: {input_path}")

    # Filter to nearest L1 cost grid point
    if L1_COLUMN not in df.columns:
        raise ValueError(f"Joint conditional CSV does not contain '{L1_COLUMN}' column")
    nearest = df[L1_COLUMN].unique()
    target = min(nearest, key=lambda v: abs(v - l1_value))
    df = df[df[L1_COLUMN] == target].copy()
    logger.info("Filtered to %s = %s (requested %s)", L1_COLUMN, target, l1_value)

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

    logger.info("Creating stacked sensitivity plot vs value_per_yll at L1=%s", l1_value)
    _plot_for_x(
        df,
        "value_per_yll",
        metric_column,
        error_by_output,
        l1_value,
        output_value_per_yll_pdf,
    )
    logger.info("Wrote %s", output_value_per_yll_pdf)

    logger.info("Creating stacked sensitivity plot vs ghg_price at L1=%s", l1_value)
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
