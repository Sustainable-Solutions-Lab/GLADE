.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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

The feed system uses **seven distinct feed pools** that combine animal type with feed quality:

* **Ruminant pools**: ``ruminant_roughage``, ``ruminant_forage``, ``ruminant_grain``, ``ruminant_protein``
* **Monogastric pools**: ``monogastric_low_quality``, ``monogastric_grain``, ``monogastric_protein``

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

These properties are extracted from the GLEAM 3.0 supplement using ``data/curated/gleam/feed_mapping.csv`` to map between model feed items and GLEAM feed categories.

**Feed quality categories** (assigned based on nitrogen content and digestibility):

* **Ruminant feeds**:

  * **Protein**: High nitrogen content (>50 g N/kg DM) - protein meals such as rapeseed-meal, oilseed-meal, soybean meal (assigned by N content; takes precedence over digestibility)
  * **Roughage**: Low digestibility (<0.55), high-fiber forages (crop residues, straw)
  * **Forage**: Medium digestibility (0.55-0.70), improved forages and grassland (silage maize, alfalfa, pasture)
  * **Grain**: High digestibility (0.70-0.90), energy concentrates (maize, wheat, barley)

* **Monogastric feeds**:

  * **Protein**: High nitrogen content (>35 g N/kg DM) - protein meals such as soybean meal, fish meal, rapeseed-meal (assigned by N content; takes precedence over energy)
  * **Low quality**: Low metabolizable energy (<11 MJ/kg DM), bulky feeds and byproducts
  * **Grain**: Medium energy (11-15.5 MJ/kg DM), cereal grains
  * **Energy**: High energy (>15.5 MJ/kg DM), fats and high-energy feeds

**Categorization logic**: Both ruminant and monogastric feeds prioritize nitrogen content to identify protein feeds, ensuring high-protein oilseed meals are correctly classified regardless of digestibility. For feeds below the nitrogen threshold, ruminants use digestibility ranges while monogastrics use metabolizable energy thresholds.

Byproducts from food processing (with ``source_type=food``) are automatically excluded from human consumption and can only be used as animal feed.

.. _feed-conversion-efficiencies:

Feed Conversion Efficiencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Feed conversion efficiencies (tonnes **retail product** per tonne feed DM) are
derived per country from GLEAM 3.0 feed intake and production data, combined
with GLEAM 3.0 feed category energy values.  The pipeline has two stages:
first, per-country ME requirements are computed from GLEAM3; then, these are
combined with per-category feed energy contents to produce conversion
efficiencies.

ME Requirements from GLEAM3
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The script ``compute_gleam3_me_requirements.py`` derives metabolizable energy
(ME) requirements per kg product for each country and product.  For each
country, the total feed ME intake of a species is computed from GLEAM3 intake
data (kg DM × ME per kg DM for each feed category), then divided among the
species' products to obtain ME per kg product output.

**Backyard "Other non-edible" correction**.  GLEAM3 reclassifies regular
grains and crops (wheat, maize, barley, etc.) as "Other non-edible" in
Backyard monogastric systems, because these feeds are locally sourced or
scavenged rather than commercially purchased.  In non-Backyard systems, the
same category contains only genuinely non-feed supplements (synthetic amino
acids, fishmeal, limestone) plus swill.  The ME computation accounts for this
LPS-dependent composition: Backyard "Other non-edible" uses the ``grain``
category ME (since reclassified grains dominate ~93% of backyard non-edible
intake globally), while non-Backyard "Other non-edible" uses swill ME from
GLEAM Table S.3.4 (13.0 MJ/kg DM for chicken, 10.5 MJ/kg DM for pigs).

**Multi-product splitting**.  Most animal species produce multiple model
products simultaneously (e.g. cattle produce both dairy and meat).  The total
system ME must be allocated between these co-products.  Wirsenius (2000) [1]_
provides regional dairy-to-meat energy ratios (see table below) that serve as
the proportional guide, while GLEAM3 sets the absolute level:

.. math::

   f_\text{cattle} = \frac{\text{ME}_\text{feed,GLEAM3}}
       {W_\text{dairy} \times \text{prod}_\text{milk}
       + W_\text{meat} \times \text{prod}_\text{meat}}

where :math:`W_\text{dairy}` and :math:`W_\text{meat}` are Wirsenius reference
ME values, and :math:`f` is a country-specific scaling factor applied to both
products.  This preserves the Wirsenius dairy:meat *ratio* while anchoring the
absolute ME to observed GLEAM3 feed intake.

For **ruminant products**, the Wirsenius net-energy values (NE) are first
converted to metabolizable energy using efficiency factors from config
(``animal_products.net_to_metabolizable_energy_conversion``):

