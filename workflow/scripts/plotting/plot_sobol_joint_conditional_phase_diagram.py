# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot dominant non-slice sensitivity factor across 2D policy space."""

from math import ceil
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

OUTPUT_ORDER = ["total_cost", "ghg_emissions", "land_use", "yll"]
OUTPUT_LABELS = {
    "total_cost": "Total Cost",
    "ghg_emissions": "GHG Emissions",
    "land_use": "Land Use",
    "yll": "Years of Life Lost",
}
X_COLUMN = "ghg_price"
Y_COLUMN = "value_per_yll"
L1_COLUMN = "prod_stability_cost"


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


def _dominant_grid(
    df: pd.DataFrame,
    output: str,
    parameters: list[str],
    metric_column: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sub = df[df["output"] == output]
    table = sub.pivot_table(
        index=[Y_COLUMN, X_COLUMN],
        columns="parameter",
        values=metric_column,
        aggfunc="mean",
    )
    if table.empty:
        raise ValueError(f"No conditional data found for output='{output}'")

    for parameter in parameters:
        if parameter not in table.columns:
            table[parameter] = 0.0
    table = table[parameters]

    dominant = table.idxmax(axis=1)
    dom_grid = dominant.unstack(X_COLUMN).sort_index().sort_index(axis=1)
    if dom_grid.isna().any().any():
        raise ValueError(f"Incomplete conditional grid for output='{output}'")

    x = dom_grid.columns.to_numpy(dtype=float)
    y = dom_grid.index.to_numpy(dtype=float)
    z = dom_grid.to_numpy(dtype=object)
    return x, y, z


def _imshow_extent(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """Compute imshow extent from 1D grid centers."""
    dx = float(x[1] - x[0]) if len(x) > 1 else 1.0
    dy = float(y[1] - y[0]) if len(y) > 1 else 1.0
    return (
        float(x[0] - dx / 2.0),
        float(x[-1] + dx / 2.0),
        float(y[0] - dy / 2.0),
        float(y[-1] + dy / 2.0),
    )


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:  # pragma: no cover - Snakemake injects this variable
        raise RuntimeError("This script must be run from Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    input_path = Path(snakemake.input.conditional_joint_indices)  # type: ignore[attr-defined]
    validation_path = Path(snakemake.input.validation)  # type: ignore[attr-defined]
    output_pdf = Path(snakemake.output.pdf)  # type: ignore[attr-defined]
    metric_column = str(snakemake.params.metric)  # type: ignore[attr-defined]
    allowed_parameters = list(snakemake.params.allowed_parameters)  # type: ignore[attr-defined]
    l1_value = getattr(snakemake.params, "l1_value", None)

    if not input_path.exists():
        raise FileNotFoundError(f"Missing conditional joint indices file: {input_path}")
    if not validation_path.exists():
        raise FileNotFoundError(f"Missing validation file: {validation_path}")

    df = pd.read_parquet(input_path)
    validation_df = pd.read_parquet(validation_path)
    if df.empty:
        raise ValueError(f"Conditional joint indices file is empty: {input_path}")
    if validation_df.empty:
        raise ValueError(f"Validation file is empty: {validation_path}")

    # Filter to specific L1 cost value if requested
    if l1_value is not None and L1_COLUMN in df.columns:
        nearest = df[L1_COLUMN].unique()
        target = min(nearest, key=lambda v: abs(v - l1_value))
        df = df[df[L1_COLUMN] == target].copy()
        logger.info("Filtered to %s = %s (requested %s)", L1_COLUMN, target, l1_value)

    required_columns = {"output", "parameter", X_COLUMN, Y_COLUMN, metric_column}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(
            "Conditional joint indices file is missing required columns: "
            + ", ".join(sorted(missing))
        )
    if (
        "output" not in validation_df.columns
        or "validation_error" not in validation_df.columns
    ):
        raise ValueError(
            "Validation file must contain 'output' and 'validation_error' columns"
        )

    parameters = [p for p in allowed_parameters if p in set(df["parameter"].unique())]
    if not parameters:
        raise ValueError("No non-slice parameters available for dominant-factor plot")

    outputs = _ordered_outputs(df["output"].unique().tolist())
    error_by_output = (
        validation_df.dropna(subset=["output", "validation_error"])
        .set_index("output")["validation_error"]
        .astype(float)
        .to_dict()
    )

    colors = categorical_colors(parameters, cmap_name="tab20")
    cmap = mcolors.ListedColormap([colors[p] for p in parameters])
    bounds = np.arange(len(parameters) + 1) - 0.5
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    param_to_idx = {param: idx for idx, param in enumerate(parameters)}

    n_outputs = len(outputs)
    n_cols = 2 if n_outputs > 1 else 1
    n_rows = ceil(n_outputs / n_cols)
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(6.8 * n_cols, 4.2 * n_rows),
        sharex=True,
        sharey=True,
        squeeze=False,
    )

    for ax in axes.flat[n_outputs:]:
        ax.axis("off")

    for i, output in enumerate(outputs):
        ax = axes.flat[i]
        x, y, dominant_labels = _dominant_grid(df, output, parameters, metric_column)
        idx_grid = np.vectorize(param_to_idx.get)(dominant_labels)

        ax.imshow(
            idx_grid,
            origin="lower",
            extent=_imshow_extent(x, y),
            interpolation="nearest",
            aspect="auto",
            cmap=cmap,
            norm=norm,
        )
        ax.grid(False)
        err_value = error_by_output.get(output)
        err_suffix = (
            ""
            if err_value is None
            else f"\nerr={err_value:.3f} ({_validation_quality(float(err_value))})"
        )
        ax.set_title(f"{OUTPUT_LABELS.get(output, output)}{err_suffix}")
        ax.set_xlabel("GHG Price (USD per tCO2e)")
        if i % n_cols == 0:
            ax.set_ylabel("Value per YLL (USD per YLL)")

    legend_handles = [
        mpatches.Patch(color=colors[param], label=param) for param in parameters
    ]
    fig.legend(
        handles=legend_handles,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        title="Dominant factor",
    )
    l1_suffix = f" (L1 cost = {l1_value})" if l1_value is not None else ""
    fig.suptitle(
        f"Dominant non-slice sensitivity factor across policy space{l1_suffix}", y=1.02
    )
    fig.text(
        0.01,
        0.01,
        "Color indicates the parameter with largest conditional first-order Sobol share (S1).",
        fontsize=8,
        alpha=0.8,
    )
    fig.tight_layout(rect=(0, 0.02, 0.85, 1))

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote %s", output_pdf)


if __name__ == "__main__":
    main()
