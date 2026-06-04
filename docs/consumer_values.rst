.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _consumer-values:

Consumer Values
===============

Overview
--------

Many of the most interesting questions for ``GLADE`` ask how diets *respond*
to environmental or health pricing — for example, "what would people eat under
a $50/tCO\ :sub:`2`-eq price?". Naively letting the optimizer choose the
cheapest macronutrient-and-food-group-feasible diet gives implausible answers:
the unconstrained model will gladly replace half of today's food consumption
with whatever is cheapest to grow. We need a way to bake **revealed consumer
preferences** into the objective.

This page documents how the model derives those preferences from a baseline
solve and feeds them back as a piecewise utility curve in subsequent solves.
The full workflow has three steps:

1. Solve a **baseline scenario** with consumption fixed to the observed diet
   via ``validation.enforce_baseline_diet: true`` (see :doc:`current_diets`).
2. **Extract consumer values** as the dual variables of the per-(food, country)
   consumption equality constraints in that solve.
3. **Calibrate piecewise utility blocks** centred on baseline consumption,
   using the extracted duals as marginal utilities at the baseline quantity.

In subsequent scenarios the diet is freed (``enforce_baseline_diet: false``)
and the calibrated blocks are applied via ``food_utility_piecewise.enabled:
true``. Consumption then deviates from baseline only when the GHG/health
savings outweigh the consumer-value cost of the deviation.

Tutorial Part 2 (:doc:`tutorial`) walks through this workflow end-to-end with
a small config; the present page focuses on the *interpretation* of the
extracted values and on the model preconditions that make them meaningful.

What the duals encode
---------------------

When ``enforce_baseline_diet`` is on, every food consumption link gets an
equality constraint :math:`p = p_{\mathrm{set}}` pinning consumption to the
processed baseline diet (see :ref:`baseline-diet-estimation`). The dual
variable :math:`\mu_{\text{p\_set}}` of this equality is the marginal change
in objective per unit of relaxed consumption:

.. math::

   \mu_{\text{p\_set}}(f, c)
     = \frac{\partial (\text{total cost})}{\partial p_{\text{set}}(f, c)}.

Sign convention in ``extract_consumer_values.py``:
``value_bnusd_per_mt = -mu_p_set`` so that positive values mean *consumption
is valuable to the consumer* (the model would pay this much per Mt to be
allowed to consume more).

Read carefully, a positive dual is **not** a "preference" in the everyday
sense — it is whatever marginal cost the supply chain has to incur to deliver
the next Mt of that food. It bundles together land, water, fertilizer,
processing, trade, and emissions costs net of any byproduct value. The
calibration relies on the assumption that *at the baseline quantity*, this
marginal supply cost is a reasonable proxy for the consumer's willingness
to pay — i.e. that observed consumption is approximately at the equilibrium
of supply cost and demand value. That is a common revealed-preference
assumption in food-system modelling.

**Negative duals are floored at zero.** A negative ``mu_p_set`` would mean
the consumer pays the model to take more of the food, which is semantically
backwards as a preference signal — it always indicates a supply-side
artifact (e.g. forced co-product disposal, L1 production-stability dragging
production toward baseline through binding caps elsewhere). The extractor
floors these at zero so downstream consumers see consistent non-negative
values. The clipped count and the most-negative foods are logged for
traceability; the *Preconditions* section below catalogs the structural
issues that produce them.

Visualisation
-------------

The figure below shows the per-(food, country) consumer values from the
documentation baseline solve, ordered by food group. Each row is one food;
the boxen shows the spread across countries. Colours follow the food-group
palette used elsewhere in the documentation.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/consumer_values_distribution.png
   :width: 100%
   :alt: Letter-value plot of consumer values per food, coloured by food group

   Distribution of consumer values (USD\ :sub:`2024` per kg) across 175
   countries for each modelled food, derived from the dual variables of the
   per-(food, country) consumption equalities in the documentation baseline
   solve. Foods are ordered by food group (group label on the right margin)
   and within each group by within-group median value. The x-axis is symlog
   with a linear region near zero; the vertical line at zero separates foods
   the consumer would pay to consume more of (right) from foods that cost
   nothing or less to consume more of (left).

A few patterns are worth flagging.

- **Animal products** (red meat, processed meat, dairy, eggs, poultry) sit
  at the high end. This is consistent with their high supply cost — they
  consume large amounts of crop and grassland feed, land, and emit substantial
  GHGs.
- **Cereals and starchy vegetables** sit in the low-positive range. They are
  cheap calorie sources at baseline.
- **A few oils and seeds** cluster near zero. These come from co-products
  of larger commodity flows (e.g. coconut oil and meal from copra-based
  coconut production), and their marginal cost is dominated by the
  byproduct-value side of the balance sheet. The extractor floors these
  at zero (see above), so any food whose raw dual was negative shows as
  zero in the figure.

Preconditions for sensible duals
--------------------------------

Three model details are easy to overlook and each can pollute the extracted
duals if it goes wrong. They are the reason the documentation baseline
configuration looks the way it does (see ``docs/config/doc_figures.yaml``'s
``baseline`` scenario).