* ``k_m`` = 0.65 — maintenance efficiency. From the California Net Energy
  System (NRC 2000 Beef Cattle [6]_, with cubic-in-ME equations updated in
  NASEM 2016 [7]_), evaluated at a typical mixed-diet metabolizability
  :math:`q = \mathrm{ME}/\mathrm{GE} \approx 0.60`.
* ``k_g`` = 0.43 — growth efficiency. Same source as ``k_m`` [6]_ [7]_.
* ``k_l`` = 0.64 — lactation efficiency, dairy only. NRC 2001 Dairy
  Cattle, 7th rev. ed. [8]_, where ME-to-NEL conversion is fixed at 0.64.

For **monogastric products** (pigs, chicken), Wirsenius values are already in
ME and the conversion step is skipped.  Single-product species (pigs) are
computed directly as total feed ME / total production.

**Sheep/goat milk proxy**.  Sheep and goat milk (~3–4% of global production)
is proxied through the cattle ``dairy`` product rather than modeled separately.
For ME derivation, the Wirsenius cattle dairy:meat ME ratio is used to split
sheep/goat system feed between milk (folded into the ``dairy`` product) and
``meat-sheep``, using the same scaling-factor approach as for cattle.  This
replaces an earlier residual method that was numerically unstable for countries
with extreme sheep milk:meat ratios.  See the config comment on
``gleam3_system_product_map`` for the rationale.

**Scaling-factor clamping**.  The factor :math:`f` measures how much a
country's total feed intensity deviates from its Wirsenius regional average.
Small countries with limited GLEAM3 data can produce extreme :math:`f` values
that translate to unrealistic per-product ME (e.g. Montenegro at
:math:`f = 0.24` would imply a dairy ME of 4.1 MJ/kg).  To guard against
this, :math:`f` is clamped to the range
:math:`[\text{median}/k,\; \text{median} \times k]` where the median is taken
over countries in the same Wirsenius region and :math:`k` is the config
parameter ``animal_products.me_scaling_clamp_factor`` (default 2.0).  The
clamping applies to all multi-product species groups (cattle, buffalo, chicken,
sheep/goats).

**Fallback**.  Countries without sufficient GLEAM3 data for a species receive
the production-weighted global average ME for that product.

**Output**: ``processing/{name}/gleam3_me_requirements.csv`` with columns
``animal_product``, ``country``, ``ME_MJ_per_kg`` (at carcass/farm-gate level).

Efficiency Calculation
^^^^^^^^^^^^^^^^^^^^^^^

The script ``build_feed_to_animal_products.py`` converts ME requirements to
feed conversion efficiencies.  Units at each stage:

* **Feed inputs**: Dry matter (tonnes DM)
* **Animal product outputs**: Fresh weight, retail meat (tonnes fresh weight)

  * For meats: **retail/edible meat** weight (boneless, trimmed) — not carcass
  * For dairy: whole milk (fresh weight)
  * For eggs: whole eggs (fresh weight)

GLEAM3 ME requirements are at **carcass/farm-gate** level.  Carcass-to-retail
conversion factors (from OECD-FAO Agricultural Outlook 2023-2032, Box 6.1
[2]_) are applied to obtain feed requirements per kg retail meat:

* Cattle meat: 0.67 kg boneless retail per kg carcass
* Sheep meat: 0.63 kg boneless retail per kg carcass
* Pig meat: 0.73 kg boneless retail per kg carcass
* Chicken meat: 0.60 kg boneless retail per kg carcass
* Eggs, dairy, & buffalo milk: 1.00 (no conversion, already retail products)

This increases feed requirements per kg retail meat by ~33–50% compared to per
kg carcass, reflecting bone removal and trimming losses.

The final efficiency for each (country, product, feed_category) triple:

.. math::

   \text{efficiency} = \frac{\text{ME}_\text{feed} \;[\text{MJ/kg DM}]}
       {\text{ME}_\text{product,retail} \;[\text{MJ/kg retail}]}

This gives tonnes of retail product per tonne of feed DM.  Each product ×
feed-category combination yields a distinct efficiency, allowing the model to
represent different production systems (grass-fed vs. grain-finished beef,
pasture vs. intensive dairy, etc.).

**Output**: ``processing/{name}/feed_to_animal_products.csv`` with
columns:

* ``country``: ISO 3166-1 alpha-3 country code
* ``product``: Product name (e.g., "meat-cattle", "dairy")
* ``feed_category``: Feed pool (e.g., ``ruminant_forage``, ``monogastric_grain``)
* ``efficiency``: Feed conversion efficiency (t product / t feed DM)

Regional Feed Energy Requirements (Wirsenius reference)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Feed requirements vary significantly by region due to differences in production
systems, genetics, and environmental conditions.  Wirsenius (2000) [1]_
provides estimated feed energy requirements per unit of commodity output.
These values serve as reference ratios for splitting multi-product systems in
the GLEAM3 ME derivation described above:

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

.. _gleam-feed-baseline:

Baseline Feed Intake
--------------------

