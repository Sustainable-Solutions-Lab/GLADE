#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Apply custom styling and text wrapping to Snakemake workflow graph.

Reads a DOT format graph and applies documentation-consistent styling:
- Brand colors from doc theme
- Text wrapping for long rule names
- Clean, minimal design
"""

import textwrap

import pydot

from workflow.scripts.doc_figures_config import COLORS


def wrap_label(label: str, max_width: int = 16) -> str:
    """Wrap long rule names with newlines.

    Args:
        label: Rule name to wrap
        max_width: Maximum characters per line

    Returns:
        Wrapped label with \\n for line breaks
    """
    if len(label) <= max_width:
        return label

    # Replace underscores with spaces for better wrapping
    label_spaced = label.replace("_", " ")
    wrapped = textwrap.fill(label_spaced, width=max_width, break_long_words=False)
    # Restore underscores
    wrapped = wrapped.replace(" ", "_")

    return wrapped


def main(input_path: str, output_path: str):
    """Apply custom styling to workflow graph.

    Args:
        input_path: Path to input DOT file
        output_path: Path for output styled DOT file
    """
    # Read the DOT file
    graphs = pydot.graph_from_dot_file(input_path)
    if not graphs:
        raise ValueError(f"Could not parse DOT file: {input_path}")
    graph = graphs[0]

    # Apply graph-level styling
    graph.set_bgcolor("white")
    graph.set_rankdir("LR")  # Left to right layout
    graph.set_ranksep("0.4")  # Spacing between ranks (horizontal)
    graph.set_nodesep("0.3")  # Spacing between nodes in same rank (vertical)

    # Apply styling to each node individually
    for node in graph.get_nodes():
        if node.get_name() in ["node", "edge", "graph"]:
            continue  # Skip defaults

        # Get current label
        label = node.get_label()
        if label:
            # Remove quotes
            label = label.strip('"')
            # Wrap text
            wrapped = wrap_label(label)
            # Set new label
            node.set_label(f'"{wrapped}"')

        # Override node styling with brand colors
        node.set_shape("box")
        node.set_style("filled,rounded")
        node.set_fillcolor(COLORS["primary"])  # Brand green
        node.set_fontcolor("white")
        node.set_fontname("sans-serif")
        node.set_fontsize("10")
        node.set_penwidth("0")  # No border
        node.set_margin("0.08,0.06")  # Tighter margins (horizontal, vertical)

    # Apply styling to each edge
    for edge in graph.get_edges():
        edge.set_color("#999999")
        edge.set_penwidth("1.5")
        edge.set_arrowsize("0.7")

    # Write styled DOT file
    graph.write(output_path)


if __name__ == "__main__":
    # Snakemake integration
    main(
        input_path=snakemake.input.dot,
        output_path=snakemake.output.dot,
    )
