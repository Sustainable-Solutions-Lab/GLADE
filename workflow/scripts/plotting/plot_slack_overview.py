# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot total slack use by category as cost (bnUSD) with quantities."""

import logging
from pathlib import Path

import matplotlib
import pandas as pd
import pypsa

matplotlib.use("pdf")
import matplotlib.pyplot as plt

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.snakemake_utils import load_solved_network

logger = logging.getLogger(__name__)


def _mask_carrier_equals(carrier: str):
    def _mask(columns: pd.Index, generators: pd.DataFrame) -> pd.Series:
        carriers = generators.loc[columns, "carrier"].astype(str)
        return carriers == carrier

    return _mask


CATEGORIES = [
    ("Land", _mask_carrier_equals("land_slack"), "Mha"),
    ("Feed (positive)", _mask_carrier_equals("slack_positive_feed"), "Mt"),
    ("Feed (negative)", _mask_carrier_equals("slack_negative_feed"), "Mt"),
    ("Food (positive)", _mask_carrier_equals("slack_positive_food"), "Mt"),
    ("Food (negative)", _mask_carrier_equals("slack_negative_food"), "Mt"),
    ("Water", _mask_carrier_equals("water_slack"), "Mm3"),
]


def _collect_production_slack(
    network: pypsa.Network, slack_cost: float
) -> list[dict[str, object]]:
    """Extract production stability slack from network metadata."""
    records: list[dict[str, object]] = []
    slack_data = network.meta.get("production_stability_slack", {})

    if "crop" in slack_data:
        total = sum(slack_data["crop"].values())
        if total > 1e-6:
            records.append(
                {
                    "category": "Crop production (min)",
                    "quantity": total,
                    "unit": "Mt",
                    "cost_bnusd": total * slack_cost,
                }
            )

    if "animal" in slack_data:
        total = sum(slack_data["animal"].values())
        if total > 1e-6:
            records.append(
                {
                    "category": "Animal production (min)",
                    "quantity": total,
                    "unit": "Mt",
                    "cost_bnusd": total * slack_cost,
                }
            )

    return records


def _collect_slack(network: pypsa.Network, slack_cost: float) -> pd.DataFrame:
    """Aggregate slack quantities and costs by category."""

    generators = network.generators.static
    dispatch = network.generators.dynamic.p

    records: list[dict[str, object]] = []

    # Collect generator-based slack (land, feed, food, water)
    if not generators.empty and not dispatch.empty:
        for label, mask_fn, unit in CATEGORIES:
            mask = mask_fn(dispatch.columns, generators)
            if not mask.any():
                continue

            cols = dispatch.loc[:, mask]
            quantity = cols.abs().sum().sum()
            if quantity < 1e-6:
                continue
            marginal_cost = generators.loc[mask, "marginal_cost"]
            cost_bnusd = (cols.abs() * marginal_cost.abs()).sum().sum()

            records.append(
                {
                    "category": label,
                    "quantity": quantity,
                    "unit": unit,
                    "cost_bnusd": cost_bnusd,
                }
            )

    # Collect production stability slack from metadata
    records.extend(_collect_production_slack(network, slack_cost))

    if not records:
        return pd.DataFrame(columns=["quantity", "unit", "cost_bnusd"])

    return pd.DataFrame.from_records(records).set_index("category")


def _plot(df: pd.DataFrame, output_pdf: Path) -> None:
    """Render horizontal bar chart of slack cost by category."""

    plt.figure(figsize=(8, 4.5))
    ax = plt.gca()

    if df.empty:
        ax.text(0.5, 0.5, "No slack recorded", ha="center", va="center")
        ax.axis("off")
        plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
        plt.close()
        logger.info("No slack to plot; wrote placeholder to %s", output_pdf)
        return

    df_sorted = df.sort_values("cost_bnusd", ascending=False)
    max_cost = max(df_sorted["cost_bnusd"].max(), 1e-9)

    bars = ax.barh(df_sorted.index, df_sorted["cost_bnusd"], color="#4c72b0")
    ax.set_xlabel("Cost (bn USD)")
    ax.set_title("Slack penalty by category")
    ax.grid(axis="x", alpha=0.3)

    for bar, (_, row) in zip(bars, df_sorted.iterrows(), strict=False):
        text = f"{row['quantity']:.3g} {row['unit']}"
        offset = 0.01 * max_cost
        ax.text(
            bar.get_width() + offset,
            bar.get_y() + bar.get_height() / 2,
            text,
            va="center",
            ha="left",
        )

    ax.set_xlim(0, max_cost * 1.1)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote slack overview plot to %s", output_pdf)


def _write_csv(df: pd.DataFrame, output_csv: Path) -> None:
    if df.empty:
        df = pd.DataFrame(columns=["quantity", "unit", "cost_bnusd"])

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, float_format="%.6g")
    logger.info("Wrote slack overview table to %s", output_csv)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    logger.info("Loading solved network from %s", snakemake.input.network)
    network = load_solved_network(snakemake.input.network)

    # Get slack marginal cost from config (for production slack calculation)
    slack_cost = float(snakemake.config["validation"]["slack_marginal_cost"])

    slack_df = _collect_slack(network, slack_cost)

    _plot(slack_df, Path(snakemake.output.pdf))
    _write_csv(slack_df, Path(snakemake.output.csv))
