<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# Documentation Figures

This directory contains scripts for generating figures used in the Sphinx documentation.

## Overview

Documentation figures are:
- **Git-tracked** in `docs/_static/figures/`
- **SVG format** for scalability and web display
- **Harmonized styling** matching the Furo theme colors
- Generated using **coarse resolution** config for faster execution

## Usage

### Generate a single figure

```bash
tools/smk --configfile config/default.yaml --configfile docs/config/doc_figures.yaml \
  -- docs/_static/figures/intro_global_coverage.svg
```

### Generate all doc figures

```bash
tools/smk --configfile config/default.yaml --configfile docs/config/doc_figures.yaml \
  -- generate_all_doc_figures
```

### Build documentation with figures

```bash
tools/smk --configfile config/default.yaml --configfile docs/config/doc_figures.yaml \
  -- build_docs
```

## Configuration

### Style Configuration

Styling is defined in two places:

1. **`doc_figures_style.mplstyle`** - Matplotlib style sheet
   - Font settings (sans-serif, size 10)
   - Figure dimensions
   - Color cycle matching Furo theme
   - Grid and spine settings

2. **`doc_figures_config.py`** - Python module
   - Brand colors (primary: #3b745f)
   - Colormaps for different data types
   - Figure size presets
   - Helper functions for crop colors

### Scenario Configuration

`docs/config/doc_figures.yaml` defines a coarse-resolution scenario:
- 250 regions (vs 400 in default)
- 3 resource classes (vs 4)
- 10 trade hubs (vs 20)
- 15 health clusters (vs 30)

This reduces computation time while showcasing global coverage.

## Adding New Figures

To add a new figure:

1. **Add to figure list** in `workflow/rules/documentation.smk`:
   ```python
   DOC_FIGURES = [
       "intro_global_coverage",
       "land_regions_map",
       "your_new_figure",  # Add here
   ]
   ```

2. **Create a rule** in `workflow/rules/documentation.smk`:
   ```python
   rule doc_fig_your_new_figure:
       input:
           data="processing/{DOC_FIG_NAME}/data.csv",
       output:
           svg="docs/_static/figures/your_new_figure.svg",
       script:
           "../scripts/doc_figures/your_new_figure.py"
   ```

3. **Write the script** in `workflow/scripts/doc_figures/your_new_figure.py`:
   ```python
   import matplotlib.pyplot as plt
   from pathlib import Path

   from workflow.scripts.doc_figures_config import (
       apply_doc_style,
       COLORS,
       FIGURE_SIZES,
       save_doc_figure,
   )

   def main(data_path: str, output_path: str):
       apply_doc_style()
       fig, ax = plt.subplots(figsize=FIGURE_SIZES["chart"])
       # ... plotting code ...
       save_doc_figure(fig, output_path, format="svg")
       plt.close(fig)

   if __name__ == "__main__":
       main(
           data_path=snakemake.input.data,
           output_path=snakemake.output.svg,
       )
   ```

4. **Reference in docs** by adding to RST files:
   ```rst
   .. figure:: _static/figures/your_new_figure.svg
      :width: 100%
      :alt: Description

      Caption text here.
   ```

## Styling Guidelines

### Colors
- Primary brand: `#3b745f` (dark green)
- Secondary: `#5fa285` (light green)
- Use `COLORS` dict from `doc_figures_config.py`

### Colormaps
- Yields: `YlGn` (yellow-green)
- Water: `Blues`
- Emissions: `YlOrRd`
- Health: `RdYlGn_r` (reversed)

### Figure Sizes
Use presets from `FIGURE_SIZES`:
- `map_wide`: (12, 6) - Global maps
- `map_square`: (8, 8) - Regional focus
- `chart`: (8, 6) - Standard charts
- `chart_wide`: (10, 4) - Timeseries

### Typography
- Title: 12pt
- Labels: 10pt
- Legend: 9pt
- Annotations: 8pt
