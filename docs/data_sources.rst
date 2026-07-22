.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Data Sources
============

Overview
--------

The model integrates multiple global datasets covering agricultural production, climate, population, health, and water resources. This page documents all external datasets used by the project, their licenses, and how to obtain them.

.. _manual-download-checklist:

Manual Download Checklist
~~~~~~~~~~~~~~~~~~~~~~~~~

Several licensed datasets cannot be fetched automatically. While their use is free for non-commercial research purposes, these have to be downloaded manually or require API key registration.

**Required only for the health module / GBD diet anchoring:**

The following IHME GBD datasets are needed **only** when the health
module is enabled (``health.enabled: true``) or when the baseline diet
anchors to GBD (``diet.anchor_groups_to_gbd``; see
:ref:`current-diets-gbd-anchoring`). With both off -- the default -- they
are not required and the workflow runs without them. If they are missing
while needed, the workflow stops at startup with an explicit message.

1. Create an account with IHME and download GBD death rates as described in :ref:`ihme-gbd-mortality`.
2. Download the IHME 2023 dietary risk exposure estimates (two archives, ``IHME_GBD_2023_RISK_EXPOSURE_DIET_1`` and ``_2``) (:ref:`ihme-diet-risk-exposure`).

**Optional (only to regenerate curated health inputs):**

3. The IHME 2019 relative risk workbook ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX`` (:ref:`ihme-relative-risks`) is only used by a standalone curation script to regenerate the git-tracked RR age-attenuation table; it is not consumed by the normal workflow.

The baseline diet needs no manual downloads: the default GDD-IA source
(:ref:`gdd-ia-dietary-intake`) is fetched automatically from Zenodo.

The one build-time credential is a free USDA FoodData Central key, used only to refresh the nutritional data (see :doc:`introduction`). Everything else is fetched from public downloads, Zenodo (the GDD-IA dataset, :ref:`gdd-ia-dietary-intake`, and our land-cover mirror, :ref:`copernicus-land-cover`), or bundled data.


.. _weight-bases:

Weight bases for animal products
--------------------------------

Animal-product mass appears in several different "weight bases" along
the supply chain. Mixing these silently is a common source of bugs, so
the project tracks them explicitly in column names and in loader/writer
docstrings. For animal foods specifically:

.. list-table::
   :header-rows: 1
   :widths: 22 18 60

   * - Basis
     - Notation
     - Definition
   * - Live weight
     - LW
     - The animal alive on the farm. Not used directly in the model.
   * - Carcass weight equivalent
     - CWE
     - The slaughtered carcass with bones, blood drained. FAOSTAT FBS
       reports meat in CWE; FAOSTAT QCL "Meat of … with the bone, fresh
       or chilled" is also CWE.
   * - Fresh retail
     - retail
     - Boneless, trimmed cuts as sold to the consumer; equals
       ``CWE × carcass_to_retail`` (OECD-FAO 2023 Box 6.1: 67 % cattle,
       73 % pig, 60 % chicken, 66 % sheep). Dairy and eggs use
       ``carcass_to_retail = 1`` (no conversion).
   * - Intake
     - intake
     - What people actually consume; equals
       ``retail × (1 − loss_fraction) × (1 − waste_fraction)``. This is
       what dietary intake surveys measure (GDD, GBD, NHANES) and the
       basis the model's food bus delivers after the ``animal_production``
       link applies its FLW multiplier.

CSV columns and config keys carry an explicit suffix where the basis
matters:

.. list-table::
   :header-rows: 1
   :widths: 45 25 30

   * - Column / file
     - Basis
     - Source
   * - ``faostat_animal_production.csv:production_mt_fresh_retail``
     - retail
     - QCL element 5510 × ``weight_conversion.carcass_to_fresh`` (raw
       fresh weight for milk and eggs).
   * - ``baseline_diet.csv:consumption_g_per_day_intake``
     - intake
     - GDD/GBD/FAOSTAT/NHANES intake surveys, or for FBS-overridden
       foods, ``FBS_supply × within_FBS_share × basis_factor
       × (1 − loss) × (1 − waste)``, where ``basis_factor`` resolves
       to ``weight_conversion.carcass_to_fresh`` for meats and
       ``weight_conversion.fresh_to_dry`` for tea-dried.
   * - ``faostat_fbs_items.csv:supply_kg_per_capita_year``
     - CWE for meat;
       fresh for milk/eggs/crops
     - FAOSTAT FBS element 645 (`Food supply quantity, kg/capita/yr`).
       Mixed-unit file: meat items are CWE per FAOSTAT convention;
       crops are fresh weight. Convert to retail via the shared
       ``weight_conversion.carcass_to_fresh`` table for meat items.
   * - ``feed_baseline.csv:feed_use_mt_dm``
     - dry matter
     - GLEAM 3.0 (already explicit).

Source selection for animal products
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For animal products the project uses **FAOSTAT throughout** — both
production (QCL) and per-country food supply (FBS) — because:

* QCL slaughter-volume data is more reliable than self-reported intake
  for socially significant foods like red meat, and FBS aggregates the
  same QCL primary commodities into a closed
  ``production + trade − non-food − loss`` balance per country.
* Anchoring the consumption side to FBS supply with the same FLW
  factors used on the production side keeps diet and supply on the same
  intake basis at baseline, so the food-balance constraint closes
  without artificial slack.
* Trade falls out for free: FBS supply per country already nets in
  imports and exports, so per-country diet matches what FAOSTAT reports
  each country actually consumed. The model's trade hubs reproduce the
  observed flows at solve time.

The exception is **dairy**, which uses GDD-disaggregated values rather
than an FBS override. Dairy's ``food_loss_waste`` convention lumps
non-food uses of raw milk (calf feed, processing, industrial) into a
single 30 % factor, because the model has no separate non-food milk
outlet. Under that convention the GDD-based dairy total happens to
mass-balance against the production-side ``QCL × 0.7`` delivered to the
food bus; switching to FBS would break that balance. See
:ref:`Why animal products use FBS, not GDD <animal-source-selection>`
for the full rationale.


Agricultural Production Data
-----------------------------

GAEZ (Global Agro-Ecological Zones) v5
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO/IIASA

**Description**: Global crop suitability and attainable yield estimates under various climate and management scenarios.

**Version**: GAEZ v5; raster datasets (variables ``yc``, ``sx1``) under selected climate and management scenarios

**Coverage**:
  * Spatial: Global, 0.083333° x 0.083333° (~5 arc-minute grid, ~9 km at the equator)
  * Temporal: Multiple climate scenarios

**Access**: https://data.apps.fao.org/gaez/ -- bulk downloads through a Google Cloud Storage interface

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0) + FAO database terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO/IIASA (2025). Global Agro-Ecological Zones v5 (GAEZ v5).

**Retrieval**: Automatic via Snakemake rules in ``workflow/rules/retrieve.smk``

**Usage**: Crop yield and suitability rasters feeding into production potential calculations. Crops listed in ``config["cropgrids_crops"]`` bypass GAEZ entirely and are sourced from CROPGRIDS + FAOSTAT instead (see next section).

CROPGRIDS v1.08
~~~~~~~~~~~~~~~

**Provider**: Tang, Nguyen, Conchedda, Casse, Tubiello & Maggi (2024)

**Description**: Global geo-referenced harvested and physical crop area maps for 173 crops around 2020 at 0.05° (~5.6 km) resolution; compiled from Monfreda et al. (2008) and 28 newer gridded sources, aligned to FAOSTAT statistics.

**Version**: v1.08 (Figshare release v9). The model uses only the per-crop NetCDF ``harvarea`` band.

**Coverage**:
  * Spatial: Global, 0.05° × 0.05°
  * Temporal: Reference around 2020

**Access**: https://figshare.com/articles/dataset/CROPGRIDS/22491997 (full dataset; DOI https://doi.org/10.6084/m9.figshare.22491997)

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Tang, F. H. M., Nguyen, T. H., Conchedda, G., Casse, L., Tubiello, F. N., & Maggi, F. (2024). *CROPGRIDS: a global geo-referenced dataset of 173 crops*. Scientific Data 11, 413. https://doi.org/10.1038/s41597-024-03247-7

**Retrieval**: Bulk zip download via ``download_cropgrids_nc_maps`` (uses ``download_figshare_file.py``); per-crop NetCDFs extracted on demand by ``extract_cropgrids_nc``.

**Usage**: Fallback source of harvested area and current cropland footprint for crops listed in ``config["cropgrids_crops"]`` (e.g. ``apple``), which are not covered by GAEZ. The CROPGRIDS ``harvarea`` raster drives both ``baseline_area_mha`` and ``suitable_area`` for these crops (see ``build_crop_yields_cropgrids.py``); per-country FAOSTAT QCL yields (item 515 for apple, element 5419 hg/ha) supply the dry-matter yield, broadcast uniformly to every (region, resource_class) cell within each country.

MIRCA-OS v2
~~~~~~~~~~~