1. **The fixed diet must be supplyable.** If the model cannot deliver the
   baseline consumption of food *f* in country *c* through real production
   pathways, the food consumption equality is closed by **food slack** at
   ``validation.slack_marginal_cost`` (default 50 bn USD/Mt). The dual then
   saturates at exactly that price with the wrong sign — it reflects the
   slack penalty rather than any consumer preference.

   This is most likely to happen for foods that are forced co-products of
   commodity demands the model represents only partially. Cottonseed oil is
   the textbook case: cotton is grown for fiber demand (``enforce_fiber_demand``),
   the ginning pathway has fixed coefficients (cotton-lint 0.38, cottonseed
   oil 0.083, oilseed meal 0.275), and at the global fiber-demand level the
   joint cottonseed oil output exceeds baseline-diet absorption. Without an
   outlet the surplus exits via food slack and the cottonseed-oil dual
   saturates at −50 USD/kg in every country.

   The mitigation is to give surplus a route to the energy sector via
   ``biomass.disposal_foods`` (see :ref:`disposal foods <biomass-disposal-foods>`). Foods
   currently on this list — cottonseed oil, the sesame and groundnut oils
   and seeds, coconut and coconut oil, foxtail millet — were each identified
   from a baseline solve where their dual sat at the slack price or had a
   strongly negative median across countries.

2. **The L1 deviation penalty pulls in the same direction.** When
   ``deviation_penalty`` is enabled with ``penalty_mode: "l1"``
   (typical for the central and GSA configurations), the objective gains a
   term :math:`l_1 \cdot \sum |a - a_{\mathrm{baseline}}|` on harvested area
   per crop. If the modelled outlets for some crop's production cannot
   absorb its baseline area, the L1 term drags the corresponding food
   consumption duals **negative** — relaxing the consumption equality lets
   the model grow more of the upstream crop and reduces L1 deviation,
   so the marginal value of consumption is *negative* (the consumer would
   "save" the L1 penalty per extra Mt consumed).

   Empirically this affected sesame, groundnut, coconut, foxtail-millet,
   chickpea and gram in earlier baseline solves: each was under-produced
   relative to its baseline area by 1–6 Mha. The fix is the same as in
   point 1 — provide a missing real-world outlet (biomass disposal,
   feed routing, or both).

3. **Redundant constraints can leak into duals.** If the diet is enforced
   per-food via ``enforce_baseline_diet`` *and* within-group ratios are
   simultaneously enforced via ``food_groups.fix_within_group_ratios``, the
   second set of constraints is mathematically redundant (the per-food
   p_set already implies the within-group shares) but can split the
   marginal value across the two constraint families in unpredictable ways.
   Keep ``fix_within_group_ratios.enabled: false`` whenever
   ``enforce_baseline_diet`` is on. The same goes for any additional
   constraint that further pins what is already pinned.

Interpreting disposal flows as a residual diagnostic
----------------------------------------------------

A useful side benefit of the disposal-route mechanism is that the *amount*
routed to biomass in a baseline solve is the gap between baseline production
and what the modelled diet absorbs. Reading these flows answers "what real
demand am I missing for this food?":

- Cottonseed oil: ~1.8 Mt globally, all in cotton-fiber-producing countries —
  the forced co-product story.
- Foxtail-millet: ~2.8 Mt — birdseed and forage demand outside of the East
  and South Asian food markets.
- Coconut oil: ~3 Mt — coir/charcoal/husk uses are the missing demand;
  the L1 baseline is calibrated against total coconut area but the modelled
  outlets are only food and oil.
- Sesame oil and groundnut oil: ~0.8 and ~0–4 Mt respectively — partly post-
  harvest losses beyond food-group waste factors, partly under-attribution
  of these oils in the FBS-derived diet.

Where these flows are large or geographically concentrated, they point to
specific model improvements: an explicit non-food demand term (analogous to
``fiber_demand`` for cotton), a finer split between competing pathways, or
revised loss/waste factors. Until those are in place the disposal route is
the pragmatic choice — it lets the L1 baseline reflect total observed area
without poisoning the consumer-value duals.

How the calibrated blocks use the duals
---------------------------------------

The ``calibrate_food_utility_blocks`` rule reads ``values.csv`` together with
the per-(food, country) baseline consumption levels and emits a piecewise
diminishing-marginal-utility curve per (food, country). The block containing
baseline consumption uses the extracted dual as its marginal utility; blocks
below baseline are more valuable (decline_factor < 1) and blocks above
baseline are less valuable, all parameterised by:

- ``food_utility_piecewise.n_blocks`` — number of steps per side
  (default: 4).
- ``food_utility_piecewise.decline_factor`` — geometric ratio between
  successive block values (default: 0.7, i.e. each step is worth 70% of the
  previous one).
- ``food_utility_piecewise.total_width_multiplier`` — total width of the
  curve relative to baseline (default: 2.0, so the curve spans 0 to 2×
  baseline).

See :doc:`configuration` and :doc:`tutorial` for the full configuration
reference and a worked example.

Workflow Integration
--------------------

**Snakemake rules**:
  * ``extract_consumer_values`` — produces
    ``<results>/{name}/consumer_values/{baseline}/values.csv``
  * ``calibrate_food_utility_blocks`` — produces
    ``<results>/{name}/consumer_values/{baseline}/utility_blocks.csv``
  * ``plot_consumer_values_comparison`` — produces consumption,
    objective and consumer-value comparison figures

**Inputs**:
  * Solved baseline network with ``mu_p_set`` duals on food consumption links.

**Configuration parameters**:
  * ``consumer_values.baseline_scenario`` — name of the scenario whose
    duals are extracted (default: ``"baseline"``).
  * ``food_utility_piecewise.enabled`` — set ``true`` for scenarios that
    should respond to consumer values; **must** be ``false`` in any
    scenario that also sets ``enforce_baseline_diet`` (the validation
    layer rejects the combination).
  * ``food_utility_piecewise.n_blocks``, ``decline_factor``,
    ``total_width_multiplier`` — block geometry described above.

**Output schema** (``values.csv``):
  ``food, food_group, country, value_bnusd_per_mt, adjustment_bnusd_per_mt``.
  ``value_bnusd_per_mt`` and ``adjustment_bnusd_per_mt`` differ only in sign
  (the latter is what gets added to the marginal cost of the consumption
  link in subsequent solves).
