.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Model Framework
===============

Mathematical Structure
----------------------

The ``food-opt`` model is formulated as a mixed integer linear programming (MILP) problem that minimizes total system costs subject to constraints on production capacity, nutritional requirements, and environmental limits. Here, we give a high-level overview over the model structure; a complete listing of equations is outstanding.

Objective Function
~~~~~~~~~~~~~~~~~~

The objective function minimizes total costs across multiple dimensions:

.. math::

   \min \sum_{i} c_i x_i

where :math:`x_i` are decision variables and :math:`c_i` are associated costs including:

* **Production costs**: Crop and livestock production expenses
* **Trade costs**: Transportation costs based on distance and product type
* **Environmental costs**: Emissions priced at configured carbon price (USD/tCO₂-eq)
* **Health costs**: Dietary risk factors valued using years of life lost (YLL) multiplied by the configured ``health.value_per_yll``

Decision Variables
~~~~~~~~~~~~~~~~~~

The model optimizes the following classes of decision variables:

* **Crop production** (:math:`P_{c,r,w,k}`): Production of crop :math:`c` in region :math:`r` with water supply :math:`w` (irrigated/rainfed) and resource class :math:`k`
* **Livestock production** (:math:`L_{a,r,s}`): Production of animal product :math:`a` in region :math:`r` using production system :math:`s` (grazing/feed-based)
* **Food processing** (:math:`F_{c,f,r}`): Processing activities converting crop :math:`c` to food product :math:`f` in region :math:`r`
* **Land allocation** (:math:`A_{r,w,k}`): Cropland area allocated in region :math:`r`, water supply :math:`w`, resource class :math:`k`
* **Trade flows** (:math:`T_{c,r,h}`, :math:`T_{c,h,h'}`): Trade of commodity :math:`c` between region :math:`r` and hub :math:`h`, as well as between hubs :math:`h` and :math:`h'`.
* **Food consumption** (:math:`D_{f,r}`): Per-capita consumption of food :math:`f` in region :math:`r`

Constraints
~~~~~~~~~~~

The model is subject to multiple constraint categories:

**Production Constraints**

* Crop yields limiting crop production based on region and resource class
* Livestock feed requirements and conversion efficiencies
* Land availability limits by region and resource class
* Water availability constraints by basin and growing season

**Nutritional Constraints**

* Minimum macronutrient requirements (carbohydrates, protein, fat, calories)
* Minimum food group consumption (whole grains, fruits, vegetables, etc.)
* Per-capita dietary balance across the population

**Processing and Trade Constraints**

* Crop-to-food conversion efficiencies
* Hub-based trade network topology
* Transport costs differentiated by commodity category
* Non-tradable commodities (e.g., fodder crops)

**Environmental Constraints**

* Optional limits on total greenhouse gas emissions
* Optional limits on total nitrogen fertilizer application

PyPSA Implementation
--------------------

The model is implemented using `PyPSA <https://pypsa.org>`_ (Python for Power System Analysis), which provides a flexible framework for optimizing flow networks with linear constraints. While PyPSA was originally designed for energy systems, its component-based structure maps naturally to food system flows.

Network Components
~~~~~~~~~~~~~~~~~~

**Carriers**
  Define the commodity types flowing through the network (e.g., ``crop_wheat``, ``food_bread``, ``nutrient_protein``). Each carrier has an associated unit (tonnes, megacalories, etc.). See the `PyPSA carriers documentation <https://pypsa.readthedocs.io/en/latest/user-guide/components.html#carrier>`_ for more details.

**Buses**
  Represent locations where commodities accumulate or are exchanged. Buses are typically defined per-country or per-region (e.g., ``crop_wheat_USA``, ``land_class0_region42``). See the `PyPSA buses documentation <https://pypsa.readthedocs.io/en/latest/user-guide/components.html#bus>`_ for more details.

**Links**
  Represent transformations or transport of commodities. See the `PyPSA links documentation <https://pypsa.readthedocs.io/en/latest/user-guide/components.html#link>`_ for more details. Links have:

  * ``bus0``: Primary input bus
  * ``bus1``: Primary output bus
  * ``efficiency``: Conversion efficiency from bus0 → bus1
  * ``bus2``, ``bus3``, ...: Additional input/output legs
  * ``efficiency2``, ``efficiency3``, ...: Efficiencies for additional legs (positive = output, negative = input)

  Examples:

  * Crop production link: inputs = land + water + fertilizer; output = crop
  * Food processing link: inputs = crops; output = food product
  * Trade link: input = crop in region A; output = crop in region B (with transport cost)

**Stores**
  Represent resource availability or capacity limits. See the `PyPSA stores documentation <https://pypsa.readthedocs.io/en/latest/user-guide/components.html#store>`_ for more details. Key uses:

  * Land area available in each region/resource class
  * Water availability in each basin
  * Global fertilizer supply limits