**Provider**: Kebede, Nagpal, Krueger, Grafton, Siebert & others (2025)

**Description**: Global gridded monthly irrigated and rainfed cropped-area dataset (an open-source update of MIRCA2000), 5-arcmin, for 23 crop classes and the years 2000-2020. The model selects the release nearest ``baseline_year`` from 2010, 2015, and 2020 (ties select the earlier year). Its annual harvested-area grids count every harvested cycle, while the maximum-monthly-cropped-area grids provide the AEI-capped physical field footprint. The ``Rice1/2/3`` subcrop grids identify repeated same-crop cycles.

**Version**: v2 (adds 2020 relative to the v0.1 preprint release).

**Coverage**:
  * Spatial: Global, 5-arcmin (~10 km)
  * Temporal: 2000, 2005, 2010, 2015, 2020 (the workflow currently retrieves 2010, 2015, and 2020)

**Access**: HydroShare resource ``e4582ca0042148338bb5e0148b749ed6`` (https://www.hydroshare.org/resource/e4582ca0042148338bb5e0148b749ed6/).

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Kebede, T. A., et al. (2025). *A global open-source dataset of monthly irrigated and rainfed cropped areas (MIRCA-OS) for the 21st century*. Scientific Data 12, 208. https://doi.org/10.1038/s41597-025-04313-4

**Retrieval**: The per-file HydroShare endpoint 500s for this resource, so the grid archives are fetched as members of the whole-resource BagIt zip via partial HTTP-range extraction (``download_mirca_os_bag_member.py``); the nested RAR5 archives are unpacked in one batch per product and year with ``bsdtar`` (``extract_mirca_os_*`` rules). The v2 archive ships only ``ir`` and ``rf`` footprint layers despite its README mentioning a ``tot`` layer. The derivation uses each layer with the matching water supply.

**Usage**: Source of the observed multiple-cropping baseline (see :doc:`crop_production`), derived by the config-specific ``derive_mirca_multicropping`` rule. The MIRCA-OS-to-GLADE crop concordance is in ``data/curated/mirca_os_crop_mapping.csv``; the fixed candidate sequence catalog is in ``data/curated/mirca_os_multicropping_combinations.yaml``.

FAOSTAT Prices (PP)
~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Producer prices in USD per tonne for agricultural commodities, used together with FAOSTAT Production (QCL) yields to derive per-(crop, country) production cost estimates.

**Coverage**:
  * Spatial: 245+ countries and territories
  * Temporal: 1991 onward
  * Element 5532: Producer Price (USD/tonne)

**Access**: https://www.fao.org/faostat/en/#data/PP (bulk download)

**License**: CC BY 4.0 + FAO database terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO. FAOSTAT Producer Prices. https://www.fao.org/faostat/en/

**Retrieval**: Bulk zip download via ``download_faostat_pp`` rule, converted to Parquet by ``extract_faostat_pp``. Output: ``data/downloads/faostat/PP.parquet``.

**Usage**: Combined with QCL yields and a configurable ``non_endogenous_cost_share`` to produce per-(crop, country) crop production costs in ``prepare_faostat_crop_costs``. Prices are CPI-deflated to the configured base year before averaging. Crops without FAOSTAT price data use proxy mappings from ``data/curated/faostat_cost_proxies.yaml``. See :doc:`costs` for full methodology.

USDA Livestock Cost Data
~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United States Department of Agriculture Economic Research Service (USDA ERS)

**Description**: Production cost estimates for major livestock products in the United States, including operating costs and allocated overhead.

**Version**: Excel files; temporal coverage varies by product

**Coverage**:
  * Spatial: U.S. total (national averages)
  * Temporal: Milk 2005-present, cow-calf 2008-present, hogs 2010-present
  * Products: 3 major animal products (dairy/milk, beef cattle via cow-calf, pork via hogs)

**Access**:
  * Milk: https://www.ers.usda.gov/data-products/milk-cost-of-production-estimates/
  * Commodity Costs and Returns (includes cow-calf, hogs): https://www.ers.usda.gov/data-products/commodity-costs-and-returns/

**License**: Creative Commons Attribution (CC BY)

**Citation**: U.S. Department of Agriculture, Economic Research Service. Milk Cost of Production Estimates / Commodity Costs and Returns (Livestock).

**Retrieval**: Excel download and processing via ``workflow/scripts/retrieve_usda_animal_costs.py``. The script implements robust retries (5 attempts with backoff) to handle intermittent server timeouts. URLs are listed in ``data/curated/usda_animal_cost_sources.csv``.
  * **Manual Fallback**: If automated retrieval fails, users can manually download the CSVs from the URLs listed in ``data/curated/usda_animal_cost_sources.csv`` and place them in the processing directory, or simply re-run the workflow as the server often recovers.

**Usage**: Production cost estimates per unit of output. Costs are converted from per-head-per-year or per-cwt to per-tonne-product for model integration.

Costs explicitly **excluded** (modeled endogenously):

* Feed costs (crops and grassland modeled separately)
* Land costs and grazing rent (land allocation is optimized)

**Included costs**: Labor, veterinary services, energy, housing, equipment depreciation, interest on operating capital

**Inflation adjustment**: Costs are inflation-adjusted to the configurable base year (default: 2024) using US CPI-U data from BLS.

**Note**: USDA animal cost data is merged with EU FADN livestock data via the ``merge_animal_costs`` rule. Products without direct source data (currently ``meat-chicken``, ``meat-sheep``, ``dairy-buffalo``) are resolved through an alias-then-literature fallback chain configured under ``animal_costs`` in ``config/default.yaml``; see :ref:`animal_cost_fallbacks` for the values and references. When data is available from multiple sources, costs are averaged.

.. _bls-cpi-data:

BLS Consumer Price Index (CPI-U)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: U.S. Bureau of Labor Statistics (BLS) |
**Access**: https://api.bls.gov/publicAPI/v2/timeseries/data/ |
**License**: Public domain

Consumer Price Index for All Urban Consumers (CPI-U), U.S. city average, all items (series CUUR0000SA0). Used for inflation adjustment of cost data throughout the workflow. Annual CPI averages are computed from monthly values and stored in ``processing/shared/cpi_annual.csv`` for reuse across the workflow. Retrieved automatically via BLS Public Data API (``workflow/scripts/retrieve_cpi_data.py``). Base year is configured via ``currency_base_year`` in ``config/default.yaml`` (default: 2024).

FAOSTAT Land Use (RL)
~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Country-level land use statistics, including permanent meadows and pastures area. Used to cap satellite-derived grassland area to match FAOSTAT ground-truth pasture extent.

**Coverage**:
  * Spatial: 245+ countries and territories
  * Temporal: 1961 onward

**Key variables**:
  * Item Code 6655: "Land under permanent meadows and pastures"
  * Element Code 5110: "Area" (in 1000 ha)

**Access**: https://www.fao.org/faostat/en/ (Land Use domain)

**License**: CC BY 4.0 + FAO database terms

**Retrieval**: Downloaded as bulk CSV (``Inputs_LandUse_E_All_Data_(Normalized).zip``), converted to Parquet, and processed by ``workflow/scripts/prepare_faostat_pasture_area.py``.

**Usage**: Provides per-country permanent pasture area used to scale down satellite grassland area in ``build_model.py``, replacing the previous forage overlap subtraction approach.

FAOSTAT Food Balance Sheets (FBS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Per-capita food supply quantities (kg/capita/year) by country, item, and year from FAO's global statistical database covering 245+ countries from 1961 onward. The model uses FBS item-level supply to disaggregate food-group totals into per-food consumption shares, to anchor the FBS-override foods in the baseline diet (meats, eggs, yam, coffee, cocoa), and to feed the food-loss-waste accounting.

**Version**: Retrieved via the FAOSTAT API using the ``faostat`` Python client (JSON -> Pandas DataFrame)

**Coverage**:
  * Spatial: 245+ countries and territories
  * Temporal: 1961 onward

**Access**: https://www.fao.org/faostat/en/ (Food Balance Sheets domain)

**License**: CC BY 4.0 + FAO database terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO. FAOSTAT Food Balance Sheets. https://www.fao.org/faostat/en/

**Retrieval**: Downloaded as a bulk CSV from FAOSTAT (converted to Parquet) and processed by scripts in ``workflow/scripts/`` (e.g., ``prepare_faostat_fbs_items.py``, ``prepare_food_loss_waste.py``, ``prepare_faostat_food_group_supply.py``).

**Usage**:
  * **Within-group disaggregation**: FBS item-level supply is the basis for per-food shares within each food group in ``estimate_baseline_diet.py``.
  * **FBS-anchored intake**: For the foods in ``diet.fbs_override_foods`` (meats, eggs, yam, coffee, cocoa), per-country intake is computed directly from FBS supply, the within-FBS-item share, the carcass-to-retail factor for meat, and the country/group consumer-waste fraction.
  * **Food loss and waste**: FBS Grand Total and per-item supply benchmark per-capita waste data when computing country- and group-level loss/waste fractions in ``prepare_food_loss_waste.py``.

UNSD SDG Indicator 12.3.1 (Food Loss & Waste)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United Nations Statistics Division (UNSD)

**Description**: SDG indicator database series ``AG_FLS_PCT`` (Food loss percentage) and ``AG_FOOD_WST_PC`` (Food waste per capita) covering SDG 12.3.1a/b.

**Version**: 2025 Q4.1 release; bulk CSV (~56 MB zip, ~1.7 GB uncompressed), filtered during extraction to the two relevant series (~500 KB)

**Coverage**:
  * Spatial: Global
  * Temporal: Varies by country

**Access**: https://unstats.un.org/sdgs/dataportal; bulk archive at https://unstats.un.org/sdgs/indicators/database/archive

**License**: UNdata terms -- data may be copied and redistributed free of charge provided UNdata/UNSD is cited.

**Citation**: United Nations Statistics Division. SDG Indicator Database, Goal 12.3.1a/b (Food Loss and Waste). https://unstats.un.org/sdgs/dataportal

**Retrieval**: Retrieved via the ``download_unsd_sdg`` rule (bulk zip download) and ``extract_unsd_sdg`` rule (filter to AG_FLS_PCT and AG_FOOD_WST_PC series). Output: ``data/downloads/unsd/SDG_12_3_1.csv``. Processed by ``prepare_food_loss_waste.py``.

**Usage**: Supplies per-country loss and waste fractions for food groups, injected into the crop-to-food conversion efficiencies during ``build_model``.

**Curated overrides**: Where the SDG/FBS-derived defaults are known to under-
or over-correct against survey-based intake data, ``prepare_food_loss_waste.py``
applies values from ``data/curated/food_loss_waste_overrides.csv``. Each
override row carries a ``source`` field that must cite a published estimate.
Country-specific rows take precedence over global rows
(``country == "*"``). Currently in use:

* **dairy** (global, ``waste_fraction = 0.30``): the SDG country-level waste
  fraction is a single all-foods average that systematically under-corrects
  dairy. USDA ERS Loss-Adjusted Food Availability puts combined retail and
  consumer loss at ~32% for fluid milk and dairy products in the United
  States, and FAO Save Food (Gustavsson et al. 2011) reports comparable
  industrialised-region losses with 14–19% post-harvest loss in low-income
  regions. The override brings modelled dairy intake into line with intake
  surveys (e.g. NHANES What We Eat in America), where the unmodified
  pipeline over-estimated US dairy by roughly a factor of two.

IFA FUBC -- Global Fertilizer Use by Crop and Country
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: International Fertilizer Association (IFA) / Dryad

**Description**: Global dataset on inorganic fertilizer application rates (N, P2O5, K2O) by crop and country based on expert surveys. The dataset includes historical data from 8 previous reports (1986-2014/15) and the most recent survey for the 2017-18 period, covering fertilizer application rates (kg/ha) and total consumption (thousand tonnes) for major crops worldwide.

**Version**: Dryad deposit (2022), covering FUBC reports 1-9

**Coverage**:
  * Spatial: Global, covering countries with significant fertilizer use
  * Temporal: 1986 onwards; latest survey covers 2017-18
  * Crops: Major crops including cereals, oilseeds, roots & tubers, vegetables, fruits, fiber crops, sugar crops

**Access**: https://datadryad.org/stash/dataset/doi:10.5061/dryad.2rbnzs7qh

**License**: Creative Commons Zero v1.0 Universal (CC0 1.0). Data is in the public domain.

**Citation**: Ludemann, C., Gruere, A., Heffer, P., & Dobermann, A. (2022). Global data on fertilizer use by crop and by country [Dataset]. Dryad. https://doi.org/10.5061/dryad.2rbnzs7qh

**Retrieval**: Bundled with the repository under ``data/bundled/doi_10_5061_dryad_2rbnzs7qh__v20250311/`` (CC0 licensed). The full Dryad dataset is included as-is due to Dryad API access restrictions.

**Usage**: Crop-specific fertilizer application rates for N2O emissions modeling and nutrient budget analysis.

.. _fadn-cost-data:

FADN -- Farm Accountancy Data Network (EU)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: European Commission DG Agriculture and Rural Development / LAMASUS project (Zenodo)

**Description**: European Union farm-level accounting database providing economic and structural data for agricultural holdings. This project uses the LAMASUS-processed NUTS-level agricultural dataset for **livestock production costs** (crop costs are now sourced from FAOSTAT; see above).

**Version**: LAMASUS v0.1 (2024); FADN Standard Results (SO classification, 2004-2020)

**Coverage**:
  * Spatial: EU-27 member states
  * Temporal: 2004-2020 (this project uses 2015-2020 for consistency with USDA data)

**Access**: https://zenodo.org/records/10939892 (LAMASUS dataset); original FADN at https://agriculture.ec.europa.eu/data-and-analysis/farm-structures-and-economics/fsdn_en

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0) for LAMASUS dataset; original FADN data (C) European Union, free for non-commercial use with attribution

**Citation**: Wogerer, M. (2024). LAMASUS NUTS-level agricultural data derived from public FADN 1989-2009 (SGM) & 2004-2020 (SO) (0.1) [Data set]. Zenodo. https://doi.org/10.5281/zenodo.10939892

**Retrieval**: Automatic via ``download_fadn_data`` Snakemake rule (Zenodo direct download). Output files: ``data/downloads/fadn_nuts0_so.csv`` and ``data/downloads/fadn_variables.xlsx``.

FADN Livestock Costs
^^^^^^^^^^^^^^^^^^^^

The FADN dataset provides livestock production cost data for animal products:

**Coverage**: 4 livestock categories (dairy cattle, beef cattle, pigs, poultry)

**Cost variables**: Same farm overhead costs as crops (SE340-SE380), plus livestock-specific costs (SE320 - veterinary, breeding, etc.)

**Explicitly EXCLUDED**: Purchased feed costs (SE310 - modeled endogenously), rent paid (SE375)

**Methodology**: A yield-based "bottom-up" approach is used to calculate costs per tonne:

1.  **Allocation**: Farm costs are allocated to livestock categories proportionally by output value, normalized by Total Farm Output (SE131) to properly account for mixed farming systems.
2.  **Unit Cost**: Costs are normalized to **Cost per Head** using Eurostat livestock unit (LU) coefficients (e.g., 1 Dairy Cow = 1 LU, 1 Pig = 0.3 LU).
3.  **Yield Conversion**: Cost per Head is converted to **Cost per Tonne** using country-specific physical yields derived from FAOSTAT (Production / Stocks). This ensures consistent physical metrics across all products and countries, correcting for internal data gaps in FADN reporting.

**Currency adjustment**: Same as crops (EUR -> USD using PPP, inflation-adjusted to base year)

**Usage**: Provides EU livestock production cost estimates, complementing USDA data. Costs are merged with USDA animal cost data via ``merge_animal_costs`` rule; when both sources have data for a product, values are averaged. Workflow: ``retrieve_fadn_animal_costs`` script -> ``processing/{name}/fadn_animal_costs.csv`` -> merged with USDA animal costs -> ``processing/{name}/animal_costs.csv``.

Eurostat Crop Production Statistics (apro_cpsh1)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Eurostat (Statistical Office of the European Union)

**Description**: Annual crop production statistics for EU and EFTA countries. This project uses the ``apro_cpsh1`` dataset to retrieve fodder crop production data for decomposing the aggregate GAEZ FDD (fodder) harvested area module into individual crops (alfalfa and silage maize).

**Version**: Annual data; 5-year average (configurable, default 2018-2022)

**Coverage**:
  * Spatial: EU-27 + EFTA (CH, IS, LI, NO) + UK (historical)
  * Temporal: 2000 onward
  * Crop codes used: G0000 (total plants harvested green), G2100 (lucerne/alfalfa), G3000 (green maize)

**Access**: https://ec.europa.eu/eurostat/databrowser/view/apro_cpsh1/default/table (Eurostat REST API)

**License**: Eurostat copyright policy; free reuse with attribution (`Eurostat copyright <https://ec.europa.eu/eurostat/help/copyright-notice>`_)

**Citation**: Eurostat. Crop production in EU standard humidity [apro_cpsh1]. https://ec.europa.eu/eurostat/databrowser/view/apro_cpsh1/

**Retrieval**: Automatic via ``retrieve_eurostat_fodder`` Snakemake rule (Eurostat REST API, ``workflow/scripts/retrieve_eurostat_fodder.py``). Output: ``data/downloads/eurostat_fodder_production.csv``.

**Usage**: Country-level production shares for splitting the GAEZ RES06 FDD harvested area between alfalfa (G2100/G0000) and silage maize (G3000/G0000). For non-EU/EFTA countries, GAEZ RES05 potential yield ratios are used as a suitability-based fallback. See ``workflow/scripts/build_fdd_area_shares.py``.

Livestock Data
--------------

GLEAM 3.0 Supplementary Tables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO (Food and Agriculture Organization of the United Nations)

**Description**: Supplementary tables from FAO's Global Livestock Environmental Assessment Model (GLEAM) providing detailed livestock system parameters including feed composition, digestibility, methane conversion factors, and manure management characteristics by species, production system, and region.

**Version**: GLEAM 3.0 Supplement S1; Excel workbook

**Coverage**:
  * Spatial: Global, by FAO region
  * Temporal: Reference year 2015

**Access**: https://www.fao.org/fileadmin/user_upload/gleam/docs/GLEAM_3.0_Supplement_S1.xlsx

**License**: FAO terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO. (2022). Global Livestock Environmental Assessment Model (GLEAM). Rome. https://www.fao.org/gleam/

**Retrieval**: Automatic via the ``download_gleam_supplement`` Snakemake rule. Downloaded to ``data/downloads/gleam_3.0_supplement_s1.xlsx``.

**Usage**: Livestock system parameters (feed digestibility, methane conversion factors, manure characteristics) for the animal production module.

GLEAM 3.0 Feed Intake and Production Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO (Food and Agriculture Organization of the United Nations)

**Description**: Country-level feed intake and animal production data from GLEAM version 3.0, together with supporting tables from the GLEAM documentation. Feed intakes are reported by country, species, production system, and aggregate feed category (kg DM/year). Production data covers meat (carcass weight), milk, and eggs (kg/year) per country and system. The documentation tables provide feed composition, yield fractions, and manure management parameters.

**Version**: GLEAM 3.0 outputs; bundled in ``data/bundled/gleam3/``

**Coverage**:
  * Spatial: 229 countries, 6 animal species (cattle, buffalo, sheep, goats, chicken, pigs), 8 production systems, 8 feed categories
  * Temporal: Reference year 2015

**Access**: Intake and production data obtained directly from FAO upon request. Documentation tables available from the `GLEAM resources page <https://www.fao.org/gleam/resources/en/>`_.

**License**: CC BY 4.0 + FAO database terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO. (2022). Global Livestock Environmental Assessment Model (GLEAM). Rome. https://www.fao.org/gleam/

**Retrieval**: Bundled with the repository under ``data/bundled/gleam3/``:
  * ``intakes.csv``: Feed intake by country, species, production system, and feed category
  * ``production.csv``: Animal production by country, species, production system, and product type
  * ``feed_items_categories.xlsx``: Authoritative feed item categorisation used for feed fraction computation
  * ``livestock_emissions.csv``: Livestock GHG emissions by country and species (used for validation)
  * ``manure_management_systems_fraction.csv``, ``manure_management_systems_type.csv``: Manure management system data
  * ``ruminants_feed_yield_fractions.csv``, ``ruminants_feed_codes.csv``: Ruminant feed composition
  * ``monogastrics_feed_yeild_fractions.csv``, ``monogastrics_feed_codes.csv``: Monogastric feed composition

Additionally, model-specific feed category mappings live in ``data/curated/gleam/feed_mapping.csv``.

**Usage**: Provides the feed baseline for the livestock module. Country-level intakes are mapped to model feed categories via ``compute_gleam3_feed_fractions.py``, split between co-products using FCR-weighted shares, and scaled to the configured reference year. Consumed by ``prepare_feed_baseline.py``. See :ref:`gleam-feed-baseline` for details.

Ruminant Roughage Composition (Mottet et al. 2017)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Mottet et al. (2017), based on FAO GLEAM 2.0

**Description**: Region x species ruminant roughage composition (fresh grass, hay, grass-legume/silage, crop residues, sugarcane tops, tree leaves) as shares of dry-matter intake. Used to re-split the GLEAM 3.0 ruminant roughage total, whose availability-based grass/residue split over-attributes grazed/cut grass relative to feeding surveys in South/East Asia (see :ref:`gleam-feed-baseline`).

**Coverage**: 10 GLEAM regions x 6 ruminant species (dairy/meat cattle, buffalo, small ruminants)

**Access**: Transcribed from the supplementary information (Tables SI 4-9) of the cited paper.

**License**: CC BY 4.0

**Citation**: Mottet, A., de Haan, C., Falcucci, A., Tempio, G., Opio, C., Gerber, P. (2017). Livestock: On our plates or eating at our table? A new analysis of the feed/food debate. *Global Food Security* 14, 1-8. https://doi.org/10.1016/j.gfs.2017.01.001

**Retrieval**: Bundled as ``data/curated/gleam/roughage_composition.csv`` (composition shares) and ``data/curated/country_mottet_region.csv`` (country-to-region map). Consumed by ``prepare_feed_baseline.py``.

Crop Residue Specifications
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Description**: The crop-residue supply model in ``data/curated/crop_residue_specs.csv`` -- one row per model crop giving the residue feed item it produces, the above-ground residue ratio (``slope`` per unit grain DM, ``intercept_kg_ha``), and the field utilisation efficiency (``fue``). Residue ratios are GLEAM 3.0 Supplement S1 Table S.3.1 R_AG regressions for the cereal, sugar, pulse, banana, soybean, rapeseed and cassava residues; groundnut and sweet-potato (absent from S.3.1) use fodder-literature ratios. Beyond GLEAM's cereal-straw set the table adds groundnut haulm, cassava foliage, sweet-potato vine, sugar-beet tops, soybean and rapeseed straw -- real ruminant roughages that GLEAM characterises only for residue production, not feed.

**Description (nutritive values)**: ``data/curated/supplementary_feed_properties.csv`` supplies gross energy, nitrogen content and digestibility for feed items GLEAM does not characterise in its material tables (the added residues above), merged into the feed-properties database by ``prepare_gleam_feed_properties.py``.

**License**: CC BY 4.0

**Sources**: GLEAM 3.0 Supplement S1 (Table S.3.1); Feedipedia (INRAE/CIRAD/AFZ/FAO) feed tables; Oteng-Frimpong et al. (2017) for groundnut haulm. Each row cites its source. Consumed by ``build_crop_residue_yields.py`` and ``prepare_gleam_feed_properties.py``.

Grassland Yield Data (ISIMIP / LPJmL)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ISIMIP (Inter-Sectoral Impact Model Intercomparison Project) |
**Access**: `ISIMIP data portal <https://www.isimip.org/outputdata/>`_ |
**License**: `CC BY 4.0 <https://creativecommons.org/licenses/by/4.0/>`_ (ISIMIP2a agriculture-sector LPJmL output; see `ISIMIP data licenses <https://www.isimip.org/gettingstarted/terms-of-use/licenses-publicly-available-isimip-data/>`_)

Historical managed grassland yields from the LPJmL model (above-ground dry matter production) at 0.5° x 0.5° resolution, ISIMIP2a agriculture sector, WATCH forcing, no bias correction, variable CO2, 1971-2001 (`yield-mgr-noirr-default`). Used for grazing-based livestock production potential estimates. Cite the ISIMIP2a agriculture-sector archive (`10.5880/PIK.2017.006 <https://doi.org/10.5880/PIK.2017.006>`_) and Schaphoff et al. (2018, `gmd-11-1343-2018 <https://doi.org/10.5194/gmd-11-1343-2018>`_) for LPJmL.

Spatial and Land Use Data
--------------------------

GADM (Global Administrative Areas) v4.1
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: GADM project

**Description**: Global administrative boundary polygons (ADM_0 to ADM_5 levels). This project uses level-1 (ADM_1) regions (e.g., states/provinces) for building optimization regions via spatial clustering.

**Version**: GADM 4.1; multi-layer GeoPackage (``gadm_410-levels.gpkg``), with ``ADM_1`` extracted to a lighter GPKG for convenience

**Coverage**:
  * Spatial: Global administrative boundaries

**Access**: https://gadm.org/

**License**: Free for academic/non-commercial use with attribution; redistribution not allowed; commercial use requires permission (`License <https://gadm.org/license.html>`__)

**Citation**: GADM (2024). Global Administrative Areas, version 4.1. https://gadm.org/

**Retrieval**: Automatic via Snakemake rules

**Usage**: Building optimization regions via clustering of ADM_1 (states/provinces)

.. _copernicus-land-cover:

Copernicus Satellite Land Cover
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Copernicus Climate Change Service (C3S)

**Description**: Global land cover classification gridded maps from 1992 to present derived from satellite observations. The dataset describes the land surface into 22 classes including various vegetation types, water bodies, built-up areas, and bare land.

**Version**: v2.1.1 (2016 onwards); NetCDF format

**Coverage**:
  * Spatial: Global (Plate Carree projection), 300 m resolution
  * Temporal: Annual (with approximately one-year publication delay)

**Access**: Original source: https://cds.climate.copernicus.eu/datasets/satellite-land-cover. For builds, GLADE downloads a mirror of the single year/version it needs from Zenodo (see *Retrieval* below).

**License**: CC-BY-4.0. The 2016-onwards C3S maps (which is what GLADE uses, since ``baseline_year`` is 2020) are released under the Creative Commons Attribution 4.0 International licence, as stated in the authoritative C3S/Copernicus metadata. This permits redistribution provided the Copernicus attribution and source DOI are retained; both are embedded in the Zenodo deposition. (The CDS download page also bundles the ESA CCI licence -- which governs the pre-2016 v2.0.7 maps that GLADE does not use -- and the VITO licence, which restricts only near-real-time PROBA-V products, not historical annual maps.)

**Required attribution**: "Generated using Copernicus Climate Change Service information 2020. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains."

**Citation**: Copernicus Climate Change Service, Climate Data Store, (2019): Land cover classification gridded maps from 1992 to present derived from satellite observation. Copernicus Climate Change Service (C3S) Climate Data Store (CDS). https://doi.org/10.24381/cds.006f2c9a

**Retrieval**: Automatic via the ``download_land_cover`` Snakemake rule, which uses ``curl`` to fetch the pre-extracted land cover classification (``lccs_class`` only, ~320 MB NetCDF) from our Zenodo mirror. The rule writes ``data/downloads/land_cover_lccs_class.nc``. The mirror itself is produced from the upstream CDS dataset by the maintainer tool ``tools/mirror_land_cover.py`` (see :ref:`redistributing-datasets`).

**Configuration**: The land cover year is derived from the top-level ``baseline_year`` parameter, and the version from ``config['data']['land_cover']['version']`` (default: v2_1_1). The mirror to download from is pinned by ``config['data']['land_cover']['zenodo_record']`` (the numeric Zenodo record id); the download URL and file name are derived from these three values.

**Usage**: Spatial analysis of agricultural land availability and land use constraints.

.. _esa-biomass-cci:

ESA Biomass CCI -- Global Above-Ground Biomass
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ESA Climate Change Initiative (Biomass_cci), NERC EDS Centre for Environmental Data Analysis (CEDA)

**Description**: Global forest above-ground biomass (AGB) maps derived from satellite observations (Sentinel-1 SAR, Envisat ASAR, ALOS PALSAR). Provides annual AGB estimates in tonnes per hectare, along with per-pixel uncertainty estimates and change maps.

**Version**: v6.0 (released April 2025); NetCDF format

**Coverage**:
  * Spatial: Global (90N to 90S, 180W to 180E), 10 km resolution
  * Temporal: Years 2007, 2010, 2015-2022

**Access**: https://catalogue.ceda.ac.uk/uuid/95913ffb6467447ca72c4e9d8cf30501

**License**: ESA CCI Biomass Terms and Conditions. Public data available to both registered and non-registered users. (`License <https://artefacts.ceda.ac.uk/licences/specific_licences/esacci_biomass_terms_and_conditions_v2.pdf>`__)

**Citation**: Santoro, M.; Cartus, O. (2025): ESA Biomass Climate Change Initiative (Biomass_cci): Global datasets of forest above-ground biomass for the years 2007, 2010, 2015, 2016, 2017, 2018, 2019, 2020, 2021 and 2022, v6.0. NERC EDS Centre for Environmental Data Analysis. https://doi.org/10.5285/95913ffb6467447ca72c4e9d8cf30501

**Retrieval**: Automatic via the ``download_biomass_cci`` Snakemake rule using curl. Downloaded to ``data/downloads/esa_biomass_cci_v6_0.nc``. No registration or API key required.

**Usage**: Analysis of carbon storage potential and forest biomass constraints on land use.

.. _soilgrids-soc:

ISRIC SoilGrids -- Global Soil Organic Carbon Stock
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ISRIC - World Soil Information

**Description**: Global soil organic carbon (SOC) stock predictions for 0-30 cm depth interval based on digital soil mapping using Quantile Random Forest. Provides mean predictions along with quantile estimates and uncertainty layers derived from the global compilation of soil ground observations (WoSIS).

**Version**: SoilGrids250m 2.0 (v2.0); GeoTIFF format via Web Coverage Service (WCS)

**Coverage**:
  * Spatial: Global (-180 to 180, -56 to 84), native 250 m resolution; retrieved at configurable resolution (default: 10 km)
  * Temporal: Based on data from April 1905 to July 2016

**Access**:
  * Website: https://www.isric.org/explore/soilgrids
  * Data catalogue: https://data.isric.org/geonetwork/srv/api/records/713396f4-1687-11ea-a7c0-a0481ca9e724
  * FAQ: https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B., Ribeiro, E., & Rossiter, D. (2021). SoilGrids 2.0: producing soil information for the globe with quantified spatial uncertainty. *SOIL*, 7(1), 217-240. https://doi.org/10.5194/soil-7-217-2021

**Retrieval**: Automatic via the ``download_soilgrids_ocs`` Snakemake rule using ISRIC's WCS endpoint. Output: ``data/downloads/soilgrids_ocs_0-30cm_mean.tif`` (~1.2 MB at 10km resolution). No registration or API key required.

**Configuration**: Target resolution via ``config['data']['soilgrids']['target_resolution_m']`` (default: 10000 meters = 10 km)

**Usage**: Soil carbon baseline for land-use change emissions calculations. Units: tonnes per hectare (t/ha) for 0-30 cm depth interval.

.. _cook-patton-regrowth:

Cook-Patton & Griscom -- Forest Carbon Accumulation Potential
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Global Forest Watch / The Nature Conservancy / World Resources Institute

**Description**: Global map of carbon accumulation potential from natural forest regrowth in forest and savanna biomes. Estimates the rate at which carbon could be sequestered in aboveground and belowground (root) live biomass during the first thirty years of natural forest regrowth. Based on 13,112 georeferenced measurements combined with 66 environmental covariate layers in a random forest model.

**Version**: GeoTIFF, native 1 km resolution; resampled to model grid (~9 km)

**Coverage**:
  * Spatial: Global, forest and savanna biomes (~16% of land pixels have valid data)
  * Projection: ESRI:54034 (World Cylindrical Equal Area)

**Access**: https://data.globalforestwatch.org/documents/f950ea7878e143258a495daddea90cc0

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., ... & Griscom, B. W. (2020). Mapping carbon accumulation potential from global natural forest regrowth. *Nature*, 585(7826), 545-550. https://doi.org/10.1038/s41586-020-2686-x

**Retrieval**: Automatic via the ``download_forest_carbon_accumulation_1km`` rule followed by ``resample_regrowth``. The native 1 km GeoTIFF (~610 MB) is downloaded with curl (stored as a temporary file), then resampled onto the model's 1/12 degree resource grid. Final output: ``processing/shared/luc/regrowth_resampled.nc``. No registration or API key required.

**Usage**: Estimating carbon sequestration potential (Mg C/ha/yr) from natural forest restoration and regrowth for land sparing credits.

.. _hayek-reforestation-mask:

Hayek et al. -- Carbon Opportunity Areas in Global Beef Pastures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Hayek, M. N. et al. / New York University

**Description**: Geospatial data supplement to Hayek et al. (2024), providing biome classifications, potential vegetation carbon stocks, and carbon accumulation rates for global pasture areas. This project uses the biome layer (band 1 of ``pastures_coi_Geospatial.tif``) and potential vegetation carbon stocks (``pastures_coi_pvC_stack.tif``) to build a binary reforestation eligibility mask. Biomes 1--8 are classified as forest-potential; biome 9 (savanna) is split at a 75 MgC/ha potential vegetation carbon threshold; biomes 10--15 are non-forest.

**Version**: v1 (2024); GeoTIFF files on Zenodo

**Coverage**:
  * Spatial: Global, 5 arcminute resolution (~9 km)
  * Temporal: Present-day conditions

**Access**: https://doi.org/10.5281/zenodo.12688280

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Hayek, M. N., Piipponen, J., Resare Sahlin, K., Kummu, M., McClelland, S. C. & Carlson, K. M. (2024). Opportunities for carbon sequestration from removing or intensifying pasture-based beef production. *Proceedings of the National Academy of Sciences*, 121(46), e2405758121. https://doi.org/10.1073/pnas.2405758121

**Retrieval**: Automatic via the ``download_hayek_reforestation_biomes`` and ``download_hayek_reforestation_pvc`` rules. Two GeoTIFFs (~5.4 MB and ~37 MB) are downloaded with curl to ``data/downloads/hayek_reforestation/``. No registration or API key required.

**Usage**: Building a binary reforestation eligibility mask (``processing/shared/luc/reforestation_mask.nc``) to restrict Cook-Patton regrowth credits to biomes where forest could plausibly regrow. See :ref:`luc-spared-land-filtering` for details.

LUIcube -- Global Land-Use Intensity Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Matej et al. (2025)

**Description**: Global gridded dataset of land-use intensity indicators covering multiple land-use classes including grasslands. Provides area, harvested HANPP (Human Appropriation of Net Primary Production), and remaining NPP per grid cell, enabling computation of grassland yields and grazing intensity. This project uses the GL-owl (grassland with scattered trees) and GL-notrees (open grassland) classes.

**Version**: v1.0 (2025); GeoTIFF files within ZIP archives on Zenodo

**Coverage**:
  * Spatial: Global, 30 arcsec (~1 km) resolution
  * Temporal: Annual, year derived from top-level ``baseline_year`` parameter

**Access**: Zenodo: `14137284 <https://zenodo.org/records/14137284>`_ (GL-owl), `14013964 <https://zenodo.org/records/14013964>`_ (GL-notrees)

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Matej, S., Weidinger, F., Kaufmann, L., Roux, N., Gingrich, S., Haberl, H., Krausmann, F., & Erb, K.-H. (2025). A global land-use data cube 1992-2020 based on the Human Appropriation of Net Primary Production. *Scientific Data*, 12, 511. https://doi.org/10.1038/s41597-025-04788-1

**Retrieval**: Automatic via the ``download_luicube_grassland`` rule using ``remotezip`` to extract individual GeoTIFFs from Zenodo ZIP archives without downloading the full archive. The ``resample_luicube_grassland`` rule sums the two grassland classes, reprojects to the model grid, and outputs ``processing/shared/luc/luicube_grassland.nc``. The ``build_luicube_grassland_yields`` rule computes per-region/class yields (tDM/ha) and grazing intensity.

**Usage**: Grassland area, productivity, utilisation intensity, and derived yields for the grassland production module.

Population Data
---------------

UN World Population Prospects (WPP) 2024
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: UN DESA Population Division

**Description**: Official UN population estimates and projections by country, age, and sex. This project uses the total population table (medium variant) for planning-horizon population totals and the abridged life table for years-of-life-lost calculations.

**Version**: 2024 Revision; CSV files (medium variant)

**Coverage**:
  * Spatial: Global, by country
  * Temporal: Estimates and projections

**Access**: https://population.un.org/wpp/

**License**: Creative Commons Attribution 3.0 IGO (CC BY 3.0 IGO) (`Copyright notice <https://population.un.org/wpp/downloads>`_)

**Citation**: United Nations, Department of Economic and Social Affairs, Population Division (2024). World Population Prospects 2024. https://population.un.org/wpp/

**Retrieval**: Automatic via Snakemake rules

**Files used**:
  * ``WPP2024_TotalPopulationBySex.csv.gz``
  * ``WPP2024_Life_Table_Abridged_Medium_2024-2100.csv.gz``

**Usage**:
  * Scaling per-capita dietary requirements to total demand
  * Age-structured population for health burden calculations
  * Global life expectancy schedule for health loss valuation

Economic Data
-------------

IMF World Economic Outlook -- GDP per Capita
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: International Monetary Fund (IMF) |
**Access**: https://www.imf.org/external/datamapper/NGDPDPC@WEO (`API documentation <https://www.imf.org/external/datamapper/api/help>`__) |
**License**: Free to use with attribution (`Terms of use <https://www.imf.org/en/about/copyright-and-terms#data>`__)

GDP per capita estimates (current prices, USD) from the World Economic Outlook database (indicator ``NGDPDPC``). Retrieved automatically via the IMF DataMapper API. Output: ``processing/{name}/gdp_per_capita.csv``. Used by ``prepare_health_costs`` for multi-objective country clustering based on geography, GDP similarity, and population balance.

Health and Epidemiology Data
-----------------------------

.. _ihme-gbd-mortality:

IHME GBD 2023 -- Mortality Rates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Cause-specific mortality rates by country, age, and sex from the Global Burden of Disease Study 2023. Used to calculate baseline disease burden attributable to dietary risk factors.

**Version**: GBD 2023; CSV export from the GBD Results Tool

**Coverage**:
  * Spatial: Global, by country
  * Query parameters:

    - Measure: Deaths (Rate per 100,000 population)
    - Causes: Ischemic heart disease, Ischemic stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
    - Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual age bins)
    - Sex: Both
    - Year: must match ``baseline_year`` in the config (default: 2020)

