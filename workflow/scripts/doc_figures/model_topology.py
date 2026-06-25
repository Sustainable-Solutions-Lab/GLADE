# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""High-level conceptual topology of the GLADE food-system network.

A hand-laid diagram of the major commodities and resources (PyPSA buses) and
the processes connecting them (PyPSA links). The layout is fixed by design
(it does not depend on a solved network), so the figure is drawn directly with
matplotlib rather than auto-routed. Material flows are solid, emission flows
are light, and the dashed loop is manure nitrogen recycled to fertilizer.

Deliberate simplifications for legibility (the full network has more detail):

* Trade is omitted (crops, food, and feed are traded between regions via hubs).
* Biomass/biofuel and fibre sinks are omitted.
* "Feed" is one node; the model resolves seven feed pools (four ruminant
  quality classes and three monogastric).
* Food by-products/co-products are folded into food and feed.
* "Fertilizer" combines synthetic supply and recycled manure nitrogen; only the
  manure loop is drawn, and synthetic-fertilizer N2O is folded into the crop
  N2O arrow.
* "Land" is one node: cropland vs. pasture, productivity classes, and
  existing vs. new land are collapsed. The land -> CO2 arrow stands for both
  land-use-change emissions and the spared-land sink (i.e. it is bidirectional).
* Emission sources are limited to the dominant ones; rice-paddy CH4 is omitted.
* Nutrient and food-group detail is folded into "diet".
"""

import logging
from pathlib import Path

import matplotlib.patches as mpatches
from matplotlib.path import Path as MplPath
import matplotlib.pyplot as plt

from workflow.scripts.doc_figures_config import (
    COLORS,
    FIGURE_WIDTH,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

INK = "#333333"

# Per-role box styling (border, fill).
ROLE_STYLE = {
    "resource": (COLORS["primary"], "#e9f2ec"),  # green
    "commodity": ("#555555", "#ffffff"),  # neutral / white
    "diet": (COLORS["warning"], "#fdf0e2"),  # orange
    "health": (COLORS["danger"], "#fbe7e1"),  # red-orange
    "emission": ("#5f7079", "#eef2f4"),  # slate
    "ghg": ("#3f535c", "#dbe4e8"),  # emphasised slate
}

MAT_ARROW = INK  # material flow
EMI_ARROW = "#9aabb4"  # emission flow (light)
MANURE = COLORS["accent"]  # recycled-manure loop

# Node registry: name -> (x, y, role, label, half-width, half-height).
NODES = {
    "water": (0.0, 4.2, "resource", "water", 0.62, 0.30),
    "fertilizer": (0.0, 3.2, "resource", "fertilizer", 0.72, 0.30),
    "land": (0.0, 2.2, "resource", "land", 0.62, 0.30),
    "crops": (2.6, 4.2, "commodity", "crops", 0.62, 0.30),
    "grassland": (2.6, 2.2, "commodity", "grassland", 0.72, 0.30),
    "residues": (4.6, 3.55, "commodity", "residues", 0.66, 0.30),
    "feed": (4.6, 2.2, "commodity", "feed", 0.55, 0.30),
    "animal": (7.0, 2.2, "commodity", "animal\nproducts", 0.72, 0.44),
    "food": (7.0, 4.2, "commodity", "food", 0.55, 0.30),
    "diet": (9.1, 4.2, "diet", "diet", 0.55, 0.30),
    "health": (9.1, 3.0, "health", "health", 0.60, 0.30),
    "co2": (2.2, 0.55, "emission", r"CO$_2$", 0.50, 0.30),
    "n2o": (4.6, 0.55, "emission", r"N$_2$O", 0.50, 0.30),
    "ch4": (7.0, 0.55, "emission", r"CH$_4$", 0.50, 0.30),
    "ghg": (4.6, -0.5, "ghg", "GHG", 0.50, 0.30),
}

# Material flows (solid, dark) and emission flows (light).
MATERIAL_EDGES = [
    ("water", "crops"),
    ("fertilizer", "crops"),
    ("land", "crops"),
    ("land", "grassland"),
    ("crops", "residues"),
    ("crops", "feed"),
    ("crops", "food"),
    ("grassland", "feed"),
    ("residues", "feed"),
    ("feed", "animal"),
    ("animal", "food"),
    ("food", "diet"),
    ("diet", "health"),
]
EMISSION_EDGES = [
    ("land", "co2"),
    ("crops", "n2o"),
    ("animal", "n2o"),
    ("animal", "ch4"),
    ("co2", "ghg"),
    ("n2o", "ghg"),
    ("ch4", "ghg"),
]


def _edge_point(name, toward):
    """Point on the border of box ``name`` along the ray to ``toward`` (x, y)."""
    cx, cy, *_rest = NODES[name]
    hw, hh = NODES[name][4], NODES[name][5]
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def _arrow(ax, p0, p1, color, lw, dashed=False, rad=0.0, zorder=3):
    ax.add_patch(
        mpatches.FancyArrowPatch(
            p0,
            p1,
            arrowstyle="-|>",
            mutation_scale=11,
            connectionstyle=f"arc3,rad={rad}",
            color=color,
            lw=lw,
            linestyle=(0, (4, 2)) if dashed else "solid",
            shrinkA=0,
            shrinkB=0,
            zorder=zorder,
        )
    )


def build_figure():
    apply_doc_style()

    xlim = (-1.5, 9.95)
    ylim = (-1.15, 4.75)
    aspect = (ylim[1] - ylim[0]) / (xlim[1] - xlim[0])
    fig = plt.figure(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * aspect))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")

    # Recessed emissions lane (behind nodes).
    ax.add_patch(
        mpatches.FancyBboxPatch(
            (1.5, -0.95),
            7.55 - 1.5,
            1.05 - (-0.95),
            boxstyle="round,pad=0,rounding_size=0.12",
            facecolor="#eef2f4",
            edgecolor="none",
            zorder=0,
        )
    )
    ax.text(
        1.62,
        -0.72,
        "emissions",
        ha="left",
        va="center",
        fontsize=9.5,
        color="#5f7079",
        zorder=1,
    )

    # Material + emission edges (straight, clipped to box borders).
    for a, b in MATERIAL_EDGES:
        p0 = _edge_point(a, NODES[b][:2])
        p1 = _edge_point(b, NODES[a][:2])
        _arrow(ax, p0, p1, MAT_ARROW, 1.4)
    for a, b in EMISSION_EDGES:
        p0 = _edge_point(a, NODES[b][:2])
        p1 = _edge_point(b, NODES[a][:2])
        _arrow(ax, p0, p1, EMI_ARROW, 1.0, zorder=2)

    # residues -> N2O (soil incorporation), bowed right around the feed node.
    rx, ry, *_ = NODES["residues"]
    nx, ny, *_ = NODES["n2o"]
    _arrow(
        ax,
        (rx + 0.30, ry - 0.30),
        (nx + 0.30, ny + 0.30),
        EMI_ARROW,
        1.0,
        rad=-0.4,
        zorder=2,
    )

    # Manure loop: leaves animal products at 45 from its lower-left corner,
    # runs below feed/grassland, up the left side, and into the fertilizer
    # lower-left corner at 45. Drawn as one dashed arrow so the whole path
    # (including both 45 segments) is dashed.
    a_bottom = (NODES["animal"][0], NODES["animal"][1] - NODES["animal"][5])
    fx, fy = NODES["fertilizer"][0], NODES["fertilizer"][1]
    f_corner = (fx - NODES["fertilizer"][4], fy - NODES["fertilizer"][5])
    run_y = 1.35
    verts = [
        a_bottom,
        (a_bottom[0] - (a_bottom[1] - run_y), run_y),  # 45 down-left to the run
        (f_corner[0] - 0.4, run_y),  # horizontal left
        (f_corner[0] - 0.4, f_corner[1] - 0.4),  # vertical riser
        f_corner,  # 45 into the corner
    ]
    ax.add_patch(
        mpatches.FancyArrowPatch(
            path=MplPath(verts, [MplPath.MOVETO] + [MplPath.LINETO] * 4),
            arrowstyle="-|>",
            mutation_scale=11,
            color=MANURE,
            lw=1.1,
            linestyle=(0, (4, 2)),
            shrinkA=0,
            shrinkB=0,
            zorder=2,
        )
    )
    ax.text(
        1.7,
        run_y,
        "manure N",
        ha="center",
        va="center",
        fontsize=8.5,
        color=MANURE,
        zorder=3,
        bbox={"boxstyle": "round,pad=0.1", "fc": "white", "ec": "none"},
    )

    # Nodes on top.
    for cx, cy, role, label, hw, hh in NODES.values():
        border, fill = ROLE_STYLE[role]
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (cx - hw, cy - hh),
                2 * hw,
                2 * hh,
                boxstyle="round,pad=0,rounding_size=0.1",
                facecolor=fill,
                edgecolor=border,
                lw=1.3,
                zorder=4,
            )
        )
        ax.text(
            cx,
            cy,
            label,
            ha="center",
            va="center",
            fontsize=11,
            color=INK,
            zorder=5,
            linespacing=0.95,
        )

    return fig


def main(svg_path, png_path, log_path=None):
    global logger
    if log_path is not None:
        logger = setup_script_logging(log_path)
    logger.info("Building model topology diagram")
    fig = build_figure()
    Path(svg_path).parent.mkdir(parents=True, exist_ok=True)
    save_doc_figure(fig, svg_path, format="svg")
    save_doc_figure(fig, png_path, format="png", dpi=300)
    plt.close(fig)
    logger.info("Wrote %s and %s", svg_path, png_path)


if __name__ == "__main__":
    try:
        main(
            snakemake.output.svg,  # type: ignore[name-defined]
            snakemake.output.png,  # type: ignore[name-defined]
            snakemake.log[0],  # type: ignore[name-defined]
        )
    except NameError:
        import sys

        out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/model_topology.svg"
        main(out, str(Path(out).with_suffix(".png")))
