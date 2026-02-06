.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Production Costs
================

Overview
--------

The model incorporates production costs for both crop and livestock systems to represent the economic considerations of agricultural production. Costs are applied as marginal costs on production links in the PyPSA network, ensuring that the optimization accounts for both physical and economic constraints.

This page provides an overview of how production costs are sourced, processed, and applied throughout the model.

.. contents::
   :local:
   :depth: 2

Cost Categories
---------------

The model distinguishes between three main categories of production costs:

**Crop Production Costs**
   Costs associated with growing crops, including labor, machinery, energy, and other inputs (excluding fertilizer, which is modeled endogenously).

**Livestock Production Costs**
   Costs associated with raising animals for meat, milk, and eggs, including labor, veterinary services, housing, and energy (excluding feed and land, which are modeled endogenously).

**Grazing Costs**
   Costs specifically associated with pasture-based livestock production, representing the management and maintenance of grassland feed systems.

What Costs Include and Exclude
-------------------------------

Included Costs
~~~~~~~~~~~~~~

Production costs in the model capture the following expense categories:

* **Labor**: Both hired labor and the opportunity cost of unpaid/family labor
* **Veterinary services**: Animal health care and preventive treatments (livestock only)
* **Energy**: Electricity and fuel for farm operations
* **Machinery and equipment**: Depreciation and maintenance
* **Housing and facilities**: Depreciation of buildings and infrastructure (livestock only)
* **Interest on operating capital**: Financial costs of production
* **Other variable inputs**: Seeds, pesticides, and other operational expenses (crops only)

Excluded Costs (Modeled Endogenously)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following cost categories are **excluded** from the production cost data because they are represented explicitly in the optimization model:

* **Feed costs**: Crop and residue feed inputs are modeled as network flows with their own costs
* **Fertilizer costs**: Synthetic fertilizer is a constrained resource in the model
* **Land costs and rent**: Land opportunity cost is implicit in the land allocation decisions
* **Grazing feed costs** (for livestock production costs): Grassland feed is modeled separately with its own grazing costs

This separation ensures that costs are not double-counted while maintaining accurate economic representation.

Data Sources
------------

Production cost data is sourced from two major agricultural accounting systems, providing coverage for both US and European production systems.

USDA Economic Research Service (United States)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Coverage**:
  * Crops: Major commodity crops (wheat, corn, soybeans, rice, cotton, etc.)
  * Livestock: Dairy (milk), beef cattle (cow-calf), hogs (pork)

**Data characteristics**:
  * Time period: 2015-2024 (averaged across years)
  * Units: USD per acre (crops) or USD per head (livestock)
  * Spatial resolution: US national averages
  * Cost structure: Detailed line-item breakdown by expense category

**Sources**:
  * `Commodity Costs and Returns <https://www.ers.usda.gov/data-products/commodity-costs-and-returns/>`_
  * `Milk Cost of Production Estimates <https://www.ers.usda.gov/data-products/milk-cost-of-production-estimates/>`_

**Workflow scripts**:
  * ``workflow/scripts/retrieve_usda_costs.py``: Processes crop cost data
  * ``workflow/scripts/retrieve_usda_animal_costs.py``: Processes livestock cost data

FADN - Farm Accountancy Data Network (European Union)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Coverage**:
  * Crops: All major crop groups (cereals, oilseeds, vegetables, fruits, etc.)
  * Livestock: All major animal production systems

**Data characteristics**:
  * Time period: 2004-2020 (averaged across years)
  * Units: EUR per farm (allocated to crop/livestock categories)
  * Spatial resolution: Country-level data for all EU member states
  * Cost structure: Farm-level accounting data allocated by output value share

**Source**:
  * `FADN Public Database <https://agridata.ec.europa.eu/extensions/FADNPublicDatabase/FADNPublicDatabase.html>`_

