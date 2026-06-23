.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

Because every sample requires a full model build and solve, Sobol indices
are not estimated directly on the model. Instead a cheap **surrogate** is
fitted to the solved samples and the indices are computed from it. Four
surrogate methods are available (``pce``, ``rf``, ``xgb``, ``mlp``); how they
are fitted, validated, and stored is described in
:doc:`surrogate_modelling`. This page covers the experimental design, the
Sobol indices themselves, and how to read them.


Computing Sobol Indices
-----------------------

Once a surrogate is fitted (:doc:`surrogate_modelling`), Sobol indices are
extracted from it by one of two routes. The PCE surrogate gives them in
closed form from its coefficients; the regression surrogates (RF, XGB, MLP)
give them by Monte Carlo sampling of the fitted predictor.

.. _sobol-from-pce:

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


Sobol Indices by Monte Carlo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The regression surrogates (RF, XGB, MLP) have no analytic variance
decomposition, so their Sobol indices are estimated by **Saltelli
pick-freeze Monte Carlo** on the fitted predictor. Two large sample matrices
``A`` and ``B`` are drawn from the joint input distribution; for each
parameter, a hybrid matrix re-uses one column from one matrix and the rest
from the other, and the resulting variance contrasts estimate :math:`S_1`
and :math:`S_T`. Because the surrogate is cheap to evaluate, the Monte Carlo
sample size (``sobol.n_mc_global``) can be large enough to keep the estimator
noise small. This route is more expensive and noisier than the PCE analytic
one, which is the trade-off for handling non-smooth responses.


Conditional Sensitivity Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some parameters are **policy choices** (e.g., GHG price, value per YLL) rather
than epistemic uncertainties. It is often useful to ask: *given a specific
policy setting, how sensitive are outcomes to the remaining uncertain
parameters?*

Designated **slice parameters** are fixed at specific conditioning values.
For **PCE**, the conditioning is performed analytically: for each slice
parameter :math:`j` with conditioning value :math:`x_j^*`, the univariate
basis is evaluated at that value, and the resulting factors are absorbed into
the PCE coefficients:

.. math::

   c'_{\boldsymbol{\alpha}} = c_{\boldsymbol{\alpha}} \cdot
   \prod_{j \in \text{slice}} \psi_{\alpha_j}^{(j)}(x_j^*)

Sobol indices are then computed from the transformed coefficients
:math:`c'_{\boldsymbol{\alpha}}`, considering only terms where at least one
non-slice parameter is active. The result is a set of Sobol indices for the
remaining parameters, conditional on the policy choices — showing how
sensitivity patterns shift as policy values change.

For the **regression surrogates** (RF, XGB, MLP), conditioning is done via
Monte Carlo: slice parameters are held fixed while remaining parameters are
sampled from their marginal distributions, and Sobol indices are computed
from the resulting predictions.


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
   :header: Distribution, Required fields, Optional fields, Description

   ``uniform`` (default), "``lower``, ``upper``", , "Flat distribution over [lower, upper]"
   ``log_uniform``, "``lower``, ``upper``", , "Log-uniform: uniform on log scale over [lower, upper] (both > 0)"
   ``normal``, "``mean``, ``std``", ``bounds``, "Gaussian with given mean and standard deviation"
   ``normal_ci``, "``lower``, ``upper``", "``confidence``, ``bounds``", "Normal distribution where [lower, upper] defines a confidence interval"
   ``lognormal``, "``mu``, ``sigma``", , "Log-normal with log-scale mean and std"

When the ``distribution`` field is omitted, ``uniform`` is assumed (requiring
only ``lower`` and ``upper``).

The ``normal_ci`` distribution derives its mean as ``(lower + upper) / 2`` and
its standard deviation from the confidence level (default: 0.9, i.e. 90% CI).
This is useful when the literature reports uncertainty as a confidence interval
around a central value rather than as explicit standard deviations.

The optional ``bounds`` field (a two-element list ``[lo, hi]``) truncates
``normal`` and ``normal_ci`` distributions to the given range using a truncated
normal. Use ``null`` for an unbounded side (e.g., ``bounds: [0, null]`` enforces
non-negativity). This prevents physically meaningless values (e.g., negative
multiplicative factors) in the tails of the distribution.


Configuration
-------------

Sensitivity analysis is configured through the ``_generators`` DSL in the
scenarios file (see :doc:`configuration` for the full generator syntax). A
sensitivity generator uses ``mode: sensitivity`` and specifies parameter
distributions rather than fixed value lists.

**Full example** (from the production configuration):

