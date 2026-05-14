.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _calibration:

Calibration
===========

The default workflow relies on several separate calibrations that each
transform outputs of a dedicated solve into a git-tracked input consumed
by every subsequent solve. Running ``tools/calibrate`` regenerates them
all in the right dependency order; individual steps can also be run
directly.

The calibration artefacts live under ``data/curated/calibration/`` and
are version-controlled so that ordinary builds don't need to re-solve
anything.

.. list-table::
   :header-rows: 1
   :widths: 18 22 30 30

   * - Step
     - Config
     - Produces
     - Purpose
   * - :ref:`feed <calibration-feed-step>`
     - ``config/calibration/feed.yaml``
     - ``grassland_yield.csv``,
       ``fodder_conversion.csv``,
       ``exogenous_forage.csv``,
       ``exogenous_protein.csv``
     - Per-country corrections that balance ruminant-forage and
       monogastric/ruminant-protein supply against the GLEAM3-derived
       baseline demand.
   * - :ref:`food_waste <food-waste-calibration>`
     - ``config/calibration/food_waste.yaml``
     - ``food_waste.yaml``
     - Per-food-group multiplier on the consumer-side waste fraction
       that absorbs systematic food-bus surpluses/shortages relative to
       the GDD-IA intake baseline.
   * - :ref:`cost <cost-calibration>`
     - ``config/calibration/cost.yaml``
     - ``crop_cost.csv``,
       ``grassland_cost.csv``,
       ``animal_cost.csv``
     - Additive production-cost corrections derived from stability-
       constraint duals (observed allocation → optimal allocation).
   * - :ref:`stability <prod-stability-calibration>`
     - ``config/calibration/stability.yaml``
     - ``prod_stability_l1.yaml``
     - The L1 penalty pair :math:`(\ell^c_1, \ell^a_1)` that brings both
       land-use and animal-feed deviations to ~5 % of observed totals.

Dependency order
----------------

When upstream data or build logic changes, rerun in this order:

#. :ref:`feed <calibration-feed-step>` — other calibrations solve against a
   model whose feed slack is already closed by the forage and protein
   corrections.
#. :ref:`food_waste <food-waste-calibration>` — uses the calibrated
   feed behaviour so that food-bus slack is not contaminated by
   feed-side mismatches.
#. :ref:`cost <cost-calibration>` — the cost-calibration solve uses the
   calibrated feed and waste behaviour to extract duals that make
   economic sense.
#. :ref:`stability <prod-stability-calibration>` — the L1 Broyden
   iteration uses all previous corrections so that the observed
   deviations reflect the fully-calibrated baseline.

Running the calibrations
------------------------

Everything is wrapped by ``tools/calibrate``:

.. code-block:: bash

   tools/calibrate              # all, in dependency order
   tools/calibrate feed         # one step (forage + protein feed slack)
   tools/calibrate food_waste
   tools/calibrate cost
   tools/calibrate stability
   tools/calibrate --check      # per-step staleness, no execution

The wrapper invokes ``tools/smk`` with the matching config and the
appropriate output targets. Any extra flags are passed through, e.g.
``tools/calibrate cost -j8 --slurm``.

The stability calibration runs locally in-process and is inherently
sequential (each Broyden step depends on the previous solve), so HPC
offloading isn't worthwhile at this size. Each iteration is one paired
solve (baseline + main), and 3–5 iterations are typically enough.

Consuming the calibrated values
-------------------------------

All calibration outputs are consumed automatically by the default
workflow when their configuration blocks are enabled (the default):

* ``grazing.grassland_forage_calibration.enabled: true`` loads the
  three forage-side CSVs at solve time (see
  :ref:`grassland-forage-calibration`).
* ``feed_protein_calibration.enabled: true`` loads
  ``exogenous_protein.csv`` and injects free per-country generators on
  the monogastric/ruminant protein feed buses (see
  :ref:`exogenous-protein-feed`).
* ``cost_calibration.enabled: true`` loads the three cost-correction
  CSVs at build time (see :ref:`cost-calibration-correction`).