**Access**: https://vizhub.healthdata.org/gbd-results/

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement; `Terms <https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement>`_)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2024. Available from https://vizhub.healthdata.org/gbd-results/

**Retrieval**: Automatically processed via ``workflow/scripts/prepare_gbd_mortality.py``

**Manual download steps**:

1. Visit https://vizhub.healthdata.org/gbd-results/ and sign in with your IHME account.
2. Reproduce the query parameters above. This permanent link is configured for year 2020: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/ab3e7b526315599bf5cabbfe6c34e104 -- adjust the year if using a different ``baseline_year``.
3. Export the results as CSV (allow some time for the IHME to process the query) and save to ``data/manually_downloaded`` as ``IHME-GBD_2023-death-rates-{year}.csv`` where ``{year}`` matches your ``baseline_year``. Consider checking the file modification time and potentially resetting it (on Linux, run ``touch`` on the file); sometimes the modification time of the downloaded file can be in the future, which confuses Snakemake.

.. _ihme-relative-risks:

IHME GBD 2023 Burden of Proof -- Dietary Relative-Risk Curves
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Age-aggregated exposure-response (relative risk vs. intake) curves for each dietary risk factor and cause, from the GBD 2023 Burden of Proof analysis. One curve per ``(risk_factor, cause)`` pair, with mean and 95% uncertainty interval over an intake grid.

