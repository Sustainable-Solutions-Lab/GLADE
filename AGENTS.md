<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# AGENTS.md

Guidance for AI coding agents contributing to this repository.

## Purpose

Provide clear expectations and a safe, efficient workflow so agents can make small, correct, and reversible changes that fit the project’s conventions.

## Project Overview (brief)

- Global food systems optimization using linear programming.
- Built on PyPSA for modeling and Snakemake for workflow orchestration.
- Configuration-driven; results materialized under `results/{config_name}/`.

## Filesystem Layout

- `config/`: Scenario configuration files and shared YAML fragments; edits here drive what Snakemake targets construct and solve.
- `data/`: Source datasets and mock CSVs used for testing; treat contents as inputs only and keep large/raw data out of Git.
- `docs/`: Sphinx documentation (17 sections covering all model aspects); see `docs/README.md` for build instructions.
- `workflow/`: Snakemake project root with the main `workflow/Snakefile`, modular rules, and workflow scripts under `workflow/scripts/`.
- `tools/`: Utility wrappers (e.g., `tools/smk`) that pin resource limits and interpreter settings for repeatable runs.
- `processing/`: Intermediate datasets that feed the modeled workflow.
- `notebooks/`: Exploratory analyses and sanity-check visualisations.
- `results/`: Auto-generated artifacts organized as `results/{config_name}/`; never hand-edit. Rerun the relevant target instead.
- `vendor/`: Custom branches of PyPSA and linopy for reference, though not used as local dependencies.

## Model Structure

The model represents global food systems as a PyPSA network where commodities flow through a supply chain from land/resources to human nutrition. The `build_model` rule (in `workflow/rules/model.smk`) orchestrates construction via `workflow/scripts/build_model.py`, which calls functions from the modular `workflow/scripts/build_model/` package.

### Supply Chain Overview

Key commodity flows:
- **Land → Crops**: Production links consume land (Mha) and produce crops (Mt) with yields as efficiency
- **Crops → Foods**: Processing pathways convert crops to foods with mass-balance factors
- **Foods → Nutrition**: Consumption links route foods to nutrient stores and food-group stores
- **Crops/Foods → Feed**: Conversion links supply animal feed categories
- **Feed → Animal products**: Animal production with emissions and manure outputs
- **Trade**: Hub-based networks enable commodity movement between countries

The model also tracks supporting resource flows: regional irrigation **water** availability and consumption, synthetic **fertilizer N** supply with manure recycling, crop **residues** routed to feed or soil, and **biomass** export to the energy sector.

### Emissions Tracking

GHG emissions flow to global buses (`emission:co2`, `emission:ch4`, `emission:n2o`) that aggregate to `emission:ghg` using configurable GWP factors:

| Source | Gas | Mechanism |
|--------|-----|-----------|
| Land-use change | CO₂ | Efficiency on `land_conversion` links (LUC carbon coefficients) |
| Spared land | CO₂ | Sequestration credits on `spare_land` links (negative emissions) |
| Rice cultivation | CH₄ | Efficiency on wetland rice production links (IPCC emission factors) |
| Enteric fermentation | CH₄ | Efficiency on `animal_production` links (from feed digestibility) |
| Manure management | CH₄ | Efficiency on `animal_production` links (country-specific factors) |
| Synthetic fertilizer | N₂O | Efficiency on `fertilizer_distribution` links (direct + indirect) |
| Manure application | N₂O | Efficiency on `animal_production` links (pasture + applied fractions) |
| Residue incorporation | N₂O | Efficiency on `residue_incorporation` links (from residue N content) |

The `emission:ghg` store accumulates total CO₂-equivalent emissions for use in optimization objectives and constraints.

### Module Organization

| Module | Purpose |
|--------|---------|
| `infrastructure.py` | Carriers, buses for crops/foods/feeds/nutrients per country |
| `land.py` | Land buses, existing/new land generators, land-use-change emissions |
| `primary_resources.py` | Water, fertilizer supply, emission aggregation buses |
| `crops.py` | Crop production links, multi-cropping, spared land, residue incorporation |
| `grassland.py` | Grassland/pasture feed production |
| `food.py` | Crop→food conversion pathways, feed supply links |
| `animals.py` | Feed→animal product conversion with CH₄/N₂O emissions |
| `nutrition.py` | Food→nutrient/group links, stores for nutritional tracking |
| `trade.py` | K-means hub networks for crop/food/feed trade |
| `health.py` | Health impact stores by disease cluster |
| `biomass.py` | Biomass export routes for crops and byproducts |

