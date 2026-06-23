.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: GPL-3.0-or-later

Surrogate Modelling
===================

Each global sensitivity sweep solves thousands of model instances over a
space-filling design of uncertain parameters. A **surrogate** is a cheap
statistical approximation of the model's input-output mapping, fitted once
on those solved samples and then evaluated millions of times in place of the
real model. Surrogates are what make downstream diagnostics tractable:
Sobol variance decomposition (:doc:`sensitivity_analysis`), policy sweeps,
and uncertainty-band plots all query the surrogate rather than re-solving.

This page covers how surrogates are fitted, validated, and consumed. The
parameter design they are fitted on, and the Sobol indices computed from
them, are described in :doc:`sensitivity_analysis`.

The surrogate bundle
--------------------

A single Snakemake rule (``build_surrogate``) fits a surrogate for every
declared output and persists it as a self-contained, pickled
``SurrogateBundle``. The bundle
carries the trained model(s), the generator spec (parameter names,
distributions, slice parameters, seed), the list of trained output columns,
and per-output validation metrics. It is the single artifact every
downstream consumer loads -- nothing re-reads the raw scenario outputs.

All methods expose a uniform prediction interface, so consumers are
agnostic to which surrogate was fitted:

.. code-block:: python

   from workflow.scripts.analysis.surrogate import load_bundle, predict

   bundle = load_bundle("results/gsa/surrogates/surrogate_gsa_mlp.pkl")
   y = predict(bundle, "co2", x)   # x: (n_samples, n_params) in physical units

See :ref:`surrogate-predict-api` for the full API.

Surrogate methods
-----------------

Four methods are supported: ``pce``, ``rf``, ``xgb``, and ``mlp``. The same
solved scenarios can be fitted by several methods independently; which one
downstream consumers load by default is set by
``sensitivity_analysis.default_surrogate`` (currently ``mlp``).

Polynomial Chaos Expansion (PCE)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PCE approximates the response as a polynomial in the uncertain inputs. If
:math:`\mathbf{X} = (X_1, \ldots, X_d)` are the parameters and :math:`Y` is a
scalar output,

.. math::

   Y \approx \sum_{\boldsymbol{\alpha}} c_{\boldsymbol{\alpha}}\,
   \Psi_{\boldsymbol{\alpha}}(\mathbf{X})

where :math:`\boldsymbol{\alpha} = (\alpha_1, \ldots, \alpha_d)` is a
multi-index, :math:`c_{\boldsymbol{\alpha}}` are scalar coefficients, and
:math:`\Psi_{\boldsymbol{\alpha}}` are multivariate orthonormal polynomials
with respect to the joint input distribution, each a product of univariate
orthonormal polynomials:

.. math::

   \Psi_{\boldsymbol{\alpha}}(\mathbf{X}) =
   \prod_{i=1}^d \psi_{\alpha_i}^{(i)}(X_i)

For uniform inputs these are (normalised) Legendre polynomials; for normal
inputs, Hermite polynomials. The orthonormality property
:math:`\mathbb{E}[\Psi_{\boldsymbol{\alpha}} \Psi_{\boldsymbol{\beta}}] =
\delta_{\boldsymbol{\alpha}\boldsymbol{\beta}}` makes the variance
decomposition exact, so once the coefficients are known Sobol indices follow
analytically without further model evaluations (see
:ref:`sobol-from-pce`). The basis is generated with `chaospy
<https://chaospy.readthedocs.io/>`_; a **cross-truncation** parameter
:math:`q \in (0, 1]` controls the multi-index set, with lower values
favouring lower-order interaction terms.

**Sparse fitting via LARS.** With many parameters and moderate degree, the
number of candidate basis terms can exceed the number of samples. Rather
than requiring a full tensor-product design, the coefficients are fitted
with **Least Angle Regression** (`sklearn.linear_model.LarsCV
<https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LarsCV.html>`_,
5-fold CV), which incrementally adds the basis terms most correlated with
the residual and uses cross-validation to choose how many to keep. This
yields a parsimonious expansion that captures the dominant polynomial
structure without overfitting.

