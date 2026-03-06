.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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

**Required manual downloads:**

1. Create an account with IHME and download GBD death rates as described in :ref:`ihme-gbd-mortality`.
2. Download the IHME 2019 relative risk workbook ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX`` (:ref:`ihme-relative-risks`).
3. Download the IHME 2019 dietary risk exposure estimates ``IHME_GBD_2019_DIET_RISK_1990_2019_DATA`` (:ref:`ihme-diet-risk-exposure`).
4. Register at the Global Dietary Database portal and download the dataset, placed locally as the directory ``GDD-dietary-intake`` (:ref:`gdd-dietary-intake`).

**Required API key setup:**

5. Register for a Copernicus Climate Data Store account and configure your API key to enable automatic retrieval of land cover data (:ref:`copernicus-land-cover`).


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

**Usage**: Crop yield and suitability rasters feeding into production potential calculations

USDA Cost and Returns Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United States Department of Agriculture Economic Research Service (USDA ERS)

**Description**: Detailed production cost estimates (USD/acre) for major field crops in the United States, including operating costs and allocated overhead.

**Version**: Individual crop CSV files; 10-year average (2015-2024)

**Coverage**:
  * Spatial: U.S. total (national averages)
  * Temporal: 1996-2024 (varies by crop)
  * Crops: 9 major crops (corn/maize, wheat, rice, barley, oats, sorghum, soybeans, groundnut/peanuts, cotton)

**Access**: https://www.ers.usda.gov/data-products/commodity-costs-and-returns/ (`Data catalog <https://catalog.data.gov/dataset/commodity-costs-and-returns>`_)

**License**: Creative Commons Attribution (CC BY)

**Citation**: U.S. Department of Agriculture, Economic Research Service. Commodity Costs and Returns. https://www.ers.usda.gov/data-products/commodity-costs-and-returns/

**Retrieval**: Direct CSV download via ``workflow/scripts/retrieve_usda_costs.py``. The script implements robust retries (5 attempts with backoff) to handle server instability. URLs are listed in ``data/curated/usda_cost_sources.csv``.

  * **Manual Fallback**: If automated retrieval fails, download the CSVs from the URLs listed in ``data/curated/usda_cost_sources.csv``.

**Usage**: Bottom-up mechanistic estimates of crop production costs per hectare. Costs are split into:

* **Per-year costs** (annual fixed): Machinery depreciation, farm overhead, taxes/insurance
* **Per-planting costs** (variable): Seed, chemicals, labor, fuel, repairs, custom services

Costs explicitly **excluded** (modeled endogenously):

* Fertilizer costs (nitrogen/phosphorus/potassium modeled separately)
* Land opportunity costs (land allocation is optimized)
* Irrigation water costs (water is a separate constraint)

**Inflation adjustment**: All costs are inflation-adjusted to a configurable base year (default: 2024) using US CPI-U data from BLS. See :ref:`bls-cpi-data` for details.

**Note**: USDA data is merged with EU FADN data (see :ref:`fadn-cost-data`) via the ``merge_crop_costs`` rule to provide comprehensive global coverage. For crops without direct cost data from either source, fallback mappings are applied via ``data/curated/crop_cost_fallbacks.yaml`` (e.g., other cereals use wheat costs, other legumes use soybean costs). When data is available from multiple sources, costs are averaged.

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

**Note**: USDA animal cost data is merged with EU FADN livestock data via the ``merge_animal_costs`` rule. For products without direct cost data (chicken, eggs), fallback mappings are applied via ``data/animal_cost_fallbacks.yaml`` using pork costs as a proxy. When data is available from multiple sources, costs are averaged.

.. _bls-cpi-data:

BLS Consumer Price Index (CPI-U)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: U.S. Bureau of Labor Statistics (BLS) |
**Access**: https://api.bls.gov/publicAPI/v2/timeseries/data/ |
**License**: Public domain

Consumer Price Index for All Urban Consumers (CPI-U), U.S. city average, all items (series CUUR0000SA0). Used for inflation adjustment of cost data throughout the workflow. Annual CPI averages are computed from monthly values and stored in ``processing/shared/cpi_annual.csv`` for reuse across the workflow. Retrieved automatically via BLS Public Data API (``workflow/scripts/retrieve_cpi_data.py``). Base year is configured via ``currency_base_year`` in ``config/default.yaml`` (default: 2024).

