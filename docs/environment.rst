.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Environmental Impacts
=====================

Overview
--------

The environmental module accounts for greenhouse gas emissions and land use change from food production. These impacts are monetized and included in the objective function via configurable prices/penalties. Land-use change accounting distinguishes between existing cropland and newly converted area so that only new conversions bear LUC costs, while existing cropland can generate regrowth credits when spared.

This is currently a work in progress and not all relevant environmental impacts are implemented and monetized yet.

Greenhouse Gas Emissions
-------------------------

The model tracks three major greenhouse gases using 100-year global warming potentials (GWP100):

* **CO₂** (GWP = 1): From land use change
* **CH₄** (GWP = 27 by default): From enteric fermentation (ruminants), rice paddies, manure
* **N₂O** (GWP = 273 by default): From nitrogen fertilizer application, manure, crop residue incorporation

All emissions are aggregated to CO₂-equivalent (internally tracked in MtCO₂-eq; the configured price still applies per tonne) for carbon pricing.

Implementation notes (buses, stores, links)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The optimisation model represents environmental flows with three PyPSA components that are worth keeping in mind:

* **Buses** act as balance sheets. Process components report raw emissions to the ``co2`` and ``ch4`` buses, while a dedicated ``ghg`` bus tracks the combined CO₂-equivalent balance.
* **Links** move quantities between buses, applying efficiencies that encode global warming potentials. ``convert_co2_to_ghg`` has efficiency 1.0, and ``convert_ch4_to_ghg`` uses the configured ``emissions.ch4_to_co2_factor``; similar for N₂O. Every megatonne of CH₄ and N₂O (after scaling from tonnes) therefore appears on the ``ghg`` bus weighted by its 100-year GWP.
* **Stores** accumulate quantities over the horizon. The extendable ``ghg`` store sits on the combined bus and is priced at ``emissions.ghg_price``. Because neither the ``co2``, ``ch4`` nor ``n2o`` buses have stores, their flows must pass through the conversion links before the objective is charged.

With this structure the linear program keeps separate ledgers for each greenhouse gas while charging the objective using a single priced stock of CO₂-equivalent. Scenario files can tighten or relax climate policy simply by changing the configuration values—no code modifications are required.

Land representation in the network
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Land is represented with a dual-pool structure per (region, resource class):

* ``land:cropland:*`` buses hold the usable cropland area that crop production links consume (per region/class/water).
* ``land:pasture:*`` buses hold the pasture area that grassland production links consume (per region/class, water-agnostic).
* ``land:existing_cropland:*`` buses supply the baseline cropland area (from ``processing/{name}/cropland_baseline_by_class.csv``) via fixed-capacity generators and links into both pools.
* ``land:new:*`` buses supply expansion land up to the configured regional limit; conversion links route this expansion into either pool and emit CO₂ according to the relevant LEFs.
* ``land:existing_pasture:*`` buses supply grazing-only land that flows exclusively to the pasture pool.

Crop production links draw from ``land:cropland:*``; grassland production links draw from ``land:pasture:*``. LUC emissions are carried on the conversion links, not on production links. When validation fixes harvested areas, optional slack generators attach to both pool types.

Sources of Emissions
~~~~~~~~~~~~~~~~~~~~

**Crop Production**:
  * N₂O from synthetic fertilizer application (direct and indirect)
  * CH₄ from flooded rice cultivation
  * N₂O from crop residues incorporated into soil

**Livestock**:
  * CH₄ from enteric fermentation (ruminants) - see :ref:`enteric-fermentation`
  * CH₄ and N₂O from manure management (all animals) - see :ref:`manure-management`
  * CO₂ from feed production (indirect)

**Land Use Change**:
  * CO₂ from converting natural land to new cropland (charged on ``convert_new_land_*`` links)
  * Soil carbon losses embodied in the cropland LEFs; spared land on existing cropland can generate regrowth credits

Direct N₂O emission factors
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model uses the 2019 Refinement to the IPCC Guidelines for National Greenhouse Gas Inventories to parameterise direct N₂O emissions from managed soils. Table 11.1 (updated) is reproduced below to make the default emission factors and their uncertainty ranges readily accessible when configuring fertilizer-related pathways.

