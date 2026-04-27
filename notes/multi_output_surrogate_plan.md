<!-- SPDX-FileCopyrightText: 2026 Koen van Greevenbroek -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Multi-output surrogate modelling for GSA

Parking note from a design discussion on 2026-04-20. Not scheduled;
captured so we can pick it up without re-deriving the trade-offs.

## Current state (as of writing)

- GSA pipeline: 8 free uncertain parameters + 2 slice parameters
  (`ghg_price`, `value_per_yll`); Sobol design with ~8k samples; see
  `config/gsa.yaml`.
- Surrogate construction was recently isolated from downstream analysis
  (commits 477bdab, 00ef00c, 1de7fd3). The `build_surrogate` rule writes
  a `SurrogateBundle` pickle; `compute_sobol_sensitivity.py` consumes it
  without refitting.
- Four surrogate methods are wired up (PCE, RF, MARS, XGBoost) through a
  uniform `SurrogateBundle` + `predict()` API in
  `workflow/scripts/analysis/surrogate.py`.
- Surrogates are fit **one output at a time** on four global scalars:
  `total_cost`, `ghg_emissions`, `land_use`, `yll` (see
  `OUTPUT_COLUMNS` in `surrogate.py`).
- Empirically, PCE behaves poorly on this problem — expected, because
  LP solutions are piecewise linear in parameters (kinks where active
  constraints change). We are gravitating toward XGBoost as the default.

## Why go multi-output at all

1. **Richer analysis.** Per-country / per-crop / per-source Sobol maps
   and uncertainty bands, instead of four scalars.
2. **Invariant preservation.** Aggregation identities (total = Σ
   country, emission_ghg = Σ GWP·gas, cost = Σ components) should hold
   between surrogate outputs — they currently don't, because independent
   fits on aggregates are inconsistent with independent fits on
   components.
3. **Efficiency.** The expensive step in LARS-style fitting is basis
   selection; doing it once across outputs rather than per-output is a
   large win when we scale to 10³–10⁵ output coords.

## Method landscape

### Smooth surrogates (PCE family) — documented but not our current path

- **Shared-basis PCE.** Fit one sparse basis across all outputs
  (group-LARS, multi-task LASSO, or simultaneous OMP). Leaves a
  coefficient matrix `C ∈ R^{n_basis × n_out}`.
  - *Key property:* if all outputs share the same basis Φ, any linear
    relation `y₁ = a·y₂ + b·y₃` is preserved **exactly** (least-squares
    projection commutes with linear combinations).
  - Sobol stays analytic per output.
- **POD-PCE.** SVD the output matrix; fit PCE on the first k scores;
  reproject. Scales to huge output dimensions; preserves linear
  structure.
- Both are defeated by kinks unless you push the degree impractically
  high.

### Non-smooth surrogates — the direction we're heading

- **Multi-output XGBoost** with `multi_strategy='multi_output_tree'`
  (XGBoost ≥ 2.0): one tree structure, vector-valued leaves.
  - *Key property:* with **MSE loss**, a leaf value is a mean of
    residuals — a linear operator on targets. Shared tree structure +
    MSE ⇒ **linear invariants preserved exactly**, the tree analogue
    of the shared-basis PCE property.
  - **Independent per-output XGBoost does NOT preserve invariants**
    (different splits, different partition cells). If we go XGBoost,
    we should flip the multi-output switch, not wrap with
    `MultiOutputRegressor`.
- **PCA on outputs → per-component XGBoost → reproject.** Analogue of
  POD-PCE with a non-smooth component emulator. Linear invariants
  survive reprojection (PCA is linear). Cheap Sobol: one Saltelli pass
  evaluates all coords through k component models.
- **LightGBM `linear_tree=True` / Cubist / M5'.** Piecewise-linear
  leaves — structurally a much better match to LP outputs than
  piecewise-constant trees. Less mature tooling; no uniform multi-output.
- **GP with Matérn-½ (Laplace) kernel** + LMC. Handles kinks, preserves
  linear structure, calibrated uncertainty. Data-hungry and O(N³) — at
  N=8192 already uncomfortable. Only if we start caring about calibrated
  uncertainty.

### Sobol without an analytic path

- **Saltelli / pick-freeze** on the trained surrogate. Same definitions
  as PCE's closed form; trivially parallel; easily fits the conditional
  Sobol we already do on slice parameters.
- **fANOVA on tree ensembles** (Hutter et al.): exact variance
  decomposition by traversing the tree partition. Analytical,
  tree-specific.
- **TreeSHAP** is *not* a Sobol substitute — different decomposition.
  Useful for local attribution only.

## Concrete path: multi-output XGBoost on PCA-reduced outputs

Sketch for if/when we pick this up.

### Panels to emulate (in priority order)