FAOSTAT Food Balance Sheets (FBS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Per-capita food supply quantities (kg/capita/year) by country, item, and year from FAO's global statistical database covering 245+ countries from 1961 onward. We use the Grand Total item to benchmark available food supply when scaling food waste fractions, and per-commodity supply data to supplement baseline dietary intake estimates.

**Version**: Retrieved via the FAOSTAT API using the ``faostat`` Python client (JSON -> Pandas DataFrame)

**Coverage**:
  * Spatial: 245+ countries and territories
  * Temporal: 1961 onward

**Access**: https://www.fao.org/faostat/en/ (Food Balance Sheets domain)

**License**: CC BY 4.0 + FAO database terms (`Terms of Use <https://www.fao.org/contact-us/terms/db-terms-of-use/en/>`_)

**Citation**: FAO. FAOSTAT Food Balance Sheets. https://www.fao.org/faostat/en/

**Retrieval**: Downloaded as bulk CSVs from FAOSTAT and processed by scripts in ``workflow/scripts/`` (e.g., ``prepare_faostat_gdd_supplements.py``, ``prepare_food_loss_waste.py``).

**Usage**:
  * **Food Waste**: Converts per-capita waste (kg) to fractions relative to available food supply.
  * **Dietary Intake**: Provides baseline consumption data for **dairy**, **poultry**, and **vegetable oils**, supplementing the GDD intake surveys.

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

**Description**: European Union farm-level accounting database providing economic and structural data for agricultural holdings. This project uses the LAMASUS-processed NUTS-level agricultural dataset, which aggregates FADN farm accounting data (crop outputs, production costs, areas) into standard results by country, year, and farm typology.

**Version**: LAMASUS v0.1 (2024); FADN Standard Results (SO classification, 2004-2020)

**Coverage**:
  * Spatial: EU-27 member states
  * Temporal: 2004-2020 (this project uses 2015-2020 for consistency with USDA data)
  * Crops: 10 categories (cereals, protein crops, potatoes, sugar beet, oilseeds, vegetables & flowers, fruit trees, citrus, wine & grapes, olives)

**Access**: https://zenodo.org/records/10939892 (LAMASUS dataset); original FADN at https://agriculture.ec.europa.eu/data-and-analysis/farm-structures-and-economics/fsdn_en

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0) for LAMASUS dataset; original FADN data (C) European Union, free for non-commercial use with attribution

**Citation**: Wogerer, M. (2024). LAMASUS NUTS-level agricultural data derived from public FADN 1989-2009 (SGM) & 2004-2020 (SO) (0.1) [Data set]. Zenodo. https://doi.org/10.5281/zenodo.10939892

**Retrieval**: Automatic via ``download_fadn_data`` Snakemake rule (Zenodo direct download). Output files: ``data/downloads/fadn_nuts0_so.csv`` and ``data/downloads/fadn_variables.xlsx``.

**Cost variables**:

* **Crop-specific costs** (per-planting): Seeds and plants (SE285), crop protection (SE300), other crop-specific costs (SE305)
* **Farm overhead costs** (per-year): Machinery & building costs (SE340), energy (SE345), contract work (SE350), depreciation (SE360), wages paid (SE370), interest paid (SE380)
* **Explicitly EXCLUDED**: Fertilizer costs (SE295 - modeled endogenously), rent paid (SE375 - land opportunity cost modeled endogenously)

**Cost allocation methodology**:

1. **Allocation by Value**: Costs are allocated to crop categories proportionally by output value share.
2. **Normalization by Specific Area**: Allocated costs are normalized using crop-specific area variables (e.g., SE035 for Cereals, SE046 for Vegetables, SE050 for Vineyards) rather than total farm area, ensuring intensive crops receive higher cost-per-hectare.
3. **Group Aggregation**: Crops without specific area variables are grouped into "Other Field Crops" (SE041 area) for a representative group-average.

**Currency and inflation adjustment**:

1. EUR costs are inflation-adjusted to base year (default: 2024) using EU HICP
2. Converted to USD using average EUR/USD exchange rate (1.10 for 2015-2024 period)
3. USD values are further adjusted for US inflation using CPI-U to ensure consistency with USDA cost data

**Usage**: Provides production cost estimates for EU agriculture, complementing USDA data with broader crop coverage (vegetables, fruits, etc.). Costs are merged with USDA data via ``merge_crop_costs`` rule; when both sources have data for a crop, values are averaged. Workflow: ``retrieve_fadn_costs`` script -> ``processing/{name}/fadn_costs.csv`` -> merged with USDA costs -> ``processing/{name}/crop_costs.csv``.

