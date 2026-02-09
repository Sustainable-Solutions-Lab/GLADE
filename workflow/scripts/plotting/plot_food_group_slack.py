# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot positive/negative food group slack aggregated globally (Mt).

Supports two slack mechanisms:
1. Generator-based: group-level slack generators (slack_positive_group_*)
2. Food-level constraints: per-food equality constraints from enforce_baseline_diet
"""

import logging
from pathlib import Path

import matplotlib
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import pypsa

matplotlib.use("pdf")
import matplotlib.pyplot as plt

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

logger = logging.getLogger(__name__)

POSITIVE_PREFIX = "slack_positive_group_"
NEGATIVE_PREFIX = "slack_negative_group_"


def _snapshot_weights(network: pypsa.Network) -> pd.Series:
    """Return per-snapshot weights; defaults to ones if missing."""

    weights = network.snapshot_weightings.get("objective")
    if weights is None:
        return pd.Series(1.0, index=network.snapshots)
    return weights


def _has_food_level_constraints(network: pypsa.Network) -> bool:
    """Check if the network has food-level equality constraints."""
    gc = network.global_constraints.static
    if gc.empty:
        return False
    return gc.index.astype(str).str.startswith("food_equal_").any()


def _aggregate_food_level_slack(
    network: pypsa.Network,
) -> tuple[pd.Series, pd.Series]:
    """Compute per-food-group slack from food-level equality constraints.

    Returns (positive, negative) Series indexed by food group name.
    Positive = overconsumption (actual > target).
    Negative = underconsumption (actual < target).
    """
    gc = network.global_constraints.static
    food_gc = gc[gc.index.astype(str).str.startswith("food_equal_")]

    if food_gc.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    # Get consumption link p0 values
    consume_links = network.links.static[
        network.links.static["carrier"] == "food_consumption"
    ]
    p0 = network.links.dynamic["p0"]

    # Build actual consumption: (food, country) → p0
    actual = {}
    for link_name in consume_links.index:
        food = consume_links.loc[link_name, "food"]
        country = consume_links.loc[link_name, "country"]
        val = float(p0.loc["now", link_name]) if link_name in p0.columns else 0.0
        actual[(food, country)] = actual.get((food, country), 0.0) + val

    # Compute per-food deviation
    records = []
    for _, gc_row in food_gc.iterrows():
        food = str(gc_row["food"])
        country = str(gc_row["country"])
        food_group = str(gc_row.get("food_group", ""))
        target = float(gc_row["constant"])
        p0_val = actual.get((food, country), 0.0)
        deviation = p0_val - target
        records.append(
            {
                "food_group": food_group,
                "overconsumption": max(0.0, deviation),
                "underconsumption": max(0.0, -deviation),
            }
        )

    df = pd.DataFrame(records)
    positive = df.groupby("food_group")["overconsumption"].sum()
    negative = df.groupby("food_group")["underconsumption"].sum()

    # Filter out negligible values
    positive = positive[positive > 1e-6].sort_index()
    negative = negative[negative > 1e-6].sort_index()

    return positive, negative


def _aggregate_positive_slack(network: pypsa.Network) -> pd.Series:
    """Aggregate positive (shortage) slack by food group in Mt."""

    generators = network.generators.static
    if generators.empty or "carrier" not in generators.columns:
        return pd.Series(dtype=float)

    mask = generators["carrier"].astype(str).str.startswith(POSITIVE_PREFIX)
    if not mask.any():
        return pd.Series(dtype=float)

    dispatch = network.generators.dynamic.p.loc[:, mask]
    weights = _snapshot_weights(network)
    weighted = dispatch.multiply(weights, axis=0)

    totals = weighted.clip(lower=0.0).sum(axis=0)
    carriers = generators.loc[mask, "carrier"]
    by_group = totals.groupby(carriers).sum()

    return by_group.rename(lambda c: c.replace(POSITIVE_PREFIX, "")).sort_index()


def _aggregate_negative_slack(network: pypsa.Network) -> pd.Series:
    """Aggregate negative (excess) slack by food group in Mt."""

    generators = network.generators.static
    if generators.empty or "carrier" not in generators.columns:
        return pd.Series(dtype=float)

    mask = generators["carrier"].astype(str).str.startswith(NEGATIVE_PREFIX)
    if not mask.any():
        return pd.Series(dtype=float)

    dispatch = network.generators.dynamic.p.loc[:, mask]
    weights = _snapshot_weights(network)
    weighted = dispatch.multiply(weights, axis=0)

    # Negative p values (consumption) correspond to absorbing surplus food
    absorption = -weighted.clip(upper=0.0).sum(axis=0)
    carriers = generators.loc[mask, "carrier"]
    by_group = absorption.groupby(carriers).sum()

    return by_group.rename(lambda c: c.replace(NEGATIVE_PREFIX, "")).sort_index()


def _aggregate_consumption_by_group(network: pypsa.Network) -> pd.Series:
    """Aggregate total food consumption by group in Mt from consumption links."""
    consume = network.links.static[
        network.links.static["carrier"] == "food_consumption"
    ]
    if consume.empty:
        return pd.Series(dtype=float)

    p0 = network.links.dynamic["p0"]
    consume_p0 = p0.loc["now", consume.index].clip(lower=0)
    consume_groups = consume["food_group"].astype(str)
    return consume_p0.groupby(consume_groups).sum().sort_index()


def _aggregate_demand_by_group(network: pypsa.Network) -> pd.Series:
    """Aggregate baseline food demand by group in Mt.

    Uses food-level equality constraints when available; falls back to
    realized consumption for legacy generator-based slack mode.
    """
    if not _has_food_level_constraints(network):
        return _aggregate_consumption_by_group(network)

    gc = network.global_constraints.static
    if gc.empty:
        return pd.Series(dtype=float)

    food_gc = gc[gc.index.astype(str).str.startswith("food_equal_")]
    if food_gc.empty or "food_group" not in food_gc.columns:
        return pd.Series(dtype=float)

    constants = pd.to_numeric(food_gc["constant"], errors="coerce").fillna(0.0)
    groups = food_gc["food_group"].astype(str)
    return constants.groupby(groups).sum().sort_index()


def _build_food_slack_df(network: pypsa.Network) -> pd.DataFrame:
    """Build per-food slack DataFrame from food-level constraints or generators.

    Returns DataFrame with columns: food, food_group, overconsumption, underconsumption
    """
    if _has_food_level_constraints(network):
        return _build_food_slack_from_constraints(network)
    return _build_food_slack_from_generators(network)


def _build_food_slack_from_constraints(network: pypsa.Network) -> pd.DataFrame:
    """Build per-food slack from food-level equality constraints."""
    gc = network.global_constraints.static
    food_gc = gc[gc.index.astype(str).str.startswith("food_equal_")]

    consume = network.links.static[
        network.links.static["carrier"] == "food_consumption"
    ]
    p0 = network.links.dynamic["p0"]

    # Actual consumption: (food, country) → p0
    actual = {}
    for link_name in consume.index:
        food = consume.loc[link_name, "food"]
        country = consume.loc[link_name, "country"]
        val = float(p0.loc["now", link_name]) if link_name in p0.columns else 0.0
        actual[(food, country)] = actual.get((food, country), 0.0) + val

    records = []
    for _, gc_row in food_gc.iterrows():
        food = str(gc_row["food"])
        country = str(gc_row["country"])
        food_group = str(gc_row.get("food_group", ""))
        target = float(gc_row["constant"])
        deviation = actual.get((food, country), 0.0) - target
        records.append(
            {
                "food": food,
                "food_group": food_group,
                "overconsumption": max(0.0, deviation),
                "underconsumption": max(0.0, -deviation),
            }
        )

    df = pd.DataFrame(records)
    # Aggregate across countries
    return (
        df.groupby(["food", "food_group"])[["overconsumption", "underconsumption"]]
        .sum()
        .reset_index()
    )


def _build_food_slack_from_generators(network: pypsa.Network) -> pd.DataFrame:
    """Build per-food-group slack from generator-based slack (legacy)."""
    positive = _aggregate_positive_slack(network)
    negative = _aggregate_negative_slack(network)

    groups = sorted(set(positive.index) | set(negative.index))
    records = []
    for g in groups:
        records.append(
            {
                "food": g,
                "food_group": g,
                "overconsumption": negative.get(g, 0.0),
                "underconsumption": positive.get(g, 0.0),
            }
        )
    return pd.DataFrame(records)


def _plot_food_slack(
    slack_df: pd.DataFrame,
    demand: pd.Series,
    consumption: pd.Series,
    output_pdf: Path,
) -> None:
    """Render stacked bar chart of per-food slack grouped by food group."""
    group_colors_param = getattr(snakemake.params, "group_colors", {}) or {}

    all_groups = sorted(
        set(demand.index.astype(str).tolist())
        | set(consumption.index.astype(str).tolist())
        | set(slack_df.get("food_group", pd.Series(dtype=str)).astype(str).tolist())
        | set(group_colors_param)
    )
    if not all_groups:
        _fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "No food groups found", ha="center", va="center")
        ax.axis("off")
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
        plt.close()
        logger.info("No slack to plot; wrote placeholder to %s", output_pdf)
        return

    plot_df = slack_df.copy()
    if not plot_df.empty:
        plot_df = plot_df[
            (plot_df["overconsumption"] > 0.01) | (plot_df["underconsumption"] > 0.01)
        ].copy()

    # Sort food groups by total slack but keep zero-slack groups in the plot.
    if slack_df.empty:
        abs_slack_by_group = pd.Series(0.0, index=all_groups)
    else:
        abs_slack_by_group = (
            slack_df.groupby("food_group")[["overconsumption", "underconsumption"]]
            .sum()
            .sum(axis=1)
            .reindex(all_groups, fill_value=0.0)
        )

    if plot_df.empty:
        group_totals = pd.Series(0.0, index=all_groups)
    else:
        plot_df["total"] = plot_df["overconsumption"] + plot_df["underconsumption"]
        group_totals = (
            plot_df.groupby("food_group")["total"]
            .sum()
            .reindex(all_groups, fill_value=0.0)
        )

    group_order = (
        group_totals.rename_axis("food_group")
        .reset_index(name="total")
        .sort_values(["total", "food_group"], ascending=[False, True])
    )["food_group"].tolist()
    plot_df["group_rank"] = plot_df["food_group"].map(
        {g: i for i, g in enumerate(group_order)}
    )
    plot_df = plot_df.sort_values(["group_rank", "total"], ascending=[True, False])

    # Assign colors per food group
    colors = categorical_colors(group_order, overrides=group_colors_param)

    _fig, ax = plt.subplots(figsize=(10, 6))

    # Build stacked bars: one bar per food group, stacked by food
    bar_width = 0.7
    positions = np.arange(len(group_order))

    label_candidates: list[dict[str, float | str]] = []

    for direction, sign, alpha in [
        ("overconsumption", 1, 1.0),
        ("underconsumption", -1, 0.45),
    ]:
        for gi, group in enumerate(group_order):
            group_foods = plot_df[plot_df["food_group"] == group]
            cumulative = 0.0
            for _, row in group_foods.iterrows():
                val = row[direction]
                if val < 0.01:
                    continue
                if cumulative > 0.0:
                    boundary = sign * cumulative
                    ax.hlines(
                        boundary,
                        gi - bar_width / 2,
                        gi + bar_width / 2,
                        colors="white",
                        linewidth=0.9,
                        zorder=4,
                    )
                ax.bar(
                    gi,
                    sign * val,
                    width=bar_width,
                    bottom=sign * cumulative,
                    color=colors[group],
                    edgecolor="white",
                    linewidth=0.8,
                    alpha=alpha,
                )
                y_center = sign * (cumulative + val / 2.0)
                label_candidates.append(
                    {
                        "x_bar": float(gi),
                        "y_center": float(y_center),
                        "food": str(row["food"]),
                    }
                )
                cumulative += val

    # Place labels centered on each segment.
    if label_candidates:
        labels_df = pd.DataFrame(label_candidates)
        for _, row in labels_df.iterrows():
            ax.text(
                float(row["x_bar"]),
                float(row["y_center"]),
                str(row["food"]),
                ha="center",
                va="center",
                fontsize=6,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.2},
                zorder=6,
            )

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(positions)
    ax.set_xticklabels(group_order, rotation=35, ha="right")
    ax.set_xlim(-0.6, len(group_order) - 0.4)
    ax.set_ylabel("Mt")
    ax.set_title("Food slack by group (stacked by food)")
    ax.grid(axis="y", alpha=0.3)
    if plot_df.empty:
        ax.text(
            0.5,
            0.95,
            "Food slack < 0.01 Mt across all food groups",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=9,
        )

    # Secondary axis: absolute slack relative to baseline demand by group.
    demand_by_group = pd.to_numeric(
        demand.reindex(group_order, fill_value=0.0), errors="coerce"
    ).fillna(0.0)
    abs_slack = pd.to_numeric(
        abs_slack_by_group.reindex(group_order, fill_value=0.0), errors="coerce"
    ).fillna(0.0)
    ratio_pct = pd.Series(np.nan, index=group_order, dtype=float)
    positive_demand = demand_by_group > 0
    ratio_pct.loc[positive_demand] = (
        abs_slack.loc[positive_demand] / demand_by_group.loc[positive_demand] * 100.0
    )

    ax2 = ax.twinx()
    x_vals = np.asarray(positions, dtype=float)
    mask = np.isfinite(ratio_pct.values)
    ax2.scatter(
        x_vals[mask],
        ratio_pct.values[mask],
        color="black",
        s=18,
        marker="o",
        zorder=7,
    )
    ax2.set_ylabel("Absolute slack / demand (%)")
    finite_ratio = ratio_pct[np.isfinite(ratio_pct)].values
    y2_max = 1.0
    if finite_ratio.size > 0:
        ymax = float(np.max(finite_ratio))
        y2_max = max(1.0, ymax * 1.15)

    # Align secondary-axis zero with primary-axis zero while keeping
    # secondary tick labels non-negative.
    y1_min, y1_max = ax.get_ylim()
    if y1_max <= y1_min:
        y2_min = 0.0
    else:
        zero_frac = (0.0 - y1_min) / (y1_max - y1_min)
        zero_frac = float(np.clip(zero_frac, 0.0, 1.0))
        if zero_frac <= 0.0:
            y2_min = 0.0
        elif zero_frac >= 1.0:
            y2_min = -y2_max
        else:
            y2_min = -zero_frac / (1.0 - zero_frac) * y2_max
    ax2.set_ylim(y2_min, y2_max)
    ax2.set_yticks(np.linspace(0.0, y2_max, 6))

    # Legend
    handles = [
        Patch(facecolor="gray", alpha=1.0, label="Excess (above baseline)"),
        Patch(facecolor="gray", alpha=0.45, label="Shortage (below baseline)"),
        Line2D(
            [],
            [],
            color="black",
            marker="o",
            linestyle="None",
            markersize=5,
            label="|Slack| / demand",
        ),
    ]
    ax.legend(handles=handles, loc="lower right")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote food slack plot to %s", output_pdf)


def _write_csv(
    slack_df: pd.DataFrame,
    consumption: pd.Series,
    output_csv: Path,
) -> None:
    """Write per-food-group slack summary CSV."""
    if slack_df.empty:
        df = pd.DataFrame(
            columns=[
                "positive_mt",
                "negative_mt",
                "consumption_mt",
                "net_mt",
                "slack_mt",
            ]
        )
    else:
        group_agg = slack_df.groupby("food_group")[
            ["overconsumption", "underconsumption"]
        ].sum()
        df = pd.DataFrame(
            {
                "positive_mt": group_agg["overconsumption"],
                "negative_mt": group_agg["underconsumption"],
                "consumption_mt": consumption,
            }
        ).fillna(0.0)
        df["net_mt"] = df["positive_mt"] - df["negative_mt"]
        df["slack_mt"] = df["positive_mt"] + df["negative_mt"]
        total_consumption = float(df["consumption_mt"].sum())
        if total_consumption > 0.0:
            df["slack_share_global_pct"] = df["slack_mt"] / total_consumption * 100.0
        df = df.sort_index()

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, float_format="%.6g")
    logger.info("Wrote food group slack totals to %s", output_csv)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    logger.info("Loading solved network from %s", snakemake.input.network)
    network = pypsa.Network(snakemake.input.network)

    slack_df = _build_food_slack_df(network)
    demand = _aggregate_demand_by_group(network)
    consumption = _aggregate_consumption_by_group(network)

    _plot_food_slack(slack_df, demand, consumption, Path(snakemake.output.pdf))
    _write_csv(slack_df, consumption, Path(snakemake.output.csv))
