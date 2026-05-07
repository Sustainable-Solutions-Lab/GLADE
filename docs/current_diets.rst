.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/baseline_diet_by_region.png
   :alt: Baseline diet composition by world region
   :width: 100%
   :align: center

   Population-weighted mean food group consumption (g/person/day) by UN M49 macro-region, showing how dietary patterns vary across world regions.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/baseline_diet_by_food.png
   :alt: Baseline diet breakdown by individual foods
   :width: 100%
   :align: center

   Global population-weighted mean consumption (g/person/day) broken down by individual foods within each food group.

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

* **Fresh vegetables and fruits**: Reported in fresh weight (e.g., a fresh banana, fresh tomato)
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
     - v09, v10
     - Unprocessed red meats (cattle, pig) plus total processed meats. v09 (processed meats) is folded into ``red_meat`` because the model has no separate processed-meat food group, while FAOSTAT animal production accounts for cattle/pig at slaughter (i.e. before the cured/processed split). Routing v09 into ``red_meat`` closes the consumption-vs-production leak that otherwise shows up in emissions and feed accounting. The health module's ``red_meat`` risk function -- calibrated against unprocessed red meat per GBD -- becomes a slight conservative approximation as a consequence (see :doc:`health`).
   * - ``eggs``
     - v12
     - Eggs. Processed from GDD for reference, but the merged baseline diet currently overrides this group with FAOSTAT Food Balance Sheet supply for validation consistency.
   * - ``sugar``
     - v35
     - Added sugars (% of daily energy intake). v15 sugar-sweetened beverages is intentionally excluded; v35 already accounts for SSB-derived sugar, so summing the two would double-count beverage-derived sugar.
   * - ``coffee-green``
     - *(none)*
     - Not covered by GDD. GDD v17 data was found to be unreliable for many countries (e.g. India: 42× overestimate vs FAOSTAT). Sourced via FAOSTAT FBS override (``fbs_override_foods``).
   * - ``tea-dried``
     - v18
     - Tea. GDD reports in cups/day (brewed beverage); the script converts to **dry commodity weight** using a configured factor (default: 2.4 g-dry/cup). Passed through as a direct per-food value, bypassing food group aggregation.
   * - ``cocoa-powder``
     - *(none)*
     - Not covered by GDD. Sourced via FAOSTAT FBS override (``fbs_override_foods``).

**Notes:**

* Multiple GDD variables can map to a single food group (e.g., starchy_vegetable = v03 potatoes + v04 other starchy veg)
* When aggregating, values are summed within each food group
* The ``fruits`` food group uses only v01 (whole fruits), excluding v16 (fruit juices), to align with the GBD fruit risk factor definition used in health impact modeling
* GDD also tracks fish/seafood (v11), but fish is not currently modelled as a food group

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
   * - ``eggs``
     - **Eggs** (2744).
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
2. **Prepare FAOSTAT Data** (``workflow/scripts/prepare_faostat_food_group_supply.py``): Fetches FAOSTAT supply data for dairy, eggs, poultry, and oil; converts supply to intake by subtracting waste; fills missing countries using proxies.
3. **Merge Sources** (``workflow/scripts/merge_dietary_sources.py``): Combines the datasets into a unified ``dietary_intake.csv``.

The GDD processing step (Step 1) performs the following:

1. **Load GDD files**: Read country-level CSV files (``v*_cnty.csv``) for each dietary variable
2. **Filter to reference year**: Extract data for ``config.health.reference_year`` (default: 2018)
3. **Map age groups**: Convert GDD age midpoints to GBD-compatible age buckets (0-1, 1-2, 2-5, 6-10, 11-74, 75+ years)
4. **Aggregate strata**: Use GDD's pre-computed population-weighted national aggregate rows (``female=999``, ``urban=999``, ``edu=999``) rather than a simple mean across all demographic strata, which would ignore stratum sizes. Falls back to simple mean only when aggregate rows are absent.
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
  * ``g/day (refined sugar eq)``: Refined sugar equivalent for the sugar food group

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

GBD Dietary Risk Exposure Data
------------------------------

