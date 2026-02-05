.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Current Diets
=============

Overview
--------

The model uses a hybrid approach to represent current consumption patterns, combining empirical dietary intake data from the **Global Dietary Database (GDD)** [GDD2024]_ [Miller2021]_ with food supply data from **FAOSTAT Food Balance Sheets (FBS)**. This baseline data serves multiple purposes:

* **Health impact assessment**: Calculating disease burden attributable to current dietary patterns
* **Baseline reference**: Comparing optimized diets against current consumption
* **Model constraints**: Optionally constrain the optimization to remain near current diets

Data Sources
------------

**Global Dietary Database (GDD)**
  * **Provider**: Tufts University Friedman School of Nutrition Science and Policy
  * **Coverage**: 185 countries, individual-level dietary surveys (1990-2018)
  * **Variables**: 54 dietary factors including foods, beverages, and nutrients
  * **Download**: Requires free registration at https://globaldietarydatabase.org/data-download
  * **Citation**: [GDD2024]_

**FAOSTAT Food Balance Sheets (FBS)**
  * **Provider**: FAO Statistics Division
  * **Coverage**: Global, annual estimates of food supply
  * **Variables**: Food supply quantity (kg/capita/year)
  * **Usage**: Supplements GDD for food groups where intake survey data is sparse or inconsistent (Dairy, Poultry, Vegetable Oils)

Weight Conventions
~~~~~~~~~~~~~~~~~~

GDD reports all dietary intake values in **grams per day using "as consumed" weights** [Miller2021]_. This means:

* **Fresh vegetables and fruits**: Reported in fresh weight (e.g., a raw apple, fresh tomato)
* **Grains**: Reported in cooked weight (e.g., cooked rice, prepared bread)
* **Dairy**: Reported as **total milk equivalents**, which includes milk, yogurt, cheese and other dairy products converted to their milk equivalent weight
* **Meats**: Reported in cooked/prepared weight

The model preserves these conventions in the processed output files. Units in the output CSV distinguish between general fresh weight (``g/day (fresh wt)``) and dairy milk equivalents (``g/day (milk equiv)``).

GDD to Food Group Mapping
--------------------------

The model maps GDD dietary variables to the food groups defined in ``config/food_groups``. This mapping is implemented in ``workflow/scripts/prepare_gdd_dietary_intake.py``.

Food Groups with GDD Data
~~~~~~~~~~~~~~~~~~~~~~~~~~

The following food groups are populated from GDD variables:

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Food Group
     - GDD Code
     - Description
   * - ``fruits``
     - v01
     - Total fruits (whole fruits only, excluding juices)
   * - ``vegetables``
     - v02
     - Non-starchy vegetables
   * - ``starchy_vegetable``
     - v03, v04
     - Potatoes + other starchy vegetables (aggregated)
   * - ``legumes``
     - v05
     - Beans and legumes
   * - ``nuts_seeds``
     - v06
     - Nuts and seeds
   * - ``grain``
     - v07
     - Refined grains (white flour, white rice)
   * - ``whole_grains``
     - v08
     - Whole grains
   * - ``red_meat``
     - v10
     - Unprocessed red meats (cattle, pig)
   * - ``prc_meat``
     - v09
     - Total processed meats
   * - ``fish``
     - v11
     - Total seafoods (fish + shellfish)
   * - ``eggs``
     - v12
     - Eggs
   * - ``sugar``
     - v15, v35
     - Sugar-sweetened beverages and added sugars

**Notes:**

* Multiple GDD variables can map to a single food group (e.g., starchy_vegetable = v03 potatoes + v04 other starchy veg)
* When aggregating, values are summed within each food group
* The ``fruits`` food group uses only v01 (whole fruits), excluding v16 (fruit juices), to align with the GBD fruit risk factor definition used in health impact modeling

Food Groups Sourced from FAOSTAT
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following food groups are populated from FAOSTAT Food Balance Sheets (FBS) because intake survey data (GDD) is often sparse, inconsistent, or structurally missing for these commodities:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Food Group
     - Description & Source Items
   * - ``dairy``
     - **Total Milk Equivalent**. Aggregated from FAOSTAT items: Milk - Excluding Butter (2848), Butter/Ghee (2740), and Cream (2743). Butter and cream are converted to milk equivalents using FAO dairy commodity tree extraction rates (≈21.3× for butter/ghee, ≈6.7× for cream); milk-excl.-butter is taken as-is.
   * - ``poultry``
     - **Poultry Meat** (2734).
   * - ``oil``
     - **Vegetable Oils** (2586).

**Methodology for FAOSTAT Data:**
FAOSTAT reports "Food Supply" (retail weight), which typically includes household waste. The model converts this to "Dietary Intake" (consumed weight) by applying country-specific waste fractions derived from the UNSD Food Waste Index (see :doc:`food_processing`).

Data Processing
---------------

The dietary data processing pipeline involves three stages:

1. **Prepare GDD Data** (``workflow/scripts/prepare_gdd_dietary_intake.py``): Processes GDD survey data for most food groups.
2. **Prepare FAOSTAT Data** (``workflow/scripts/prepare_faostat_dietary_intake.py``): Fetches FAOSTAT supply data for dairy, poultry, and oil; converts supply to intake by subtracting waste; fills missing countries using proxies.
3. **Merge Sources** (``workflow/scripts/merge_dietary_sources.py``): Combines the datasets into a unified ``dietary_intake.csv``.

The GDD processing step (Step 1) performs the following:

1. **Load GDD files**: Read country-level CSV files (``v*_cnty.csv``) for each dietary variable
2. **Filter to reference year**: Extract data for ``config.health.reference_year`` (default: 2018)
3. **Map age groups**: Convert GDD age midpoints to GBD-compatible age buckets (0-1, 1-2, 2-5, 6-10, 11-74, 75+ years)
4. **Aggregate strata**: Compute national averages by age group across sex/education/urban-rural strata
5. **Map to food groups**: Apply the GDD-to-food-group mapping defined in the script
6. **Aggregate variables**: Sum multiple GDD variables that map to the same food group (preserving age stratification)
7. **Handle missing countries**: Apply proxies for territories without separate GDD data
8. **Validate completeness**: Ensure all required countries and food groups are present
9. **Output**: Write ``processing/{name}/gdd_dietary_intake.csv`` with age-stratified data

Output Format
~~~~~~~~~~~~~

The processed dietary intake file has the following structure:

.. code-block:: none

   unit,item,country,age,year,value
   g/day (milk equiv),dairy,USA,0-1 years,2018,252.3
   g/day (milk equiv),dairy,USA,1-2 years,2018,258.3
   g/day (milk equiv),dairy,USA,11-74 years,2018,174.6
   g/day (milk equiv),dairy,USA,All ages,2018,187.1
   g/day (fresh wt),fruits,USA,11-74 years,2018,145.2
   ...

Where:

* ``unit``: Weight convention specific to the food group

  * ``g/day (fresh wt)``: Fresh/cooked "as consumed" weight for most foods
  * ``g/day (milk equiv)``: Total milk equivalents for dairy

* ``item``: Food group name
* ``country``: ISO 3166-1 alpha-3 country code
* ``age``: Age group using GBD-compatible naming

  * ``0-1 years``: Infants under 1 year
  * ``1-2 years``: Toddlers 1-2 years
  * ``2-5 years``: Early childhood 2-5 years
  * ``6-10 years``: Middle childhood 6-10 years
  * ``11-74 years``: Adults 11-74 years
  * ``75+ years``: Elderly 75+ years
  * ``All ages``: Population-weighted average across all age groups

* ``year``: Reference year
* ``value``: Mean daily intake in grams per person for the specified age group

Country Coverage
----------------

The GDD dataset covers 185 countries. For a small number of territories without separate dietary surveys, the model uses proxy data from similar countries:

* **American Samoa (ASM)**: Uses Samoa (WSM) data
* **French Guiana (GUF)**: Uses France (FRA) data
* **Puerto Rico (PRI)**: Uses USA data
* **Somalia (SOM)**: Uses Ethiopia (ETH) data

These proxies are defined in the ``COUNTRY_PROXIES`` dictionary in ``prepare_gdd_dietary_intake.py``.

Workflow Integration
--------------------

**Snakemake rules**:
  * ``prepare_gdd_dietary_intake``
  * ``prepare_faostat_dietary_intake``
  * ``merge_dietary_sources``

**Input**:
  * ``data/manually_downloaded/GDD-dietary-intake/Country-level estimates/*.csv`` (GDD)
  * FAOSTAT API (live fetch)

**Configuration parameters**:
  * ``config.countries``: List of countries to process
  * ``config.food_groups.included``: Food groups to filter and aggregate in GDD data
  * ``config.health.reference_year``: Year for dietary intake data

**Output**:
  * ``processing/{name}/dietary_intake.csv`` (Final merged file)

**Scripts**:
  * ``workflow/scripts/prepare_gdd_dietary_intake.py``
  * ``workflow/scripts/prepare_faostat_dietary_intake.py``
  * ``workflow/scripts/merge_dietary_sources.py``

Baseline diet enforcement in the optimization can be toggled via
``config.validation.enforce_baseline_diet``. When enabled, the solver reads
``processing/{name}/baseline_diet.csv`` and adds per-food, per-country equality
constraints on food consumption links, forcing the solution to replicate
observed intake at the individual food level.

References
----------

.. [GDD2024] Global Dietary Database. Dietary intake data by country, 2018. Tufts University Friedman School of Nutrition Science and Policy. https://www.globaldietarydatabase.org/ (accessed 2025)

.. [Miller2021] Miller V, Singh GM, Onopa J, et al. Global Dietary Database 2017: Data Availability and Gaps on 54 Major Foods, Beverages and Nutrients among 5.6 Million Children and Adults from 1220 Surveys Worldwide. *BMJ Global Health*, 2021;6(2):e003585. https://doi.org/10.1136/bmjgh-2020-003585