| Panel | Source parquet | Coord index | Rough dim |
|---|---|---|---|
| `crop_production` | `crop_production.parquet` | (country, crop) | 5–8k |
| `food_group_consumption` | `food_group_consumption.parquet` | (country, food_group) | ~1.5k |
| `net_emissions` | `net_emissions.parquet` | (source, gas) | 50–200 |
| `land_use` | `land_use.parquet` (aggregated) | (country, crop, water_supply) | 5–10k |
| `objective_breakdown` | `objective_breakdown.parquet` | (component) | 10–20 |
| `health_attribution` | `health_attribution.parquet` | (country, food_group, cluster) | 3–5k |

Skip monetized panels in v1: `ghg_attribution` × `ghg_price`,
`health_marginals` × `value_per_yll`. These mix LP output with a slice
parameter multiplicatively — emulate the physical quantity and apply
monetization at predict time.

### Architecture fit with the current refactor

The refactor is friendly to this extension — changes are additive:

1. New concept `Panel` beside the existing scalar `output`. Scalars
   stay unchanged; panels live in `bundle.panels:
   dict[str, PanelSurrogate]`.
2. New `PanelSurrogate` dataclass in `surrogate.py` holding:
   `coord_index` (canonical MultiIndex), `mean`, `components`,
   `explained_variance_ratio`, `component_models` (list of k XGBs or
   one multi_output_tree), per-panel `transform`.
3. New API `predict_panel(bundle, panel, x) -> DataFrame`. Existing
   `predict(bundle, output, x)` stays scalar.
4. Once panels exist, derivable scalars (`total_cost` = Σ
   `objective_breakdown`, `ghg_emissions` = Σ `net_emissions`, …) can be
   computed from panel predictions — single source of truth.

### Phasing

1. **Phase 0 — input plumbing.** Factor `load_scenario_outputs` in
   `sensitivity_common.py` into `load_scenario_panel(scen_dir,
   panel_spec)` returning a `pd.Series` on a canonical MultiIndex, plus
   `load_panel_matrix(scen_dirs, panel_spec) -> DataFrame (n_scen,
   d_P)`. Canonical coord index is the union across scenarios,
   zero-filled; persist it with the bundle.
2. **Phase 1 — fitting.** `fit_panel(X, Y_panel, method="xgb", k=None,
   variance_target=0.99, transform=...)`:
   - Drop zero-variance coords (e.g. crops not grown in a country).
   - Optional per-panel transform (`log1p` for strictly positive and
     unbounded; `none` for emissions with negative values from spared
     land).
   - Mean-centre; `TruncatedSVD` picks k for cumulative variance ≥
     target, cap k ≤ 50.
   - Fit XGBoost per component with shared hyperparameters (tune on
     component 0, reuse). Alternative: single multi_output_tree XGB on
     the (n_scen, k) score matrix.
3. **Phase 2 — validation.** Per-panel holdout metrics to
   `surrogate_validation_{group}_{method}.parquet`: `panel`, `coord_*`,
   `r2`, `rmse`, `baseline_mean`; plus per-panel summary rows
   (pooled R², median, worst coord).
4. **Phase 3 — Sobol on panels.** `compute_sobol_panel.py`: one
   Saltelli design, one forward pass through `predict_panel` per panel
   produces indices for every coord at cost ≈ k components, not d_P.
5. **Phase 4 — deprecate redundant scalars** once panels cover them.

### First slice if we pick this up

Start with `crop_production` alone. Highest analytical value (per-
country food security, Sobol maps), modest dimension, strictly positive
so `log1p` is clean, no parameter-dependent post-processing. Accept
criteria: pooled holdout R² ≥ 0.95, worst-case coord R² ≥ 0.7. If it
clears that, the rest is mechanical; if not, we've learned something
about kinks before committing to more panels.

## Roadblocks and gotchas

1. **Coord alignment across scenarios.** Long-format parquets omit
   zero-valued rows, and the set of non-zero (country, crop) pairs
   varies. Must build a canonical coord index over the full scenario set
   and reindex-zero-fill. Moderate work, zero risk.
2. **Scale heterogeneity (the real issue).** Wheat/China ~100 Mt,
   minor-crop/small-country ~1e-6 Mt. Plain PCA is dominated by large
   entries.
   - `log1p`: compresses range well but **breaks linear invariants**
     (sum of predictions ≠ prediction of sum). Use only where downstream
     additivity isn't needed.
   - Per-coord z-score: linear, but preserves invariants only when
     scaling factors match across related coords — they won't.
   - *Pragmatic answer:* no transform on emissions/land (where
     `Σ country = global` must hold); `log1p` on production/consumption
     (tail accuracy matters, totals are recomputed on an untransformed
     panel). Make it a per-panel config choice.
3. **Kinks + low rank.** LP kinks may fall in different PCs for
   different coords; too small a k collapses coord-specific R² near
   kinks. Mitigation: `variance_target=0.99`, watch worst-case R²,
   escalate k up to ~50. If a panel still fails, fall back to per-coord
   XGBoost for that panel.
4. **Parameter-dependent outputs.** `ghg_attribution` uses `ghg_price`
   (a slice parameter). Emulate the physical intensity panel, multiply
   by `ghg_price` at predict time. Same for health × `value_per_yll`.
