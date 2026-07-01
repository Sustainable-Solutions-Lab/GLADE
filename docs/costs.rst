.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Production Costs
================

Overview
--------

The model incorporates production costs for both crop and livestock systems to represent the economic considerations of agricultural production. Costs are applied as marginal costs on production links in the PyPSA network, ensuring that the optimization accounts for both physical and economic constraints.

This page provides an overview of how production costs are sourced, processed, and applied throughout the model.

Cost Categories
---------------

The model distinguishes between three main categories of production costs:

**Crop Production Costs**
   Costs associated with growing crops, including labor, machinery, energy, and other inputs (excluding fertilizer, which is modeled endogenously).

**Livestock Production Costs**
   Costs associated with raising animals for meat, milk, and eggs, including labor, veterinary services, housing, and energy (excluding feed and land, which are modeled endogenously).

**Grazing Costs**
   Costs specifically associated with pasture-based livestock production, representing the management and maintenance of grassland feed systems.

**Land Conversion Costs**
   Investment costs for expanding agriculture onto new land, covering physical clearing and soil preparation. Differentiated by forest vs. non-forest cover type, annualized using a capital recovery factor over a configurable investment horizon. Applied as marginal costs on ``land_conversion`` and ``new_to_pasture`` links. See :doc:`land_use` for details.

**Marketing Costs (Farm-to-Wholesale)**
   The markup between the farm gate and the wholesale (inter-regional trade) market. Covers drying, on-farm and commercial storage, first-mile transport, elevator / aggregator handling, slaughter and packing for meat, processing for foods and feeds, insurance, and the trader margin. Applied as a per-tonne markup on the relevant production link (``crop_production``, ``food_processing``, ``feed_conversion``, ``animal_production``). See :ref:`marketing-costs` below for the per-class table.

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

Crop Costs: FAOSTAT Producer Prices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Crop production costs are derived from FAOSTAT data, using producer prices as a revenue proxy scaled by a configurable cost share.

**Coverage**:
  * Spatial: Per-(crop, country) for all modeled countries
  * Temporal: Configurable averaging period (default 2015-2022)
  * Crops: All crops with FAOSTAT price and yield data; unmapped crops use proxy mappings from ``data/curated/faostat_cost_proxies.yaml``

**Data characteristics**:
  * Prices from the FAOSTAT Prices (PP) domain (element 5532: Producer Price USD/tonne)
  * Yields from the FAOSTAT Production (QCL) domain (element 5412: Yield kg/ha)
  * CPI-deflated to the configured base year before averaging

**Source**:
  * `FAOSTAT Prices <https://www.fao.org/faostat/en/#data/PP>`_
  * `FAOSTAT Production <https://www.fao.org/faostat/en/#data/QCL>`_

**Workflow script**: ``workflow/scripts/prepare_faostat_crop_costs.py``

Livestock Costs: USDA and FADN
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Livestock production costs are sourced from two agricultural accounting systems.

**USDA Economic Research Service (United States)**:
  * Coverage: Dairy (milk), beef cattle (cow-calf), hogs (pork)
  * Time period: 2015-2024 (averaged across years)
  * Units: USD per acre or USD per head
  * Workflow script: ``workflow/scripts/retrieve_usda_animal_costs.py``

**FADN - Farm Accountancy Data Network (European Union)**:
  * Coverage: All major animal production systems
  * Time period: 2004-2020 (averaged across years)
  * Units: EUR per farm (allocated to livestock categories)
  * Workflow script: ``workflow/scripts/retrieve_fadn_animal_costs.py``

Cost Processing Methodology
----------------------------

The cost data undergoes several processing steps to ensure consistency and accuracy across different sources and production systems.

Crop Costs
~~~~~~~~~~

Crop production costs are derived from FAOSTAT producer prices and yields, providing per-(crop, country) cost estimates.

**Script**: ``workflow/scripts/prepare_faostat_crop_costs.py``