**Version**: GBD 2023 (served by the Burden of Proof tool); JSON API

**Access**: https://vizhub.healthdata.org/burden-of-proof/

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement). **Non-redistributable**: the downloaded curves are gitignored (``data/downloads/burden_of_proof/``) and must be fetched per user.

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Burden of Proof Risk Function estimates. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2025.

**Retrieval**: The ``retrieve_burden_of_proof`` rule (``workflow/scripts/retrieve_burden_of_proof.py``) downloads the curves automatically -- **no login required**. The Burden of Proof data endpoints sit behind a Cloudflare edge bot-check only, which a normal browser User-Agent passes from a residential/university IP (automated cloud IPs may get a 403; run the rule from a normal machine if so). The risk/cause GBD identifiers come from ``config.health.gbd_rei_id`` / ``gbd_cause_id``. ``prepare_relative_risks.py`` then converts the exposure axis to the model basis, clips each curve at the TMREL, and restores the GBD age structure (see the curated tables below).

Two companion tables under ``data/curated/health/`` are our own derived results (committed; see the model health documentation for the method):

* ``rr_age_attenuation.csv`` -- per ``(risk_factor, cause, age)`` multiplicative log-RR attenuation. The Burden of Proof tool serves only all-ages curves, so the age structure is reconstructed: the age *shape* is the GBD 2019 RR appendix (indirect; ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX``), normalized to GBD's 60-64 reference age group (the median age-at-event of the cardiovascular age trend, to which GBD assigns the estimated risk curve), so the BoP "All Ages" curve is reproduced at age 60-64. Regenerated once via ``workflow/scripts/generate_rr_age_attenuation.py``.
* ``rr_tmrel.csv`` -- theoretical minimum risk exposure level per risk factor, from GBD 2023 appendix Table 18 (in GBD intake basis; converted to model basis at build time). ``red_meat`` is treated as monotonic-harmful (TMREL 0) to match its literature override.

