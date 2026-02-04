.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Nutrition
=========

Overview
--------

The nutrition module ensures that the optimized food system meets population dietary requirements. This includes:

* **Macronutrient constraints**: Carbohydrates, protein, fat, and calories per capita
* **Food group constraints**: Consumption of whole grains, fruits, vegetables, etc.
* **Population scaling**: Aggregating per-capita needs to regional/national totals

Macronutrients
--------------

Configuration
~~~~~~~~~~~~~

Macronutrient constraints are specified in ``config/default.yaml``:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: macronutrients ---
   :end-before: # --- section: animal_products ---

**Constraint types**:

* ``min``: Lower bound (≥)
* ``max``: Upper bound (≤)
* ``equal``: Exact requirement (=)

.. TODO: write in more detail about the implementation
..
   Model Implementation
   ~~~~~~~~~~~~~~~~~~~~

Food Groups
-----------

Beyond macronutrients, the model can also constrains consumption of food groups. Moreover, food groups are used to assess dietary risk factors (see :ref:`health-impacts`).

Configuration
~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: food_groups ---
   :end-before: # --- section: diet ---

List the active groups under ``food_groups.included`` and only specify
constraints for the ones that need limits (``min``, ``max``, or ``equal`` in
g/person/day). Leaving ``constraints`` empty allows the optimizer to choose any
mix of foods that satisfies macronutrient and other requirements.

Foods are assigned to groups in ``data/curated/food_groups.csv``. Example:

.. TODO: refine this section
..
   Model Implementation
   ~~~~~~~~~~~~~~~~~~~~

   Food group constraints work similarly to macronutrients:

   1. **Food group buses**: Per-country buses (e.g., ``food_group_fruit_USA``)

   2. **Food → Group links**: Foods contribute to their group bus

   3. **Population constraints**: Σ(group consumption) ≥ population × min requirement

   This ensures dietary diversity even if macronutrient needs could be met by a narrow set of crops.

Population Data
---------------

Population projections come from the UN World Population Prospects (WPP) 2024 revision.

Data Processing
~~~~~~~~~~~~~~~

The ``prepare_population`` rule (``workflow/scripts/prepare_population.py``):

1. **Load WPP data**: ``data/downloads/WPP_population.csv.gz``

2. **Filter**:

   * Countries in ``config['countries']``
   * Planning horizon year (``config['planning_horizon']``, e.g., 2030)
   * Medium variant projection

3. **Aggregate**: Sum population by country (converts thousands → persons)

4. **Output**:

   * ``processing/{name}/population.csv``: Total population by country
   * ``processing/{name}/population_age.csv``: Age-structured population for health module

Age Structure
~~~~~~~~~~~~~

Age-structured population is used in the health module to weight dietary risk factors by demographic composition (children vs. adults vs. elderly have different disease burdens).

Nutritional Content Data
-------------------------

The file ``data/curated/nutrition.csv`` contains nutritional composition for each food product, sourced from the **USDA FoodData Central** database. This data is retrieved from the SR Legacy (Standard Reference) database, which provides laboratory-analyzed nutrient data for foods.

**Data source**: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. https://fdc.nal.usda.gov/

**Content**: Macronutrient values (protein, carbohydrates, fat) and energy (kcal) per 100g of food product.

**License**: Public domain under CC0 1.0 Universal. See :doc:`data_sources` for full details.

The FAO Nutrient Conversion Table for Supply Utilization Accounts (2024 edition) is also stored locally in ``data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx`` via the ``download_fao_nutrient_conversion_table`` workflow rule, providing FAO-authored nutrient factors for cross-checking FAOSTAT supply data (subject to FAO's non-commercial use guidance). ``workflow/scripts/prepare_fao_edible_portion.py`` distils the edible portion coefficients from sheet ``03`` of that workbook for all configured crops, materialising them in ``processing/{name}/fao_edible_portion.csv`` for downstream use.

When the model assembles crop→food conversion links it rescales dry-matter crop production to fresh edible food mass using these coefficients together with moisture fractions from ``data/curated/crop_moisture_content.csv``: dry harvests are uplifted by ``edible_portion_coefficient / (1 - moisture_fraction)`` before applying the pathway-specific processing factors from ``data/curated/foods.csv``. Each processing pathway can produce multiple food products with factors that maintain mass balance (sum ≤ 1.0). Crops flagged in ``data/curated/yield_unit_conversions.csv`` are the few cases where GAEZ reports processed outputs (sugar or oil); those entries handle the unit conversion back to dry matter so that downstream processing can proceed uniformly.

**Retrieval**:

* The repository includes pre-fetched nutritional data from USDA
* To update with fresh data, enable ``data.usda.retrieve_nutrition: true`` in the config
* Run: ``snakemake -- data/curated/nutrition.csv`` (requires network access and API key)
* Food-to-USDA mappings are maintained in ``data/curated/usda_food_mapping.csv``
* A shared API key is included in the repository; users can optionally obtain their own free API key at https://fdc.nal.usda.gov/api-key-signup

Per-Capita vs. Total Consumption
---------------------------------

The model works with total annual flows (Mt/year) but nutritional requirements are per-capita per-day. Conversion:

.. math::

   \text{Total requirement (Mt/year)} = \frac{\text{per capita (g/day)} \times \text{population} \times 365}{10^{12}}

From the model's perspective:

* Food buses carry total food availability (Mt)
* Nutrient buses carry total nutrient availability (Mt for mass, PJ for energy)
* Constraints compare these totals to population-scaled requirements

Dietary Patterns
----------------

The model does not currently prescribe specific dietary patterns (e.g., Mediterranean, vegetarian, EAT-Lancet) but rather:

1. **Lower / upper bounds**: Ensure minimum nutritional adequacy
2. **Cost minimization**: Subject to those bounds, minimize environmental + health costs

Workflow Integration
--------------------

Nutritional constraints are incorporated in the ``build_model`` rule:

1. **Load population**: ``processing/{name}/population.csv``
2. **Load nutrition data**: ``data/curated/nutrition.csv``
3. **Create nutrient buses**: Per-country buses for each nutrient
4. **Create food → nutrient links**: Based on nutritional content
5. **Add global constraints**: Population × requirement bounds

No separate rule needed—nutrition is integrated into the model structure.
