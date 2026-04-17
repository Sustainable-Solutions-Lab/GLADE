# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot optimal taxes/subsidies by food group.

Generates:
1. Bar chart of taxes/subsidies by food group (mean across countries with std)
"""

from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging


def _plot_taxes_by_group(
    taxes_df: pd.DataFrame,
    colors: dict[str, str],
    output_path: Path,
) -> pd.DataFrame:
    """Plot mean taxes/subsidies by food group with error bars.

    Returns summary DataFrame for CSV export.
    """
    # Aggregate by group
    group_stats = taxes_df.groupby("group")["tax_usd_per_kg"].agg(["mean", "std"])
    group_stats = group_stats.sort_values("mean", ascending=True)

    fig_height = max(6, 0.5 * len(group_stats))
    fig, ax = plt.subplots(figsize=(10, fig_height))

    y_pos = np.arange(len(group_stats))
    means = group_stats["mean"].values
    stds = group_stats["std"].fillna(0).values
    groups = group_stats.index.tolist()

    bar_colors = [colors.get(g, "#888888") for g in groups]

    ax.barh(y_pos, means, xerr=stds, color=bar_colors, capsize=3, alpha=0.8)

    ax.set_yticks(y_pos)
    ax.set_yticklabels([g.replace("_", " ").title() for g in groups])
    ax.set_xlabel("Tax (positive) / Subsidy (negative) [USD/kg]")
    ax.set_title("Optimal Taxes/Subsidies by Food Group")
    ax.axvline(0, color="black", linewidth=0.5)
    ax.grid(axis="x", alpha=0.3)

    # Add value labels with offset proportional to data range
    data_range = max(abs(means.min()), abs(means.max()))
    label_offset = data_range * 0.02
    for i, (mean, _) in enumerate(zip(means, groups)):
        offset = label_offset if mean >= 0 else -label_offset
        ha = "left" if mean >= 0 else "right"
        ax.text(mean + offset, i, f"{mean:.2f}", va="center", ha=ha, fontsize=8)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)

    # Return summary for CSV
    summary = group_stats.reset_index()
    summary.columns = ["group", "mean_tax_usd_per_kg", "std_tax_usd_per_kg"]
    return summary


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:
        raise RuntimeError("This script must be run from Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])

    # Load data
    taxes_df = pd.read_csv(snakemake.input.taxes)
    taxes_df["country"] = taxes_df["country"].astype(str).str.upper()

    # Get colors
    group_colors = dict(snakemake.params.group_colors or {})
    for group in set(taxes_df["group"].unique()):
        if group not in group_colors:
            group_colors[group] = "#888888"

    # Create output directories
    Path(snakemake.output.taxes_pdf).parent.mkdir(parents=True, exist_ok=True)

    # Plot and save taxes
    tax_summary = _plot_taxes_by_group(
        taxes_df, group_colors, Path(snakemake.output.taxes_pdf)
    )
    tax_summary.to_csv(snakemake.output.taxes_csv, index=False)
    logger.info("Saved tax plot to %s", snakemake.output.taxes_pdf)


if __name__ == "__main__":
    main()
