.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _calibration:

Calibration
===========

The default workflow relies on three separate calibrations that each
transform outputs of a dedicated solve into a git-tracked input consumed
by every subsequent solve. Running ``tools/calibrate`` regenerates all
three in the right dependency order; individual steps can also be run
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
   * - ``grassland``
     - ``config/calibration/grassland.yaml``
     - ``grassland_yield.csv``,
       ``fodder_conversion.csv``,
       ``exogenous_forage.csv``
     - Per-country corrections that balance grassland / fodder supply
       against ruminant-forage demand.
   * - ``cost``
     - ``config/calibration/cost.yaml``
     - ``crop_cost.csv``,
       ``grassland_cost.csv``,
       ``animal_cost.csv``
     - Additive production-cost corrections derived from stability-
       constraint duals (observed allocation → optimal allocation).
   * - ``stability``
     - ``config/calibration/stability.yaml``
     - ``prod_stability_l1.yaml``
     - The L1 penalty pair :math:`(\ell^c_1, \ell^a_1)` that brings both
       land-use and animal-feed deviations to ~5 % of observed totals.

Dependency order
----------------

When upstream data or build logic changes, rerun in this order:

#. **grassland** — other calibrations solve against a model that already
   has the grassland corrections applied.
#. **cost** — the cost-calibration solve uses the calibrated grassland
   behaviour to extract duals that make economic sense.
#. **stability** — the L1 grid sweep uses both previous corrections so
   that the observed 5 % contours reflect the fully-calibrated baseline.

Running the calibrations
------------------------

Everything is wrapped by ``tools/calibrate``:

.. code-block:: bash

   tools/calibrate              # all three, in dependency order
   tools/calibrate grassland    # one step
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

All three outputs are consumed automatically by the default workflow
when their configuration blocks are enabled (the default):

* ``grazing.grassland_forage_calibration.enabled: true`` loads the three
  grassland CSVs at solve time.
* ``cost_calibration.enabled: true`` loads the three cost-correction
  CSVs at build time.
* ``prod_stability_calibration.enabled: true`` resolves the sentinel
  ``"calibrated"`` in
  ``validation.production_stability.land_l1_cost`` and
  ``.animal_feed_l1_cost`` from
  ``data/curated/calibration/prod_stability_l1.yaml`` at solve time.
  Scenarios that want an explicit numeric value simply override the
  sentinel with a number.

Grassland calibration
---------------------

See :ref:`grassland-forage-calibration` in the livestock chapter for the
algorithm. The relevant rule is ``compute_grassland_calibration`` in
``workflow/rules/animals.smk``; ``generate: true`` lives in
``config/calibration/grassland.yaml`` and is ``false`` everywhere else,
which breaks the otherwise circular dependency.

Cost calibration
----------------

Derived from the dual variables on hard production-stability constraints
(±1 %). When the model is forced to reproduce observed production
levels, :math:`\mu^+_\ell - \mu^-_\ell` on each constraint indicates how
much the link's marginal cost would need to shift for the observed
allocation to be cost-optimal. The per-group median becomes an additive
correction. See :doc:`costs` for how the corrections are applied at
build time.

Rule: ``extract_cost_calibration`` in ``workflow/rules/crops.smk``.
Script: ``workflow/scripts/extract_cost_calibration.py``.

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
analysis) become uninterpretable.

The model therefore adds a **production-stability penalty** that
discourages departures from the observed-year baseline. Every crop,
grassland and animal-feed production link :math:`\ell` carries a linear
:math:`L_1` term in the objective,

.. math::

   \sum_{\ell \in \text{crop,grass}} \ell^c_1 \cdot
   |x_\ell - \bar x_\ell|
   \;+\;
   \sum_{\ell \in \text{animal}} \ell^a_1 \cdot
   |x_\ell - \bar x_\ell|,

where :math:`\bar x_\ell` is the baseline activity of the link (area in
Mha for crops / grassland, feed use in Mt DM for animals) and
:math:`\ell^c_1`, :math:`\ell^a_1` are the two penalty coefficients
calibrated here. The :math:`L_1` form is deliberate: it is piecewise
linear (so the LP stays an LP), and it produces *sparse* deviations —
links either match the baseline exactly or pay a proportional cost to
move.

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
   ``.animal_feed_l1_cost``.

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
``workflow/scripts/compute_prod_stability_calibration.py``. Diagnostic
heatmaps live in ``notebooks/prod_stability_calibration.ipynb``; the
notebook is no longer part of the workflow but is useful for visual
sanity-checking of the grid after a resolve.

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
