<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
SPDX-License-Identifier: CC-BY-4.0
-->

# Carbon Price Dial

An interactive, dependency-light web widget: drag a single slider to set a
global greenhouse-gas price and watch the GLADE-optimised food system
reorganise. Two maps (dominant cropland use and pasture / grazing land), net
emissions, the cost-vs-emissions abatement curve, the global diet, and animal
feed by source all update together.

It is a **static** front end (HTML + CSS + D3, no backend) that evaluates a
GLADE **MLP surrogate directly in the browser** -- no precomputed scenario
grid, no interpolation. A tiny JS forward pass runs the network at the current
slider values; the maps are reconstructed from PCA-compressed spatial fields.
A fixed-diet / flexible-diet toggle switches between two surrogates -- one with
consumption pinned to the 2020 baseline, one where the diet re-optimises with
the carbon price.

## Layout

Everything lives in this one directory, `docs/_static/carbon-dial/`:

| File | Purpose |
|------|---------|
| `index.html`, `style.css`, `app.js` | The widget and its in-browser surrogate forward pass. |
| `lib/d3.min.js` | Bundled D3 v7 (no CDN dependency). |
| `data/regions.geojson` | Region polygons for the maps (tracked in git). |
| `data/surrogate.json` | Serialized surrogate bundles the page evaluates (see Hosting). |
| `export_surrogate.py` | Builds `data/surrogate.json` from the fitted MLP bundles. |

It is embedded in the docs at `docs/carbon_price_dial.rst` (an `<iframe>` into
`_static/carbon-dial/index.html`).

## How the surrogate is built

The dial reads the GLADE GSA: a Sobol design of production-side uncertainties
plus a `ghg_price` slice parameter, solved and analysed once, then summarised
by a single multi-output MLP surrogate that the browser evaluates live.

The two diet modes come from two base GSA configs:

| Mode | Base config | Surrogate bundle |
|------|-------------|------------------|
| flexible | `config/gsa.yaml` | `results/gsa/surrogates/surrogate_gsa_mlp.pkl` |
| fixed | `config/gsa_fixed_diet.yaml` | `results/gsa_fixed_diet/surrogates/surrogate_gsa-fd_mlp.pkl` |

Both are layered with the dial overlay `docs/config/carbon_dial_surrogate.yaml`,
which adds the extra surrogate **outputs** the dial needs (spatial cropland /
grazing fields, per-food energy, cost decomposition) and the dial-specific MLP
**tuning** (wide net, seed ensemble, scalar-vs-field loss weighting). The base
configs keep neutral single-net MLP defaults, so a plain `surrogate_*_mlp.pkl`
build still works without the overlay.

```bash
# 1. Solve + analyse the GSA designs (large; normally run on the cluster --
#    see docs/cluster_execution.rst and the gsa-cluster-rerun workflow). This
#    produces results/{gsa,gsa_fixed_diet}/analysis/<scenario>/ outputs.

# 2. Fit the dial MLP surrogates (reads the existing analysis; no re-solve).
#    Pass BOTH config files to ONE --configfile flag so the overlay deep-merges
#    onto the base (a second --configfile flag would replace, dropping the base):
tools/smk -e gurobi -j4 \
  --configfile config/gsa.yaml docs/config/carbon_dial_surrogate.yaml \
  --allowed-rules build_surrogate -- \
  results/gsa/surrogates/surrogate_gsa_mlp.pkl

tools/smk -e gurobi -j4 \
  --configfile config/gsa_fixed_diet.yaml docs/config/carbon_dial_surrogate.yaml \
  --allowed-rules build_surrogate -- \
  results/gsa_fixed_diet/surrogates/surrogate_gsa-fd_mlp.pkl

# 3. Serialize both bundles to the browser JSON. The script reimplements the JS
#    forward pass in numpy and checks it against each bundle's own predict() /
#    predict_field() before writing, so the browser math is verified.
pixi run -e dev python docs/_static/carbon-dial/export_surrogate.py
```

For tuning the MLP hyperparameters against an already-solved design (the sweep
behind the overlay's net width / ensemble size / loss weight), see
`tools/tune-mlp-surrogate`.

## Hosting `surrogate.json`

`data/surrogate.json` is ~9 MB and is regenerated whenever the GSA is
re-solved, so it is **not tracked in git** (it would bloat history on every
update). Instead it is hosted on the `doc-figures` GitHub release, exactly like
the documentation figures:

- `docs/conf.py` downloads it into `_static/carbon-dial/data/` at build time if
  it is not already present locally, so ReadTheDocs serves it as a static asset.
- Locally, `export_surrogate.py` writes it directly, so a local build (or
  `python -m http.server`) uses the freshly generated file with no download.

After regenerating, publish it:

```bash
tools/upload-carbon-dial-surrogate
```

(In-git compression is not worth it: the bundle is base64-encoded float32, so
gzip only recovers ~30%. The dominant cost is the ensemble MLP weights; the
server CDN already gzips transport on the fly.)

## Run locally

```bash
cd docs/_static/carbon-dial
python -m http.server 8123
# open http://localhost:8123/  (optionally ?price=200&mode=fixed)
```

The front end's diet-mode toggle activates each mode present in
`surrogate.json`; a mode whose bundle was missing at export time is disabled
automatically.