PCE handles only scalar outputs; vector and field targets
(:ref:`surrogate-output-kinds`) require a multi-output method.

Regression surrogates (RF, XGB, MLP)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The three regression surrogates share a common **multi-output** pipeline:
all output targets are fitted by a single shared-structure estimator rather
than one model per output. Targets are standardized to zero mean and unit
variance before fitting, so the shared objective is not dominated by the
output with the largest absolute scale; predictions are de-standardized back
to physical units on the way out. Unlike PCE, these methods support scalar,
vector, and field targets, and their Sobol indices are computed by Saltelli
pick-freeze Monte Carlo on the fitted surrogate (:doc:`sensitivity_analysis`)
rather than analytically.

**Random Forest** (``rf``). A bagged ensemble of regression trees
(`sklearn.ensemble.RandomForestRegressor
<https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html>`_,
OOB scoring enabled). Non-parametric and able to capture non-smooth or
discontinuous responses that polynomials miss, at the cost of noisier
sensitivity estimates and piecewise-constant (staircased) predictions.

**XGBoost** (``xgb``). Gradient-boosted trees with
``multi_strategy='multi_output_tree'``, so a single boosted model predicts
all standardized targets jointly. Early stopping on the holdout set governs
the number of boosting rounds. Typically the most accurate method on the
sharp, near-discontinuous scalar outputs (e.g. yield-loss thresholds), but
the resulting bundles are large.

**ReLU MLP** (``mlp``, the default). A multilayer perceptron with ReLU
activations, wrapped in a pipeline that log-transforms the log-uniform
input columns, standardizes all inputs, and regresses the standardized
targets (`sklearn.neural_network.MLPRegressor
<https://scikit-learn.org/stable/modules/generated/sklearn.neural_network.MLPRegressor.html>`_).
Two properties motivate it as the default:

- A ReLU MLP is a **continuous piecewise-linear** map, so predictions have
  smooth, non-staircased gradients -- well suited to the PCA score targets
  of spatial fields (:ref:`surrogate-output-kinds`), where it outperforms the
  tree methods.
- The fitted bundle is **one to two orders of magnitude smaller** on disk
  than an XGBoost bundle and faster at inference, which matters when many
  bundles are synced from the cluster and loaded by notebooks.

The log-transform stage matters because the MLP is not invariant to monotone
input rescalings (tree methods are). Log-uniform parameters such as
``value_per_yll`` or ``ghg_price`` span several orders of magnitude; logging
them first turns a log-uniform marginal into a uniform one and a typically
log-linear response into a near-linear one, which the network fits far more
readily. ``solver='adam'`` with early stopping on a held-out fraction scales
to the full design and avoids the poorer multi-output minima reached by
full-batch lbfgs.

The trade-off versus XGBoost: the MLP's smoothness bias costs a little
accuracy on the sharpest scalar outputs while winning on spatial fields and
on bundle size. Pick ``xgb`` explicitly when raw scalar accuracy on
bang-bang responses matters most.

.. _surrogate-output-kinds:

Output target kinds
-------------------

Each entry under ``sensitivity_analysis.outputs`` declares a ``kind`` that
controls how its reducer output is turned into surrogate targets:

``scalar`` (default)
   The reducer returns one float per scenario, fitted as a single target.

``vector``
   The reducer returns a ``dict[str, float]`` (e.g. global mass per food),
   expanded to one ``{output}.{element}`` column per key and fitted as
   individual targets. Requires a multi-output method (``rf``/``xgb``/``mlp``).

``field``
   A high-dimensional spatial ``dict[str, float]`` -- e.g. per-region
   cropland or grazing area (~700 regions) -- that is too wide to fit
   element by element. It is **PCA-compressed**: ``build_surrogate`` fits a
   PCA on the field matrix (training rows only) and the surrogate predicts
   the ``n_components`` score columns alongside the scalar/vector targets.
   Requires a multi-output method and an explicit ``n_components``.

