#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a high-level topology visualization of the food systems model.

This script aggregates the detailed PyPSA network into a simplified graph
showing major material flows between conceptual nodes (land, crops, feed,
animal products, food, nutrients, emissions).
"""

import logging
from pathlib import Path

import graphviz
import pypsa

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def categorize_bus(bus_name: str, carrier: str) -> str | None:
    """Categorize a bus into a high-level node type.

    Returns None if the bus should not appear in the high-level topology.
    """
    # Land
    if carrier.startswith("land"):
        return "Land"

    # Water
    if carrier == "water" or bus_name.startswith("water_"):
        return "Water"

    # Crops (grown on land)
    if carrier.startswith("crop_") and not bus_name.startswith("food_"):
        return "Crops"

    # Crop residues
    if carrier.startswith("residue_"):
        return "Crop Residues"

    # Animal feed categories
    if any(
        x in bus_name
        for x in [
            "ruminant_",
            "monogastric_",
            "_energy",
            "_protein",
            "_roughage",
            "_grassland",
        ]
    ):
        if "ruminant" in bus_name:
            return "Ruminant Feed"
        elif "monogastric" in bus_name:
            return "Monogastric Feed"

    # Animal products
    if any(
        x in bus_name for x in ["meat-cattle", "dairy", "meat-pig", "chicken", "eggs"]
    ):
        return "Animal Products"

    # Food items ready for consumption
    if bus_name.startswith("food_"):
        # Check if it's a byproduct (bran, meal, hulls, germ)
        if any(bp in bus_name.lower() for bp in ["bran", "meal", "hull", "germ"]):
            return "Food Byproducts"
        return "Food"

    # Biomass for energy sector
    if carrier == "biomass" or bus_name.startswith("biomass_"):
        return "Biomass"

    # Nutrients
    if any(x in bus_name for x in ["carb_", "fat_", "protein_", "kcal_"]):
        return "Nutrients"

    # Fertilizer application
    if "fertilizer" in bus_name or carrier == "fertilizer":
        return "Fertilizer"

    # Individual greenhouse gases
    if carrier == "co2" or bus_name == "co2":
        return "CO2"
    if carrier == "ch4" or bus_name == "ch4":
        return "CH4"
    if carrier == "n2o" or bus_name == "n2o":
        return "N2O"
    if carrier == "ghg" or bus_name == "ghg":
        return "GHG"

    # Skip intermediate nodes like trade hubs
    if bus_name.startswith("hub_"):
        return None

    return None


def build_topology_graph(network_path: str) -> graphviz.Digraph:
    """Build an aggregated topology graph from the PyPSA network."""
    n = pypsa.Network(network_path)

    # Track connections between high-level nodes
    edges = set()

    # Process all links to identify connections
    # Link semantics:
    # - bus0 is always an input
    # - bus1 is always an output
    # - bus2, bus3, etc. are inputs if efficiency < 0, outputs otherwise
    # - All inputs connect to all outputs
    for _idx, link in n.links.static.iterrows():
        # Collect all input and output buses for this link
        inputs = []
        outputs = []

        # bus0 is always an input
        if link["bus0"] in n.buses.static.index:
            bus0_carrier = n.buses.static.loc[link["bus0"], "carrier"]
            cat0 = categorize_bus(link["bus0"], bus0_carrier)
            if cat0:
                inputs.append(cat0)

        # bus1 is always an output
        if link["bus1"] in n.buses.static.index:
            bus1_carrier = n.buses.static.loc[link["bus1"], "carrier"]
            cat1 = categorize_bus(link["bus1"], bus1_carrier)
            if cat1:
                outputs.append(cat1)

        # Check bus2, bus3, etc.
        for bus_key, eff_key in [("bus2", "efficiency2"), ("bus3", "efficiency3")]:
            if bus_key in link.index and link[bus_key] and link[bus_key] != "":
                bus_name = link[bus_key]
                if bus_name in n.buses.static.index:
                    bus_carrier = n.buses.static.loc[bus_name, "carrier"]
                    cat = categorize_bus(bus_name, bus_carrier)
                    if cat:
                        # Negative efficiency = input, positive = output
                        if eff_key in link.index and link[eff_key] < 0:
                            inputs.append(cat)
                        else:
                            outputs.append(cat)

        # Connect each input to each output (but not inputs to inputs)
        for inp in inputs:
            for out in outputs:
                if inp != out:  # Don't add self-loops
                    edges.add((inp, out))

    # Add manual edges that represent implicit model relationships
    # Manure production from animal feed and its outputs
    if "Ruminant Feed" in {
        cat0,
        cat1,
        *[cat for src, dst in edges for cat in (src, dst)],
    }:
        edges.add(("Ruminant Feed", "Manure"))
    if "Monogastric Feed" in {
        cat0,
        cat1,
        *[cat for src, dst in edges for cat in (src, dst)],
    }:
        edges.add(("Monogastric Feed", "Manure"))

    # Check if we have feed nodes (indicating we should add manure flows)
    has_feed = any("Feed" in node for edge in edges for node in edge)
    if has_feed:
        edges.add(("Manure", "CH4"))
        edges.add(("Manure", "N2O"))
        edges.add(("Manure", "Fertilizer"))
        # Add synthetic fertilizer production and merging
        edges.add(("Synthetic Fertilizer", "Fertilizer"))

    # Land use change emissions
    if "Land" in {cat for edge in edges for cat in edge}:
        edges.add(("Land", "CO2"))

    # Crop residues are a byproduct of crop production
    if "Crops" in {cat for edge in edges for cat in edge}:
        edges.add(("Crops", "Crop Residues"))

    # Remove spurious edges
    # 1. Input-to-input edges (e.g., Fertilizer→Water from multi-input links)
    # 2. Reverse edges when bidirectional
    # 3. Specific incorrect edges based on domain knowledge
    input_categories = {"Land", "Water", "Synthetic Fertilizer", "Fertilizer"}

    edges_to_remove = set()
    for src, dst in edges:
        # Remove edges between input categories (they don't produce each other)
        # Exception: Synthetic Fertilizer → Fertilizer represents production/merging
        if src in input_categories and dst in input_categories:
            if not (src == "Synthetic Fertilizer" and dst == "Fertilizer"):
                edges_to_remove.add((src, dst))
        # Remove reverse edges when bidirectional
        elif (dst, src) in edges:
            # Keep only the logical direction based on domain knowledge
            if src == "Crops" and dst == "Water":
                edges_to_remove.add((src, dst))
        # Remove Monogastric Feed → CH4 (much smaller than ruminants, omit for clarity)
        elif (src == "Monogastric Feed" and dst == "CH4") or (
            src == "Food Byproducts" and dst == "Nutrients"
        ):
            edges_to_remove.add((src, dst))
    edges -= edges_to_remove

    # Create graphviz diagram with better layout settings
    dot = graphviz.Digraph(comment="Food Systems Model Topology")
    dot.attr(rankdir="LR", size="11,7", ranksep="0.8", nodesep="0.4", dpi="300")
    dot.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fontname="sans-serif",
        fontsize="11",
    )
    dot.attr("edge", fontname="sans-serif", fontsize="9")

    # Define semantic color scheme
    # Inputs (natural resources and synthetic inputs)
    input_color = "#7B9FAB"  # blue-gray
    # Food products (crops, feed, animal products, food)
    food_color = "#8BC34A"  # green
    # Emissions (all the same)
    emission_color = "#B0B0B0"  # gray

    # Define node colors by category
    node_colors = {
        # Inputs
        "Land": input_color,
        "Water": input_color,
        "Synthetic Fertilizer": input_color,
        "Fertilizer": input_color,
        # Food products (all same green color)
        "Crops": food_color,
        "Crop Residues": food_color,
        "Ruminant Feed": food_color,
        "Monogastric Feed": food_color,
        "Animal Products": food_color,
        "Food": food_color,
        "Food Byproducts": food_color,
        "Manure": food_color,
        "Biomass": "#FF9800",  # orange (energy sector export)
        "Nutrients": "#4CAF50",  # darker green (final nutrition)
        # Emissions (all the same color)
        "CO2": emission_color,
        "CH4": emission_color,
        "N2O": emission_color,
        "GHG": emission_color,
    }

    # Add nodes with rank constraints for layout
    all_nodes = set()
    for src, dst in edges:
        all_nodes.add(src)
        all_nodes.add(dst)

    # Define rank groups for left-to-right layout
    # Column 0: Primary inputs
    col0_nodes = {"Land", "Water", "Synthetic Fertilizer"}
    # Column 1: Combined fertilizer
    col1_nodes = {"Fertilizer"}
    # Column 2: Crops
    col2_nodes = {"Crops"}
    # Column 3: Intermediate products
    col3_nodes = {"Crop Residues", "Food", "Food Byproducts"}
    # Column 4: Animal feed
    col4_nodes = {"Ruminant Feed", "Monogastric Feed"}
    # Column 5: Manure and animal products
    col5_nodes = {"Manure", "Animal Products"}
    # Column 6: Individual emissions
    col6_nodes = {"CO2", "CH4", "N2O"}
    # Column 7: Final outputs (nutrients, biomass exports, and aggregated emissions)
    col7_nodes = {"Nutrients", "Biomass", "GHG"}

    # Add nodes to appropriate subgraphs for ranking
    all_rank_nodes = (
        col0_nodes
        | col1_nodes
        | col2_nodes
        | col3_nodes
        | col4_nodes
        | col5_nodes
        | col6_nodes
        | col7_nodes
    )

    for col_nodes in [
        col0_nodes,
        col1_nodes,
        col2_nodes,
        col3_nodes,
        col4_nodes,
        col5_nodes,
        col6_nodes,
        col7_nodes,
    ]:
        with dot.subgraph() as s:
            s.attr(rank="same")
            for node in col_nodes:
                if node in all_nodes:
                    color = node_colors.get(node, "#E0E0E0")
                    s.node(node, fillcolor=color)

    # Add any remaining nodes not in predefined ranks
    for node in sorted(all_nodes):
        if node not in all_rank_nodes:
            color = node_colors.get(node, "#E0E0E0")
            dot.node(node, fillcolor=color)

    # Add edges
    for src, dst in sorted(edges):
        dot.edge(src, dst)

    return dot


def main(network_path: str, svg_output_path: str, png_output_path: str):
    """Generate model topology diagram.

    Args:
        network_path: Path to built PyPSA network (.nc file)
        svg_output_path: Path for output SVG file
        png_output_path: Path for output PNG file
    """
    logger.info("Loading network from %s", network_path)
    dot = build_topology_graph(network_path)

    # Render SVG
    logger.info("Rendering topology to %s", svg_output_path)
    svg_dir = Path(svg_output_path).parent
    svg_name = Path(svg_output_path).stem
    dot.render(svg_name, directory=svg_dir, format="svg", cleanup=True)

    # Render PNG
    logger.info("Rendering topology to %s", png_output_path)
    png_dir = Path(png_output_path).parent
    png_name = Path(png_output_path).stem
    dot.render(png_name, directory=png_dir, format="png", cleanup=True)

    logger.info("Topology visualization complete!")


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    # Snakemake integration
    main(
        network_path=snakemake.input.model,  # type: ignore[name-defined]
        svg_output_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_output_path=snakemake.output.png,  # type: ignore[name-defined]
    )
