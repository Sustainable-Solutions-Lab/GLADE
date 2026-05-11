.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

Model Implementation
~~~~~~~~~~~~~~~~~~~~

Macronutrients are realised in the PyPSA network as a per-country
**store** for each nutrient, fed by **food-consumption links** that
convert food flows (Mt/year) into nutrient flows. The construction
happens in ``workflow/scripts/build_model/nutrition.py``; the bounds
themselves are added at solve time in
``workflow/scripts/solve_model/core.py``.

**Nutrient buses and stores.** ``add_macronutrient_loads`` creates one
bus ``nutrient:{nutrient}:{country}`` and one extendable store
``store:nutrient:{nutrient}:{country}`` for every configured nutrient
and country. The store carrier name equals the nutrient (e.g.
``protein``); its ``unit`` (``Mt`` for mass nutrients, ``PJ`` for
energy) tells downstream code how to convert per-capita requirements
into network units.

**Food → nutrient conversion.** ``add_food_nutrition_links`` adds one
multi-output link ``consume:{food}:{country}`` per food and country,
with ``bus0 = food:{food}:{country}`` and additional output buses for
every nutrient. The efficiency on bus *i* equals the food's content of
nutrient *i*, taken from ``data/curated/nutrition.csv`` (USDA FDC SR
Legacy values per 100 g) and rescaled by
``_nutrition_efficiency_factor`` so that food flows in Mt/year map onto
the carrier units above. Foods listed in ``byproducts.include`` are
excluded from these links so they cannot enter human consumption.

**Bounds on store level.** Because the network is solved over a single
representative snapshot ``now``, the macronutrient store level
``Store-e`` equals the annual nutrient throughput. ``add_macronutrient_constraints``
adds one constraint per nutrient and per country to the linopy model,
selecting ``Store-e`` for the matching nutrient stores and comparing it
to a population-scaled RHS. The RHS conversion is

.. math::

   \text{rhs}_{n,c} =
   \begin{cases}
     \dfrac{r_n \cdot p_c \cdot 365}{10^{12}} & \text{(mass nutrients, Mt/year)} \\[6pt]
     r_n \cdot p_c \cdot 365 \cdot K_{\mathrm{kcal\rightarrow PJ}} & \text{(energy, PJ/year)}
   \end{cases}

where :math:`r_n` is the per-capita daily requirement (g/day or
kcal/day), :math:`p_c` is the population of country :math:`c` (persons),
and :math:`K_{\mathrm{kcal\rightarrow PJ}} = 4.184 \times 10^{-12}`.
Configuration entries map to operators as

* ``min: x`` → ``Store-e ≥ rhs``
* ``max: x`` → ``Store-e ≤ rhs``
* ``equal: x`` or ``equal_to_baseline: true`` → ``Store-e == rhs``

Equality constraints silence any ``min``/``max`` on the same nutrient;
``equal_to_baseline`` uses each country's baseline per-capita intake
(``_compute_baseline_macronutrient_by_country``) as the RHS instead of a
global value, so countries hold their own current diet on that
nutrient.

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

Foods are assigned to groups in ``data/curated/food_groups.csv`` (one
``food,group`` row per food). Unmapped foods bypass the group buses
entirely — their nutrients still count, but they are unconstrained at
the group level.

Model Implementation
~~~~~~~~~~~~~~~~~~~~

Food groups mirror the macronutrient pattern but route a *mass* of food
to a per-country store instead of a nutrient mass:

1. **Group buses and stores.** ``add_food_group_buses_and_loads``
   (``workflow/scripts/build_model/nutrition.py``) creates a bus
   ``group:{group}:{country}`` and an extendable store
   ``store:group:{group}:{country}`` (carrier ``group_{group}``) for
   every included group. When ``food_groups.max_per_capita`` is set,
   the store's ``e_nom_max`` is pre-clamped to the corresponding
   Mt/year cap (g/person/day × population × 365 / 10¹²), so the
   network rejects infeasible diets up-front.
2. **Food → group routing.** The same multi-link that carries food into
   the nutrient buses (``consume:{food}:{country}``) also adds an extra
   output bus ``group:{group}:{country}`` with efficiency 1, looked up
   from ``food_groups.csv``. One unit of food therefore deposits one
   unit of mass on its group's store, in addition to its nutrient
   contributions.
3. **Population-scaled bounds.** ``add_food_group_constraints``
   (``workflow/scripts/solve_model/core.py``) selects the
   ``Store-e`` variables for each ``group_{group}`` carrier and adds
   one linopy constraint per country:

   .. math::

      \text{Store-e}_{g,c}\;\{\le,\ge,=\}\;\frac{r_g \cdot p_c \cdot 365}{10^{12}}\quad [\text{Mt/year}]

   with operator chosen from ``min``/``max``/``equal`` in the config.
   As with macronutrients, an ``equal`` bound silences ``min``/``max``
   for that group.
4. **Per-country equality from the baseline diet.** When the diet
   module is configured to anchor a group to current per-country
   consumption (``diet.enforce_baseline`` or an equality CSV), the
   solver builds a ``per_country_equal`` mapping
   ``{group: {country: g/person/day}}`` from the baseline diet and
   feeds it to ``add_food_group_constraints``. The equality RHS then
   uses the country-specific value instead of a global one — useful
   when the goal is *to hold today's group mix fixed and let the model
   choose within-group composition*.

This setup keeps dietary diversity decoupled from macronutrient
adequacy: the optimizer can satisfy energy/protein/fat from a narrow
set of foods only if no binding group ``min`` (e.g. fruits, vegetables)
prevents it.

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
