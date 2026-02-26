.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Data Sources
============

Overview
--------

The model integrates multiple global datasets covering agricultural production, climate, population, health, and water resources. This page documents the key datasets, their licenses, and how to obtain them.

For comprehensive documentation of all datasets, see ``data/DATASETS.md`` in the repository.

.. _manual-download-checklist:

Manual Download Checklist
~~~~~~~~~~~~~~~~~~~~~~~~~

Several licensed datasets cannot be fetched automatically. While their use is free for non-commercial research purposes, these have to be downloaded manually or require API key registration.

**Required manual downloads:**

1. Create an account with IHME and download ``IHME-GBD_2023-dealth-rates.csv`` as described in :ref:`ihme-gbd-mortality`.
2. Download the IHME 2019 relative risk workbook ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX`` (:ref:`ihme-relative-risks`).
3. Download the IHME 2019 dietary risk exposure estimates ``IHME_GBD_2019_DIET_RISK_1990_2019_DATA`` (:ref:`ihme-diet-risk-exposure`).
4. Register at the Global Dietary Database portal and download the dataset, placed locally as the directory ``GDD-dietary-intake`` (:ref:`gdd-dietary-intake`).

**Required API key setup:**

5. Register for a Copernicus Climate Data Store account and configure your API key to enable automatic retrieval of land cover data (:ref:`copernicus-land-cover`).


Agricultural Production Data
----------------------------

GAEZ (Global Agro-Ecological Zones) v5
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO/IIASA

**Description**: Global crop suitability and attainable yield estimates under various climate and management scenarios.

**Resolution**: 0.083333° × 0.083333° (~5 arc-minute grid, ≈9 km at the equator)

**Access**: https://data.apps.fao.org/gaez/; bulk downloads through a Google Cloud Storage interface.

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0) + FAO database terms

**Citation**: FAO/IIASA (2025). Global Agro-Ecological Zones v5 (GAEZ v5).

**Workflow retrieval**: Automatic via Snakemake rules in ``workflow/rules/retrieve.smk``

USDA Cost and Returns Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United States Department of Agriculture Economic Research Service (USDA ERS)

**Description**: Detailed production cost estimates (USD/acre) for major field crops in the United States, including operating costs and allocated overhead.

**Temporal coverage**: 1996-2024 (varies by crop)

**Access**: https://www.ers.usda.gov/data-products/commodity-costs-and-returns/

**License**: Creative Commons Attribution (CC BY)

**Retrieval**: Direct CSV download via ``workflow/scripts/retrieve_usda_costs.py``. The script implements robust retries (5 attempts with backoff) to handle server instability.

  * **Manual Fallback**: If automated retrieval fails, download the CSVs from the URLs listed in ``data/curated/usda_cost_sources.csv``.

**Coverage**: 9 major crops (corn/maize, wheat, rice, barley, oats, sorghum, soybeans, groundnut/peanuts, cotton)

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

**Temporal coverage**: Varies by product (milk: 2005-present, cow-calf: 2008-present, hogs: 2010-present)

**Access**:
  * Milk: https://www.ers.usda.gov/data-products/milk-cost-of-production-estimates/
  * Commodity Costs and Returns (includes cow-calf, hogs): https://www.ers.usda.gov/data-products/commodity-costs-and-returns/

**License**: Creative Commons Attribution (CC BY)

**Retrieval**: Excel download and processing via ``workflow/scripts/retrieve_usda_animal_costs.py``. The script implements robust retries (5 attempts with backoff) to handle intermittent server timeouts.

  * **Manual Fallback**: If automated retrieval fails, users can manually download the CSVs from the URLs listed in ``data/curated/usda_animal_cost_sources.csv`` and place them in the processing directory, or simply re-run the workflow as the server often recovers.

**Coverage**: 3 major animal products (dairy/milk, beef cattle via cow-calf, pork via hogs)

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

**Provider**: U.S. Bureau of Labor Statistics (BLS)

**Description**: Consumer Price Index for All Urban Consumers (CPI-U), U.S. city average, all items. Used for inflation adjustment of cost data.

**Series**: CUUR0000SA0

**Temporal coverage**: 1913-present (monthly data)

**Access**: https://api.bls.gov/publicAPI/v2/timeseries/data/

**License**: Public domain.

