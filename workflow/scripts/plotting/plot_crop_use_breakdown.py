# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create a stacked bar chart of global crop uses (human vs. animal feed)."""

from collections import defaultdict
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


def _extract_crop_production(
    n: pypsa.Network,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Aggregate crop production (megatonnes) over all regions/resource classes.

    Returns total, irrigated, and rainfed production separately.
    """

    if "now" not in n.snapshots:
        raise ValueError("Expected snapshot 'now' in solved network")

    production = {}
    irrigated = {}
    rainfed = {}
    links_dynamic = n.links.dynamic
    flows = (
        links_dynamic.p1.loc["now"]
        if hasattr(links_dynamic, "p1") and not links_dynamic.p1.empty
        else pd.Series(dtype=float)
    )
    links_df = n.links.static

    for link in links_df.index:
        carrier = str(links_df.at[link, "carrier"])

        # Filter for crop production links using carrier
        if carrier != "crop_production":
            continue

        # Use crop column for metadata
        # Skip links without crop metadata (NA or empty string per PyPSA convention)
        crop = links_df.at[link, "crop"]
        if pd.isna(crop) or crop == "":
            continue
        crop_token = str(crop)

        # Get water supply from link attribute
        water_supply = str(links_df.at[link, "water_supply"])

        value = abs(float(flows.get(link, 0.0)))
        if value <= 0.0:
            continue

        production[crop_token] = production.get(crop_token, 0.0) + value

        if water_supply == "irrigated":
            irrigated[crop_token] = irrigated.get(crop_token, 0.0) + value
        elif water_supply == "rainfed":
            rainfed[crop_token] = rainfed.get(crop_token, 0.0) + value

    # Add grassland production
    grassland_links = links_df[links_df["carrier"] == "grassland_production"]
    pasture_total = sum(
        abs(float(flows.get(link, 0.0)))
        for link in grassland_links.index
        if abs(float(flows.get(link, 0.0))) > 0.0
    )

    if pasture_total > 0.0:
        production["grassland"] = pasture_total
        rainfed["grassland"] = pasture_total

    return (
        pd.Series(production, dtype=float),
        pd.Series(irrigated, dtype=float),
        pd.Series(rainfed, dtype=float),
    )


def _extract_crop_use(n: pypsa.Network) -> tuple[pd.Series, pd.Series]:
    """Return crop use split into human consumption vs. animal feed (megatonnes).

    Human consumption is tracked via pathway links (crop → food processing).
    Animal feed is tracked via convert_to_feed links from crop buses.
    """

    if "now" not in n.snapshots:
        raise ValueError("Expected snapshot 'now' in solved network")

    human_use: defaultdict[str, float] = defaultdict(float)
    feed_use: defaultdict[str, float] = defaultdict(float)

    links_dynamic = n.links.dynamic
    flows_p0 = (
        links_dynamic.p0.loc["now"]
        if hasattr(links_dynamic, "p0") and not links_dynamic.p0.empty
        else pd.Series(dtype=float)
    )
    flows_p1 = (
        links_dynamic.p1.loc["now"]
        if hasattr(links_dynamic, "p1") and not links_dynamic.p1.empty
        else pd.Series(dtype=float)
    )
    links_df = n.links.static

    for link in links_df.index:
        flow_in = abs(float(flows_p0.get(link, 0.0)))
        if flow_in <= 0.0:
            continue

        carrier = str(links_df.at[link, "carrier"])

        # Human consumption: track via food_processing links (crop → food processing)
        # These links have the crop column set and represent crop use for food
        if carrier == "food_processing":
            crop = links_df.at[link, "crop"]
            if pd.notna(crop):
                human_use[str(crop)] += flow_in
        # Animal feed: track via feed_conversion links from crop buses
        # Only count direct crop→feed conversions (links with crop column set)
        elif carrier == "feed_conversion":
            crop = links_df.at[link, "crop"]
            if pd.notna(crop):
                feed_use[str(crop)] += flow_in

    # Add grassland feed production
    grassland_links = links_df[links_df["carrier"] == "grassland_production"]
    pasture_total = sum(
        abs(float(flows_p1.get(link, 0.0)))
        for link in grassland_links.index
        if abs(float(flows_p1.get(link, 0.0))) > 0.0
    )

    if pasture_total > 0.0:
        feed_use["grassland"] = feed_use.get("grassland", 0.0) + pasture_total

    return pd.Series(human_use, dtype=float), pd.Series(feed_use, dtype=float)


def _build_dataframe(
    production: pd.Series,
    irrigated: pd.Series,
    rainfed: pd.Series,
    human_use: pd.Series,
    feed_use: pd.Series,
    animal_products: set[str],
) -> pd.DataFrame:
    """Combine the individual series into a single dataframe for plotting/export."""

    df = pd.DataFrame(
        {
            "production_mt": production,
            "irrigated_mt": irrigated,
            "rainfed_mt": rainfed,
            "human_consumption_mt": human_use,
            "animal_feed_mt": feed_use,
        }
    ).fillna(0.0)

    if df.empty:
        return df

    df = df[(df > 0).any(axis=1)]

    # Filter out animal products (non-crops)
    animal_products_lower = {str(product).lower() for product in animal_products}
    df = df[~df.index.str.lower().isin(animal_products_lower)]

    if df.empty:
        return df

    df["irrigated_fraction"] = df["irrigated_mt"] / df["production_mt"].replace(
        0, float("nan")
    )

    df["residual_mt"] = df["production_mt"] - df[
        ["human_consumption_mt", "animal_feed_mt"]
    ].sum(axis=1)
    tolerance = 1e-6
    residual_mask = df["residual_mt"].abs() > tolerance
    if residual_mask.any():
        logger.warning(
            "Crop use does not sum to production for: %s",
            ", ".join(df.index[residual_mask]),
        )

    # Sort by total height (human consumption + animal feed)
    df["total_use_mt"] = df["human_consumption_mt"] + df["animal_feed_mt"]
    df.sort_values("total_use_mt", ascending=False, inplace=True)
    df.drop(columns=["total_use_mt"], inplace=True)

    return df


def _plot(df: pd.DataFrame, output_pdf: Path) -> None:
    """Render stacked bar chart for crop uses with irrigation info."""

    plt.figure(figsize=(12, 7))

    if df.empty:
        plt.text(0.5, 0.5, "No crop production data", ha="center", va="center")
        plt.axis("off")
    else:
        x = range(len(df))
        human = df["human_consumption_mt"].to_numpy()
        feed = df["animal_feed_mt"].to_numpy()
        irrigated_frac = df["irrigated_fraction"].fillna(0).to_numpy()

        human_rainfed = human * (1 - irrigated_frac)
        human_irrigated = human * irrigated_frac
        feed_rainfed = feed * (1 - irrigated_frac)
        feed_irrigated = feed * irrigated_frac

        plt.bar(
            x,
            human_rainfed,
            label="Human consumption (rainfed)",
            color="#1f77b4",
            edgecolor="black",
            linewidth=0.5,
        )
        plt.bar(
            x,
            human_irrigated,
            bottom=human_rainfed,
            label="Human consumption (irrigated)",
            color="#1f77b4",
            hatch="///",
            edgecolor="black",
            linewidth=0.5,
        )
        plt.bar(
            x,
            feed_rainfed,
            bottom=human,
            label="Animal feed (rainfed)",
            color="#ff7f0e",
            edgecolor="black",
            linewidth=0.5,
        )
        plt.bar(
            x,
            feed_irrigated,
            bottom=human + feed_rainfed,
            label="Animal feed (irrigated)",
            color="#ff7f0e",
            hatch="///",
            edgecolor="black",
            linewidth=0.5,
        )

        plt.ylabel("Megatonnes (Mt)")
        plt.xticks(x, df.index, rotation=45, ha="right")
        plt.grid(axis="y", alpha=0.3)
        plt.legend()
        plt.tight_layout()

    plt.title("Global crop use breakdown (hatched = irrigated)")
    plt.tight_layout()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Wrote stacked crop use plot to %s", output_pdf)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    network = load_solved_network(snakemake.input.network)
    production, irrigated, rainfed = _extract_crop_production(network)
    human_use, feed_use = _extract_crop_use(network)

    raw_animal_products = getattr(
        snakemake.params,
        "animal_products",
        snakemake.config["animal_products"]["include"],
    )
    animal_products = {str(product) for product in raw_animal_products}

    df = _build_dataframe(
        production, irrigated, rainfed, human_use, feed_use, animal_products
    )

    csv_path = Path(snakemake.output.csv)
    pdf_path = Path(snakemake.output.pdf)

    df.to_csv(csv_path)
    logger.info("Wrote crop use data to %s", csv_path)

    _plot(df, pdf_path)
