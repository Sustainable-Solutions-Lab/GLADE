<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
SPDX-License-Identifier: CC-BY-4.0
-->

# Carbon Price Dial (prototype)

An interactive, dependency-light web widget that lets a visitor drag a single
slider to set a global greenhouse-gas price and watch the GLADE-optimised food
system reorganise: a world map of dominant cropland use, net emissions,
the cost-vs-emissions abatement curve, and global diet composition all update
together, interpolating between solved scenarios.

It is a **static** front end (HTML + CSS + D3, no backend): GLADE is solved
offline for a sweep of carbon prices and the results are exported to a small
JSON the page interpolates over.

## Status

Prototype. The committed `data/data.json` is **synthetic placeholder data**
(real region geometry, fabricated trends) used to develop the visuals; the page
shows a "placeholder data" badge while `meta.synthetic` is true. The real
scenario sweep (`config/web_dial.yaml`, 25 GHG prices $0-500, HiGHS solver) is
generated separately and exported with `export_data.py`.

## Files

| File | Purpose |
|------|---------|
| `index.html`, `style.css`, `app.js` | The widget (D3 v7 from CDN). |
| `export_data.py` | Build `data/{data.json,regions.geojson}` from the real `results/web_dial` analysis tables. |
| `make_synthetic.py` | Generate synthetic `data/` for UI development. |
| `data/` | Generated JSON consumed by the page. |

## Run locally

```bash
cd web/carbon-dial
python -m http.server 8123
# open http://localhost:8123/  (optionally ?price=200)
```

## Regenerate data

```bash
# synthetic (for UI work):
pixi run python web/carbon-dial/make_synthetic.py

# real (after solving the sweep):
tools/smk -j2 --configfile config/web_dial.yaml --rerun-triggers mtime -- \
  results/web_dial/analysis/scen-ghg_{0,5,...,500}/net_emissions.parquet
pixi run python web/carbon-dial/export_data.py
```