**Global Constraints**
  Enforce system-wide limits. See the `PyPSA global constraints documentation <https://pypsa.readthedocs.io/en/latest/user-guide/components.html#global-constraints>`_ for more details. Examples include:

  * Total fertilizer consumption
  * Total greenhouse gas emissions
  * Population-level nutritional requirements

Component Naming Conventions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model uses systematic naming conventions to organize components:

* Crops: ``crop_{crop_name}_{country_code}``
* Foods: ``food_{food_name}_{country_code}``
* Nutrients: ``nutrient_{nutrient_name}_{country_code}``
* Land: ``land_class{class_num}_{region_id}``
* Water: ``water_basin{basin_id}``
* Primary resources: ``primary_fertilizer``, ``primary_water``

Multi-Bus Links for Complex Processes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Many agricultural processes involve multiple inputs and outputs, which are represented as multi-bus links. For example, crop production:

* ``bus0``: Land (input, primary)
* ``bus1``: Crop product (output, primary)
* ``bus2``: Water (input, with negative efficiency2)
* ``bus3``: Fertilizer (input, with negative efficiency3)
* ``bus4``: CO₂ emissions (output, with positive efficiency4)

The efficiency parameters capture:

* Crop yield per hectare (``efficiency`` on bus0→bus1)
* Water requirement per tonne of crop (``efficiency2``, negative)
* Fertilizer requirement per tonne of crop (``efficiency3``, negative)
* CO₂ emissions per tonne of crop (``efficiency4``, positive)

Configured multi-cropping sequences reuse the same pattern but emit several crop
outputs from a single land link. The land input (bus0) reuses the rainfed or irrigated
land bus (``_r``/``_i``) for the region, while each cycle contributes its own ``bus{n}``
with efficiency set to the aggregated yield (t/ha) for that crop. Fertilizer
requirements sum across all cycles before being applied on the fertilizer bus; irrigated
variants also attach to the region water bus with the summed water requirement.
At present we ignore relay-cropping possibilities in the GAEZ multiple-cropping
classification and treat every sequence as strictly sequential within the year.

Resource Flow Structure
-----------------------

The model follows a hierarchical flow structure:

1. **Primary resources** → Land, water, fertilizer availability in each region
2. **Crop production** → Raw agricultural commodities produced on land
3. **Animal production** → Livestock products from grassland (grazing) or crops (feed-based)
4. **Food processing** → Conversion of crops to food products
5. **Trade** → Inter-regional flows via hub networks
6. **Consumption** → Aggregation to nutritional outcomes and dietary risk factors
7. **Health impacts** → DALYs from dietary exposures

Units and Conversions
----------------------

The model uses consistent units throughout:

**Mass**
  * Land area: Mha (million hectares)
  * Crop/food production: tonnes (t) or megatonnes (Mt)
  * Nutritional mass (protein, etc.): grams/person/day → Mt/year

**Energy**
  * Nutritional energy (calories): kcal/person/day → PJ/year

**Emissions**
  * Methane (CH₄) and nitrous oxide (N₂O): tonnes of gas, converted downstream to Mt CO₂-eq
  * Aggregate greenhouse gases: tCO₂-eq (tonnes CO₂-equivalent)
  * Conversion factors: CH₄ (28 GWP100), N₂O (265 GWP100)

**Water**
  * Water use: Mm³ (million cubic meters)

**Economic**
  * Costs: USD (various sub-units: USD/tonne, USD/km, USD/tCO₂-eq)

Key conversion factors used in the code (``workflow/scripts/build_model.py``):

* ``TONNE_TO_MEGATONNE = 1e-6``
* ``KCAL_TO_PJ = 4.184e-12``
* ``KCAL_PER_100G_TO_PJ_PER_MEGATONNE ≈ 4.184e-2``
* ``DAYS_PER_YEAR = 365``

Solver Configuration
--------------------

The model supports multiple LP solvers:

* **HiGHS** (default, open-source): Fast interior-point method, suitable for large problems
* **Gurobi** (commercial): Often faster for very large problems, supports advanced solver options

Solver selection and options are configured in ``config/default.yaml``:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: solving ---
   :end-before: # --- section: plotting ---

Model Scale
-----------

Typical model dimensions (for the default configuration with 750 regions):

* **Regions**: 750 sub-national optimization regions
* **Crops**: ~70 crop types
* **Resource classes**: 3-4 yield quality classes per region
* **Variables**: ~1-5 million decision variables
* **Constraints**: ~1-10 million constraints
* **Solve time**: Minutes to hours depending on region count and solver

The model scales roughly linearly with the number of regions. Reducing ``aggregation.regions.target_count`` in the configuration will decrease solve time at the cost of spatial resolution.