**Retrieval**: Automatic via BLS Public Data API (``workflow/scripts/retrieve_cpi_data.py``)

**Usage**: Convert nominal USD values from different years to real USD in a common base year. Annual CPI averages are computed from monthly values and stored in ``processing/shared/cpi_annual.csv`` for reuse across the workflow.

**Configuration**: Base year for currency values is set in ``config/default.yaml`` as ``currency_base_year`` (default: 2024).

FAOSTAT Food Balance Sheets (FBS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Per-capita food supply quantities (kg/capita/year) by country, item, and year. We use the Grand Total item to benchmark available food supply when scaling food waste fractions.

**Access**: https://www.fao.org/faostat/en/ (Food Balance Sheets domain)

**License**: CC BY 4.0 + FAO database terms

**Retrieval**: Downloaded as bulk CSVs from FAOSTAT and processed by scripts in ``workflow/scripts/`` (e.g., ``prepare_faostat_gdd_supplements.py``, ``prepare_food_loss_waste.py``).

**Usage**:
  * **Food Waste**: Converts per-capita waste (kg) to fractions relative to available food supply.
  * **Dietary Intake**: Provides baseline consumption data for **dairy**, **poultry**, and **vegetable oils**, supplementing the GDD intake surveys.

UNSD SDG Indicator 12.3.1 (Food Loss & Waste)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United Nations Statistics Division (UNSD)

**Description**: SDG indicator database series ``AG_FLS_PCT`` (Food loss percentage) and ``AG_FOOD_WST_PC`` (Food waste per capita) covering SDG 12.3.1a/b.

**Access**: https://unstats.un.org/sdgs/dataportal; bulk archive at https://unstats.un.org/sdgs/indicators/database/archive

**License**: UNdata terms — data may be copied and redistributed free of charge provided UNdata/UNSD is cited ("All data and metadata provided on UNdata's website are available free of charge and may be copied freely, duplicated and further distributed provided that UNdata is cited as the reference.").

**Retrieval**: Downloaded as a bulk CSV archive from the UNSD SDG Indicators Database (2025 Q4.1 release, ~56 MB zip). During extraction, the CSV is filtered to retain only the two relevant series (``AG_FLS_PCT`` and ``AG_FOOD_WST_PC``, ~500 KB). ``workflow/scripts/prepare_food_loss_waste.py`` reads the filtered CSV and falls back to global product shares to derive food group–specific loss factors where regional detail is missing.

**Usage**: Supplies per-country loss and waste fractions for food groups, injected into the crop→food conversion efficiencies during ``build_model``.

IFA FUBC — Global Fertilizer Use by Crop and Country
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: International Fertilizer Association (IFA) / Dryad

**Description**: Global dataset on inorganic fertilizer application rates (N, P₂O₅, K₂O) by crop and country based on expert surveys. The dataset includes historical data from 8 previous reports (1986–2014/15) and the most recent survey for the 2017–18 period, covering fertilizer application rates (kg/ha) and total consumption (thousand tonnes) for major crops worldwide.

**Source**: https://datadryad.org/stash/dataset/doi:10.5061/dryad.2rbnzs7qh

**Version**: Version 1 (March 2025)

**Coverage**:
  * Temporal: 2017–18 period for latest survey, with historical data from 1986 onwards
  * Geographic: Global, covering countries with significant fertilizer use
  * Crops: Major crops including cereals, oilseeds, roots & tubers, vegetables, fruits, fiber crops, sugar crops, and others

**License**: Creative Commons Zero v1.0 Universal (CC0 1.0). Data is in the public domain and may be used without restriction.

  * License: https://creativecommons.org/publicdomain/zero/1.0/

**Citation**: Ludemann, C., Gruere, A., Heffer, P., & Dobermann, A. (2025). Global data on fertilizer use by crop and by country [Dataset]. Dryad. https://doi.org/10.5061/dryad.2rbnzs7qh

**Data files**:
  * ``FUBC_1_to_9_data.csv``: Main dataset with fertilizer application rates and quantities by crop, country, and year
  * ``Meta_data_FUBC_1_to_9_data.csv``: Column descriptions and metadata

**Key variables**:
  * Country, ISO3 code, Year, FUBC report number
  * Crop name and crop area (thousand hectares)
  * N, P₂O₅, K₂O quantities (thousand tonnes)
  * N, P₂O₅, K₂O application rates (kg/ha)
  * Average application rates by nutrient

**Usage**: Crop-specific fertilizer application rates for N₂O emissions modeling and nutrient budget analysis

**Workflow integration**: Bundled with the repository under ``data/bundled/doi_10_5061_dryad_2rbnzs7qh__v20250311/`` (CC0 licensed). The full Dryad dataset is included as-is due to Dryad API access restrictions.

.. _fadn-cost-data:

FADN — Farm Accountancy Data Network (EU)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: European Commission DG Agriculture and Rural Development / LAMASUS project (Zenodo)

**Description**: European Union farm-level accounting database providing economic and structural data for agricultural holdings. This project uses the LAMASUS-processed NUTS-level agricultural dataset, which aggregates FADN farm accounting data (crop outputs, production costs, areas) into standard results by country, year, and farm typology.

**Temporal coverage**: 2004-2020 (this project uses 2015-2020 for consistency with USDA data)

**Geographic coverage**: EU-27 member states

**Access**: https://zenodo.org/records/10939892 (LAMASUS dataset)

**Original FADN**: https://agriculture.ec.europa.eu/data-and-analysis/farm-structures-and-economics/fsdn_en

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0) for LAMASUS dataset; original FADN data © European Union, free for non-commercial use with attribution

