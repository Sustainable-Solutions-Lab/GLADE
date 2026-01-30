#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot relative risk curves by risk factor and cause."""

import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def plot_relative_risk_curves(
    relative_risks: pd.DataFrame,
    output_path: Path,
) -> None:
    """
    Plot relative risk curves with one panel per risk factor.

    Each panel shows different lines for different causes.

    Args:
        relative_risks: DataFrame with columns [risk_factor, cause, exposure_g_per_day, rr_mean, rr_low, rr_high]
        output_path: Path to save the PDF output
    """
    # Get unique risk factors and causes
    risk_factors = sorted(relative_risks["risk_factor"].unique())
    causes = sorted(relative_risks["cause"].unique())

    if not risk_factors:
        logger.warning("No risk factors found in data")
        return

    # Calculate grid dimensions (prefer 3 columns)
    num_panels = len(risk_factors)
    ncols = min(3, num_panels)
    nrows = (num_panels + ncols - 1) // ncols  # ceiling division

    # Create figure with subplots
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(5.5 * ncols, 4.5 * nrows),
        dpi=150,
        squeeze=False,
    )

    # Flatten axes array for easy iteration
    axes_flat = axes.flatten()

    # Define colors for causes
    cause_colors = {
        "CHD": "#e41a1c",  # red
        "Stroke": "#377eb8",  # blue
        "T2DM": "#4daf4a",  # green
        "CRC": "#984ea3",  # purple
    }

    # Plot each risk factor
    for idx, risk in enumerate(risk_factors):
        ax = axes_flat[idx]
        risk_data = relative_risks[relative_risks["risk_factor"] == risk]

        # Plot each cause for this risk factor
        for cause in causes:
            cause_data = risk_data[risk_data["cause"] == cause].sort_values(
                "exposure_g_per_day"
            )

            if cause_data.empty:
                continue

            x = cause_data["exposure_g_per_day"]
            y = cause_data["rr_mean"]

            color = cause_colors.get(cause, "#999999")
            ax.plot(
                x, y, label=cause, color=color, linewidth=2, marker="o", markersize=4
            )

            # Optionally add confidence intervals if available
            if "rr_low" in cause_data.columns and "rr_high" in cause_data.columns:
                y_low = cause_data["rr_low"].fillna(y)
                y_high = cause_data["rr_high"].fillna(y)
                ax.fill_between(x, y_low, y_high, alpha=0.2, color=color)

        # Add reference line at RR = 1
        ax.axhline(y=1.0, color="#666666", linestyle="--", linewidth=1, alpha=0.7)

        # Formatting
        ax.set_xlabel("Exposure (g/day)", fontsize=10)
        ax.set_ylabel("Relative Risk", fontsize=10)
        ax.set_title(risk.replace("_", " ").title(), fontsize=11, fontweight="bold")
        ax.grid(True, alpha=0.3, linestyle=":")
        ax.legend(fontsize=8, framealpha=0.9)

        # Set y-axis to start at 0 or slightly below minimum RR
        y_min = risk_data["rr_mean"].min()
        if y_min > 0.5:
            ax.set_ylim(bottom=max(0, y_min - 0.1))

    # Hide any unused subplots
    for idx in range(num_panels, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info(f"Saved relative risk curves to {output_path}")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])
    relative_risks = pd.read_csv(snakemake.input.relative_risks)  # type: ignore[name-defined]
    output_path = Path(snakemake.output.pdf)  # type: ignore[name-defined]

    logger.info(
        f"Loaded {len(relative_risks)} risk-cause-exposure records across "
        f"{relative_risks['risk_factor'].nunique()} risk factors and "
        f"{relative_risks['cause'].nunique()} causes"
    )

    plot_relative_risk_curves(relative_risks, output_path)


if __name__ == "__main__":
    main()
