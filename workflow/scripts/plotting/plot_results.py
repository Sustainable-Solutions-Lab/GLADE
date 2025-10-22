# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pandas as pd
import pypsa
import matplotlib

matplotlib.use("pdf")  # Use PDF backend
import matplotlib.pyplot as plt
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def extract_crop_production(n: pypsa.Network) -> pd.Series:
    """Extract total crop production aggregated across regions/classes.

    Link naming convention from build_model.py:
    produce_{crop}_{region}_class{resource_class}
    We aggregate by the {crop} token only.
    """
    crop_totals: dict[str, float] = {}

    # All crop production links
    production_links = [link for link in n.links.index if link.startswith("produce_")]

    for link in production_links:
        # Extract crop token between 'produce_' and the next underscore
        # Fallback to conservative behavior if pattern unexpected
        try:
            crop = link.split("_", 2)[1]
        except Exception:
            crop = link.replace("produce_", "").split("_")[0]

        # Flow at bus1 is crop output (tonnes)
        flow = float(n.links_t.p1.loc["now", link])
        production = abs(flow)
        crop_totals[crop] = crop_totals.get(crop, 0.0) + production

    return pd.Series(crop_totals).sort_index()


def extract_food_production(n: pypsa.Network) -> pd.Series:
    """Extract food production from solved network."""
    food_production = {}

    # Get all food conversion links (those starting with "convert_")
    conversion_links = [link for link in n.links.index if link.startswith("convert_")]

    for link in conversion_links:
        # Extract food name from link name
        # Link format is "convert_{crop}_to_{food}"
        parts = link.split("_to_")
        if len(parts) == 2:
            food = parts[1].replace("_", " ")
            # Get the flow through this link
            flow = n.links_t.p0.loc["now", link]
            # The output flow is the food production
            production = abs(flow * n.links.loc[link, "efficiency"])

            if food in food_production:
                food_production[food] += production
            else:
                food_production[food] = production

    return pd.Series(food_production)


def plot_crop_production(crop_production: pd.Series, output_dir: Path) -> None:
    """Create bar plot for crop production; always writes a PDF."""
    # Sort by production value for better visualization
    ser = crop_production.fillna(0.0).astype(float)
    ser = ser[ser > 0]
    ser = ser.sort_values(ascending=False)

    plt.figure(figsize=(12, 8))
    if len(ser) == 0:
        plt.text(0.5, 0.5, "No crop production found", ha="center", va="center")
        plt.axis("off")
    else:
        plt.bar(range(len(ser)), ser.values)
        max_value = ser.max()
        for i, (crop, value) in enumerate(ser.items()):
            plt.text(
                i,
                value + max_value * 0.01,
                f"{value:.1e}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
        plt.xlabel("Crops")
        plt.ylabel("Production (tonnes)")
        plt.xticks(range(len(ser)), ser.index, rotation=45, ha="right")
        plt.grid(True, alpha=0.3)
    plt.title("Crop Production by Type")
    plt.tight_layout()

    out = output_dir / "crop_production.pdf"
    plt.savefig(out, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Crop production plot saved to %s", out)


## Removed: food production plot (slow and cluttered)


def plot_resource_usage(n: pypsa.Network, output_dir: Path) -> None:
    """Create bar plot for resource usage; always writes a PDF."""
    resources = ["land", "water", "fertilizer"]
    resource_usage = {}

    def _snapshot_series(attr: str) -> pd.Series:
        table = getattr(n.links_t, attr, None)
        if table is None or "now" not in table.index:
            return pd.Series(dtype=float)
        return table.loc["now"]

    p0_now = _snapshot_series("p0")
    p2_now = _snapshot_series("p2")
    p3_now = _snapshot_series("p3")

    links = n.links
    has_bus2 = "bus2" in links.columns and "efficiency2" in links.columns
    has_bus3 = "bus3" in links.columns and "efficiency3" in links.columns

    for resource in resources:
        total_flow = 0.0

        bus0_idx = links.index[links["bus0"] == resource]
        if not p0_now.empty and len(bus0_idx) > 0:
            total_flow += p0_now.reindex(bus0_idx, fill_value=0.0).abs().sum()

        if has_bus2 and not p2_now.empty:
            mask2 = (links["bus2"] == resource) & (
                links["efficiency2"].fillna(0.0) < 0.0
            )
            bus2_idx = links.index[mask2]
            if len(bus2_idx) > 0:
                total_flow += p2_now.reindex(bus2_idx, fill_value=0.0).abs().sum()

        if has_bus3 and not p3_now.empty:
            mask3 = (links["bus3"] == resource) & (
                links["efficiency3"].fillna(0.0) < 0.0
            )
            bus3_idx = links.index[mask3]
            if len(bus3_idx) > 0:
                total_flow += p3_now.reindex(bus3_idx, fill_value=0.0).abs().sum()

        resource_usage[resource] = total_flow

    resource_series = pd.Series(resource_usage)

    plt.figure(figsize=(10, 6))
    if resource_series.sum() <= 0:
        plt.text(0.5, 0.5, "No resource usage found", ha="center", va="center")
        plt.axis("off")
    else:
        plt.bar(resource_series.index, resource_series.values)
        # Add value labels on bars with appropriate units
        units = {"land": "ha", "water": "m³", "fertilizer": "Mt"}
        max_value = resource_series.max()
        for i, (resource, value) in enumerate(resource_series.items()):
            unit = units.get(resource, "units")
            plt.text(
                i,
                value + max_value * 0.01,
                f"{value:.1e} {unit}",
                ha="center",
                va="bottom",
                fontsize=10,
            )
        plt.xlabel("Resources")
        plt.ylabel("Usage")
        plt.grid(True, alpha=0.3)
    plt.title("Primary Resource Usage")
    plt.tight_layout()

    out = output_dir / "resource_usage.pdf"
    plt.savefig(out, bbox_inches="tight", dpi=300)
    plt.close()
    logger.info("Resource usage plot saved to %s", out)


if __name__ == "__main__":
    # Load the solved network
    logger.info("Loading solved network...")
    n = pypsa.Network(snakemake.input.network)

    # Output directory from params
    output_dir = Path(snakemake.params.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Creating plots in %s", output_dir)

    # Extract data
    logger.info("Extracting crop production data...")
    crop_production = extract_crop_production(n)
    logger.info("Found %d crops with production data", len(crop_production))

    logger.info("Extracting food production data (for CSV)...")
    food_production = extract_food_production(n)
    logger.info("Found %d foods with production data", len(food_production))

    # Create plots
    plot_crop_production(crop_production, output_dir)
    # Skipped food production PDF on purpose
    plot_resource_usage(n, output_dir)

    # Save summary data as CSV for reference (always write files)
    crop_production.to_csv(
        output_dir / "crop_production.csv", header=["production_tonnes"]
    )
    food_production.to_csv(
        output_dir / "food_production.csv", header=["production_tonnes"]
    )

    logger.info("Plotting completed successfully!")