In addition to the GDD survey data and FAOSTAT supplements described above,
the model also incorporates dietary exposure estimates from the **Global Burden
of Disease (GBD) Study 2019** [Brauer2024]_. These estimates cover adults aged
25 and older and are derived from the GBD's dietary risk factor framework (see
the "GBD Dietary Risk Factors" section in :doc:`health` for the full risk
factor definitions).

The GBD data provides country-level intake estimates (g/day) for the following
food groups:

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - GBD Risk Factor
     - Model Food Group
     - Notes
   * - ``FRUIT``
     - ``fruits``
     - Whole fruits, excluding juices
   * - ``VEG``
     - ``vegetables``
     - Non-starchy vegetables
   * - ``WHOLEGRAINS``
     - ``whole_grains``
     - Whole grains
   * - ``LEGUMES``
     - ``legumes``
     - Beans and pulses
   * - ``NUTS``
     - ``nuts_seeds``
     - Nuts, seeds, and peanuts
   * - ``REDMEAT``
     - ``red_meat``
     - Unprocessed red meats
   * - ``MILK``
     - *(cross-validation only)*
     - Logged for comparison against FAOSTAT dairy

These six food groups (excluding milk) overlap with GDD estimates, enabling
both cross-validation and averaging to produce more robust group totals. The
GBD data is processed by ``workflow/scripts/prepare_gbd_food_group_intake.py``,
which filters the raw IHME CSV files for the configured reference year, maps
GBD location names to ISO3 country codes, and outputs
``processing/{name}/gbd_food_group_intake.csv``.

.. _baseline-diet-estimation:

Baseline Diet Estimation
------------------------

The dietary intake pipeline described above produces **food-group-level**
totals (e.g., "fruits: 145 g/day in the USA"). The model, however, operates
at the level of individual foods (e.g., banana, citrus). The baseline
diet estimation algorithm bridges this gap by combining food-group totals with
FAOSTAT item-level supply data to produce **per-food, per-country** consumption
estimates.

This algorithm is implemented in ``workflow/scripts/estimate_baseline_diet.py``
and proceeds in four steps.

Step 1: Food Group Totals
~~~~~~~~~~~~~~~~~~~~~~~~~

For food groups where both GDD and GBD estimates are available, the two
sources are **averaged** to produce a more robust estimate:

.. math::

   T_g = \frac{T_g^{\mathrm{GDD}} + T_g^{\mathrm{GBD}}}{2}

This averaging applies to six food groups: ``fruits``, ``vegetables``,
``whole_grains``, ``legumes``, ``nuts_seeds``, and ``red_meat``. If GBD data
is missing for a particular country, the GDD value is used alone.

For all other food groups (``dairy``, ``poultry``, ``oil``, ``grain``,
``starchy_vegetable``, ``eggs``, ``sugar``),
the GDD or FAOSTAT value from ``dietary_intake.csv`` is used as-is.

For the United States specifically, NHANES "What We Eat in America" /
FPED values (parsed from the USDA ARS demographic-table PDF) take
precedence over both GDD and FAOSTAT for every food group they cover
(fruits, vegetables, starchy vegetables, refined and whole grains,
dairy, eggs, oils, red meat, poultry, nuts and seeds, legumes, sugar).
NHANES Total Dairy is the low-fat / skim-equivalent fraction (FPED
strips butterfat into a separate Solid Fats axis), so the script also
adds country-specific FAOSTAT FBS butter (item 2740) as a milk-equivalent
top-up to give a complete dairy mass. NHANES Cured Meat is folded into
``red_meat`` (matching the GDD v09 fold) and FPED Fruit Juice is counted
as fresh-fruit-equivalent under ``fruits``. See :doc:`data_sources` for
the curated unit-conversion table.

For stimulants, only ``tea-dried`` uses GDD data directly (v18, converted from
cups/day to dry weight). ``coffee-green`` and ``cocoa-powder`` are both sourced
from FAOSTAT FBS via ``fbs_override_foods``. GDD v17 (coffee) was excluded
because it overestimates consumption for many countries (e.g. 42× for India).