**Retrieval**: Automatic via ``download_fadn_data`` Snakemake rule (Zenodo direct download)

**Coverage**: 10 crop categories (cereals, protein crops, potatoes, sugar beet, oilseeds, vegetables & flowers, fruit trees, citrus, wine & grapes, olives)

**Cost variables**:

* **Crop-specific costs** (per-planting): Seeds and plants (SE285), crop protection (SE300), other crop-specific costs (SE305)
* **Farm overhead costs** (per-year): Machinery & building costs (SE340), energy (SE345), contract work (SE350), depreciation (SE360), wages paid (SE370), interest paid (SE380)
* **Explicitly EXCLUDED**: Fertilizer costs (SE295 - modeled endogenously), rent paid (SE375 - land opportunity cost modeled endogenously)

**Cost allocation methodology**:

1. **Allocation by Value**: Costs are first allocated to crop categories proportionally by output value share (e.g., if cereals represent 30% of total farm output value, they receive 30% of total costs).
2. **Normalization by Specific Area**: To correctly reflect production intensity, the allocated costs are normalized using **crop-specific area variables** (e.g., SE035 for Cereals, SE046 for Vegetables, SE050 for Vineyards) rather than total farm area. This ensures that intensive crops like vegetables receive a higher cost-per-hectare than extensive crops like cereals, even within the same aggregated farm data.
3. **Group Aggregation**: Crops without specific area variables (e.g., potatoes, sugar beet, oilseeds) are grouped into the "Other Field Crops" category (SE041 area) to calculate a representative group-average cost per hectare.

**Currency and inflation adjustment**:

1. EUR costs are inflation-adjusted to base year (default: 2024) using EU HICP (Harmonized Index of Consumer Prices)
2. Converted to USD using average EUR/USD exchange rate (1.10 for 2015-2024 period)
3. USD values are further adjusted for US inflation using CPI-U to ensure consistency with USDA cost data

**Usage**: Provides production cost estimates for EU agriculture, complementing USDA data with broader crop coverage (vegetables, fruits, etc.). Costs are merged with USDA data via ``merge_crop_costs`` rule; when both sources have data for a crop, values are averaged.

**Workflow integration**: Processed via ``retrieve_fadn_costs`` script → ``processing/{name}/fadn_costs.csv`` → merged with USDA costs → ``processing/{name}/crop_costs.csv`` (final output used by ``build_model``).

**Citation**: Wögerer, M. (2024). LAMASUS NUTS-level agricultural data derived from public FADN 1989-2009 (SGM) & 2004-2020 (SO) (0.1) [Data set]. Zenodo. https://doi.org/10.5281/zenodo.10939892

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

**Currency adjustment**: Same as crops (EUR → USD using PPP, inflation-adjusted to base year)

**Usage**: Provides EU livestock production cost estimates, complementing USDA data. Costs are merged with USDA animal cost data via ``merge_animal_costs`` rule; when both sources have data for a product, values are averaged.

