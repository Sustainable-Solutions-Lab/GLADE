.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

food-opt Documentation
======================

A global food systems optimization model that explores trade-offs between environmental sustainability and nutritional outcomes using linear programming.

.. image:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/production_pattern.gif
   :alt: Animated map showing how optimal crop production patterns shift as trade friction increases from free trade to near-autarky.
   :width: 100%

*Dominant crop group and land-use intensity under increasing trade friction. The animation sweeps from nearly free trade (0.25× baseline transport costs) through the baseline (1×) and costly trade (4×) to near-autarky (100×). As trade becomes more expensive, production disperses from comparative-advantage regions toward local self-sufficiency, and total land use rises.*

.. toctree::
   :maxdepth: 2

   introduction
   model_framework
   land_use
   crop_production
   livestock
   food_processing
   nutrition
   current_diets
   health
   environment
   costs
   configuration
   data_sources
   workflow
   results
   analysis
   api/index
   development

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