**Workflow scripts**:
  * ``workflow/scripts/retrieve_fadn_costs.py``: Processes crop cost data
  * ``workflow/scripts/retrieve_fadn_animal_costs.py``: Processes livestock cost data

Cost Processing Methodology
----------------------------

The cost data undergoes several processing steps to ensure consistency and accuracy across different sources and production systems.

Crop Costs
~~~~~~~~~~

Crop production costs are processed separately for USDA and FADN sources, then merged to produce global cost estimates.

USDA Crop Cost Processing
^^^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/retrieve_usda_costs.py``

1. **Download and Parse**: Fetch CSV files from USDA ERS database for each crop
2. **Filter Cost Categories**:

   * **Per-year costs**: Operating costs allocated across the growing season (labor, energy, machinery use)
   * **Per-planting costs**: One-time costs per planting cycle (seeds, pesticides, planting operations)
   * **Excluded**: Fertilizer costs (modeled endogenously), land costs/rent

3. **Temporal Aggregation**: Average costs across configured years (typically 2015-2024)
4. **Inflation Adjustment**: Convert all costs to base year USD using CPI indices
5. **Unit Conversion**: Convert from USD/acre to USD/hectare (× 2.471)
6. **Output**: ``processing/{name}/usda_costs.csv`` with columns:

   * ``crop``: Crop name
   * ``cost_per_year_usd_{base_year}_per_ha``: Annual recurring costs (USD/ha)
   * ``cost_per_planting_usd_{base_year}_per_ha``: Per-planting costs (USD/ha)

FADN Crop Cost Processing
^^^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/retrieve_fadn_costs.py``

FADN data requires more complex processing due to its farm-level aggregation:

1. **Load Farm-Level Data**: Read FADN database with farm accounting variables (SE codes)
2. **Cost Allocation by Output Value**:

   * Calculate total farm output value (SE131)
   * For each crop group, sum output values from relevant SE codes
   * Allocate farm-level costs proportionally to each crop group's share of total output
   * This approach correctly captures economic intensity: high-value crops receive higher allocated costs

3. **Cost Categories**:

   * **Per-year costs**: Total farm costs (labor, energy, depreciation, etc.) allocated by output share
   * **Per-planting costs**: Currently set to zero (not separately tracked in FADN)
   * **Excluded**: Feed costs, fertilizer costs, rent

4. **Calculate Cost per Hectare**:

   .. math::

      \text{Cost per ha} = \frac{\text{Allocated Cost (EUR)}}{\text{Crop Group Area (ha)}}

5. **Currency and Inflation Adjustment**:

   * Inflate to base year EUR using HICP (Harmonized Index of Consumer Prices)
   * Convert to international dollars using PPP (Purchasing Power Parity) rates
   * This produces costs comparable across countries with different price levels

6. **Temporal and Spatial Aggregation**: Average across countries and years for each FADN crop category
7. **Crop Mapping**: Map FADN categories to model crop names using ``data/curated/fadn_crop_mapping.yaml``
8. **Output**: ``processing/{name}/fadn_costs.csv`` with same column structure as USDA

Merging Crop Costs
^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/merge_crop_costs.py``

The merging process combines USDA and FADN cost estimates:

1. **Load Multiple Sources**: Read cost CSVs from both USDA and FADN processing
2. **Average Across Sources**: For crops with data from multiple sources, compute the mean cost
3. **Apply Fallback Mappings**: For crops without direct cost data, use costs from similar crops:

   * Defined in ``data/curated/crop_cost_fallbacks.yaml``
   * Example: Rye → Wheat costs, Silage-maize → Maize costs

4. **Default to Zero**: Crops without data or fallbacks receive zero costs (with warnings logged)
5. **Output**: ``processing/{name}/crop_costs.csv`` containing cost data for all configured crops

Livestock Costs
~~~~~~~~~~~~~~~

Livestock production costs follow a similar two-source approach with additional complexity due to the need to convert per-head costs to per-tonne costs.