.. _ihme-diet-risk-exposure:

IHME GBD 2023 -- Dietary Risk Exposure Estimates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Country-level dietary risk exposure estimates from the Global Burden of Disease Study 2023, covering 15 dietary risk factors with mean exposure and uncertainty by country, age group, sex, and year. Used as the **anchor source** for risk-factor food groups (fruits, vegetables, whole_grains, legumes, nuts_seeds, red_meat) in the baseline diet, so the model's intake basis matches the basis the GBD relative-risk functions are calibrated against. GDD-IA provides the fallback when GBD lacks a country.

The 2023 release ships only per-5-year-age-bucket estimates split by sex; it does not include the ready-made "25 plus" both-sex aggregate that the 2019 release provided. ``prepare_gbd_food_group_intake.py`` therefore reconstructs the adult (25+) both-sex exposure by population-weighting the adult age buckets (using per-country age-bucket population for the reference year) and averaging the two sexes. The bulk files also contain subnational locations (US states, UK nations, Indian/Pakistani provinces, ...) whose names collide with countries, so processing restricts to national locations by ``location_id`` (taken from the GBD 2023 death-rates file) rather than by name.

**Version**: GBD 2023; two ZIP archives, each containing per-risk-factor CSVs (~90 MB each)

**Coverage**:
  * Spatial: 204 countries and territories (plus subnational units, filtered out)
  * Temporal: 1990-2023, by age group and sex
  * Risk factors: Calcium, fiber, fruit, legumes, milk, nuts and seeds, omega-3 (seafood), omega-6 PUFA, processed meat, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains

