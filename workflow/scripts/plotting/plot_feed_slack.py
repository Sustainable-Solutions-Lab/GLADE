# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot positive/negative animal feed slack aggregated globally (Mt).

Shows per-feed-category slack from the bidirectional feed slack generators
added during validation mode.  Positive slack = feed shortage (model had to
conjure feed), negative slack = feed excess (model had to absorb feed).
"""

import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.snakemake_utils import load_solved_network

logger = logging.getLogger(__name__)

POSITIVE_CARRIER = "slack_positive_feed"
NEGATIVE_CARRIER = "slack_negative_feed"

# Display-friendly labels
CATEGORY_LABELS = {
    "ruminant_forage": "Forage",
    "ruminant_roughage": "Roughage",
    "ruminant_grain": "Rum. grain",
    "ruminant_protein": "Rum. protein",
    "monogastric_grain": "Mon. grain",
    "monogastric_protein": "Mon. protein",
    "monogastric_low_quality": "Mon. low-qual.",
}


def _extract_category(name: str) -> str:
    """Extract feed category from a slack generator name.

    Names follow the pattern ``slack:feed_{direction}_{category}:{country}``.
    """
    # e.g. "slack:feed_positive_ruminant_grain:USA"
    after_colon = name.split(":")[1]  # "feed_positive_ruminant_grain"
    # Strip the "feed_positive_" or "feed_negative_" prefix
    for prefix in ("feed_positive_", "feed_negative_"):
        if after_colon.startswith(prefix):
            return after_colon[len(prefix) :]
    return after_colon


def _aggregate_slack(network: pypsa.Network) -> pd.DataFrame:
    """Compute per-feed-category positive and negative slack.

    Returns DataFrame indexed by feed category with columns
    ``positive_mt`` and ``negative_mt`` (both non-negative).
    """
    gens = network.generators.static
    gen_p = network.generators.dynamic["p"]

    records = []
    for carrier, col_name in [
        (POSITIVE_CARRIER, "positive_mt"),
        (NEGATIVE_CARRIER, "negative_mt"),
    ]:
        mask = gens["carrier"] == carrier
        if not mask.any():
            continue
        dispatch = gen_p.loc[:, mask]
        totals = dispatch.sum(axis=0)

        categories = pd.Series(
            [_extract_category(n) for n in totals.index],
            index=totals.index,
        )
        by_cat = totals.abs().groupby(categories).sum()
        for cat, val in by_cat.items():
            records.append({"feed_category": cat, col_name: val})

    if not records:
        return pd.DataFrame(
            columns=["feed_category", "positive_mt", "negative_mt"]
        ).set_index("feed_category")

    df = pd.DataFrame(records)
    df = df.groupby("feed_category").sum()
    for col in ("positive_mt", "negative_mt"):
        if col not in df.columns:
            df[col] = 0.0
    return df


def _aggregate_baseline_by_category(network: pypsa.Network) -> pd.Series:
    """Sum baseline feed use by feed category (Mt DM)."""
    animal = network.links.static[
        network.links.static["carrier"] == "animal_production"
    ]
    if "baseline_feed_use_mt_dm" not in animal.columns:
        return pd.Series(dtype=float)

    return animal.groupby("feed_category")["baseline_feed_use_mt_dm"].sum()


def _plot_feed_slack(
    slack_df: pd.DataFrame,
    baseline: pd.Series,
    output_pdf: Path,
) -> None:
    """Render bar chart of per-category feed slack."""
    all_cats = sorted(set(slack_df.index) | set(baseline.index) | set(CATEGORY_LABELS))
    # Order: ruminant categories first, then monogastric
    ruminant_order = [
        "ruminant_forage",
        "ruminant_roughage",
        "ruminant_grain",
        "ruminant_protein",
    ]
    monogastric_order = [
        "monogastric_grain",
        "monogastric_protein",
        "monogastric_low_quality",
    ]
    cat_order = [c for c in ruminant_order + monogastric_order if c in all_cats]
    # Append any extras
    for c in all_cats:
        if c not in cat_order:
            cat_order.append(c)

    slack = slack_df.reindex(cat_order, fill_value=0.0)
    baseline = baseline.reindex(cat_order, fill_value=0.0)

    # Drop categories with no baseline and no slack
    active = (baseline > 0) | (slack["positive_mt"] > 0) | (slack["negative_mt"] > 0)
    cat_order = [c for c in cat_order if active.get(c, False)]
    slack = slack.loc[cat_order]
    baseline = baseline.loc[cat_order]

    colors = categorical_colors(cat_order)
    labels = [CATEGORY_LABELS.get(c, c) for c in cat_order]

    fig, ax = plt.subplots(figsize=(10, 6))
    positions = np.arange(len(cat_order))
    bar_width = 0.7

    # Positive slack (upward)
    ax.bar(
        positions,
        slack["positive_mt"],
        width=bar_width,
        color=[colors[c] for c in cat_order],
        edgecolor="white",
        linewidth=0.8,
        alpha=1.0,
    )
    # Negative slack (downward)
    ax.bar(
        positions,
        -slack["negative_mt"],
        width=bar_width,
        color=[colors[c] for c in cat_order],
        edgecolor="white",
        linewidth=0.8,
        alpha=0.45,
    )

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_xlim(-0.6, len(cat_order) - 0.4)
    ax.set_ylabel("Mt DM")
    ax.set_title("Animal feed slack by category")
    ax.grid(axis="y", alpha=0.3)

    # Secondary axis: slack as % of baseline demand
    total_slack = slack["positive_mt"] + slack["negative_mt"]
    ratio_pct = (
        (total_slack / baseline.replace(0, np.nan) * 100.0)
        .reindex(cat_order)
        .astype(np.float64)
    )

    ax2 = ax.twinx()
    mask = np.isfinite(ratio_pct.values)
    ax2.scatter(
        positions[mask],
        ratio_pct.values[mask],
        color="black",
        s=18,
        marker="o",
        zorder=7,
    )
    ax2.set_ylabel("|Slack| / baseline demand (%)")
    finite = ratio_pct[np.isfinite(ratio_pct)].values
    y2_max = max(1.0, float(np.max(finite)) * 1.15) if finite.size > 0 else 1.0

    # Align secondary zero with primary zero
    y1_min, y1_max = ax.get_ylim()
    if y1_max <= y1_min:
        y2_min = 0.0
    else:
        zero_frac = float(np.clip((0.0 - y1_min) / (y1_max - y1_min), 0.0, 1.0))
        y2_min = -zero_frac / (1.0 - zero_frac) * y2_max if zero_frac < 1.0 else -y2_max
    ax2.set_ylim(y2_min, y2_max)
    ax2.set_yticks(np.linspace(0.0, y2_max, 6))

    handles = [
        Patch(facecolor="gray", alpha=1.0, label="Shortage (positive slack)"),
        Patch(facecolor="gray", alpha=0.45, label="Excess (negative slack)"),
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
    ax.legend(handles=handles, loc="upper right")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote feed slack plot to %s", output_pdf)


def _write_csv(
    slack_df: pd.DataFrame,
    baseline: pd.Series,
    output_csv: Path,
) -> None:
    """Write per-category feed slack summary CSV."""
    df = slack_df.copy()
    df["baseline_mt_dm"] = baseline
    df["net_mt"] = df["positive_mt"] - df["negative_mt"]
    df["total_slack_mt"] = df["positive_mt"] + df["negative_mt"]
    df = df.fillna(0.0).sort_index()

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, float_format="%.6g")
    logger.info("Wrote feed slack CSV to %s", output_csv)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", snakemake.input.network)  # type: ignore[name-defined]
    network = load_solved_network(snakemake.input.network)  # type: ignore[name-defined]

    slack_df = _aggregate_slack(network)
    baseline = _aggregate_baseline_by_category(network)

    _plot_feed_slack(slack_df, baseline, Path(snakemake.output.pdf))  # type: ignore[name-defined]
    _write_csv(slack_df, baseline, Path(snakemake.output.csv))  # type: ignore[name-defined]