USDA Livestock Cost Processing
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/retrieve_usda_animal_costs.py``

1. **Download Data**: Fetch cost and returns data from USDA ERS for each animal product
2. **Filter and Aggregate Costs**:

   * **Included**: Operating costs, allocated overhead, labor (including opportunity cost)
   * **Excluded**: Feed costs (endogenous), land rent
   * **Grazing costs**: Separately extracted using ``grazing_cost_items`` parameter (e.g., "Grazed feed" line item)

3. **Per-Head Calculation**: Sum relevant cost line items to get total cost per animal per year
4. **Physical Yield Data**: Use USDA production statistics to get output per head:

   * Milk: Pounds per cow per year → tonnes per head
   * Meat: Live weight → carcass weight → retail meat weight (using USDA conversion factors)

5. **Convert to Per-Tonne Costs**:

   .. math::

      \text{Cost per tonne product} = \frac{\text{Cost per head (USD/year)}}{\text{Yield (tonnes product/head/year)}}

6. **Separate Grazing Costs**: Maintain separate column for grazing-specific costs
7. **Inflation Adjustment**: Convert to base year USD using CPI
8. **Output**: ``processing/{name}/usda_animal_costs.csv``

FADN Livestock Cost Processing
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/retrieve_fadn_animal_costs.py``

FADN livestock costs use a sophisticated allocation methodology:

1. **Farm-Level Cost Extraction**:

   * Read FADN farm accounting data for livestock-specialized farms
   * Extract total livestock costs (labor, veterinary, energy, depreciation, etc.)
   * **Excluded**: Feed costs, land rent
   * **Grazing costs**: Separately identified using ``grazing_cost_items`` (SE codes for pasture/grazing)

2. **Allocation to Livestock Categories**:

   * **Specific costs** (veterinary, animal-specific inputs): Allocated by livestock output value share
   * **Shared overhead** (buildings, energy, general labor): Allocated by livestock sector's share of **total farm output** to avoid over-allocation in mixed farms

3. **Normalize to Livestock Units (LU)**:

   * Convert farm-level costs to per-LU using standard coefficients:

     * 1 Dairy Cow = 1.0 LU
     * 1 Beef Cow = 0.8 LU
     * 1 Pig = 0.3 LU
     * 1 Sheep = 0.1 LU

   * This produces cost per head for each animal category

4. **Physical Yield Calculation**:

   * Use FAOSTAT country-level data: Total Production ÷ Total Stocks = Yield per head
   * This captures regional differences in:

     * Slaughter weights and cycles per year (meat)
     * Dairy productivity (milk yield per cow)
     * Herd structure (breeding vs. production animals)

5. **Convert to Per-Tonne Costs** (same formula as USDA)
6. **Currency and Inflation**: Inflate to base year EUR (HICP), convert to international USD (PPP)
7. **Separate Grazing Costs**: Maintain separate column for pasture/grazing-related costs
8. **Output**: ``processing/{name}/fadn_animal_costs.csv``

Merging Livestock Costs
^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/merge_animal_costs.py``

1. **Load Multiple Sources**: Combine USDA and FADN livestock cost estimates
2. **Average Across Sources**: For products with multiple data sources, compute mean
3. **Apply Fallback Mappings**: For products without direct data:

   * Chicken → Pork (similar intensive housed systems)
   * Eggs → Pork (intensive production)
   * Defined in configuration under ``animal_cost_fallbacks``

4. **Maintain Separate Grazing Costs**: Keep grazing cost column distinct from general production costs
5. **Output**: ``processing/{name}/animal_costs.csv`` with columns:

   * ``product``: Animal product name
   * ``cost_per_mt_usd_{base_year}``: Production cost excluding grazing (USD/tonne product)
   * ``grazing_cost_per_mt_usd_{base_year}``: Grazing-specific cost (USD/tonne product)

Grazing Costs
~~~~~~~~~~~~~