.. code-block:: yaml

   # config/gsa.yaml
   name: "gsa"

   scenarios:
     default: {}

     _generators:
       - name: gsa_{sample_id}
         mode: sensitivity
         samples: 4096
         slice_parameters: [value_per_yll, ghg_price]
         parameters:
           yield_factor:
             lower: 0.8
             upper: 1.2
             bounds: [0, null]
           ch4_factor:
             distribution: normal_ci
             lower: 0.5
             upper: 1.5
             confidence: 0.9
             bounds: [0, null]
           # ... (remaining parameters)
         template:
           sensitivity:
             crop_yields:
               all: "{yield_factor}"
             emission_factors:
               ch4: "{ch4_factor}"
               # ...
           health:
             value_per_yll: "{value_per_yll}"
           emissions:
             ghg_price: "{ghg_price}"

   # Surrogate fitting + Sobol settings (see surrogate_modelling for methods)
   sensitivity_analysis:
     holdout_fraction: 0.15
     default_surrogate: mlp
     sobol:
       outputs: [total_cost, co2, ch4, n2o, land_use, yll]
       grid_resolution: 15     # conditional-Sobol grid points per slice axis
       n_mc_global: 16384      # Monte Carlo sample size for global indices
       n_mc_conditional: 2048  # Monte Carlo sample size per conditioning point
     methods:
       # ... per-method hyperparameters; see Surrogate Modelling
     outputs:
       # ... surrogate target declarations; see Surrogate Modelling

The generator defines only the scenario sampling design (parameters,
distributions, sample count). The surrogate methods fitted on those
scenarios, their hyperparameters, and the output targets are configured in
the ``sensitivity_analysis`` section and documented in
:doc:`surrogate_modelling`; the ``sobol`` sub-block below holds the
Sobol-index settings shared across methods.