The script also logs **cross-validation metrics** between GDD and GBD for the
overlapping groups, reporting the median and range of the GDD/GBD ratio across
countries. GBD milk intake is logged separately for comparison against the
FAOSTAT-derived dairy estimate.

The age group used for baseline totals is configured via
``config.diet.baseline_age`` (default: ``"11-74 years"``).

Step 2: Within-Group Food Shares
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once food-group totals are established, the algorithm determines how to
distribute each group's total across its constituent foods. This uses
**FAOSTAT FBS item-level supply** data to compute relative proportions.

Food-to-FBS-item mapping
^^^^^^^^^^^^^^^^^^^^^^^^^

The mapping between model foods and FBS items is defined in
``data/curated/faostat_food_item_map.csv``. Key characteristics:

* A food can map to **multiple FBS items** (supplies are summed). For example,
  ``citrus`` maps to four FBS items: Oranges/Mandarines (2611),
  Lemons/Limes (2612), Grapefruit (2613), and Citrus Other (2614).

* Multiple foods can share a **single FBS item** (requiring disambiguation).
  For example, ``cowpea``, ``chickpea``, ``gram``, ``phaseolus-bean``, and
  ``pigeon-pea`` all map to FBS item "Beans" (2546).

Three resolution scenarios arise when computing within-group shares from FBS supply:

1. **Unique mapping**: The food is the sole claimant of its FBS item and
   receives 100% of that item's supply.

2. **Shared FBS item with QCL resolution**: Multiple foods share an FBS item.
   Country-level production data from FAOSTAT QCL (production) statistics is
   used to split the shared supply proportionally (see below).

3. **No QCL mapping**: If no production data is available, the shared supply
   is split equally among the unresolved foods.

QCL-based disambiguation
^^^^^^^^^^^^^^^^^^^^^^^^^

When multiple model foods map to the same FBS item, their relative consumption
shares are estimated from **country-level production** data. The file
``data/curated/faostat_food_qcl_resolution.csv`` maps each such food to a
specific QCL (production statistics) item code.

.. admonition:: Example

   ``cowpea``, ``chickpea``, ``gram``, ``phaseolus-bean``, and ``pigeon-pea``
   all map to FBS item "Beans" (2546). The QCL resolution file maps each to a
   distinct production item:

   - ``cowpea`` → QCL 195 (Cow peas, dry)
   - ``phaseolus-bean`` → QCL 176 (Beans, dry)
   - ``pigeon-pea`` → QCL 197 (Pigeon peas, dry)
   - ``chickpea`` → QCL 191 (Chick peas, dry)
   - ``gram`` → QCL 191 (Chick peas, dry)

   In a country where cow pea production is 2 Mt and chickpea production is
   1 Mt (with no other pulses), cowpea would receive 2/3 of the "Beans" supply
   while chickpea and gram together receive 1/3 (split equally between them,
   since they share QCL code 191).

The same approach is used for dairy: ``dairy`` (cattle milk) and
``dairy-buffalo`` (buffalo milk) share FBS item 2848 and are resolved via
QCL items 882 and 951 respectively.

Millet species split
^^^^^^^^^^^^^^^^^^^^^

Pearl millet (``pearl-millet``) and foxtail millet (``foxtail-millet``) both
map to FBS item 2517 ("Millet and products") and to the same QCL aggregate
"Millet", which does not distinguish species. Since FAO lacks species-level
millet production data, a **fixed 80/20 split** is applied globally: 80% pearl
millet, 20% foxtail millet, based on literature estimates of global production
shares.

Vegetable residual projection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

FAOSTAT FBS item 2605 ("Vegetables, other") is a **residual catch-all**
covering all vegetables not individually itemized. Tomato has its own explicit
FBS item (2601) and is unaffected by the residual.

The residual supply is distributed across the model's "other vegetable group"
(OVG) crops — ``onion``, ``cabbage``, and ``carrot`` — using **blended
production shares**:

.. math::

   s_{c,f} = 0.7 \times s_{c,f}^{\mathrm{country}} + 0.3 \times s_f^{\mathrm{global}}

