.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _health-impacts:

Health Impacts
==============

This chapter describes how the model quantifies the health consequences of
dietary choices. It begins with the epidemiological concepts that underpin the
methodology, then explains the implementation strategy for embedding these
nonlinear relationships into a linear optimisation framework.

Conceptual Framework
--------------------

The health module converts dietary intake patterns into monetised health costs
using epidemiological dose–response relationships from the `Global Burden of
Disease (GBD) Study <https://www.healthdata.org/research-analysis/gbd>`_. This
section explains the key concepts and formulas.

Relative Risk
~~~~~~~~~~~~~

For a given disease :math:`d` (e.g., coronary heart disease) and dietary risk
factor (e.g., vegetable intake), the **relative risk** :math:`\mathrm{RR}_d(x)`
quantifies how the probability of developing that disease changes with intake
level :math:`x`. Specifically, :math:`\mathrm{RR}_d(x)` is the ratio of disease
probability at intake :math:`x` to the probability at some reference intake.

.. admonition:: Example

   From GBD data, vegetables and CHD: :math:`\mathrm{RR}_{\mathrm{CHD}}(0) = 1.0`,
   :math:`\mathrm{RR}_{\mathrm{CHD}}(100\text{g}) = 0.91`,
   :math:`\mathrm{RR}_{\mathrm{CHD}}(300\text{g}) = 0.80`.
   Consuming 300g/day of vegetables reduces CHD risk by 20% compared to zero intake.

For **protective foods** (fruits, vegetables, whole grains, etc.), RR decreases
as intake increases. For **harmful foods** (red meat, processed meat), RR
increases with intake.

Theoretical Minimum Risk Exposure Level (TMREL)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The **TMREL**, denoted :math:`\bar{x}`, is the intake level that minimises
disease risk. We define:

.. math::
   \mathrm{RR}_d^{\mathrm{ref}} = \mathrm{RR}_d(\bar{x})

as the reference relative risk at optimal intake. For protective foods, TMREL
corresponds to high intake where the RR curve reaches its minimum. For harmful
foods, TMREL is typically zero.

Population Attributable Fraction (PAF)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The **population attributable fraction** measures how much of the disease burden
would change if intake shifted from a baseline level :math:`x^{\mathrm{base}}`
to a new level :math:`x`. It is defined as:

.. math::
   \mathrm{PAF}_d(x) = 1 - \frac{\mathrm{RR}_d(x)}{\mathrm{RR}_d(x^{\mathrm{base}})}

Interpretation:

- :math:`\mathrm{PAF}_d(x) > 0`: intake :math:`x` is healthier than baseline
  (disease burden decreases)
- :math:`\mathrm{PAF}_d(x) < 0`: intake :math:`x` is less healthy than baseline
  (disease burden increases)
- :math:`\mathrm{PAF}_d(\bar{x})` is the fraction of burden avoidable by shifting
  to optimal intake

.. admonition:: Example

   Suppose baseline vegetable intake is 150g/day with
   :math:`\mathrm{RR}_{\mathrm{CHD}}(150) = 0.87`, and we consider shifting to
   300g/day with :math:`\mathrm{RR}_{\mathrm{CHD}}(300) = 0.80`. Then:

   .. math::
      \mathrm{PAF}_{\mathrm{CHD}}(300) = 1 - \frac{0.80}{0.87} \approx 0.08

   An 8% reduction in CHD burden is attributable to this dietary shift.

Years of Life Lost (YLL)
~~~~~~~~~~~~~~~~~~~~~~~~

**Years of life lost** quantifies premature mortality by multiplying deaths by
remaining life expectancy. Let :math:`\mathrm{YLL}_d` denote the observed
baseline YLL for disease :math:`d` in a population.

When intake changes from baseline :math:`x^{\mathrm{base}}` to :math:`x`, the
change in YLL is:

.. math::
   \Delta\mathrm{YLL}_d = \mathrm{PAF}_d(x) \times \mathrm{YLL}_d

