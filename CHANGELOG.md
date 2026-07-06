<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# Changelog

All notable changes to GLADE are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the model remains under active development (pre-1.0), minor releases may
introduce breaking changes to configuration and outputs.

## [Unreleased]

### Added

- New `health.segment_formulation: relax_and_fix` option (now the default):
  a two-pass LP scheme for the health module's non-convex dose-response
  curves (solve the relaxation, pin each non-convex curve to the segment of
  its relaxed intake, re-solve warm-started) with a certified optimality gap
  checked against `health.relax_and_fix_max_gap`. If the certificate fails,
  the solve re-fixes the segments from the repaired solution and, as a last
  resort, automatically falls back to the exact sos1 MIP seeded with the
  repaired solution, instead of erroring. Health-enabled solves no
  longer contain integer variables: full-resolution scenarios solve with the
  open-source HiGHS solver in about 5 minutes (`solving.options_highs:
  {solver: ipm, run_crossover: on}`, no Gurobi license required) and about
  30% faster than before under Gurobi, with results that agree across
  solvers and certify tighter than the previous 0.1% MIP gap. The exact MIP
  indicators remain available via `health.segment_formulation: sos1`.
- Interactive **Carbon Price Dial**: a web widget embedded in the
  documentation where GHG-price and value-per-life-year sliders drive live
  land-use maps, net-emissions, system-cost and diet readouts by evaluating
  the MLP surrogate directly in the browser. Includes constant- and
  flexible-diet modes, a grams/kcal diet toggle, per-region hover breakdowns
  of cropland and grazing land, and an advanced panel exposing the remaining
  surrogate inputs.
- New `mlp` surrogate method (now the default) with optional seed-ensemble
  averaging and per-target loss weighting, plus PCA-compressed spatial-field
  surrogate outputs that reconstruct per-region land-use maps. Surrogate
  modelling is documented on a dedicated docs page
  (`docs/surrogate_modelling.rst`).
- Calibration artefacts are now organized in per-config **sets** under
  `data/curated/calibration/<source>/`, selected via the new
  `calibration.source` config key. Each set carries a `provenance.yaml` stamp
  of the structural config it was calibrated against; workflow runs error on
  structural mismatch (downgradable via
  `calibration.accept_provenance_mismatch`). `tools/calibrate --base <config>`
  calibrates a dedicated set for a structurally divergent config.
- New `diet.anchor_groups_to_gbd` option that decouples GBD anchoring of the
  baseline diet's risk-factor food groups from the health module. Defaults to
  the sentinel `match_health` (follow `health.enabled`); set `true`/`false` to
  control it independently. Previously anchoring was unconditional. See
  `docs/current_diets.rst` for a quantitative description of the difference and
  the refined-grain caveat. The baseline diet feeds calibration, so a
  `gbd-anchored` artefact set (the previous GBD-anchored artefacts) is
  committed alongside `default` and consumed by the health-enabled configs via
  `calibration.source: gbd-anchored`. Provenance stamps record the *resolved*
  anchoring, and `tools/calibrate` pins the base config's resolved anchoring
  across all five calibration steps.

### Changed

- Tightened the default solve memory allocation (`solving.mem_mb`) to match
  the reduced memory usage of full-resolution solves.
- Reformulated the **L1 deviation penalties** (production, animal-feed, diet
  stability) from an absolute-value auxiliary variable with two inequality
  rows per link to an equivalent equality split into non-negative
  positive/negative deviation parts, and priced the zero-baseline
  land-conversion penalty directly on link flows. Together with a faster
  nodal-balance construction in the vendored PyPSA fork, this cuts
  full-resolution solve times by roughly a third (about 40% fewer
  constraint rows after presolve) with identical optima up to solver
  tolerance.
- Improved the optimisation model's **numerical conditioning** to remove
  Gurobi's "large matrix coefficient range" warning. The CH₄ and N₂O emission
  buses are now denominated in kilotonnes (previously tonnes) so their flow
  coefficients sit within a few orders of the CO₂ bus, and a new `numerics`
  config block clips physically-negligible coefficients at build time
  (sub-hectare areas, trace irrigation/carbon fluxes, rounding-level cost
  corrections). The former `land.filtering` thresholds now live under
  `numerics`. Emission totals and the objective are unchanged (to within
  solver tolerance); only reported CH₄/N₂O bus flows change units.
