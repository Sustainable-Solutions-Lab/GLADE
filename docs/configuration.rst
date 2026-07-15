.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Configuration
=============

Overview
--------

The GLADE model is configuration-driven: all scenario parameters, crop selections, constraints, and solver options are defined in YAML configuration files under ``config/``. This allows exploring different scenarios without modifying code.

The default configuration is ``config/default.yaml``, structured into thematic sections.

Custom configuration files
~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of modifying the default configuration file, it is recommended to explore individual scenarios by creating named configuration files, overriding specific parts of the default configuration. Such a named configuration file must contain at the minimum a ``name``. An example could be something like the following: 

.. code-block:: yaml

   # config/my_scenario.yaml
   name: "my_scenario"           # Scenario name → results/my_scenario/
   planning_horizon: 2040        # Override the default 2030 horizon
   land:
     regional_limit: 0.6         # Tighten land availability
     slack_marginal_cost: 1e10   # Optional: raise slack penalty during validation
   emissions:
     ghg_price: 250              # Raise the carbon price above the default

Any keys omitted in your custom file fall back to the defaults shown in the sections below, so you can keep overrides concise.

By default, results are saved under ``results/{name}/``, allowing multiple scenarios coming from different configuration files to coexist. This root (and roots for ``processing``, ``logs``, and ``benchmarks``) can be overridden via ``paths`` in the config.

To build and solve the model based on the above example configuration, you would run the following::

  tools/smk -j4 --configfile config/my_scenario.yaml

Scenario Presets
~~~~~~~~~~~~~~~~

The workflow supports scenario presets defined in ``config/scenarios.yaml`` that apply configuration overrides via a ``{scenario}`` wildcard. This allows exploring variations (e.g., with/without health constraints or GHG pricing) within a single configuration without duplicating config files.

Each scenario preset in ``scenarios.yaml`` contains a set of configuration overrides that are applied recursively on top of the base configuration. For example:

.. code-block:: yaml

   # config/scenarios.yaml
   default:
     health:
       enabled: false
     emissions:
       ghg_pricing_enabled: false

   HG:
     health:
       enabled: true
     emissions:
       ghg_pricing_enabled: true

With default path roots, the scenario name becomes part of all output paths:

- Built models: ``results/{name}/build/model_scen-{scenario}.nc``
- Solved models: ``results/{name}/solved/model_scen-{scenario}.nc``
- Plots: ``results/{name}/plots/scen-{scenario}/``

To build a specific scenario::

  tools/smk -j4 --configfile config/my_scenario.yaml -- results/my_scenario/build/model_scen-HG.nc

This feature enables systematic sensitivity analysis and comparison across policy scenarios using a single configuration file.

Programmatic Scenario Generation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When conducting sensitivity analyses or parameter sweeps, you often need many scenarios that differ only in one or two parameter values. Writing these out manually is tedious and error-prone. The ``_generators`` DSL allows you to define scenario templates that are automatically expanded into concrete scenarios at configuration load time.

**Basic structure**

A generator specification has three required fields:

.. code-block:: yaml

   _generators:
     - name: scenario_{param}      # Name pattern with {placeholders}
       parameters:                 # Parameter definitions
         param:
           <value-spec>
       template:                   # Configuration template
         some_section:
           some_key: "{param}"     # Placeholder substitution

When the configuration is loaded, each generator expands into multiple concrete scenarios. The ``{param}`` placeholders in both the name and template are replaced with generated values.

**Generating parameter values**

There are three ways to specify parameter values:

1. **Log-spaced values** (``space: log``): Uses logarithmic spacing, useful when sensitivity varies across orders of magnitude.

   .. code-block:: yaml

      parameters:
        price:
          space: log
          start: 5       # First value
          stop: 500      # Last value
          num: 8         # Number of points
          round: true    # Optional: round to integers

2. **Linear-spaced values** (``space: lin`` or omitted): Uses uniform spacing.

   .. code-block:: yaml

      parameters:
        fraction:
          space: lin
          start: 0.0
          stop: 1.0
          num: 11

