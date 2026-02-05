.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _sensitivity-analysis:

Sensitivity Analysis
====================

The food systems model involves many uncertain inputs: emission factors for
different greenhouse gases, crop yield potentials, health dose-response
parameters, and policy-level choices like carbon prices and health valuations.
Understanding which of these uncertainties matter most for model outcomes is
essential for directing research priorities and communicating result robustness.

Global sensitivity analysis answers the question: *which input uncertainties
drive the most variation in model outcomes?* This is quantified through
**Sobol indices**, which decompose the variance of each output into
contributions from individual inputs and their interactions.

- **First-order index** (:math:`S_1`): The fraction of output variance
  attributable to a single input, acting alone.
- **Total-order index** (:math:`S_T`): The fraction of output variance
  attributable to a single input, including all its interactions with other
  inputs.

A parameter with :math:`S_1 \approx S_T` influences the output mainly through
its direct effect. A parameter with :math:`S_T \gg S_1` is involved in
significant interactions with other parameters.

This implementation uses **Polynomial Chaos Expansion (PCE)** as a surrogate
model to compute Sobol indices analytically, avoiding the thousands of
additional model evaluations that traditional Monte Carlo methods require.


Methodology
-----------

Polynomial Chaos Expansion
~~~~~~~~~~~~~~~~~~~~~~~~~~

The central idea is to approximate the model's input-output mapping as a
polynomial in the uncertain inputs. If :math:`\mathbf{X} = (X_1, \ldots, X_d)`
are the uncertain parameters and :math:`Y` is a scalar output, the PCE
representation is:

.. math::

   Y \approx \sum_{\boldsymbol{\alpha}} c_{\boldsymbol{\alpha}}\,
   \Psi_{\boldsymbol{\alpha}}(\mathbf{X})

where :math:`\boldsymbol{\alpha} = (\alpha_1, \ldots, \alpha_d)` is a
multi-index, :math:`c_{\boldsymbol{\alpha}}` are scalar coefficients, and
:math:`\Psi_{\boldsymbol{\alpha}}` are multivariate orthonormal polynomials
with respect to the joint input distribution. Each
:math:`\Psi_{\boldsymbol{\alpha}}` is a product of univariate orthonormal
polynomials:

.. math::

   \Psi_{\boldsymbol{\alpha}}(\mathbf{X}) =
   \prod_{i=1}^d \psi_{\alpha_i}^{(i)}(X_i)

where :math:`\psi_k^{(i)}` is the degree-:math:`k` orthonormal polynomial for
the marginal distribution of :math:`X_i`. For uniform inputs these are
(normalised) Legendre polynomials; for normal inputs, Hermite polynomials.

The orthonormality property
:math:`\mathbb{E}[\Psi_{\boldsymbol{\alpha}} \Psi_{\boldsymbol{\beta}}] =
\delta_{\boldsymbol{\alpha}\boldsymbol{\beta}}` is what makes the variance
decomposition exact: the variance of the expansion is simply the sum of
squared coefficients. Once the coefficients are known, Sobol indices follow
directly without further model evaluations.

The polynomial basis is generated using `chaospy
<https://chaospy.readthedocs.io/>`_. A **cross-truncation** parameter
:math:`q \in (0, 1]` controls the multi-index set: lower values favour
lower-order interaction terms, keeping the basis compact. The default is
:math:`q = 0.75`.


Sparse Fitting via LARS
~~~~~~~~~~~~~~~~~~~~~~~~

With many parameters and moderate polynomial degree, the number of candidate
basis terms can exceed the number of model samples. Rather than requiring a
full tensor-product design, the implementation uses **Least Angle Regression
(LARS)** with cross-validation to select a sparse subset of active terms.

LARS incrementally adds basis terms that are most correlated with the current
residual, using cross-validation to choose the optimal number of active terms.
This produces a parsimonious expansion that captures the dominant polynomial
structure without overfitting.

The fitting uses `sklearn.linear_model.LarsCV
<https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LarsCV.html>`_
with 5-fold cross-validation.

**Validation**: The quality of the PCE surrogate is assessed by two metrics:

- **Leave-one-out (LOO) error**: A relative error measure computed via the hat
  matrix, approximating the prediction error on held-out samples without
  refitting. Values below 0.1 indicate a reliable surrogate.
- **R-squared** (:math:`R^2`): The coefficient of determination on training
  data. Values close to 1.0 confirm good fit.


Sobol Indices from PCE Coefficients
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Thanks to the orthonormality of the basis, the total variance of the expansion
is:

.. math::

   D = \sum_{\boldsymbol{\alpha} \neq \mathbf{0}}
   c_{\boldsymbol{\alpha}}^2

