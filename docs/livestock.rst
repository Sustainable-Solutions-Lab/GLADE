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

The model includes five major animal product categories configured in ``config/default.yaml``:

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

.. figure:: _static/figures/grassland_yield.png
   :alt: Managed grassland yield potential
   :width: 100%
   :align: center

   Global distribution of managed grassland yield potential (tonnes dry matter per hectare per year) from ISIMIP LPJmL historical simulations

Feed Conversion
---------------

The model uses feed conversion ratios to link feed inputs to animal outputs, with explicit categorization by feed quality to enable accurate CH₄ emissions tracking.

Feed System Architecture
~~~~~~~~~~~~~~~~~~~~~~~~

The feed system uses **eight distinct feed pools** that combine animal type with feed quality:

* **Ruminant pools**: ``ruminant_roughage``, ``ruminant_forage``, ``ruminant_grain``, ``ruminant_protein``
* **Monogastric pools**: ``monogastric_low_quality``, ``monogastric_grain``, ``monogastric_energy``, ``monogastric_protein``

This categorization enables the model to:

1. Differentiate methane emissions using GLEAM feed digestibility classes (roughage/forage vs. grain/protein)
2. Route crops, residues, and processing byproducts to appropriate feed pools based on nutritional properties
3. Model production system choices (e.g., roughage-dominated beef vs. high-grain finishing rations)

data/feed_properties.csv
~~~~~~~~~~~~~~~~~~~~~~~~~

Unified database mapping all feed items (crops and food byproducts) to feed categories and digestibility values. Columns:

* ``feed_item``: Item name (e.g., "maize", "wheat bran")
* ``source_type``: Either "crop" or "food" (byproduct)
* ``feed_category``: Feed quality category (``forage``, ``concentrate``, or ``byproduct``)
* ``digestibility_ruminant``: Digestible fraction for ruminants (0-1)
* ``digestibility_monogastric``: Digestible fraction for monogastrics (0-1)
* ``ME_MJ_per_kg_DM``: Metabolizable energy (MJ per kg dry matter) - for future use
* ``CP_pct_DM``: Crude protein (% of dry matter) - for future use
* ``NDF_pct_DM``: Neutral detergent fiber (% of dry matter) - for future use
* ``notes``: Description and source information

**Feed categories**:
  * **Forage**: High-fiber forages (grassland, silage, hay-type crops) - low digestibility, high CH₄
  * **Concentrate**: Energy/protein concentrates (grains, oilseeds, pulses) - high digestibility, low CH₄
  * **Byproduct**: Processing byproducts (brans, meals, hulls) - moderate digestibility, moderate CH₄

**Typical items**:
  * *Roughage/forage*: grassland forage, alfalfa, silage maize
  * *Grain/energy*: wheat, maize, soybean, dry pea
  * *Byproducts/residues*: wheat bran, rice bran, sunflower meal, crop residues (e.g., wheat straw, maize stover)

Byproducts from food processing (with ``source_type=food``) are automatically excluded from human consumption and can only be used as animal feed.

.. Note:: Current digestibility and nutritional values are mock data; to be replaced with actual feed value data from animal nutrition literature.

data/feed_to_animal_products.csv
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Maps feed pool requirements to animal product yields. Columns:

* ``product``: Product name (e.g., "cattle meat", "dairy")
* ``feed_category``: Specific feed pool (e.g., ``ruminant_forage``, ``ruminant_concentrate``)
* ``efficiency``: Feed conversion efficiency (tonnes product per tonne feed DM)
* ``notes``: Description and source information

This structure allows modeling different production systems for the same product:
  * Grass-fed beef: ``cattle meat`` from ``ruminant_forage``
  * Grain-finished beef: ``cattle meat`` from ``ruminant_concentrate``
  * Pasture dairy: ``dairy`` from ``ruminant_forage``
  * Intensive dairy: ``dairy`` from ``ruminant_concentrate``

.. Note:: Current values are mock data; to be replaced by actual feed conversion ratios.

Model Implementation
--------------------

In ``workflow/scripts/build_model.py``, livestock production is represented as multi-bus links:

Grazing Links
~~~~~~~~~~~~~

**Inputs**:
  * ``bus0``: Grassland (land bus for region/class)

**Outputs**:
  * ``bus1``: Ruminant forage feed pool (``feed_ruminant_forage``)
  * ``bus2``: CO₂ emissions from land-use change (if configured)

**Efficiency**: Grassland yield (t DM/ha)

Grassland is treated as forage-quality feed and routed to the ``feed_ruminant_forage`` pool, where it competes with other forage sources.

Crop Residue Feed Supply
~~~~~~~~~~~~~~~~~~~~~~~~

Crop residues (e.g., straw, stover, pulse haulms) are now generated explicitly using the new Snakemake rule ``build_crop_residue_yields``:

* **Configuration**: Select residue crops via ``animal_products.residue_crops`` in ``config/default.yaml``. Only crops present in ``config.crops`` are processed.
* **Data sources**:
  - GLEAM Supplement S1 Table S.3.1 (slope/intercept) and Tables 3.3 / 3.6 (FUE factors)
  - GLEAM feed codes → model mapping in ``data/gleam_feed_mapping.csv``
* **Outputs**: Per-crop CSVs at ``processing/{name}/crop_residue_yields/{crop}.csv`` with net dry-matter residue yields (t/ha) by region, resource class, and water supply.
* **Integration**: ``build_model`` reads all residue CSVs, adds ``residue_{feed_item}_{country}`` buses, and attaches them as additional outputs on crop production links. Residues flow through the same feed supply logic as crops/foods and enter the appropriate feed pools.

Feed Supply Links
~~~~~~~~~~~~~~~~~

The ``add_feed_supply_links()`` function creates links from crops, crop residues, and food byproducts to the eight feed pools:

**Item-to-Feed-Pool Links**:
  * **Inputs**: Crop, residue, or food byproduct buses (``bus0``)
  * **Outputs**: One of eight feed pool buses (``bus1``)
    - Ruminants: ``feed_ruminant_roughage``, ``feed_ruminant_forage``, ``feed_ruminant_grain``, ``feed_ruminant_protein``
    - Monogastrics: ``feed_monogastric_low_quality``, ``feed_monogastric_grain``, ``feed_monogastric_energy``, ``feed_monogastric_protein``
  * **Efficiency**: Category-specific digestibility (from ``processing/{name}/ruminant_feed_categories.csv`` / ``monogastric_feed_categories.csv``)
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
  * **Outputs**: Animal product bus (``bus1``, e.g., ``food_cattle_meat``)
  * **Emissions**: CH₄ from enteric fermentation (``bus2`` for ruminants only)
  * **Efficiency**: Feed conversion ratio (tonnes product per tonne feed DM)
  * **CH₄ calculation**: For ruminants only:

    .. math::

       \text{DMI} &= 1 / \text{efficiency} \quad \text{(t DM per t product)} \\
       \text{MY} &= \text{methane yield from category} \quad \text{(g CH}_4\text{/kg DM)} \\
       \text{CH}_4 &= \text{DMI} \times \text{MY} / 1000 \quad \text{(t CH}_4\text{ per t product)}

**Example**: Grass-fed beef with efficiency 0.15 (6.7 t DM/t product) and forage MY 23.3 g/kg:
  * DMI = 1/0.15 = 6.67 t DM/t product
  * CH₄ = 6.67 × 23.3 / 1000 = 0.155 t CH₄/t product

Emissions from Livestock
-------------------------

Livestock production generates significant greenhouse gas emissions.

Enteric Fermentation (CH₄)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ruminants (cattle, sheep) produce methane through digestive fermentation. The model uses a simplified IPCC Tier 2 approach based on methane yields (MY) per unit dry matter intake (DMI).

Methodology
^^^^^^^^^^^

Methane emissions are calculated as:

.. math::

   \text{CH}_4 = \text{DMI} \times \text{MY}

where:
  * **DMI** is dry matter intake (kg/day)
  * **MY** is methane yield (g CH₄ per kg DMI)

The methane yield depends on feed quality, specifically digestibility (DE%) and fiber content (NDF%). The IPCC provides differentiated conversion factors for various livestock categories and feeding systems.

IPCC Conversion Factors
^^^^^^^^^^^^^^^^^^^^^^^^

The model uses methane yields from the `2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas Inventories <https://www.ipcc.ch/report/2019-refinement-to-the-2006-ipcc-guidelines-for-national-greenhouse-gas-inventories/>`_, Volume 4, Table 10.12:

.. table:: Cattle/Buffalo Methane Conversion Factors (Ym)
   :widths: 20 40 20 10 10

   +-------------------+-------------------------------------------+----------------------+------------+----------+
   | Livestock         | Description                               | Feed Digestibility   | MY         | Ym³ (%)  |
   | category          |                                           | (DE %) and Neutral   | g CH₄ kg   |          |
   |                   |                                           | Detergent Fibre      | DMI⁻¹      |          |
   |                   |                                           | (NDF, % DMI)         |            |          |
   +===================+===========================================+======================+============+==========+
   | Dairy cows        | High-producing cows⁵                      | DE ≥ 70              | 19.0       | 5.7      |
   | and Buffalo       | (>8500 kg/head/yr⁻¹)                      | NDF ≤ 35             |            |          |
   |                   +-------------------------------------------+----------------------+------------+----------+
   |                   | High-producing cows⁵                      | DE ≥ 70              | 20.0       | 6.0      |
   |                   | (>8500 kg/head/yr⁻¹)                      | NDF ≥ 35             |            |          |
   |                   +-------------------------------------------+----------------------+------------+----------+
   |                   | Medium producing cows                     | DE 63-70             | 21.0       | 6.3      |
   |                   | (5000 – 8500 kg yr⁻¹)                     | NDF > 37             |            |          |
   |                   +-------------------------------------------+----------------------+------------+----------+
   |                   | Low producing cows                        | DE ≤ 62              | 21.4       | 6.5      |
   |                   | (<5000 kg yr⁻¹)                           | NDF >38              |            |          |
   +-------------------+-------------------------------------------+----------------------+------------+----------+
   | Non dairy and     | > 75 % forage                             | DE ≤ 62              | 23.3       | 7.0      |
   | multi-purpose     +-------------------------------------------+----------------------+------------+----------+
   | Cattle and        | Rations of >75% high quality              | DE 62–71             | 21.0       | 6.3      |
   | Buffalo           | forage and/or mixed rations,              |                      |            |          |
   |                   | forage of between 15 and 75%              |                      |            |          |
   |                   | the total ration mixed with               |                      |            |          |
   |                   | grain, and/or silage.                     |                      |            |          |
   |                   +-------------------------------------------+----------------------+------------+----------+
   |                   | Feedlot (all other grains, 0-15%          | DE ≥ 72              | 13.6       | 4.0      |
   |                   | forage)                                   |                      |            |          |
   |                   +-------------------------------------------+----------------------+------------+----------+
   |                   | Feedlot (steam-flaked corn - 0-           | DE ≥ 75              | 10.0       | 3.0      |
   |                   | 10% forage)                               |                      |            |          |
   +-------------------+-------------------------------------------+----------------------+------------+----------+

**Source**: IPCC (2019), Table 10.12 (Updated)

**Notes**:
  * ⁵ High-producing cows are defined as those yielding >8500 kg milk/head/year
  * Ym³ (%) represents the methane conversion factor (percentage of gross energy in feed converted to methane)
  * DE = Digestible Energy
  * NDF = Neutral Detergent Fibre

Implementation
^^^^^^^^^^^^^^

The model implements IPCC Tier 2 methodology using feed quality-differentiated methane yields:

* Feed is categorized into **6 pools** combining animal type (ruminant/monogastric) with feed quality (forage/concentrate/byproduct)
* Each feed category is assigned a specific MY value based on IPCC guidelines:

  - **Forage** (23.3 g CH₄/kg DMI): High-forage diets >75% forage, DE ≤ 62%
  - **Byproduct** (21.0 g CH₄/kg DMI): Mixed rations 15-75% forage + byproducts/grain, DE 62-71%
  - **Concentrate** (13.6 g CH₄/kg DMI): Feedlot/grain-based feeding 0-15% forage, DE ≥ 72%

* Monogastric animals (pigs, poultry) produce minimal enteric methane (not modeled)
* CH₄ emissions are calculated dynamically in ``add_feed_to_animal_product_links()`` based on DMI and feed category

data/enteric_methane_yields.csv
++++++++++++++++++++++++++++++++

Simplified methane yield lookup table mapping feed categories to MY values. Columns:

* ``feed_category``: Feed quality category (``forage``, ``byproduct``, ``concentrate``)
* ``MY_g_CH4_per_kg_DMI``: Methane yield (g CH₄ per kg dry matter intake)
* ``notes``: IPCC source and diet description

**Source**: IPCC (2019), 2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas Inventories, Volume 4, Table 10.12

The file contains only 3 rows - one for each feed quality category. The values are inferred from IPCC Table 10.12 cattle/buffalo categories based on forage percentage and digestibility.

.. Note:: Future refinements could differentiate by production intensity (high/medium/low-producing dairy) and more detailed feed composition (NDF percentage).

Manure Management (N₂O, CH₄)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Manure storage and application releases:

* **N₂O**: From nitrogen in manure (direct and indirect emissions)
* **CH₄**: From anaerobic manure decomposition (especially in lagoons)

These are incorporated into the production link efficiencies, priced at the configured ``emissions.ghg_price`` (USD/tCO₂-eq; flows are scaled to MtCO₂-eq internally).

Configuration Parameters
------------------------

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: animal_products ---
   :end-before: # --- section: trade ---

Disabling grazing (``enabled: false``) forces all animal products to come from feed-based systems or imports, useful for exploring intensification scenarios. All food group minima are zero by default; raise ``food_groups.animal_protein.min_per_person_per_day`` (e.g., to 30 g) to enforce minimum consumption of animal-source foods.

Workflow Rules
--------------

**build_grassland_yields**
  * **Input**: ISIMIP grassland yield NetCDF, resource classes, regions
  * **Output**: ``processing/{name}/grassland_yields.csv``
  * **Script**: ``workflow/scripts/build_grassland_yields.py``

Livestock production is then integrated into the ``build_model`` rule using the grassland yields and feed conversion CSVs.