3. **Explicit values** (``values``): Specify exact values for non-uniform grids.

   .. code-block:: yaml

      parameters:
        n:
          values: [3, 5, 10, 20, 50, 100]

**Combination modes**

When a generator has multiple parameters, the ``mode`` field controls how they are combined:

- **Zip mode** (default): Pairs parameters element-wise. All parameter lists must have the same length. Generates N scenarios from N values per parameter. Use this when parameters should vary together along a single dimension.

- **Grid mode**: Computes the Cartesian product. Generates M × N scenarios from M values of one parameter and N of another. Use this to explore a full parameter space.

**Example: Single-parameter sweep**

This generator creates 8 scenarios with log-spaced GHG prices from 5 to 500:

.. code-block:: yaml

   _generators:
     - name: ghg_{ghg}
       parameters:
         ghg:
           space: log
           start: 5
           stop: 500
           num: 8
           round: true
       template:
         emissions:
           ghg_price: "{ghg}"

Result: scenarios ``ghg_5``, ``ghg_8``, ``ghg_14``, ..., ``ghg_500`` (8 total).

**Example: Paired parameters (zip mode)**

This generator creates scenarios where GHG price and YLL value increase together:

.. code-block:: yaml

   _generators:
     - name: ghg_yll_{ghg}
       mode: zip
       parameters:
         ghg:
           space: log
           start: 5
           stop: 500
           num: 8
           round: true
         yll:
           space: log
           start: 50
           stop: 100000
           num: 8
           round: true
       template:
         emissions:
           ghg_price: "{ghg}"
         health:
           value_per_yll: "{yll}"

Result: 8 scenarios where the i-th GHG value pairs with the i-th YLL value.

**Example: Parameter grid (grid mode)**

This generator explores all combinations of GHG and biomass prices:

.. code-block:: yaml

   _generators:
     - name: ghg{ghg}_biomass{biomass}
       mode: grid
       parameters:
         ghg:
           values: [0, 50, 100, 150, 200, 250, 300]
         biomass:
           values: [0, 50, 100, 150, 200]
       template:
         emissions:
           ghg_price: "{ghg}"
         biomass:
           marginal_values_usd_per_tonne: "{biomass}"

Result: 35 scenarios (7 × 5 combinations).

**Mixing generators with manual scenarios**

Generators can coexist with manually defined scenarios in the same file:

.. code-block:: yaml

   # Manual scenario
   baseline:
     validation:
       enforce_baseline_diet: true

   # Generated scenarios
   _generators:
     - name: sensitivity_{x}
       parameters:
         x:
           values: [1, 2, 3]
       template:
         some_param: "{x}"

**Type preservation**

When a placeholder is the entire value (e.g., ``"{param}"``), the numeric type is preserved. When embedded in a string (e.g., ``"prefix_{param}"``), values are converted to strings. This ensures configuration values have the correct types for downstream processing.

Sensitivity analysis mode
^^^^^^^^^^^^^^^^^^^^^^^^^

In addition to ``zip`` and ``grid`` modes, generators support ``mode: sensitivity`` for surrogate-based global sensitivity analysis. In this mode, parameter values are drawn from a **space-filling Sobol sequence** transformed to specified probability distributions, rather than from fixed value lists.

Each parameter specifies a distribution instead of a value range:

.. code-block:: yaml

   _generators:
     - name: gsa_{sample_id}
       mode: sensitivity
       samples: 256
       slice_parameters: [ghg_price]
       parameters:
         yield_factor:
           lower: 0.8
           upper: 1.2
         ch4_factor:
           distribution: lognormal
           mu: 0.0
           sigma: 0.15
         ghg_price:
           lower: 0
           upper: 300
       template:
         sensitivity:
           crop_yields:
             all: "{yield_factor}"
           emission_factors:
             ch4: "{ch4_factor}"
         emissions:
           ghg_price: "{ghg_price}"

Supported distributions are ``uniform`` (default; requires ``lower``, ``upper``), ``log_uniform`` (requires ``lower``, ``upper``; both positive), ``normal`` (requires ``mean``, ``std``), ``normal_ci`` (requires ``lower``, ``upper``; optional ``confidence``, ``bounds``), and ``lognormal`` (requires ``mu``, ``sigma``).