### PyPSA Component Patterns

**Multi-bus links** (the workhorse of this model):
- `bus0`: Primary input (e.g., land in Mha)
- `bus1`: Primary output; `efficiency` = output/input ratio
- `bus2`, `bus3`, …: Additional inputs/outputs with `efficiency2`, `efficiency3`, …
  - Positive efficiency → output; negative → input (relative to `bus0`)

**Adding components**: PyPSA auto-expands scalar arguments, so use `marginal_cost=1` instead of `[1] * len(...)`.

### Naming Conventions

Names use `:` as delimiter. Pattern: `{type}:{specifier}:{scope}`

| Component | Pattern | Examples |
|-----------|---------|----------|
| **Buses** | | |
| Crops/foods | `{type}:{item}:{country}` | `crop:wheat:USA`, `food:bread:USA` |
| Feed | `feed:{category}:{country}` | `feed:ruminant_grain:USA` |
| Nutrients | `nutrient:{nutrient}:{country}` | `nutrient:protein:USA` |
| Land (cropland) | `land:cropland:{region}_c{class}_{water}` | `land:cropland:usa_east_c1_r` |
| Land (pasture) | `land:pasture:{region}_c{class}` | `land:pasture:usa_east_c1` |
| Water | `water:{region}` | `water:usa_east` |
| Emissions | `emission:{type}` | `emission:co2`, `emission:ghg` |
| **Links** | | |
| Production | `produce:{crop}_{water}:{region}_c{class}` | `produce:wheat_rainfed:usa_east_c1` |
| Processing | `pathway:{pathway}:{country}` | `pathway:milling:USA` |
| Consumption | `consume:{food}:{country}` | `consume:bread:USA` |
| Animal | `animal:{product}_{feed}:{country}` | `animal:beef_grassfed:USA` |
| Trade | `trade:{item}:{from}_to_{to}` | `trade:wheat:USA_to_hub0` |
| **Stores** | `store:{type}:{item}:{scope}` | `store:group:cereals:USA` |
| **Generators** | `supply:{type}:{scope}` | `supply:land_existing:usa_east_c1_r` |

### Carrier and Metadata Columns

**IMPORTANT**: Never parse component names. Always use columns for filtering.

The `carrier` column identifies link/component type:
- `crop_production`, `crop_production_multi`, `grassland_production`
- `food_processing`, `food_consumption`, `feed_conversion`
- `animal_production`, `trade_crop`, `trade_food`, `trade_feed`
- `land_use`, `land_conversion`, `spare_land`

Domain-specific columns for filtering:
- `country`, `region`: Geographic scope
- `crop`, `food`, `food_group`: Commodity type
- `product`, `feed_category`: Animal production
- `resource_class` (int), `water_supply` ("irrigated"/"rainfed"): Land characteristics

### Accessing Components

Always filter by carrier and metadata columns, never by parsing names:

```python
# Get crop production links for wheat in a country
wheat_links = n.links.static[
    (n.links.static["carrier"] == "crop_production") &
    (n.links.static["crop"] == "wheat") &
    (n.links.static["country"] == "USA")
]

# Get all food consumption links
consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]

# Get stores for a food group
group_stores = n.stores.static[n.stores.static["carrier"] == f"group_{group}"]
```

Fail fast when components are missing:
```python
if group_stores.empty:
    raise ValueError(f"No stores found for food group '{group}'")
```

### Units

| Quantity | Unit | Notes |
|----------|------|-------|
| Land area | Mha | Megahectares (10⁶ ha) |
| Commodities | Mt | Megatonnes (fresh weight for foods, dry matter for crops) |
| Water | Mm³ | Million cubic meters |
| Fertilizer N | Mt N | Megatonnes nitrogen |
| Emissions | t CO₂/CH₄/N₂O | Tonnes (aggregated to GHG via GWP factors) |
| Costs | bn USD | Billion USD (marginal_cost per unit of bus0 flow) |

See `workflow/scripts/build_model/__init__.py` for the complete reference.

## Core Principles