**Access**: https://ghdx.healthdata.org/record/ihme-data/gbd-2023-dietary-risk-exposure-estimates

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement; `Terms <https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement>`_)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Dietary Risk Exposure Estimates. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2025.

**Retrieval**: Processed via ``workflow/scripts/prepare_gbd_food_group_intake.py`` from ``data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_1/`` and ``data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_2/``.

**Manual download steps**:

1. Log in to your IHME account.
2. Download both archives (direct links: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2023_RISK_EXPOSURE_DIET_1.zip and https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2023_RISK_EXPOSURE_DIET_2.zip).
3. Extract each ZIP and place the resulting directories as ``data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_1`` and ``data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_2``.

.. _gdd-ia-dietary-intake:

Global Dietary Database for Impact Assessments (GDD-IA)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Marco Springmann (University College London).

**Description**: Country-level dietary intake estimates that combine
regional food availability and food-waste estimates, socio-demographic
variation in intake from dietary surveys, and energy-intake estimates
based on measurements of body weight, height and physical activity. The
result is a harmonised per-country intake dataset reported in parallel
grams/day and kcal/day for every food category. It is designed for
dietary impact assessments that need complete diets, absolute intake
levels that minimise the risk of over- or under-estimation, and regional
comparability. This is the model's default baseline-diet source
(``diet.source: gdd_ia``).