The first-order Sobol index for parameter :math:`i` sums the squared
coefficients of terms where *only* :math:`\alpha_i > 0` (no other parameter
is active):

.. math::

   S_{1,i} = \frac{1}{D}
   \sum_{\substack{\boldsymbol{\alpha}:\, \alpha_i > 0 \\
   \alpha_j = 0\; \forall\, j \neq i}}
   c_{\boldsymbol{\alpha}}^2

The total-order Sobol index sums all terms where :math:`\alpha_i > 0`,
regardless of other active parameters:

.. math::

   S_{T,i} = \frac{1}{D}
   \sum_{\boldsymbol{\alpha}:\, \alpha_i > 0}
   c_{\boldsymbol{\alpha}}^2

These indices satisfy :math:`0 \le S_{1,i} \le S_{T,i} \le 1` and
:math:`\sum_i S_{1,i} \le 1` (with equality when there are no interactions).


Conditional Sensitivity Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some parameters are **policy choices** (e.g., GHG price, value per YLL) rather
than epistemic uncertainties. It is often useful to ask: *given a specific
policy setting, how sensitive are outcomes to the remaining uncertain
parameters?*

Designated **slice parameters** are fixed at specific conditioning values.
The conditioning is performed analytically: for each slice parameter :math:`j`
with conditioning value :math:`x_j^*`, the univariate basis is evaluated at
that value, and the resulting factors are absorbed into the PCE coefficients:

.. math::

   c'_{\boldsymbol{\alpha}} = c_{\boldsymbol{\alpha}} \cdot
   \prod_{j \in \text{slice}} \psi_{\alpha_j}^{(j)}(x_j^*)

Sobol indices are then computed from the transformed coefficients
:math:`c'_{\boldsymbol{\alpha}}`, considering only terms where at least one
non-slice parameter is active. The result is a set of Sobol indices for the
remaining parameters, conditional on the policy choices — showing how
sensitivity patterns shift as policy values change.


Experimental Design
-------------------

Sobol Quasi-Random Sequences
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model is sampled using a **scrambled Sobol sequence** — a quasi-random,
low-discrepancy design that provides better coverage of the parameter space
than simple random sampling, especially in high dimensions. The Sobol sequence
fills the :math:`[0, 1]^d` hypercube with balanced coverage, and an inverse
CDF transform maps each dimension to the corresponding parameter distribution.

The implementation uses `scipy.stats.qmc.Sobol
<https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.Sobol.html>`_
with scrambling enabled for improved uniformity. Scrambling is seeded for
deterministic reproducibility (default seed: 42).

.. note::

   The number of samples should be a **power of 2** for optimal balance
   of the Sobol sequence (e.g., 64, 128, 256, 512, 1024).


.. _supported-distributions:

Supported Distributions
~~~~~~~~~~~~~~~~~~~~~~~~

Each uncertain parameter is assigned an independent marginal distribution.
The joint distribution is the product of the marginals (i.e., parameters are
assumed independent).

.. csv-table::
   :header: Distribution, Required fields, Description

   ``uniform`` (default), ``lower``, ``upper``, "Flat distribution over [lower, upper]"
   ``normal``, ``mean``, ``std``, "Gaussian with given mean and standard deviation"
   ``lognormal``, ``mu``, ``sigma``, "Log-normal with log-scale mean and std"

When the ``distribution`` field is omitted, ``uniform`` is assumed (requiring
only ``lower`` and ``upper``).


Configuration
-------------

Sensitivity analysis is configured through the ``_generators`` DSL in the
scenarios file (see :doc:`configuration` for the full generator syntax). A
sensitivity generator uses ``mode: sensitivity`` and specifies parameter
distributions rather than fixed value lists.

**Full example** (from the production configuration):

.. code-block:: yaml

   # config/pce_sensitivity_scenarios.yaml

   # Reference scenario (no sensitivity adjustments)
   default: {}

   _generators:
     - name: pce_{sample_id}
       mode: sensitivity
       samples: 1024
       slice_parameters: [value_per_yll, ghg_price]
       parameters:
         yield_factor:
           lower: 0.8
           upper: 1.2
         ch4_factor:
           lower: 0.7
           upper: 1.3
         n2o_factor:
           lower: 0.7
           upper: 1.3
         luc_factor:
           lower: 0.5
           upper: 1.5
         rr_factor:
           lower: 0.8
           upper: 1.2
         value_per_yll:
           lower: 5000
           upper: 500000
         ghg_price:
           lower: 0
           upper: 300
       template:
         sensitivity:
           crop_yields:
             all: "{yield_factor}"
           emission_factors:
             ch4: "{ch4_factor}"
             n2o: "{n2o_factor}"
             luc: "{luc_factor}"
           health_relative_risk: "{rr_factor}"
         health:
           value_per_yll: "{value_per_yll}"
         emissions:
           ghg_price: "{ghg_price}"