Health relative risk parameters use a **quantile parameterization**: each
``rr_<risk_factor>`` value is a quantile :math:`q \in [0, 1]` that interpolates
between the GBD confidence bounds [#gbd]_ at every dose-response breakpoint:

.. math::

   \log(\text{RR}(q)) = (1 - q) \cdot \log(\text{RR}_{\text{low}})
   + q \cdot \log(\text{RR}_{\text{high}})

- :math:`q = 0`: GBD lower bound (strongest protective effect for beneficial foods)
- :math:`q = 1`: GBD upper bound (weakest protective effect for beneficial foods)

This is applied per (risk factor, cause, exposure) point, so a single quantile
parameter per risk factor produces cause-specific adjustments automatically.

**Generator field reference**:

- ``name``: Scenario name pattern. Use ``{sample_id}`` as a placeholder for the
  zero-indexed sample number (e.g., ``gsa_{sample_id}`` produces ``gsa_0``,
  ``gsa_1``, ..., ``gsa_4095``).
- ``mode: sensitivity``: Activates space-filling Sobol sampling with
  distribution-based parameter specifications.
- ``samples``: Number of samples to draw. Should be a power of 2.
- ``seed`` (optional): Random seed for the scrambled Sobol sequence. Default: 42.
- ``slice_parameters`` (optional): List of parameter names to use as
  conditioning variables in the conditional Sobol analysis.
- ``parameter_groups`` (optional): Mapping of group names to parameter lists,
  used for plot organisation and colour grouping.
- ``parameters``: Mapping of parameter names to distribution specifications (see
  :ref:`supported-distributions` above).
- ``template``: Configuration template with ``{param_name}`` placeholders that
  are substituted with sampled values. Type is preserved when the placeholder
  is the entire value.

**``sensitivity_analysis.sobol`` field reference** (the Sobol-index settings;
the surrogate-fitting fields -- ``methods``, ``outputs``,
``default_surrogate``, ``holdout_fraction``, ``discover_scenarios_on_disk`` --
are documented in :doc:`surrogate_modelling`):

- ``outputs``: Allowlist of output names whose Sobol indices are computed and
  plotted. Vector and field outputs are excluded by default because the
  per-element fan-out across Monte Carlo samples blows up.
- ``grid_resolution``: Number of grid points for conditional Sobol evaluation
  along each slice-parameter axis.
- ``n_mc_global``: Monte Carlo sample size for global indices (regression
  surrogates only; PCE is analytic).
- ``n_mc_conditional``: Monte Carlo sample size per conditioning point.


.. _sensitivity-parameter-ranges:

Parameter Range Justification
-----------------------------

This section documents the uncertainty ranges assigned to each sensitivity
parameter, with references to the scientific literature. The range represents
the multiplicative factor applied to the model's default values.

**Distribution choice.** Parameters whose ranges are derived from formal
confidence intervals reported in the literature use ``normal_ci`` distributions,
where ``lower`` and ``upper`` define the 90% confidence interval of a normal
distribution (truncated at zero to prevent physically meaningless negative
factors). This applies to the three emission-related factors (CH\ :sub:`4`,
N\ :sub:`2`\ O, and LUC), whose ranges are grounded in IPCC confidence
intervals. The remaining parameters (crop yields, food loss & waste, feed
conversion ratios) use ``uniform`` distributions because their ranges are
derived from informal expert assessments, inter-estimate disagreement, or error
metrics that do not correspond to a well-defined confidence level.

The CH\ :sub:`4` and N\ :sub:`2`\ O sensitivity factors represent combined
uncertainty in both the underlying **emission factors** (measurement and
methodology uncertainty) and the **100-year global warming potentials** (GWP100)
used to convert physical emissions to CO\ :sub:`2`-equivalents. The IPCC AR6
reports GWP100 90% confidence intervals of ±40% for CH\ :sub:`4` and ±47% for
N\ :sub:`2`\ O [#gwp]_. Since EF and GWP uncertainties are independent (the
former is an agricultural measurement question, the latter a climate science
question), they combine approximately in quadrature.

CH\ :sub:`4` factor (``ch4_factor``: 0.5–1.5)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales all CH\ :sub:`4` emissions in the model: enteric
fermentation, manure management (from animal production links), and rice paddy
emissions (from wetland rice crop production links).

**Emission factor uncertainty.** The IPCC 2019 Refinement reports a Tier 1
uncertainty of ±30–50% (95% CI) for livestock CH\ :sub:`4` emission factors
[#ipcc_ch10]_. Enteric fermentation dominates total livestock CH\ :sub:`4`
(roughly 80–90% of the total). Species-specific standard deviations in the 2019
Refinement (Tables 10.12–10.13) translate to 95% CIs of ±26% (sheep) to ±39%
(cattle/buffalo). Manure management uncertainty is considerably larger: Hristov
et al. report a 95% CI of ±63–65% for US manure CH\ :sub:`4` using gridded
Monte Carlo analysis [#hristov]_. A GLEAM one-at-a-time sensitivity analysis
found approximately ±39% total variation in ruminant CH\ :sub:`4` when
perturbing all 92 input parameters [#gleam]_. The midpoint of the IPCC Tier 1
range, ±40%, is a reasonable central estimate for the emission factor alone.

**Combined uncertainty.** Adding GWP100 uncertainty (±40%, 90% CI [#gwp]_) in
quadrature with the emission factor uncertainty (±40%, 95% CI) gives a combined
uncertainty of approximately ±57%. The ±50% range is a conservative rounding of
this combined estimate.

**Distribution.** Since both the IPCC GWP100 (90% CI) and Tier 1 emission factor
(95% CI) uncertainties are reported as formal confidence intervals, the combined
range is treated as a 90% CI of a ``normal_ci`` distribution (truncated at
zero). This is slightly conservative relative to the quadrature result.

N\ :sub:`2`\ O factor (``n2o_factor``: 0.3–1.7)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales all N\ :sub:`2`\ O emissions in the model: manure management
and manure applied to soils (from animal production links), and direct and
indirect emissions from synthetic fertiliser application (from fertiliser
distribution links). N\ :sub:`2`\ O emission factors are among the most
uncertain parameters in agricultural GHG inventories.

**Emission factor uncertainty.** The IPCC 2019 Refinement [#ipcc_ch11]_ reports
the aggregated Tier 1 direct emission factor EF\ :sub:`1` = 0.01 with a 95% CI
of 0.001–0.018, corresponding to multiplicative factors of ×0.1–1.8. When
disaggregated by climate, the 95% CIs are tighter (e.g., wet-climate synthetic
fertiliser: 0.013–0.019 around 0.016, i.e., ±19%) but differ substantially
between wet and dry climates.

Since the model uses the aggregated EF\ :sub:`1` = 0.01 uniformly across
countries, it mixes climate zones where the true EF differs by a factor of ~3
(wet 0.016 vs. dry 0.005). An emission-factor-only range of approximately ±50%
captures:

- The **global aggregate** N\ :sub:`2`\ O **uncertainty** from Tian et al., who
  synthesise bottom-up inventories to estimate total agricultural N\ :sub:`2`\ O
  at 3.8 Tg N/yr with a min–max range across methodologies of 2.5–5.8 Tg N/yr,
  i.e., factors of ×0.66–1.53 relative to the central estimate [#tian]_.
- The **structural uncertainty** from using aggregated rather than
  climate-disaggregated emission factors. Hergoualc'h et al. showed that
  propagating the 95% CIs of disaggregated EFs gives a global estimate of
  883–1,285 Gg N\ :sub:`2`\ O-N/yr (±19% around the midpoint), whereas the
  aggregated method spans 539–2,713 Gg/yr [#hergoualch]_.
- **Indirect** N\ :sub:`2`\ O **pathways** with very wide 95% CIs
  (EF\ :sub:`4` for volatilisation: 0.002–0.05; Frac\ :sub:`LEACH`:
  0.10–0.80) [#ipcc_ch11]_.

The range is narrower than the full IPCC aggregated 95% CI [×0.1, ×1.8] because
global aggregation across countries and N sources averages out regional
extremes. Evidence of possible systematic underestimation (legacy effects
suggesting a true global mean EF of ~1.9% [#legacy_n2o]_) supports including
factors well above 1.0 in the range.

**Combined uncertainty.** Adding GWP100 uncertainty (±47%, 90% CI [#gwp]_) in
quadrature with the emission factor uncertainty (±50%) gives a combined
uncertainty of approximately ±69%, rounded to ±70%.

**Distribution.** As with CH\ :sub:`4`, both contributing uncertainties are
formal confidence intervals, so the combined range is treated as a 90% CI of a
``normal_ci`` distribution (truncated at zero).

Land-use change emissions (``luc_factor``: 0.3–1.7)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales CO\ :sub:`2` emissions from land conversion (both cropland
and pasture expansion). LUC emissions are among the most uncertain components of
the global carbon budget, driven by uncertainty in carbon stocks, the spatial
pattern of conversion, and methodological choices.

The ±70% range aligns with the IPCC AR6 WGIII assessment, which reports a 90%
CI of ±70% for CO\ :sub:`2`-LULUCF emissions — the largest fractional
uncertainty of any major emission category [#ipcc_ar6_luc]_. This is consistent
with:

- The **Global Carbon Budget**: E\ :sub:`LUC` for 2022 is 1.2 ± 0.7 GtC/yr
  (semi-quantitative 1σ / 68% CI), with biogeochemical parameterisation as the
  dominant uncertainty component [#gcb2023]_.
- **Above-ground biomass** carbon stock estimates: the IPCC 2019 Refinement
  default AGB values for tropical forests have standard deviations averaging
  ~55% of the mean across ecological zones and continents [#rozendaal]_. The
  GlobBiomass dataset used in global maps has a global mean error of ~50%
  [#spawn]_.
- **Soil organic carbon** stock change factors (F\ :sub:`LU`, F\ :sub:`MG`,
  F\ :sub:`I`): 95% CIs of ±11–16% per factor, propagating to a combined
  SOC-change uncertainty of ~20–25% for well-characterised climate zones,
  reaching ±50% in poorly sampled regions [#ipcc_ch5]_.
- **Amortisation period** choices: switching from the conventional 20-year to a
  30-year period changes annualised emissions by ~33% [#maciel]_.

**Distribution.** The IPCC AR6 WGIII directly reports ±70% as a 90% CI, making
this the most straightforward case for a ``normal_ci`` distribution (truncated
at zero).

Crop yield factor (``yield_factor``: 0.8–1.2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales all crop production yields (efficiency on ``crop_production``
links), representing uncertainty in attainable yield estimates. The model uses
GAEZ v5 attainable yield data, which are subject to climate model uncertainty,
agronomic model limitations, and spatial aggregation error.

The ±20% range is supported by:

- **Grid-cell prediction error**: Normalised root-mean-square error (NRMSE)
  between GAEZ attainable yields and observed yields is 18–28% across major
  crop groups [#mueller]_.
- **Inter-annual variability**: Coefficients of variation in national crop
  yields are 13–22% for major cereals, reflecting weather-driven fluctuations
  that the model's single-year snapshot does not capture [#ray]_.
- **Climate model spread**: GAEZ v5 provides yield projections under five
  GCMs (GFDL-ESM4, IPSL-CM6A-LR, MPI-ESM1-2-HR, MRI-ESM2-0, UKESM1-0-LL);
  inter-model yield spread is typically 10–25% for a given crop and region.

Yield uncertainty propagates strongly to land use (more yield means less land
required) and GHG emissions (through reduced land-use change pressure).

**Distribution.** A ``uniform`` distribution is used because the range is
synthesised from heterogeneous error metrics (NRMSE, CV, model spread) that do
not correspond to a formal confidence interval at any specific level.

Food loss and waste factor (``flw_factor``: 0.7–1.3)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales the efficiency of ``food_processing`` links, which
incorporate food loss and waste fractions from SDG 12.3.1 data (food loss index
from FAO + food waste index from UNEP). A factor below 1.0 means more food is
lost than the baseline estimates suggest (higher FLW); above 1.0 means less food
is lost (lower FLW).

The ±30% range is supported by:

- **Inter-estimate disagreement**: Global FLW estimates range from ~24% of food
  supply on a caloric basis [#kummu]_ to ~33% on a mass basis [#gustavsson]_ to
  ~40% when on-farm harvest losses are included [#wwf]_, giving multiplicative
  factors of ×0.73–1.21 relative to the FAO central estimate.
- **Measurement bias**: Self-reported consumer waste data (which feed into the
  UNEP Food Waste Index) systematically underestimate actual waste by 20–40%
  compared to direct waste compositional analysis [#quested]_.
- **Country-level data gaps**: The SDG 12.3.1 Food Loss Index has a stated
  random error of ~25% at the country level [#fao_sofa]_. Only ~12% of the
  global population lives in countries that directly track FLW. Much
  post-harvest loss data for developing countries was collected over 30 years
  ago [#parfitt]_.

The dominant bias direction in the literature is underestimation, which would
support an asymmetric range skewed higher. The symmetric ±30% range is a
conservative simplification.

**Distribution.** A ``uniform`` distribution is used because the range is derived
from inter-estimate disagreement and data gap assessments rather than formal
confidence intervals.

Feed conversion ratio factor (``fcr_factor``: 0.8–1.2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This factor scales feed conversion efficiencies (efficiency on
``animal_production`` links), representing uncertainty in how much feed is
required per unit of animal product. Higher values mean better conversion (more
product per unit feed). The model uses Wirsenius (2000) regional feed energy
requirements [#wirsenius]_ converted via NRC/NASEM net-energy-to-metabolisable-energy
factors [#nrc_beef]_ [#nasem_beef]_ [#nrc_dairy]_.

The ±20% range is supported by:

- **Inter-source disagreement**: The model applies calibration multipliers (up
  to 2.0×) to reconcile Wirsenius-based efficiencies with GLEAM feed baselines.
  After calibration, residual disagreement between Wirsenius and GLEAM / Herrero
  et al. [#herrero]_ is typically 10–30% for a given region–product pair.
- **Energy conversion uncertainty**: The NE-to-ME conversion factors (k_m=0.65,
  k_g=0.43, k_l=0.64) are central NRC/NASEM values for a typical mixed diet
  [#nasem_beef]_ [#nrc_dairy]_. Published ranges span k_m: 0.55–0.70 and
  k_g: 0.35–0.50 (depending on diet metabolizability q), introducing ~10–15%
  uncertainty.
- **Temporal lag**: The Wirsenius data reflects ~1994–1998 conditions.
  Monogastric FCRs have improved ~10–20% since then through genetic progress
  (~0.5–1%/year for poultry and pork); ruminant improvement is minimal.
- **Precedent**: Alexander et al. [#alexander]_ and Springmann et al.
  [#springmann]_ both used ±20% for FCR uncertainty in Monte Carlo global food
  system analyses. The IPCC 2019 Refinement reports ±20% uncertainty for Tier 2
  feed intake estimates [#ipcc_ch10]_.

The ±20% range captures data source disagreement, conversion factor
uncertainty, and temporal lag without bleeding into inter-system variation
(which is already represented by the model's regionalized FCR assignment).

**Distribution.** A ``uniform`` distribution is used because the range is based
on inter-source disagreement and precedent from other studies that also used
uniform distributions for FCR uncertainty [#alexander]_ [#springmann]_.

Health relative risk parameters (``rr_protective``, ``rr_harmful``: 0–1)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Relative risk uncertainty is parameterized as quantiles :math:`q \in [0, 1]`
that interpolate between the GBD lower and upper confidence bounds for
dose-response relative risks. At :math:`q = 0` the strongest effect estimate is
used; at :math:`q = 1` the weakest.

Rather than specifying a separate quantile per risk factor, two **grouped**
parameters reduce dimensionality:

- ``rr_protective``: applies to all risk factors whose RR *decreases* with
  intake (e.g., fruits, vegetables, whole grains, legumes, nuts & seeds).
- ``rr_harmful``: applies to all risk factors whose RR *increases* with
  intake (e.g., red meat, processed meat).

Direction is inferred automatically from the dose-response data: for each risk
factor, log_rr at the lowest intake is compared with log_rr at the highest
intake. This grouping is justified because protective food groups share a common
uncertainty mechanism (GBD confidence interval bounds).

Individual risk factor keys (e.g., ``whole_grains: 0.5``) remain supported and
take precedence over group keys when both are specified. However, specifying
both a group key and an individual key for the same risk factor raises an error.

Reforestation cap fraction (``reforest_fraction``: 0.05--1.0)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A per-country cap on the fraction of spareable agricultural land
(baseline cropland + existing pasture) that may be spared for carbon
sequestration, wired to the solve-time constraint
``land.reforestation_cap.max_fraction`` (see :ref:`reforestation-cap`).
At ``1.0`` the cap is inactive; under heavy emission weighting the
unconstrained model reforests 80--90% of some countries' agricultural
area, so this parameter tests how strongly results depend on permitting
such concentrated reforestation. Unlike the other parameters it is a
policy/plausibility lever rather than an empirical uncertainty, so it is
sampled uniformly over a wide range rather than from a fitted
distribution. The ``sequestration`` output (net CO\ :sub:`2` from
spared-land sequestration credits) is included as a Sobol target to
capture its most direct effect.

.. _sensitivity-prod-stability-cost:

Deviation-penalty regime (companion ``gsa_l1.yaml`` config)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The L1 deviation-penalty regime is a structural modelling choice rather than
an empirical uncertainty, so it is not sampled as a Sobol parameter.
``config/gsa.yaml`` runs the single calibrated central regime
(``deviation_penalty.{land.crops,land.grassland,feed}.l1_cost:
"calibrated"``). The lower/higher regimes
(``l1_cost_factor = 1/sqrt(10)`` and ``sqrt(10)``, i.e. half an order of
magnitude below/above the calibrated centre on cropland, grassland and
feed) live in a companion config, ``gsa_l1.yaml``, maintained alongside
the paper. Each regime has its own consumer-values baseline so that the
piecewise food utility blocks are calibrated against the matching L1
regime; the regimes share the rest of the GSA design (parameters,
sampling, slice parameters). Comparing Sobol indices across regimes
shows how sensitivity structure shifts with the stability regime,
without spending Sobol degrees of freedom on a non-empirical axis.

Policy slice parameters
~~~~~~~~~~~~~~~~~~~~~~~

The **value per YLL** (``value_per_yll``: 0–20,000 USD) and **GHG price**
(``ghg_price``: 0–300 USD/tCO\ :sub:`2`-eq) are policy-choice parameters rather
than epistemic uncertainties. They are designated as **slice parameters**:
included in the PCE fit but analytically conditioned on specific values to show
how sensitivity patterns shift across the policy space. Their ranges are chosen
to span a wide policy-relevant domain without claiming to represent a specific
probability distribution.


.. rubric:: References

.. [#gwp] IPCC, 2021: *Climate Change 2021: The Physical Science Basis*,
   WG I, Chapter 7, Table 7.15. GWP100 (fossil CH\ :sub:`4`): 29.8 ± 11;
   GWP100 (N\ :sub:`2`\ O): 273 ± 130 (90% CI).
   https://www.ipcc.ch/report/ar6/wg1/chapter/chapter-7/

.. [#ipcc_ch10] IPCC, 2019: *2019 Refinement to the 2006 IPCC Guidelines for
   National Greenhouse Gas Inventories*, Vol. 4, Ch. 10: Emissions from
   Livestock and Manure Management. Tier 1 uncertainty: ±30–50% (95% CI).
   https://www.ipcc-nggip.iges.or.jp/public/2019rf/pdf/4_Volume4/19R_V4_Ch10_Livestock.pdf

.. [#hristov] Hristov, A. N. et al., 2017: Discrepancies and uncertainties in
   bottom-up gridded inventories of livestock methane emissions for the
   contiguous United States. *Environ. Sci. Technol.*, 51(23), 13668–13677.
   Enteric fermentation 95% CI: ±16–17%; manure management 95% CI: ±63–65%.
   https://doi.org/10.1021/acs.est.7b03332

.. [#gleam] Rivera Moncada, A. et al., 2025: Sensitivity analysis of
   parameters, emission factors, and coefficients for estimating animal
   emissions of ruminant species in GLEAM. *Int. J. Life Cycle Assess.*
   One-at-a-time perturbation of 92 input parameters.
   https://doi.org/10.1007/s11367-025-02529-5

.. [#ipcc_ch11] IPCC, 2019: *2019 Refinement to the 2006 IPCC Guidelines for
   National Greenhouse Gas Inventories*, Vol. 4, Ch. 11: N\ :sub:`2`\ O
   Emissions from Managed Soils, and CO\ :sub:`2` Emissions from Lime and Urea
   Application. EF\ :sub:`1` = 0.01 (95% CI: 0.001–0.018).
   https://www.ipcc-nggip.iges.or.jp/public/2019rf/pdf/4_Volume4/19R_V4_Ch11_Soils_N2O_CO2.pdf

.. [#tian] Tian, H. et al., 2020: A comprehensive quantification of global
   nitrous oxide sources and sinks. *Nature*, 586, 248–256. Central estimate
   3.8 Tg N/yr; range 2.5–5.8 Tg N/yr represents the spread across bottom-up
   inventory methodologies.
   https://doi.org/10.1038/s41586-020-2780-0

.. [#hergoualch] Hergoualc'h, K. et al., 2021: Improved accuracy and reduced
   uncertainty in greenhouse gas inventories by refining the IPCC emission
   factor for direct N\ :sub:`2`\ O emissions from nitrogen inputs to managed
   soils. *Global Change Biol.*, 27, 6536–6550. Global estimate ranges derived
   by propagating IPCC 95% CIs of disaggregated emission factors.
   https://doi.org/10.1111/gcb.15884

.. [#legacy_n2o] Qian, H. et al., 2025: Legacy effects cause systematic
   underestimation of N\ :sub:`2`\ O emission factors. *Nat. Commun.*, 16.
   https://doi.org/10.1038/s41467-025-58090-0

.. [#ipcc_ar6_luc] IPCC, 2022: *Climate Change 2022: Mitigation of Climate
   Change*, WG III, Chapter 7: Agriculture, Forestry, and Other Land Uses.
   CO\ :sub:`2`-LULUCF uncertainty: ±70% (90% CI).
   https://www.ipcc.ch/report/ar6/wg3/chapter/chapter-7/

.. [#gcb2023] Friedlingstein, P. et al., 2023: Global Carbon Budget 2023.
   *Earth Syst. Sci. Data*, 15, 5301–5369. E\ :sub:`LUC` uncertainty described
   as a "semi-quantitative" 1σ (68% CI).
   https://doi.org/10.5194/essd-15-5301-2023

.. [#rozendaal] Rozendaal, D. M. A. et al., 2022: Aboveground forest biomass
   varies across continents, ecological zones and successional stages: refined
   IPCC default values for tropical and subtropical forests. *Environ. Res.
   Lett.*, 17, 014047.
   https://doi.org/10.1088/1748-9326/ac45b3

.. [#spawn] Spawn, S. A. et al., 2020: Harmonized global maps of above and
   belowground biomass carbon density in the year 2010. *Sci. Data*, 7, 112.
   https://doi.org/10.1038/s41597-020-0444-4

.. [#ipcc_ch5] IPCC, 2019: *2019 Refinement to the 2006 IPCC Guidelines for
   National Greenhouse Gas Inventories*, Vol. 4, Ch. 5: Cropland. SOC stock
   change factor uncertainties: ±11–50%.
   https://www.ipcc-nggip.iges.or.jp/public/2019rf/pdf/4_Volume4/19R_V4_Ch05_Cropland.pdf

.. [#maciel] Maciel, V. G. et al., 2022: Towards a non-ambiguous view of the
   amortization period for quantifying direct land-use change in LCA. *Int. J.
   Life Cycle Assess.*, 27, 1299–1315.
   https://doi.org/10.1007/s11367-022-02103-3

.. [#gbd] GBD 2017 Diet Collaborators, 2019: Health effects of dietary risks
   in 195 countries, 1990–2017. *Lancet*, 393, 1958–1972.
   https://doi.org/10.1016/S0140-6736(19)30041-8

.. [#mueller] Mueller, N. D. et al., 2012: Closing yield gaps through nutrient
   and water management. *Nature*, 490, 254–257.
   https://doi.org/10.1038/nature11420

.. [#ray] Ray, D. K. et al., 2015: Climate variation explains a third of
   global crop yield variability. *Nat. Commun.*, 6, 5989.
   https://doi.org/10.1038/ncomms6989

.. [#kummu] Kummu, M. et al., 2012: Lost food, wasted resources: Global food
   supply chain losses and their impacts on freshwater, cropland, and fertiliser
   use. *Sci. Total Environ.*, 438, 477–489.
   https://doi.org/10.1016/j.scitotenv.2012.08.092

.. [#gustavsson] Gustavsson, J. et al., 2011: *Global food losses and food
   waste: extent, causes and prevention*. FAO, Rome.
   https://www.fao.org/4/mb060e/mb060e00.htm

.. [#wwf] WWF-UK, 2021: *Driven to Waste: The Global Impact of Food Loss and
   Waste on Farms*. WWF-UK, Woking. Estimates total FLW at ~40% of production
   when on-farm losses are included.
   https://wwfint.awsassets.panda.org/downloads/driven_to_waste___the_global_impact_of_food_loss_and_waste_on_farms.pdf

.. [#quested] Quested, T. E. et al., 2020: Comparing diaries and waste
   compositional analysis for measuring food waste in the home. *J. Clean.
   Prod.*, 279, 123635. Self-reported estimates 7–40% lower than compositional
   analysis.
   https://doi.org/10.1016/j.jclepro.2020.123635

.. [#fao_sofa] FAO, 2019: *The State of Food and Agriculture 2019: Moving
   forward on food loss and waste reduction*. FAO, Rome. SDG 12.3.1 Food Loss
   Index country-level random error: ~25%.
   https://www.fao.org/3/ca6030en/ca6030en.pdf

.. [#parfitt] Parfitt, J. et al., 2010: Food waste within food supply chains:
   quantification and potential for change to 2050. *Phil. Trans. R. Soc. B*,
   365, 3065–3081.
   https://doi.org/10.1098/rstb.2010.0126

.. [#wirsenius] Wirsenius, S., 2000: *Human Use of Land and Organic Materials:
   Modeling the Turnover of Biomass in the Global Food System*. PhD thesis,
   Chalmers University of Technology.

.. [#nrc_beef] NRC, 2000: *Nutrient Requirements of Beef Cattle*, 7th
   revised ed., update 2000. Washington, DC: The National Academies Press.
   Source of the California Net Energy System used for ruminant ME→NE
   conversion (k_m, k_g). https://doi.org/10.17226/9791

.. [#nasem_beef] NASEM, 2016: *Nutrient Requirements of Beef Cattle*, 8th
   revised ed. Washington, DC: The National Academies Press. Provides the
   updated cubic-in-ME equations for k_m and k_g; evaluated at typical
   mixed-diet metabolizability q ≈ 0.60 these give k_m ≈ 0.65 and
   k_g ≈ 0.43. https://doi.org/10.17226/19014

.. [#nrc_dairy] NRC, 2001: *Nutrient Requirements of Dairy Cattle*, 7th
   revised ed. Washington, DC: The National Academies Press. Specifies
   the fixed ME-to-NEL efficiency k_l = 0.64 used for dairy.
   https://doi.org/10.17226/9825

.. [#herrero] Herrero, M. et al., 2013: Biomass use, production, feed
   efficiencies, and greenhouse gas emissions from global livestock systems.
   *PNAS*, 110, 20888–20893.
   https://doi.org/10.1073/pnas.1308149110

.. [#alexander] Alexander, P. et al., 2016: Human appropriation of land for
   food: The role of diet. *Global Environ. Change*, 41, 88–98. Used ±20% FCR
   uncertainty in Monte Carlo sensitivity analysis.
   https://doi.org/10.1016/j.gloenvcha.2016.09.005

.. [#springmann] Springmann, M. et al., 2018: Options for keeping the food
   system within environmental limits. *Nature*, 562, 519–525. Used ±20% FCR
   uncertainty in Monte Carlo analysis (500 samples, uniform distribution).
   https://doi.org/10.1038/s41586-018-0594-0


Running the Analysis
--------------------

The sensitivity analysis has four stages: build sampled scenarios, solve
them, fit a surrogate over the declared outputs (:doc:`surrogate_modelling`),
and compute Sobol indices (or other diagnostics) from the surrogate.
Snakemake handles all dependencies automatically.

**Run the full pipeline** (build + solve + analyze for all samples):

.. code-block:: bash

   tools/smk -j4 --configfile config/gsa.yaml

**Fit a surrogate only** (after scenarios are already solved):

.. code-block:: bash

   # MLP surrogate for the default scenario group
   tools/smk -j4 --configfile config/gsa.yaml -- \
       results/gsa/surrogates/surrogate_gsa_mlp.pkl

The ``build_surrogate`` rule writes a pickled
``SurrogateBundle`` plus a
validation parquet (see :doc:`surrogate_modelling`); the bundle is what the
Sobol computation, policy sweeps, and uncertainty-band plots consume.

.. note::

   When a sweep solves outside Snakemake (typically the cluster path
   driven by ``tools/batch-solve``; see :doc:`cluster_execution`), set
   ``sensitivity_analysis.discover_scenarios_on_disk: true`` in the
   config (already set in ``config/gsa.yaml``).  With the flag on, the surrogate-fit
   rule scans the analysis directory and fits over scenarios with
   complete outputs on disk, dropping the small fraction that may have
   hit per-solve TimeLimit.  The rule errors out if more than 50 % of
   scenarios are missing, as a guardrail against running it before the
   solve+analyse phase has finished.  When the flag is left at its
   default ``false``, ``build_surrogate`` declares every Sobol scenario
   as an input so a single ``tools/smk`` call drives the whole
   solve→analyse→surrogate chain — the canonical Snakemake idiom and
   the right choice for small / test runs.

**Compute Sobol indices from a surrogate**:

.. code-block:: bash

   tools/smk -j4 --configfile config/gsa.yaml -- \
       results/gsa/analysis/sobol_global_indices_gsa_mlp.parquet

Output paths use two wildcards: ``{group}`` identifies the scenario sampling
group (e.g., ``gsa``, ``gsa-l1-low``) and ``{method}`` selects the surrogate
type (``pce``, ``rf``, ``xgb``, ``mlp``).  All methods consume the same
solved scenarios, and ``sensitivity_analysis.default_surrogate`` selects the
surrogate downstream consumers (notebooks, uncertainty plots) load by
default.

.. note::

   Each sample requires a full model build and solve. Start with a small sample
   count (32--64) for testing, then increase (1024--4096) for production. A
   coarser spatial resolution also reduces per-sample cost.


Output Files
------------

Per (group, method) combination, the surrogate fit writes a pickled bundle
and a validation parquet under ``results/{name}/surrogates/`` (see
:doc:`surrogate_modelling`). The Sobol computation then writes three parquets
under ``results/{name}/analysis/``:

**sobol_global_indices_{group}_{method}.parquet** — Global Sobol indices

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric (``total_cost``, ``co2``, ``ch4``, ``n2o``, ``land_use``, ``yll``; per-gas emissions are in MtCO\u2082eq)"
   ``parameter``, string, "Parameter name from generator spec"
   ``S1``, float, "First-order Sobol index"
   ``ST``, float, "Total-order Sobol index"

One row per (output, parameter) pair.

**sobol_conditional_indices_{group}_{method}.parquet** — Conditional Sobol indices (1D slices)

.. csv-table::
   :header: Column, Type, Description

   ``output``, string, "Output metric"
   ``parameter``, string, "Parameter name (non-slice parameters only)"
   ``S1_cond``, float, "Conditional first-order Sobol index"
   ``ST_cond``, float, "Conditional total-order Sobol index"
   ``conditional_variance``, float, "Output variance when slice parameters are fixed"
   *slice columns*, float, "One column per slice parameter with the conditioning value"

One row per (output, parameter, conditioning-value combination).

**sobol_conditional_joint_indices_{group}_{method}.parquet** — Joint conditional Sobol indices (2D grid)

Same schema as above, but conditioned on *all* slice parameters simultaneously
over a 2D grid. Used by the dominant factor phase diagram plot.

The surrogate bundle and its validation parquet (fit-quality metrics per
target, including spatial ``field`` reconstruction) are described in
:doc:`surrogate_modelling`.

**Plots**

Three types of sensitivity plots are generated per (group, method):

.. code-block:: bash

   tools/smk -j4 --configfile config/gsa.yaml -- \
       results/gsa/plots/sobol_conditional_s1_vs_ghg_price_gsa_pce.pdf

- **Stacked area charts** (``sobol_conditional_s1_vs_{slice}_{group}_{method}.pdf``):
  Conditional first-order Sobol shares vs each slice parameter. One panel per
  model output.
- **Dominant factor phase diagrams** (``sobol_conditional_dominant_factor_{group}_{method}.pdf``):
  2D policy space coloured by which parameter has the highest conditional S1.
- **Contour surfaces** (``sobol_conditional_s1_surface_{parameter}_{group}_{method}.pdf``):
  Per-parameter conditional S1 surface over the 2D policy space.


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
``co2``, then 60% of the variance in net CO\u2082 emissions is explained by
crop yield uncertainty alone.  Total GHG emissions are the sum of the
``co2``, ``ch4``, and ``n2o`` outputs (all in MtCO\u2082eq); for the
multi-output tree methods (RF, XGBoost) that sum is also recovered as the
direct surrogate prediction for total emissions, because linear relations
between outputs are preserved exactly under MSE loss (the smooth MLP and PCE
preserve them only approximately).

**Validation quality**: see :doc:`surrogate_modelling` for the full set of
fit-quality metrics. As a quick guide, a validation error below 0.1 indicates
a reliable surrogate; if it is higher, increase the sample count, or for PCE
the polynomial degree. Comparing a smooth method (PCE/MLP) against a tree
method (RF/XGB) reveals whether the difficulty is response non-smoothness
(where the trees may do better) or simply insufficient data.

**Conditional indices**: These show how sensitivity patterns shift with policy
choices. For instance, at low GHG prices, yield uncertainty may dominate
emissions variance, while at high GHG prices, land-use-change factors may
become more important as the model restructures production patterns.