.. note::

   GDD-IA is a distinct dataset from the **Global Dietary Database
   (GDD)** of Tufts University, despite the similar name. The model does
   not use the Tufts GDD.

**Version**: Zenodo record `20818140
<https://doi.org/10.5281/zenodo.20818140>`_. The record covers 1990-2020
in five-year steps; the workflow fetches the two CSVs (grams/day and
kcal/day) for the release closest to the configured ``baseline_year``
and warns when an exact release is unavailable.

**Coverage**:
  * Spatial: 171 source countries. Twelve of GLADE's 175 default
    countries are filled via configured proxies -- see
    :doc:`current_diets`.
  * Content: per-country mean dietary intake covering the major food
    groups the model represents (cereals — refined and whole-grain —
    vegetables, fruits, nuts and seeds, oils, sugar, legumes, poultry,
    red meat, dairy, eggs) plus out-of-scope categories (alcohol,
    seafood, spices, rendered animal fats) used only for the
    caloric-normalisation step. The dataset is stratified by age, sex
    and urban/rural residence; the pipeline consumes the all-ages,
    both-sexes, all-residences mean strata.

**Access**: Public. Downloaded automatically from Zenodo; no manual step
and no registration.

**License**: CC-BY-4.0.

**Citation**: Springmann, M. Global dietary estimates for conducting
health, environmental and economic impact assessments. *Nature Food*
(2026). `doi:10.1038/s43016-026-01388-z
<https://doi.org/10.1038/s43016-026-01388-z>`_. Dataset:
`doi:10.5281/zenodo.20818140 <https://doi.org/10.5281/zenodo.20818140>`_.

**Retrieval**: Downloaded by the ``download_gdd_ia_intake`` rule (which
pins the Zenodo record id) to ``data/downloads/gdd_ia/``; processed by
``workflow/scripts/prepare_gdd_ia_dietary_intake.py`` (rule
``prepare_gdd_ia_dietary_intake``).

NHANES / FPED -- What We Eat in America
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: USDA Agricultural Research Service, Food Surveys Research Group (in partnership with the U.S. Department of Health and Human Services / CDC NHANES)

**Description**: "What We Eat in America" is the dietary intake interview component of the National Health and Nutrition Examination Survey (NHANES). The Food Patterns Equivalents Database (FPED) translates each reported food into USDA Food Pattern equivalents (cup-eq, ounce-eq, teaspoon-eq, or grams) for 37 food-pattern components. We consume the released demographic-table summary (mean amounts of equivalents consumed per individual, by male/female and age) for the most recent prepandemic NHANES cycle and use it as an intake-based override for the United States.

**Version**: FPED 1720 -- "for use with WWEIA, NHANES 2017-March 2020 Prepandemic". Single-PDF demographic table (~160 KB), USDA ARS / FSRG, 2023 release.

**Coverage**:
  * Spatial: United States only.
  * Content: Mean daily intake of fruit, vegetables, grains (whole and refined), dairy, meat / poultry / seafood / eggs / nuts and seeds, legumes, oils, solid fats, added sugars, and alcoholic drinks for the population aged 2+ (and finer male/female × age strata which we do not currently consume).

**Access**: https://www.ars.usda.gov/northeast-area/beltsville-md-bhnrc/beltsville-human-nutrition-research-center/food-surveys-research-group/docs/fped-data-tables/

**License**: U.S. Government Work / Creative Commons CC0 (public domain). No restrictions on use; attribution to USDA is requested but not required. (`USDA WWEIA license <https://agdatacommons.nal.usda.gov/articles/dataset/What_We_Eat_In_America_WWEIA_Database/24660126>`_)

**Citation**: U.S. Department of Agriculture, Agricultural Research Service. 2023. *Food Patterns Equivalents Intakes from Foods: Mean Amounts Consumed per Individual, by Male/Female and Age, What We Eat in America, NHANES 2017-March 2020 Prepandemic*. www.ars.usda.gov/nea/bhnrc/fsrg

**Retrieval**: Automatic via the ``download_nhanes_fped`` rule (URL templated by FPED cycle, default ``1720``). The PDF is parsed with ``pdftotext`` (poppler) by ``prepare_nhanes_dietary_intake.py``; the column-to-food-group mapping and unit conversions live in the curated CSV ``data/curated/nhanes_fped_mapping.csv``, with conversion factors sourced from USDA MyPlate cup/ounce-equivalent definitions and the FPED Methodology and User Guide.

**Usage**: For the United States, NHANES values take precedence over both GDD intake estimates and FAOSTAT food-supply estimates in ``merge_dietary_sources.py`` for every food group it covers (fruits, vegetables, starchy vegetables, refined and whole grains, dairy, eggs, oils, red meat, poultry, nuts and seeds, legumes, sugar). Coverage of other intake-survey-based national datasets (e.g. CCHS for Canada, ENSANUT for Mexico) can be added in the same precedence layer.

The FPED columns are *fat-stripped decompositions* rather than food masses:
FPED Total Dairy is the low-fat / skim-equivalent fraction (butterfat is
extracted into a separate Solid Fats axis), and Meat / Poultry are reported
as lean fractions. Three projection adjustments are therefore applied to
arrive at modelled-food masses:

* **Butter top-up**: ``prepare_nhanes_dietary_intake.py`` reads FAOSTAT FBS
  Butter and Ghee (item 2740) for the configured country, applies the FAO
  dairy-commodity-tree milk-equivalent factor (21.3) and the country-level
  dairy waste fraction, and adds the result to the FPED Total Dairy value.
* **Cured meat fold**: FPED Cured Meat is added into ``red_meat`` to match
  the GDD v09 fold and keep consumption consistent with FAOSTAT
  slaughter-volume production accounting.
* **Fruit juice projection**: FPED Fruit Juice cup-equivalents are
  projected onto fresh-fruit-equivalent grams under ``fruits`` (USDA
  MyPlate counts 1 cup juice as 1 cup fruit).

All cup-, oz-, and tsp-equivalent conversion factors used in
``data/curated/nhanes_fped_mapping.csv`` are sourced row-by-row from
the FPED Methodology and User Guide
(`Bowman et al. 2020 <https://www.ars.usda.gov/ARSUserFiles/80400530/pdf/fped/FPED_1718.pdf>`_)
Tables 8, 9, 10, 11, and 13. One factor -- the grams-per-oz-equivalent
for grains -- is an explicit modelling **assumption** rather than a
published value. FPED MUG Table 10 gives two rules: 16 g flour per
ounce-equivalent for flour-based products (bread, biscuits, pancakes,
crackers, baked goods) and 28.35 g grain per ounce-equivalent for
intact grains (rice, pasta, oats, RTE cereals). The FPED demographic
table aggregates the two product types into a single oz-eq column per
grain group without an external mix breakdown, so we apply the
unweighted midpoint (~22 g/oz-eq) as a deliberate compromise. The
trade-off is documented in the per-row note in the mapping CSV; if a
better-sourced US-mix breakdown becomes available, the factor should
be updated accordingly.

Water Resources Data
--------------------

Water Footprint Network -- Monthly Blue Water Availability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Water Footprint Network (Hoekstra & Mekonnen)

**Description**: Monthly blue water availability for 405 GRDC river basins, provided alongside blue-water scarcity indicators.

**Version**: Appendix VII of Hoekstra & Mekonnen (2011); ESRI shapefile + Excel workbook (monthly availability in Mm3/month)

**Coverage**:
  * Spatial: 405 GRDC river basins (global)

**Access**: https://www.waterfootprint.org/resources/appendix/Report53_Appendix.zip

**License**: No explicit license; citation requested. Users should evaluate whether their use qualifies as fair use and contact UNESCO-IHE for commercial applications.

**Citation**: Hoekstra, A.Y. and Mekonnen, M.M. (2011). *Global water scarcity: monthly blue water footprint compared to blue water availability for the world's major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, Netherlands.

