.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Livestock & Grazing
===================

Overview
--------

The livestock module models animal product production (meat, dairy, eggs) through two distinct production systems:

* **Grazing-based**: Animals feed on managed grasslands
* **Feed-based**: Animals consume crops as concentrated feed

Animal Products
---------------

The model includes seven major animal product categories configured in ``config/default.yaml``:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: animal_products ---
   :end-before: # --- section: food_groups ---

Each product can be produced via either production system, with different feed requirements and efficiencies.

Production Systems
------------------

Grazing-Based Production
~~~~~~~~~~~~~~~~~~~~~~~~

**Concept**: Animals graze on managed grasslands, converting grass biomass to animal products.

**Inputs**:
  * Land (per region and resource class, similar to cropland)
  * Managed grassland yields from ISIMIP LPJmL model

**Process**:
  1. Grassland yields (t dry matter/ha/year) are computed per region and resource class
  2. Feed conversion ratios translate grass biomass → animal products
  3. Land allocation to grazing competes with cropland expansion

**Configuration**: Enable/disable grazing with ``grazing.enabled: true``

Feed-Based Production
~~~~~~~~~~~~~~~~~~~~~

**Concept**: Animals consume crops (grains, soybeans, etc.) as concentrated feed.

**Inputs**:
  * Crops from crop production buses
  * Feed conversion ratios (kg crop → kg animal product)

**Process**:
  1. Crops are allocated to animal feed (competing with direct human consumption)
  2. Feed conversion links transform crop inputs to animal products
  3. Multiple crops can contribute (e.g., maize + soybean for poultry)

.. _grassland-yields:

Grassland Yields
----------------

Grazing supply is determined by managed grassland yields from the ISIMIP LPJmL historical simulation.

Data Source
~~~~~~~~~~~

**Dataset**: ISIMIP2b managed grassland yields (historical)

**Resolution**: 0.5° × 0.5° gridded annual yields

**Variable**: Above-ground dry matter production (t/ha/year)

**Processing**: ``workflow/scripts/build_grassland_yields.py``

Aggregation follows the same resource class structure as crops:

1. Load grassland yield NetCDF
2. Aggregate by (region, resource_class) using area-weighted means
3. Output CSV with yields in t/ha/year

Pasture Utilization
~~~~~~~~~~~~~~~~~~~

The model assumes that only a portion of the total grassland biomass production is available for grazing livestock. This reflects the need to leave biomass for regrowth, soil protection, and ecosystem function ("take half, leave half" principle). The correction is applied upstream in the ``merge_grassland_yields`` step so that the ``yield`` column in the merged output is already effective feed yield:

* **LUIcube rows**: yield is multiplied by the per-cell ``grazing_intensity`` from LUIcube data.
* **ISIMIP rows**: yield is multiplied by a fixed utilization rate (default 50%).
* **Parameter**: ``grazing.isimip_utilization_rate`` in configuration (applied to ISIMIP fallback yields).

This value is consistent with the **GLOBIOM** model, which assumes a 50% grazing efficiency for grass in native grasslands [3]_. While intensive dairy systems can achieve higher utilization (up to 70-80%), global rangeland management guidelines typically recommend utilization rates below 50% to prevent degradation.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/grassland_yield.png
   :alt: Managed grassland yield potential
   :width: 100%
   :align: center

   Global distribution of managed grassland yield potential (tonnes dry matter per hectare per year) from ISIMIP LPJmL historical simulations

.. _byproduct-feed-conversion:

Feed Conversion
---------------

The model uses feed conversion ratios to link feed inputs to animal outputs, with explicit categorization by feed quality to enable accurate CH₄ emissions tracking.

Feed System Architecture
~~~~~~~~~~~~~~~~~~~~~~~~

The feed system uses **nine distinct feed pools** that combine animal type with feed quality:

* **Ruminant pools**: ``ruminant_roughage``, ``ruminant_forage``, ``ruminant_grassland``, ``ruminant_grain``, ``ruminant_protein``
* **Monogastric pools**: ``monogastric_low_quality``, ``monogastric_grain``, ``monogastric_energy``, ``monogastric_protein``

This categorization enables the model to:

1. Differentiate methane emissions using GLEAM feed digestibility classes (roughage/forage vs. grain/protein)
2. Route crops, residues, and processing byproducts to appropriate feed pools based on nutritional properties
3. Model production system choices (e.g., roughage-dominated beef vs. high-grain finishing rations)
4. Distinguish between grazing (grassland) and confinement feeding systems for nitrogen management

Feed Properties (Generated from GLEAM)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Feed properties (digestibility, metabolizable energy, protein content) are automatically generated from GLEAM 3.0 data during workflow execution. The workflow produces two files in ``processing/{name}/``:

* ``ruminant_feed_properties.csv``: Properties for all feeds used by ruminants
* ``monogastric_feed_properties.csv``: Properties for all feeds used by monogastrics

Each file contains:

* ``feed_item``: Item name (e.g., "maize", "wheat-bran")
* ``source_type``: Either "crop" or "food" (byproduct)
* ``digestibility``: Digestible fraction (0-1)
* ``ME_MJ_per_kg_DM``: Metabolizable energy (MJ per kg dry matter)
* ``CP_pct_DM``: Crude protein (% of dry matter)
* ``ash_pct_DM``: Ash content (% of dry matter)
* ``NDF_pct_DM``: Neutral detergent fiber (% of dry matter)

These properties are extracted from the GLEAM 3.0 supplement using ``data/curated/gleam_feed_mapping.csv`` to map between model feed items and GLEAM feed categories.

**Feed quality categories** (assigned based on nitrogen content and digestibility):

* **Ruminant feeds**:

  * **Protein**: High nitrogen content (>50 g N/kg DM) - protein meals such as rapeseed-meal, sunflower-meal, soybean meal (assigned by N content; takes precedence over digestibility)
  * **Grassland**: Managed pasture grazing (special category for nitrogen management; manure deposited on pasture)
  * **Roughage**: Low digestibility (<0.55), high-fiber forages (crop residues, straw)
  * **Forage**: Medium digestibility (0.55-0.70), improved forages (silage maize, alfalfa)
  * **Grain**: High digestibility (0.70-0.90), energy concentrates (maize, wheat, barley)

* **Monogastric feeds**:

  * **Protein**: High nitrogen content (>35 g N/kg DM) - protein meals such as soybean meal, fish meal, rapeseed-meal (assigned by N content; takes precedence over energy)
  * **Low quality**: Low metabolizable energy (<11 MJ/kg DM), bulky feeds and byproducts
  * **Grain**: Medium energy (11-15.5 MJ/kg DM), cereal grains
  * **Energy**: High energy (>15.5 MJ/kg DM), fats and high-energy feeds

**Categorization logic**: Both ruminant and monogastric feeds prioritize nitrogen content to identify protein feeds, ensuring high-protein oilseed meals are correctly classified regardless of digestibility. For feeds below the nitrogen threshold, ruminants use digestibility ranges while monogastrics use metabolizable energy thresholds.

Byproducts from food processing (with ``source_type=food``) are automatically excluded from human consumption and can only be used as animal feed.

Feed Conversion Efficiencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Feed conversion efficiencies (tonnes **retail product** per tonne feed DM) are generated automatically from Wirsenius (2000) regional feed energy requirements combined with GLEAM 3.0 feed category energy values.

**Proxy Products**: Buffalo milk (``dairy-buffalo``) and sheep meat (``meat-sheep``) use cattle feed requirements as proxies, since Wirsenius (2000) does not provide separate regional estimates for these products. Buffalo milk inherits dairy cattle parameters, while sheep meat inherits beef cattle parameters with adjustments for the different carcass-to-retail conversion factor (0.63 for sheep vs 0.67 for cattle).

