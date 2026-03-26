# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot conditional Sobol sensitivity shares as stacked area charts."""

from math import ceil
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("pdf")
import matplotlib.colors as mcolors
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
PARAMETER_LABELS = {
    "luc_factor": "LUC Emissions",
    "rr_protective": "RR Protective",
    "rr_harmful": "RR Harmful",
    "fcr_factor": "Feed Conversion Ratio",
    "yield_factor": "Crop Yield",
    "n2o_factor": "N\u2082O Emissions",
    "value_per_yll": "Value per YLL",
    "ch4_factor": "CH\u2084 Emissions",
    "flw_factor": "Food Loss & Waste",
    "ghg_price": "GHG Price",
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


def _slice_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in METADATA_COLUMNS]


def _ordered_outputs(available: list[str]) -> list[str]:
    ordered = [name for name in OUTPUT_ORDER if name in available]
    for name in sorted(available):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _pretty(param: str) -> str:
    return PARAMETER_LABELS.get(param, param)


def _text_color_for_bg(hex_color: str) -> str:
    """Return 'white' or a dark grey based on background luminance."""
    rgb = mcolors.to_rgb(hex_color)
    luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return "#333333" if luminance > 0.55 else "white"


def _label_areas(
    ax: plt.Axes,
    x: np.ndarray,
    y_arrays: list[np.ndarray],
    parameters: list[str],
    colors: dict[str, str],
    min_height: float = 0.08,
) -> None:
    """Place text labels directly on stacked areas that are tall enough.

    Labels are placed in the interior of the x range (middle 60%) at the
    position where each band is tallest, with overlap avoidance.
    """
    n = len(x)
    # Restrict candidate positions to the interior to avoid edge clipping
    margin = max(1, n // 5)
    interior = slice(margin, n - margin)

    cumulative = np.zeros_like(x)
    bands: list[tuple[np.ndarray, np.ndarray, str]] = []
    for y, param in zip(y_arrays, parameters):
        lower = cumulative.copy()
        cumulative = cumulative + y
        bands.append((lower, cumulative.copy(), param))

    # Track placed labels as (x_idx, y_center) for overlap checks
    placed: list[tuple[int, float]] = []
    for lower, upper, param in bands:
        height = upper - lower
        interior_height = height[interior]
        if len(interior_height) == 0:
            continue
        best_interior = int(np.argmax(interior_height))
        best_idx = margin + best_interior
        band_h = float(height[best_idx])
        if band_h < min_height:
            continue
        y_center = (lower[best_idx] + upper[best_idx]) / 2
        # Skip if another label is nearby in both x and y
        label_half = 0.035
        overlaps = any(
            abs(y_center - py) < (label_half * 2 + 0.01)
            and abs(best_idx - px) < n * 0.25
            for px, py in placed
        )
        if overlaps:
            continue
        fg = _text_color_for_bg(colors.get(param, "#000000"))
        ax.text(
            x[best_idx],
            y_center,
            _pretty(param),
            ha="center",
            va="center",
            fontsize=7,
            color=fg,
            fontweight="bold",
            clip_on=True,
        )
        placed.append((best_idx, y_center))


def _plot_for_x(
    df: pd.DataFrame,
    x_column: str,
    metric_column: str,
    error_by_output: dict[str, float],
    output_pdf: Path,
    color_overrides: dict[str, str] | None = None,
    group_order: list[str] | None = None,
) -> None:
    aggregated = (
        df.groupby(["output", x_column, "parameter"], as_index=False)[metric_column]
        .mean()
        .sort_values(["output", x_column, "parameter"])
    )

    outputs = _ordered_outputs(aggregated["output"].unique().tolist())

    # Use config group order if provided, falling back to contribution-based ranking.
    available = set(aggregated["parameter"].unique())
    if group_order:
        parameters = [p for p in group_order if p in available]
        # Append any parameters not in the group config
        for p in sorted(available):
            if p not in parameters:
                parameters.append(p)
    else:
        if "total_cost" in aggregated["output"].values:
            rank_data = aggregated[aggregated["output"] == "total_cost"]
        else:
            rank_data = aggregated
        parameters = (
            rank_data.groupby("parameter")[metric_column]
            .mean()
            .sort_values(ascending=False)
            .index.tolist()
        )
    colors = categorical_colors(parameters, overrides=color_overrides)

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
            edgecolor="white",
            linewidth=0.5,
            alpha=0.95,
        )
        _label_areas(ax, x, y_arrays, output_parameters, colors)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        ax.set_xlim(x.min(), x.max())
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

    # Reverse legend order to match bottom-to-top stacking
    legend_handles = [
        matplotlib.patches.Patch(color=colors[param], label=_pretty(param))
        for param in reversed(used_parameters)
    ]
    if legend_handles:
        fig.tight_layout(rect=(0, 0.05, 0.82, 1))
        fig.legend(
            handles=legend_handles,
            loc="center left",
            bbox_to_anchor=(0.83, 0.55),
            frameon=False,
            fontsize=9,
        )
    else:
        fig.tight_layout(rect=(0, 0.05, 1, 1))

    fig.text(
        0.01,
        0.01,
        "Areas are conditional first-order Sobol shares. "
        "Error bands: <0.01 excellent, <0.05 very good, <0.1 acceptable, <0.2 weak, >=0.2 poor.",
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
    input_path = Path(snakemake.input.conditional_indices)  # type: ignore[attr-defined]
    validation_path = Path(snakemake.input.validation)  # type: ignore[attr-defined]
    output_value_per_yll_pdf = Path(snakemake.output.value_per_yll_pdf)  # type: ignore[attr-defined]
    output_ghg_price_pdf = Path(snakemake.output.ghg_price_pdf)  # type: ignore[attr-defined]
    metric_column = str(snakemake.params.metric)  # type: ignore[attr-defined]
    color_overrides = dict(snakemake.params.parameter_colors)  # type: ignore[attr-defined]
    group_order = list(snakemake.params.parameter_group_order)  # type: ignore[attr-defined]

    if not input_path.exists():
        raise FileNotFoundError(f"Missing conditional indices file: {input_path}")
    if not validation_path.exists():
        raise FileNotFoundError(f"Missing validation file: {validation_path}")

    df = pd.read_parquet(input_path)
    validation_df = pd.read_parquet(validation_path)
    if df.empty:
        raise ValueError(f"Conditional indices file is empty: {input_path}")
    if validation_df.empty:
        raise ValueError(f"Validation file is empty: {validation_path}")
    if metric_column not in df.columns:
        raise ValueError(
            f"Expected metric column '{metric_column}' in conditional indices"
        )
    required_validation_columns = {"output", "validation_error"}
    missing_validation_columns = required_validation_columns - set(
        validation_df.columns
    )
    if missing_validation_columns:
        raise ValueError(
            "Validation file is missing required columns: "
            + ", ".join(sorted(missing_validation_columns))
        )
    error_by_output = (
        validation_df.dropna(subset=["output", "validation_error"])
        .set_index("output")["validation_error"]
        .astype(float)
        .to_dict()
    )

    slices = _slice_columns(df)
    required_slices = {"value_per_yll", "ghg_price"}
    missing_slices = required_slices - set(slices)
    if missing_slices:
        raise ValueError(
            "Conditional indices file does not include required slice columns: "
            + ", ".join(sorted(missing_slices))
        )

    output_value_per_yll_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_ghg_price_pdf.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Creating stacked conditional sensitivity plot vs value_per_yll")
    _plot_for_x(
        df,
        "value_per_yll",
        metric_column,
        error_by_output,
        output_value_per_yll_pdf,
        color_overrides=color_overrides,
        group_order=group_order,
    )
    logger.info("Wrote %s", output_value_per_yll_pdf)

    logger.info("Creating stacked conditional sensitivity plot vs ghg_price")
    _plot_for_x(
        df,
        "ghg_price",
        metric_column,
        error_by_output,
        output_ghg_price_pdf,
        color_overrides=color_overrides,
        group_order=group_order,
    )
    logger.info("Wrote %s", output_ghg_price_pdf)


if __name__ == "__main__":
    main()