**Workflow integration**: Processed via ``retrieve_fadn_animal_costs`` script → ``processing/{name}/fadn_animal_costs.csv`` → merged with USDA animal costs → ``processing/{name}/animal_costs.csv`` (final output used by ``build_model``).

Grassland Yield Data
~~~~~~~~~~~~~~~~~~~~

**Provider**: ISIMIP (Inter-Sectoral Impact Model Intercomparison Project)

**Description**: Historical managed grassland yields from LPJmL model (above-ground dry matter production).

**Resolution**: 0.5° × 0.5°

**Access**: ISIMIP data portal

**Usage**: Grazing-based livestock production potential

Spatial and Administrative Data
--------------------------------

GADM (Global Administrative Areas) v4.1
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: GADM project

**Description**: Global administrative boundary polygons (ADM_0 to ADM_5 levels).

**Format**: GeoPackage with multiple layers

**Access**: https://gadm.org/

**License**: Free for academic/non-commercial use with attribution; redistribution not allowed; commercial use requires permission

**Citation**: GADM (2024). Global Administrative Areas, version 4.1. https://gadm.org/

**Usage**: Building optimization regions via clustering of ADM_1 (states/provinces)

.. _copernicus-land-cover:

Copernicus Satellite Land Cover
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Copernicus Climate Change Service (C3S)

**Description**: Global land cover classification gridded maps from 1992 to present derived from satellite observations. The dataset describes the land surface into 22 classes including various vegetation types, water bodies, built-up areas, and bare land.

**Resolution**: 300 m spatial resolution; annual temporal resolution (with approximately one-year publication delay)

**Coverage**: Global (Plate Carrée projection)

**Access**: https://cds.climate.copernicus.eu/datasets/satellite-land-cover

**API Documentation**: https://cds.climate.copernicus.eu/how-to-api

**Version**: v2.1.1 (2016 onwards)

**License**: Multiple licenses apply including ESA CCI licence, CC-BY licence, and VITO licence. Users must also cite the Climate Data Store entry and provide attribution to the Copernicus program.

**Citation**: Copernicus Climate Change Service, Climate Data Store, (2019): Land cover classification gridded maps from 1992 to present derived from satellite observation. Copernicus Climate Change Service (C3S) Climate Data Store (CDS). DOI: 10.24381/cds.006f2c9a

**Usage**: Spatial analysis of agricultural land availability and land use constraints

**Workflow retrieval**: Automatic via the ``download_land_cover`` and ``extract_land_cover_class`` Snakemake rules. The full dataset (~2.2GB) contains multiple variables (lccs_class, processed_flag, current_pixel_state, observation_count, change_count), but only the land cover classification (``lccs_class``) is needed for the model. The extraction rule automatically extracts just this variable to ``data/downloads/land_cover_lccs_class.nc`` (~440MB) and the full download is automatically deleted to save disk space

**Manual setup required**:

1. Register for a free CDS account at https://cds.climate.copernicus.eu/user/register
2. Accept the required dataset licenses at https://cds.climate.copernicus.eu/datasets/satellite-land-cover?tab=download#manage-licences
3. Obtain an API key from your account settings
4. Configure the API key in ``~/.ecmwfdatastoresrc`` or via environment variables (see API documentation for setup instructions)

**Configuration**: Year and version can be configured via ``config['data']['land_cover']['year']`` and ``config['data']['land_cover']['version']`` (defaults: year 2022, version v2_1_1)

.. _esa-biomass-cci:

ESA Biomass CCI — Global Above-Ground Biomass
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ESA Climate Change Initiative (Biomass_cci), NERC EDS Centre for Environmental Data Analysis (CEDA)

**Description**: Global forest above-ground biomass (AGB) maps derived from satellite observations (Sentinel-1 SAR, Envisat ASAR, ALOS PALSAR). The dataset provides annual AGB estimates in tonnes per hectare, along with per-pixel uncertainty estimates and change maps between consecutive years.

**Resolution**: 10 km (10,000 m) spatial resolution; annual temporal resolution

**Coverage**: Global (90°N to 90°S, 180°W to 180°E); years 2007, 2010, 2015-2022

**Version**: v6.0 (released April 2025)

**Access**: https://catalogue.ceda.ac.uk/uuid/95913ffb6467447ca72c4e9d8cf30501