.. admonition:: Example

   A population loses 50,000 years of life annually to CHD
   (:math:`\mathrm{YLL}_{\mathrm{CHD}} = 50{,}000`). If a dietary intervention
   achieves :math:`\mathrm{PAF}_{\mathrm{CHD}} = 0.08`, then:

   .. math::
      \Delta\mathrm{YLL}_{\mathrm{CHD}} = 0.08 \times 50{,}000 = 4{,}000 \text{ YLL avoided}

Multiple Risk Factors
~~~~~~~~~~~~~~~~~~~~~

When multiple dietary risk factors affect the same disease :math:`d`, their
effects combine **multiplicatively**:

.. math::
   \mathrm{RR}_d = \prod_{r} \mathrm{RR}_{r,d}(x_r)

where :math:`r` indexes risk factors and :math:`x_r` is the intake for each.

.. admonition:: Example

   CHD is affected by both vegetables (:math:`\mathrm{RR}_{v,\mathrm{CHD}} = 0.80`)
   and red meat (:math:`\mathrm{RR}_{m,\mathrm{CHD}} = 1.15`). The combined effect:

   .. math::
      \mathrm{RR}_{\mathrm{CHD}} = 0.80 \times 1.15 = 0.92

   Net 8% reduction in CHD risk despite increased red meat consumption.

Health Cost Formulation
~~~~~~~~~~~~~~~~~~~~~~~

In food-opt, we define the health cost as the monetised value of years
of life lost that could have been avoided by eating optimally. For a
population cluster :math:`c` and disease :math:`d`:

.. math::
   \mathrm{Cost}_{c,d}(x) = V \times \left(
   \Delta\mathrm{YLL}_d(\bar{x}) - \Delta\mathrm{YLL}_d(x)
   \right)

where :math:`V` is the value per year of life lost (configured as
``health.value_per_yll``, default 50,000 USD). The term
:math:`\Delta\mathrm{YLL}_d(\bar{x})` is the maximum YLL avoidable (at optimal
intake), while :math:`\Delta\mathrm{YLL}_d(x)` is the YLL actually avoided at
intake :math:`x`. The difference is the YLL that *could have been* avoided but
wasn't—the health cost of not eating optimally.

To get an implementation-friendly formula using relative risk factors directly, we can expand a simplify using :math:`\Delta\mathrm{YLL}_d(x) = \mathrm{PAF}_d(x) \times \mathrm{YLL}_{c,d}` and the above formula for :math:`\mathrm{PAF_d}`:

.. math::
   \Delta\mathrm{YLL}_d(\bar{x}) - \Delta\mathrm{YLL}_d(x)
   &= \mathrm{YLL}_{c,d} \times \left[ \mathrm{PAF}_d(\bar{x}) - \mathrm{PAF}_d(x) \right] \\
   &= \mathrm{YLL}_{c,d} \times \left[
      \left(1 - \frac{\mathrm{RR}_d(\bar{x})}{\mathrm{RR}_d(x^{\mathrm{base}})}\right)
      - \left(1 - \frac{\mathrm{RR}_d(x)}{\mathrm{RR}_d(x^{\mathrm{base}})}\right)
   \right] \\
   &= \frac{\mathrm{YLL}_{c,d}}{\mathrm{RR}_d(x^{\mathrm{base}})}
      \times \left( \mathrm{RR}_d(x) - \mathrm{RR}_d^{\mathrm{ref}} \right)

This gives the final formula:

.. math::
   \mathrm{Cost}_{c,d}(x) = V \times
   \frac{\mathrm{YLL}_{c,d}}{\mathrm{RR}_d(x^{\mathrm{base}})}
   \times \left( \mathrm{RR}_d(x) - \mathrm{RR}_d^{\mathrm{ref}} \right)

**Key properties:**

1. **Zero cost at TMREL**: When :math:`x = \bar{x}`, the cost is zero because
   we avoid as many years of life lost as possible.

2. **Non-negative costs**: Since TMREL minimises RR, we have
   :math:`\mathrm{RR}_d(x) \geq \mathrm{RR}_d^{\mathrm{ref}}` always.