To ground the livestock module in observed feed flows, the model constructs a
country-level baseline from FAO's GLEAM 3.0 model [4]_ [5]_ that describes how
much dry-matter feed of each category each animal product consumed in the
reference year. GLEAM 3.0 provides country-level data for 229 countries
(reference year 2015), eliminating the need for the OECD/Non-OECD
disaggregation required by the earlier GLEAM 2.0 data. In validation mode
this baseline can pin the model to observed feed mixes; in optimisation runs
it serves as a reference point for comparison with solved results.

The script ``workflow/scripts/prepare_feed_baseline.py`` produces
``processing/{name}/feed_baseline.csv``.

Data Sources
~~~~~~~~~~~~

* **GLEAM 3.0 feed intakes** (``data/bundled/gleam3/intakes.csv``):
  Country-level dry-matter feed intake by species (Cattle, Buffalo, Sheep,
  Goats, Chicken, Pigs), production system (Grassland, Mixed, Feedlots,
  Layer, Broiler, Backyard, Intermediate, Industrial), and feed category
  (Grains, Oil seed cakes, Grass and leaves, Crop residues, Fodder crop,
  By-products, Other edible, Other non-edible). Global total: ~6,208 Mt DM.

* **GLEAM 3.0 production** (``data/bundled/gleam3/production.csv``):
  Country-level animal product output (meat carcass weight, milk/egg weight)
  per species and production system, used for FCR-weighted product splitting.

* **Feed fractions** (``processing/{name}/gleam3_feed_fractions.csv``):
  Pre-computed mapping from GLEAM3 aggregate feed categories to model feed
  categories, produced by ``compute_gleam3_feed_fractions.py``. Most
  categories have constant 1:1 mappings; By-products and Other edible use
  country-varying fractions estimated from FAOSTAT crop production volumes.

* **GLEAM3-derived ME requirements**
  (``processing/{name}/gleam3_me_requirements.csv``): Per-country metabolizable
  energy requirements per kg product, used as FCR weights for product splitting.

* **FAOSTAT QCL**: National animal product output for 2015 (the GLEAM 3.0
  reference year) and the model's configured reference year
  (``baseline_year``). Used for product splitting and temporal scaling.

Processing Pipeline
~~~~~~~~~~~~~~~~~~~

GLEAM 3.0 country-level intakes are converted to a country × product ×
feed-category matrix through the following steps.

**Step 1 — Product split for multi-product systems**

The mapping from GLEAM3 (Animal, LPS) systems to model products is defined in
``animal_products.gleam3_system_product_map`` in the configuration.  Several
systems serve more than one model product simultaneously: Cattle Grassland and
Mixed systems supply dairy and meat-cattle; Buffalo systems supply
dairy-buffalo and meat-cattle; Sheep and Goat systems supply dairy and
meat-sheep (sheep/goat milk is proxied through the cattle dairy pathway);
Chicken Backyard systems supply both eggs and meat-chicken.

Feed is allocated between co-products in proportion to their energy demand,
using GLEAM3 per-LPS production data and per-country ME requirements:

.. math::

   \text{product\_share}_{p} =
       \frac{\text{production}_{c,p,\text{LPS}} \times \text{ME}_{c,p}}
            {\sum_{p'} \text{production}_{c,p',\text{LPS}} \times \text{ME}_{c,p'}}

Countries with no GLEAM3 production data for a system fall back to FAOSTAT
production ratios. Cattle Feedlots map 100% to meat-cattle (finishing only).

**Step 2 — Feed category mapping**

Each GLEAM3 aggregate feed category is mapped to one or more model feed
categories using pre-computed fractions:

.. list-table::
   :header-rows: 1
   :widths: 30 20 30 10

   * - GLEAM3 category
     - Animal type
     - Model category
     - Exogenous
   * - Grains
     - ruminant / monogastric
     - ``ruminant_grain`` / ``monogastric_grain``
     - No
   * - Oil seed cakes
     - ruminant / monogastric
     - ``ruminant_protein`` / ``monogastric_protein``
     - No
   * - Crop residues
     - ruminant / monogastric
     - ``ruminant_roughage`` / ``monogastric_low_quality``
     - No
   * - Grass and leaves
     - ruminant / monogastric
     - ``ruminant_forage`` / ``monogastric_low_quality``
     - No
   * - Fodder crop
     - ruminant
     - ``ruminant_forage``
     - No
   * - By-products
     - both
     - Country-varying (bran → grain, DDGS → forage/protein, molasses → grain)
     - No
   * - Other edible
     - monogastric
     - Country-varying (cassava/banana → grain, soy/pulses → protein)
     - No
   * - Other non-edible
     - monogastric
     - ``monogastric_low_quality``
     - **Yes**

**Other non-edible** (~180 Mt DM, ~14% of monogastric feed).  In non-Backyard
systems this consists of synthetic amino acids, minerals, limestone, fishmeal,
and swill.  In Backyard systems, GLEAM3 additionally reclassifies locally
sourced grains and crops into this category (see the backyard correction in
:ref:`feed-conversion-efficiencies`).  The entire amount is marked as
exogenous in the feed baseline since these feeds have no endogenous crop-based
production route in the model.

**Grass and leaves** maps 100% to ``ruminant_forage``. The grassland forage
calibration mechanism (see :ref:`grassland-forage-calibration`) detects any
forage shortfall from the leaves/browse component and creates exogenous
supply to compensate.

**Step 3 — Scaling to the reference year**

The GLEAM 3.0 baseline reflects 2015 conditions. If the configured reference
year differs from 2015, country- and product-level feed intakes are scaled
using FAO production trends:

.. math::

   \text{feed}_{\text{ref}} = \text{feed}_{2015}
       \times \frac{\text{production}_{\text{ref}}}
                   {\text{production}_{2015}}

**Step 4 — Production-based feed scaling**

The pipeline rescales feed quantities per (country, product) so that the
implied animal output matches FAOSTAT production data. This preserves
GLEAM 3.0's feed composition (the relative split across feed categories)
while correcting absolute feed levels to observed production:

.. math::

   \begin{aligned}
   \text{implied}_{c,p} &= \sum_f \text{feed}_{c,p,f}
       \times \text{efficiency}_{c,p,f} \\[6pt]
   \text{scale}_{c,p} &= \frac{\text{FAOSTAT}_{c,p}}
                               {\text{implied}_{c,p}} \\[6pt]
   \text{feed_scaled}_{c,p,f} &= \text{feed}_{c,p,f}
       \times \text{scale}_{c,p}
   \end{aligned}

The efficiencies used here are the *uncalibrated* values from
``feed_to_animal_products.csv``.  Scale factors outside the
range [0.3, 3.0] are logged as potential data inconsistencies in the GLEAM
disaggregation (e.g., a mismatch between GLEAM's regional feed totals and
FAOSTAT's country-level production data).  If FAOSTAT reports zero production
for a (country, product) pair, all feed is set to zero; if the implied
production is zero but FAOSTAT is positive, the scale factor defaults to 1.0
and a warning is logged.

Output
~~~~~~

``processing/{name}/feed_baseline.csv`` contains one row per
(country, product, feed category) combination:

* ``country``: ISO 3166-1 alpha-3 country code
* ``product``: Animal product (``dairy``, ``dairy-buffalo``, ``meat-cattle``,
  ``meat-sheep``, ``eggs``, ``meat-chicken``, ``meat-pig``)
* ``feed_category``: Feed pool (e.g., ``ruminant_forage``,
  ``ruminant_grain``, ``monogastric_protein``)
* ``feed_use_mt_dm``: Dry-matter feed consumption (Mt DM) in the reference year
* ``exogenous_mt_dm``: Portion of feed demand that must be supplied
  exogenously (Mt DM) — primarily Other non-edible for monogastrics
  (synthetic amino acids, minerals, limestone, fishmeal)

All (country, product, feed category) combinations are always present,
including zeros, so every animal production link in the model has an explicit
baseline entry.

When feed efficiency calibration is enabled (see :ref:`feed-calibration`),
the calibrated baseline is written to
``processing/{name}/feed_baseline.csv`` and the calibrated efficiencies to
``processing/{name}/feed_to_animal_products.csv``.

Model Integration
~~~~~~~~~~~~~~~~~

The baseline participates in the model in two distinct ways:

1. **Validation mode**: When ``validation.enforce_baseline_feed: true``, the
   baseline feed quantities are imposed as equality constraints on the model,
   fixing the feed mix to GLEAM-derived estimates. This removes a degree of
   freedom and is useful for diagnosing supply-side inconsistencies—any
   imbalance shows up as feed slack (see :ref:`validation-feed-breakdown`).

2. **Optimisation mode**: The model is free to choose any feed mix within the
   bounds set by available supply and feed conversion efficiencies. The
   baseline is not enforced but is available for post-hoc comparison with
   solved solutions.

Exogenous Feed Supply
~~~~~~~~~~~~~~~~~~~~~

Some GLEAM 3.0 feed types have no endogenous supply route in the model:

* **Other non-edible** (~180 Mt DM globally): Synthetic amino acids,
  minerals, limestone, and fishmeal consumed by monogastrics, mapped to
  ``monogastric_low_quality``.

These are tracked in the ``exogenous_mt_dm`` column of the baseline and
supplied via ``exogenous_feed`` generators on the corresponding feed buses
(named ``supply:exogenous_{category}:{country}``).

In **validation mode**, these generators are fixed at the baseline amount
(forced dispatch).  In **optimisation mode**, they are extendable up to the
baseline amount at zero marginal cost, allowing the solver to use them if
beneficial but not requiring it.

.. _feed-calibration:

Calibration
-----------

The uncalibrated baseline and feed efficiencies inevitably produce
supply–demand gaps when tested in the full model: regional mismatches between
grassland output and ruminant forage demand are the primary source.  The
calibration pipeline uses a *validation solve* to diagnose these gaps via
slack variables and compute corrections.

Grassland Area Determination
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model uses two independent corrections to determine grassland area and
production:

1. **Area**: FAOSTAT "permanent meadows and pastures" (Item 6655) provides
   ground-truth pasture area per country.  Satellite-derived grassland area
   (from ESA CCI / LUIcube) is scaled down uniformly per country to match
   FAOSTAT, since satellite data systematically overestimates agricultural
   pasture by including non-agricultural grassland (alpine meadows, CRP land,
   ungrazed shrub-grassland).

2. **Production balance**: A proportional forage calibration adjusts
   grassland yield and fodder conversion efficiency to match supply to demand.

The FAOSTAT area cap is applied in ``build_model.py`` before any downstream
functions, so all components (generators, spare links, land budget) inherit
the corrected area.

.. _grassland-forage-calibration:

Grassland Forage Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Grassland forage calibration addresses remaining mismatches between forage
supply (grassland + fodder crops) and ruminant forage demand after the
FAOSTAT area cap.

**Algorithm** (implemented in
``workflow/scripts/compute_grassland_calibration.py``):

1. Solve the model with uncalibrated parameters (FAOSTAT area cap applied,
   but no yield/efficiency corrections).
2. Per country, extract forage slack from ``feed:ruminant_forage:{country}``
   buses and attribute surplus **proportionally** between grassland and
   fodder crops based on their shares of total supply:

   * **Surplus countries** (supply > demand): Both grassland yield and
     fodder conversion efficiency receive the same correction factor:

     .. math::

        \text{factor}_c = \frac{\text{demand}_c}{\text{total\_supply}_c}

     This produces ``yield_correction`` (applied to grassland_production
     link efficiencies) and ``fodder_conversion_correction`` (applied to
     feed_conversion link efficiencies for forage crops).

   * **Deficit countries** (demand > supply): An exogenous forage source
     covers the shortfall:

     .. math::

        \text{exogenous\_forage}_c = \text{deficit}_c

3. Three separate output files:

   * ``grassland_yield_correction.csv`` — per-country factor [0, 1]
   * ``fodder_conversion_correction.csv`` — per-country factor [0, 1]
   * ``exogenous_forage.csv`` — per-country Mt DM

**Configuration**:

.. code-block:: yaml

   grazing:
     grassland_forage_calibration:
       enabled: true
       generate: false
       grassland_yield_correction: "data/curated/calibration/grassland_yield.csv"
       fodder_conversion_correction: "data/curated/calibration/fodder_conversion.csv"
       exogenous_forage: "data/curated/calibration/exogenous_forage.csv"
       scenario: "default"

The figure below shows the grassland calibration results.
``yield_correction`` is one-sided:
values run from 0 to 1 and only reduce effective grassland yield (never
increase it).  Countries with forage deficits are additionally flagged via
hatching, indicating reliance on ``exogenous_forage_mt_dm`` after endogenous
grassland supply is exhausted.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/validation_grassland_calibration.png
   :width: 100%
   :alt: Map of grassland forage yield correction by country
   :align: center

   Grassland forage calibration by country.  Colour indicates the
   ``yield_correction`` factor applied to grassland yields (1.0 = no
   adjustment; lower values = stronger downward correction).  Countries
   receiving exogenous forage (``exogenous_forage_mt_dm > 0``) are marked with
   hatching.

Calibration Pipeline
~~~~~~~~~~~~~~~~~~~~

The calibration pipeline avoids circular dependencies by defining a dedicated
``uncalibrated`` scenario whose effective configuration disables grassland
forage calibration:

1. **Phase 1 — Uncalibrated solve**: The ``uncalibrated`` scenario sets
   ``grassland_forage_calibration.enabled: false``.  Building and solving
   this scenario produces the uncalibrated model with the FAOSTAT area cap
   but no yield/efficiency corrections.  ``compute_grassland_calibration``
   writes yield corrections, fodder conversion corrections, and exogenous
   forage amounts to the configured paths.

2. **Phase 2 — Calibrated model**: All other scenarios (including
   ``default``) apply all three calibration files.

Pre-computed calibration files are stored under ``data/curated/calibration/``
so they can be reused across configurations without re-running the validation
solve.  Set ``generate: true`` in the relevant configuration block to
re-generate them (requires a full validation solve).

.. _exogenous-protein-feed:

Exogenous Protein Feed
~~~~~~~~~~~~~~~~~~~~~~~

The model's *protein feed* category aggregates dry-matter inflows to two
buses, ``feed:monogastric_protein:{country}`` and
``feed:ruminant_protein:{country}``. Membership is set in
``workflow/scripts/categorize_feeds.py`` and is driven purely by
nitrogen content: feeds with N > 35 g/kg DM go into monogastric
protein, feeds with N > 50 g/kg DM (or digestibility ≥ 0.90 after
excluding grassland and crop residues) go into ruminant protein.
Modelled supply routes are essentially:

* Oilseed meals from crop-to-food processing pathways
  (soybean → oilseed-meal, rapeseed → rapeseed-meal, sunflower /
  groundnut / sesame / cotton → oilseed-meal, etc.).
* Direct legume grains for monogastric protein (soybean, dry-pea,
  chickpea, cowpea, gram, phaseolus-bean, pigeonpea, groundnut).
* Alfalfa and a 30 % share of DDGS (corn ethanol byproduct, via a
  multi-category override in ``feed_category_overrides.csv``).

Globally these routes do not cover the GLEAM3 [#gleam_protein]_
baseline. The shortfall maps cleanly onto specific real-world feed
sources that the model does not produce endogenously:

.. list-table:: Approximate global protein-feed sources outside the
   modelled crop-to-food pathways
   :header-rows: 1
   :widths: 30 15 55

   * - Source
     - Mt DM
     - Notes
   * - Fishmeal
     - ~5
     - Byproduct of the fish-processing industry; seafood is not
       modelled. Global production has been stable at roughly 5 Mt for
       the last decade, mostly fed to aquaculture, with a non-trivial
       share to monogastric livestock (pigs, poultry).
       [#fishmeal_iffo]_
   * - Synthetic amino acids
     - ~5
     - Industrial-fermentation L-lysine, DL-methionine, L-threonine,
       L-tryptophan and others added to monogastric rations. The
       feed-grade share of the global amino-acids market was about
       6 Mt in 2025 (52 % of a 12 Mt total). [#aa_market]_
   * - Rendered animal byproducts
     - ~8
     - Meat-and-bone meal, blood meal, feather meal, hydrolysed
       feathers, etc., fed primarily to monogastrics and pet food.
       The world's renderers process ~60 Mt/yr of raw animal
       byproducts, yielding ~8 Mt of rendered animal protein (plus
       ~8 Mt of fats). [#fao_rendering]_ GLEAM3 does not separately
       book these flows because they are an internal livestock
       recycling loop.
   * - Palm kernel cake (PKEXP)
     - ~10
     - Modelled in the model: an extra coproduct on the ``palm_oil``
       pathway in ``foods.csv``. See :ref:`palm-kernel-pathway`.
   * - Maize gluten meal / feed (MZGLTM, MZGLTF)
     - ~5 + ~10
     - Modelled in the model via the new ``maize_wetmill`` pathway in
       ``foods.csv``. See :ref:`maize-wetmill-pathway`.
   * - Sugar beet pulp (BPULP)
     - ~9
     - Modelled in the model as a coproduct on the
       ``sugarbeet_sugar`` pathway. Categorises as a ruminant grain
       (not protein) — included for completeness of GLEAM3's
       "By-products" intake bucket attribution. See
       :ref:`sugarbeet-pulp-pathway`.

For the genuinely unmodellable sources (fishmeal, synthetic AAs,
animal byproducts), the workflow adds a calibration-derived exogenous
supply that mirrors the existing
:ref:`grassland forage calibration <grassland-forage-calibration>`:

1. Solve the model in validation mode with both feed calibrations
   *disabled*. Positive slack on each
   ``feed:{monogastric,ruminant}_protein:{country}`` bus reveals the
   per-country gap.
2. ``compute_protein_feed_calibration`` writes those positive slacks
   to ``data/curated/calibration/exogenous_protein.csv``.
3. At solve time, ``_apply_protein_feed_calibration`` reads the CSV
   and adds free per-country generators on the matching protein feed
   buses. In validation / ``enforce_baseline_feed: true`` mode the
   generators are forced to dispatch at the listed amount (so the
   exogenous supply enters the mass balance unconditionally); in
   optimisation mode they are extendable up to the listed cap at zero
   marginal cost (the optimiser uses them if beneficial).

The calibration carrier is ``exogenous_protein_cal`` and the
generators are named ``supply:exogenous_{category}:{country}``.

**Configuration**:

.. code-block:: yaml

   feed_protein_calibration:
     enabled: true
     generate: false
     exogenous_protein: "data/curated/calibration/exogenous_protein.csv"
     scenario: "default"

When upstream feed or crop data changes, re-run

.. code-block:: bash

   tools/calibrate feed

to regenerate both the forage and protein calibration CSVs from a
fresh validation solve.

.. [#gleam_protein] FAO (2022), *GLEAM 3.0 Model Documentation*,
   Tables 3.1 / 3.5 list the protein feed categories
   (``MLSOY``, ``MLRAPE``, ``MLCTTN``, ``PKEXP``, ``MLOILSDS``,
   ``MZGLTM``, ``MZGLTF``, ``FISHMEAL``, ``SYNTHETIC``).
   `<https://www.fao.org/gleam/en/>`_

.. [#fishmeal_iffo] IFFO — The Marine Ingredients Organisation,
   *Key Facts*, reports global fishmeal production at roughly 5 Mt/yr
   over 1980–2020.
   `<https://www.iffo.com/key-facts>`_

.. [#aa_market] Industry-Experts and Fortune Business Insights value
   the 2025 global amino-acids market at ~12 Mt with animal feed
   accounting for 52 % of the volume, i.e. ~6 Mt of feed-grade amino
   acids; major drivers are L-lysine (fermentation) and DL-methionine
   (chemical synthesis).
   `<https://industry-experts.com/verticals/food-and-beverage/feed-amino-acids-a-global-market-overview>`_

.. [#fao_rendering] FAO (2004), *Protein Sources for the Animal Feed
   Industry* (Y5019E), reports that global rendering processes ~60 Mt
   of animal byproducts per year, yielding ~8 Mt of rendered animal
   proteins and ~8 Mt of rendered fats.
   `<http://www.fao.org/docrep/007/y5019e/y5019e0g.htm>`_

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

Grassland production is routed to the ``feed_ruminant_forage`` pool, alongside legumes and silage from crop-based forage. Manure management for all ruminant feed categories uses Mixed LPS parameters.

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
  - GLEAM feed codes → model mapping in ``data/curated/gleam/feed_mapping.csv``
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

The ``add_feed_supply_links()`` function creates links from crops, crop residues, and food byproducts to the seven feed pools:

**Item-to-Feed-Pool Links**:
  * **Inputs**: Crop, residue, or food byproduct buses (``bus0``)
  * **Outputs**: One of eight feed pool buses (``bus1``)
    - Ruminants: ``feed_ruminant_roughage``, ``feed_ruminant_forage``, ``feed_ruminant_grain``, ``feed_ruminant_protein``
    - Monogastrics: ``feed_monogastric_low_quality``, ``feed_monogastric_grain``, ``feed_monogastric_protein``
  * **Efficiency**: 1.0 (feed buses are in tonnes dry matter intake; digestibility is applied later in feed-to-animal efficiencies and emissions)
  * **Routing**: Each feed item is mapped via ``processing/{name}/ruminant_feed_mapping.csv`` and ``processing/{name}/monogastric_feed_mapping.csv`` (generated by ``categorize_feeds.py``) to the relevant pool(s)
  * Crops compete between human consumption, food processing, and animal feed use; residues and byproducts are exclusive to feed use

**Example flow**:
  * Wheat grain → ``feed_ruminant_grain`` + ``feed_monogastric_grain`` (digestibility from GLEAM)
  * Wheat straw (residue) → ``feed_ruminant_roughage`` (low digestibility)
  * Wheat bran (byproduct) → ``feed_ruminant_grain`` + ``feed_monogastric_low_quality``

Feed-to-Animal-Product Links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``add_feed_to_animal_product_links()`` function converts feed pools to
animal products with associated emissions and manure outputs.  Each link is
named ``animal:{product}_{feed_category}:{country}`` (carrier
``animal_production``):

**Multi-bus structure**:

* **bus0** (input): Feed pool bus (e.g., ``feed:ruminant_forage:USA``)
* **bus1** (output): Animal product food bus (e.g., ``food:meat-cattle:USA``)
* **bus2** (output): CH₄ emissions bus (``emission:ch4``) — enteric
  fermentation + manure methane
* **bus3** (output): Manure nitrogen to country fertilizer bus
  (``fertilizer:{country}``) — recycled as organic N
* **bus4** (output): N₂O emissions bus (``emission:n2o``) — from manure
  application and deposition

**Efficiency** (bus0 → bus1): Feed conversion ratio (tonnes retail product per
tonne feed DM), from the per-country efficiencies described above.

**CH₄ calculation** (efficiency2): Combines enteric fermentation (ruminants
only) and manure management (all animals):

.. math::

   \text{CH}_4\text{/t feed} = \text{MY}_\text{enteric} + \text{MY}_\text{manure}

where methane yields (MY) are in kg CH₄ per kg dry matter intake.

**Example**: Grass-fed beef from forage feed with enteric MY 23.3 g/kg and
manure MY 2.2 g/kg:

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

Rules are listed in pipeline order.  All rules are defined in
``workflow/rules/animals.smk``.

**prepare_gleam_feed_properties**
  * **Input**: GLEAM 3.0 supplement, feed mapping
  * **Output**: ``ruminant_feed_properties.csv``, ``monogastric_feed_properties.csv``
  * **Script**: ``workflow/scripts/prepare_gleam_feed_properties.py``

**categorize_feeds**
  * **Input**: Feed properties, enteric methane yields, ash content
  * **Output**: ``ruminant_feed_categories.csv``,
    ``monogastric_feed_categories.csv``, ``ruminant_feed_mapping.csv``,
    ``monogastric_feed_mapping.csv``
  * **Script**: ``workflow/scripts/categorize_feeds.py``

**compute_gleam3_me_requirements**
  * **Input**: GLEAM3 intakes/production, feed categories, Wirsenius data,
    country-region mapping
  * **Output**: ``gleam3_me_requirements.csv``
  * **Script**: ``workflow/scripts/compute_gleam3_me_requirements.py``

**build_feed_to_animal_products**
  * **Input**: ME requirements, ruminant/monogastric feed categories
  * **Output**: ``feed_to_animal_products.csv``
  * **Script**: ``workflow/scripts/build_feed_to_animal_products.py``

**compute_gleam3_feed_fractions**
  * **Input**: Foods, crop production, feed mappings
  * **Output**: ``gleam3_feed_fractions.csv``
  * **Script**: ``workflow/scripts/compute_gleam3_feed_fractions.py``

**build_grassland_yields**
  * **Input**: ISIMIP grassland yield NetCDF, resource classes, regions
  * **Output**: ``grassland_yields.csv``
  * **Script**: ``workflow/scripts/build_grassland_yields.py``

**prepare_feed_baseline**
  * **Input**: GLEAM3 intakes/production, feed fractions, ME requirements,
    FAOSTAT QCL, feed mappings, uncalibrated efficiencies
  * **Output**: ``feed_baseline.csv``
  * **Script**: ``workflow/scripts/prepare_feed_baseline.py``

**compute_grassland_calibration**
  * **Input**: Solved network (uncalibrated scenario)
  * **Output**: ``data/curated/calibration/grassland_yield.csv``, ``fodder_conversion.csv``, ``exogenous_forage.csv``
  * **Script**: ``workflow/scripts/compute_grassland_calibration.py``

All ``processing/`` outputs are prefixed with ``{name}/`` (the config name).
Livestock production is integrated into the ``build_model`` rule using the
grassland yields and feed conversion CSVs.

References
----------

.. [1] Wirsenius, S. (2000). *Human Use of Land and Organic Materials: Modeling the Turnover of Biomass in the Global Food System*. Chalmers University of Technology and Göteborg University, Sweden. ISBN 91-7197-886-0. https://publications.lib.chalmers.se/records/fulltext/827.pdf

.. [2] Organisation for Economic Co-operation and Development / Food and Agriculture Organization of the United Nations (2023). *OECD-FAO Agricultural Outlook 2023-2032*, Box 6.1: Meat. https://www.oecd.org/en/publications/oecd-fao-agricultural-outlook-2023-2032_08801ab7-en/full-report/meat_7b036d52.html#title-a5a1984180

.. [3] Havlík, P., Valin, H., Herrero, M., Obersteiner, M., Schmid, E., Rufino, M. C., ... & Notenbaert, A. (2014). Climate change mitigation through livestock system transitions. *Proceedings of the National Academy of Sciences*, 111(10), 3709-3714, https://doi.org/10.1073/pnas.130804411. See the supporting information, Section 2.4.

.. [4] FAO (2022). Global Livestock Environmental Assessment Model (GLEAM) version 3.0. Rome. https://www.fao.org/gleam/. Country-level feed intake and production data (reference year 2015), obtained directly from FAO upon request under CC BY 4.0, used as the feed baseline in this model.

.. [5] FAO (2023). *Pathways towards lower emissions – A global assessment of the greenhouse gas emissions and mitigation options from livestock agrifood systems*. Rome. https://doi.org/10.4060/cc9029en. This GLEAM 3.0-based assessment (2015 baseline) reports updated global feed totals reflecting improved efficiencies relative to the GLEAM 2.0 (2010) estimates.

.. [6] National Research Council (2000). *Nutrient Requirements of Beef Cattle: Seventh Revised Edition: Update 2000*. Washington, DC: The National Academies Press. https://doi.org/10.17226/9791. Source of the California Net Energy System used for ruminant ME→NE conversion (k_m, k_g).

.. [7] National Academies of Sciences, Engineering, and Medicine (2016). *Nutrient Requirements of Beef Cattle: Eighth Revised Edition*. Washington, DC: The National Academies Press. https://doi.org/10.17226/19014. Provides the updated cubic-in-ME equations for k_m and k_g; evaluated at q ≈ 0.60 these give k_m ≈ 0.65 and k_g ≈ 0.43.

.. [8] National Research Council (2001). *Nutrient Requirements of Dairy Cattle: Seventh Revised Edition, 2001*. Washington, DC: The National Academies Press. https://doi.org/10.17226/9825. Specifies the fixed ME-to-NEL efficiency k_l = 0.64 used for dairy.
