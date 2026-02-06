# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate land flow diagram showing cropland and pasture pool structure."""

import logging
from pathlib import Path

import graphviz

from workflow.scripts.doc_figures_config import COLORS, FONT_SIZES
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Colors for the diagram
CLUSTER_BORDER = "#cccccc"  # Faint gray for all cluster borders
EXISTING_LAND = COLORS["primary"]  # Green for existing land flows
NEW_LAND = "#c9a227"  # Dark yellow/gold for new land flows
SPARING = "#7B1FA2"  # Purple for sparing flows
POOL_FILL = "#E3F2FD"  # Light blue fill for pools
POOL_BORDER = COLORS["info"]  # Teal border for pools
DEMAND_FILL = "#FFF3E0"  # Light orange fill for demand


def build_land_flow_diagram() -> graphviz.Digraph:
    """Generate land flow diagram showing cropland and pasture pools.

    Returns
    -------
    graphviz.Digraph
        Graphviz diagram object
    """
    dot = graphviz.Digraph(comment="Land Flow Structure")
    dot.attr(
        rankdir="TB",
        fontname="Helvetica",
        fontsize=str(FONT_SIZES["label"]),
        nodesep="0.5",  # Horizontal spacing between nodes
        ranksep="0.5",  # Vertical spacing between ranks
        splines="true",  # Curved edges
        dpi="300",  # High resolution for PNG output
    )
    dot.attr("node", fontname="Helvetica", fontsize=str(FONT_SIZES["label"]))
    dot.attr("edge", fontname="Helvetica", fontsize=str(FONT_SIZES["legend"]))

    # Define node styles
    supply_style = {"shape": "box", "style": "rounded", "color": CLUSTER_BORDER}
    pool_style = {
        "shape": "box",
        "style": "filled,bold",
        "fillcolor": POOL_FILL,
        "color": POOL_BORDER,
    }
    sink_style = {"shape": "box", "style": "rounded,dashed", "color": SPARING}
    demand_style = {"shape": "ellipse", "style": "filled", "fillcolor": DEMAND_FILL}

    # Supply nodes cluster - rank=same INSIDE cluster with invisible edges
    with dot.subgraph(name="cluster_supply") as s:
        s.attr(label="Land Supply", style="rounded", color=CLUSTER_BORDER, margin="8")
        with s.subgraph() as inner:
            inner.attr(rank="same")
            inner.node("existing", "Existing\nCropland\nBaseline", **supply_style)
            inner.node(
                "grassland_convertible",
                "Current Grassland\n(Cropland-suitable)",
                **supply_style,
            )
            inner.node(
                "grassland_marginal",
                "Current Grassland\n(Marginal)",
                **supply_style,
            )
            inner.node("new", "New Land\n(Expansion\nPotential)", **supply_style)
            inner.edge("existing", "grassland_convertible", style="invis")
            inner.edge("grassland_convertible", "grassland_marginal", style="invis")
            inner.edge("grassland_marginal", "new", style="invis")

    # Pools cluster
    with dot.subgraph(name="cluster_pools") as p:
        p.attr(label="Land Pools", style="rounded", color=CLUSTER_BORDER, margin="8")
        with p.subgraph() as inner:
            inner.attr(rank="same")
            inner.node(
                "cropland_pool", "Cropland Pool\n(per region/class/water)", **pool_style
            )
            inner.node("pasture_pool", "Pasture Pool\n(per region/class)", **pool_style)
            inner.edge("cropland_pool", "pasture_pool", style="invis")

    # Spared land cluster
    with dot.subgraph(name="cluster_sinks") as k:
        k.attr(
            label="Spared Land (Sequestration)",
            style="rounded",
            color=CLUSTER_BORDER,
            margin="8",
        )
        with k.subgraph() as inner:
            inner.attr(rank="same")
            inner.node("spared_grass", "Spared\nGrassland", **sink_style)
            inner.node("spared_crop", "Spared\nCropland", **sink_style)
            inner.edge("spared_grass", "spared_crop", style="invis")

    # Production cluster
    with dot.subgraph(name="cluster_demand") as d:
        d.attr(label="Production", style="rounded", color=CLUSTER_BORDER, margin="8")
        with d.subgraph() as inner:
            inner.attr(rank="same")
            inner.node("crops", "Crop\nProduction", **demand_style)
            inner.node("grazing", "Grassland\nProduction", **demand_style)
            inner.edge("crops", "grazing", style="invis")

    # Edges: Existing land to pools (green, solid)
    dot.edge("existing", "cropland_pool", "land_use", color=EXISTING_LAND)
    dot.edge("existing", "pasture_pool", "existing_to_pasture", color=EXISTING_LAND)

    # Edges: New land to pools (dark yellow, solid)
    dot.edge(
        "new", "cropland_pool", "land_conversion\n(+LUC emissions)", color=NEW_LAND
    )
    dot.edge("new", "pasture_pool", "new_to_pasture\n(+LUC emissions)", color=NEW_LAND)

    # Edges: Current grassland pools to pasture pool (green, solid)
    dot.edge(
        "grassland_convertible",
        "pasture_pool",
        "existing_grassland\n(convertible)\n_to_pasture",
        color=EXISTING_LAND,
    )
    dot.edge(
        "grassland_marginal",
        "pasture_pool",
        "existing_grassland\n(marginal)\n_to_pasture",
        color=EXISTING_LAND,
    )

    # Edges: Supply to sparing sinks (purple, dashed)
    dot.edge(
        "existing",
        "spared_crop",
        "spare_land\n(-sequestration)",
        color=SPARING,
        style="dashed",
    )
    dot.edge(
        "grassland_convertible",
        "spared_grass",
        "spare_existing_grassland\n(convertible)",
        color=SPARING,
        style="dashed",
    )
    dot.edge(
        "grassland_marginal",
        "spared_grass",
        "spare_existing_grassland\n(marginal)",
        color=SPARING,
        style="dashed",
    )

    # Edges: Pools to demand
    dot.edge("cropland_pool", "crops", color=POOL_BORDER)
    dot.edge("pasture_pool", "grazing", color=POOL_BORDER)

    return dot


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    svg_path = Path(snakemake.output.svg)  # type: ignore[name-defined]
    png_path = Path(snakemake.output.png)  # type: ignore[name-defined]

    logger.info("Building land flow diagram")
    diagram = build_land_flow_diagram()

    # Render to both formats
    svg_path.parent.mkdir(parents=True, exist_ok=True)

    # Render SVG
    diagram.render(svg_path.with_suffix(""), format="svg", cleanup=True)
    logger.info("Wrote land flow diagram SVG to %s", svg_path)

    # Render PNG with higher DPI for quality
    diagram.render(png_path.with_suffix(""), format="png", cleanup=True)
    logger.info("Wrote land flow diagram PNG to %s", png_path)


if __name__ == "__main__":
    main()