.. admonition:: Example

   Consider a cluster with:

   - :math:`\mathrm{YLL}_{\mathrm{CHD}} = 100{,}000` years (observed CHD burden)
   - :math:`\mathrm{RR}_{\mathrm{CHD}}(x^{\mathrm{base}}) = 1.10` (baseline diet slightly unhealthy)
   - :math:`\mathrm{RR}_{\mathrm{CHD}}^{\mathrm{ref}} = 0.85` (at TMREL)
   - :math:`\mathrm{RR}_{\mathrm{CHD}}(x) = 0.95` (optimised diet, not quite optimal)
   - :math:`V = 50{,}000` USD/YLL

   .. math::
      \mathrm{Cost} = 50{,}000 \times \frac{100{,}000}{1.10} \times (0.95 - 0.85)
      \approx 50{,}000 \times 90{,}909 \times 0.10 \approx 455 \text{ million USD}

   The health cost is approximately 455 million USD for this cluster–disease pair.

GBD Dietary Risk Factors
------------------------

The model uses dietary risk factor definitions from the Global Burden
of Disease Study 2021 [Brauer2024]_. The following table reproduces a
subset of these definitions from Brauer et al. (2024, Supplementary
Appendix 1, p. 171).

.. list-table::
   :header-rows: 1
   :widths: 25 40 35

   * - Risk Factor
     - Definition of Exposure
     - Optimal Level (TMREL)
   * - Diet low in fruit
     - Average daily consumption of fruit including fresh, frozen, cooked,
       canned, or dried fruit, excluding fruit juices and salted or pickled
       fruits
     - 340–350 g/day
   * - Diet low in vegetables
     - Average daily consumption of vegetables, including fresh, frozen, cooked,
       canned, or dried vegetables, excluding legumes, salted or pickled
       vegetables, juices, nuts and seeds, and starchy vegetables
     - 306–372 g/day
   * - Diet low in whole grains
     - Average daily consumption of whole grains (bran, germ, and endosperm in
       natural proportion) from cereals, bread, rice, pasta, etc.
     - 160–210 g/day
   * - Diet low in nuts and seeds
     - Average daily consumption of nuts and seeds, including tree nuts, seeds,
       and peanuts
     - 19–24 g/day
   * - Diet low in legumes
     - Average daily consumption of legumes and pulses, including fresh, frozen,
       cooked, canned, or dried legumes
     - 100–110 g/day
   * - Diet low in seafood omega-3
     - Average daily consumption of EPA and DHA (mg/day)
     - 470–660 mg/day
   * - Diet high in red meat
     - Average daily consumption of unprocessed red meat (beef, pork, lamb,
       goat), excluding processed meats, poultry, fish, and eggs
     - 0–200 g/day
   * - Diet high in processed meat
     - Average daily consumption of meat preserved by smoking, curing, salting,
       or chemical preservatives
     - 0 g/day

**Notes on current implementation:**

- **Risk factors modelled by default**: fruits, vegetables, whole_grains,
  nuts_seeds, legumes, fish, red_meat, prc_meat (configured in
  ``health.risk_factors``)
- **Disease causes modelled**: CHD (coronary heart disease), Stroke, T2DM (type
  2 diabetes), CRC (colorectal cancer)
- **Sugar**: The GBD dataset includes relative risk factors for
  sugar-sweetened beverages, which are not represented in the model
  and thus not included here. No relative risk factors are given for
  total added sugar intake.
- **TMREL values**: Derived from relative risk curves, not taken from the table
  above (see :ref:`tmrel-derivation`)
- **Age range**: Risk factors evaluated for adults ≥25 years
  (``health.intake_age_min``)
- **Intake units**: All quantities in fresh (as consumed) weight, matching GDD
  dietary data conventions

.. _tmrel-derivation:

TMREL Derivation
~~~~~~~~~~~~~~~~