**Retrieval**: Automatic via Snakemake rules

**Usage**: Constraining irrigated crop production by basin-level water availability.

Huang et al. -- Gridded Irrigation Water Withdrawals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Huang et al. (2018), HESS

**Description**: Monthly gridded (0.5 degree) sectoral water withdrawal dataset for 1971-2010, distinguishing six water use sectors. This project uses the irrigation sector data to represent current agricultural water withdrawals for validation scenarios.

**Version**: v2 (Zenodo); 7z-compressed NetCDF bundle (~296 MB). We extract the irrigation withdrawal WaterGAP file (``withd_irr_watergap.nc``).

**Coverage**:
  * Spatial: Global, 0.5 degree resolution
  * Temporal: Monthly, 1971-2010 (final year 2010 used as proxy for current irrigation)

**Access**: https://zenodo.org/records/1209296

**License**: Open access; citation requested

**Citation**: Huang, Z., Hejazi, M., Li, X., Tang, Q., Vernon, C., Leng, G., Liu, Y., Doll, P., Eisner, S., Gerten, D., Hanasaki, N., and Wada, Y. (2018). Reconstruction of global gridded monthly sectoral water withdrawals for 1971-2010 and analysis of their spatiotemporal patterns. *Hydrology and Earth System Sciences*, 22, 2117-2133. https://doi.org/10.5194/hess-22-2117-2018

**Retrieval**: Retrieved via the ``download_huang_irrigation_water`` rule (Zenodo download, 7z extraction). The ``process_huang_irrigation_water`` rule aggregates gridded monthly data to model regions by area-weighted summation. Outputs: ``processing/{name}/water/current_use/monthly_region_water.csv`` and ``processing/{name}/water/current_use/region_growing_season_water.csv``, selected when ``config['water']['supply_scenario']`` is ``"current_use"``.

**Usage**: Aggregated to regions for validation of water module against observed irrigation withdrawals.

Nutritional Data
----------------

USDA FoodData Central
~~~~~~~~~~~~~~~~~~~~~

**Provider**: U.S. Department of Agriculture, Agricultural Research Service

**Description**: Comprehensive food composition database providing nutritional data for foods. This project uses the SR Legacy (Standard Reference) database, which contains laboratory-analyzed nutrient data for over 7,000 foods.

**Version**: Retrieved via REST API; nutritional values per 100g of food product

**Coverage**:
  * Content: Macronutrient composition (protein, carbohydrates, total lipid/fat, energy/calories)

**Access**: https://fdc.nal.usda.gov/ (web interface) or via REST API (`API documentation <https://fdc.nal.usda.gov/api-guide.html>`__)

**License**: Public domain under CC0 1.0 Universal (CC0 1.0). No permission needed for use, but USDA requests attribution.

**Citation**: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. https://fdc.nal.usda.gov/

**Retrieval**: The build uses the pre-fetched ``data/curated/nutrition.csv``. Set ``data.usda.retrieve_nutrition: true`` to instead fetch fresh data via the ``retrieve_usda_nutrition`` rule, which requires a USDA API key.

**API Key**: Free, instant signup at https://fdc.nal.usda.gov/api-key-signup. Provide the key via the ``USDA_API_KEY`` environment variable or ``credentials.usda.api_key`` in ``config/secrets.yaml``; it is read only when ``retrieve_nutrition`` is enabled.

**Usage**: Nutritional composition of model foods (protein, carbohydrates, fat, energy). The mapping from model foods to USDA FoodData Central IDs is maintained in ``data/curated/usda_food_mapping.csv``.

FAO Nutrient Conversion Table for SUA (2024)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Food and Agriculture Organization of the United Nations (FAO)

**Description**: Official nutrient conversion factors that align FAO Supply Utilization Account (SUA) quantities with macro- and micronutrient totals for hundreds of food items.

**Version**: 2024 Excel workbook

**Access**: https://www.fao.org/3/CC9678EN/Nutrient_conversion_table_for_SUA_2024.xlsx

**License**: (C) FAO 2024. Reuse for private study, research, teaching, or other non-commercial purposes is allowed with acknowledgement of FAO; translation, adaptation, resale, and commercial uses require prior permission via copyright@fao.org.

**Retrieval**: Automatically downloaded to ``data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx`` by the ``download_fao_nutrient_conversion_table`` rule.

**Usage**: Contains data on edible portion of foods. ``workflow/scripts/prepare_fao_edible_portion.py`` reads sheet ``03`` to export edible portion coefficients for configured crops into ``processing/{name}/fao_edible_portion.csv``. Moisture fractions required for fresh-mass scaling live in ``data/curated/crop_moisture_content.csv`` (derived primarily from the GAEZ v5 Module VII documentation with a few documented assumptions) and are joined inside ``workflow/scripts/build_model.py``. Note that for certain crops (grains: rice, barley, oat, buckwheat; sugar crops: sugarcane, sugarbeet; oil crops: oil-palm, rapeseed), the script overrides FAO's coefficients to 1.0 so that downstream processing pathways manage the losses explicitly.

Data License Summary
--------------------

Most datasets used in this project require attribution. Some disallow redistribution, meaning that GLADE cannot be distributed together with these datasets. Some furthermore prohibit commercial use without prior agreement or a paid-for license.

**Open licenses (attribution required, redistribution allowed)**:

* **CC0 1.0 / Public domain** (USDA FoodData Central, IFA FUBC, BLS CPI-U): No restrictions; attribution requested
* **CC BY 4.0** (GAEZ, FAOSTAT, GDD-IA, GLEAM 3.0 Feed Intake, SoilGrids, Cook-Patton, LUIcube, LAMASUS, ISIMIP2a / LPJmL grassland yield, Copernicus Land Cover 2016+): Requires attribution
* **CC BY 3.0 IGO** (UN WPP): Requires attribution to UN
* **CC BY** (USDA Costs, USDA Livestock Costs): Requires attribution
* **Eurostat copyright** (Eurostat apro_cpsh1): Free reuse with attribution
* **Open access** (Huang et al. irrigation, UNSD SDG, IMF WEO): Free to use with attribution/citation

**Restrictive licenses (non-commercial use and/or no redistribution)**:

* **Non-commercial, no redistribution** (IHME GBD mortality, IHME GBD relative risks, IHME GBD dietary exposure): Free for non-commercial research; data may not be redistributed or used commercially without permission
* **Non-commercial with attribution** (GADM, FADN): Free for academic/non-commercial use; GADM prohibits redistribution, FADN requires EU attribution
* **FAO terms** (GLEAM 3.0 Supplement, FAO Nutrient Conversion): Non-commercial reuse with FAO acknowledgement; commercial use requires prior permission
* **Custom terms** (ESA Biomass CCI, Water Footprint Network): Various provider-specific terms; see individual entries above

.. _redistributing-datasets:

Redistributing datasets via Zenodo
----------------------------------

Some upstream datasets are free to use but sit behind an API key or registration
wall (historically the Copernicus land cover data). Where the licence permits
redistribution, GLADE mirrors the exact slice it needs to `Zenodo
<https://zenodo.org/>`__ and downloads it during builds with a plain HTTP
request. This removes the per-user credential, pins an immutable, citable
version (each Zenodo version has its own DOI and record id), and gives a single
reusable pattern for any future dataset in the same situation.

The components are:

* ``tools/zenodo_publish.py`` -- a dataset-agnostic helper that creates (or
  versions) a Zenodo deposition, uploads files, sets metadata, and publishes via
  the Zenodo REST API. Reuse it for any redistributable dataset.
* ``tools/mirror_land_cover.py`` -- the land-cover-specific maintainer tool. It
  downloads ``satellite-land-cover`` from the Copernicus CDS, extracts
  ``lccs_class``, and publishes it to Zenodo under CC-BY-4.0 with the required
  Copernicus attribution baked into the deposition metadata.
* The ``download_land_cover`` build rule, which ``curl``\ s the mirrored file
  from the record pinned by ``config['data']['land_cover']['zenodo_record']``.

**Before mirroring a new dataset**, confirm its licence actually permits
redistribution (CC-BY / CC0 / public domain are safe; "use only" or
non-commercial-no-redistribution terms are not) and record the required
attribution in the deposition metadata.

**Refreshing the land cover mirror** (maintainer, requires a Copernicus CDS
token and a Zenodo token -- see ``config/secrets.yaml.example``)::

    # Optional dry-run against the Zenodo sandbox (leaves an unpublished draft):
    pixi run -e dev python tools/mirror_land_cover.py --sandbox --no-publish

    # First publication (creates a new Zenodo record):
    pixi run -e dev python tools/mirror_land_cover.py

    # New data version (publishes a new version of an existing record):
    pixi run -e dev python tools/mirror_land_cover.py --parent-record <record-id>

The tool prints the published record id; set it as
``config['data']['land_cover']['zenodo_record']`` in ``config/default.yaml`` and
commit that change so builds pick up the new mirror.