The ``samples`` field sets the number of quasi-random samples (should be a power of 2). The ``slice_parameters`` field designates parameters for conditional analysis — these are included in the surrogate fit but can be fixed at specific values to study how sensitivity changes with policy choices. Surrogate method configuration (PCE, RF) lives in a separate ``sensitivity_analysis`` top-level section.

See :doc:`sensitivity_analysis` for full methodology details, output file formats, and interpretation guidance.

Configuration sections
----------------------

Scenario Metadata
~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: scenario_metadata ---
   :end-before: # --- section: downloads ---

* **planning_horizon**: Target year for optimization (default: 2030). Currently determined only which (projected) population levels to use.
* **currency_base_year**: Base year for inflation-adjusted USD values (default: 2024). All cost data is automatically converted to real USD in this base year using CPI adjustments. See :doc:`crop_production` (Production Costs section) for details on cost modeling.

Download Options
~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: downloads ---
   :end-before: # --- section: paths ---

Path Options
~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: paths ---
   :end-before: # --- section: calibration ---

Calibration Artefact Set
~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: calibration ---
   :end-before: # --- section: netcdf ---

See :ref:`calibration-provenance` for the semantics of ``source`` and
``accept_provenance_mismatch``.

NetCDF Options
~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: netcdf ---
   :end-before: # --- section: validation ---

``paths.*_root`` values support environment-variable and ``~`` expansion in the
Snakefile (for example ``"${GROUP_SCRATCH}/${USER}/GLADE/processing"``).

Validation Options
~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: validation ---
   :end-before: # --- section: land ---

Set ``validation.enforce_baseline_diet`` to ``true`` to force the optimizer to match
baseline consumption derived from the estimated baseline diet. When this flag is active,
the ``diet.baseline_age`` and ``baseline_year`` settings determine which
cohort/year is enforced. Use ``validation.food_group_slack_marginal_cost`` to set the
penalty (USD\ :sub:`2024` per Mt) for the slack generators that backstop those fixed
food-group loads. Keep the value high so slack only activates when recorded production
cannot meet the enforced demand targets.

Set ``validation.enforce_baseline_feed`` to ``true`` to fix animal feed use to
GLEAM-derived baseline levels (see :ref:`gleam-feed-baseline`). The baseline is
scaled from GLEAM 2.0 (2010) to the reference year and calibrated against the
known GLEAM 3.0 global total using ``validation.gleam_calibration_year`` and
``validation.gleam_calibration_total_gt_dm``.

See :doc:`validation` for a detailed walkthrough of the validation workflow and
diagnostic figures.

Consumer Utility Options
^^^^^^^^^^^^^^^^^^^^^^^^

Two mutually exclusive options can be used to represent consumer preference in
the objective:

* ``food_incentives`` applies a single linear marginal-cost adjustment per
  ``(food, country)`` pair.
* ``food_utility_piecewise`` applies a piecewise diminishing marginal utility
  curve per ``(food, country)`` pair.

When ``food_utility_piecewise.enabled`` is ``true``, the workflow always reads
utility blocks from ``results/{name}/consumer_values/utility_blocks.csv``.
These blocks are generated by ``calibrate_food_utility_blocks`` from:

* baseline dual values extracted by ``extract_consumer_values``; and
* baseline per-food consumption from the baseline scenario solve.

The current calibration anchors marginal utility at the baseline quantity:
the utility block containing baseline consumption uses the extracted dual
value, with higher utility below baseline and lower utility above baseline
according to ``food_utility_piecewise.decline_factor``.

``food_utility_piecewise`` cannot be combined with
``validation.enforce_baseline_diet`` in the same scenario.

.. _production-stability-bounds:

Deviation Penalty
^^^^^^^^^^^^^^^^^

The ``deviation_penalty`` section anchors four independent quantities to
their observed baseline-year levels:

