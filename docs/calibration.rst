.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _calibration:

Calibration
===========

.. contents::
   :local:
   :depth: 2

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

Solves for the stability step are typically offloaded to the HPC cluster
via ``remote_solve`` (see :doc:`cluster_execution`); the 25 grid points
each take several minutes with Gurobi.

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

The model uses an :math:`L_1` penalty on per-link deviation from
baseline production (both land-use and animal-feed). The penalty
coefficients are calibrated so the optimal solution exhibits ~5 %
deviation on each axis:

.. math::

   \min_{(\ell^c_1, \ell^a_1)} \quad
   \big\lVert \text{land\_dev}(\ell^c_1, \ell^a_1) - 5\,\%\big\rVert,
   \quad
   \big\lVert \text{feed\_dev}(\ell^c_1, \ell^a_1) - 5\,\%\big\rVert.

``config/calibration/stability.yaml`` defines a 5 × 5 log-spaced grid on
:math:`10^{-2} \ldots 10^0` for each coefficient; each grid point is
solved with a matching baseline scenario for the piecewise consumer-
value blocks. ``compute_prod_stability_calibration`` computes the 5 %
contour in each dimension (log-linear interpolation) and returns their
intersection via a fixed-point iteration on
:math:`\ell^c_1 \mapsto \ell^a_{1,\text{feed=5\%}}(\ell^c_1)`
and :math:`\ell^a_1 \mapsto \ell^c_{1,\text{land=5\%}}(\ell^a_1)`.
No rounding is applied; the exact intersection is written to
``prod_stability_l1.yaml`` and resolved at solve time whenever the
sentinel ``"calibrated"`` is present.

Rule: ``compute_prod_stability_calibration`` in
``workflow/rules/animals.smk``. Script:
``workflow/scripts/compute_prod_stability_calibration.py``. Diagnostic
heatmaps live in ``notebooks/prod_stability_calibration.ipynb``; the
notebook is no longer part of the workflow but is useful for visual
sanity-checking of the grid.

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