**License**: ESA CCI Biomass Terms and Conditions. Public data available to both registered and non-registered users. Must cite dataset correctly.

  * License: https://artefacts.ceda.ac.uk/licences/specific_licences/esacci_biomass_terms_and_conditions_v2.pdf

**Citation**: Santoro, M.; Cartus, O. (2025): ESA Biomass Climate Change Initiative (Biomass_cci): Global datasets of forest above-ground biomass for the years 2007, 2010, 2015, 2016, 2017, 2018, 2019, 2020, 2021 and 2022, v6.0. NERC EDS Centre for Environmental Data Analysis. DOI: 10.5285/95913ffb6467447ca72c4e9d8cf30501

**Variables**: Above-ground biomass (tons/ha), per-pixel uncertainty (standard deviation), AGB change maps

**Usage**: Analysis of carbon storage potential and forest biomass constraints on land use

**Workflow retrieval**: Automatic via the ``download_biomass_cci`` Snakemake rule using curl. The file downloads directly to ``data/downloads/esa_biomass_cci_v6_0.nc``.

.. _soilgrids-soc:

ISRIC SoilGrids — Global Soil Organic Carbon Stock
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: ISRIC - World Soil Information

**Description**: Global soil organic carbon (SOC) stock predictions for 0-30 cm depth interval based on digital soil mapping using Quantile Random Forest. The dataset provides mean predictions along with quantile estimates (5th, 50th, 95th percentiles) and uncertainty layers derived from the global compilation of soil ground observations (WoSIS).

**Resolution**: Native 250 m; this project retrieves data at configurable resolution (default: 10 km) via WCS scaling

**Coverage**: Global (-180° to 180°, -56° to 84°); Interrupted Goode Homolosine projection (EPSG:152160)

**Temporal coverage**: Based on data from April 1905 to July 2016

**Version**: SoilGrids250m 2.0 (v2.0)

**Access**:

  * Website: https://www.isric.org/explore/soilgrids
  * Data catalogue: https://data.isric.org/geonetwork/srv/api/records/713396f4-1687-11ea-a7c0-a0481ca9e724
  * FAQ: https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

  * License: https://creativecommons.org/licenses/by/4.0/

**Citation**: Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B., Ribeiro, E., & Rossiter, D. (2021). SoilGrids 2.0: producing soil information for the globe with quantified spatial uncertainty. *SOIL*, 7(1), 217–240. https://doi.org/10.5194/soil-7-217-2021

**Units**: Tonnes per hectare (t/ha) for 0-30 cm depth interval

**Variables**: Mean organic carbon stock (``ocs_0-30cm_mean``), 5th/50th/95th percentile estimates, uncertainty (standard deviation)

**Usage**: Soil carbon baseline for carbon sequestration analysis and land use constraints

**Workflow retrieval**: Automatic via the ``download_soilgrids_ocs`` Snakemake rule using ISRIC's Web Coverage Service (WCS). The script downloads global mean soil carbon stock at the resolution specified by ``config['data']['soilgrids']['target_resolution_m']`` (default: 10000m = 10km). Output file: ``data/downloads/soilgrids_ocs_0-30cm_mean.tif`` (~1.2 MB at 10km resolution). No registration or API key required.

**Configuration**: Target resolution can be configured via ``config['data']['soilgrids']['target_resolution_m']`` (default: 10000 meters = 10 km)

.. _cook-patton-regrowth:

Cook-Patton & Griscom — Forest Carbon Accumulation Potential
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Global Forest Watch / The Nature Conservancy / World Resources Institute

**Description**: Global map of carbon accumulation potential from natural forest regrowth in forest and savanna biomes. The dataset estimates the rate at which carbon could be sequestered in aboveground and belowground (root) live biomass during the first thirty years of natural forest regrowth, regardless of current land cover or potential for reforestation. Based on a compilation of 13,112 georeferenced measurements combined with 66 environmental covariate layers in a machine learning model (random forest).

**Resolution**: Native 1 km (1000 m); this project retrieves data at 1 km and resamples to configurable resolution (default: 10 km) using GDAL with average resampling

**Coverage**: Global; all forest and savanna biomes (approximately 16% of global land pixels have valid data)

