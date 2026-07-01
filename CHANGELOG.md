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

### Changed

- The health module is now **disabled by default** (`health.enabled: false`).
  With health off, the workflow no longer requires the manually-downloaded
  IHME GBD data and runs end to end without it; a clear startup error is
  raised if health (or GBD anchoring) is enabled but the data is absent.

### Added

- New `diet.anchor_groups_to_gbd` option that decouples GBD anchoring of the
  baseline diet's risk-factor food groups from the health module. Defaults to
  the sentinel `match_health` (follow `health.enabled`); set `true`/`false` to
  control it independently. Previously anchoring was unconditional. See
  `docs/current_diets.rst` for a quantitative description of the difference and
  the refined-grain caveat. Note that the baseline diet feeds calibration, so
  changing this (or `health.enabled`) requires re-running `tools/calibrate`.
  The committed calibration artefacts remain the GBD-anchored ones (the
  calibration configs pin anchoring on to reproduce them); regenerating them
  for the anchoring-off default is deferred to a dedicated calibration change
  because the dual-based cost/stability steps require Gurobi.

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