* ``prod_stability_calibration.enabled: true`` resolves the sentinel
  ``"calibrated"`` in
  ``validation.production_stability.land_l1_cost`` and
  ``.animal_feed_l1_cost`` from
  ``data/curated/calibration/prod_stability_l1.yaml`` at solve time
  (see :ref:`production-stability-bounds` for the config reference).
  Scenarios that want an explicit numeric value simply override the
  sentinel with a number.

.. _calibration-feed-step:

Feed calibration
----------------

The feed step generates two parallel sets of corrections from a single
validation solve:

* **Forage corrections** (surplus + deficit on
  ``feed:ruminant_forage:*``). Surplus countries get a per-country
  multiplier on grassland yield and fodder-conversion efficiency;
  deficit countries get an exogenous-forage supply written to
  ``data/curated/calibration/exogenous_forage.csv``. See
  :ref:`grassland-forage-calibration` in the livestock chapter for the
  algorithm.
* **Protein corrections** (deficit side only, on
  ``feed:monogastric_protein:*`` and ``feed:ruminant_protein:*``). The
  positive slack on each protein feed bus is written to
  ``data/curated/calibration/exogenous_protein.csv``. See
  :ref:`exogenous-protein-feed` for what real-world sources it stands
  in for.

Both rules read the same solved validation network. The relevant
Snakemake rules are ``compute_grassland_calibration`` (forage) and
``compute_protein_feed_calibration`` (protein), both in
``workflow/rules/animals.smk``. ``generate: true`` lives in
``config/calibration/feed.yaml`` and is ``false`` everywhere else,
which breaks the otherwise circular dependency.

.. _food-waste-calibration:

Food-waste calibration
----------------------

See the food-loss-and-waste discussion in :doc:`food_processing` for
the underlying SDG 12.3 derivation. Rule:
``compute_food_waste_calibration`` in ``workflow/rules/diet.smk``;
``generate: true`` lives in ``config/calibration/food_waste.yaml``.

.. _cost-calibration:

Cost calibration
----------------

Derived from the dual variables on hard production-stability constraints
(±1 %; see :ref:`production-stability-bounds` for how these bounds are
configured). When the model is forced to reproduce observed production
levels, :math:`\mu^+_\ell - \mu^-_\ell` on each constraint indicates how
much the link's marginal cost would need to shift for the observed
allocation to be cost-optimal. The per-group median becomes an additive
correction. See :ref:`cost-calibration-correction` for how the
corrections are applied at build time.

Rule: ``extract_cost_calibration`` in ``workflow/rules/crops.smk``.
Script: ``workflow/scripts/extract_cost_calibration.py``.

.. _prod-stability-calibration:

Production-stability L1 calibration
-----------------------------------

Motivation
~~~~~~~~~~

A pure cost-minimisation solve of a global food system model is free
to reorganise production arbitrarily: if a country produces wheat more
cheaply than its neighbour, the optimiser will shift the neighbour's
entire wheat output across the border. This is unrealistic — real
production patterns reflect a long tail of frictions (rotations,
contracts, infrastructure, labour, insurance, policy) that the model
does not represent. Without a counterweight, the optimal allocation
diverges sharply from observed production and analyses that build on
top (marginal-cost attribution, counterfactual comparisons, sensitivity
analysis) become different to relate to the current food system.

The model therefore adds a **production-stability penalty** (see
:ref:`production-stability-bounds` for the full configuration reference)
that discourages departures from the observed-year baseline. Every crop,
grassland and animal-feed production link :math:`\ell` carries a linear
:math:`L_1` term in the objective,

.. math::

   \sum_{\ell \in \text{crop,grass}} \ell^c_1 \cdot
   |x_\ell - \bar x_\ell|
   \;+\;
   \sum_{\ell \in \text{animal}} \ell^a_1 \cdot
   |x_\ell - \bar x_\ell|,

where :math:`\bar x_\ell` is the baseline activity of the link (area
in Mha for crops / grassland, feed use in Mt DM for animals) and
:math:`\ell^c_1`, :math:`\ell^a_1` are the two penalty coefficients
calibrated here. The :math:`L_1` form is convenient: it can be
implemented linearly so the LP stays an LP.

Why two coefficients?
~~~~~~~~~~~~~~~~~~~~~

