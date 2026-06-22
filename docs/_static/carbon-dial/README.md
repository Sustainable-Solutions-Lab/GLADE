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
JSON the page interpolates over. A fixed-diet / flexible-diet toggle switches
between two such sweeps -- one with consumption pinned to the 2020 baseline, one
where the diet re-optimises with the carbon price.

## Layout

Everything lives in this one directory, `docs/_static/carbon-dial/`:

| File | Purpose |
|------|---------|
| `index.html`, `style.css`, `app.js` | The widget. |
| `lib/d3.min.js` | Bundled D3 v7 (no CDN dependency). |
| `data/{data.json,regions.geojson}` | Generated data the page fetches. |
| `export_data.py` | Build `data/` from the paper's solved networks (one subprocess per network; writes into the sibling `data/`). |
| `make_synthetic.py` | Generate synthetic `data/` for UI development. |

It is embedded in the docs at `docs/carbon_price_dial.rst` (an `<iframe>` into
`_static/carbon-dial/index.html`, with the page's right-hand TOC hidden so the
dashboard gets the full width).

## Data source

The data are the paper's **fixed-diet** and **flexible-diet** carbon-price
sweeps. `export_data.py` reads each tree's solved networks and computes net
emissions, cost, diet, feed (the paper's six source categories), and per-region
cropland / pasture intensities with the current `extract_*` analysis functions.

It reads from the model-output deposition on Zenodo (DOI
`10.5281/zenodo.20617942`) when it is extracted under
`.cache/zenodo/extract/GLADE-paper-data/`, and otherwise falls back to the local
`results/` tree. The flexible-diet sweep's solved networks are *not* shipped in
the Zenodo deposition (only its `net_emissions` parquets are), so its widget
data comes from solving `config/ghg_sensitivity_flexible_diet.yaml` locally.

## Run locally

```bash
cd docs/_static/carbon-dial
python -m http.server 8123
# open http://localhost:8123/  (optionally ?price=200&mode=fixed)
```

## Regenerate data

```bash
# Fixed-diet sweep: download + extract the Zenodo deposition (once), OR solve
# config/ghg_sensitivity_fixed_diet.yaml locally:
#    curl -L -o GLADE-paper-data.tar.gz \
#      https://zenodo.org/api/records/20617942/files/GLADE-paper-data.tar.gz/content
#    tar xzf GLADE-paper-data.tar.gz -C .cache/zenodo/extract \
#      GLADE-paper-data/results/ghg_sensitivity_fixed_diet/{solved,analysis} \
#      GLADE-paper-data/processing/central/regions.geojson
#
# Flexible-diet sweep: not in the deposition, solve it locally:
#    tools/smk -e gurobi -j4 --configfile config/ghg_sensitivity_flexible_diet.yaml \
#      -- solve_all_scenarios analyze_all_scenarios
#
# Export (prefers the extracted archive, else the local results/ tree; writes
# the sibling data/ directory):
pixi run python docs/_static/carbon-dial/export_data.py

# Synthetic data for UI work instead:
pixi run python docs/_static/carbon-dial/make_synthetic.py
```

The front end's diet-mode toggle activates each mode present in `data.json`; a
mode whose tree is missing at export time is disabled automatically.