In this calculation, we have to account for the following units:
* **Feed inputs**: Dry matter (tonnes DM)
* **Animal product outputs**: Fresh weight, retail meat (tonnes fresh weight)

  * For meats: **retail/edible meat** weight (boneless, trimmed) - NOT carcass weight
  * For dairy: whole milk (fresh weight)
  * For eggs: whole eggs (fresh weight)

Wirsenius (2000) [1]_ provides feed requirements per kg **carcass weight** (dressed, bone-in). We apply carcass-to-retail conversion factors to obtain feed requirements per kg **retail meat**, from OECD-FAO Agricultural Outlook 2023-2032, Box 6.1 [2]_:

* Cattle meat: 0.67 kg boneless retail per kg carcass
* Sheep meat: 0.63 kg boneless retail per kg carcass
* Pig meat: 0.73 kg boneless retail per kg carcass
* Chicken meat: 0.60 kg boneless retail per kg carcass
* Eggs, dairy, & buffalo milk: 1.00 (no conversion, already retail products)

**Generation workflow**:

1. **Regional feed energy requirements** from Wirsenius (2000) provide MJ per kg **carcass** output for eight world regions
2. **Carcass-to-retail conversion**: Convert MJ per kg carcass → MJ per kg retail meat

   * For meats: ME_retail = ME_carcass / carcass_to_retail_factor
   * For dairy/eggs: No conversion (already retail products)

3. **Energy conversion for ruminants**: Net energy (NE) requirements converted to metabolizable energy (ME) using NRC (2000) efficiency factors:

   * k_m = 0.60 (maintenance)
   * k_g = 0.40 (growth)
   * k_l = 0.60 (lactation)

4. **Feed category energy content** from GLEAM 3.0 provides ME (MJ per kg DM) for each feed quality category
5. **Efficiency calculation**: efficiency = ME_feed / ME_retail (tonnes **retail product** per tonne feed DM)

**Output**: ``processing/{name}/feed_to_animal_products.csv`` with columns:

* ``country``: ISO 3166-1 alpha-3 country code
* ``product``: Product name (e.g., "meat-cattle", "dairy")
* ``feed_category``: Feed pool (e.g., ``ruminant_forage``, ``ruminant_grain``, ``monogastric_grain``)
* ``efficiency``: Feed conversion efficiency (t product / t feed DM)
* ``notes``: Description with inverse feed requirement

**Configuration**: The ``feed_efficiency_regions`` setting controls how feed conversion efficiencies are assigned:

.. code-block:: yaml

   animal_products:
     # Option 1: Average specific regions (all countries use same values)
     feed_efficiency_regions:
     - North America & Oceania
     - West Europe

     # Option 2: Use country-specific regional values (set to null)
     # feed_efficiency_regions: null

Available regions (from Wirsenius 2000): East Asia, East Europe, Latin America & Caribbean, North Africa & West Asia, North America & Oceania, South & Central Asia, Sub-Saharan Africa, West Europe

When ``feed_efficiency_regions`` is null, each country uses the feed conversion efficiencies from its geographic region. The mapping from countries to Wirsenius regions is defined in ``data/curated/country_wirsenius_region.csv``.

**Example efficiencies** (North America & Oceania + West Europe average, with carcass-to-retail conversion):

* Cattle meat from forage: ~0.026 t/t (~38 t DM feed per tonne retail beef)
* Cattle meat from grain: ~0.035 t/t (~28 t DM feed per tonne retail beef)
* Dairy from forage: ~0.480 t/t (~2.1 t DM feed per tonne milk)
* Pig meat from grain: ~0.110 t/t (~9.1 t DM feed per tonne retail pork)
* Chicken meat from grain: ~0.226 t/t (~4.4 t DM feed per tonne retail chicken)

Note: Carcass-to-retail conversion increases feed requirements per kg retail meat by ~33-50% compared to per kg carcass, reflecting bone removal and trimming losses.

