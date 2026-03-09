.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Food Processing & Trade
========================

Food Processing
---------------

Overview
~~~~~~~~

The food processing module converts raw agricultural products (crops and animal products) into final food products consumed by the population. This captures:

* **Multi-output processing**: Single crops can produce multiple co-products (e.g., wheat → white flour + bran + germ)
* **Alternative pathways**: Different processing options for the same crop (e.g., white flour vs. wholemeal flour from wheat)
* **Mass balance**: Processing losses and byproducts are explicitly tracked
* **Unit conversion**: Conversion from dry matter (DM) to fresh weight as consumed (configurable per food via ``mass_basis``)

Processing is represented in the model as PyPSA multi-output links with crop buses as inputs and multiple food buses as outputs. Each pathway creates one link per country, with efficiencies adjusted for food loss and waste factors.

Data Files
~~~~~~~~~~

The two files below, created and distributed for internal ``food-opt`` use, define possible food processing pathways and food groups.

**data/curated/foods.csv**
  Defines crop-to-food processing pathways using a pathway-based format that supports multi-output processing. Each pathway can convert one crop into one or more food products, with conversion factors maintaining mass balance.

  Columns:

  * ``pathway``: Unique identifier for the processing pathway (e.g., ``white_flour``, ``milled_rice``)
  * ``crop``: Input crop name (must match config crops list)
  * ``food``: Output food product name
  * ``factor``: Conversion factor (mass of food output per unit mass of crop input)
  * ``mass_basis``: Either ``fresh`` (default) or ``dry``. When ``fresh``, the standard DM-to-fresh conversion (``edible_portion / (1 - moisture)``) is applied to the pathway efficiency. When ``dry``, no moisture re-inflation is applied — the food is already a dry commodity (e.g., dried tea, cocoa powder, green coffee beans) and pathway factors represent DM-to-DM ratios.
  * ``description``: Explanation of the conversion and source reference

  **Multi-output pathways**: Multiple rows with the same pathway name represent co-products from a single processing operation. For example, the ``white_flour`` pathway produces flour-white (0.75), wheat-bran (0.20), and wheat-germ (0.03) from wheat, with factors summing to ≤ 1.0 to respect mass balance.

  **Alternative pathways**: Different pathways for the same crop represent processing alternatives that the model can choose between based on demand and costs. For example, wheat can be processed via ``white_flour`` or ``wholemeal_flour`` pathways.

  **Sugar and oil crops**: Yields for sugarcane, sugarbeet, and oil-palm are first converted back to whole-crop dry matter (see :doc:`crop_production`) and then uplifted to fresh mass using the moisture table. Pathway factors therefore reflect *fresh* extraction efficiencies: 0.24 for palm oil (24 % oil from fresh fruit bunches), 0.14 for cane sugar, and 0.10 for beet sugar.

  **Cotton ginning**: Cotton is processed via the ``cotton_ginning`` pathway, which produces three outputs from seed cotton: cotton lint (0.38), cottonseed oil (0.083), and oilseed meal (0.275). Cotton lint is a non-food byproduct routed to per-country fiber demand stores (see :doc:`configuration`); cottonseed oil enters the food system as an edible oil; oilseed meal enters the generic oilcake pool available as animal feed.

  .. note::

     When ``validation.use_actual_yields`` is true, the sugarcane, sugarbeet, and oil-palm rasters already deliver whole-crop fresh mass, so the workflow skips the conversion above and relies on the moisture table to convert to dry matter before applying extraction factors.

**data/curated/food_groups.csv**
  Maps foods to food groups for dietary constraint aggregation and health impact assessment. Each food must be assigned to exactly one food group.

  Columns:

  * ``food``: Food product name (must match foods produced in ``data/curated/foods.csv``)
  * ``group``: Food group identifier (e.g., ``grain``, ``whole_grains``, ``legumes``, ``oil``, ``byproduct``)

  **Coverage**: This file must include all foods that can be produced according to ``data/curated/foods.csv`` pathways, including byproducts (bran, meal, hulls, etc.). Foods without group assignments will generate warnings and will not contribute to food group constraints or health impact calculations.

  **Food groups**: Standard groups include grains, whole_grains, legumes, nuts_seeds, oil, starchy_vegetable, fruits, vegetables, sugar, stimulants, byproduct, red_meat, prc_meat, poultry, dairy, and eggs. Fish/seafood is not currently modelled. Additional groups can be defined by extending ``config.food_groups.included``.

  **Byproduct handling**: Foods assigned to the ``byproduct`` group (such as wheat-bran, rice-bran, oat-bran, wheat-germ, oilseed-meal, rapeseed-meal, and buckwheat-hulls) are **excluded from direct human consumption**. Instead, these byproducts can be utilized as animal feed (see :ref:`byproduct-feed-conversion`), making them available for livestock production systems. Byproducts also have links to per-country ``biomass`` buses, providing a disposal route for byproducts that lack feed mappings; surplus can be exported to the energy sector at the configured ``biomass.marginal_values_usd_per_tonne`` (set to 0 for free disposal).

Food Loss & Waste Adjustments
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The workflow incorporates **food loss** (pre-retail) and **food waste** (retail & household) adjustments when converting crops to foods. Food loss and waste are measured and tracked by the UN under the Sustainable Development Goal 12.3. The FAO is responsible for preparing data on `food loss <https://openknowledge.fao.org/server/api/core/bitstreams/d420dd69-cf78-4464-ad91-115df3b5ed9f/content>`_, whereas the UNEP is responsible for preparing data on `food waste <https://www.unep.org/indicator-1231b>`_. Both are available through a UN Statistics Division `API <https://unstats.un.org/SDGAPI/swagger/>`_.

