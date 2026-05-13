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
#. :ref:`stability <prod-stability-calibration>` — the L1 grid sweep
   uses all previous corrections so that the observed 5 % contours
   reflect the fully-calibrated baseline.

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

Solves for the stability step can be offloaded to the HPC cluster via
``remote_solve`` (see :doc:`cluster_execution`); the 9 grid points
(plus 9 matching baselines) each take several minutes with Gurobi.

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

Grid sweep and intersection
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``config/calibration/stability.yaml`` defines a narrow 3 × 3 log-spaced
grid that brackets the known intersection (roughly
:math:`\ell^c_1 \approx 0.1`, :math:`\ell^a_1 \approx 0.033`) with half
a decade of cushion on each side; each grid point is solved against a
matching baseline scenario for the piecewise consumer-value blocks.
If an upstream data update pushes the intersection outside the grid,
``compute_prod_stability_calibration`` fails with an error pointing at
``config/calibration/stability.yaml`` so the bounds can be widened or
shifted. Given a valid grid the script

#. interpolates, row-by-row, the animal_cost at which **feed**
   deviation crosses 5 % (a 1-D curve in the plane), and analogously
   the crop_cost at which **land** deviation crosses 5 % column-by-
   column,
#. iterates
   :math:`\ell^c_1 \mapsto \ell^a_{1,\text{feed=5\%}}(\ell^c_1)`
   and
   :math:`\ell^a_1 \mapsto \ell^c_{1,\text{land=5\%}}(\ell^a_1)`
   to a fixed point — the unique :math:`(\ell^c_1, \ell^a_1)` pair
   that hits 5 % on both axes simultaneously,
#. writes the exact intersection (no rounding) to
   ``data/curated/calibration/prod_stability_l1.yaml``. It is resolved
   at solve time wherever the sentinel ``"calibrated"`` appears in
   ``validation.production_stability.land_l1_cost`` or
   ``.animal_feed_l1_cost`` (see :ref:`production-stability-bounds`).

The figure below illustrates the calibration geometry using
representative values from an actual sweep.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/prod_stability_calibration.png
   :width: 100%
   :alt: Production-stability L1 calibration: land-use deviation, feed deviation, and their 5% contours

   Production-stability L1 calibration on the
   :math:`(\ell^c_1, \ell^a_1)` plane. *Left:* total land-use
   deviation (crop + grassland) from baseline, as a percentage of the
   baseline land total. The 5 % contour is essentially flat in
   :math:`\ell^a_1` — raising the animal-feed penalty barely changes
   land-use deviation once the crop penalty is past the knee.
   *Middle:* animal-feed deviation, which is driven almost entirely
   by :math:`\ell^a_1`; its 5 % contour is essentially flat in
   :math:`\ell^c_1`. *Right:* the two 5 % contours overlaid; their
   intersection (★) is the calibrated pair at which *both* deviations
   equal 5 %. The near-orthogonality of the two contours is precisely
   why the fixed-point iteration converges in a handful of steps.

Implementation
~~~~~~~~~~~~~~

Rule: ``compute_prod_stability_calibration`` in
``workflow/rules/animals.smk``. Script:
``workflow/scripts/compute_prod_stability_calibration.py``.

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