FADN Livestock Costs
^^^^^^^^^^^^^^^^^^^^

The same FADN dataset provides livestock production cost data for animal products:

**Coverage**: 4 livestock categories (dairy cattle, beef cattle, pigs, poultry)

**Cost variables**: Same farm overhead costs as crops (SE340-SE380), plus livestock-specific costs (SE320 - veterinary, breeding, etc.)

**Explicitly EXCLUDED**: Purchased feed costs (SE310 - modeled endogenously), rent paid (SE375)

**Methodology**: A yield-based "bottom-up" approach is used to calculate costs per tonne:

1.  **Allocation**: Farm costs are allocated to livestock categories proportionally by output value, normalized by Total Farm Output (SE131) to properly account for mixed farming systems.
2.  **Unit Cost**: Costs are normalized to **Cost per Head** using Eurostat livestock unit (LU) coefficients (e.g., 1 Dairy Cow = 1 LU, 1 Pig = 0.3 LU).
3.  **Yield Conversion**: Cost per Head is converted to **Cost per Tonne** using country-specific physical yields derived from FAOSTAT (Production / Stocks). This ensures consistent physical metrics across all products and countries, correcting for internal data gaps in FADN reporting.

**Currency adjustment**: Same as crops (EUR -> USD using PPP, inflation-adjusted to base year)

**Usage**: Provides EU livestock production cost estimates, complementing USDA data. Costs are merged with USDA animal cost data via ``merge_animal_costs`` rule; when both sources have data for a product, values are averaged. Workflow: ``retrieve_fadn_animal_costs`` script -> ``processing/{name}/fadn_animal_costs.csv`` -> merged with USDA animal costs -> ``processing/{name}/animal_costs.csv``.

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

Grassland Yield Data (ISIMIP / LPJmL)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ISIMIP (Inter-Sectoral Impact Model Intercomparison Project) |
**Access**: `ISIMIP data portal <https://www.isimip.org/outputdata/>`_ |
**License**: ISIMIP terms

Historical managed grassland yields from LPJmL model (above-ground dry matter production) at 0.5° x 0.5° resolution. Used for grazing-based livestock production potential estimates.

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

**License**: Free for academic/non-commercial use with attribution; redistribution not allowed; commercial use requires permission (`License <https://gadm.org/license.html>`_)

**Citation**: GADM (2024). Global Administrative Areas, version 4.1. https://gadm.org/

**Retrieval**: Automatic via Snakemake rules

**Usage**: Building optimization regions via clustering of ADM_1 (states/provinces)

.. _copernicus-land-cover:

Copernicus Satellite Land Cover
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Copernicus Climate Change Service (C3S)

**Description**: Global land cover classification gridded maps from 1992 to present derived from satellite observations. The dataset describes the land surface into 22 classes including various vegetation types, water bodies, built-up areas, and bare land.

**Version**: v2.1.1 (2016 onwards); NetCDF format via the Copernicus Climate Data Store API

**Coverage**:
  * Spatial: Global (Plate Carree projection), 300 m resolution
  * Temporal: Annual (with approximately one-year publication delay)

**Access**: https://cds.climate.copernicus.eu/datasets/satellite-land-cover (`API documentation <https://cds.climate.copernicus.eu/how-to-api>`_)

**License**: Multiple licenses apply including ESA CCI licence, CC-BY licence, and VITO licence. Users must also cite the Climate Data Store entry and provide attribution to the Copernicus program. (`Terms of use <https://cds.climate.copernicus.eu/terms-of-use>`_)

**Citation**: Copernicus Climate Change Service, Climate Data Store, (2019): Land cover classification gridded maps from 1992 to present derived from satellite observation. Copernicus Climate Change Service (C3S) Climate Data Store (CDS). https://doi.org/10.24381/cds.006f2c9a

**Retrieval**: Automatic via the ``download_land_cover`` and ``extract_land_cover_class`` Snakemake rules. The full dataset (~2.2GB) contains multiple variables but only the land cover classification (``lccs_class``) is needed. The extraction rule outputs ``data/downloads/land_cover_lccs_class.nc`` (~440MB) and deletes the full download.

**Manual setup required**:

1. Register for a free CDS account at https://cds.climate.copernicus.eu/user/register
2. Accept the required dataset licenses at https://cds.climate.copernicus.eu/datasets/satellite-land-cover?tab=download#manage-licences
3. Obtain an API key from your account settings
4. Configure the API key in ``~/.ecmwfdatastoresrc`` or via environment variables (see API documentation for setup instructions)

**Configuration**: The land cover year is derived from the top-level ``baseline_year`` parameter. The version can be configured via ``config['data']['land_cover']['version']`` (default: v2_1_1).

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

**License**: ESA CCI Biomass Terms and Conditions. Public data available to both registered and non-registered users. (`License <https://artefacts.ceda.ac.uk/licences/specific_licences/esacci_biomass_terms_and_conditions_v2.pdf>`_)

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
**Access**: https://www.imf.org/external/datamapper/NGDPDPC@WEO (`API documentation <https://www.imf.org/external/datamapper/api/help>`_) |
**License**: Free to use with attribution (`Terms of use <https://www.imf.org/en/about/copyright-and-terms#data>`_)

GDP per capita estimates (current prices, USD) from the World Economic Outlook database (indicator ``NGDPDPC``). Retrieved automatically via the IMF DataMapper API (no API key required). Output: ``data/downloads/gdp_per_capita.csv``. Used by ``prepare_health_costs`` for multi-objective country clustering based on geography, GDP similarity, and population balance.

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
    - Causes: Ischemic heart disease, Stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
    - Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual age bins)
    - Sex: Both
    - Year: must match ``baseline_year`` in the config (default: 2020)

**Access**: https://vizhub.healthdata.org/gbd-results/

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement; `Terms <https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement>`_)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2024. Available from https://vizhub.healthdata.org/gbd-results/

**Retrieval**: Automatically processed via ``workflow/scripts/prepare_gbd_mortality.py``

**Manual download steps**:

1. Visit https://vizhub.healthdata.org/gbd-results/ and sign in with your IHME account.
2. Reproduce the query parameters above. This permanent link is configured for year 2020: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/f4c7511d159798f5b8864bc83fa06451 -- adjust the year if using a different ``baseline_year``.
3. Export the results as CSV (allow some time for the IHME to process the query) and save to ``data/manually_downloaded`` as ``IHME-GBD_2023-death-rates-{year}.csv`` where ``{year}`` matches your ``baseline_year``. Consider checking the file modification time and potentially resetting it (on Linux, run ``touch`` on the file); sometimes the modification time of the downloaded file can be in the future, which confuses Snakemake.

.. _ihme-relative-risks:

IHME GBD 2019 -- Relative Risk Curves
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Appendix Table 7a from the Global Burden of Disease Study 2019, listing relative risks by dietary risk factor, outcome, age, and exposure level.

**Version**: GBD 2019; XLSX workbook

**Access**: https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Results. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2020.

**Retrieval**: Automatically processed via ``workflow/scripts/prepare_relative_risks.py``

**Manual download steps**:

1. Navigate to https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks.
2. Under the Files tab, locate and download the "Relative risks: all risk factors except for ambient air pollution, alcohol, smoking, and temperature [XLSX]" file; it will be named ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX``. Log in to your IHME account when requested.
3. Place the downloaded file under ``data/manually_downloaded``; no need to rename.

.. _ihme-diet-risk-exposure:

IHME GBD 2019 -- Dietary Risk Exposure Estimates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Country-level dietary risk exposure estimates from the Global Burden of Disease Study 2019, covering 15 dietary risk factors with mean exposure, uncertainty, and summary exposure values (SEVs) by country, age, sex, and year. Supplements the Global Dietary Database (GDD) baseline dietary intake data.

**Version**: GBD 2019; ZIP archive containing per-risk-factor CSVs (~47 MB each)

**Coverage**:
  * Spatial: 204 countries and territories
  * Temporal: 1990-2019, by age group and sex
  * Risk factors: Calcium, fiber, fruit, legumes, milk, nuts, omega-3, processed meat, PUFA, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains

**Access**: https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement; `Terms <https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement>`_)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Dietary Risk Exposure Estimates 1990-2019. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2021.

**Retrieval**: Not yet integrated into the Snakemake workflow. Data is available under ``data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA/``.

**Manual download steps**:

1. Navigate to https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019.
2. Log in to your IHME account.
3. Download ``IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip`` (direct link: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip).
4. Extract the ZIP file and place the resulting directory as ``data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA``.

