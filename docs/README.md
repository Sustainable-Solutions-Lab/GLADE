<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# `food-opt` Documentation

This directory contains the Sphinx documentation for the `food-opt` global food systems optimization model.

## Building Documentation Locally

### Prerequisites

Ensure documentation dependencies are installed:

```bash
cd ..  # Return to project root
pixi install --environment dev
```

### Build HTML Documentation

Using pixi tasks (recommended):

```bash
# From project root - build docs with figures via Snakemake
pixi run --environment dev build-docs

# Build with local figures for testing
pixi run --environment dev docs-local
```

Or from this directory:

```bash
make html
```

Or directly with sphinx-build:

```bash
pixi run sphinx-build -b html . _build/html
```

The HTML documentation will be in `_build/html/`. Open `_build/html/index.html` in your web browser.

### Other Build Formats

- **PDF**: `make latexpdf` (requires LaTeX)
- **EPUB**: `make epub`
- **Clean build**: `make clean` then `make html`

## Documentation Structure

- `index.rst` - Main table of contents
- `introduction.rst` - Getting started guide
- `model_framework.rst` - Mathematical formulation and PyPSA structure
- `land_use.rst` - Spatial aggregation and resource classes
- `crop_production.rst` - Yield modeling and GAEZ data
- `livestock.rst` - Animal production systems
- `food_processing.rst` - Processing chains and trade networks
- `nutrition.rst` - Dietary constraints
- `health.rst` - Health impact assessment (DALYs)
- `environment.rst` - Emissions, water, and nitrogen
- `configuration.rst` - Configuration file reference
- `data_sources.rst` - Dataset documentation
- `workflow.rst` - Snakemake workflow execution
- `results.rst` - Analyzing and visualizing outputs
- `development.rst` - Contributing guidelines
- `api/index.rst` - Auto-generated API reference

## Editing Documentation

1. Edit `.rst` files in this directory
2. Rebuild: `make html`
3. Check for warnings in build output
4. Preview in browser: `open _build/html/index.html` (macOS) or `xdg-open _build/html/index.html` (Linux)
5. Commit changes to Git

## Documentation Figures

Documentation figures are **not tracked in git** to avoid repository bloat. Instead, they are:
1. Generated locally using Snakemake
2. Uploaded to a GitHub Release
3. Referenced in `.rst` files via URLs

### Generating Figures

Generate all documentation figures using the lightweight `doc_figures` configuration:

```bash
tools/smk -j4 --configfile config/doc_figures.yaml -- build_docs
```

This creates figures in `docs/_static/figures/` (ignored by git).

### Uploading Figures

After generating figures, upload them to the GitHub release:

```bash
tools/upload-doc-figures [RELEASE_TAG]
```

Default tag is `doc-figures`. This script:
- Creates the release if it doesn't exist
- Removes existing figure assets
- Uploads all PNG files from `docs/_static/figures/`

**Prerequisites**: Install and authenticate with GitHub CLI (`gh`):
```bash
gh auth login
```

### Figure References

Documentation `.rst` files always reference figures via GitHub release URLs:

```rst
.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/figure_name.png
```

When building locally, `conf.py` has a `source-read` hook that transparently
rewrites these to local `_static/figures/` paths if the figures have been
generated. No manual URL switching is needed.

### Workflow Summary

When updating figures (using pixi tasks):
1. Generate: `pixi run --environment dev build-docs`
2. Test locally: `pixi run --environment dev docs-local`
3. Upload: `pixi run --environment dev upload-doc-figures`
4. Commit: Only `.rst` files change, not the figures themselves

## Publishing to ReadTheDocs

Documentation builds automatically on ReadTheDocs when pushed to GitHub:

1. Configuration: `.readthedocs.yaml` in project root
2. Figures: Fetched from GitHub release during build
3. Builds trigger: Automatically on push to main branch

## Sphinx Configuration

Documentation settings are in `conf.py`:

- Theme: sphinx_rtd_theme (Read the Docs theme)
- Extensions: autodoc, napoleon, viewcode, intersphinx, mathjax
- Intersphinx links to: Python, NumPy, pandas, xarray, PyPSA docs

## License

Documentation content is licensed under CC-BY-4.0 (see SPDX headers in `.rst` files).