* **land.crops** -- crop production area (Mha).
* **land.grassland** -- grassland production area (Mha).
* **feed** -- animal_production feed use (Mt DM).
* **diet** -- per-(food, country) food consumption (Mt).

Cropland and grassland carry separate, independently calibrated L1
costs (their optimisation tensions differ, so a shared land L1
over-penalises one to satisfy the other). Together they let the model
investigate what changes (improved health, reduced emissions, etc.) can
be achieved with limited disruption to existing production and
consumption patterns. The default profile calibrates cropland,
grassland and feed via ``tools/calibrate stability``; diet is off
by default and is intended for specific investigations where the priced
optimum would otherwise reshuffle the diet substantially while leaving
land use approximately unchanged.

Three penalty modes are available, selected via ``penalty_mode``:

* **``hard``**: inequality bounds. Per-link production / feed use is
  bounded by :math:`(1 - \delta) \cdot \text{baseline}` to
  :math:`(1 + \delta) \cdot \text{baseline}` where :math:`\delta` is the
  per-component ``max_relative_deviation``. Supported for land and feed
  only.
* **``l1``** (default): linear absolute-value penalty per link. Each unit
  of deviation costs ``deviation_penalty.<component>.l1_cost`` bn USD
  (per Mha for land, per Mt DM for feed, per Mt for diet).
* **``quadratic``**: ``0.5 * quadratic_cost * sum(deviation^2)``.

The ``deviation_type`` option (``absolute`` or ``relative``) is shared
across components.

**Configuration options**:

* ``deviation_penalty.enabled``: master switch (default: ``true``).
* ``deviation_penalty.penalty_mode``: ``hard``, ``l1``, or ``quadratic``.
* ``deviation_penalty.deviation_type``: ``absolute`` or ``relative``.
* ``deviation_penalty.quadratic_cost``: shared coefficient for quadratic mode.
* ``deviation_penalty.land.enabled`` plus per-component switches
  ``land.crops.enabled``, ``land.grassland.enabled``, ``feed.enabled``,
  ``diet.enabled``. ``land.land_conversion.enabled`` (default ``false``)
  would additionally penalise land-use transitions, but is kept off
  because those carriers include sparing -- the penalty would tax
  reforestation from a zero baseline.
* ``deviation_penalty.<component>.l1_cost`` for the components
  ``land.crops``, ``land.grassland``, ``feed``, ``diet``: L1 penalty
  coefficient (or the string ``"calibrated"`` to resolve from the
  calibration YAML).
* ``deviation_penalty.<component>.l1_cost_factor``: multiplicative factor
  applied after sentinel resolution; lets scenarios scan around the
  calibrated central value without hard-coding absolute numbers.
* ``deviation_penalty.land.crops.max_relative_deviation`` /
  ``land.grassland.max_relative_deviation`` /
  ``feed.max_relative_deviation``: hard-mode bounds.

**Behavior notes**:

* Per-link bounds with zero baseline are constrained to zero (no new
  products introduced) under hard mode.
* The L1 penalty also applies to links with zero baseline.
* Multi-cropping is automatically disabled when ``deviation_penalty.land``
  is enabled.
* The diet penalty has no effect when ``enforce_baseline_diet`` is true
  (consumption is already pinned via ``p_set``).
* Costs land in three separate columns of the per-scenario
  ``analysis/.../objective_breakdown.parquet``: production stability
  (land + feed L1) and diet stability.

The default calibration (cropland + grassland + feed) is regenerated
with ``tools/calibrate stability`` and lands at
``data/curated/calibration/<source>/deviation_penalty.yaml`` (see
:doc:`calibration`).

.. _growth-caps:

Growth Caps
^^^^^^^^^^^

Two **hard upper bounds** on production growth sit alongside the soft
production-stability penalty above. They act as structural backstops
against runaway expansion in either direction (animals or crops) under
L1 stability, *without* depending on the L1 penalty being well-tuned.

Both caps are independent of ``deviation_penalty.enabled`` and are
configured under ``validation.animal_growth_cap`` and
``validation.crop_growth_cap`` respectively.

**Animal growth cap** (``validation.animal_growth_cap``)