5. **Bundle size.** k XGBs × several panels easily exceeds 100 MB.
   Use XGBoost's JSON booster serialization
   (`booster.save_raw()`) inside the pickle, or split into one pickle
   per panel with a manifest.
6. **Hyperparameter tuning cost.** 6 panels × k≈20 = 120 XGBs with
   per-model CV is painful. Tune once on the first PC of the first
   panel, reuse — the input distribution is fixed, only target noise
   changes modestly.
7. **Memory during fit.** (8192 × 10k × 8 bytes) ≈ 650 MB per panel.
   Stream per-panel rather than loading everything at once.
8. **Downstream plotting.** Existing plots assume scalar outputs.
   Panel-level Sobol needs new visualisations (maps, heatmaps) — plan a
   separate PR so the surrogate change stays contained.
9. **Consistency during transition.** If both scalar and panel
   surrogates exist, `predict(bundle, "total_cost", x)` ≠
   `predict_panel(bundle, "objective_breakdown", x).sum(axis=1)` in
   general. Pick one as canonical or verify agreement in the validation
   parquet.

## Pointers

- `workflow/scripts/analysis/surrogate.py` — `SurrogateBundle`,
  per-method fit/predict, Sobol helpers.
- `workflow/scripts/analysis/build_surrogate.py` — construction
  entrypoint.
- `workflow/scripts/analysis/compute_sobol_sensitivity.py` —
  analysis-only consumer.
- `workflow/scripts/analysis/sensitivity_common.py` —
  `load_scenario_outputs`, `reconstruct_samples`.
- `workflow/scripts/analysis/analyze_model.py` — writes the per-scenario
  analysis parquets that would feed the panel pipeline.
- `workflow/rules/analysis.smk` — `build_surrogate` rule (~L329–362).

---

## v0 implementation plan (decided 2026-04-27)

Pragmatic first cut, narrower than the panel/PCA design above. Targets
foods (~80) and feed categories (~5) as **per-element vector outputs**,
not PCA-reduced panels. XGB/RF only; PCE/MARS hard-blocked.

### Decisions
- Targets: `foods` (global Mt, summed across countries) and
  `feed_categories` (global Mt DM). Drop food groups — recoverable by
  summing foods.
- Surrogate methods: XGB and RF only for vector outputs. PCE/MARS raise
  `NotImplementedError` if a vector output is in scope.
- Sobol indices: new `sensitivity_analysis.sobol` config block with an
  explicit `outputs` allowlist. Default allowlist = the 6 existing
  scalars. Vector outputs are fit but not Sobol-decomposed.
- Storage: keep pickle. Log bundle size in `save_bundle`. Reconsider
  XGB UBJSON serialization only if a real run breaks ~100 MB.
- Vector key naming: `{spec_name}.{key}` (e.g. `foods.wheat`).

### Per-file changes
- `sensitivity_common.py`:
  - `OutputSpec.kind: "scalar" | "vector"` (default scalar).
  - New reducer `pivot_column(path, *, key_col, value_col) -> dict[str, float]`.
  - `load_scenario_outputs()` two-pass: union-of-keys per vector spec,
    expand to flat columns named `{spec.name}.{key}`. Missing keys → 0.
  - `sobol_outputs(sobol_cfg, specs)` helper for the allowlist.
- `surrogate.py`: hard-block PCE/MARS at top of `fit_bundle` when any
  vector output is in `available_columns`. No change to multi-output
  XGB/RF path.
- `build_surrogate.py`: pass expanded column list (after vector
  expansion) to `fit_bundle`.
- `compute_sobol_sensitivity.py` / `sobol_rows_from_bundle()`: accept
  `sobol_columns` filter; plot rules also receive the allowlist.
- `default.yaml`: restructure `sensitivity_analysis` — promote
  `grid_resolution`, `n_mc_global`, `n_mc_conditional` from per-method
  blocks into new `sensitivity_analysis.sobol` block. Add `foods` and
  `feed_categories` vector entries under `outputs`.
- `gsa.yaml`: drop the per-method Sobol knobs (now inherited from
  default).
- `config/schemas/config.schema.yaml`: extend.
- `tests/config/test.yaml`: add a tiny vector output to exercise the new
  path.

### Cost projections
- Bundle size: scalars ~MB; +85 vector elements adds ~5–10 MB for XGB
  shared-tree, more for RF (per-tree leaf vector grows from 6 to 91 dim)
  but still well under 100 MB at 24k scenarios.
- Fit time: XGB ~2–4× current; RF ~2×.
- Sobol & plot fan-out: **unchanged** — that's the point of the
  allowlist.

### Open / deferred
- Per-element validation-error warning threshold for vector outputs
  (current `> 0.1` will spam for small-mass foods).
- Whether to expose a per-spec `methods_excluded` list later, or keep
  the hard-block.
- Population-weighted per-capita variant — deferred; global Mt is
  sufficient and avoids threading population data through reducers.