This structure allows modeling different production systems for the same product (grass-fed vs. grain-finished beef, pasture vs. intensive dairy, etc.).

Regional Feed Energy Requirements
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Feed requirements vary significantly by region due to differences in production systems, genetics, and environmental conditions. Wirsenius (2000) [1]_ provides estimated feed energy requirements per unit of commodity output:

.. table:: Feed energy requirements per unit of animal product output (Wirsenius 2000, Table 3.9)
   :widths: auto

   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Commodity                     | Unit   | East     | East     | Latin     | North     | North     | South &   | Sub-      | West      |
   |                               |        | Asia     | Europe   | America   | Africa &  | America   | Central   | Saharan   | Europe    |
   |                               |        |          |          | & Carib.  | W. Asia   | & Oc.     | Asia      | Africa    |           |
   +===============================+========+==========+==========+===========+===========+===========+===========+===========+===========+
   | Cattle milk & cow carcass     | NE_l   | 8.2      | 8.2      | 11        | 12        | 5.3       | 11        | 23        | 5.6       |
   | (MJ per kg whole milk &       | NE_m   | 2.3      | 1.3      | 1.9       | 2.0       | 1.1       | 2.5       | 5.4       | 1.3       |
   | carcass as-is)                | NE_g   | 0.46     | 0.45     | 0.30      | 0.32      | 0.50      | 0.32      | 0.70      | 0.52      |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Dairy bulls & heifers carcass | NE_m   | 187      | 47       | 143       | 130       | 53        | 344       | 211       | 41        |
   | (MJ per kg carcass as-is)     | NE_g   | 22       | 14       | 24        | 21        | 16        | 19        | 20        | 16        |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Beef carcass                  | NE_m   | 288      | 141      | 236       | 262       | 109       | 479       | 352       | 103       |
   | (MJ per kg carcass as-is)     | NE_g   | 25       | 19       | 28        | 23        | 23        | 20        | 21        | 23        |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Pig carcass                   | ME     | 86       | 84       | 131       | 86        | 65        | 115       | 123       | 64        |
   | (MJ per kg carcass-side       |        |          |          |           |           |           |           |           |           |
   | as-is)                        |        |          |          |           |           |           |           |           |           |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Eggs & hen carcass            | ME     | 43       | 42       | 39        | 43        | 32        | 53        | 56        | 30        |
   | (MJ per kg whole egg &        |        |          |          |           |           |           |           |           |           |
   | carcass as-is)                |        |          |          |           |           |           |           |           |           |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+
   | Meat-type chicken carcass     | ME     | 60       | 56       | 51        | 61        | 42        | 72        | 77        | 38        |
   | (MJ per kg eviscerated        |        |          |          |           |           |           |           |           |           |
   | carcass as-is)                |        |          |          |           |           |           |           |           |           |
   +-------------------------------+--------+----------+----------+-----------+-----------+-----------+-----------+-----------+-----------+

**Energy types**:
  * **NE_l**: Net energy for lactation (dairy production)
  * **NE_m**: Net energy for maintenance (basic metabolism)
  * **NE_g**: Net energy for growth (body mass gain)
  * **ME**: Metabolizable energy (for monogastrics)

**Notes**:
  * Values calculated from productivity estimates in Wirsenius (2000) Table 3.8
  * Regional variation reflects differences in production systems, breed genetics, climate, and management practices
  * Sub-Saharan Africa shows significantly higher requirements due to less intensive production systems
  * North America and Western Europe have lowest requirements, reflecting highly optimized industrial systems

Model Implementation
--------------------

In ``workflow/scripts/build_model.py``, livestock production is represented as multi-bus links:

Grazing Links
~~~~~~~~~~~~~

**Inputs**:
  * ``bus0``: Grassland (land bus for region/class)

**Outputs**:
  * ``bus1``: Ruminant grassland feed pool (``feed_ruminant_grassland``)
  * ``bus2``: CO₂ emissions from land-use change (if configured)

**Efficiency**: Grassland yield (t DM/ha)