Upper-bounds each ``animal_production`` link's feed input at
:math:`(1 + \delta) \cdot \text{baseline}\_\text{feed}\_\text{use}\_\text{mt}\_\text{dm}`.
The granularity is per-(product, feed-category, country), which directly
constrains the feed mix as well as the production level.

* ``animal_growth_cap.enabled``: master switch (default: ``true``)
* ``animal_growth_cap.max_relative_increase``: cap (default ``0.1`` = +10%)

Zero-baseline links get an upper bound of zero, so animal systems cannot
be introduced in countries where they were not present in the baseline.

**Crop growth cap** (``validation.crop_growth_cap``)

Upper-bounds the **total country-level harvested area** of each
modelled crop at :math:`(1 + \delta) \cdot \sum_{r,c,w} \text{baseline}\_\text{area}\_\text{mha}`,
where the sum is over regions, resource classes, and water-supply
types within a country. Country-level (rather than per-link)
granularity preserves within-country reallocation freedom — the model
can still shift crop production between regions and resource classes
based on yield economics — while bounding total country-level
expansion.

* ``crop_growth_cap.enabled``: master switch (default: ``true``)
* ``crop_growth_cap.max_relative_increase``: cap (default ``10.0`` = +1000%, i.e. 11× baseline)

Zero-baseline crop-country groups get an upper bound of zero, so crops
cannot be introduced in countries where they were not present in the
baseline.

The crop cap is intentionally **much** more generous than the animal
cap's ``+10%`` because realistic dietary-shift scenarios already produce
legitimate global crop expansions of 300–400% (e.g. legumes under
plant-shift diets), and per-country shifts can be larger still. The
crop cap is a *backstop* against ridiculous expansion (the canonical
olive-USA case at 19× baseline) rather than a fine-tuned bound on
realistic reallocation. The principled fix to the underlying cost
calibration / L1 interaction lives elsewhere (in how negative
corrections are applied — see :ref:`cost-calibration`).

**Why both caps exist (interaction with cost calibration)**

Cost calibration (see :ref:`cost-calibration`) extracts per-Mha (or
per-Mt-DM) cost corrections from the duals of ±1% hard-bound stability
constraints. Those duals are local marginal-cost gradients valid at
baseline production; applied as a constant per-unit correction at any
production level under L1 stability, the calibration interpretation
breaks for crops or products with very small baselines. The canonical
case is olive in the USA: a moderate ``-0.40 bnUSD/Mha`` cost correction
calibrated at baseline ``0.04 Mha`` would otherwise drive the model to
~0.7 Mha (19× baseline) and starve other US crops (notably maize) of
land. The growth caps prevent this kind of pathological extrapolation
without changing the calibration itself.

**Limitations**

Caps are applied **uniformly at the country level** — the model cannot
exceed +X% in any individual country, but a sector-wide expansion (e.g.
all major producers grow soybean by 50%) is still permitted. This is
intentional: the caps target *spatial reallocation* artifacts, not
sector-level demand growth.

For animals, the per-(product, feed-category) granularity means the
cap also constrains the **feed mix**: a country can't shift entirely
from grain-fed to forage-fed cattle even if total cattle output stays
within ±10%. This is mostly desirable but can be over-restrictive for
counterfactual scenarios that probe alternative feed regimes; raise
``max_relative_increase`` for such studies.

.. _reforestation-cap:

Reforestation Cap
^^^^^^^^^^^^^^^^^

``land.reforestation_cap`` bounds total spared land (``spare_land``
cropland + ``spare_existing_grassland`` pasture) per country at
``max_fraction`` times the country's spareable agricultural area, plus
an additive ``buffer_mha`` allowance. Like the growth caps it is a
solve-time aggregate constraint at country granularity, so the model
keeps its freedom to choose *which* land to spare within a country.

* ``max_fraction``: maximum spareable fraction (default ``1.0`` =
  no constraint; ``0.0`` forbids all sparing).