- The baseline diet is now derived from **FAOSTAT Food Balance Sheets** by
  default (`diet.source: fbs`), computed from per-country food supply energy
  at model-basis densities and corrected for consumer waste. The GDD-IA
  pipeline (not yet publicly available) remains available via
  `diet.source: gdd_ia`, with its input CSVs required only in that mode. The
  `default` calibration artefact set is refit against the FBS diet; the
  previous GDD-fit set is preserved as `gdd-ia`.
- Whole-grain definitions are aligned across diet sources: a new `maize-whole`
  food carries GBD's whole-grain exposure in maize-staple regions,
  `diet.fbs.whole_grain_shares` is refit against GBD per-country whole-grain
  exposure, and GDD-IA cereal energy is re-split by each country's FBS cereal
  composition (fixing starved whole-grain intake for Sahel coarse-grain
  staples). All three calibration artefact sets are refreshed accordingly.
- The health module is now **disabled by default** (`health.enabled: false`).
  With health off, the workflow no longer requires the manually-downloaded
  IHME GBD data and runs end to end without it; a clear startup error is
  raised if health (or GBD anchoring) is enabled but the data is absent.
- A default build now requires **no credentials**: land-cover data is fetched
  from a CC-BY-4.0 Zenodo mirror instead of the Copernicus Climate Data Store
  (dropping the CDS API key), and the USDA FoodData Central key is only needed
  when refreshing nutrition data (`data.usda.retrieve_nutrition: true`, off by
  default; the bundled `data/curated/nutrition.csv` is used otherwise).
- Upgraded the vendored solver stack: linopy to `v0.8.0+glade2` (CSR-based
  matrix construction, frozen constraint storage) and PyPSA to
  `v1.2.0+glade2` (vectorized dual assignment). Together these cut solver
  matrix assembly by ~60x and dual recovery from ~470 s to ~2 s on
  full-resolution solves.

### Removed

- The MARS surrogate method; supported surrogates are now `pce`, `rf`, `xgb`
  and `mlp`.

## [0.1.0] - 2026-06-15

First public release of GLADE (Global Land, Agriculture, Diet and Emissions),
a global food-systems optimization model built on PyPSA and Snakemake.

### Added

- Configuration-driven mixed-integer linear program covering the food supply
  chain from land and primary resources through crops, processing, livestock,
  trade, and human nutrition.
- Sub-national optimization regions created by clustering administrative units,
  connected through hub-based trade networks for crops, foods, and feeds.
- Spatially explicit crop production for 60+ crops with GAEZ-derived yield
  potentials, multi-cropping, irrigation, and rainfed/irrigated land classes.
- Livestock systems with grazing and feed-based pathways, including enteric
  fermentation, manure management, and manure-application emissions.
- Greenhouse-gas accounting (CO2, CH4, N2O aggregated to CO2-equivalent) for
  land-use change, spared-land sequestration, rice cultivation, fertilizer use,
  and residue incorporation, with configurable GWP factors.
- Nutritional and food-group constraints ensuring caloric and dietary adequacy
  per country, plus health-impact tracking by disease cluster.
- Reproducible Snakemake workflow with data retrieval, model build, scenario
  solve, analysis, and plotting targets, organized under `results/{config}/`.
- Five-stage calibration pipeline (feed, food waste, food demand, cost,
  production stability) with git-tracked artefacts and a `tools/calibrate`
  entrypoint.
- Manifest-based HPC cluster execution path for large scenario sweeps (e.g.
  global sensitivity analysis) without Snakemake DAG overhead.
- Automatic JSON-schema validation of configuration files.
- Comprehensive Sphinx documentation and tutorial notebooks, published to
  GitHub Pages.

[Unreleased]: https://github.com/Sustainable-Solutions-Lab/GLADE/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/tag/v0.1.0
