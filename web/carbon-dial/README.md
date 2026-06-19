<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
SPDX-License-Identifier: CC-BY-4.0
-->

# Carbon Price Dial

An interactive, dependency-light web widget: drag a single slider to set a
global greenhouse-gas price and watch the GLADE-optimised food system
reorganise. Two maps (dominant cropland use and pasture / grazing land), net
emissions, the cost-vs-emissions abatement curve, the global diet, and animal
feed by source all update together, interpolating between the published
carbon-price scenarios.

It is a **static** front end (HTML + CSS + D3, no backend): GLADE is solved
offline for a sweep of carbon prices and the results are exported to a small
JSON the page interpolates over. A fixed-diet / flexible-diet toggle is built
in; only the fixed-diet sweep is wired up so far (flexible to come).

## Layout

The **published widget** (what the docs serve) lives under
`docs/_static/carbon-dial/`:

| File | Purpose |
|------|---------|
| `index.html`, `style.css`, `app.js` | The widget. |
| `lib/d3.min.js` | Bundled D3 v7 (no CDN dependency). |
| `data/{data.json,regions.geojson}` | Generated data the page fetches. |

It is embedded in the docs at `docs/carbon_price_dial.rst` (an `<iframe>` into
`_static/carbon-dial/index.html`, with the page's right-hand TOC hidden so the
dashboard gets the full width).

The **generators** live here in `web/carbon-dial/`:

| File | Purpose |
|------|---------|
| `export_data.py` | Build `data/` from the paper's solved networks (one subprocess per network; writes into `docs/_static/carbon-dial/data/`). |
| `make_synthetic.py` | Generate synthetic `data/` for UI development. |

## Data source

The real data are the paper's published **fixed-diet** carbon-price sweep from
the model-output deposition on Zenodo (DOI `10.5281/zenodo.20617942`).
`export_data.py` reads the solved networks extracted under
`.cache/zenodo/extract/GLADE-paper-data/` and computes net emissions, cost,
diet, feed (the paper's six source categories), and per-region cropland /
pasture intensities with the current `extract_*` analysis functions.

## Run locally

```bash
cd docs/_static/carbon-dial
python -m http.server 8123
# open http://localhost:8123/  (optionally ?price=200&mode=fixed)
```

## Regenerate data

```bash
# 1. Download + extract the Zenodo deposition (once):
#    curl -L -o GLADE-paper-data.tar.gz \
#      https://zenodo.org/api/records/20617942/files/GLADE-paper-data.tar.gz/content
#    tar xzf GLADE-paper-data.tar.gz -C .cache/zenodo/extract \
#      GLADE-paper-data/results/ghg_sensitivity_fixed_diet/{solved,analysis} \
#      GLADE-paper-data/processing/central/regions.geojson
# 2. Export (writes docs/_static/carbon-dial/data/):
pixi run python web/carbon-dial/export_data.py

# Synthetic data for UI work instead:
pixi run python web/carbon-dial/make_synthetic.py
```

When the flexible-diet sweep is solved, add its tree under
`results/ghg_sensitivity_flexible_diet` (or the archive equivalent) and re-run
the export; the front end's "Flexible diet" toggle activates automatically.