where :math:`s_{c,f}^{\mathrm{country}}` is the country-specific production
share of crop :math:`f` among the three OVG crops, and
:math:`s_f^{\mathrm{global}}` is the global production share. The 70/30
blending avoids over-reliance on country-level data, which can be noisy for
small producers.

.. admonition:: Note

   ``onion`` also has an explicit FBS item (2602, "Onions"), so its total
   supply is the sum of its explicit item supply and its projected share of
   the residual. ``cabbage`` and ``carrot`` rely entirely on the residual
   projection.

Starchy vegetable residual projection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

An analogous residual exists for starchy vegetables: FBS item 2534 ("Roots,
Other") covers root and tuber crops not individually itemized. The four
modeled starchy foods — ``potato``, ``sweet-potato``, ``yam``, and
``cassava`` — each have their own explicit FBS items (2531, 2533, 2535, and
2532 respectively), but the residual supply from item 2534 is additionally
distributed across all four using the same blended production share approach
(70% country-specific, 30% global).

.. admonition:: Note

   Because the crop production data uses the name ``white-potato`` while the
   model food is called ``potato``, the projection applies a name mapping
   before computing production shares.

Nuts/seeds residual projection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

FBS item 2551 ("Nuts and products") is a residual catch-all for tree nuts and
seeds not individually itemized. The residual supply is distributed across
``groundnut``, ``sesame-seed``, ``coconut``, and ``sunflower-seed`` using the
same 70/30 blended production share approach. Other nuts/seeds foods with
their own explicit FBS items are unaffected.

Step 3: Per-Food Consumption
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The final per-food consumption estimate is the product of the food-group total
and the within-group share:

.. math::

   c_{i,f} = T_{i,g(f)} \times s_{i,f}

where :math:`c_{i,f}` is the estimated consumption (g/day) of food :math:`f`
in country :math:`i`, :math:`T_{i,g(f)}` is the group total for the food group
containing :math:`f`, and :math:`s_{i,f}` is the within-group share.

As a validation check, within-group sums are verified to match group totals
within a tolerance of 0.1 g/day. Any discrepancies are logged as warnings.

Step 4: FBS Supply Overrides
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For specific foods, the per-food estimate from Step 3 is replaced with
**FAOSTAT Food Balance Sheet supply** data converted to consumer-eaten
intake mass. The list of overridden foods is configured via
``config.diet.fbs_override_foods``. The replacement formula is

.. math::

   c_{i,f} = \frac{S_{i,f} \times \sigma_{i,f} \times r_f \times 1000}
                  {365}
            \times (1 - \ell_{i,g(f)}) \times (1 - w_{i,g(f)})

where

* :math:`S_{i,f}` is the FAOSTAT FBS supply (kg/capita/year) summed
  across the food's FBS item codes (carcass weight equivalent for meat),
* :math:`\sigma_{i,f}` is the within-FBS-item share (1.0 unless several
  override foods share an FBS code, in which case the supply is split by
  country-level QCL production weights — e.g. dairy/dairy-buffalo both
  map to FBS 2848 "Milk - Excluding Butter"),
* :math:`r_f` is the carcass-to-retail factor (0.67 cattle, 0.73 pig,
  0.66 sheep, 0.60 chicken; 1.0 for non-meat foods),
* :math:`\ell_{i,g(f)}` and :math:`w_{i,g(f)}` are the country- and
  group-level supply-chain loss and consumer waste fractions from
  ``processing/{name}/food_loss_waste.csv``.

The :math:`(1-\ell)(1-w)` multiplier mirrors exactly the FLW correction
that the build_model ``animal_production`` and ``food_processing`` links
apply on the production side, so the resulting baseline diet is on the
**post-FLW intake basis** that the food bus delivers — the two sides
mass-balance at baseline.

.. admonition:: Why yam needs an override

   GDD starchy vegetable intake for sub-Saharan Africa is 7–33× below FAOSTAT
   food supply (e.g., Nigeria: GDD ≈ 72 g/day vs. FBS ≈ 700 g/day for
   starchy vegetables). Because yam production is almost entirely concentrated
   in West Africa, the GDD underestimate translates directly to a ~10×
   underestimate of yam demand. The within-group shares are correct — the
   problem is entirely in the GDD group total for starchy vegetables in these
   countries. Overriding yam consumption with FBS supply ensures that the
   model's demand matches observed food availability.

.. _animal-source-selection:

.. admonition:: Why animal products use FBS, not GDD

   For meats, poultry, and eggs the per-food intake is anchored to
   FAOSTAT FBS supply rather than the GDD-disaggregated group total.
   Three reasons:

   1. **Survey bias on socially significant foods.** Self-reported food
      intake systematically over-reports red meat (cattle, pig, sheep)
      against slaughter-volume supply in many populations. GDD harmonises
      survey data across studies but does not reconcile against
      production. With processed-meat (GDD v09) folded into ``red_meat``
      to match FAOSTAT cattle/pig slaughter mass, the GDD-based total
      for red meat sat ~24 Mt/yr above what total world supply
      (production net of feed/non-food/exports, after post-loss and
      consumer waste) can deliver — physically impossible. Validation
      slacks under ``enforce_baseline_diet=true`` exposed this as a
      +17.8% positive food slack on red_meat, which inflated the
      calibrated ``animal_feed_l1_cost`` ninefold (0.034 → 0.299 bn USD
      per Mt DM) because the production-stability calibration had to
      fight intake-derived consumer values that were structurally above
      supply.

   2. **Trade is handled implicitly.** FBS supply per country already
      encodes
      ``production + imports − exports − feed − seed − non-food
      − stock_changes``,
      so country-level diet automatically reflects the importer/exporter
      pattern (Japan, China, Korea import; USA, Brazil, Australia
      export). The model's trade hubs then have to reproduce only the
      observed FAOSTAT trade flows at solve time, instead of resolving a
      mismatch between intake-based diet and slaughter-based production
      via expensive feed-deviation L1 penalties.

   3. **Same FAOSTAT backbone as production.** Baseline animal
      production (``processing/{name}/faostat_animal_production.csv``)
      is built from QCL element 5510 with ``carcass_to_retail_meat``
      applied. FBS items aggregate the same QCL primary commodities at
      the carcass-weight balance level. Anchoring both sides to FAOSTAT
      removes a class of unit/source mismatches that previously surfaced
      only as residual slack after solve.

   **Dairy is intentionally excluded** from the override list. Its
   ``food_loss_waste`` convention is non-standard — the curated dairy
   override sets ``loss_fraction=0`` and ``waste_fraction=0.30``, where
   the 30% lumps in *non-food uses of raw milk* (calf feed, processing,
   industrial) plus retail and consumer waste, because the model does
   not have an explicit non-food milk outlet. Under that convention the
   GDD-based dairy total of ~645 Mt/yr happens to mass-balance against
   the production-side ``QCL × 0.7 ≈ 643 Mt/yr`` delivered to the food
   bus. Switching dairy to an FBS override would break that balance
   (FBS supply is post-non-food-use and would imply a 30% surplus on
   the food bus). If the dairy chain ever gets an explicit non-food
   outlet, both ``food_loss_waste_overrides.csv`` and
   ``fbs_override_foods`` should be revisited together.

Baseline Diet Output
---------------------

The output file ``processing/{name}/baseline_diet.csv`` contains one row per
(country, food) combination with the following columns:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Column
     - Description
   * - ``country``
     - ISO 3166-1 alpha-3 country code
   * - ``food``
     - Model food name (e.g., ``banana``, ``rice-white``, ``cowpea``)
   * - ``food_group``
     - Food group to which the food belongs
   * - ``consumption_g_per_day_intake``
     - Estimated daily consumption in grams per person, on
       **post-loss, post-waste consumer-eaten intake basis**. The
       ``_intake`` suffix flags the weight basis explicitly (see
       :ref:`weight-bases`); the value is on the same basis as what the
       food bus delivers after the build_model FLW multiplier, so
       diet and supply mass-balance at baseline.

Rows are sorted by (country, food_group, food).

Downstream Uses
~~~~~~~~~~~~~~~~

The baseline diet feeds into several parts of the model:

* **Baseline diet enforcement**: When ``config.validation.enforce_baseline_diet``
  is enabled, the solver adds per-food, per-country equality constraints on
  food consumption links, forcing the solution to replicate observed intake.

* **Within-group ratio fixing**: When ``config.food_groups.fix_within_group_ratios``
  is enabled, the solver constrains foods within each group to maintain their
  baseline proportions while allowing group totals to vary.

* **Piecewise consumer utility calibration**: In the consumer-values workflow,
  baseline per-food consumption and baseline food-equality duals are combined to
  calibrate ``results/{name}/consumer_values/utility_blocks.csv``. These blocks
  are then used in the solve objective when
  ``config.food_utility_piecewise.enabled`` is true.

  The current calibration anchors utility at baseline quantity: the block
  containing baseline consumption uses the extracted dual value, while blocks
  below baseline are more valuable and blocks above baseline are less valuable.

* **Health impact assessment**: Baseline consumption is used when computing
  the population-attributable fraction of diet-related disease burden (see
  :doc:`health`).

Workflow Integration
--------------------

**Snakemake rules**:
  * ``prepare_gdd_dietary_intake``
  * ``prepare_faostat_food_group_supply``
  * ``merge_dietary_sources``
  * ``prepare_gbd_food_group_intake``
  * ``estimate_baseline_diet``

**Input data**:
  * ``data/manually_downloaded/GDD-dietary-intake/Country-level estimates/*.csv`` (GDD)
  * ``data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA/*.csv`` (GBD)
  * FAOSTAT API (live fetch for FBS, QCL, and animal production data)

**Curated data files**:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - File
     - Purpose
   * - ``data/curated/faostat_food_item_map.csv``
     - Maps model foods to FAOSTAT FBS item codes for within-group share calculation
   * - ``data/curated/faostat_food_qcl_resolution.csv``
     - Maps foods sharing an FBS item to individual QCL production codes for disambiguation
   * - ``data/curated/food_groups.csv``
     - Defines the food-to-food-group mapping

**Configuration parameters**:
  * ``config.countries``: List of countries to process
  * ``config.food_groups.included``: Food groups to filter and aggregate
  * ``config.baseline_year``: Reference year for GDD dietary intake and GBD exposure data
  * ``config.diet.baseline_age``: Age group for baseline totals (default: ``"11-74 years"``)
  * ``config.diet.fbs_override_foods``: Foods whose consumption is anchored
    to FBS supply (default: ``yam``, ``cocoa-powder``, ``coffee-green``,
    ``meat-cattle``, ``meat-pig``, ``meat-sheep``, ``meat-chicken``,
    ``eggs``). See :ref:`Why animal products use FBS, not GDD
    <animal-source-selection>` for the rationale on animal products and
    the deliberate exclusion of dairy.
  * ``config.byproducts``: Foods to exclude from share calculation (e.g., wheat-bran)

**Output**:
  * ``processing/{name}/dietary_intake.csv`` — Merged food-group-level intake
  * ``processing/{name}/gbd_food_group_intake.csv`` — GBD risk exposure estimates
  * ``processing/{name}/baseline_diet.csv`` — Per-food, per-country consumption

**Scripts**:
  * ``workflow/scripts/prepare_gdd_dietary_intake.py``
  * ``workflow/scripts/prepare_faostat_food_group_supply.py``
  * ``workflow/scripts/merge_dietary_sources.py``
  * ``workflow/scripts/prepare_gbd_food_group_intake.py``
  * ``workflow/scripts/estimate_baseline_diet.py``

References
----------

.. [GDD2024] Global Dietary Database. Dietary intake data by country, 2018. Tufts University Friedman School of Nutrition Science and Policy. https://www.globaldietarydatabase.org/ (accessed 2025)

.. [Miller2021] Miller V, Singh GM, Onopa J, et al. Global Dietary Database 2017: Data Availability and Gaps on 54 Major Foods, Beverages and Nutrients among 5.6 Million Children and Adults from 1220 Surveys Worldwide. *BMJ Global Health*, 2021;6(2):e003585. https://doi.org/10.1136/bmjgh-2020-003585

.. Reference [Brauer2024] is defined in health.rst (Sphinx citations are global).
