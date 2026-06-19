.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

GLADE Documentation
==========================

GLADE (Global Land, Agriculture, Diet and Emissions) is a global food systems optimization model that explores trade-offs between environmental sustainability and nutritional outcomes using linear programming.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/production_pattern.gif
   :alt: Animated map showing how optimal crop production patterns shift as carbon prices increase from $0 to $500 per tonne CO2-equivalent.
   :width: 100%

   Dominant crop group, land-use intensity, and livestock protein output under increasing carbon pricing. The animation sweeps from no carbon price ($0/tCO\ :sub:`2`-eq) through moderate ($50) and high ($200) to very high ($500). Circles show animal-product protein output per country (area proportional to Mt protein); colour indicates feed conversion ratio (green = efficient, red = inefficient). As carbon prices rise, cropland contracts and livestock production concentrates in more efficient systems.

.. raw:: html

   <a class="cd-launch" href="carbon_price_dial.html">&#9658;&nbsp; Try the interactive Carbon Price Dial</a>

----

Global scope and spatial resolution
------------------------------------

The model divides the world into sub-national optimization regions and connects them through hub-based :doc:`trade networks <food_processing>`. Within each region, high-resolution geophysical data — crop yield potentials, land cover, irrigation infrastructure, water availability — are aggregated from gridcell-level datasets to drive the optimization. More than 60 crops are represented, each with spatially explicit yield potentials derived from the `GAEZ <https://gaez.fao.org/>`_ framework. See :doc:`data_sources` for the full list of input datasets.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/intro_global_coverage.png
   :width: 100%
   :alt: Global model coverage map

   Optimization regions (here 250) created by clustering administrative units. Each region has its own land endowment, crop yields, water budget, and dietary requirements.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/crop_yield_wheat.png
   :width: 100%
   :alt: Wheat yield potential map

   Example input data: wheat rainfed yield potential (tonnes/ha) from GAEZ v5. See :doc:`crop_production` for yield maps of other crops, water resources, and multi-cropping.

Supply chain representation
---------------------------

The optimization covers the food supply chain from primary resources to human nutrition: :doc:`land allocation <land_use>` and :doc:`crop production <crop_production>`, :doc:`livestock <livestock>` systems with grazing and feed-based pathways, :doc:`food processing <food_processing>` with co-products, waste, and international trade, and finally :doc:`nutritional requirements <nutrition>` that ensure diets meet caloric and food-group constraints for every country's population.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/model_topology.png
   :width: 100%
   :alt: Model topology showing high-level material flows

   High-level topology of the model. Commodities flow from primary inputs (land, water, fertilizer) through crop and animal production, processing and trade, to final consumption — with emissions tracked at each stage. See :doc:`model_framework` for the mathematical formulation.

Environmental impacts
---------------------

The model tracks greenhouse gas emissions from multiple sources — including land-use change, rice cultivation, livestock, and fertilizer application — all spatially resolved and converted to CO\ :sub:`2`-equivalents. The figure below shows one component: annualised emission factors from land-use change, derived from satellite-based biomass and soil carbon data. These and other emissions can be priced into the objective function or capped as constraints, allowing the optimizer to find production patterns that reduce environmental pressure. See :doc:`environment` for details.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/environment_luc_lef.png
   :alt: Global maps of land-use change emission factors
   :width: 100%

   Annualised land-use change emission factors used in the optimisation. Left: CO\ :sub:`2` released per hectare of cropland expansion. Right: CO\ :sub:`2` sequestered per hectare of existing cropland spared and allowed to regenerate.

Diet and health
---------------

Dietary constraints ensure that each country's population meets nutritional requirements across food groups. The model integrates epidemiological data from the `Global Burden of Disease <https://www.healthdata.org/research-analysis/gbd>`_ study to quantify how dietary patterns affect disease burden, measured in years of life lost. This makes it possible to optimize jointly for environmental sustainability and public health. See :doc:`nutrition`, :doc:`current_diets`, and :doc:`health`.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/health_burden.png
   :width: 100%
   :alt: Choropleth map of diet-attributable disease burden by health cluster

   Baseline diet-attributable chronic disease burden (years of life lost per 100,000 population) by health cluster, derived from Global Burden of Disease data. Countries are grouped into epidemiological clusters that share similar disease profiles.

.. toctree::
   :hidden:
   :caption: Getting started
   :maxdepth: 2

   introduction
   tutorial
   about
   publications

.. toctree::
   :hidden:
   :caption: Model framework
   :maxdepth: 2

   model_framework
   data_sources

.. toctree::
   :hidden:
   :caption: Supply chain & impacts
   :maxdepth: 2

   land_use
   crop_production
   livestock
   food_processing
   nutrition
   current_diets
   consumer_values
   health
   environment
   costs

.. toctree::
   :hidden:
   :caption: Running the model
   :maxdepth: 2

   configuration
   workflow
   cluster_execution

.. toctree::
   :hidden:
   :caption: Outputs & evaluation
   :maxdepth: 2

   carbon_price_dial
   results
   analysis
   validation
   calibration
   sensitivity_analysis

.. toctree::
   :hidden:
   :caption: Reference & contributing
   :maxdepth: 2

   api/index
   development
   changelog
