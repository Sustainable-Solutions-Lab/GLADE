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

.. _calibration-enabled-generate-pattern:

.. note::

   **Canonical configuration pattern.** Every calibration section has two
   flags: ``enabled`` controls whether the calibration is *applied* at
   solve/build time, and ``generate`` controls whether the workflow
   *produces* the calibration file from a source scenario. The canonical
   pattern for generation is ``enabled: false, generate: true`` so that
   ``enabled`` is the single source of truth at runtime. The alternative
   (``enabled: true, generate: true``) is rejected by
   ``workflow/validation/calibration.py`` to keep configurations
   unambiguous. The same validator also checks that the named source
   ``scenario`` is defined under ``config["scenarios"]`` when
   ``generate: true``, and that referenced calibration files exist on
   disk when ``enabled: true, generate: false``.

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
       ``exogenous_feed.csv``
     - Per-country corrections that balance ruminant-forage and
       monogastric/ruminant-protein and ruminant-roughage supply against the GLEAM3-derived
       baseline demand.
   * - :ref:`food_waste <food-waste-calibration>`
     - ``config/calibration/food_waste.yaml``
     - ``food_waste.yaml``
     - Per-food-group multiplier on the consumer-side waste fraction
       that absorbs systematic food-bus surpluses/shortages relative to
       the GDD-IA intake baseline.
   * - :ref:`food_demand <food-demand-calibration>`
     - ``config/calibration/food_demand.yaml``
     - ``food_demand.csv``
     - Per-food global multiplier on the baseline-diet ``target_mt``
       that closes the residual per-food gap between FAOSTAT QCL supply
       and GDD-IA demand left over after the food-waste step.
   * - :ref:`cost <cost-calibration>`
     - ``config/calibration/cost.yaml``
     - ``crop_cost.csv``,
       ``grassland_cost.csv``,
       ``animal_cost.csv``
     - Additive production-cost corrections derived from stability-
       constraint duals (observed allocation -> optimal allocation).
   * - :ref:`stability <prod-stability-calibration>`
     - ``config/calibration/stability.yaml``
     - ``deviation_penalty.yaml``
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
#. :ref:`food_demand <food-demand-calibration>` — uses the calibrated
   food-waste fractions so that any per-food gap that remains reflects
   a genuine supply / demand mismatch (FAOSTAT QCL vs GDD-IA) and not
   a waste mis-attribution.
#. :ref:`cost <cost-calibration>` — the cost-calibration solve uses
   the calibrated feed, waste, and demand behaviour to extract duals
   that make economic sense. Without the food-demand step, residual
   per-food mismatch leaks into the cost-calibration duals as spurious
   sign (e.g. olive-oil cost driven negative, coffee/cocoa pegged at
   the slack ceiling) and inflates the stability L1 cost downstream.
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
   tools/calibrate food_demand
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
* ``exogenous_feed_calibration.enabled: true`` loads
  ``exogenous_feed.csv`` and injects free per-country generators on
  the monogastric/ruminant protein and ruminant-roughage feed buses (see
  :ref:`exogenous-protein-feed`).
* ``food_demand_calibration.enabled: true`` loads
  ``data/curated/calibration/food_demand.csv`` at solve time and applies
  each per-food multiplier uniformly to the baseline-diet ``target_mt``
  in ``_match_baseline_to_consume_links`` (see
  :ref:`food-demand-calibration`).
* ``cost_calibration.enabled: true`` loads the three cost-correction
  CSVs at build time (see :ref:`cost-calibration-correction`).
* ``deviation_penalty.calibration.enabled: true`` resolves the sentinel
  ``"calibrated"`` on any of
  ``deviation_penalty.{land.crops,land.grassland,feed,diet}.l1_cost`` from
  ``data/curated/calibration/deviation_penalty.yaml`` at solve time
  (see :ref:`production-stability-bounds` for the config reference).
  Scenarios that want an explicit numeric value simply override the
  sentinel with a number; scenarios that want to scan around the
  calibrated value can leave the sentinel in place and set the
  matching ``l1_cost_factor``.

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
  ``data/curated/calibration/exogenous_feed.csv``. See
  :ref:`exogenous-protein-feed` for what real-world sources it stands
  in for.

Both rules read the same solved validation network. The relevant
Snakemake rules are ``compute_grassland_calibration`` (forage) and
``compute_exogenous_feed_calibration`` (protein and roughage), both in
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

.. _food-demand-calibration:

Food-demand calibration
-----------------------

Even after the food-waste step closes the **group-level** gap between
FAOSTAT-derived supply and GDD-IA-derived intake, per-food residuals
remain: foods within a group can be jointly consistent at the group
total while individual foods are systematically over- or
under-demanded. The food-demand calibration absorbs this residual into
a per-food global multiplier on the baseline-diet ``target_mt``.