The cost model uses revenue per hectare as a proxy for total production cost, scaled by a configurable share:

.. math::

   C_{\mathrm{ha}} = P \times Y \times f_{\mathrm{non\text{-}endog}}

where :math:`P` is the producer price (USD/t), :math:`Y` is yield (t/ha), and :math:`f_{\mathrm{non\text{-}endog}}` is the non-endogenous cost share (default 0.7).

Processing steps:

1. **Load FAOSTAT bulk data**: Producer prices from the PP domain (element 5532, USD/tonne) and yields from the QCL domain (element 5412, kg/ha, converted to t/ha)
2. **Map crops to FAOSTAT items**: Using ``data/curated/faostat_crop_item_map.csv``; crops without a direct mapping use proxy crops defined in ``data/curated/faostat_cost_proxies.yaml`` (e.g., alfalfa uses soybean prices, biomass-sorghum uses sorghum)
3. **CPI deflation**: Prices are deflated to the configured base year using US CPI-U data
4. **Merge price and yield**: For each (crop, country, year), compute revenue per hectare = price × yield
5. **Temporal averaging**: Average revenue per hectare across the configured period (default 2015-2022)
6. **Apply cost share**: Multiply by ``non_endogenous_cost_share`` (default 0.7) to obtain the cost estimate
7. **Winsorize per-crop outliers**: For each crop, clip non-fallback
   per-tonne cost (``cost_per_ha / yield_per_ha``) above the configured
   ``outlier_cap_quantile`` (default p90 of the non-fallback distribution)
   to that quantile, then recompute per-hectare cost as
   ``capped_per_tonne * actual_yield``. Capping per-tonne rather than
   per-hectare preserves elevated implicit prices in high-yield
   greenhouse producers while bounding them at realistic wholesale
   levels. See :ref:`crop_cost_outlier_cap` below. Capped rows are
   flagged via the ``is_capped`` audit column. Set
   ``outlier_cap_quantile: null`` to disable the cap.
8. **Fill gaps**: Missing (crop, country) pairs receive the global median cost for that crop (computed from the post-cap distribution) as fallback.
9. **Output**: ``processing/{name}/faostat_crop_costs.csv`` with columns:

   * ``crop``: Crop name
   * ``country``: ISO3 country code
   * ``cost_usd_{base_year}_per_ha``: Production cost estimate (USD/ha)
   * ``n_years``: Number of years with valid price-yield data
   * ``is_fallback``: Whether the value is a global median fallback
   * ``is_capped``: Whether the value was winsorized in step 7

.. _crop_cost_outlier_cap:

Outlier Cap (Greenhouse / Cold-Climate Producers)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

FAOSTAT producer prices and yields are reported on a country basis and
make no distinction between field and protected (greenhouse) cultivation.
For a small group of high-value crops (tomato, carrot, mango, tea,
cabbage, apple, banana, watermelon, sweet-potato) in cold-climate or
high-income producers (ISL, NOR, DNK, FIN, CHE, IRL, SWE, DEU, GBR, NLD,
BEL, AUT, JPN, GUY, BRB, etc.), virtually all reported production comes
from greenhouses. The combined producer-price * yield product is
1-3 orders of magnitude above the field-cultivation norm: pre-cap
tomato cost reaches USD 1.4 million per hectare in Iceland, USD 980k in
Norway, USD 200k+ for carrot in Iceland, USD 150k for mango in Japan.

The model treats this country-aggregate value as field cost applied to
the model's notional cropland area, so these outliers feed directly
into ``crop_production`` link costs. They are not field cost in any
useful sense and were forcing the cost calibration to absorb the gap.

