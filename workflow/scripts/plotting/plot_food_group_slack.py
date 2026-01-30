# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot positive/negative food group slack aggregated globally (Mt)."""

import logging
from pathlib import Path

import matplotlib
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


def _group_leg_for_link(link_row: pd.Series) -> tuple[int, str] | None:
    """Return (leg_idx, group) for a consume link's group output bus.

    Uses the food_group column to identify which group a consume link contributes to,
    and finds which bus leg connects to a group carrier.
    """
    # Use the food_group column directly instead of parsing bus names
    food_group = link_row.get("food_group")
    if pd.isna(food_group) or not food_group:
        return None

    group_name = str(food_group)

    # Find which bus leg connects to the group store
    for column in link_row.index:
        if not str(column).startswith("bus"):
            continue

        bus_val = link_row[column]
        if not isinstance(bus_val, str):
            continue

        # Group buses have carrier format like "group_cereals"
        # Bus names follow pattern store:group:{group}:{country}
        if f":group:{group_name}:" in bus_val:
            try:
                leg_idx = int(str(column).replace("bus", "") or 0)
            except ValueError:
                continue
            return leg_idx, group_name

    return None


def _aggregate_consumption_by_group(network: pypsa.Network) -> pd.Series:
    """Aggregate total food consumption by group in Mt using snapshot weights."""

    links = network.links.static
    if links.empty or "carrier" not in links.columns:
        return pd.Series(dtype=float)

    weights = _snapshot_weights(network)
    totals: dict[str, float] = {}

    # Filter to consume links using carrier-based filtering
    consume_mask = links["carrier"] == "food_consumption"
    consume_links = links[consume_mask]

    for link_name, link_row in consume_links.iterrows():
        leg_info = _group_leg_for_link(link_row)
        if leg_info is None:
            continue

        leg_idx, group_name = leg_info
        series_table = getattr(network.links.dynamic, f"p{leg_idx}", None)
        if series_table is None or link_name not in series_table:
            continue

        dispatch = series_table[link_name]
        weighted = (-dispatch.clip(upper=0.0)).multiply(weights, axis=0)
        total = float(weighted.sum())
        if total <= 0.0:
            continue

        totals[group_name] = totals.get(group_name, 0.0) + total

    return pd.Series(totals, dtype=float).sort_index()


def _plot_slack(
    positive: pd.Series,
    negative: pd.Series,
    consumption: pd.Series,
    output_pdf: Path,
) -> None:
    """Render bar plot with slack bars and consumption-share markers."""

    df = pd.DataFrame(
        {
            "positive_mt": positive,
            "negative_mt": negative,
            "consumption_mt": consumption,
        }
    ).fillna(0.0)
    df = df[(df["positive_mt"] > 0) | (df["negative_mt"] > 0)]

    plt.figure(figsize=(10, 6))
    ax = plt.gca()

    if df.empty:
        ax.text(0.5, 0.5, "No food group slack recorded", ha="center", va="center")
        ax.axis("off")
        plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
        plt.close()
        logger.info("No slack to plot; wrote placeholder to %s", output_pdf)
        return

    df["slack_mt"] = df["positive_mt"] + df["negative_mt"]
    df = df.sort_values("slack_mt", ascending=False)

    total_consumption = float(df["consumption_mt"].sum())
    if total_consumption > 0.0:
        df["share_pct"] = df["slack_mt"] / total_consumption * 100.0
    else:
        df["share_pct"] = 0.0

    df["excess_plot"] = df["negative_mt"]
    df["shortage_plot"] = -df["positive_mt"]

    groups = df.index.tolist()
    group_colors = getattr(snakemake.params, "group_colors", {}) or {}
    colors = categorical_colors(groups, overrides=group_colors)

    positions = range(len(groups))
    excess_bars = ax.bar(
        positions,
        df["excess_plot"],
        color=[colors[g] for g in groups],
        edgecolor="black",
        linewidth=0.4,
        label="Slack (excess)",
    )
    shortage_bars = ax.bar(
        positions,
        df["shortage_plot"],
        color=[colors[g] for g in groups],
        edgecolor="black",
        linewidth=0.4,
        alpha=0.45,
        label="Slack (shortage)",
    )

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(groups, rotation=35, ha="right")
    ax.set_ylabel("Mt")
    ax.set_title("Food group slack (global)")
    ax.grid(axis="y", alpha=0.3)

    legend_handles: list[matplotlib.artist.Artist] = []
    labels: list[str] = []
    if excess_bars.patches:
        legend_handles.append(excess_bars.patches[0])
        labels.append("Slack (excess)")
    if shortage_bars.patches:
        legend_handles.append(shortage_bars.patches[0])
        labels.append("Slack (shortage)")

    handles = list(legend_handles)

    plot_min = float(df["shortage_plot"].min()) if not df.empty else 0.0
    plot_max = float(df["excess_plot"].max()) if not df.empty else 0.0
    bottom = plot_min * 1.05 if plot_min < 0 else -1.0
    top = plot_max * 1.1 if plot_max > 0 else 1.0

    shares = df["share_pct"]

    if shares.max() > 0.0:
        share_span = float(shares.max())
        ax2 = ax.twinx()
        ax2.set_ylabel("% of global consumption")
        ax2.set_ylim(0.0, share_span * 1.15)
        scatter = ax2.scatter(
            positions,
            shares,
            color="black",
            marker="o",
            zorder=4,
            label="Slack as % of global consumption",
        )
        handles.append(scatter)
        labels.append(scatter.get_label())
    else:
        ax2 = None

    ax.legend(handles, labels, loc="upper right")
    ax.set_ylim(bottom=bottom, top=top if top > 0 else 1.0)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote food group slack plot to %s", output_pdf)


def _write_csv(
    positive: pd.Series, negative: pd.Series, consumption: pd.Series, output_csv: Path
) -> None:
    df = pd.DataFrame(
        {
            "positive_mt": positive,
            "negative_mt": negative,
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

    positive = _aggregate_positive_slack(network)
    negative = _aggregate_negative_slack(network)
    consumption = _aggregate_consumption_by_group(network)

    _plot_slack(positive, negative, consumption, Path(snakemake.output.pdf))
    _write_csv(positive, negative, consumption, Path(snakemake.output.csv))