Rather than using the published TMREL ranges from the table above, the
model derives TMREL values directly from the GBD relative risk curves.
For each risk factor, the derived TMREL is the intake level :math:`x`
that minimises the product of :math:`\mathrm{RR}_d(x)` across all
associated disease causes :math:`x`, evaluated on the empirical
exposure points in the RR data. This approach ensures consistency
between the TMREL used in health cost calculations and the underlying
dose–response curves.

Implementation Strategy
-----------------------

Embedding the health cost formulation into a linear programme requires careful
handling of nonlinearities. This section provides a high-level overview of the
implementation approach.

Linearizing multiplicative risk factors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The core challenge is that relative risks **multiply** across risk factors :math:`r`:

.. math::
   \mathrm{RR}_d = \prod_{r} \mathrm{RR}_{r,d}(x_r)

This product is nonlinear in the intake variables :math:`x_r`. This is
a problem since food-opt is nominally formulated as a *linear*
optimization model. Non-linear constraints such as the above cannot
directly be incorporated into the overall linear program formulation,
and generally make the optimization program more difficult to solve
both theoretically and practically speaking.

In order to still incorporate the multiplicative factors, we convert
multiplication to a logarithm + addition + exponential, and use
piecewise-linear approximations of the logarithmic and exponential
functions.

1. Convert multiplication to addition: :math:`\log(\prod_r \mathrm{RR}_{r,d}) = \sum_r \log(\mathrm{RR}_{r,d})`
2. Approximate :math:`\log(\mathrm{RR}_{r,d}(x_r))` as a piecewise-linear function of :math:`x_r`
3. Approximate :math:`\exp(z)` as a piecewise-linear function to recover :math:`\mathrm{RR}_d`

Two-Stage SOS2 Interpolation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The implementation uses **Special Ordered Sets of Type 2 (SOS2)** constraints to
represent piecewise-linear functions without introducing binary variables (when
the solver supports SOS2).

**Stage 1: Intake → log(RR)**

For each risk factor :math:`r` and disease :math:`d`, precompute breakpoints
:math:`(x_k, \log\mathrm{RR}_{r,d}(x_k))` from the GBD dose–response data. During
optimisation, introduce SOS2 variables :math:`\lambda_k` satisfying:

.. math::
   \sum_k \lambda_k = 1, \quad
   x_r = \sum_k x_k \lambda_k, \quad
   \log\mathrm{RR}_{r,d} = \sum_k \lambda_k \log\mathrm{RR}_{r,d}(x_k)

The SOS2 constraint ensures at most two adjacent :math:`\lambda_k` are nonzero,
yielding piecewise-linear interpolation.

**Stage 2: Aggregated log(RR) → RR**

Sum the log-RR contributions across risk factors:
:math:`z_d = \sum_r \log\mathrm{RR}_{r,d}`. Then apply a second SOS2 interpolation
using precomputed breakpoints :math:`(z_m, \exp(z_m))` to recover :math:`\mathrm{RR}_d`.

Health Clustering
~~~~~~~~~~~~~~~~~

Modelling health impacts for each country individually would create an
intractable number of variables and constraints. Instead, countries are grouped
into **health clusters** that share:

- Similar geographic location
- Similar GDP per capita (proxy for healthcare quality)
- Roughly balanced population sizes

The clustering algorithm uses weighted K-means with iterative refinement. The
number of clusters is configured via ``health.region_clusters``.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/health_clusters.png
   :width: 100%
   :alt: Health cluster map

   Health clusters grouping countries based on geographic proximity, GDP per
   capita similarity, and population balance.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/health_burden.png
   :width: 100%
   :alt: Choropleth map of diet-attributable disease burden by health cluster

   Baseline diet-attributable chronic disease burden (years of life lost per
   100,000 population) by health cluster, computed from Global Burden of Disease
   data. Clusters with higher burden tend to have diets with greater exposure to
   dietary risk factors such as low fruit and vegetable intake or high red meat
   consumption.

Solver Compatibility
~~~~~~~~~~~~~~~~~~~~

The piecewise-linear interpolation uses solver-dependent formulations:

- **Gurobi**: Native SOS2 constraint support. Uses λ (lambda) variables with SOS2
  adjacency constraints for efficient piecewise-linear interpolation.

