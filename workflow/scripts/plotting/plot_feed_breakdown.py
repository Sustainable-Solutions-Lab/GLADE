# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot dry-matter feed use by animal and feed category."""

import logging
from pathlib import Path

import matplotlib
import pandas as pd
import pypsa

matplotlib.use("pdf")
import matplotlib.pyplot as plt

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.snakemake_utils import load_solved_network

logger = logging.getLogger(__name__)


PRODUCT_TO_ANIMAL = {
    "meat-cattle": "Cattle",
    "dairy": "Cattle",
    "meat-pig": "Pigs",
    "meat-chicken": "Chicken",
    "eggs": "Chicken",
    "meat-sheep": "Sheep",
    "meat-goat": "Goats",
    "meat-buffalo": "Buffalo",
    "milk-sheep": "Sheep",
    "milk-goat": "Goats",
    "milk-buffalo": "Buffalo",
}

FEED_CATEGORY_LABELS = {
    "ruminant_forage": "Grass & fodder",
    "ruminant_roughage": "Crop residues",
    "ruminant_grain": "Grains",
    "ruminant_protein": "Oilseed cakes",
    "monogastric_grain": "Grains",
    "monogastric_low_quality": "By-products",
    "monogastric_protein": "Oilseed cakes",
}

# Order used for both legend and bar stacking
FEED_ORDER = [
    "Grass & fodder",
    "Crop residues",
    "Oilseed cakes",
    "By-products",
    "Grains",
]

FEED_COLOR_OVERRIDES = {
    "Grass & fodder": "#4f9d69",
    "Crop residues": "#8c6b4f",
    "Oilseed cakes": "#b8de6f",
    "By-products": "#7b6ba8",
    "Grains": "#d95f02",
}


def _map_animal(product: str) -> str:
    """Convert model product names to display animal categories."""

    if not product:
        return "Unknown"

    if product in PRODUCT_TO_ANIMAL:
        return PRODUCT_TO_ANIMAL[product]

    if product.startswith("meat-"):
        return product.split("-", 1)[1].replace("_", " ").title()

    return str(product).replace("_", " ").title()


def _map_feed_category(feed_category: str) -> str:
    """Convert feed category column values to human-friendly labels."""
    return FEED_CATEGORY_LABELS.get(
        feed_category, feed_category.replace("_", " ").title()
    )


def _extract_feed_use(n: pypsa.Network) -> pd.DataFrame:
    """Return long-form feed use (Mt DM) by animal and feed category."""

    links_static = n.links.static
    if links_static.empty:
        return pd.DataFrame(columns=["animal", "feed_category", "feed_mt"])

    # Filter for animal production links using carrier and feed_category columns
    feed_links = links_static[
        (links_static["carrier"] == "animal_production")
        & links_static["product"].notna()
        & links_static["feed_category"].notna()
    ]

    if feed_links.empty:
        logger.info("No feed→animal links found in network")
        return pd.DataFrame(columns=["animal", "feed_category", "feed_mt"])

    # Sum over snapshots with objective weightings (keeps units in Mt/year)
    links_dynamic = n.links.dynamic
    if not hasattr(links_dynamic, "p0") or links_dynamic.p0.empty:
        logger.warning("Network is missing link flow data (p0); returning empty frame")
        return pd.DataFrame(columns=["animal", "feed_category", "feed_mt"])

    weights = n.snapshot_weightings["objective"]
    p0 = links_dynamic.p0.loc[:, feed_links.index]
    weighted = p0.multiply(weights, axis=0)
    totals = weighted.sum()

    records = []
    for link_name, value in totals.items():
        flow_mt = abs(float(value))
        if flow_mt <= 0.0:
            continue

        product = str(feed_links.at[link_name, "product"])
        animal = _map_animal(product)

        # Skip links without a valid animal product (e.g., trade links)
        if animal == "Unknown":
            continue

        raw_category = str(feed_links.at[link_name, "feed_category"])
        feed_category = _map_feed_category(raw_category)

        records.append(
            {
                "animal": animal,
                "feed_category": feed_category,
                "feed_mt": flow_mt,
            }
        )

    return pd.DataFrame(records)


def _pivot_for_plot(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long-form feed use into animal x feed_category wide format."""

    if df.empty:
        return pd.DataFrame()

    wide = df.pivot_table(
        index="animal",
        columns="feed_category",
        values="feed_mt",
        aggfunc="sum",
        fill_value=0.0,
    )

    totals = wide.sum(axis=1).sort_values(ascending=False)
    wide = wide.loc[totals.index]

    ordered_cols = [cat for cat in FEED_ORDER if cat in wide.columns]
    unordered_cols = [c for c in wide.columns if c not in ordered_cols]
    return wide[ordered_cols + unordered_cols]


def _plot_feed_breakdown(wide: pd.DataFrame, output_pdf: Path) -> None:
    """Render stacked horizontal bars of feed use."""

    plt.figure(figsize=(10, 6))
    ax = plt.gca()

    if wide.empty:
        ax.text(0.5, 0.5, "No feed flows in network", ha="center", va="center")
        ax.axis("off")
    else:
        categories = list(wide.columns)
        colors = categorical_colors(categories, overrides=FEED_COLOR_OVERRIDES)
        left = pd.Series(0.0, index=wide.index)

        for cat in categories:
            values = wide[cat]
            ax.barh(
                wide.index,
                values,
                left=left,
                color=colors[cat],
                edgecolor="black",
                linewidth=0.4,
                label=cat,
            )
            left = left + values

        ax.set_xlabel("Mt of DM")
        ax.set_ylabel("Animal")
        ax.invert_yaxis()  # Largest animals at top
        ax.grid(axis="x", alpha=0.3)
        ax.legend(
            title="Feed",
            bbox_to_anchor=(1.02, 1),
            loc="upper left",
            borderaxespad=0,
        )
        plt.tight_layout()

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote feed breakdown plot to %s", output_pdf)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    network = load_solved_network(snakemake.input.network)

    feed_long = _extract_feed_use(network)

    wide = _pivot_for_plot(feed_long)

    csv_path = Path(snakemake.output.csv)
    pdf_path = Path(snakemake.output.pdf)

    feed_long.to_csv(csv_path, index=False)
    logger.info("Wrote feed breakdown table to %s", csv_path)

    _plot_feed_breakdown(wide, pdf_path)