Grassland is routed to its own dedicated ``feed_ruminant_grassland`` pool. This separate category enables special handling of nitrogen: manure from grazing animals is deposited on pasture (not collected), so it produces N₂O emissions but does not contribute to the collected manure fertilizer pool available for cropland.

.. note::

   Validation runs that set ``validation.use_actual_production: true`` also pin grassland production to present-day managed areas. The dataset ``processing/{name}/luc/current_grassland_area_by_class.csv`` is derived from the land-cover fractions prepared for LUC calculations and caps each grazing link at the observed area, forcing the solver to reproduce current grazing output.

In standard optimisation runs, current grassland is split into two pools per region/resource class: ``land:existing_grassland_convertible:*`` (cropland-suitable) and ``land:existing_grassland_marginal:*`` (grazing-only). The total current grassland comes from ``build_current_grassland_area``, while ``build_grazing_only_land`` provides the marginal subset. All hectares from both pools flow into the ``land:pasture:*`` pool via ``existing_grassland_to_pasture`` links. Grassland production links consume from that pooled pasture bus, together with land supplied from existing cropland and new conversion.

When demand falls and grazing links release land, ``spare_existing_grassland_*`` links allow the model to rewild formerly grazed hectares and credit the associated CO₂ removal using the same LUC emission factors that apply to cropland-suitable land.

Crop Residue Feed Supply
~~~~~~~~~~~~~~~~~~~~~~~~

Crop residues (e.g., straw, stover, pulse haulms) are now generated explicitly using the new Snakemake rule ``build_crop_residue_yields``:

* **Configuration**: Select residue crops via ``animal_products.residue_crops`` in ``config/default.yaml``. Only crops present in ``config.crops`` are processed.
* **Data sources**:
  - GLEAM Supplement S1 Table S.3.1 (slope/intercept) and Tables 3.3 / 3.6 (FUE factors)
  - GLEAM feed codes → model mapping in ``data/curated/gleam_feed_mapping.csv``
* **Outputs**: Per-crop CSVs at ``processing/{name}/crop_residue_yields/{crop}.csv`` with net dry-matter residue yields (t/ha) by region, resource class, and water supply.
* **Integration**: ``build_model`` reads all residue CSVs, adds ``residue_{feed_item}_{country}`` buses, and attaches them as additional outputs on crop production links. Residues flow through the same feed supply logic as crops/foods and enter the appropriate feed pools or soil incorporation.

Residue Removal Limits for Feed
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To maintain soil health and prevent land degradation, the model constrains the fraction of crop residues that can be removed for animal feed. The majority of residues must be left on the field and incorporated into the soil to maintain organic matter and nutrient cycling.

**Constraint formulation**:

* **Maximum feed removal**: 30% of generated residues (configurable via ``residues.max_feed_fraction``; override per ISO3 country or M49 region/sub-region via ``residues.max_feed_fraction_by_region`` with country > sub-region > region)
* **Minimum soil incorporation**: 70% of generated residues

The optimization model implements this as a constraint between residue feed use and soil incorporation for each residue type and country:

.. math::

   \text{feed use} \leq \frac{\text{max feed fraction}}{1 - \text{max feed fraction}} \times \text{incorporation}

With the default 30% limit:

.. math::

   \text{feed use} \leq \frac{3}{7} \times \text{incorporation}

This ensures that for every 3 units of residue used as feed, at least 7 units are incorporated into the soil. The constraint is applied during model solving (in ``solve_model.py``) after the network structure is built.

**Environmental implications**: Residues incorporated into soil generate direct N₂O emissions according to the IPCC EF\ :sub:`1` emission factor applied to their nitrogen content (see :doc:`environment`). The model therefore balances:

* **Feed benefits**: Residues reduce demand for dedicated feed crops (reducing land use and associated emissions)
* **Soil incorporation costs**: Incorporated residues produce N₂O emissions but maintain soil health

Feed Supply Links
~~~~~~~~~~~~~~~~~