Land activity and animal-feed activity are measured in different units
and have different baseline totals (roughly 4,000 Mha of land vs
6,500 Mt DM of feed). A single shared coefficient would penalise one
axis much more strongly than the other. Splitting the penalty into a
crop/grassland coefficient :math:`\ell^c_1` (bn USD per Mha of
deviation) and an animal-feed coefficient :math:`\ell^a_1` (bn USD per
Mt DM) lets us tune each axis independently.

Calibration target
~~~~~~~~~~~~~~~~~~

We pick :math:`(\ell^c_1, \ell^a_1)` so that the optimal solution
exhibits **~5 %** total deviation on each axis (summed absolute
deviation divided by the baseline total). The 5 % target is a
compromise: large enough that the optimiser can still express
meaningful shifts in response to scenarios (carbon prices, diet
changes, yield shocks), small enough that the resulting production
pattern stays recognisably close to observed production and
interpretation remains tractable.

Formally the calibration solves

.. math::

   \min_{(\ell^c_1, \ell^a_1)} \quad
   \big\lVert \text{land\_dev}(\ell^c_1, \ell^a_1) - 5\,\%\big\rVert,
   \quad
   \big\lVert \text{feed\_dev}(\ell^c_1, \ell^a_1) - 5\,\%\big\rVert.

Broyden iteration
~~~~~~~~~~~~~~~~~

The deviation map

.. math::

   F : (\ell^c_1, \ell^a_1) \;\longmapsto\;
   (\text{land\_dev}, \text{feed\_dev})

is monotone and near-affine in log-log coordinates (raising
:math:`\ell^c_1` mainly tightens land deviation, raising
:math:`\ell^a_1` mainly tightens feed deviation, with small cross
coupling). Calibration is therefore a 2-D root-finding problem,
solved with **Broyden's quasi-Newton method** on
:math:`x = (\log \ell^c_1, \log \ell^a_1)` with residual
:math:`r(x) = (\log(\text{land\_dev}/t),\, \log(\text{feed\_dev}/t))`.
A trust-region cap of :math:`\lvert \Delta x \rvert_\infty \le \log 2`
prevents single-step overshoot near the zero-baseline growth caps.

Each iteration is one paired solve (baseline with
``enforce_baseline_diet=true`` to derive consumer values, then main
with piecewise utility active). Convergence is typically reached in
3–5 iterations from a cold start and 1–2 from a warm start (the
previously calibrated YAML is auto-detected and used as the seed). The
initial Jacobian is :math:`\mathrm{diag}(-1, -1)`, which is the exact
log-log slope for a relationship of the form
:math:`\text{dev} \propto 1/\ell_1`.

Convergence target: :math:`\lvert \log(\text{dev}/t) \rvert_\infty
< 0.02`, i.e. both deviations within ±2 % of the target. The
calibrated pair is written to
``data/curated/calibration/prod_stability_l1.yaml`` and resolved at
solve time wherever the sentinel ``"calibrated"`` appears in
``validation.production_stability.land_l1_cost`` or
``.animal_feed_l1_cost`` (see :ref:`production-stability-bounds`).

A per-iteration diagnostic CSV is written to
``results/{name}/calibration/prod_stability_trace.csv`` with the
:math:`(\ell^c_1, \ell^a_1)` iterate, achieved deviations, and residual
norm for each step.

Implementation
~~~~~~~~~~~~~~

Rule: ``calibrate_prod_stability`` in
``workflow/rules/prod_stability.smk``. Script:
``workflow/scripts/calibrate_prod_stability.py``.

The calibrated L1 cost is also used as a slice parameter in the
sensitivity analysis; see :ref:`sensitivity-prod-stability-cost` for the
range, distribution, and rationale.

Staleness detection
-------------------

``tools/smk`` prints a one-line reminder when any file under
``data/curated/`` (excluding ``data/curated/calibration/`` itself) is
newer than the oldest file in ``data/curated/calibration/``. This is a
cheap mtime heuristic and may produce false positives after
``git pull`` (because checkout touches mtimes); for the authoritative
answer run

.. code-block:: bash

   tools/calibrate --check

which performs a Snakemake dry-run against each calibration target and
reports ``[up-to-date]`` / ``[STALE]`` per step.

Set ``SMK_SKIP_CALIBRATION_HINT=1`` to silence the reminder in scripted
contexts.
