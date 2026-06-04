# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: CC-BY-4.0

"""Sphinx configuration for GLADE documentation."""

import os
import sys

# Add project root and scripts directory to path for autodoc
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../workflow/scripts"))

# Project information
project = "GLADE"
copyright = "2026, Koen van Greevenbroek"
author = "Koen van Greevenbroek"
release = "0.1.0"

# General configuration
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
    "sphinx_autodoc_typehints",
    "myst_nb",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".ipynb": "myst-nb",
}

# MyST-NB: render committed outputs as-is; never execute notebooks at build
# time. Tutorial notebooks are executed locally by the author after solving
# the corresponding scenarios, then committed with their outputs intact (see
# .gitattributes for the nbstripout exemption).
nb_execution_mode = "off"
myst_enable_extensions = ["dollarmath", "colon_fence"]

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    ".uv-cache",
    "*/.uv-cache/*",
    # Developer README for the docs directory; not part of the rendered site.
    "README.md",
]

# HTML output options
html_theme = "furo"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_logo = "_static/logo.svg"
html_favicon = "_static/logo.svg"
html_title = "GLADE"
html_theme_options = {
    "navigation_with_keys": True,
    "light_css_variables": {
        "color-brand-primary": "#3b745f",
        "color-brand-content": "#2f5e49",
    },
    "dark_css_variables": {
        "color-brand-primary": "#5fa285",
        "color-brand-content": "#7db79e",
    },
}

# Autodoc options
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Napoleon settings for NumPy docstrings
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "xarray": ("https://docs.xarray.dev/en/stable/", None),
    "pypsa": ("https://docs.pypsa.org/latest/", None),
}

# Type hints configuration
typehints_fully_qualified = False
always_document_param_types = True
typehints_document_rtype = True
# Autodoc tweaks
autodoc_typehints = "none"
autodoc_mock_imports = [
    "linopy",
    "pypsa",
    "color_utils",
]

# Figure URL configuration
# Figures are hosted on GitHub Releases to avoid tracking large assets in git
FIGURE_RELEASE_TAG = "doc-figures"
GITHUB_REPO = "Sustainable-Solutions-Lab/GLADE"
FIGURE_BASE_URL = (
    f"https://github.com/{GITHUB_REPO}/releases/download/{FIGURE_RELEASE_TAG}"
)

# When building locally, automatically use local figures if they exist.
# This means .rst files can always contain remote URLs (the committed state)
# and local builds will transparently use local figures without manual switching.
LOCAL_FIGURES_DIR = os.path.join(os.path.dirname(__file__), "_static", "figures")


def _use_local_figures(app, docname, source):
    """Replace remote figure URLs with local paths when local figures exist."""
    if os.path.isdir(LOCAL_FIGURES_DIR):
        source[0] = source[0].replace(FIGURE_BASE_URL + "/", "_static/figures/")


def setup(app):
    app.connect("source-read", _use_local_figures)