The winsorization step is applied to per-tonne cost
(``cost_per_ha / yield_per_ha``, equivalent up to the
``non_endogenous_cost_share`` factor to the FAOSTAT producer price):
for each crop, non-fallback country values above the configured
quantile (default 0.90) of the per-tonne distribution are clipped to
that quantile, and per-hectare cost is recomputed as
``capped_per_tonne * actual_yield_per_ha``. The cap is applied
**before** the global median is computed for missing (crop, country)
fallback rows, so the post-cap distribution drives both. Fallback
rows are themselves never marked as capped.

Capping per-tonne rather than per-hectare matters because the
underlying outlier pattern is *both* high producer prices *and* high
greenhouse yields. Capping per-hectare cost while leaving yields
untouched would collapse the implicit per-tonne cost to artificially
low values (e.g. tomato in Belgium would drop to ~$700/t against a
wholesale-realistic ~$2-3 k/t), which then feeds large positive
corrections in the cost calibration and inflates the
production-stability L1 penalty. Capping per-tonne preserves the
elevated implicit price but bounds it at realistic wholesale levels;
per-hectare cost scales with the actual yield.

The resulting cost estimates vary substantially across crops and countries, reflecting differences in local prices, yields, and agricultural productivity.  The map below shows the median cost per country (across all crops), while the distribution plot reveals the cross-country spread for each crop individually.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/costs_crop_cost_map.png
   :width: 100%
   :alt: World map of median crop production costs per country

   Median crop production cost across all crops per country (USD/ha, log scale).
   Higher costs in Europe and North America reflect higher input prices and labour costs;
   lower costs in Sub-Saharan Africa and South Asia reflect lower price levels.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/costs_crop_cost_distribution.png
   :width: 100%
   :alt: Distribution of crop production costs across countries for each crop

   Cross-country cost distributions per crop (USD/ha, log scale).
   Boxen (letter-value) plots show the full distributional shape; crops are grouped
   by category.  Vegetables and fruits have the highest and most variable costs,
   while cereals and legumes cluster at lower levels.

.. _cost-calibration-correction:

Calibration Correction
^^^^^^^^^^^^^^^^^^^^^^

An optional additive calibration correction adjusts production costs
for crops, grassland, and animals based on shadow prices from a
two-step paired solve:

* **Step 1** pins consumption to the baseline diet, enables hard
  production-stability bounds at +/-20 %, and applies the file-level
  ``validation.slack_marginal_cost: 5.0`` override (5 000 USD/t) to
  cap the duals of the small set of foods whose FAOSTAT-vs-FBS
  mismatch still exceeds the +/-20 % band (buckwheat, plantain,
  coffee, tea, olive-oil). The food-bus duals feed the piecewise
  consumer-utility blocks used by step 2.
* **Step 2** activates the step-1 piecewise utility and tightens
  production stability to +/-1 %. The duals on these tight constraints
  become the per-group additive cost corrections.

The corrections are additive, clipped to zero (no negative costs), and
applied at build time whenever ``cost_calibration.enabled`` is true
(the default).

Regenerate with ``tools/calibrate cost``. See :ref:`calibration` for
the full dependency graph and algorithm, including the upstream
``food_demand`` step that closes residual per-food gaps before the
cost solve.

.. _marketing-costs:

Marketing Costs (Farm-to-Wholesale)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Farm-gate production costs alone substantially understate the price a
commodity carries when it reaches an inter-regional wholesale market.
The intervening marketing margin -- drying, storage, first-mile
transport, elevator / aggregator handling, slaughter and packing for
meat, dairy and oil processing, insurance, and the trader margin --
typically accounts for 10-25 % of bulk-grain wholesale price,
15-40 % of fresh-produce wholesale price, and 8-15 % of the meat
wholesale value paid to slaughter and packing.

The model captures this as a single per-tonne ``marketing_cost_per_t``
parameter per commodity class, configured under the unified
``commodities:`` block (see :doc:`configuration`). The markup is
applied as a one-shot per-tonne cost on each commodity's *production*
link:

* On ``crop_production`` links: ``marketing_cost_per_t * yield``, added
  to the link's marginal cost (USD/ha basis).
* On ``food_processing`` links: ``sum over outputs of
  marketing_cost_per_t * efficiency``, summed across the pathway's
  output foods (multi-output Links).
* On ``feed_conversion`` links: ``marketing_cost_per_t * share`` for
  the destination feed category.
* On ``animal_production`` links: ``marketing_cost_per_t * efficiency``
  for the produced animal product.

The defaults below come from the public agricultural-marketing
literature -- principally USDA ERS and FAO. They are *order of
magnitude* anchors that the cost-calibration step adjusts locally.

.. _commodity-cost-classes:

.. list-table:: Default marketing-cost parameters (USD_2024 per tonne)
   :header-rows: 1
   :widths: 18 12 60 10

   * - Class
     - Default ``marketing_cost_per_t``
     - Composition and source
     - Coverage
   * - ``crops.bulk_dry_goods``
     - 30
     - Farm-to-grain-elevator markup for storable bulk commodities:
       drying (~$3-7/t), shrink (~1.4 % of grain value), commercial
       storage 3-6 months (~$7-13/t), elevator handling in+out
       (~$3-11/t), first-mile truck (~$3-6/t). Bottom end of the
       $9-65/t range reported by Texas A&M Transportation Institute
       (`TTI 2019`_) for US soybeans farm-to-Gulf, picking the
       no-delay base case.
     - cereals, oilseeds, pulses, stimulant crops, cotton fibre
   * - ``crops.bulky_fresh``
     - 60
     - Twice the bulk-dry-goods markup. Bulky low-value perishables
       (roots, tubers, sugar crops, biomass) carry a larger handling
       margin relative to wholesale price because their mass per
       value is high and shelf life is short. FAO (`FAO 1997`_,
       chapter 12) places handling in this group near 15-25 % of
       wholesale price.
     - roots and tubers (potato, cassava, yam, plantain), sugarbeet,
       sugarcane, oil-palm, fodder/biomass crops
   * - ``crops.perishable_high_value``
     - 200
     - Cooling, cold-chain transport, packing, sorting and the wider
       trader margin pull this class to the 25-40 % share of
       wholesale price reported by FAO (`FAO 1997`_) and the USDA
       Food Dollar Series (`USDA ERS Food Dollar`_) farm-share data
       for fresh produce.
     - fresh vegetables, fruits, olives
   * - ``foods.processed_dry_goods``
     - 80
     - Mill / dry-processing margin plus dry-goods wholesale: USDA
       ERS reports the wholesaling and retailing share of food-at-home
       spending in the 13-16 % range (`USDA ERS Food Dollar`_), which
       for a $400-600/t milled-grain or processed-pulse wholesale
       price puts the marketing layer around $80/t.
     - milled cereals, dehulled grains, food-grade pulses, whole
       seeds, sugar, dried stimulants
   * - ``foods.processed_oils``
     - 120
     - Oil extraction, refining and wholesale margin. Oil mill margins
       reported by AOCS / industry surveys (`USDA WASDE`_ oilseed
       chapter) are typically 5-10 % of refined-oil wholesale price
       (~$1100-1500/t), giving ~$60-150/t.
     - vegetable oils
   * - ``foods.fresh_produce``
     - 200
     - Same magnitude and same logic as ``crops.perishable_high_value``;
       most items in this class are simply the crop sold as food.
     - fresh fruits, vegetables, roots delivered as food
   * - ``foods.chilled_meat``
     - 800
     - USDA ERS Meat Price Spreads (`USDA ERS Meat Price Spreads`_)
       gives a 2023 farm-to-wholesale spread for Choice beef of
       36.8 cents per pound retail-equivalent (wholesale 452.9 minus
       gross farm 416.1), i.e. ~$810/t retail-equivalent. Pork and
       broiler farm-to-wholesale margins fall in the same magnitude
       once expressed per tonne retail equivalent.
     - meat-cattle, meat-pig, meat-chicken, meat-sheep
   * - ``foods.dairy_and_eggs``
     - 300
     - Dairy processing, packaging and cold-chain wholesale margin.
       USDA ERS reports a 2022 farm share of US dairy retail near
       28 % on a ~$5.3/kg basket, which on a per-tonne wholesale
       basis implies a farm-to-wholesale margin of $200-400/t
       (`USDA ERS Dairy`_).
     - dairy, dairy-buffalo, eggs
   * - ``foods.feed_byproduct``
     - 30
     - Bulk low-value co-products diverted to feed (brans, meals,
       hulls, gluten products, distillers grains, molasses) carry
       the same farm-to-wholesale handling cost as bulk grains.
     - milling and oil-extraction byproducts, sugar molasses
   * - ``foods.industrial_byproduct``
     - 40
     - Fibre, biofuel and industrial co-products. Cotton lint at the
       gin-to-warehouse stage adds roughly $30-50/t in handling
       (USDA AMS cotton classing fees); fuel ethanol and starch
       carry similar bulk-storage handling margins.
     - cotton-lint, ethanol products, maize-starch, rendered-fat
   * - ``feeds.grain_protein``
     - 30
     - Compounded feed mill margin (grinding, mixing, bagging) on
       grain and protein concentrates. Mirrors the bulk-dry-goods
       crop markup since these feeds are largely those crops in a
       different form.
     - ruminant_grain, ruminant_protein, monogastric_grain,
       monogastric_protein
   * - ``feeds.forage``
     - 25
     - Hay-and-forage handling: baling, on-farm or commercial
       barn storage, short-haul truck. Lower than grain marketing
       because most forage moves short distances on or near the
       farm of origin.
     - ruminant_forage
   * - ``feeds.bulky_low_quality``
     - 35
     - Bulk roughage and low-quality co-products with higher
       per-tonne handling cost than baled forage.
     - ruminant_roughage, monogastric_low_quality

