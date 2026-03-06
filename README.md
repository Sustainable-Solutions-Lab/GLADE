<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

<h1>
  <img src="docs/_static/logo.svg" alt="food-opt logo" height="40" style="vertical-align: middle;"> food-opt
</h1>

[![Docs](https://github.com/Sustainable-Solutions-Lab/food-opt/actions/workflows/docs.yml/badge.svg)](https://sustainable-solutions-lab.github.io/food-opt/)

food-opt is a global food-systems optimization model built on [PyPSA](https://pypsa.org/) and [Snakemake](https://snakemake.readthedocs.io). It explores environmental, nutritional, and economic trade-offs through a configuration-driven mixed integer linear program build by reproducible workflow.

🚧 This project is currently **under construction**! 🚧

Documentation is kept up to date at a best effort basis. Certain model elements are still missing. Validation of model results has not yet been conducted. Expect frequent breaking changes.

## Documentation

Documentation (model design, configuration reference, data provenance, API) lives under `docs/`; the documentation is built automatically by a Github Action and, for now, can be accessed by clicking the documentation badge at the top of this page.

## Quickstart

### Prerequisites

1. Install [Git](https://git-scm.com/) and [pixi](https://pixi.sh/) (cross-platform package manager)
2. Ensure at least ~20 GB of free disk space for datasets, software dependencies and intermediate results.

### Installation

```bash
git clone https://github.com/Sustainable-Solutions-Lab/food-opt.git
cd food-opt
pixi install
```

### Setup (required before first run)

1. **API credentials**: Copy and configure the secrets file:
   ```bash
   cp config/secrets.yaml.example config/secrets.yaml
   # Edit config/secrets.yaml with your ECMWF Climate Data Store credentials
   # Get credentials at: https://cds.climate.copernicus.eu/user/register
   ```

2. **Manual downloads**: Three datasets require free registration and manual download:
   - IHME GBD mortality rates and relative risks (https://vizhub.healthdata.org/)
   - Global Dietary Database (https://globaldietarydatabase.org/)

   See the [Data Sources documentation](https://sustainable-solutions-lab.github.io/food-opt/data_sources.html#manual-download-checklist) for detailed instructions. Place files in `data/manually_downloaded/`.

### Run the model

```bash
tools/smk -j4 --configfile config/validation.yaml
```

The first run downloads several gigabytes of global datasets (GAEZ, GADM, land cover, etc.) and may take 30+ minutes. Once the data downloading and preprocessing steps are complete, subsequent model runs are relatively fast. Building and solving a typical model instance at default resolution will typically take only a few minutes and require about 3 GB of RAM.

### Solver options

The default environment uses the HiGHS open-source solver. For faster solving with Gurobi (requires license):

```bash
pixi install --environment gurobi
tools/smk -e gurobi -j4 --configfile config/validation.yaml
```

### Notes

- `tools/smk` wraps Snakemake with memory limits and environment configuration
- By default, results are saved under `results/{config_name}/` (path roots can be overridden via `config.paths`)
- The workflow validates configuration and data before running

## Repository Layout

- `workflow/` – Snakemake rules and scripts, including the top-level `workflow/Snakefile`.
- `config/` – Scenario YAMLs and shared fragments that parameterize the workflow.
- `docs/` – Sphinx documentation sources (see `docs/README.md` for dev tips).
- `tools/` – Helper wrappers such as `tools/smk` for consistent CLI entry points.
- `results/` – Generated artifacts grouped by configuration (never hand-edit).

Additional contribution guidance can be found in the documentation; dataset provenance is tracked in `docs/data_sources.rst`.

## License

food-opt is licensed under GPL-3.0-or-later; documentation content follows CC-BY-4.0. See `LICENSES/` for details.