- This project is currently unstable and under rapid development; never implement any kind of backward compatibility.
- Keep code concise: Prefer simple control flow; fail early on invalid external inputs.
- Do your best to avoid over-engineering. If you see possibilities for simplifying, suggest improvements (but let the user approve of such drive-by refactors first).
- Consistent style: Follow existing patterns in nearby files; don’t introduce new paradigms ad hoc.
- Reproducibility: Use the Snakemake targets below to validate changes; don’t hand‑run ad hoc pipelines unless necessary.
- No unused imports: The linter removes them automatically; only add imports when adding code that uses them.
- Do not add `from __future__ import annotations`; type checkers and tooling already expect
  runtime string annotations, so this import is unnecessary and should be avoided.
- Documentation-first interfaces: If you change a script’s inputs/outputs, update inline docstrings and any referenced docs/config keys.
- Never use `config.get(<attr>, <default>)` or similar with a hardcoded default value in any script; we always assume that the configuration is well-formed and complete, so we can just index directly (`config[<attr>]`).

## Environment & Tooling

- Dependency manager: `pixi` (see `pixi.toml`).
- Lint/format: `ruff` for Python, `snakefmt` for Snakemake files (auto-enforced via hooks; no manual action usually needed).
- Workflow engine: `snakemake` (run via `tools/smk` wrapper by default).

### Available Environments

- `default`: Base environment with HiGHS solver (open-source)
- `dev`: Development tools (Jupyter, Sphinx, prek, etc.)
- `gurobi`: Includes Gurobi solver (requires license)
- `dev-gurobi`: Development tools + Gurobi solver

Recommended commands (use the memory-capped wrapper):

```bash
# Install and sync dependencies (default environment)
pixi install

# Install with Gurobi solver support
pixi install --environment gurobi

# Install development environment
pixi install --environment dev

# Run the full workflow (data prep → build → solve)
tools/smk -j4 --configfile config/<name>.yaml

# Run with specific environment
tools/smk -e gurobi -j4 --configfile config/<name>.yaml

# Build model only (for scenario "default")
tools/smk -j4 --configfile config/<name>.yaml -- results/{config_name}/build/model_scen-default.nc

# Solve model only (after build)
tools/smk -j4 --configfile config/<name>.yaml -- results/{config_name}/solved/model_scen-default.nc

# Build the docs, including figures; this needs the 'dev' environment
tools/smk -e dev -j4 --configfile config/doc_figures.yaml -- build_docs

# Test small snippets of code
pixi run python <...>
```

Notes:

- Remember the double dash (--) before any target file, to separate flags from the target file.
- **Scenario wildcard**: All model and plot targets include a `{scenario}` wildcard (e.g., `model_scen-default.nc`). Scenarios are defined in `config/scenarios.yaml` and apply configuration overrides. Available scenarios: default, H, G, HG.
- Snakemake tracks code changes and will rerun affected rules; manual cleanup of workflow artefacts is unnecessary. You almost never have to use the `--forcerun` argument.
- Prefer small, testable edits and validate by running the narrowest target that exercises your change.
- `tools/smk` runs Snakemake in a systemd cgroup with a hard 10G cap and swap disabled by default; override with `SMK_MEM_MAX=12G tools/smk ...`. It also implements the `-e <environment>` flag to select the pixi environment.
- Retrieval / downloading rules and scripts make network calls; when running such rules you will need to ask for permission to run outside the sandbox in order to get network access.
- Never rerun retrieval rules without explicitly being instructed to do so. This includes implicit calls like an indiscriminate use of the `--forceall` Snakemake argument.

## Repository Conventions

- Scripts used by the workflow live in `workflow/scripts/`.
- Configuration lives under `config/` (e.g., `config.yaml`).
- Input data under `data/`; outputs under `results/` (structured by config name).
- Don’t commit large data or generated results; `.gitignore` and the workflow manage these.
- If you are working on incorporating a new dataset, check that the dataset is listed in data/DATASETS.md.

### Git guidelines

- AI Agents (Claude, Codex, etc) should not add themselves as co-authors to commits unless explicitly asked for.

## Configuration Validation

The project uses automatic configuration validation via JSON Schema to ensure all config files are complete and well-formed.

### How It Works

- **Schema location**: `config/schemas/config.schema.yaml` (JSON Schema in YAML format)
- **Automatic validation**: Runs at the start of every Snakemake workflow execution via `workflow/validation/config_schema.py`
- **Based on**: `config/default.yaml` structure; all fields in default are generally required
- **User configs**: Only need to specify overrides; they're merged with default before validation
- **Scientific notation**: PyYAML 6.0+ parses scientific notation like `1e-2` as strings. Use decimal notation (`0.01`) instead.