.. list-table:: Default emission factors to estimate direct N₂O emissions from managed soils (IPCC, 2019 Refinement - Table 11.1)
   :header-rows: 1
   :widths: 32 11 14 25 9 9

   * - Emission factor
     - Aggregated default value
     - Aggregated uncertainty range
     - Disaggregation
     - Default value
     - Uncertainty range
   * - EF\ :sub:`1` for N additions from synthetic fertilisers, organic amendments and crop residues, and N mineralised from mineral soil as a result of loss of soil carbon [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.010
     - 0.002 – 0.018
     - Synthetic fertiliser inputs in wet climates

       Other N inputs in wet climates

       All N inputs in dry climates
     - 0.016 (wet synthetic)

       0.006 (wet other)

       0.005 (dry)
     - 0.013 – 0.019

       0.001 – 0.011

       0.000 – 0.011
   * - EF\ :sub:`1FR` for flooded rice fields [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.004
     - 0.000 – 0.029
     - Continuous flooding

       Single and multiple drainage
     - 0.003

       0.005
     - 0.000 – 0.010

       0.000 – 0.016
   * - EF\ :sub:`3PRP,CPP` for cattle (dairy, non-dairy and buffalo), poultry and pigs [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.004
     - 0.000 – 0.014
     - Wet climates

       Dry climates
     - 0.006

       0.002
     - 0.000 – 0.027

       0.000 – 0.007
   * - EF\ :sub:`3PRP,SO` for sheep and "other animals" [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.003
     - 0.000 – 0.010
     - –
     - –
     - –

Crop Residue Incorporation (N₂O)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Crop residues left on the field and incorporated into the soil contribute to direct N₂O emissions. The model applies the IPCC EF\ :sub:`1` emission factor to residue nitrogen content to calculate these emissions.

Methodology
^^^^^^^^^^^

N₂O emissions from incorporated crop residues are calculated as:

.. math::

   \text{N}_2\text{O} = \text{Residue}_\text{DM} \times \text{N}_\text{content} \times \text{EF}_1 \times \frac{44}{28}

where:
  * **Residue**\ :sub:`DM` is the dry matter of crop residues incorporated into soil (tonnes DM)
  * **N**\ :sub:`content` is the nitrogen content of the residue (kg N per kg DM)
  * **EF**\ :sub:`1` is the IPCC direct emission factor for N inputs (kg N₂O-N per kg N input) = 0.010 (aggregated default)
  * **44/28** converts N₂O-N to N₂O mass

Residue Management Constraints
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To ensure soil health and prevent degradation, the model limits the fraction of crop residues that can be removed for animal feed. The remainder must be left on the field and incorporated into the soil.

* **Maximum removal for feed**: 30% of generated residues (configurable via ``residues.max_feed_fraction``; override per ISO3 country or M49 region/sub-region via ``residues.max_feed_fraction_by_region`` with country > sub-region > region)
* **Minimum soil incorporation**: 70% of generated residues

This constraint is implemented as:

.. math::

   \text{feed use} \leq \frac{0.30}{0.70} \times \text{incorporation}

The constraint ensures that residue removal for feed does not compromise soil organic matter maintenance and nutrient cycling.

Data Sources
^^^^^^^^^^^^

* **Residue N content**: ``processing/{name}/ruminant_feed_categories.csv``, column ``N_g_per_kg_DM``, derived from GLEAM 3.0 [2]_ Supplement S1, Table S.3.3
* **Emission factor**: IPCC 2019 Refinement, Table 11.1 (EF\ :sub:`1` aggregated default = 0.010)
* **Removal limits**: Model assumption based on sustainable residue management practices

Rice Cultivation (CH₄)
~~~~~~~~~~~~~~~~~~~~~~~

Flooded rice paddies are a major source of methane emissions due to anaerobic decomposition of organic matter in the soil.

Methodology
^^^^^^^^^^^

The model applies a per-hectare emission factor to wetland rice production, distinguishing between irrigated and rainfed water regimes:

.. math::

   \text{CH}_4 = \text{Area}_\text{irrigated} \times \text{EF}_\text{base} + \text{Area}_\text{rainfed} \times \text{EF}_\text{base} \times \text{SF}_\text{rainfed}

where:
  * **Area** is the harvested area of wetland rice (hectares) by water supply
  * **EF**\ :sub:`base` is the baseline methane emission factor for continuously flooded fields (kg CH₄ per hectare per crop cycle)
  * **SF**\ :sub:`rainfed` is the scaling factor for the rainfed water regime (dimensionless)

Configuration
^^^^^^^^^^^^^

The emission parameters are configured via ``emissions.rice``:

* **methane_emission_factor_kg_per_ha**: Baseline factor for continuously flooded fields (~134.5 kg CH₄/ha/crop). Based on the IPCC 2019 Tier 1 global default daily emission factor (1.19 kg CH₄/ha/day) and cultivation period (113 days).
* **rainfed_wetland_rice_ch4_scaling_factor**: Scaling factor for "Regular rainfed" fields (0.54). Reduces emissions to account for non-continuous flooding.

Dryland (upland) rice is assumed to have zero methane emissions.

Reference
^^^^^^^^^
IPCC 2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas Inventories, Volume 4, Chapter 5.5, Tables 5.11, 5.11A, and 5.12.

.. _enteric-fermentation:

Enteric Fermentation (CH₄)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ruminant livestock (cattle, sheep, goats, buffalo) produce methane during digestion through microbial fermentation in the rumen. The model calculates enteric CH₄ emissions using IPCC Tier 2 methodology based on feed-specific methane yields.

Methodology
^^^^^^^^^^^

Enteric methane emissions are calculated as:

.. math::

   \text{CH}_4 = \text{DMI} \times \text{MY}_\text{enteric}

where:
  * **DMI** is dry matter intake (kg feed/day or t feed/year)
  * **MY**\ :sub:`enteric` is the enteric methane yield (g CH₄ per kg DMI)

The methane yield depends primarily on feed digestibility and fiber content. Higher-quality feeds (grains, concentrates) produce less CH₄ per unit intake than low-quality forages because they ferment more efficiently with less methane as a byproduct.

IPCC Conversion Factors
^^^^^^^^^^^^^^^^^^^^^^^^

The model uses methane yields from the `2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas Inventories <https://www.ipcc.ch/report/2019-refinement-to-the-2006-ipcc-guidelines-for-national-greenhouse-gas-inventories/>`_ [1]_, Volume 4, Chapter 10, Table 10.12 (Methane Conversion Factors for Cattle and Buffalo).

Feed categories are mapped to IPCC dietary classes:

* **Roughage** (23.3 g CH₄/kg DMI): High-forage diets >75% forage, digestible energy (DE) ≤ 62%, typical of extensive grazing systems
* **Forage** (21.0 g CH₄/kg DMI): Mixed rations 15-75% forage with grain/silage, DE 62-71%, typical of semi-intensive dairy and beef
* **Grain** (13.6 g CH₄/kg DMI): Concentrate-based feedlot diets 0-15% forage, DE ≥ 72%, typical of intensive finishing systems
* **Protein** (13.6 g CH₄/kg DMI): High-protein concentrates, same as grain category

Monogastric animals (pigs, poultry) produce negligible enteric methane and are not included in this calculation.

Data Sources
^^^^^^^^^^^^

* **IPCC values**: ``data/ipcc_enteric_methane_yields.csv`` maps feed categories to MY values from IPCC (2019) Table 10.12
* **Feed properties**: ``processing/{name}/ruminant_feed_categories.csv`` generated from GLEAM 3.0 [2]_ Supplement S1, Table S.3.3 (Ruminant Nutrition Parameters)
* **Feed mapping**: ``data/gleam_feed_mapping.csv`` links model feed items to GLEAM feed categories

Implementation
^^^^^^^^^^^^^^

Enteric emissions are calculated in ``workflow/scripts/build_model.py`` within the ``add_feed_to_animal_product_links()`` function:

1. Feed items are categorized by digestibility into roughage/forage/grain/protein pools (``workflow/scripts/categorize_feeds.py``)
2. Each category is assigned an MY value from ``data/ipcc_enteric_methane_yields.csv``
3. For each animal production link, CH₄ emissions per tonne of feed intake are calculated and attached to ``bus2`` (methane bus)
4. Emissions scale linearly with feed consumption in the optimization

.. _manure-management:

Manure Management (CH₄)
~~~~~~~~~~~~~~~~~~~~~~~

Livestock in confined systems produce methane emissions from manure storage, handling, and treatment. Unlike enteric fermentation, manure CH₄ affects both ruminants and monogastrics (pigs, poultry), with emissions varying significantly by management system. However, **manure deposited directly on pasture during grazing produces negligible CH₄** because aerobic decomposition dominates (IPCC MCF ~0.5% for "Pasture, Range & Paddock"). The model therefore excludes manure CH₄ for grassland feed categories.

Methodology
^^^^^^^^^^^

Manure methane emissions follow IPCC Tier 2 methodology based on volatile solids excretion and system-specific methane conversion factors:

.. math::

   \text{CH}_4\text{_manure} = \text{VS} \times B_0 \times \text{MCF} \times 0.67

where:
  * **VS** is volatile solids excretion (kg VS per kg feed DM intake)
  * **B**\ :sub:`0` is maximum methane producing capacity (m³ CH₄ per kg VS)
  * **MCF** is the methane conversion factor (fraction 0-1, varies by management system and climate)
  * **0.67** converts m³ CH₄ to kg CH₄

Volatile Solids Calculation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Volatile solids represent the organic fraction of manure available for anaerobic decomposition. The model calculates VS using an adapted version of IPCC Equation 10.24:

.. math::

   \text{VS} = (1 - \text{digestibility} + \text{UE}) \times (1 - \text{ash}/100)

where:
  * **Digestibility** is the fraction of feed digested by the animal (from GLEAM feed properties)
  * **UE** is urinary energy excretion as a fraction of gross energy intake:

    * 0.04 for ruminants (cattle, sheep, goats)
    * 0.02 for pigs
    * 0.00 for poultry (minimal urinary losses)

  * **Ash** is the ash content of feed (% dry matter, from ``data/feed_ash_content.csv`` based on `feedtables.com <https://www.feedtables.com/>`_)

The formula accounts for:
  * Undigested feed (1 - digestibility)
  * Urinary excretion (UE)
  * Mineral content that doesn't decompose (ash fraction)

Maximum Methane Producing Capacity (B₀)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

B₀ represents the theoretical maximum CH₄ yield from complete anaerobic digestion of manure volatile solids. Values are animal-specific:

.. list-table:: B₀ values by animal product (IPCC 2019, Table 10.16)
   :header-rows: 1
   :widths: 40 30 30

   * - Animal Product
     - B₀ (m³ CH₄/kg VS)
     - Source
   * - Dairy cattle
     - 0.24
     - IPCC Table 10.16, high productivity
   * - Beef cattle
     - 0.18
     - IPCC Table 10.16, high productivity
   * - Pigs
     - 0.45
     - IPCC Table 10.16
   * - Poultry (broilers)
     - 0.36
     - IPCC Table 10.16
   * - Poultry (layers/eggs)
     - 0.39
     - IPCC Table 10.16

Data source: ``data/ipcc_manure_methane_producing_capacity.csv``

Methane Conversion Factors (MCF)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

MCF represents the fraction of B₀ actually realized under specific management conditions. It varies by:

* **Management system**: Liquid systems (lagoons, slurry pits) have high MCF (0.4-0.8), solid systems (composting, daily spread) have low MCF (0.001-0.05)
* **Climate zone**: Warmer climates increase anaerobic activity and MCF
* **Storage duration**: Longer storage increases MCF

The model uses MCF values from IPCC (2019) Table 10.17, which provides system-specific and climate-specific factors for 21 manure management systems.

**Current simplification**: MCF values are averaged across climate zones for each management system (``workflow/scripts/calculate_manure_emissions.py``). This will be refined when climate zone data is added to modeling regions, allowing for country-specific and region-specific emission factors.

Manure Management System Distribution
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Real-world manure CH₄ emissions reflect a weighted average across multiple management practices. The model uses global system distributions from `GLEAM 3.0 <https://foodandagricultureorganization.shinyapps.io/GLEAMV3_Public/>`_ [2]_ (Supplement S1, Tables 4.4 and 4.5):

* **Cattle**: Primarily pasture/paddock (low MCF ~0.005) with some confinement and liquid systems
* **Pigs**: Mix of solid storage, liquid slurry, and pit systems (higher MCF ~0.1-0.4)
* **Poultry**: Mostly litter-based and solid systems (moderate MCF ~0.01-0.04)

The weighted MCF is calculated as:

.. math::

   \text{MCF}_\text{weighted} = \sum_{i} f_i \times \text{MCF}_i

where **f**\ :sub:`i` is the fraction of manure managed in system *i* (from ``data/gleam_tables/manure_management_systems_fraction.csv``).

Data Sources
^^^^^^^^^^^^

* **B₀ values**: ``data/ipcc_manure_methane_producing_capacity.csv`` (IPCC 2019 Table 10.16)
* **MCF values**: ``data/ipcc_manure_methane_conversion_factors.csv`` (IPCC 2019 Table 10.17)
* **MMS distributions**: ``data/gleam_tables/manure_management_systems_fraction.csv`` (GLEAM 3.0 Supplement S1)
* **Ash content**: ``data/feed_ash_content.csv`` (from feedtables.com, matched to model feed entities)
* **Feed properties**: ``processing/{name}/ruminant_feed_categories.csv`` and ``processing/{name}/monogastric_feed_categories.csv`` (digestibility from GLEAM 3.0)

Implementation
^^^^^^^^^^^^^^

Manure emissions are calculated in ``workflow/scripts/calculate_manure_emissions.py`` and integrated into the model via ``workflow/scripts/build_model.py``:

1. **Preprocessing** (``calculate_manure_emissions.py``):

   * Calculate VS excretion for each feed category using digestibility and ash content
   * Average MCF across climate zones for each management system (temporary simplification)
   * Compute weighted MCF for each animal product using GLEAM MMS distributions
   * Calculate CH₄ emissions per kg feed intake: VS × B₀ × MCF\ :sub:`weighted` × 0.67
   * Generate ``processing/{name}/manure_ch4_emission_factors.csv`` with emissions by country, product, and feed category

2. **Model integration** (``build_model.py``):

   * Load manure emission factors from ``processing/{name}/manure_ch4_emission_factors.csv``
   * In ``add_feed_to_animal_product_links()``, combine enteric and manure CH₄:

     .. math::

        \text{CH}_4\text{/t feed} = \text{MY}_\text{enteric} + \text{MY}_\text{manure}

   * **Exception**: For grassland feed categories (``ruminant_grassland``), manure CH₄ is set to zero because pasture-deposited manure decomposes aerobically with negligible methane production
   * Attach total CH₄ to ``bus2`` (methane bus) for all animal production links
   * Emissions scale with feed consumption in the optimization

Example Calculation
^^^^^^^^^^^^^^^^^^^^

**Scenario**: Dairy cow fed on forage (ruminant_forage category)

**Parameters**:
  * Digestibility: 0.61 (from GLEAM)
  * Ash content: 7.15% (average for forage feeds)
  * Urinary fraction (UE): 0.04 (ruminants)
  * B₀ (dairy): 0.24 m³ CH₄/kg VS
  * Weighted MCF (dairy, global average): 0.034 (mostly pasture-based)

**Calculation**:

1. VS excretion:

   .. math::

      \text{VS} = (1 - 0.61 + 0.04) \times (1 - 7.15/100) = 0.43 \times 0.9285 = 0.399 \text{ kg VS/kg DMI}

2. Manure CH₄:

   .. math::

      \text{CH}_4 = 0.399 \times 0.24 \times 0.034 \times 0.67 = 0.00217 \text{ kg CH₄/kg DMI} = 2.17 \text{ g CH₄/kg DMI}

3. Total CH₄ (enteric + manure):

   .. math::

      \text{Total} = 21.0 \text{ (enteric)} + 2.17 \text{ (manure)} = 23.17 \text{ g CH₄/kg DMI}

This shows that for dairy cattle on forage diets, manure contributes ~9% of total CH₄ emissions, with enteric fermentation being dominant. For monogastrics (pigs, poultry), where enteric emissions are zero, manure is the sole CH₄ source.

Manure Nitrogen Management
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In addition to methane emissions, the model tracks nitrogen flows from livestock manure, accounting for both the fertilizer value and N₂O emissions from manure application.

**Nitrogen Mass Balance**

Nitrogen excreted in manure is calculated from a simple mass balance:

.. math::

   N_\text{excretion} = N_\text{feed} - N_\text{product}

where:
  * **N**\ :sub:`feed` is the nitrogen content of feed (from GLEAM feed properties, g N/kg DM)
  * **N**\ :sub:`product` is the nitrogen content of the animal product, derived from protein content

Protein-to-Nitrogen Conversion
"""""""""""""""""""""""""""""""

Animal product nitrogen content is calculated from protein using the standard Jones factor of 6.25:

.. math::

   N_\text{product} = \frac{\text{Protein}}{6.25}

This factor reflects that proteins average ~16% nitrogen by mass (1/6.25 ≈ 0.16). While specific proteins vary (5.18-6.38), 6.25 is the `FAO-recommended general conversion factor <https://www.fao.org/4/y5022e/y5022e03.htm>`_ for mixed animal products [3]_.

Protein content is sourced from USDA FoodData Central (``data/nutrition.csv``).

Manure Nitrogen as Fertilizer
""""""""""""""""""""""""""""""

Not all excreted nitrogen becomes available as fertilizer due to volatilization and other losses during storage and handling. The model applies a configurable recovery fraction:

.. math::

   N_\text{fertilizer} = N_\text{excretion} \times f_\text{recovery}

where **f**\ :sub:`recovery` is configured via ``fertilizer.manure_n_to_fertilizer`` (default: 0.75, representing 75% recovery and 25% losses).

This manure N is added to the global fertilizer pool (``n_fertilizer`` bus) where it competes with and substitutes for synthetic fertilizer, subject to the global fertilizer limit.

**Special Case: Grazing Systems**

For animals fed from the ``ruminant_grassland`` feed category (pasture grazing), manure is deposited directly on pasture and not collected. For these systems:

* **N**\ :sub:`fertilizer` = 0 (no manure collection)
* N₂O emissions are still calculated from the full excreted nitrogen (representing pasture deposition emissions)

This distinction reflects the practical reality that grazing manure cannot be redistributed to cropland, while maintaining accurate N₂O accounting for pasture emissions.

N₂O Emissions from Manure Application
""""""""""""""""""""""""""""""""""""""

Applied manure nitrogen produces both direct and indirect N₂O emissions following IPCC 2019 Refinement Tier 1 methodology (Chapter 11, Equations 11.1, 11.9, 11.10):

**Direct N₂O emissions** (Equation 11.1):

.. math::

   N_2O_\text{direct} = N_\text{applied} \times EF_1 \times \frac{44}{28}

**Indirect N₂O from volatilization and atmospheric deposition** (Equation 11.9):

.. math::

   N_2O_\text{vol} = N_\text{applied} \times Frac_\text{GASM} \times EF_4 \times \frac{44}{28}

**Indirect N₂O from leaching and runoff** (Equation 11.10):

.. math::

   N_2O_\text{leach} = N_\text{applied} \times Frac_\text{LEACH} \times EF_5 \times \frac{44}{28}

**Total N₂O emissions**:

.. math::

   N_2O_\text{total} = N_2O_\text{direct} + N_2O_\text{vol} + N_2O_\text{leach}

where:
  * **N**\ :sub:`applied` is manure N applied to soil (F\ :sub:`ON`) or deposited on pasture (F\ :sub:`PRP`)
  * **EF**\ :sub:`pasture` = EF\ :sub:`3PRP` from IPCC Table 11.1 (0.02 for cattle/buffalo, 0.01 for others)
  * **EF**\ :sub:`managed` = weighted storage EF (Table 10.21) + recovery × application EF (0.006)
  * **Frac**\ :sub:`GASM` = 0.21 kg NH₃-N + NOₓ-N per kg N (volatilization fraction for organic N, IPCC Table 11.3)
  * **EF**\ :sub:`4` = 0.010 kg N₂O-N per kg volatilized N (indirect volatilization/deposition factor, IPCC Table 11.3)
  * **Frac**\ :sub:`LEACH` = 0.24 kg N per kg N (leaching fraction in wet climates, IPCC Table 11.3)
  * **EF**\ :sub:`5` = 0.011 kg N₂O-N per kg leached N (indirect leaching/runoff factor, IPCC Table 11.3)
  * **44/28** converts N₂O-N to N₂O (molecular weight ratio)

The direct emission factors are calculated from Manure Management System (MMS) distributions
from GLEAM, combined with IPCC emission factors for each MMS type. This accounts for the
different N₂O characteristics of different management systems (pasture deposition, solid storage,
liquid systems, deep litter, etc.).

Configuration
"""""""""""""

Manure nitrogen management is configured under ``fertilizer`` and ``emissions.fertilizer``:

.. code-block:: yaml

   fertilizer:
     manure_n_to_fertilizer: 0.75  # Fraction of excreted N available as fertilizer

   emissions:
     fertilizer:
       # Note: Direct N2O factors are computed from MMS distributions (see calculate_manure_emissions.py)
       indirect_ef4: 0.010           # kg N₂O-N per kg volatilized N
       indirect_ef5: 0.011           # kg N₂O-N per kg leached N
       frac_gasm: 0.21               # Fraction of organic N volatilized
       frac_leach: 0.24              # Fraction of N leached (wet climate)

Implementation
""""""""""""""

Manure N₂O emission factors are preprocessed in ``workflow/scripts/calculate_manure_emissions.py``:

1. For each (product, feed_category) combination, calculate MMS-weighted emission factors:

   * Load MMS distributions from GLEAM (``data/gleam_tables/manure_management_systems_fraction.csv``)
   * Map feed categories to Livestock Production Systems (LPS):

     - ``ruminant_grassland`` → Grassland LPS (high pasture fraction)
     - Other ruminant categories → Mixed LPS (moderate pasture fraction)
     - Monogastrics → Industrial/Intermediate LPS (low pasture fraction)

   * Calculate weighted N₂O factors from ``data/ipcc_manure_n2o_emission_factors.csv``:

     - **pasture_fraction**: Share of manure deposited on pasture
     - **pasture_n2o_ef**: EF\ :sub:`3PRP` (0.02 for cattle, 0.01 for others)
     - **managed_n2o_ef**: Storage EF + (recovery × application EF)

2. In ``workflow/scripts/build_model/utils.py``, the ``_calculate_manure_n_outputs()`` function:

   * Calculates N excretion from feed N content (GLEAM) minus product N content (protein ÷ 6.25)
   * Splits excreted N between pasture and managed fractions using MMS-based ``pasture_fraction``
   * Applies appropriate emission factors to each fraction:

     - Pasture: N × pasture_n2o_ef + indirect emissions
     - Managed: N × managed_n2o_ef + indirect emissions

   * Computes ``pasture_n2o_share`` for plotting breakdown

3. Attach outputs to the link:

   * ``bus3``: ``fertilizer_{country}`` (manure N contributing to fertilizer pool)
   * ``bus4``: ``n2o`` (total N₂O emissions including direct and indirect)

This creates a closed nutrient cycle where livestock manure offsets synthetic fertilizer demand while incurring proportional N₂O emissions, with grazing systems correctly accounting for on-pasture deposition and all N sources including indirect emission pathways.

Example Calculation
"""""""""""""""""""

**Scenario**: Beef cattle on forage diet (ruminant_forage)

**Parameters**:
  * Feed N: 19.5 g N/kg DM (from GLEAM)
  * Product protein: 18.59 g/100g (meat-cattle, from USDA FoodData Central)
  * Product N: 18.59 ÷ 6.25 = 2.97 g N/100g = 29.7 g N/kg
  * Feed conversion efficiency: 0.15 (6.67 kg feed per kg product)
  * Recovery fraction: 0.75
  * Emission factors: EF\ :sub:`1` = 0.010, EF\ :sub:`4` = 0.010, EF\ :sub:`5` = 0.011
  * Fractions: Frac\ :sub:`GASM` = 0.21, Frac\ :sub:`LEACH` = 0.24

**Calculation** (per tonne of feed DM):

1. N inputs and outputs:

   .. math::

      N_\text{feed} &= 19.5 \text{ g/kg} = 0.0195 \text{ t N/t feed} \\
      \text{Product output} &= 0.15 \text{ t product/t feed} \\
      N_\text{product} &= 29.7 \text{ g/kg} \times 0.15 \text{ t/t} = 0.00446 \text{ t N/t feed}

2. N excretion:

   .. math::

      N_\text{excretion} = 0.0195 - 0.00446 = 0.0150 \text{ t N/t feed}

3. Manure N fertilizer (collected manure):

   .. math::

      N_\text{applied} = N_\text{fertilizer} = 0.0150 \times 0.75 = 0.0113 \text{ t N/t feed}

4. N₂O emissions (direct + indirect):

   .. math::

      N_2O_\text{direct} &= 0.0113 \times 0.010 \times \frac{44}{28} = 0.000178 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{vol} &= 0.0113 \times 0.21 \times 0.010 \times \frac{44}{28} = 0.000037 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{leach} &= 0.0113 \times 0.24 \times 0.011 \times \frac{44}{28} = 0.000043 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{total} &= 0.000178 + 0.000037 + 0.000043 = 0.000258 \text{ t N}_2\text{O/t feed}

**Result**: Each tonne of feed produces 11.3 kg of manure N (contributing to the fertilizer pool) and 258 g of total N₂O emissions (178 g direct + 37 g volatilization + 43 g leaching).

**Grazing Example**: Beef cattle on pasture (ruminant_grassland)

Using the same parameters as above but with pasture grazing:

1-2. N excretion remains the same: 0.0150 t N/t feed

3. Manure N fertilizer (grazing):

   .. math::

      N_\text{fertilizer} = 0 \text{ (manure deposited on pasture, not collected)}

4. N₂O emissions from pasture deposition (direct + indirect, all excreted N):

   .. math::

      N_\text{applied} &= 0.0150 \text{ t N/t feed (all excreted N)} \\
      N_2O_\text{direct} &= 0.0150 \times 0.010 \times \frac{44}{28} = 0.000236 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{vol} &= 0.0150 \times 0.21 \times 0.010 \times \frac{44}{28} = 0.000050 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{leach} &= 0.0150 \times 0.24 \times 0.011 \times \frac{44}{28} = 0.000057 \text{ t N}_2\text{O/t feed} \\
      N_2O_\text{total} &= 0.000236 + 0.000050 + 0.000057 = 0.000343 \text{ t N}_2\text{O/t feed}

**Result**: No manure N enters the fertilizer pool, but 343 g total N₂O per tonne feed is emitted from pasture deposition (236 g direct + 50 g volatilization + 57 g leaching). Higher than confined systems since all excreted N remains on pasture and is subject to emissions.

Future Refinements
^^^^^^^^^^^^^^^^^^

Planned improvements to manure emissions modeling:

* **Climate zone differentiation**: Use actual climate zones for each region instead of averaging MCF across zones and using wet climate assumption for all regions
* **Country-specific MMS distributions**: Currently all countries use global GLEAM averages
* **Manure management system emissions**: Differentiate N₂O emission factors by storage system (currently uses field-application factor for all)

.. [3] FAO (2003). *Food energy - methods of analysis and conversion factors*. FAO Food and Nutrition Paper 77. Report of a Technical Workshop, Rome, 3-6 December 2002. https://www.fao.org/4/y5022e/y5022e03.htm

Carbon Pricing
~~~~~~~~~~~~~~

All GHG emissions (CO₂, CH₄, N₂O) are priced at a configurable rate:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: emissions ---
   :end-before: # --- section: crops ---

.. [1] IPCC (2019). *2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas Inventories*, Volume 4: Agriculture, Forestry and Other Land Use. https://www.ipcc.ch/report/2019-refinement-to-the-2006-ipcc-guidelines-for-national-greenhouse-gas-inventories/

.. [2] FAO (2022). *Global Livestock Environmental Assessment Model (GLEAM) 3.0*. Food and Agriculture Organization of the United Nations. https://www.fao.org/gleam/

.. _luc-emissions:

Land Use Change
---------------

Land-use change (LUC) emissions capture the carbon consequences of converting land between natural vegetation, cropland, pasture, and spared (actively rewilded) states. The model derives annualized per-hectare coefficients for each resource class and water supply that quantify the net CO₂ flux associated with allocating an additional hectare to a specific land use.

Conceptual overview
~~~~~~~~~~~~~~~~~~~

For every grid cell on the common suitability grid, the workflow computes three main quantities:

* **Pulse emissions (:math:`P_{i,u}`)** – the one-off release (or uptake) that occurs when land transitions from its natural state to land use :math:`u` (cropland or pasture). We estimate above-ground biomass (AGB), below-ground biomass (BGB), and soil organic carbon (SOC) stocks for both the natural and agricultural equilibria, then convert the difference to CO₂ using the stoichiometric factor :math:`44/12`.
* **Annual regrowth (:math:`R_i`)** – the ongoing sequestration potential when land is spared and allowed to regrow. Regrowth rates are derived from Cook-Patton & Griscom (2020), which quantifies carbon accumulation in young regenerating forests. Credits are only granted where current above-ground biomass is below a threshold (default 20 tC/ha), ensuring that only recently cleared or degraded land—not mature forest—receives regrowth credits.
* **Managed flux (:math:`M_{i,u}`)** – ongoing emissions from managed systems (e.g., peat oxidation, continuous tillage). The current implementation sets :math:`M_{i,u} = 0` everywhere as a simplifying assumption.

The per-hectare land-use change factor (LEF) for **conversion** (cropland or pasture) includes only amortised pulse emissions:

.. math::

   \mathrm{LEF}_{\mathrm{crop}} = \frac{P_{\mathrm{crop}}}{H}, \quad \mathrm{LEF}_{\mathrm{pasture}} = \frac{P_{\mathrm{pasture}}}{H}

The LEF for **spared land** provides sequestration credits through regrowth:

.. math::

   \mathrm{LEF}_{\mathrm{spared}} = \begin{cases} -R_i & \text{if AGB} \leq \text{threshold} \\ 0 & \text{otherwise} \end{cases}

Regrowth is *not* included in the conversion LEFs to avoid double-counting: the model explicitly represents the reforestation alternative via separate ``spare_land`` links. Area-weighted aggregation over resource classes produces region-level coefficients that the optimisation layer consumes.

Application in the optimisation distinguishes new conversion from existing area: cropland LEFs are charged only when land expands (``convert_new_land_*``), while baseline cropland can be spared to earn the spared LEF. Pasture LEFs attach to grazing supply links so that expanding pasture bears its conversion cost.

Input datasets
~~~~~~~~~~~~~~

The LUC pipeline harmonises several global datasets to the common grid:

* Land cover fractions and forest masks from Copernicus ESA CCI land cover (:ref:`copernicus-land-cover`)
* Above-ground biomass from ESA Biomass CCI v6.0 (:ref:`esa-biomass-cci`)
* Soil organic carbon stocks (0–30 cm) from ISRIC SoilGrids 2.0 (:ref:`soilgrids-soc`), scaled to 1 m depth using IPCC Tier 1 factors
* Natural forest regrowth rates from Cook-Patton & Griscom (2020) (:ref:`cook-patton-regrowth`), representing the carbon that would accumulate if previously cleared land were reforested
* IPCC Tier 1 below-ground biomass ratios, soil depletion factors, and agricultural equilibrium assumptions stored in ``data/luc_zone_parameters.csv``

These layers are reprojected, resampled, and combined by dedicated Snakemake rules to produce per-cell biomass/SOC stocks, forest masks, and regrowth rates ready for downstream processing. Figure :ref:`fig-luc-inputs` summarises the harmonised rasters on the common model grid.

.. _fig-luc-inputs:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/environment_luc_inputs.png
   :alt: Global maps showing forest fraction, biomass, soil carbon, and regrowth inputs
   :align: center
   :width: 95%

   Land-use change input layers harmonised to the modelling grid: forest fraction (Copernicus CCI), above-ground biomass (ESA Biomass CCI v6.0), soil organic carbon 0–30 cm (SoilGrids 2.0), and natural forest regrowth potential (Cook-Patton & Griscom, 2020).

Model integration and land states
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The land-use change workflow:

1. ``prepare_luc_inputs.py`` aligns the raw rasters to the resource-class grid and stores intermediate masks and carbon pools under ``processing/{config}/luc/``.
2. ``build_luc_carbon_coefficients.py`` derives pulse emissions, annual LEFs, and aggregates them to ``luc_carbon_coefficients.csv``.
3. ``build_current_cropland_area.py`` captures irrigated and rainfed cropland already in use as ``cropland_baseline_by_class.csv``.

During model construction, ``build_model.py`` loads these inputs, converts LEFs to marginal CO₂ flows (MtCO₂ per Mha-year), and applies them by land state:

* Baseline cropland enters via fixed ``land_existing_cropland_*`` generators. It does **not** pay conversion costs but can be **spared** via ``spare_land_*`` links that earn regrowth credits.
* Expansion cropland lives on ``land_new_*`` buses up to the suitability cap; ``convert_new_land_*`` links move this expansion into ``land:cropland:*`` (applying cropland LEFs), and ``convert_new_to_pasture_*`` links move it into ``land:pasture:*`` (applying pasture LEFs).
* Existing grazing-only land enters via ``land_existing_pasture_*`` generators and flows to the pasture pool via ``marginal_to_pasture`` links, or can be spared via ``spare_marginal`` links.

All LUC flows connect to the global ``co2`` bus, which feeds a priced CO₂ store (``emissions.ghg_price``). This keeps cropland expansion, pasture expansion, and regrowth credits on the same carbon price scale while avoiding double-charging existing land. The spatial pattern of the resulting LEFs is shown in :ref:`fig-luc-lef`.

Cropland baseline data source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model can derive baseline cropland area from two sources, controlled by ``luc.cropland_source``:

* **"gaez"** (default): Uses GAEZ RES06-HAR (2010-2019 average harvested area) summed across all crop modules. This ensures consistency with production stability constraints that also use GAEZ data. When multiple model crops map to the same RES06 module (e.g., oat, rye, and buckwheat all map to OCE), the module's harvested area is counted only once to avoid double-counting.

* **"esa"**: Uses ESA CCI land cover satellite data to identify pixels classified as cropland. This approach may show different spatial patterns than GAEZ, particularly in areas with multi-cropping or mixed land use.

Multi-cropping handling
^^^^^^^^^^^^^^^^^^^^^^^

GAEZ RES06-HAR stores *harvested* area, which can exceed physical land area in regions with double or triple cropping. For example, a field that produces two rice crops per year would have harvested area equal to twice its physical area. To convert harvested area to physical cropland extent:

1. Sum harvested area across all unique RES06 modules for each water supply (irrigated/rainfed)
2. Where total harvested area exceeds gridcell physical area, scale proportionally so that irrigated + rainfed = cell area

This approach preserves the irrigated/rainfed split while ensuring baseline cropland doesn't exceed physical limits.

Irrigated vs. rainfed split
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Both cropland sources use the GAEZ "land equipped for irrigation" share raster to split total cropland into irrigated and rainfed fractions. This ensures consistent water supply attribution regardless of the underlying cropland extent source.

.. _luc-spared-land-filtering:

Spared land filtering
~~~~~~~~~~~~~~~~~~~~~

Regrowth sequestration rates from Cook-Patton et al. (2020) represent **young regenerating forest** (0-30 years) on previously cleared or degraded land. They do not apply to mature forests, which have already accumulated most of their carbon stock and exhibit near-zero net sequestration.

To avoid incorrectly crediting sequestration on mature forest, the LEF calculation for spared land includes a conditional that sets the sequestration benefit to zero where **current above-ground biomass (AGB)** exceeds a configurable threshold (``luc.spared_land_agb_threshold_tc_per_ha``, default 20 tC/ha). Specifically:

Regrowth credits are granted only when **both** of the following hold:

.. math::

   \mathrm{LEF}_{\mathrm{spared}} = \begin{cases}
   -R & \text{if } \mathrm{forest\_mask} = 1 \text{ and } \mathrm{AGB} \leq \text{threshold} \\
   0 & \text{otherwise}
   \end{cases}

This ensures:

* Low-biomass areas (recently cleared or degraded land suitable for agriculture) receive negative LEFs (sequestration credits) if left unused
* Cells flagged as potential forest by the land-cover dataset are eligible for credits provided their AGB is low enough, representing areas that could regrow quickly if spared
* High-biomass areas (mature tropical rainforest, boreal forest) receive zero spared-land LEF—their carbon value is already captured via high pulse emissions if converted, but they are not credited for additional regrowth

The threshold of 20 tC/ha is intermediate between typical agricultural land (0-10 tC/ha) and mature forest (50-200+ tC/ha). Areas above this threshold are assumed to represent established vegetation that would not exhibit the rapid early-successional regrowth rates quantified by Cook-Patton et al.

Only baseline cropland (existing managed area) and existing grazing-only land can be spared in the optimisation; newly converted land must first revert to the baseline pool before becoming eligible for regrowth credits.

Network links that implement this behaviour use the ``spare_*`` naming scheme: ``spare_land_*`` links pull from ``land:existing_cropland:*`` buses, and ``spare_marginal_*`` links pull from ``land:existing_pasture:*`` buses. Both produce to dedicated spared-land sinks with CO₂ outputs proportional to the spared LEF.

.. _fig-luc-lef:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/environment_luc_lef.png
   :alt: Global maps of cropland expansion and spared land emission factors
   :align: center
   :width: 95%

   Annualised land-use change emission factors (LEFs) used in the optimisation. Left: CO₂ released per hectare of cropland expansion. Right: CO₂ sequestered per hectare of existing cropland spared and allowed to regenerate.

Limitations and assumptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The current implementation makes several simplifying assumptions that should be considered when interpreting results:

* **Climatic zones**: Zones (tropical, temperate, boreal) are assigned by latitude only (tropical: :math:`\lvert \phi \rvert < 23.5^\circ`, boreal: :math:`\lvert \phi \rvert \ge 50^\circ`, temperate: otherwise). This does not account for altitude effects (e.g., highland tropics behave more like temperate zones) or local climate variations. A future enhancement would use actual biome or Köppen-Geiger climate classifications.

* **Agricultural biomass stocks**: Cropland and pasture equilibrium above-ground biomass is assumed to be negligible (0 tC/ha) for annual crops. This is a conservative assumption appropriate for grain crops where biomass is harvested annually, but underestimates carbon storage in perennial crops (orchards, oil palm, coffee) and improved pastures. See ``data/luc_zone_parameters.csv`` for the zone-specific parameters.

* **Forest mask threshold**: Regrowth sequestration is only applied to cells with ≥20% forest fraction in the land-cover-derived potential forest layer (i.e., areas that would naturally support forest if unmanaged). This threshold can be adjusted via ``config['luc']['forest_fraction_threshold']`` (default: 0.2). Raising the threshold restricts eligibility to areas that are strongly classified as forest; lowering it allows credits on lightly wooded mosaics.

* **Soil organic carbon depth**: SOC stocks in the 0-30 cm layer (from SoilGrids) are scaled to 1 m depth using zone-specific factors from ``data/luc_zone_parameters.csv``. **TODO**: These factors require verification against IPCC 2006/2019 Guidelines Volume 4 Chapter 2 to ensure they match the intended Tier 1 methodology.

* **Managed flux**: Set to zero everywhere (:math:`M_{i,u} = 0`), meaning ongoing emissions from agricultural management (e.g., peat oxidation, tillage-induced decomposition) are not currently modeled. Future work could incorporate organic soil maps and management-specific emission factors.