DIA Health Impact Inputs (Diet Impact Assessment)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: WHO-DIA (Marco Springmann) |
**Access**: https://github.com/marco-spr/WHO-DIA |
**License**: GPL-3.0

Epidemiological inputs used by the Diet Impact Assessment (DIA) model to translate dietary exposures into health burdens. We copy a minimal subset covering dietary risk relative-risk schedules, baseline consumption, mortality, demographic structure, and regional values of a statistical life year. CSV snapshots dated 2021-05-28 (diet, risk schedules, demographics) and 2021-10-18 (VSL region table).

.. _gdd-dietary-intake:

Global Dietary Database (GDD)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Tufts University Friedman School of Nutrition Science and Policy

**Description**: Country-level estimates of dietary intake for major food groups and dietary risk factors based on systematic review and meta-analysis of national dietary surveys.

**Version**: Downloaded as CSV (~1.6 GB); coverage circa 2015-2020 depending on country survey availability

**Coverage**:
  * Spatial: 185+ countries
  * Content: Mean daily intake (g/day per capita) for major food groups including vegetables, fruits, whole grains, legumes, nuts & seeds, red meat, processed meat, and seafood, with uncertainty estimates

**Access**: https://globaldietarydatabase.org/data-download

**License**: Free for non-commercial research, teaching, and private study with attribution. Data may not be redistributed or used commercially without Tufts permission. (`Terms and conditions <https://globaldietarydatabase.org/terms-and-conditions-use>`_)

**Citation**: Global Dietary Database. Dietary intake data by country. https://www.globaldietarydatabase.org/

**Retrieval**: Automatically processed via ``workflow/scripts/prepare_gdd_dietary_intake.py``

**Manual download steps**:

1. Create or sign in to a Global Dietary Database account at https://globaldietarydatabase.org/data-download.
2. When you are signed in, navigate back to the download page, accept the terms and proceed to download the GDD dataset, which will be ~1.6GB zip file.
3. Extract the zip file; you will get a directory named ``GDD_FinalEstimates_01102022``
4. Move this directory to ``data/manually_downloaded`` and rename the directory to ``GDD-dietary-intake``.

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
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

**Access**: https://fdc.nal.usda.gov/ (web interface) or via REST API (`API documentation <https://fdc.nal.usda.gov/api-guide.html>`_)

**License**: Public domain under CC0 1.0 Universal (CC0 1.0). No permission needed for use, but USDA requests attribution.

**Citation**: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. https://fdc.nal.usda.gov/

**Retrieval**: Optional via ``retrieve_usda_nutrition`` rule (using the API with included API key). Set ``data.usda.retrieve_nutrition: true`` in config to fetch fresh data. By default, the repository includes pre-fetched data in ``data/curated/nutrition.csv``.

**API Key**: The repository includes a shared API key for convenience. Users can optionally obtain their own API key (free, instant signup) at https://fdc.nal.usda.gov/api-key-signup and update the ``data.usda.api_key`` value in the config.

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

Mock and Placeholder Data
--------------------------

Several CSV files in ``data/`` currently contain **mock placeholder values** and must be replaced with sourced data before publication-quality analysis:

data/feed_conversion.csv
~~~~~~~~~~~~~~~~~~~~~~~~~

**Status**: Mock data

**Description**: Crop nutrient content for animal feed

data/feed_to_animal_products.csv
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Status**: Mock data

**Description**: Feed-to-product conversion ratios for livestock

Data License Summary
--------------------

Most datasets used in this project require attribution. Some disallow redistribution, meaning that ``food-opt`` cannot be distributed together with these datasets. Some furthermore prohibit commercial use without prior agreement or a paid-for license.

* **CC0 1.0 (Public Domain)** (USDA FoodData Central, IFA FUBC): Public domain, no restrictions; attribution requested
* **CC BY 4.0** (GAEZ, FAOSTAT, GLEAM 3.0, SoilGrids, Cook-Patton, LUIcube, ESA Biomass CCI, FADN/LAMASUS): Requires attribution
* **CC BY 3.0 IGO** (UN WPP): Requires attribution to UN
* **Academic use only** (GADM, GBD, GDD): Commercial use requires permission or paid license
* **Public domain** (BLS CPI-U): No restrictions
* **GPL-3.0** (DIA Health Inputs): Copyleft; derived works must use same license