The ``add_feed_supply_links()`` function creates links from crops, crop residues, and food byproducts to the nine feed pools:

**Item-to-Feed-Pool Links**:
  * **Inputs**: Crop, residue, or food byproduct buses (``bus0``)
  * **Outputs**: One of nine feed pool buses (``bus1``)
    - Ruminants: ``feed_ruminant_roughage``, ``feed_ruminant_forage``, ``feed_ruminant_grassland``, ``feed_ruminant_grain``, ``feed_ruminant_protein``
    - Monogastrics: ``feed_monogastric_low_quality``, ``feed_monogastric_grain``, ``feed_monogastric_energy``, ``feed_monogastric_protein``
  * **Efficiency**: 1.0 (feed buses are in tonnes dry matter intake; digestibility is applied later in feed-to-animal efficiencies and emissions)
  * **Routing**: Each feed item is mapped via ``processing/{name}/ruminant_feed_mapping.csv`` and ``processing/{name}/monogastric_feed_mapping.csv`` (generated by ``categorize_feeds.py``) to the relevant pool(s)
  * Crops compete between human consumption, food processing, and animal feed use; residues and byproducts are exclusive to feed use

**Example flow**:
  * Wheat grain → ``feed_ruminant_grain`` + ``feed_monogastric_grain`` (digestibility from GLEAM)
  * Wheat straw (residue) → ``feed_ruminant_roughage`` (low digestibility)
  * Wheat bran (byproduct) → ``feed_ruminant_grain`` + ``feed_monogastric_low_quality``

Feed-to-Animal-Product Links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``add_feed_to_animal_product_links()`` function converts feed pools to animal products with CH₄ emissions:

**Feed-Pool-to-Product Links**:
  * **Inputs**: Feed pool bus (``bus0``, e.g., ``feed_ruminant_forage``)
  * **Outputs**:

    * Animal product bus (``bus1``, e.g., ``food_cattle_meat``)
    * CH₄ emissions bus (``bus2``) - all animal products

  * **Efficiency**: Feed conversion ratio (tonnes product per tonne feed DM)
  * **CH₄ calculation**: Combines enteric fermentation (ruminants) and manure management (all animals)

    .. math::

       \text{CH}_4\text{/t feed} = \text{MY}_\text{enteric} + \text{MY}_\text{manure}

    where methane yields (MY) are in kg CH₄ per kg dry matter intake.

**Example**: Grass-fed beef from forage feed with enteric MY 23.3 g/kg and manure MY 2.2 g/kg:
  * Total CH₄ = 23.3 + 2.2 = 25.5 g CH₄ per kg feed DM
  * For 1 tonne feed → 0.0255 t CH₄ emissions

See :ref:`livestock-emissions` for detailed methodology and data sources.

.. _livestock-emissions:

Emissions from Livestock
-------------------------

Livestock production generates significant greenhouse gas emissions from two primary sources:

* **Enteric fermentation (CH₄)**: Ruminants produce methane through digestive fermentation
* **Manure management (CH₄, N₂O)**: All livestock produce emissions from manure storage and handling

For detailed methodology, data sources, and IPCC calculations, see :doc:`environment` (sections on :ref:`enteric-fermentation` and :ref:`manure-management`).

Enteric Fermentation (CH₄)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ruminants (cattle, sheep) produce methane through digestive fermentation. The model uses IPCC Tier 2 methodology based on methane yields (MY) per unit dry matter intake (DMI).

Summary
^^^^^^^

* Enteric fermentation produces CH₄ in ruminants during digestion
* Methane yield (MY) varies by feed quality (roughage > forage > grain > protein)
* Model uses IPCC Tier 2 methodology with feed-specific emission factors
* See :ref:`enteric-fermentation` for full details

Data Sources
^^^^^^^^^^^^

* ``data/curated/ipcc_enteric_methane_yields.csv``: IPCC methane yields by feed category
* ``processing/{name}/ruminant_feed_categories.csv``: Feed categories with MY values (generated from GLEAM 3.0 data)