## Secrets Management

API credentials for external data sources (USDA, ECMWF) are managed separately from the main configuration to avoid committing secrets to version control.

### Setup Options

**Option 1: Secrets File (Recommended for local development)**

1. Copy the template:
   ```bash
   cp config/secrets.yaml.example config/secrets.yaml
   ```

2. Edit `config/secrets.yaml` and fill in your API credentials:
   - **USDA API key**: Get from https://fdc.nal.usda.gov/api-guide.html
   - **ECMWF credentials**: Get from https://cds.climate.copernicus.eu/api-how-to
     - Register at https://cds.climate.copernicus.eu/user/register
     - Accept dataset licenses at https://cds.climate.copernicus.eu/datasets/satellite-land-cover
     - Get your UID and API key from your profile page

3. The file is excluded from git - never commit real credentials!

**Option 2: Environment Variables (Recommended for CI/CD)**

Set these environment variables before running the workflow:

```bash
export USDA_API_KEY="your-usda-api-key"
export ECMWF_DATASTORES_URL="https://cds.climate.copernicus.eu/api"
export ECMWF_DATASTORES_KEY="your-ecmwf-key"
```

### Precedence

Environment variables take precedence over the secrets file. This allows you to override file-based credentials in CI/CD or testing environments.

### Validation

The workflow validates that all required credentials are present at startup (before any rules execute). If credentials are missing, you'll see a clear error message with instructions on how to configure them.

## When Implementing Changes

- Keep function/module scope tight; avoid broad rewrites.
- Mirror existing error handling: validate external data; trust internal invariants.
- Add or adjust docstrings where behavior or parameters change.
- If you add a new rule or script, integrate it into the `workflow/Snakefile` and ensure targets are reproducible.
- Don’t introduce network calls or external services in core code unless explicitly required by the task.

## Documentation

- Comprehensive Sphinx documentation lives in `docs/` with 17 major sections covering:
  - Model framework, components, and mathematical formulation
  - Data sources, workflow execution, and configuration
  - All model aspects: land use, crops, livestock, nutrition, health, environment
  - Contributing guidelines, API reference
- When adding features or changing behavior, update relevant documentation sections in `docs/*.rst`.
- Build docs locally: `cd docs && make html` (requires `pixi install --environment dev`).
- Documentation is version-controlled and builds automatically on ReadTheDocs.

### Documentation Figures

**Important**: Documentation figures are **NOT tracked in git**. They are:
- Generated locally via Snakemake using the lightweight `config/doc_figures.yaml` configuration
- Uploaded to a GitHub Release (tag: `doc-figures`)
- Referenced in `.rst` files via GitHub release URLs
- Located in `docs/_static/figures/*` (ignored by `.gitignore`)

When updating documentation figures:

```bash
# 1. Generate figures
tools/smk -j4 --configfile config/doc_figures.yaml -- build_docs

# 2. Upload to GitHub release (requires gh CLI authentication)
tools/upload-doc-figures

# 3. Update .rst references to use remote URLs
tools/update-figure-refs --to-remote

# 4. Commit only the .rst changes (not the figures)
git add docs/*.rst
git commit -m "Update documentation figures"
```

For local testing, you can use local figure paths:
```bash
tools/update-figure-refs --to-local
make html  # Build docs with local figures
tools/update-figure-refs --to-remote  # Switch back before committing
```

**Never** commit figure files (`*.png`, `*.svg`) to git - they are hosted externally to keep the repository lean.

## Validation Checklist

- Narrow target runs clean via Snakemake for at least one `config_name`.
- No new linter errors; no unused imports.
- Results land under the expected `results/{config_name}/...` path(s).
- Documentation updated when changing user-visible behavior (check `docs/*.rst` for relevant sections).

## Safety & Licensing

- Respect SPDX headers; keep or add them to new files following repository practice.
- Do not introduce secrets, credentials, or hard-coded local paths.
- Use only licensed datasets and dependencies already declared in `pixi.toml` unless explicitly instructed to add new ones.

## Reminder

Againt, always remember to use pixi to run snippets of python; do not run python directly or you won't be able to use any project dependencies.
