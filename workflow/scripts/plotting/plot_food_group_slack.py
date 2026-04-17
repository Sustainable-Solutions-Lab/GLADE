# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot positive/negative food group slack aggregated globally (Mt).

Reads food-level slack generators (slack_positive_food / slack_negative_food)
and aggregates dispatch by food group for visualization.
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


def _has_food_slack_generators(network: pypsa.Network) -> bool:
    """Check if the network has food-level slack generators."""
    generators = network.generators.static
    if generators.empty or "carrier" not in generators.columns:
        return False
    return (generators["carrier"] == "slack_positive_food").any()


def _build_food_slack_df(network: pypsa.Network) -> pd.DataFrame:
    """Build per-food slack DataFrame from food-level slack generators.

    Returns DataFrame with columns: food, food_group, overconsumption, underconsumption
    """
    generators = network.generators.static
    dispatch = network.generators.dynamic.p

    pos_mask = generators["carrier"] == "slack_positive_food"
    neg_mask = generators["carrier"] == "slack_negative_food"

    if not pos_mask.any() and not neg_mask.any():
        return pd.DataFrame(
            columns=["food", "food_group", "overconsumption", "underconsumption"]
        )

    snapshot = network.snapshots[-1]

    records = {}
    # Positive generators: dispatch > 0 means shortage (underconsumption)
    for gen_name in generators.index[pos_mask]:
        food = str(generators.loc[gen_name, "food"])
        food_group = str(generators.loc[gen_name, "food_group"])
        val = (
            float(dispatch.loc[snapshot, gen_name])
            if gen_name in dispatch.columns
            else 0.0
        )
        key = (food, food_group)
        if key not in records:
            records[key] = {
                "food": food,
                "food_group": food_group,
                "overconsumption": 0.0,
                "underconsumption": 0.0,
            }
        records[key]["underconsumption"] += max(0.0, val)

    # Negative generators: dispatch < 0 means excess (overconsumption)
    for gen_name in generators.index[neg_mask]:
        food = str(generators.loc[gen_name, "food"])
        food_group = str(generators.loc[gen_name, "food_group"])
        val = (
            float(dispatch.loc[snapshot, gen_name])
            if gen_name in dispatch.columns
            else 0.0
        )
        key = (food, food_group)
        if key not in records:
            records[key] = {
                "food": food,
                "food_group": food_group,
                "overconsumption": 0.0,
                "underconsumption": 0.0,
            }
        records[key]["overconsumption"] += max(0.0, -val)

    df = pd.DataFrame(list(records.values()))
    return df


def _aggregate_food_level_slack(
    network: pypsa.Network,
) -> tuple[pd.Series, pd.Series]:
    """Compute per-food-group slack from food-level slack generators.

    Returns (positive, negative) Series indexed by food group name.
    Positive = overconsumption (surplus absorbed by negative slack).
    Negative = underconsumption (shortage filled by positive slack).
    """
    df = _build_food_slack_df(network)
    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    positive = df.groupby("food_group")["overconsumption"].sum()
    negative = df.groupby("food_group")["underconsumption"].sum()

    positive = positive[positive > 1e-6].sort_index()
    negative = negative[negative > 1e-6].sort_index()

    return positive, negative


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

    Uses p_set on consume links when available; falls back to
    realized consumption.
    """
    consume = network.links.static[
        network.links.static["carrier"] == "food_consumption"
    ]
    if consume.empty:
        return pd.Series(dtype=float)

    if "p_set" in network.links.dynamic and not network.links.dynamic.p_set.empty:
        p_set = network.links.dynamic.p_set
        snapshot = network.snapshots[-1]
        targets = p_set.loc[snapshot].reindex(consume.index)
        has_target = targets.notna()
        if has_target.any():
            targets = targets[has_target].clip(lower=0)
            groups = consume.loc[has_target.values, "food_group"].astype(str)
            return targets.groupby(groups).sum().sort_index()

    return _aggregate_consumption_by_group(network)


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

    # Make primary y-axis symmetric around zero
    y1_min, y1_max = ax.get_ylim()
    y1_abs = max(abs(y1_min), abs(y1_max))
    ax.set_ylim(-y1_abs, y1_abs)
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

    # Symmetric secondary axis aligned with the symmetric primary axis.
    # Only show ticks on the positive side (the ratio is non-negative).
    ax2.set_ylim(-y2_max, y2_max)
    locator = plt.MaxNLocator(nbins=5, steps=[1, 2, 5, 10])
    locator.view_limits(0, y2_max)
    ticks = [t for t in locator.tick_values(0, y2_max) if 0 <= t <= y2_max]
    ax2.set_yticks(ticks)

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
