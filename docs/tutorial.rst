.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Tutorial
========

This tutorial walks you through two complete modelling exercises with
``food-opt``. It assumes you have finished the :doc:`introduction` (clone,
``pixi install``, credentials, manually-downloaded datasets). You'll leave
each part with solved scenarios, auto-generated plots, and a handful of
hand-rolled comparisons built in a notebook.

Both parts use a reduced spatial resolution of 200 optimisation regions so
that they complete in a few minutes on a laptop (once the one-off raw-data
download — about half an hour, depending on your connection — has run). The
tutorial configs live under ``config/tutorial/``.

.. toctree::
   :hidden:

   tutorials/tutorial_01_analysis
   tutorials/tutorial_02_analysis

Part 1 — GHG prices at a fixed diet
-----------------------------------

In this first exercise, we solve three scenarios that are identical except
for the greenhouse-gas price applied to the objective function, and we hold
consumption at the observed 2020 diet in all three. Because the diet is
fixed, every difference between scenarios comes from how **production** —
which crops are grown where, which livestock systems are used, and where
trade flows — reorganises when emissions become more costly.

Step 1 — Look at the config
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open ``config/tutorial/01_ghg_prices.yaml``. The file is short — every key
not listed here falls back to ``config/default.yaml``:

.. literalinclude:: ../config/tutorial/01_ghg_prices.yaml
   :language: yaml

A few things to note:

* ``name: "tutorial_01"`` controls the output directory: everything lands
  under ``results/tutorial_01/``.
* ``aggregation.regions.target_count: 200`` keeps the LP small enough to
  solve in minutes. The full-resolution default is 750; values below 200
  fail the per-country clustering step because there are more countries in
  the default list than regions.
* ``planning_horizon`` and ``baseline_year`` are both 2020, aligning the
  model with the most recent year for which GDD dietary data exist.
* The ``scenarios:`` block defines three scenarios that each set
  ``validation.enforce_baseline_diet: true``. That flag forces consumption
  per food group to equal the observed 2020 diet in every country.
* ``health.value_per_yll: 0`` disables the health-cost objective. Health
  costs are the subject of separate documentation — we keep them out of the
  tutorial on purpose.

If you want to experiment, you can copy this file to a new name (e.g.
``config/tutorial/01_my_variant.yaml``), change the ``name`` field, and edit
any overrides you like.

Step 2 — Dry run
~~~~~~~~~~~~~~~~

Before committing to a full run, it's worth asking Snakemake what it *would*
do:

.. code-block:: bash

   tools/smk -j4 --configfile config/tutorial/01_ghg_prices.yaml -n

The ``-n`` flag prints the planned execution graph without actually running
anything. On a clean checkout you'll see data-preparation rules (downloads,
region clustering, yield aggregation), the model build, three solves (one
per scenario), analysis extraction, and plotting.

Step 3 — Run the workflow
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   tools/smk -j4 --configfile config/tutorial/01_ghg_prices.yaml

The first run is the longest because Snakemake has to download raw datasets
(GAEZ, GADM, UN WPP, FAOSTAT, ESA CCI, …). Subsequent runs of any tutorial
or other configuration reuse the same cached data. The build step itself is
shared across all scenarios; only the three solves and the downstream
analysis/plots are scenario-specific.

When the workflow finishes, you will find:

* ``results/tutorial_01/build/model.nc`` — the PyPSA network before solving.
* ``results/tutorial_01/solved/model_scen-{baseline,ghg_mid,ghg_high}.nc`` —
  the three solved networks.
* ``results/tutorial_01/analysis/scen-{baseline,ghg_mid,ghg_high}/*.parquet`` —
  standardised statistics extracted from each solve (see :doc:`analysis` for
  the full schema).
* ``results/tutorial_01/plots/scen-*/*.pdf`` — auto-generated figures.
* ``results/tutorial_01/plots/comparison/`` — cross-scenario comparison
  plots, produced because we set ``plotting.comparison_scenarios: "all"``.