**Field reference**:

- ``name``: Scenario name pattern. Use ``{sample_id}`` as a placeholder for the
  zero-indexed sample number (e.g., ``pce_{sample_id}`` produces ``pce_0``,
  ``pce_1``, ..., ``pce_1023``).
- ``mode: sensitivity``: Activates space-filling Sobol sampling with
  distribution-based parameter specifications.
- ``samples``: Number of samples to draw. Should be a power of 2.
- ``seed`` (optional): Random seed for the scrambled Sobol sequence. Default: 42.
- ``slice_parameters`` (optional): List of parameter names to use as
  conditioning variables in the conditional Sobol analysis. These parameters
  are included in the PCE fit but can be fixed at specific values analytically.
- ``parameters``: Mapping of parameter names to distribution specifications (see
  :ref:`supported-distributions` above).
- ``template``: Configuration template with ``{param_name}`` placeholders that
  are substituted with sampled values. Type is preserved when the placeholder
  is the entire value.


Running the Analysis
--------------------

The sensitivity analysis requires three stages: build all sampled scenarios,
solve them, then run the PCE computation. Snakemake handles all dependencies
automatically.

**Run the full pipeline** (build + solve + analyze for all 1024 samples):

.. code-block:: bash

   tools/smk -j4 --configfile config/pce_sensitivity.yaml

**Run just the PCE analysis step** (after scenarios are already solved):

.. code-block:: bash

   tools/smk -j4 --configfile config/pce_sensitivity.yaml -- \
       results/pce_sensitivity/analysis/pce_global_indices_pce_.csv

The ``{prefix}`` wildcard in the output path matches the scenario name prefix
(here ``pce_``) so the rule knows which scenarios to aggregate.

.. note::

   Each sample requires a full model build and solve. Start with a small sample
   count (32--64) for testing, then increase (512--1024) for production. A
   coarser spatial resolution also reduces per-sample cost.


Output Files
------------

Three CSV files are written to ``results/{name}/analysis/``:

**pce_global_indices_{prefix}.csv** — Global Sobol indices

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric (``total_cost``, ``ghg_emissions``, ``land_use``, ``yll``)"
   ``parameter``, string, "Parameter name from generator spec"
   ``S1``, float, "First-order Sobol index"
   ``ST``, float, "Total-order Sobol index"

One row per (output, parameter) pair. For example, with 4 outputs and 7
parameters, this file has 28 rows.

**pce_conditional_indices_{prefix}.csv** — Conditional Sobol indices

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric"
   ``parameter``, string, "Parameter name (non-slice parameters only)"
   ``S1_cond``, float, "Conditional first-order Sobol index"
   ``ST_cond``, float, "Conditional total-order Sobol index"
   ``conditional_variance``, float, "Output variance when slice parameters are fixed"
   *slice columns*, float, "One column per slice parameter with the conditioning value"

Slice parameter columns are named after the parameters themselves (e.g.,
``value_per_yll``, ``ghg_price``). One row per (output, parameter,
conditioning-value combination).

**pce_validation_{prefix}.csv** — PCE surrogate quality metrics

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric"
   ``loo_error``, float, "Relative leave-one-out error (lower is better)"
   ``r2``, float, "Coefficient of determination"
   ``n_terms``, int, "Total candidate basis terms"
   ``n_active_terms``, int, "Non-zero PCE coefficients after LARS selection"
   ``n_samples``, int, "Number of model samples"
   ``max_degree``, int, "Maximum polynomial degree (default: 3)"

One row per output metric.


Interpreting Results
--------------------

**Reading Sobol indices**:

- :math:`S_1 \approx 1`: This parameter is the dominant driver; reducing its
  uncertainty would substantially reduce output uncertainty.
- :math:`S_T \gg S_1`: This parameter is involved in significant interactions
  with other parameters.
- :math:`S_1 \approx S_T \approx 0`: This parameter has negligible influence
  on the output.

**Example interpretation**: If ``yield_factor`` has :math:`S_1 = 0.6` for
``ghg_emissions``, then 60% of the variance in GHG emissions is explained by
crop yield uncertainty alone.

**Validation quality**:

- LOO error < 0.1 indicates a reliable PCE surrogate.
- LOO error > 0.1 suggests the polynomial approximation is insufficient.
  Consider increasing the number of samples, raising the polynomial degree,
  or checking whether the model response is highly non-smooth.

**Conditional indices**: These show how sensitivity patterns shift with policy
choices. For instance, at low GHG prices, yield uncertainty may dominate
emissions variance, while at high GHG prices, land-use-change factors may
become more important as the model restructures production patterns.
