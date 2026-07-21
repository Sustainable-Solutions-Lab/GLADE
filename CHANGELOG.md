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

- Fixed a GAEZ data artefact where a handful of cells carry a negative net
  irrigation requirement, which flipped those crop links into spurious water
  *producers*. Negative requirements are now clipped to zero.
- Multiple cropping is now anchored to an observed baseline derived from
  MIRCA-OS v2 (new automated data source), using the available 2010, 2015, or
  2020 release nearest `baseline_year`. A fixed, documented sequence catalog
  replaces dynamic combination discovery. Config entries may disable catalog
  sequences or add zero-baseline greenfield potential under a new name.
  Irrigated and rainfed observations are attributed separately, competing
  rotations share crop-area budgets, and outputs are derived per config so the
  spatial and climate inputs cannot be mixed between runs.
  `crop_production_multi` links participate in the land deviation penalty, crop
  growth cap, cost calibration, and validation-mode pinning like single-crop
  links. Harvested cycles are reconciled out of the single-crop FAOSTAT
  baselines so each cycle is counted once. A new `multi_crop_cost.csv`
  calibration artefact carries per-(combination, country) bundle corrections.
  The GAEZ growing-season compatibility gate is removed; observed feasibility
  comes from MIRCA plus the GAEZ multiple-cropping zone.

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
- New alternative baseline-diet source `diet.source: fbs`, derived from
  **FAOSTAT Food Balance Sheets**: per-country food supply energy at
  model-basis densities, corrected for consumer waste. The default remains
  the GDD-IA source. No calibration artefact set is shipped for the FBS diet,
  so using it requires running `tools/calibrate` against your config first
  (see `docs/calibration.rst`).
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

- Spatial preprocessing and model construction now reuse region/class cell
  mappings, bound raster cache memory, and vectorize repeated aggregation and
  crop-link operations. This substantially reduces the time and peak memory
  needed to build a default model without changing its contents.
- Crop-yield and harvested-area preparation now compute exact region and
  resource-class cell coverage once per configuration and reuse it across
  crops, substantially reducing build time and peak memory without changing
  outputs.
- **The water system has been rebuilt on a consumption basis.** Irrigation
  previously drew from a single per-region growing-season store sized from
  Huang et al. withdrawals. It now draws from a regional pool anchored on
  WaterGAP 2.2e irrigation consumption, through a per-region delivery link
  whose efficiency `eta_c` is calibrated at build time against observed
  consumption, with availability and scarcity characterised by AWARE 2.0. The
  three water quantities the literature conflates (crop net requirement,
  consumption, withdrawal) are now distinct and separately reported. New
  automatic downloads: AWARE 2.0 and WaterGAP 2.2e (ISIMIP3a). The Water
  Footprint Network "sustainable" supply scenario and the
  `water.supply_scenario` key are removed; the source is now
  `water.data.availability` (`aware` or `current_use`), defaulting to `aware`.
  **This is a results-affecting default change** — the AWARE pool is a looser
  constraint than the previous binding present-day withdrawal cap.
- Water supply and demand can be resolved at **intra-year periods**
  (`water.temporal_resolution`, a divisor of 12), so a season whose surface
  cannot meet its demand draws groundwater endogenously instead of being
  rescued by annual averaging. Crop water demand is placed into periods by the
  observed MIRCA-OS irrigated crop calendar, retimed to WaterGAP's monthly
  requirement. **The default is 1 (annual), which is cheap but has a
  consequence worth stating plainly: at annual resolution the groundwater bands
  are nearly inert and reported depletion falls to near zero — an artefact of
  the resolution, not a finding.** Studies about water should raise it.
- Water supply fidelity is now two independent switches rather than a ladder:
  `water.supply.scarcity_tiers` (convex AWARE scarcity tiers, default off —
  each region-period pool is one flat availability cap) and
  `water.supply.groundwater` (additive annual renewable and non-renewable
  groundwater bands, default on). All four combinations are valid. Scarcity
  pricing or capping now requires `scarcity_tiers` and raises otherwise, since
  with collapsed tiers there is no scarcity signal to price.
- New optional solve-time levers, both off by default: `water_scarcity`
  (pricing and/or capping accumulated AWARE scarcity) and
  `groundwater_depletion` (pricing and/or capping accumulated mining). Analysis
  gains a `water_metrics` output with per-region withdrawal, scarcity,
  renewable groundwater and depletion.
- Model regions are now built **basin-aware**: GADM provinces are first split
  along AWARE hydrological basin boundaries, and each country is partitioned
  into regions balancing geography against basin scarcity
  (`aggregation.regions.basin_scarcity_weight`, default 2.0; 0 recovers the
  previous purely geographic clustering). A province straddling an abundant and
  a scarce basin is no longer pooled into one region, which used to average away
  exactly the sub-provincial scarcity that constrains irrigation. Every region
  is still either contained in one province or a union of whole provinces, so
  regions remain comparable to political units. The
  `aggregation.regions.allow_cross_border` option is removed; regions never
  cross country borders. **This changes default region geometry for
  every config**, so all `processing/` artefacts are rebuilt and the tracked
  calibration sets must be regenerated. Requires the AWARE2.0 basin geopackage
  (new automatic download).

- The **GDD-IA baseline-diet dataset is now retrieved automatically** from
  Zenodo ([10.5281/zenodo.20818140](https://doi.org/10.5281/zenodo.20818140),
  CC-BY-4.0) instead of being obtained on personal request and placed under
  `data/manually_downloaded/`. It is now published as Springmann, M., *Global
  dietary estimates for conducting health, environmental and economic impact
  assessments*, Nature Food (2026),
  [doi:10.1038/s43016-026-01388-z](https://doi.org/10.1038/s43016-026-01388-z),
  and should be cited as such. The data is unchanged, so results are
  unaffected; any GDD-IA CSVs under `data/manually_downloaded/` are now
  ignored and can be deleted. The record ships 1990-2020 in five-year steps;
  for intervening `baseline_year` values the workflow warns and uses the
  closest release. Retrieving it needs no account, so a default build now
  requires no manually-downloaded data at all.
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
- Whole-grain definitions are aligned across diet sources: a new `maize-whole`
  food carries GBD's whole-grain exposure in maize-staple regions,
  `diet.fbs.whole_grain_shares` is refit against GBD per-country whole-grain
  exposure, and GDD-IA cereal energy is re-split by each country's FBS cereal
  composition (fixing starved whole-grain intake for Sahel coarse-grain
  staples). Both calibration artefact sets are refreshed accordingly.
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

### Fixed

- Baseline biofuel/industrial and biogas demand is enforced again. Since
  2026-05-20 the crops-with-supply safety check in `add_biofuel_links` ran
  before any crop production links existed, so every build silently dropped
  the entire fixed biofuel demand (~290 MtDM globally: maize and sugarcane
  ethanol plus palm, soybean and rapeseed oil). Baseline solves still looked
  right because production-stability anchoring mimics the demand, but under
  strong price signals (water or carbon pricing) the model could simply
  abandon bioenergy crops instead of meeting their demand. Models must be
  rebuilt for the fix to take effect; results solved on affected builds
  understate pressure on bioenergy feedstocks.

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