- **HiGHS**: Uses the **delta (incremental) formulation** that requires no binary
  variables, keeping the problem as a pure LP.

Delta Formulation for HiGHS
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Since HiGHS lacks native SOS2 support, the implementation uses an incremental
formulation that avoids binary variables entirely. For n breakpoints
:math:`(x_0, x_1, \ldots, x_{n-1})` with function values
:math:`(f_0, f_1, \ldots, f_{n-1})`:

**Variables**: δ_j ∈ [0,1] for j = 0, ..., n-2 (one per segment)

**Constraints**:

.. math::

   \delta_j \leq \delta_{j-1} \quad \text{for } j \geq 1 \quad \text{(fill-up ordering)}

   x = x_0 + \sum_j \delta_j \cdot \Delta x_j \quad \text{(input interpolation)}

   f(x) = f_0 + \sum_j \delta_j \cdot \Delta f_j \quad \text{(output interpolation)}

where :math:`\Delta x_j = x_{j+1} - x_j` and :math:`\Delta f_j = f_{j+1} - f_j`.

**Why it works**: The fill-up constraints ensure segments are "filled" from left
to right. When the input x is fixed by an equality constraint (as in both Stage 1
and Stage 2), the δ values are uniquely determined without degeneracy.

**Comparison with lambda formulation**:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Aspect
     - Lambda + SOS2
     - Delta (incremental)
   * - Continuous variables
     - n (one per breakpoint)
     - n-1 (one per segment)
   * - Binary variables
     - 0 (with native SOS2)
     - 0
   * - Additional constraints
     - Convexity (Σλ=1) + SOS2
     - Fill-up ordering (n-2)
   * - Problem type
     - LP (Gurobi), MIP (old HiGHS)
     - LP (all solvers)

Data Flow Overview
~~~~~~~~~~~~~~~~~~

**Preprocessing** (``workflow/scripts/prepare_health_costs.py``):

1. Cluster countries into health regions
2. Compute baseline YLL and RR for each cluster–cause pair
3. Build breakpoint tables for SOS2 interpolation
4. Output: ``risk_breakpoints.csv``, ``cause_log_breakpoints.csv``,
   ``cluster_cause_baseline.csv``

**Solver** (``workflow/scripts/solve_model.py``):

1. Read breakpoint tables
2. Create SOS2 variables and constraints for each cluster–risk–cause combination
3. Construct health cost expressions and add to objective

Detailed Implementation
-----------------------

This section provides technical details for developers working with the health
module.

Data Inputs
~~~~~~~~~~~

``workflow/scripts/prepare_health_costs.py`` assembles the following datasets:

- **Baseline diet** (``processing/{name}/dietary_intake.csv``): average daily
  intake by country and food item from the Global Dietary Database (GDD)
- **Relative risks** (``processing/{name}/health/relative_risks.csv``):
  dose–response pairs for each (risk factor, cause) combination from GBD
- **Mortality rates** (``processing/{name}/health/gbd_mortality_rates.csv``):
  cause-specific death rates by age, country and year
- **Population and life tables** (``processing/{name}/population_age.csv`` and
  ``processing/{name}/health/life_table.csv``): age-structured population counts
  and remaining life expectancy schedules

Preparation Workflow
~~~~~~~~~~~~~~~~~~~~

The preprocessing script performs these steps:

1. **Health clustering** – groups countries into ``health.region_clusters``
   clusters using a multi-objective approach that balances:

   - **Geographic proximity** (weight: ``health.clustering.weights.geography``)
   - **GDP per capita similarity** (weight: ``health.clustering.weights.gdp``)
   - **Population balance** (weight: ``health.clustering.weights.population``)

   The cluster map is saved as ``processing/{name}/health/country_clusters.csv``.

2. **Baseline burden** – combines mortality, population and life expectancy to
   compute years of life lost (YLL) per cluster. For each cause, it computes
   both total YLL and diet-attributable YLL using the population-attributable
   fraction. Results: ``processing/{name}/health/cluster_cause_baseline.csv``.