Step 4 — Analyse in a notebook
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The companion notebook :doc:`tutorials/tutorial_01_analysis` walks through
five quick comparisons across the three scenarios: total agricultural
land, the cropland vs grassland split, net GHG emissions by gas, the
composition of animal feed, and the objective-cost breakdown. Open it in
the docs to browse the rendered outputs, or download it and run it locally
against your own ``results/tutorial_01/`` directory.

To run it yourself:

.. code-block:: bash

   pixi run -e dev jupyter lab docs/tutorials/tutorial_01_analysis.ipynb

Because all three scenarios share the same (baseline) diet, anything that
moves between them reflects production-side reorganisation. Total
agricultural land typically falls sharply as the GHG price rises (because
marginal land is released and the regrowing land sequesters carbon), the
gas composition of net emissions shifts, and the objective's ``ghg_cost``
column becomes strongly negative — at these prices, net emissions are
negative, so ``ghg_price × emissions`` is a revenue term in the
objective.

.. note::

   The notebook opens with a short contextualisation that is worth
   reading: even at ``baseline``, this tutorial's model uses less land
   than the real world and produces net-negative emissions by default.
   Serious studies "coerce" the model toward observed production using
   ``validation.production_stability`` (see ``config/sensitivity.yaml``
   and ``config/gsa.yaml``) or hard constraints (see
   ``config/validation.yaml``). The tutorial omits both to keep the
   config short.

Part 1 — Summary
~~~~~~~~~~~~~~~~

At this point you've exercised the full end-to-end workflow: config, build,
solve, analysis, and custom post-processing. But because consumption was
held fixed, Tutorial 1 can't tell you whether a different *diet* would
reduce emissions more cheaply — the model had no way to weigh "change what
people eat" against "change how food is produced". Part 2 adds that missing
piece.

Part 2 — Letting diet respond via consumer values
-------------------------------------------------

In Part 1, we fixed consumption with ``enforce_baseline_diet: true``. That
guarantees realism (nobody is forced to eat something unusual), but it also
rules out dietary shift as a mitigation option. A more interesting model
lets the optimiser decide when giving up some of today's diet is worth the
GHG savings — which requires pricing the cost of deviating from today's
diet.

``food-opt`` does that by **deriving consumer values from a baseline
solve**:

1. Solve a baseline scenario with ``enforce_baseline_diet: true``. The
   per-(food, country) equality constraints on the ``food_consumption``
   links are binding, and their **dual variables** (shadow prices) represent
   each food's marginal utility under today's diet — expressed as bn USD
   per Mt.
2. Feed those consumer values into a **piecewise diminishing-marginal-utility
   curve** centred at baseline consumption. Each block represents an
   additional increment of consumption beyond (or below) baseline, with
   decreasing utility.
3. In subsequent scenarios, drop ``enforce_baseline_diet`` and enable the
   piecewise curve. Consumption is now free to move, but the optimiser
   "pays" for deviations — so small dietary shifts are cheap while large
   ones become expensive.

The workflow automates steps 1–2: the ``extract_consumer_values`` and
``calibrate_food_utility_blocks`` rules run automatically whenever a
scenario needs the calibrated blocks.

Step 1 — Look at the config
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open ``config/tutorial/02_consumer_values.yaml``:

.. literalinclude:: ../config/tutorial/02_consumer_values.yaml
   :language: yaml

The key differences from Part 1:

* ``food_utility_piecewise.enabled: true`` at the top level turns on the
  piecewise utility curve globally.
* ``consumer_values.baseline_scenario: "baseline"`` tells the calibration
  step which scenario's dual variables to extract. The name must match one
  of the scenarios below.
* The ``baseline`` scenario keeps ``enforce_baseline_diet: true`` and
  **explicitly disables** the piecewise curve
  (``food_utility_piecewise.enabled: false``). These two settings are
  mutually exclusive — attempting to combine them raises a validation
  error.
* The ``ghg_mid`` and ``ghg_high`` scenarios inherit the top-level
  ``food_utility_piecewise`` settings and no longer set
  ``enforce_baseline_diet``, so consumption is free.

The piecewise-utility parameters themselves are worth a brief look:

* ``n_blocks: 4`` — the curve has four steps above and below baseline.
* ``decline_factor: 0.7`` — each successive block is worth 70% of the
  previous one, giving diminishing returns.
* ``total_width_multiplier: 2.0`` — the curve spans from 0 up to twice
  baseline consumption.

See :doc:`configuration` for the full description.

Step 2 — Solve the baseline first
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Part 2 involves two sequential steps: the baseline must be solved before
consumer values can be extracted and the other scenarios can build their
utility blocks. Snakemake handles the dependency automatically, but it is
instructive to do the baseline on its own first:

.. code-block:: bash

   tools/smk -j4 --configfile config/tutorial/02_consumer_values.yaml -- \
       results/tutorial_02/solved/model_scen-baseline.nc

After this finishes you will have:

* ``results/tutorial_02/solved/model_scen-baseline.nc`` — the baseline
  solution.
* ``results/tutorial_02/consumer_values/baseline/values.csv`` — the
  extracted dual variables.
* ``results/tutorial_02/consumer_values/baseline/utility_blocks.csv`` — the
  calibrated piecewise utility curve.

The companion notebook :doc:`tutorials/tutorial_02_analysis` begins with a
quick look at the extracted values — the ``value_bnusd_per_mt`` column of
``values.csv`` ranks each (food, country) pair by the marginal utility the
baseline implies.

Step 3 — Solve the remaining scenarios
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   tools/smk -j4 --configfile config/tutorial/02_consumer_values.yaml

Now both the mid- and high-GHG scenarios solve, using the same calibrated
utility blocks. On a laptop, each solve takes a few minutes longer than
Part 1 because the LP has extra variables for the piecewise blocks.

Step 4 — Compare against Tutorial 1 in a notebook
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The companion notebook :doc:`tutorials/tutorial_02_analysis` covers three
comparisons:

* Global food-group consumption across the three scenarios, to see whether —
  and which — food groups actually shift once the diet is free.
* The objective breakdown with the ``consumer_values`` column visible
  alongside ``ghg_cost`` (the two forces trading off against each other).
* A side-by-side comparison of net GHG emissions between Tutorial 1
  (fixed diet) and Tutorial 2 (flexible diet) at identical GHG prices. The
  gap between the two is a rough measure of the demand-side mitigation
  potential.

Also have a look at the auto-generated comparison plot at
``results/tutorial_02/plots/consumer_values/consumption_comparison.pdf``,
which shows the same pattern per food group.

Gotchas
~~~~~~~

A few things that commonly trip people up:

* ``food_utility_piecewise.enabled: true`` and
  ``validation.enforce_baseline_diet: true`` cannot be active for the same
  scenario. The baseline scenario enables the latter and disables the
  former; all other scenarios do the opposite.
* ``consumer_values.baseline_scenario`` must name a scenario that exists and
  that has ``enforce_baseline_diet: true``. If it doesn't, the calibration
  rule fails with a validation error.
* The calibrated utility blocks are **specific to the baseline scenario**
  that produced them. If you change the baseline (e.g. different
  ``planning_horizon`` or ``baseline_year``), rerun the baseline solve so
  the values and blocks are regenerated.

Where to go from here
---------------------

You have now solved two small scenario sets, inspected the output files, and
built a handful of comparisons by hand. Some natural next steps:

* **Scale up the GHG price sweep.** ``config/sensitivity.yaml`` and
  ``config/ghg_yll_grid.yaml`` do the same thing at full resolution, with
  log-spaced GHG prices generated programmatically via the
  :doc:`scenario generator DSL <configuration>`.
* **Turn on health costs.** :doc:`health` describes the Global Burden of
  Disease integration and how ``health.value_per_yll`` prices diet-related
  disease burden alongside the environmental objectives.
* **Perform a global sensitivity analysis.** :doc:`sensitivity_analysis`
  describes the polynomial-chaos and random-forest surrogate workflows used
  for Sobol-index decomposition.
* **Learn the rule graph.** :doc:`workflow` documents every rule in the
  pipeline; :doc:`results` and :doc:`analysis` document every output file
  and column.
