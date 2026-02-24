.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

.. _validation:

Validation
==========

Model validation fixes food production and demand to observed 2020 values in
order to uncover potential data inconsistencies and verify that emissions
calculations produce plausible results. By constraining both supply and demand
to match reality, any remaining imbalances — surfaced via *slack variables* —
reveal where the model's input datasets, processing assumptions, or structural
simplifications fail to reproduce observed commodity flows.

This page presents results from the validation configuration and explains how
to interpret the diagnostic figures. For configuration reference, see
:ref:`validation-config`; for details on the slack mechanism, see
:ref:`validation-slack-mechanism`.


.. _validation-config:

Validation Configuration
------------------------

The validation configuration (``config/validation.yaml``) pins the model to
the 2020 baseline by enabling several flags:

.. code-block:: yaml

   planning_horizon: 2020

   validation:
     use_actual_yields: true          # Use observed yields instead of potential
     use_actual_production: true      # Fix harvested areas to observed values
     enforce_baseline_diet: true      # Fix consumption to baseline diet
     enforce_baseline_feed: true      # Fix animal feed use to GLEAM baseline
     disable_spared_cropland: true    # No cropland retirement
     disable_spared_grassland: true   # No grassland retirement
     slack_marginal_cost: 10          # bn USD per Mt/Mha slack penalty
     grassland_yield_multiplier: 1.2  # Calibration knob for grassland yields

These settings collectively remove the optimizer's degrees of freedom:

- **Supply side**: ``use_actual_yields`` swaps GAEZ potential yield rasters for
  observed yields (see :doc:`crop_production`), while ``use_actual_production``
  fixes harvested areas to present-day values. Grassland production is similarly
  pinned (see :doc:`livestock`).
- **Demand side**: ``enforce_baseline_diet`` adds per-food, per-country equality
  constraints that force consumption to match the processed GDD baseline diet
  (see :doc:`current_diets`). ``enforce_baseline_feed`` pins animal feed use to
  GLEAM-derived baseline levels (see :ref:`gleam-feed-baseline`).
- **Land use**: Sparing of existing cropland and grassland is disabled so the
  model matches the historical land footprint (see :doc:`land_use`).
- **Calibration multiplier**: ``grassland_yield_multiplier`` applies a small
  adjustment to grassland feed yields to compensate for known data gaps.

Additional settings select present-day water availability
(``water.supply_scenario: current_use``) and disable health impacts, since the
goal is physical mass balance rather than optimization.


.. _validation-slack-mechanism:

Slack Mechanism
---------------

When production and demand are fully fixed, the model may be unable to balance
commodity flows exactly. *Slack generators* allow small violations of
constraints at a configurable penalty cost (``slack_marginal_cost``), so the
solver always finds a feasible solution. The magnitude of slack in each
category reveals where data inconsistencies exist:

- **Food slack**: Difference between baseline demand and what the supply chain
  can deliver. Large food slack indicates missing processing pathways, incorrect
  yields, or data coverage gaps.
- **Feed slack**: Imbalance between the feed requirements of livestock and the
  available feed supply from crops, residues, and grassland.
- **Land slack**: Cases where fixed harvested areas exceed the available land
  endowment in a region.
- **Water slack**: Irrigation demand exceeding available water supply.

See :doc:`configuration` for full details on the validation configuration keys.


.. _validation-crop-production:

Crop Production
---------------

The crop production map shows the dominant crop group and land-use intensity
when production is fixed to observed 2020 values. Pasture is excluded from
this map to prevent it from dominating the visualization; it is shown
separately below.

.. _fig-validation-crop-production:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_crop_production.png
   :width: 100%
   :alt: Map of dominant crop group and land-use intensity under validation

   Dominant crop group and cropland utilization intensity under the validation
   configuration, with production fixed to observed 2020 values. Colour
   indicates the crop group with the largest area in each grid cell; alpha
   encodes utilization of potential cropland. Pasture/grassland is excluded
   and shown separately in :ref:`the pasture map below <fig-validation-pasture>`.


.. _validation-pasture:

Pasture
~~~~~~~

Grassland and pasture production typically accounts for the largest share of
agricultural land globally. The map below shows pasture utilization intensity
in isolation.

.. _fig-validation-pasture:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_pasture.png
   :width: 100%
   :alt: Map of pasture utilization intensity under validation

   Pasture utilization intensity under the validation configuration. Green
   intensity encodes the fraction of available pasture land that is used for
   grazing. High-intensity regions correspond to areas with dense livestock
   production (see :doc:`livestock`).


.. _validation-food-slack:

Food Group Slack
----------------

The food group slack plot shows the difference between baseline consumption
targets and what the model's supply chain can actually deliver when production
is fixed. Large deviations indicate data inconsistencies — for example,
missing food processing pathways, incorrect yield data, or gaps in trade
network coverage.

The figure has two panels:

- **Top panel**: Absolute slack in megatonnes. Bars above zero indicate
  *excess* (the model produces more than the baseline demands); bars below
  zero indicate *shortage* (the model cannot fully supply baseline demand).
- **Bottom panel**: Relative deviation as a percentage of baseline demand.
  This normalizes for the vastly different scales of food groups, making it
  easier to spot proportionally large imbalances.

Food groups are sorted by relative deviation (largest first) in both panels.

.. _fig-validation-food-group-slack:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_food_group_slack.png
   :width: 100%
   :alt: Two-panel food group slack showing absolute and relative deviation

   Food group slack under the validation configuration. Top: absolute slack
   (Mt). Bottom: relative deviation (% of baseline demand). Groups are sorted
   by relative deviation. See :doc:`nutrition` for food group definitions.


.. _validation-slack-overview:

Slack Overview
--------------

The slack overview aggregates all slack categories — food, feed, land, and
water — into a single chart showing the total penalty cost incurred in each
category. This provides a high-level view of the overall model balance:
categories with large slack costs are the primary areas where data or
structural assumptions need refinement.

.. _fig-validation-slack-overview:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_slack_overview.png
   :width: 80%
   :alt: Horizontal bar chart showing slack penalty by category
   :align: center

   Slack penalty by category under the validation configuration. Bar length
   shows cost (bn USD); annotations show the physical quantity and unit.
   Categories with zero slack are omitted. See :doc:`land_use` for land slack
   and :doc:`livestock` for feed slack details.


.. _validation-feed-breakdown:

Feed Breakdown
--------------

The feed breakdown shows the composition of dry-matter feed consumed by each
animal type. This is useful for validating that the model's livestock sector
produces plausible feed mixes — for example, that ruminants receive
predominantly grass and roughage while monogastrics rely on grains and protein
meals.

.. _fig-validation-feed-breakdown:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_feed_breakdown.png
   :width: 80%
   :alt: Stacked horizontal bar chart of feed composition by animal type
   :align: center

   Dry-matter feed use (Mt) by animal type and feed category under the
   validation configuration. Animals are sorted by total feed intake. See
   :doc:`livestock` for the feed conversion model and category definitions.

Feed slack from the validation solve drives the calibration pipeline
described in :ref:`feed-calibration`.


Running the Validation
----------------------

To run the validation configuration locally:

.. code-block:: bash

   # Full validation run (build + solve + analysis)
   tools/smk -j4 --configfile config/validation.yaml

   # Generate validation plots
   tools/smk -j4 --configfile config/validation.yaml -- \
       results/validation/plots/scen-default/crop_production_map.pdf \
       results/validation/plots/scen-default/food_group_slack.pdf \
       results/validation/plots/scen-default/slack_overview.pdf \
       results/validation/plots/scen-default/feed_breakdown.pdf

