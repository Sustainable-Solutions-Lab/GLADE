<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# food-opt -- paper reproduction snapshot

This branch (`orthogonal-opportunities`) is a frozen snapshot of
[food-opt](https://github.com/Sustainable-Solutions-Lab/food-opt) used to
produce the model results behind the paper on the limited synergies between
dietary-health and greenhouse-gas-emission goals in the global food system.

It is archived for reproducibility and citation (Zenodo DOI: _to be minted_).
**For the maintained, general-purpose model use the main project instead** --
this branch is not updated. Documentation:
<https://sustainable-solutions-lab.github.io/food-opt/>.

The figure-building, plotting, and manuscript code lives in the companion
**paper repository** (_link / DOI to be added_). This snapshot only produces
the food-opt model outputs that those figure scripts read; the per-figure
input map is in that repo's `scripts/README.md`.

## Reproducing the paper's model outputs

1. Install and configure food-opt as described in "Installation & setup"
   below (pixi environment, API credentials, manual dataset downloads).
2. Build the targets for each part of the paper (commands run from this
   checkout via the `tools/smk` wrapper):

```bash
# Central scenario: Figure 1, the ED reforestation map, the reference->central
# transition tables, and most Methods macros.
tools/smk -j4 --configfile config/central.yaml

# GHG-price fixed-diet sweep: Figure 3 land-use / feed / FCR panels.
tools/smk -j4 --configfile config/ghg_sensitivity_fixed_diet.yaml

# Spatial-resolution sensitivity (SI).
for R in 250 500 750 1000 1500 2000; do
  tools/smk -j4 --configfile config/region_resolution/R${R}.yaml
done

# Validation run: slack overview macros cited in Methods.
tools/smk -j4 --configfile config/validation.yaml
```

The **global sensitivity analysis** (Figures 2-4, ED burden panels, SI
combined-sensitivity) sweeps thousands of Sobol scenarios across three
production-stability regimes and is solved on an HPC cluster, not via
Snakemake locally -- see `docs/cluster_execution.rst`. The figure scripts
consume only the fitted XGBoost surrogate bundles:

```bash
# After the cluster solve, fit the surrogates (locally is fine):
tools/smk -j4 --configfile config/gsa.yaml -- \
  results/gsa/surrogates/surrogate_gsa_xgb.pkl \
  results/gsa/surrogates/surrogate_gsa-l1-low_xgb.pkl \
  results/gsa/surrogates/surrogate_gsa-l1-high_xgb.pkl
tools/smk -j4 --configfile config/gsa_fixed_diet.yaml -- \
  results/gsa_fixed_diet/surrogates/surrogate_gsa-fd_xgb.pkl
```

Calibration artefacts under `data/curated/calibration/` are git-tracked, so a
normal run uses them as-is. To regenerate them (and the
`prod_stability_trace.csv` cited in Methods) see `docs/calibration.rst` and
`tools/calibrate`.

Once the targets above exist, build the figures and PDFs from the paper
repository (see its README).

### Configs used by the paper

| Paper element | Config | Key food-opt targets |
|---|---|---|
| Fig. 1, ED map, transition tables, Methods numbers | `config/central.yaml` | `results/central/{solved,analysis}/scen-{reference,central}/...` |
| Figs. 2-4, ED burden, SI combined sensitivity | `config/gsa.yaml` | `results/gsa/surrogates/surrogate_{gsa,gsa-l1-low,gsa-l1-high}_xgb.pkl` (+ `surrogate_validation_gsa_xgb.parquet`) |
| Fig. 3 (fixed-diet abatement, FCR) | `config/gsa_fixed_diet.yaml`, `config/ghg_sensitivity_fixed_diet.yaml` | `surrogate_gsa-fd_xgb.pkl`; `results/ghg_sensitivity_fixed_diet/{solved/model_scen-ghg_{5,50,500}.nc,analysis/scen-ghg_*/feed_by_source.parquet}` |
| SI spatial-resolution sensitivity | `config/region_resolution/R{250..2000}.yaml` | `results/region_resolution_R*/analysis/scen-central/{net_emissions,land_use,health_totals}.parquet` |
| Methods slack macros | `config/validation.yaml` | `results/validation/plots/scen-default/{slack_overview,food_group_slack}.csv` |

(Configs in this repository other than those listed are not used by the
paper; they remain for snapshot fidelity.)

## Installation & setup

### Prerequisites

1. [Git](https://git-scm.com/) and [pixi](https://pixi.sh/).
2. ~20 GB free disk space for datasets, dependencies, and intermediate results
   (substantially more for the full GSA scenario set).

### Installation

```bash
git clone --branch orthogonal-opportunities \
  https://github.com/koen-vg/food-opt.git
cd food-opt
pixi install
```

### Required before the first run

- **API credentials**: `cp config/secrets.yaml.example config/secrets.yaml`
  and fill in your ECMWF Climate Data Store credentials
  (<https://cds.climate.copernicus.eu/user/register>).
- **Manual downloads**: the IHME GBD mortality rates / relative risks
  (<https://vizhub.healthdata.org/>) and the Global Dietary Database
  (<https://globaldietarydatabase.org/>) require free registration. Place the
  files under `data/manually_downloaded/`; see the
  [Data Sources documentation](https://sustainable-solutions-lab.github.io/food-opt/data_sources.html#manual-download-checklist).

The first run downloads several gigabytes of global datasets (GAEZ, GADM, land
cover) and may take 30+ minutes; subsequent runs are fast.

### Solver

The default pixi environment uses the open-source HiGHS solver. For Gurobi
(faster, requires a license):

```bash
pixi install --environment gurobi
tools/smk -e gurobi -j4 --configfile config/central.yaml
```

## License

food-opt is licensed under GPL-3.0-or-later; documentation and data outputs
follow CC-BY-4.0 (with third-party datasets under their own terms). See
`LICENSES/` and `REUSE.toml`.