* ``buffer_mha``: small per-country allowance (default ``0.05``)
  grandfathering the structural minimum sparing a few countries cannot
  avoid (baseline agricultural land no modelled crop can grow and no
  grassland can graze), so a tight cap stays feasible.

``max_fraction`` is the GSA's ``reforest_fraction`` parameter (see
:doc:`sensitivity_analysis`): under heavy emission weighting the
unconstrained model reforests 80--90% of some countries' agricultural
area, and the cap tests how results depend on disallowing such
concentrated reforestation.

Crop Selection
~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: crops ---
   :end-before: # --- section: multiple_cropping ---

See :doc:`crop_production` for full list. Add/remove crops to explore specialized vs. diversified production systems.

Multiple Cropping
~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: multiple_cropping ---
   :end-before: # --- section: macronutrients ---

Define sequential cropping systems as ordered lists of crops. Entries may
repeat a crop (double rice) or mix cereals and legumes (rice→wheat, maize→soybean) and
list multiple ``water_supplies`` (``r`` for rainfed, ``i`` for irrigated) to build both
variants. The ``build_multi_cropping`` rule checks growing-season compatibility,
aggregates eligible area/yields, and sums irrigated water demand; ``build_model`` turns
each combination into a multi-output land link. Leave the section empty to disable the
feature. Multiple cropping zones that imply relay cropping (GAEZ classes "limited double" or
"double rice … limited triple") are still accepted here but are interpreted as sequential crop
chains; relay-specific dynamics are not yet modelled.

Country Coverage
~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: countries ---
   :end-before: # --- section: data ---

Include countries/territories to model; exclude to reduce problem size. Microstate and countries missing essential data are commented out.

Spatial Aggregation
~~~~~~~~~~~~~~~~~~~

Controls regional resolution and land classification.

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: aggregation ---
   :end-before: # --- section: countries ---

**Trade-offs**:
  * More regions → higher spatial resolution, longer solve time
  * Fewer resource classes → faster solving, less yield heterogeneity

Land, Water, Fertilizer, and Residues
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Limits on land, fertilizer availability, and residue management.

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: land ---
   :end-before: # --- section: water ---

Water Supply
~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: water ---
   :end-before: # --- section: fertilizer ---

* ``water.supply_scenario`` selects the water availability dataset: ``sustainable`` (Water Footprint Network blue water availability) or ``current_use`` (Huang et al. irrigation withdrawals). Use ``current_use`` for validation or benchmarking against present-day withdrawals.
* ``water.huang_reference_year`` selects the year (1971-2010) used for the Huang monthly withdrawals when ``supply_scenario`` is ``current_use``.

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: fertilizer ---
   :end-before: # --- section: residues ---

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: residues ---
   :end-before: # --- section: emissions ---

* ``residues.max_feed_fraction_by_region`` overrides the global fraction for ISO3 countries or UN M49 regions/sub-regions.
* Precedence is: country overrides sub-region overrides region.

GAEZ Data Parameters
~~~~~~~~~~~~~~~~~~~~

Configures which GAEZ v5 climate scenario and input level to use.

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: data ---
   :end-before: # --- section: irrigation ---

**Scenarios**:
  * SSP126: Strong mitigation (1.5-2°C warming)
  * SSP370: Moderate emissions (~3°C)
  * SSP585: High emissions (~4-5°C)

**Input Levels**:
  * H: Modern agriculture (fertilizer, irrigation, pest control)
  * L: Subsistence farming (minimal external inputs)

Irrigation
~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: irrigation ---
   :end-before: # --- section: solving ---

Restrict irrigation to water-scarce scenarios or explore rainfed-only production.

Macronutrients
~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: macronutrients ---
   :end-before: # --- section: animal_products ---

Use ``min``, ``max``, or ``equal`` constraints.

Food Groups
~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: food_groups ---
   :end-before: # --- section: diet ---

``included`` lists the food groups tracked by the model. ``constraints`` is an
optional mapping where any included group may define ``min``, ``max``, or
``equal`` targets in g/person/day. Leaving ``constraints`` empty disables all
food group limits; add entries only for the groups you want to control.

Diet Controls
~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: diet ---
   :end-before: # --- section: biomass ---