Per-region land-use fields are strongly low-rank -- ~20-30 components capture
over 99 % of the variance -- so the PCA adds negligible reconstruction error,
and the field's accuracy is governed by how well the surrogate predicts the
leading scores. ``predict_field``
reconstructs the dense field as ``scores @ components + mean`` and
``field_element_keys`` returns the
matching element (e.g. region) labels in column order.

.. code-block:: yaml

   cropland_by_region:
     kind: field
     source: land_use.parquet
     reducer: region_field      # group value_col by key_col, with row filters
     value_col: area_mha
     key_col: region
     exclude_col: crop          # everything except grassland = cropland
     exclude_value: grassland
     n_components: 30
     label: Cropland area by region
     units: Mha

Validation
----------

Surrogate quality is assessed per output target through several metrics,
written to a flat validation parquet (one row per target):

- **Holdout error.** When ``holdout_fraction > 0`` (recommended), the tail of
  the Sobol sequence is reserved as held-out test data; the surrogate is
  fitted on the rest and scored out-of-sample. Using the tail preserves the
  space-filling quality of the training design (Sobol sequences front-load
  coverage). This is the primary ``validation_error``.
- **Leave-one-out error** (PCE only). A relative error computed via the hat
  matrix without refitting.
- **Out-of-bag R²** (RF only). The OOB score from bootstrap aggregation.
- **R²** on training and (when held out) test data, per target.
- **Field reconstruction R²** (field outputs). The dense field is predicted on
  the holdout via the PCA decoder and scored against the true field
  (``field_recon_r2``), alongside ``explained_variance``, ``n_components``,
  and ``n_elements``.

When holdout is disabled, ``validation_error`` falls back to LOO (PCE) or OOB
(RF) error. Vector elements (minor foods) and higher-order PCA scores often
have tiny mass and noisy targets, so poor fits there are logged quietly;
only poor *scalar* fits raise a warning.

**Validation parquet schema**
(``results/{name}/surrogates/surrogate_validation_{group}_{method}.parquet``):

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric (or ``{output}.{element}`` for vector/score columns)"
   ``validation_error``, float, "Primary error metric (holdout error when available)"
   ``r2_train``, float, "R-squared on training data"
   ``r2_test``, float, "R-squared on holdout data (null if holdout disabled)"
   ``n_train``, int, "Number of training samples"
   ``n_test``, int, "Number of holdout samples"
   ``method``, string, "Surrogate method (``pce``, ``rf``, ``xgb``, ``mlp``)"

Additional method-specific columns: ``loo_error``, ``n_terms``,
``n_active_terms``, ``max_degree`` (PCE); ``oob_error``, ``n_estimators``
(RF); ``n_estimators`` (XGB); ``n_iter`` (MLP); ``field_recon_r2``,
``explained_variance``, ``n_components``, ``n_elements`` (field rows).

As a rule of thumb, a validation error below 0.1 indicates a reliable
scalar surrogate. If it is higher, increase the sample count, or for PCE
increase the polynomial degree; comparing a smooth method (PCE/MLP) against a
tree method (RF/XGB) reveals whether the difficulty is response
non-smoothness or insufficient data.

Configuration
-------------

Surrogate configuration lives in the ``sensitivity_analysis`` section,
separate from the ``_generators`` block that defines the sampling design
(:doc:`sensitivity_analysis`). This lets several methods be fitted over the
same solved scenarios without duplicating the design.

