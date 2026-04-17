#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate two-panel food group slack figure for validation documentation.

Top panel: absolute slack (Mt) with positive/negative stacked bars.
Bottom panel: relative deviation (% of demand).
Food groups are sorted by relative deviation in both panels.
"""

import logging

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.doc_figures_config import (
    FIGURE_WIDTH,
    FONT_SIZES,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.plotting.plot_food_group_slack import (
    _aggregate_consumption_by_group,
    _aggregate_demand_by_group,
    _build_food_slack_df,
)

logger = logging.getLogger(__name__)


def _prettify_group(name: str) -> str:
    """Convert raw food-group identifiers to display labels."""
    return name.replace("_", " ").title()


def _plot_two_panel(
    slack_df: pd.DataFrame,
    demand: pd.Series,
    consumption: pd.Series,
    output_svg: str,
    output_png: str,
    group_colors: dict[str, str] | None = None,
) -> None:
    apply_doc_style()

    # Aggregate slack by food group
    if slack_df.empty:
        group_pos = pd.Series(dtype=float)
        group_neg = pd.Series(dtype=float)
    else:
        group_pos = slack_df.groupby("food_group")["overconsumption"].sum()
        group_neg = slack_df.groupby("food_group")["underconsumption"].sum()

    all_groups = sorted(
        set(demand.index.astype(str))
        | set(consumption.index.astype(str))
        | set(group_pos.index.astype(str))
        | set(group_neg.index.astype(str))
    )

    if not all_groups:
        logger.warning("No food groups found; skipping food group slack plot")
        return

    # Compute absolute slack and relative deviation
    abs_slack = pd.Series(0.0, index=all_groups)
    for g in all_groups:
        abs_slack[g] = group_pos.get(g, 0.0) + group_neg.get(g, 0.0)

    demand_aligned = demand.reindex(all_groups, fill_value=0.0)
    rel_deviation = pd.Series(np.nan, index=all_groups)
    for g in all_groups:
        d = demand_aligned[g]
        if d > 0:
            rel_deviation[g] = abs_slack[g] / d * 100.0

    # Sort by relative deviation (largest first); NaN goes to end
    sorted_groups = rel_deviation.sort_values(
        ascending=False, na_position="last"
    ).index.tolist()

    display_labels = [_prettify_group(g) for g in sorted_groups]
    colors = categorical_colors(sorted_groups, overrides=group_colors or {})

    fig, (ax_abs, ax_rel) = plt.subplots(
        2,
        1,
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.55),
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.12},
        sharex=True,
    )

    positions = np.arange(len(sorted_groups))
    bar_width = 0.7

    # --- Top panel: absolute slack (bars) ---
    for gi, group in enumerate(sorted_groups):
        pos_val = group_pos.get(group, 0.0)
        neg_val = group_neg.get(group, 0.0)
        if pos_val > 0.01:
            ax_abs.bar(
                gi,
                pos_val,
                width=bar_width,
                color=colors[group],
                edgecolor="white",
                linewidth=0.8,
            )
        if neg_val > 0.01:
            ax_abs.bar(
                gi,
                -neg_val,
                width=bar_width,
                color=colors[group],
                edgecolor="white",
                linewidth=0.8,
                alpha=0.45,
            )

    ax_abs.axhline(0, color="black", linewidth=0.8)
    ax_abs.set_ylabel("Absolute slack (Mt)", fontsize=FONT_SIZES["label"])
    ax_abs.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax_abs.grid(axis="y", alpha=0.3)
    ax_abs.set_xlim(-0.6, len(sorted_groups) - 0.4)

    # Direct text annotations at the far right instead of a legend
    x_right = len(sorted_groups) - 0.5
    y_pad = (ax_abs.get_ylim()[1] - ax_abs.get_ylim()[0]) * 0.03
    ax_abs.text(
        x_right,
        y_pad,
        "Excess",
        ha="right",
        va="bottom",
        fontsize=FONT_SIZES["legend"],
        color="#555555",
    )
    ax_abs.text(
        x_right,
        -y_pad,
        "Shortage",
        ha="right",
        va="top",
        fontsize=FONT_SIZES["legend"],
        color="#555555",
    )

    # --- Bottom panel: relative deviation (line) ---
    rel_vals = rel_deviation.reindex(sorted_groups).values
    finite_mask = np.isfinite(rel_vals)

    ax_rel.plot(
        positions[finite_mask],
        rel_vals[finite_mask],
        marker="o",
        markersize=4,
        linewidth=1.5,
        color="#555555",
        zorder=3,
    )
    # Color-code the markers
    for i in range(len(sorted_groups)):
        if finite_mask[i]:
            ax_rel.plot(
                positions[i],
                rel_vals[i],
                marker="o",
                markersize=5,
                color=colors[sorted_groups[i]],
                zorder=4,
            )

    ax_rel.set_xticks(positions)
    ax_rel.set_xticklabels(
        display_labels, rotation=35, ha="right", fontsize=FONT_SIZES["tick"]
    )
    ax_rel.set_ylabel("Rel. deviation (%)", fontsize=FONT_SIZES["label"])
    ax_rel.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax_rel.grid(axis="y", alpha=0.3)
    ax_rel.set_xlim(-0.6, len(sorted_groups) - 0.4)
    ax_rel.set_ylim(bottom=0)

    save_doc_figure(fig, output_svg, format="svg")
    save_doc_figure(fig, output_png, format="png", dpi=300)
    plt.close(fig)
    logger.info("Saved validation food group slack plot")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", snakemake.input.network)
    network = pypsa.Network(snakemake.input.network)

    slack_df = _build_food_slack_df(network)
    demand = _aggregate_demand_by_group(network)
    consumption = _aggregate_consumption_by_group(network)

    group_colors = (
        snakemake.config.get("plotting", {}).get("colors", {}).get("food_groups", {})
    )

    _plot_two_panel(
        slack_df,
        demand,
        consumption,
        snakemake.output.svg,  # type: ignore[name-defined]
        snakemake.output.png,  # type: ignore[name-defined]
        group_colors=group_colors,
    )


if __name__ == "__main__":
    main()