Customize ``baseline_age`` if you pre-process alternative cohorts for the baseline
diet. The reference year is controlled by the top-level ``baseline_year`` parameter.
These values are used whenever ``validation.enforce_baseline_diet`` is set to ``true``.

Biomass
~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: biomass ---
   :end-before: # --- section: commodities ---

Per-country ``biomass`` buses track dry-matter exports to the energy sector. All foods
listed under ``byproducts`` gain links to this bus, providing a disposal route for
byproducts that lack feed mappings. Crops listed in ``biomass.crops`` can be diverted
directly as feedstocks. The ``marginal_values_usd_per_tonne`` parameter
(USD\ :sub:`2024` per tonne dry matter) sets the price received when biomass leaves the
food system; set to 0 for free disposal.

.. _biomass-disposal-foods:

Foods listed under ``biomass.disposal_foods`` get an additional link from their food
bus to the country's biomass bus, but unlike ``byproducts`` they remain part of the
diet and food-group tracking. This route is intended for foods where actual production
exceeds what the modelled diet absorbs, leaving the optimizer no realistic outlet for
the surplus other than food-balance slack. Two patterns are common:

- **Forced co-products of non-food commodity demand**, e.g. cottonseed oil is a
  fixed-coefficient byproduct of cotton ginning when cotton is grown for fiber.
- **Crops where real-world production includes uses the model does not represent**:
  birdseed and forage for foxtail-millet, post-harvest losses beyond the food-group
  waste factors for sesame, coir/charcoal/husk uses for coconut, whole-peanut feed
  use beyond what the oilseed-meal pool captures for groundnut.

Without a disposal route the consumption equality on these foods would be satisfied
by food slack at ``validation.slack_marginal_cost``, which inflates the objective and,
more importantly, drives the dual variables of the consumption equality strongly
negative — which biases consumer-value calibration (see :doc:`consumer_values`).
The amount actually routed to biomass in a baseline solve is itself a useful diagnostic
of the gap between baseline production and modelled outlets; it can be inspected via
the ``biomass_disposal`` carrier on links in the solved network.

When ``enforce_baseline_demand`` is true, biofuel and biogas crop demand is fixed at
baseline levels. Each biofuel link is created with ``p_nom`` equal to baseline demand
and ``p_min_pu = 1.0``, forcing flow to match demand exactly. Two sources of demand
are combined:

- **Biofuel/industrial demand** from FAOSTAT Food Balance Sheets (``Other uses``
  element), routed via food buses. This captures ethanol (maize grain, sugarcane) and
  biodiesel (vegetable oils) demand.
- **Biogas crop demand** from ``biogas_crop_demand`` (default:
  ``data/curated/biogas_crop_demand.csv``), routed directly from crop buses. This
  captures whole-crop silage maize diverted to anaerobic digestion for biogas
  production. Set ``biogas_crop_demand`` to ``null`` to disable.

.. _biogas-crop-demand-table:

.. list-table:: Biogas crop demand (``data/curated/biogas_crop_demand.csv``)
   :header-rows: 1
   :widths: 10 15 10 50

   * - Country
     - Crop
     - Demand (Mt DM)
     - Source
   * - DEU
     - silage-maize
     - 14.85
     - FNR 2024: 900 kha biogas maize × ~47 t FM/ha × 35% DM [#fnr2024]_
   * - ITA
     - silage-maize
     - 2.40
     - ISAAC/CIB: ~125 kha biogas maize in Po Valley × ~55 t FM/ha [#isaac2023]_
   * - AUT
     - silage-maize
     - 0.25
     - Austrian Biomass Association: ~20 kha estimated [#aba2023]_
   * - CZE
     - silage-maize
     - 0.42
     - Czech Biogas Association: ~40 kha [#czba2023]_

Countries with negligible or zero biogas crop demand are omitted (zero by default).
Denmark banned crop-based biogas feedstock; France caps it at 15%; Poland, Netherlands,
and Belgium use manure-dominant systems.

.. rubric:: Footnotes

.. [#fnr2024] Fachagentur Nachwachsende Rohstoffe (FNR), *Basisdaten Bioenergie
   Deutschland 2024*. https://www.fnr.de/daten-und-fakten/bioenergie/biogas

.. [#isaac2023] ISAAC/CIB, *Il Biogas in Italia — Censimento impianti 2023*.
   https://www.consorziobiogas.it/

.. [#aba2023] Österreichischer Biomasse-Verband, *Basisdaten Bioenergie Österreich
   2023*. https://www.biomasseverband.at/

.. [#czba2023] Česká bioplynová asociace (CzBA), *Biogas in the Czech Republic —
   Annual Report 2023*. https://www.czba.cz/

When ``enforce_fiber_demand`` is true, baseline fiber demand (cotton lint) is enforced
via per-country fiber buses and fixed-capacity stores. Each country with positive
demand gets a ``fiber:{country}`` bus and a ``store:fiber:cotton-lint:{country}`` store
whose capacity equals the FAOSTAT-derived demand. The store bounds
(``e_min_pu = e_max_pu = 1.0``) force the store level to equal demand exactly, so
cotton lint production must match baseline fiber consumption. Cotton lint is excluded
from biomass byproduct routing when fiber demand is enforced to prevent double-counting.

Animal Products
~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: animal_products ---
   :end-before: # --- section: food_groups ---

Disable grazing to force intensive feed-based systems.

Commodity Configuration (Trade and Marketing Costs)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: commodities ---
   :end-before: # --- section: health ---

The ``commodities`` block carries both the inter-hub trade cost (``trade_cost_per_t_km``,
USD_2024 per tonne per km) and the farm-to-wholesale marketing markup
(``marketing_cost_per_t``, USD_2024 per tonne) for every modelled commodity.

Every crop in ``crops:``, every modelled feed category, and every food (including
animal products and byproducts) must appear in exactly one class. The strict
assignment is enforced by ``workflow/validation/commodities.py`` -- there is no
default fallback. See :doc:`costs` for the literature behind the default magnitudes
and :ref:`commodity-cost-classes` for the class-by-class table.

Increase ``trade_cost_per_t_km`` to explore localized food systems; decrease for
globalized trade. The ``marketing_cost_per_t`` parameter is the new
farm-to-wholesale layer; raising it widens the gap between farm-gate production
costs and effective commodity prices in the optimiser.

Emissions Pricing
~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: emissions ---
   :end-before: # --- section: land use change ---

Land Use Change
~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: land use change ---
   :end-before: # --- section: crops ---

Controls how land use change emissions and carbon sequestration are modeled over the planning horizon.

**Parameters**:
  * ``horizon_years``: Time horizon (years) for amortizing land use change emissions
  * ``managed_flux_mode``: How to treat emissions from existing managed land (``"zero"`` assumes no net flux from current agricultural land)
  * ``forest_fraction_threshold``: Minimum forest cover fraction (0-1) required for a grid cell to be eligible for regrowth sequestration when land is spared

Health Configuration
~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: health ---
   :end-before: # --- section: aggregation ---

Reduce ``region_clusters`` or ``log_rr_points`` to speed up solving.

The ``value_per_yll`` parameter monetizes health impacts in USD_2024 per year of life lost (YLL).

Solver Configuration
~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: solving ---
   :end-before: # --- section: plotting ---

**Solver choice**:
  * **HiGHS**: Open-source, fast, good for most problems
  * **Gurobi**: Commercial, often faster for very large problems, requires license (free for academic users)

The ``remote_solve`` subsection allows delegating only ``solve_model`` to a
remote SSH host (for example an HPC login node) while keeping model building
and analysis local. See :doc:`workflow` for setup instructions and usage
details.
Set ``remote_solve.local_scenarios`` (default: ``["baseline"]``) for scenarios
that must always use the local ``solve_model`` rule.

Plotting Configuration
~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: plotting ---

Customize visualization colors for publication-quality plots. The
``colors.food_groups`` palette is applied consistently across all food-group
charts and maps; extend it if you add new groups to ``data/curated/food_groups.csv``.