Grazing costs are extracted as a separate component during livestock cost processing and then converted to feed-basis costs for application in the model.

Extraction from Source Data
^^^^^^^^^^^^^^^^^^^^^^^^^^^

During USDA and FADN livestock cost processing, grazing costs are identified using configured line items:

**USDA**: Line items labeled "Grazed feed" or similar in the cost and returns spreadsheets

**FADN**: SE codes corresponding to pasture management, grassland maintenance

These costs are:

* Allocated to livestock products by output value share (same methodology as other costs)
* Stored in separate ``grazing_cost_per_mt_usd_{base_year}`` column
* Expressed per tonne of animal product (not per tonne of feed)

Conversion to Feed-Basis Costs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Script**: ``workflow/scripts/build_model/grassland.py`` (function ``calculate_grazing_cost_per_tonne_dm``)

Since grazing costs in the source data are per tonne of animal product, they must be converted to per tonne of dry matter (DM) feed for application to grassland feed links:

1. **Load Data**:

   * Grazing costs per tonne product from ``animal_costs.csv``
   * Feed conversion efficiencies from ``feed_to_products.csv`` (tonnes product per tonne feed DM)

2. **Calculate Implied Feed Cost**:

   For each ruminant product, the grazing cost per tonne of feed is:

   .. math::

      \text{Feed Cost (USD/tonne DM)} = \text{Product Cost (USD/tonne)} \times \text{Efficiency (tonne product/tonne DM)}

3. **Global Averaging**: Average the implied feed costs across all ruminant products to get a single grazing cost rate
4. **Result**: A single global grazing cost in USD per tonne dry matter feed

This approach ensures that grazing costs are:

* Properly allocated across different ruminant products
* Consistent with the feed conversion efficiencies used in the model
* Applied at the correct point in the production chain (grassland feed production)

Application in the Optimization Model
--------------------------------------

Production costs are applied as marginal costs on PyPSA network links, affecting the objective function during optimization.

Crop Production Costs
~~~~~~~~~~~~~~~~~~~~~

**Implementation**: ``workflow/scripts/build_model/crops.py`` (lines 155-202)

Crop costs are applied to production links that convert land into crop output:

**Link structure**:
  * **Input (bus0)**: Land pool (Mha) by region, resource class, water supply
  * **Output (bus1)**: Crop commodity bus (Mt) by country
  * **Efficiency**: Crop yield (Mt/Mha)

**Cost calculation**:

For single-season crops:

.. code-block:: python

   # Total cost per hectare (USD/ha)
   cost_per_ha = cost_per_year + cost_per_planting

   # Convert to bnUSD per Mha (PyPSA units)
   marginal_cost = cost_per_ha * 1e6 * USD_TO_BNUSD

For multi-cropping systems (multiple crops per year on the same land):

.. code-block:: python

   # Average per-year cost across crops in the cycle
   avg_cost_per_year = total_per_year_costs / n_crops

   # Total per-planting cost (sum across all crops)
   total_per_planting = sum(cost_per_planting for each crop)

   # Combined marginal cost
   marginal_cost = (avg_cost_per_year + total_per_planting) * 1e6 * USD_TO_BNUSD

**Interpretation**:
  * The marginal cost represents the economic cost of using one Mha of land for crop production
  * Higher-cost crops will be penalized in the objective function relative to lower-cost alternatives
  * The optimization balances production costs against other objectives (nutrition, emissions, etc.)

Livestock Production Costs
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Implementation**: ``workflow/scripts/build_model/animals.py`` (lines 230-243)

Livestock costs are applied to links that convert feed into animal products:

**Link structure**:
  * **Input (bus0)**: Feed bus (Mt DM) by feed category and country
  * **Output (bus1)**: Animal product bus (Mt fresh weight) by country
  * **Efficiency**: Feed conversion efficiency (Mt product / Mt feed DM)

**Cost calculation**:

.. code-block:: python

   # Cost from animal_costs.csv (USD per Mt product)
   cost_per_mt_product = animal_costs.loc[product]

   # Efficiency (Mt product per Mt feed DM)
   efficiency = feed_requirements.loc[product, feed_category, 'efficiency']

   # Convert to cost per Mt feed input
   cost_per_mt_feed = cost_per_mt_product / efficiency

   # Convert to bnUSD per Mt (PyPSA units)
   marginal_cost = cost_per_mt_feed * USD_TO_BNUSD

**Interpretation**:
  * The marginal cost represents the economic cost of converting feed into animal product
  * More efficient production systems (higher efficiency) have lower costs per unit feed input
  * The optimization accounts for both feed conversion efficiency and production costs

Grazing Costs
~~~~~~~~~~~~~

**Implementation**: ``workflow/scripts/build_model/grassland.py`` (lines 235-236)

Grazing costs are applied to links that produce grassland feed from land:

**Link structure**:
  * **Input (bus0)**: Land pool (Mha) by region and resource class (rainfed only)
  * **Output (bus1)**: Ruminant grassland feed bus (Mt DM) by country
  * **Efficiency**: Grassland yield (Mt DM / Mha)

**Cost calculation**:

.. code-block:: python

   # Grazing cost (USD per tonne DM)
   grazing_cost_per_tonne_dm = calculate_grazing_cost_per_tonne_dm(...)

   # Grassland yield (Mt DM per Mha) — already effective feed yield
   efficiency = grassland_yield

   # Convert to cost per Mha (bnUSD/Mha)
   marginal_cost = (grazing_cost_per_tonne_dm * efficiency *
                    MEGATONNE_TO_TONNE * USD_TO_BNUSD)

**Interpretation**:
  * The marginal cost represents the economic cost of producing grassland feed from one Mha of pasture
  * Higher-yielding grassland has higher costs per Mha (but the cost per tonne DM is constant)
  * Grassland yields are already corrected for utilization in the merge step (see :doc:`livestock`)

Model Units and Conversions
----------------------------

Mass Units
~~~~~~~~~~

* **Input data**: Often in tonnes (t) or kilograms (kg)
* **Model buses**: Megatonnes (Mt) for all commodity flows
* **Conversion**: 1 Mt = 1,000,000 t = 1e6 t

Area Units
~~~~~~~~~~

* **Input data**: Usually hectares (ha) or acres
* **Model land buses**: Mega-hectares (Mha)
* **Conversions**:

  * 1 Mha = 1,000,000 ha = 1e6 ha
  * 1 acre = 0.404686 ha
  * 1 ha = 2.47105 acres

Currency Units
~~~~~~~~~~~~~~

* **Input data**: USD or EUR (various base years)
* **Model objective**: Billion USD (bnUSD) in configured base year
* **Conversion**: 1 bnUSD = 1,000,000,000 USD = 1e9 USD
* **Constant**: ``USD_TO_BNUSD = 1e-9``


Configuration
-------------

Cost-related configuration parameters are specified in ``config/default.yaml``:

**Data retrieval**:

.. code-block:: yaml

   data:
     base_year: 2020  # Base year for all monetary values

   cost_data:
     usda_years: [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
     fadn_years: [2015, 2016, 2017, 2018, 2019, 2020]

**Fallback mappings**:

.. code-block:: yaml

   animal_cost_fallbacks:
     chicken: pork
     eggs: pork

**Grazing cost items** (USDA):

.. code-block:: yaml

   grazing_cost_items:
     - "Grazed feed"

**Grazing cost items** (FADN, SE codes):

.. code-block:: yaml

   fadn_grazing_cost_items:
     SE105: "Forage crops"
     SE110: "Pasture"

See Also
--------

* :doc:`crop_production` - Crop production modeling details
* :doc:`livestock` - Livestock production modeling details
* :doc:`data_sources` - Complete data source documentation
* :doc:`configuration` - Full configuration reference