**Projection**: ESRI:54034 (World Cylindrical Equal Area)

**Units**: Megagrams (Mg) of carbon per hectare per year (Mg C/ha/yr) for the first 30 years of natural regrowth

**Access**: https://data.globalforestwatch.org/documents/f950ea7878e143258a495daddea90cc0

**Source publication**: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., ... & Griscom, B. W. (2020). Mapping carbon accumulation potential from global natural forest regrowth. *Nature*, 585(7826), 545-550.

  * DOI: https://doi.org/10.1038/s41586-020-2686-x

**Methodology**: Machine learning model (random forest) trained on 13,112 field measurements from published literature and national forest inventories combined with 66 climate, soil, and land-use covariates to predict carbon accumulation rates globally

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., Briggs, R. D., Chazdon, R. L., Crowther, T. W., Ellis, P. W., Griscom, H. P., Herrmann, V., Holl, K. D., Houghton, R. A., Larrosa, C., Lomax, G., Lucas, R., Madsen, P., Malhi, Y., ... Griscom, B. W. (2020). Mapping carbon accumulation potential from global natural forest regrowth. *Nature*, 585(7826), 545-550. https://doi.org/10.1038/s41586-020-2686-x

**Variables**: Total carbon sequestration rate (aboveground + belowground/root biomass) from natural forest regrowth

**Usage**: Estimating carbon sequestration potential from natural forest restoration and regrowth across all forest and savanna biomes

**Workflow retrieval**: Automatic via the ``download_forest_carbon_accumulation_1km`` rule followed by ``resample_regrowth``. The native 1 km GeoTIFF (~610 MB) is downloaded with curl (stored as a temporary file), then resampled with a rasterio-based script using average aggregation onto the model's 1/12° resource grid. Final output: ``processing/shared/luc/regrowth_resampled.nc`` (compressed NetCDF, ~12 MB shared across scenarios). No registration or API key required.

Population Data
---------------

UN World Population Prospects (WPP) 2024
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: UN DESA Population Division

**Description**: Official UN population estimates and projections by country, age, and sex.

**Variant**: Medium variant projection

**Access**: https://population.un.org/wpp/

**License**: Creative Commons Attribution 3.0 IGO (CC BY 3.0 IGO)

**Files used**:
  * ``WPP2024_TotalPopulationBySex.csv.gz``
  * ``WPP2024_Life_Table_Abridged_Medium_2024-2100.csv.gz``

**Usage**:
  * Scaling per-capita dietary requirements to total demand
  * Age-structured population for health burden calculations
  * Global life expectancy schedule for health loss valuation

Health and Epidemiology Data
-----------------------------

.. _ihme-gbd-mortality:

IHME GBD 2023 — Mortality Rates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Cause-specific mortality rates by country, age, and sex from the Global Burden of Disease Study 2021. Used to calculate baseline disease burden attributable to dietary risk factors.

**Query parameters**:
  * Measure: Deaths (Rate per 100,000 population)
  * Causes: Ischemic heart disease, Stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
  * Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual age bins)
  * Sex: Both
  * Year: 2023

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2024. Available from https://vizhub.healthdata.org/gbd-results/

**Workflow integration**: Automatically processed via ``workflow/scripts/prepare_gbd_mortality.py``

**Manual download steps**:

1. Visit https://vizhub.healthdata.org/gbd-results/ and sign in with your IHME account.
2. Reproduce the query parameters above by following this permanent link: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/05de3cfb56eafc99f2cc8e135644b81f
3. Export the results as CSV (allow some time for the IHME to process the query) and save to ``data/manually_downloaded``. Rename the file to ``IHME-GBD_2023-dealth-rates.csv`` to match the name expected by the Snakemake workflow. Consider checking the file modification time and potentially resetting it (on Linux, run `touch` on the file); sometimes the modification time of the downloaded time can be in the future, which confuses snakemake.

.. _ihme-relative-risks:

IHME GBD 2019 — Relative Risk Curves
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Appendix Table 7a from the Global Burden of Disease Study 2019, listing relative risks by dietary risk factor, outcome, age, and exposure level.

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Results. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2020.

**Workflow integration**: Automatically processed via ``workflow/scripts/prepare_relative_risks.py``

**Manual download steps**:

1. Navigate to https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks.
2. Under the Files tab, locate and download the "Relative risks: all risk factors except for ambient air pollution, alcohol, smoking, and temperature [XLSX]" file; it will be named ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX``. Log in to your IHME account when requested.
3. Place the downloaded file under ``data/manually_downloaded``; no need to rename.

.. _ihme-diet-risk-exposure:

IHME GBD 2019 — Dietary Risk Exposure Estimates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Country-level dietary risk exposure estimates from the Global Burden of Disease Study 2019, covering 15 dietary risk factors with mean exposure, uncertainty, and summary exposure values (SEVs) by country, age, sex, and year. Supplements the Global Dietary Database (GDD) baseline dietary intake data.

**Risk factors**: Calcium, fiber, fruit, legumes, milk, nuts, omega-3, processed meat, PUFA, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains

**Coverage**: 204 countries and territories, 1990–2019, by age group and sex

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Dietary Risk Exposure Estimates 1990-2019. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2021.

**Workflow integration**: Not yet integrated into the Snakemake workflow. Data is available under ``data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA/``.

**Manual download steps**:

1. Navigate to https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019.
2. Log in to your IHME account.
3. Download ``IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip`` (direct link: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip).
4. Extract the ZIP file and place the resulting directory as ``data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA``.

.. _gdd-dietary-intake:

Global Dietary Database (GDD)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Tufts University Friedman School of Nutrition Science and Policy

**Description**: Country-level estimates of dietary intake for major food groups and dietary risk factors based on systematic review and meta-analysis of national dietary surveys.

**License**: Free for non-commercial research, teaching, and private study with attribution. Data may not be redistributed or used commercially without Tufts permission.

**Citation**: Global Dietary Database. Dietary intake data by country. https://www.globaldietarydatabase.org/ [Accessed YYYY-MM-DD].

**Workflow integration**: Automatically processed via ``workflow/scripts/prepare_gdd_dietary_intake.py``

**Manual download steps**:

1. Create or sign in to a Global Dietary Database account at https://globaldietarydatabase.org/data-download.
2. When you are signed in, navigate back to the download page, accept the terms and proceed to download the GDD dataset, which will be ~1.6GB zip file.
3. Extract the zip file; you will get a directory named ``GDD_FinalEstimates_01102022``
4. Move this directory to ``data/manually_downloaded`` and rename the directory to ``GDD-dietary-intake``.

Water Resources Data
--------------------

Water Footprint Network — Monthly Blue Water Availability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Water Footprint Network (Hoekstra & Mekonnen)

**Description**: Monthly blue water availability for 405 GRDC river basins.

**Format**: Shapefile + Excel workbook

**Access**: https://www.waterfootprint.org/resources/appendix/Report53_Appendix.zip

**License**: No explicit license; citation requested (see below)

**Citation**: Hoekstra, A.Y. and Mekonnen, M.M. (2011). *Global water scarcity: monthly blue water footprint compared to blue water availability for the world's major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, Netherlands.

**Usage**: Constraining irrigated crop production by basin-level water availability

Huang et al. — Gridded Irrigation Water Withdrawals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Huang et al. (2018), HESS

**Description**: Monthly gridded irrigation water withdrawals (0.5 degree resolution) for 1971-2010. Used to represent current agricultural water use in validation scenarios.

**Format**: NetCDF (7z bundle)

**Access**: https://zenodo.org/records/1209296

**License**: Open access; citation requested (see below)

**Citation**: Huang, Z., Hejazi, M., Li, X., Tang, Q., Vernon, C., Leng, G., Liu, Y., Doll, P., Eisner, S., Gerten, D., Hanasaki, N., and Wada, Y. (2018). Reconstruction of global gridded monthly sectoral water withdrawals for 1971-2010 and analysis of their spatiotemporal patterns. *Hydrology and Earth System Sciences*, 22, 2117-2133.

**Usage**: Aggregated to regions by ``workflow/scripts/process_huang_irrigation_water.py`` when ``config.water.supply_scenario`` is ``current_use``.

Food Processing Data
--------------------

data/curated/foods.csv — Crop-to-Food Processing Pathways
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Type**: Hand-written configuration file (maintained in repository)

**Description**: Defines processing pathways that convert raw crops into food products. Each pathway can produce multiple co-products (e.g., wheat → white flour + bran + germ), with conversion factors maintaining mass balance constraints.