The multiplier is derived from the global food-bus balance reported by
an uncalibrated validation-mode solve (``scenario: uncalibrated`` in
``config/calibration/food_demand.yaml``, with
``food_demand_calibration.enabled: false`` and ``generate: true`` so
the solve does not read the calibration file it is about to write,
following the
:ref:`canonical pattern <calibration-enabled-generate-pattern>`):

.. math::

   m_f = \mathrm{clip}\!\left(
       \frac{C_f}{C_f + N_f},\ [m_{\min},\ m_{\max}]
   \right)

where :math:`C_f = \sum_c p^0_{\ell_{c,f}}` is total food-consumption
flow for food :math:`f` and
:math:`N_f = \mathrm{slack}^{+}_{f} - \mathrm{slack}^{-}_{f}` is the
net food-bus slack on the two ``slack_positive_food`` /
``slack_negative_food`` generators (positive slack = LP had to invoke a
shortage filler, so demand was too high and the multiplier shrinks;
negative slack = LP absorbed excess, so demand was too low and the
multiplier grows). Both quantities are summed over countries for each
food. The clip bounds (``min_multiplier`` / ``max_multiplier`` in the
config) default to ``[0.5, 2.0]``; they are tight on purpose so that an
out-of-range value flags a structural data issue rather than being
silently absorbed.

At solve time ``_match_baseline_to_consume_links`` (in
``workflow/scripts/solve_model/core.py``) applies the multiplier
uniformly across all countries for each food when
``food_demand_calibration.enabled`` is true.

Rule: ``compute_food_demand_calibration`` in
``workflow/rules/diet.smk``. Script:
``workflow/scripts/compute_food_demand_calibration.py``.

.. _cost-calibration:

Cost calibration
----------------

The cost calibration is a two-step paired solve:

**Step 1 (consumer-value extraction).** A baseline solve with
``enforce_baseline_diet: true`` extracts food-bus duals to build the
piecewise consumer-utility blocks used downstream. Step 1 enables hard
production-stability bounds at **+/-20 %** for crops, grassland, and
animals; this prevents the LP from idealising supply patterns and
pushing the consumer-value duals below realistic supply cost, which in
earlier versions forced step 2 to absorb a large negative correction.
The +/-20 % band is loose enough to accommodate the structural
FAOSTAT-vs-FBS mismatch carried by most foods. For the small set of
foods whose mismatch still exceeds the band (buckwheat, plantain,
coffee, tea, olive-oil), a file-level
``validation.slack_marginal_cost: 5.0`` ($5 000/t) override caps the
slack-driven duals at the upper end of realistic wholesale prices --
instead of the default ~$50 000/t slack ceiling -- while leaving
enough headroom for legitimately high-value foods.

**Step 2 (cost-correction extraction).** A second solve activates the
piecewise utility built in step 1 and tightens production stability to
**+/-1 %**. The dual :math:`\mu^+_\ell - \mu^-_\ell` on each tight
production-stability constraint indicates how much the link's marginal
cost would need to shift for the observed allocation to be
cost-optimal; the per-group median becomes an additive correction. See
:ref:`cost-calibration-correction` for how the corrections are applied
at build time.

Rule: ``extract_cost_calibration`` in ``workflow/rules/crops.smk``.
Script: ``workflow/scripts/extract_cost_calibration.py``. The two
scenarios (``baseline`` and ``calibration``) live in
``config/calibration/cost.yaml``.

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
< 0.02`, i.e. all calibrated deviations within +/-2 % of the target.
The calibrated coefficients are written to
``data/curated/calibration/deviation_penalty.yaml`` under
``l1_costs.<component>`` and resolved at solve time wherever the
sentinel ``"calibrated"`` appears in
``deviation_penalty.{land.crops,land.grassland,feed,diet}.l1_cost`` (see
:ref:`production-stability-bounds`).

A per-iteration diagnostic CSV is written to
``results/{name}/calibration/deviation_penalty_trace.csv`` with the
per-component iterate, achieved deviations, and residual norm for each
step.

The set of components driven simultaneously is configured via
``deviation_penalty.calibration.components`` (default
``[cropland, grassland, feed]``). Diet calibration is available as an
opt-in ``components: [cropland, grassland, feed, diet]`` profile for
specific investigations
where the priced optimum would otherwise reshuffle the diet
substantially.

Implementation
~~~~~~~~~~~~~~

Rule: ``calibrate_deviation_penalty`` in
``workflow/rules/deviation_penalty.smk``. Script:
``workflow/scripts/calibrate_deviation_penalty.py``.

The calibrated L1 centre also defines the reference regime for the GSA
scenario groups (``gsa``, ``gsa-l1-low``, ``gsa-l1-high``); see
:ref:`sensitivity-prod-stability-cost`.

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
