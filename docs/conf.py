# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: CC-BY-4.0

"""Sphinx configuration for GLADE documentation."""

import os
import sys

import tomllib

# Add project root and scripts directory to path for autodoc
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../workflow/scripts"))

# Project information
project = "GLADE"
copyright = "2026, Koen van Greevenbroek"
author = "Koen van Greevenbroek"
# Single source of truth for the version lives in pixi.toml.
with open(os.path.join(os.path.dirname(__file__), "..", "pixi.toml"), "rb") as _f:
    release = tomllib.load(_f)["workspace"]["version"]

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
    # Developer READMEs (docs root and asset subdirs, e.g. _static/carbon-dial);
    # not part of the rendered site.
    "README.md",
    "**/README.md",
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

html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/github.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
        "sidebar/variant-selector.html",
    ]
}

html_context = {
    "github_repo_url": f"https://github.com/{GITHUB_REPO}",
}

# When building locally, automatically use local figures if they exist.
# This means .rst files can always contain remote URLs (the committed state)
# and local builds will transparently use local figures without manual switching.
LOCAL_FIGURES_DIR = os.path.join(os.path.dirname(__file__), "_static", "figures")

# The carbon-price dial fetches this surrogate bundle at runtime (app.js ->
# data/surrogate.json). At 9 MB and regenerated whenever the GSA is re-solved,
# it is hosted on the doc-figures release rather than tracked in git (see
# docs/_static/carbon-dial/README.md). Locally it is produced by
# export_surrogate.py; on the docs builder (e.g. ReadTheDocs) it is missing, so
# we fetch it into _static before Sphinx copies the static tree to the output.
CARBON_DIAL_DATA = os.path.join(
    os.path.dirname(__file__), "_static", "carbon-dial", "data", "surrogate.json"
)
CARBON_DIAL_SURROGATE_URL = f"{FIGURE_BASE_URL}/surrogate.json"


def _ensure_carbon_dial_surrogate():
    """Download the dial surrogate into _static if it is not present locally."""
    if os.path.exists(CARBON_DIAL_DATA):
        return
    import urllib.request

    os.makedirs(os.path.dirname(CARBON_DIAL_DATA), exist_ok=True)
    try:
        urllib.request.urlretrieve(CARBON_DIAL_SURROGATE_URL, CARBON_DIAL_DATA)
    except Exception as exc:  # a missing dial must not fail the build
        print(f"WARNING: could not fetch carbon-dial surrogate.json: {exc}")


def _use_local_figures(app, docname, source):
    """Replace remote figure URLs with local paths when local figures exist."""
    if os.path.isdir(LOCAL_FIGURES_DIR):
        source[0] = source[0].replace(FIGURE_BASE_URL + "/", "_static/figures/")


def setup(app):
    _ensure_carbon_dial_surrogate()
    app.connect("source-read", _use_local_figures)