Manure Management (CH₄, N₂O)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All livestock produce emissions from manure storage, handling, and application:

* **CH₄**: From anaerobic decomposition (especially liquid systems like lagoons)
* **N₂O**: From nitrogen in manure (direct and indirect emissions)

Manure CH₄ emissions are calculated for all animal products (ruminants and monogastrics) and combined with enteric emissions in the model. See :ref:`manure-management` for full methodology.

Production Costs
----------------

The model incorporates livestock production costs to represent the economic considerations of animal farming beyond feed and land costs. Costs include labor, veterinary services, energy, housing, and other operational expenses, while excluding feed (modeled endogenously) and land rent (implicit opportunity cost).

Livestock costs are applied as marginal costs on feed-to-product conversion links in the optimization model. The costs are sourced from USDA (United States) and FADN (European Union) agricultural accounting data, processed to per-tonne product costs, then converted to per-tonne feed costs using feed conversion efficiencies.

**Grazing costs** are handled separately from general livestock production costs. These costs represent the economic expenses specific to pasture-based feed production and are applied directly to grassland feed links rather than animal production links.

For comprehensive details on production cost data sources, processing methodology, and model application, see:

  * :doc:`costs` - Complete documentation of all production costs (crops, livestock, and grazing)

The livestock-specific sections include:

  * **Data sources**: USDA and FADN livestock cost data
  * **Processing methodology**: Allocation by output value, yield calculations, and unit conversions
  * **Grazing costs**: Separation, processing, and application to grassland feed
  * **Model application**: How costs are applied as marginal costs on production links

**Quick reference** for livestock cost workflow:

* ``retrieve_usda_animal_costs``: Processes USDA livestock cost data (US)
* ``retrieve_fadn_animal_costs``: Processes FADN livestock cost data (EU)
* ``merge_animal_costs``: Combines sources and applies fallback mappings
* Output: ``processing/{name}/animal_costs.csv`` with columns:

  * ``product``: Animal product name
  * ``cost_per_mt_usd_{base_year}``: Production cost excluding grazing (USD/tonne)
  * ``grazing_cost_per_mt_usd_{base_year}``: Grazing-specific cost (USD/tonne)

Configuration Parameters
------------------------

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: animal_products ---
   :end-before: # --- section: food_groups ---

Disabling grazing (``enabled: false``) forces all animal products to come from feed-based systems or imports, useful for exploring intensification scenarios.

Workflow Rules
--------------

**build_grassland_yields**
  * **Input**: ISIMIP grassland yield NetCDF, resource classes, regions
  * **Output**: ``processing/{name}/grassland_yields.csv``
  * **Script**: ``workflow/scripts/build_grassland_yields.py``

Livestock production is then integrated into the ``build_model`` rule using the grassland yields and feed conversion CSVs.

References
----------

.. [1] Wirsenius, S. (2000). *Human Use of Land and Organic Materials: Modeling the Turnover of Biomass in the Global Food System*. Chalmers University of Technology and Göteborg University, Sweden. ISBN 91-7197-886-0. https://publications.lib.chalmers.se/records/fulltext/827.pdf

.. [2] Organisation for Economic Co-operation and Development / Food and Agriculture Organization of the United Nations (2023). *OECD-FAO Agricultural Outlook 2023-2032*, Box 6.1: Meat. https://www.oecd.org/en/publications/oecd-fao-agricultural-outlook-2023-2032_08801ab7-en/full-report/meat_7b036d52.html#title-a5a1984180

.. [3] Havlík, P., Valin, H., Herrero, M., Obersteiner, M., Schmid, E., Rufino, M. C., ... & Notenbaert, A. (2014). Climate change mitigation through livestock system transitions. *Proceedings of the National Academy of Sciences*, 111(10), 3709-3714, https://doi.org/10.1073/pnas.130804411. See the supporting information, Section 2.4.