.. code-block:: yaml

   sensitivity_analysis:
     holdout_fraction: 0.15        # fraction reserved for out-of-sample validation
     threads: 6                    # sklearn n_jobs / BLAS threads
     default_surrogate: mlp        # method downstream consumers load by default
     discover_scenarios_on_disk: false
     sobol:                        # Sobol index settings (see sensitivity_analysis)
       outputs: [total_cost, co2, ch4, n2o, land_use, yll]
       grid_resolution: 15
       n_mc_global: 16384
       n_mc_conditional: 2048
     methods:
       pce:
         method_options:
           max_degree: 3
           cross_truncation: 0.8
       rf:
         method_options:
           n_estimators: 500
       xgb:
         method_options:
           n_estimators: 5000
           max_depth: 4
           learning_rate: 0.02
           subsample: 0.8
           colsample_bytree: 0.8
           min_child_weight: 5
           early_stopping_rounds: 50
       mlp:
         method_options:
           hidden_layer_sizes: [256, 128, 64]
           solver: adam
           alpha: 0.0001
           max_iter: 3000
           learning_rate_init: 0.001
           n_iter_no_change: 40
     outputs:
       # ... output target declarations (see Output target kinds)

**Field reference**

- ``holdout_fraction``: Fraction of samples reserved for out-of-sample
  validation (e.g. 0.15). Set to 0 to disable holdout.
- ``threads``: Threads for the fit (sklearn ``n_jobs`` and BLAS). Note that
  ``tools/smk -jN`` clamps rule threads to ``N``.
- ``default_surrogate``: Method downstream consumers (uncertainty-band plots,
  notebooks) load when none is named explicitly. Must match a ``methods`` key.
- ``discover_scenarios_on_disk``: When ``false`` (default), ``build_surrogate``
  declares every Sobol scenario as an input so one ``tools/smk`` call drives
  the whole solve-analyse-surrogate chain. When ``true``, it instead scans the
  analysis directory and fits over whatever scenarios have complete outputs --
  the cluster path, where solves run outside Snakemake (:doc:`cluster_execution`).
- ``sobol``: Sobol-index settings shared across methods; see
  :doc:`sensitivity_analysis`.
- ``methods``: Mapping of method name (``pce``, ``rf``, ``xgb``, ``mlp``) to its
  ``method_options`` hyperparameters. Configuration is assumed complete, so each
  declared method must specify all options it uses; the defaults shipped in
  ``config/default.yaml`` are merged in for any method a scenario config does not
  override.
- ``outputs``: Surrogate target declarations (see :ref:`surrogate-output-kinds`).

Running
-------

Fit a surrogate (after the scenarios are solved):

.. code-block:: bash

   # MLP surrogate for the default scenario group
   tools/smk -j4 --configfile config/gsa.yaml -- \
       results/gsa/surrogates/surrogate_gsa_mlp.pkl

Output paths carry two wildcards: ``{group}`` identifies the scenario
sampling group (e.g. ``gsa``, ``gsa-l1-low``) and ``{method}`` selects the
surrogate type (``pce``, ``rf``, ``xgb``, ``mlp``). Per (group, method) the
rule writes the bundle and its validation parquet under
``results/{name}/surrogates/``.

.. _surrogate-predict-api:

Using a fitted surrogate
------------------------

The bundle is the unit of reuse. Persistence is a plain pickle via
``save_bundle`` /
``load_bundle``, and prediction is
uniform across methods:

- ``predict`` ``(bundle, output, x)``
  -- predict one scalar/vector/score column at design matrix ``x`` (physical
  units), returning ``(len(x),)``.
- ``predictor`` ``(bundle, output)``
  -- return a bound ``x -> y`` callable that caches per-output setup (e.g. the
  PCE expansion), for repeated evaluation across a Monte Carlo grid.
- ``predict_field``
  ``(bundle, field_name, x)`` -- predict a full spatial field, reconstructing
  the dense ``(len(x), n_elements)`` map from its PCA scores.
- ``field_element_keys``
  ``(bundle, field_name)`` -- the element (region) labels for the columns of
  ``predict_field``.

These predictors are exactly what the Sobol computation, policy sweeps, and
uncertainty-band plots call; see :doc:`sensitivity_analysis` for how Sobol
indices are derived from them.