* ``workflow/scripts/prepare_food_loss_waste.py`` retrieves
  * SDG indicator 12.3.1 data (series ``AG_FLS_PCT`` and ``AG_FOOD_WST_PC``) from the UN Statistics Division API, using ISO-3 area codes.
  * Food Balance Sheets data (``FBS`` domain) from FAOSTAT to obtain country-level per-capita food supply.
* UNSD reports **food loss** as a percentage. Regional totals (``ALP`` product code) are available for M49 regions, while product-level breakdowns (``CRL_PUL``, ``FRT_VGT``, ``RT_TBR``, ``ANMPROD``) exist only for the global series. The script therefore:
  1. Pulls the latest world loss percentages by product type.
  2. Converts them into **correction factors** by dividing each product share by the global ``ALP`` total (e.g. fruits & vegetables ≈ 25 % / 13 % ≈ 1.9).
  3. Applies these factors to each country’s regional ``ALP`` percentage, yielding group-specific loss fractions for the model food groups.
* Food waste is reported as **kilograms per capita per year**. To convert this to a fraction of available food supply, the script retrieves the FAOSTAT FBS Grand Total item (kg/capita/year), converts both to grams/day, and computes ``waste_fraction = waste_g_day / supply_g_day``.
* The resulting dataset ``processing/{name}/food_loss_waste.csv`` lists, for every country and model food group, the derived **loss_fraction** and **waste_fraction**.

During ``build_model`` the crop→food conversion links multiply the baseline processing efficiency by ``(1 - loss_fraction) * (1 - waste_fraction)`` for the relevant country-food group pair. The same FLW adjustment is applied to animal product links (feed→dairy, feed→meat, etc.), ensuring consistent treatment across all food pathways.

**Dairy-specific loss**: The SDG data groups all animal products (meat, dairy, eggs) together under the ``ANMPROD`` category. However, dairy has significantly lower losses than meat due to better cold chain infrastructure and faster processing. To correct for this, the script compares FAOSTAT dairy production with food supply data to calculate an implicit dairy loss fraction, which is typically near zero. This FBS-derived loss fraction replaces the SDG value for dairy products.

Because all factors are multiplicative (dry matter → fresh mass → edible portion → usable food), their ordering does not affect the final efficiency.

Trade
-----

Overview
~~~~~~~~

The trade module enables inter-regional flows of crops and food products, subject to transport costs.

To avoid creating a complete graph of region-to-region links (entailing :math:`O(n^2)` links for :math:`n` regions), the model uses a **hub-based topology**:

1. **Country buses**: Each country has local crop/food buses
2. **Hub buses**: A small number of hub nodes (configured count)
3. **Hub connections**: Regions connect to nearest hubs; hubs connect to each other

This reduces links from :math:`O(n^2)` to :math:`O(n \times h + h^2)`, where :math:`n` = regions and :math:`h` = hubs.

Configuration
~~~~~~~~~~~~~

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: trade ---
   :end-before: # --- section: health ---

Trade Cost Categories
~~~~~~~~~~~~~~~~~~~~~

Transport costs differentiate by commodity handling requirements:

* **Bulk dry goods**: Cereals, legumes in containers/bulk carriers
* **Bulky fresh**: Potatoes, cassava, sugar beets
* **Perishable high-value**: Fruits, vegetables, sugarcane requiring refrigeration
* **Chilled meat**: Temperature-controlled meat transport

Hub Location
~~~~~~~~~~~~

Hub positions are determined by k-means clustering on region centroids:

1. Compute population-weighted centroid for each region
2. Run k-means with k = configured hub count
3. Assign each region to nearest hub
4. Create hub-hub distance matrix for hub-to-hub transport

This ensures hubs are spatially distributed to minimize total transport distance.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/trade_network.png
   :alt: Trade network topology
   :width: 100%
   :align: center

   Hub-based trade network showing trade hubs (green circles) and trade links: country-to-hub links (thin) and hub-to-hub links (thick).

Non-Tradable Commodities
~~~~~~~~~~~~~~~~~~~~~~~~

Certain products are designated non-tradable:

* **Fodder crops** (alfalfa, biomass sorghum) via ``trade.non_tradable_crops`` —
  too bulky/low-value to transport
* **Foods** listed in ``trade.non_tradable_foods`` (optional) — keeps fragile or
  policy-protected items local

Non-tradable crops or foods must be consumed (as food, feed, or byproducts) within
their production region.

Model Implementation
--------------------

Trade links are created in ``workflow/scripts/build_model.py``:

.. code-block:: python

   # Pseudocode
   for crop in tradable_crops:
       for region in regions:
           hub = nearest_hub(region)
           n.add("Link",
                 f"trade_{crop}_{region}_to_{hub}",
                 bus0=f"crop_{crop}_{region}",
                 bus1=f"crop_{crop}_hub{hub}",
                 p_nom=inf,  # No capacity limit
                 marginal_cost=distance * cost_per_km)

       for hub_i, hub_j in hub_pairs:
           n.add("Link",
                 f"trade_{crop}_hub{hub_i}_to_hub{hub_j}",
                 bus0=f"crop_{crop}_hub{hub_i}",
                 bus1=f"crop_{crop}_hub{hub_j}",
                 p_nom=inf,
                 marginal_cost=hub_distance * cost_per_km)

The same structure applies to all foods (including animal products and byproducts),
so every consumable item can flow through the hub network unless it is listed under
``trade.non_tradable_foods``.