**Format**: CSV with pathway-based structure

**Columns**:
  * ``pathway``: Unique identifier for the processing pathway
  * ``crop``: Input crop name (must match config crops list)
  * ``food``: Output food product name
  * ``factor``: Conversion factor (mass of food per unit mass of crop input)
  * ``description``: Source reference and explanation

**Key features**:
  * **Multi-output pathways**: Multiple rows with the same pathway ID represent co-products from a single processing operation
  * **Alternative pathways**: Different pathways for the same crop (e.g., white flour vs. wholemeal flour) let the model choose optimal processing routes
  * **Mass balance**: Sum of conversion factors per pathway must be ≤ 1.0, with remainder representing unavoidable losses
  * **Validation**: Model validates mass balance constraints when building the network

**Primary source**: FAO Nutrient Conversion Table for Supply Utilization Accounts (2024), sheet 03. Additional factors from literature for specific crops.

**License**: Data in this file is derived from FAO SUA 2024 (© FAO 2024, non-commercial use with attribution) and other cited sources. The pathway structure and organization is original to this project.

**Usage**: ``workflow/scripts/build_model.py`` reads this file and creates multi-output PyPSA Links for each pathway, with efficiencies adjusted for country-specific food loss and waste factors.

**Maintenance**: This is a hand-written configuration file that users should review and potentially customize for their analysis. When adding new crops or food products, corresponding pathways must be added to this file with appropriate conversion factors and source citations.

Nutritional Data
----------------

USDA FoodData Central
~~~~~~~~~~~~~~~~~~~~~

**Provider**: U.S. Department of Agriculture, Agricultural Research Service

**Description**: Comprehensive food composition database providing nutritional data for foods. This project uses the SR Legacy (Standard Reference) database, which contains laboratory-analyzed nutrient data for over 7,000 foods.

**Access**: https://fdc.nal.usda.gov/ (web interface) or via REST API

**API Documentation**: https://fdc.nal.usda.gov/api-guide.html

**License**: Public domain under CC0 1.0 Universal (CC0 1.0). No permission needed for use, but USDA requests attribution.

**Citation**: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. fdc.nal.usda.gov.

**Usage**: Nutritional composition of model foods (protein, carbohydrates, fat, energy)

**Workflow retrieval**: Optional via ``retrieve_usda_nutrition`` rule (using the API with included API key)

**Configuration**: Set ``data.usda.retrieve_nutrition: true`` in config to fetch fresh data. By default, the repository includes pre-fetched data in ``data/curated/nutrition.csv``.

**API Key**: The repository includes a shared API key for convenience. Users can optionally obtain their own API key (free, instant signup) at https://fdc.nal.usda.gov/api-key-signup and update the ``data.usda.api_key`` value in the config.

The mapping from model foods to USDA FoodData Central IDs is maintained in ``data/curated/usda_food_mapping.csv``. This file maps internal food names (e.g., "flour-white", "rice-white", "meat-chicken") to specific FDC IDs from the SR Legacy database (e.g., wheat flour white all-purpose enriched, white rice cooked, chicken breast raw).

FAO Nutrient Conversion Table for SUA (2024)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Food and Agriculture Organization of the United Nations (FAO)

**Description**: Official nutrient conversion factors that align FAO Supply Utilization Account (SUA) quantities with macro- and micronutrient totals for hundreds of food items.

**Access**: https://www.fao.org/3/CC9678EN/Nutrient_conversion_table_for_SUA_2024.xlsx

**License**: © FAO 2024. Reuse for private study, research, teaching, or other non-commercial purposes is allowed with acknowledgement of FAO; translation, adaptation, resale, and commercial uses require prior permission via copyright@fao.org.

**Workflow retrieval**: Automatically downloaded to ``data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx`` by the ``download_fao_nutrient_conversion_table`` rule in ``workflow/rules/retrieve.smk``.

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

* **CC0 1.0 (Public Domain)** (USDA FoodData Central): Public domain, no restrictions; attribution requested
* **CC BY 4.0** (GAEZ, FAOSTAT): Requires attribution
* **CC BY 3.0 IGO** (UN WPP): Requires attribution to UN
* **Academic use only** (GADM, GBD, GDD): Commercial use requires permission or paid licensed.