3. **TMREL derivation** – finds the intake that minimises aggregate log(RR) for
   each risk factor. Results: ``processing/{name}/health/derived_tmrel.csv``.

4. **Risk-factor breakpoints** – builds grids of intake values over the
   empirical RR data range, evaluating :math:`\log(\mathrm{RR})` at each point.
   Results: ``processing/{name}/health/risk_breakpoints.csv``.

5. **Cause-level breakpoints** – constructs breakpoints for the aggregated
   log-RR and its exponential. Results:
   ``processing/{name}/health/cause_log_breakpoints.csv``.

From Diet to Risk Exposure
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Per-capita intake**

During optimisation, consumption is tracked using food group stores named
``store_<group>_<ISO3>``. For each health cluster :math:`c` and risk factor
:math:`r`, the solver computes per-capita intake by summing store levels across
countries in the cluster:

.. math::
   I_{c,r} = \frac{10^{12}}{365\,P_c} \sum_{i \in c} e_{i,r}

where :math:`e_{i,r}` is the store level for country :math:`i` and food group
:math:`r` in Mt/year, and :math:`P_c` is the cluster population. The factor
:math:`10^{12}` converts from megatonnes to grams.

**Linearised relative risk curves**

For every (cluster, risk) pair, SOS2 variables :math:`\lambda_k` satisfy:

.. math::
   \sum_k \lambda_k = 1, \quad
   I_{c,r} = \sum_k x_k \lambda_k, \quad
   \log\mathrm{RR}_{c,r,d} = \sum_k \lambda_k \log\mathrm{RR}_{r,d}(x_k)

**Aggregating across risk factors**

The combined effect on each disease is:

.. math::
   \log\mathrm{RR}_{c,d} = \sum_{r \in \mathcal{R}_d} \log\mathrm{RR}_{c,r,d}

**Recovering total relative risk**

A second SOS2 interpolation maps :math:`z = \log\mathrm{RR}_{c,d}` back to
:math:`\mathrm{RR}_{c,d} = \exp(z)` using precomputed breakpoints.

**Health cost expression**

The PyPSA store energy level encodes deviation from optimal:

.. math::
   e_{c,d} = \left(\mathrm{RR}_d(x) - \mathrm{RR}_d^{\mathrm{ref}}\right)
   \cdot \frac{\mathrm{YLL}_{c,d}}{\mathrm{RR}_d(x^{\mathrm{base}})}
   \cdot 10^{-6}

measured in million YLL. The monetary contribution is
``marginal_cost_storage × e``.

Configuration
-------------

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: health ---
   :end-before: # --- section: aggregation ---

Key parameters:

- ``region_clusters``: Number of health clusters (more = finer resolution, slower)
- ``intake_grid_points``: Density of Stage 1 breakpoints
- ``log_rr_points``: Density of Stage 2 breakpoints
- ``value_per_yll``: Monetary value per year of life lost (USD)
- ``risk_factors``: Which dietary risk factors to model
- ``risk_cause_map``: Which causes each risk factor affects

Outputs
-------

The preprocessing rule saves all intermediate products under
``processing/{name}/health/``:

- ``country_clusters.csv``: Cluster assignments
- ``cluster_cause_baseline.csv``: Baseline YLL and RR by cluster–cause
- ``cluster_summary.csv``: Cluster populations
- ``risk_breakpoints.csv``: Stage 1 breakpoint tables
- ``cause_log_breakpoints.csv``: Stage 2 breakpoint tables
- ``derived_tmrel.csv``: TMREL values derived from RR curves

Plotting rules create visualisations under ``results/{name}/plots/``.

References
----------

.. [Brauer2024] Brauer M, Roth GA, Aravkin AY, et al. Global Burden and Strength
   of Evidence for 88 Risk Factors in 204 Countries and 811 Subnational
   Locations, 1990–2021: A Systematic Analysis for the Global Burden of Disease
   Study 2021. *The Lancet*, 2024;403(10440):2162–203.
   https://doi.org/10.1016/S0140-6736(24)00933-4
