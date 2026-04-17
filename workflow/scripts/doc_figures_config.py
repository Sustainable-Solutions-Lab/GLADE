# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared styling configuration for documentation figures.

This module provides consistent colors, sizes, and styling parameters
for all documentation figures. It complements the matplotlib style sheet
with domain-specific configuration.
"""

import logging
from pathlib import Path

from matplotlib import cm
from matplotlib import colors as mcolors
import numpy as np

# Color palette (matching Furo theme from docs/conf.py)
COLORS = {
    "primary": "#3b745f",  # Brand green (dark)
    "secondary": "#5fa285",  # Brand green (light)
    "accent": "#e07a5f",  # Coral/red
    "neutral": "#f2cc8f",  # Beige
    "highlight": "#81b29a",  # Teal
    "warning": "#f4a261",  # Orange
    "info": "#2a9d8f",  # Cyan
    "danger": "#e76f51",  # Red-orange
}


def _cmap_with_white_base(name: str) -> mcolors.ListedColormap:
    base = cm.get_cmap(name, 256)
    colors = base(np.linspace(0, 1, 256))
    colors[0, :] = np.array([1.0, 1.0, 1.0, 1.0])
    return mcolors.ListedColormap(colors, name=f"{name}_white")


# Common colormaps for different data types (with light backgrounds by default)
COLORMAPS = {
    "yield": _cmap_with_white_base("YlGn"),
    "water": _cmap_with_white_base("Blues"),
    "emissions": _cmap_with_white_base("YlOrRd"),
    "health": cm.get_cmap("RdYlGn_r"),
    "diverging": cm.get_cmap("RdBu_r"),
    "sequential": _cmap_with_white_base("viridis"),
    "biomass": _cmap_with_white_base("Greens"),
    "soil": _cmap_with_white_base("YlGnBu"),
    "forest": _cmap_with_white_base("YlGn"),
    "regrowth": _cmap_with_white_base("YlOrRd"),
}

# Canonical figure width in inches. All doc figures use this width so that
# font sizes appear consistent when rendered at the same display width.
FIGURE_WIDTH = 9

# Typography helpers
FONT_SIZES = {
    "title": 12,
    "label": 10,
    "legend": 9,
    "tick": 9,
    "annotation": 8,
    "colorbar_label": 9,
    "colorbar_tick": 8,
}

# Standard DPI for output
OUTPUT_DPI = 100  # For SVG this doesn't matter much, but good for consistency


class _FindfontFilter(logging.Filter):
    """Filter out matplotlib's verbose findfont messages."""

    def filter(self, record):
        return not record.getMessage().startswith("findfont:")


def apply_doc_style():
    """Apply documentation figure styling to matplotlib.

    This loads the custom style sheet and applies additional
    programmatic styling that can't be set via mplstyle files.
    """
    import matplotlib.pyplot as plt

    # Suppress only findfont messages while keeping other warnings
    font_logger = logging.getLogger("matplotlib.font_manager")
    font_logger.addFilter(_FindfontFilter())

    # Get path to style sheet (relative to this file)
    style_path = Path(__file__).parent / "doc_figures_style.mplstyle"

    if style_path.exists():
        plt.style.use(str(style_path))
    else:
        # Fallback to minimal styling if file not found
        plt.rcParams.update(
            {
                "font.family": "sans-serif",
                "font.size": 10,
                "axes.grid": True,
                "axes.axisbelow": True,
                "grid.alpha": 0.3,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "figure.facecolor": "white",
                "axes.facecolor": "white",
                "savefig.format": "svg",
                "savefig.bbox": "tight",
            }
        )


def save_doc_figure(fig, output_path: str, format: str = "svg", **kwargs):
    """Save figure with consistent settings and normalized width.

    Crops vertical whitespace (like ``bbox_inches="tight"``) but preserves
    the full figure width so that font sizes appear consistent when figures
    are displayed at the same width on the web page.

    Args:
        fig: Matplotlib figure object
        output_path: Output file path (extension will be replaced if needed)
        format: Output format ('svg' or 'png')
        **kwargs: Additional arguments passed to fig.savefig()
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.transforms import Bbox

    output_path = Path(output_path)

    # Ensure correct extension
    if format == "svg":
        output_path = output_path.with_suffix(".svg")
    elif format == "png":
        output_path = output_path.with_suffix(".png")
    else:
        raise ValueError(f"Unsupported format: {format}")

    pad = kwargs.pop("pad_inches", 0.1)

    # Compute tight bounding box using Agg renderer (works regardless of
    # the active backend; returns inches in matplotlib 3.x).
    original_canvas = fig.canvas
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tight_inches = fig.get_tightbbox(renderer)
    fig.set_canvas(original_canvas)

    # Preserve full figure width (no horizontal crop) but crop vertically
    fig_width = fig.get_size_inches()[0]
    bbox = Bbox.from_extents(
        0,
        tight_inches.y0 - pad,
        fig_width,
        tight_inches.y1 + pad,
    )

    save_kwargs = {
        "format": format,
        "bbox_inches": bbox,
        "pad_inches": 0,  # padding already included in bbox
    }

    if format == "png":
        save_kwargs["dpi"] = kwargs.pop("dpi", OUTPUT_DPI)

    # Override with user kwargs
    save_kwargs.update(kwargs)

    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save
    fig.savefig(output_path, **save_kwargs)

    return output_path