The configuration is the single source of truth -- raising any value
here moves that marketing layer directly into the optimisation. Every
modelled commodity must appear in exactly one class; missing
assignments are rejected by ``workflow/validation/commodities.py``
before any solve.

.. _TTI 2019: https://static.tti.tamu.edu/tti.tamu.edu/documents/TTI-2019-11.pdf
.. _FAO 1997: https://www.fao.org/4/w3240e/W3240E12.htm
.. _USDA ERS Food Dollar: https://www.ers.usda.gov/data-products/food-dollar-series
.. _USDA ERS Meat Price Spreads: https://www.ers.usda.gov/data-products/meat-price-spreads
.. _USDA ERS Dairy: https://www.ers.usda.gov/data-products/price-spreads-from-farm-to-consumer
.. _USDA WASDE: https://www.usda.gov/oce/commodity/wasde

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

1. **Load Multiple Sources**: Combine USDA and FADN livestock cost estimates.
2. **Average Across Sources**: For products with multiple data sources, compute mean.
3. **Resolve Fallbacks**: For products without direct source data, walk the
   fallback chain configured under ``animal_costs`` in ``config/default.yaml``
   (see :ref:`animal_cost_fallbacks` below for the values and sources).

   a. ``fallback_aliases``: copy another product's per-tonne cost verbatim.
   b. ``fallback_values_usd_per_t``: literature-based defaults with separate
      non-grazing ``production`` and ``grazing`` components.
   c. Otherwise: zero cost, with a warning.

4. **Maintain Separate Grazing Costs**: Keep grazing cost column distinct from general production costs.
5. **Output**: ``processing/{name}/animal_costs.csv`` with columns:

   * ``product``: Animal product name
   * ``n_sources``: Number of source datasets averaged (0 when filled by a fallback)
   * ``source``: How the row was resolved -- ``data``, ``alias:<proxy>``, ``literature``, or ``zero``
   * ``cost_per_t_usd_{base_year}``: Non-grazing production cost (USD/tonne product)
   * ``grazing_cost_per_t_usd_{base_year}``: Grazing-feed cost (USD/tonne product)

