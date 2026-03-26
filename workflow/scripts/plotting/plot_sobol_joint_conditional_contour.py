# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot 2D conditional Sobol surfaces for a single non-slice parameter."""

from math import ceil
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

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


def _surface_grid(
    df: pd.DataFrame,
    output: str,
    parameter: str,
    metric_column: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sub = df[(df["output"] == output) & (df["parameter"] == parameter)]
    pivot = (
        sub.pivot_table(
            index=Y_COLUMN,
            columns=X_COLUMN,
            values=metric_column,
            aggfunc="mean",
        )
        .sort_index()
        .sort_index(axis=1)
    )
    if pivot.empty:
        raise ValueError(
            f"No conditional data found for output='{output}', parameter='{parameter}'"
        )
    if pivot.isna().any().any():
        raise ValueError(
            f"Incomplete conditional grid for output='{output}', parameter='{parameter}'"
        )

    x = pivot.columns.to_numpy(dtype=float)
    y = pivot.index.to_numpy(dtype=float)
    z = pivot.to_numpy(dtype=float)
    return x, y, z


def _cell_edges(centers: np.ndarray) -> np.ndarray:
    """Compute cell edges from 1D grid centers (midpoints, extended at boundaries)."""
    edges = np.empty(len(centers) + 1)
    edges[1:-1] = (centers[:-1] + centers[1:]) / 2
    edges[0] = centers[0] - (edges[1] - centers[0])
    edges[-1] = centers[-1] + (centers[-1] - edges[-2])
    return edges


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
    parameter = str(snakemake.wildcards.parameter)  # type: ignore[attr-defined]
    l1_value = getattr(snakemake.params, "l1_value", None)

    if parameter not in allowed_parameters:
        raise ValueError(
            f"Parameter '{parameter}' is not in non-slice parameter set: {allowed_parameters}"
        )
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

    outputs = _ordered_outputs(df["output"].unique().tolist())
    error_by_output = (
        validation_df.dropna(subset=["output", "validation_error"])
        .set_index("output")["validation_error"]
        .astype(float)
        .to_dict()
    )

    grids = {}
    vmax = 0.0
    for output in outputs:
        x, y, z = _surface_grid(df, output, parameter, metric_column)
        grids[output] = (x, y, z)
        vmax = max(vmax, float(np.nanmax(z)))
    if vmax <= 0:
        vmax = 1.0

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
        constrained_layout=True,
    )

    for ax in axes.flat[n_outputs:]:
        ax.axis("off")

    mesh = None
    for i, output in enumerate(outputs):
        ax = axes.flat[i]
        x, y, z = grids[output]
        x_edges = _cell_edges(x)
        y_edges = _cell_edges(y)
        mesh = ax.pcolormesh(
            x_edges,
            y_edges,
            z,
            cmap="viridis",
            vmin=0.0,
            vmax=vmax,
        )
        ax.set_xscale("log")
        ax.set_yscale("log")
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_formatter(mticker.ScalarFormatter())
            axis.set_minor_formatter(mticker.NullFormatter())
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

    if mesh is not None:
        cbar = fig.colorbar(mesh, ax=axes.ravel().tolist(), shrink=0.9, pad=0.02)
        cbar.set_label("Conditional first-order Sobol share (S1)")

    l1_suffix = f" (L1 cost = {l1_value})" if l1_value is not None else ""
    fig.suptitle(
        f"Conditional sensitivity surface for '{parameter}'{l1_suffix}",
        y=1.02,
    )
    fig.text(
        0.01,
        0.01,
        "Both policy axes are conditioned jointly; colors show S1 for the selected parameter.",
        fontsize=8,
        alpha=0.8,
    )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote %s", output_pdf)


if __name__ == "__main__":
    main()