.. _animal_cost_fallbacks:

Fallbacks for Products Without Source Data
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

USDA Commodity Costs and Returns covers cattle, hogs, dairy, and broiler
breakouts (live weight), but does not publish ongoing cost-of-production
series for sheep or buffalo dairy, and the broiler series alone is
US-centric. FADN covers cattle, dairy, and sheep in the EU but is biased
toward high-cost systems. To avoid leaving large global products with a
zero per-tonne base cost (forcing the calibration to manufacture the
entire cost signal), the merge step applies the following defaults.

**Buffalo dairy -- alias to cow dairy**

``dairy-buffalo`` is resolved by copying the cow ``dairy`` per-tonne
cost verbatim. Buffalo milk's per-tonne production-cost structure is
dominated by feed and labour at scales similar to cow dairy. ICAR-NDRI
cost-of-production studies (India) report buffalo milk at roughly
INR 25-30/L (about USD 300-360/t) versus cow milk at INR 27-32/L, so
cow dairy is a defensible (slightly conservative) upper-bound proxy
until product-specific source data are added.

References:

* ICAR-National Dairy Research Institute, *Cost of milk production studies*, https://ndri.res.in/
* Birthal, P.S. et al. (2017), *Buffalo Production in India: Performance, Trends and Drivers*, NIAP Policy Paper.

**Broiler chicken -- USD 1 300/t carcass (non-grazing only)**

USDA ERS *Commodity Costs and Returns: Broilers* place broiler
operating + ownership cost at roughly USD 0.55-0.65 per pound live
weight (about USD 1 200-1 430/t live, USD 1 700-2 000/t carcass at
~70 % dressing) over 2018-2023. Brazil and China together account for
more than 40 % of global broiler meat output and produce at lower
cost; the OECD-FAO *Agricultural Outlook 2024-2033* implied production
cost is around USD 1 100-1 500/t carcass. A production-weighted global
anchor of **USD 1 300/t carcass** is used here. Chickens do not graze
in the systems represented in the model, so the grazing component is
zero.

References:

* USDA Economic Research Service, *Commodity Costs and Returns -- Broilers*, https://www.ers.usda.gov/data-products/commodity-costs-and-returns/
* OECD-FAO (2024), *Agricultural Outlook 2024-2033*, Chapter 6 -- Meat, https://www.oecd.org/en/publications/oecd-fao-agricultural-outlook-2024-2033_4c5d2cfb-en.html

**Sheepmeat (lamb) -- USD 3 500/t carcass total**

NZ Beef + Lamb Economic Service *Sheep and Beef On-farm Inventory*
weighted-average cost of production runs about NZD 5-6/kg carcass
(USD 3 000-3 700/t). MLA *Cost of Production -- Lamb* (Australia) is
about AUD 4-5/kg live weight (USD 2 700-3 500/t carcass). UK DEFRA
*Farm Business Survey* lamb is higher (USD 5 500-6 800/t carcass) but
the UK is a much smaller share of global production. NZ, AU, CHN and
IND dominate global sheepmeat output, so a production-weighted
anchor of **USD 3 500/t carcass total** is used here. Extensive
pasture systems are the global norm; the AGRI Benchmark Beef and
Sheep Network reports roughly 60-75 % of variable cost as
pasture/forage on sheep farms, so the total is split as
**USD 1 200/t non-grazing operating cost + USD 2 300/t grazed-forage
cost** (~65 % grazing share).

References:

* Beef + Lamb New Zealand Economic Service, *Sheep and Beef On-farm Inventory*, https://beeflambnz.com/data-tools
* Meat & Livestock Australia, *Cost of Production -- Lamb*, https://www.mla.com.au/prices-markets/
* UK DEFRA, *Farm Business Survey -- Lamb enterprise*, https://www.gov.uk/government/collections/farm-business-survey
* AGRI Benchmark (2022), *Beef and Sheep Report*, https://www.agribenchmark.org/beef-and-sheep.html

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
* Stored in separate ``grazing_cost_per_t_usd_{base_year}`` column
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

**Implementation**: ``workflow/scripts/build_model/crops.py``

Crop costs are applied to production links that convert land into crop output:

**Link structure**:
  * **Input (bus0)**: Land pool (Mha) by region, resource class, water supply
  * **Output (bus1)**: Crop commodity bus (Mt) by country
  * **Efficiency**: Crop yield (Mt/Mha)

**Cost calculation**:

For single-season crops:

.. code-block:: python

   # Look up per-(crop, country) cost from FAOSTAT-derived data
   cost_per_ha = crop_costs.get((crop, country), global_median_cost.get(crop, 0.0))

   # Convert USD/ha to bnUSD/Mha (PyPSA units)
   marginal_cost = cost_per_ha * 1e6 * USD_TO_BNUSD

   # Optional: add calibration correction (bnUSD/Mha, additive)
   marginal_cost += cost_calibration.get((crop, country), 0.0)

For multi-cropping systems (multiple crops per year on the same land):

.. code-block:: python

   # Sum per-(crop, country) costs across all crops in the combination
   total_cost = sum(crop_costs.get((c, country), median) for c in crops_in_cycle)

   # Convert to bnUSD/Mha
   marginal_cost = total_cost * 1e6 * USD_TO_BNUSD

**Interpretation**:
  * The marginal cost represents the economic cost of using one Mha of land for crop production
  * Costs vary by country, reflecting local price and yield conditions
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

   # Cost from animal_costs.csv (USD per tonne of product)
   cost_per_t_product = animal_costs.loc[product]

   # Efficiency (t product per t feed DM = Mt product per Mt feed DM)
   efficiency = feed_requirements.loc[product, feed_category, 'efficiency']

   # bus0 dispatch is Mt feed: scale tonne -> Mt and weight by product output
   marginal_cost = (
       cost_per_t_product
       * efficiency
       * MEGATONNE_TO_TONNE
       * USD_TO_BNUSD
   )  # bnUSD per Mt feed

**Interpretation**:
  * The marginal cost penalises feed dispatch in proportion to the resulting
    product output, so more productive feed allocations incur larger costs
    in absolute terms.
  * Higher feed-conversion efficiency raises the per-Mt-feed cost coefficient
    but produces proportionally more product, so the per-tonne-product cost
    is unchanged.

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

**Crop costs**:

.. code-block:: yaml

   crop_costs:
     non_endogenous_cost_share: 0.7  # Fraction of revenue attributed to non-endogenous costs
     outlier_cap_quantile: 0.90      # Crop-specific upper winsorization; null disables
     faostat:
       price_element_code: 5532      # Producer Price (USD/tonne)
       yield_element_code: 5412      # Yield (kg/ha)
   cost_calibration:
     enabled: false       # Apply calibration corrections
     generate: false      # Generate calibration from solved model
     scenario: "calibration"
     crop_correction_csv: "data/curated/calibration/{calibration_source}/crop_cost.csv"
     grassland_correction_csv: "data/curated/calibration/{calibration_source}/grassland_cost.csv"
     animal_correction_csv: "data/curated/calibration/{calibration_source}/animal_cost.csv"

**Animal cost fallbacks** (see :ref:`animal_cost_fallbacks`):

.. code-block:: yaml

   animal_costs:
     fallback_aliases:
       dairy-buffalo: dairy
     fallback_values_usd_per_t:
       meat-chicken:
         production: 1300
         grazing: 0
       meat-sheep:
         production: 1200
         grazing: 2300

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
