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

1. Create an account with IHME and download ``IHME-GBD_2021-dealth-rates.csv`` as described in :ref:`ihme-gbd-mortality`.
2. Download the IHME 2019 relative risk workbook ``IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX`` (:ref:`ihme-relative-risks`).
3. Register at the Global Dietary Database portal and download the dataset, placed locally as the directory ``GDD-dietary-intake`` (:ref:`gdd-dietary-intake`).

**Required API key setup:**

4. Register for a Copernicus Climate Data Store account and configure your API key to enable automatic retrieval of land cover data (:ref:`copernicus-land-cover`).


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

CROPGRIDS v1.08
~~~~~~~~~~~~~~~

**Provider**: Tang et al., FAO

**Description**: Global harvested and physical crop area maps for 173 crops around 2020 at 0.05° resolution.

**Resolution**: 0.05° × 0.05° (~5.6 km)

**Access**: https://figshare.com/articles/dataset/CROPGRIDS/22491997

**License**: Creative Commons Attribution 4.0 International (CC BY 4.0)

**Citation**: Tang, H., Nguyen, C., Conchedda, G., Casse, L., Tubiello, F. N., & Maggi, F. (2023). CROPGRIDS. *Scientific Data*, 10(1), 1-16.

**Usage**: Yield gap analysis (comparing attainable vs. actual yields)

FAOSTAT Producer Prices
~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Crop producer prices by country (2015-2024) in USD/tonne.

**Access**: https://www.fao.org/faostat/en/ (PP domain)

**License**: CC BY 4.0 + FAO database terms

**Retrieval**: Via ``faostat`` Python package (``workflow/scripts/retrieve_faostat_prices.py``)

**Usage**: Calibrating production costs in the objective function

FAOSTAT Food Balance Sheets (FBS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: FAO Statistics Division

**Description**: Per-capita food supply quantities (kg/capita/year) by country, item, and year. We use the Grand Total item to benchmark available food supply when scaling food waste fractions.

**Access**: https://www.fao.org/faostat/en/ (Food Balance Sheets domain)

**License**: CC BY 4.0 + FAO database terms

**Retrieval**: Via the ``faostat`` Python client inside ``workflow/scripts/prepare_food_loss_waste.py``.

**Usage**: Converts per-capita waste (kg) to fractions relative to available food supply.

UNSD SDG Indicator 12.3.1 (Food Loss & Waste)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: United Nations Statistics Division (UNSD)

**Description**: SDG indicator database series ``AG_FLS_PCT`` (Food loss percentage) and ``AG_FOOD_WST_PC`` (Food waste per capita) covering SDG 12.3.1a/b.

**Access**: https://unstats.un.org/sdgs/dataportal (see API documentation at https://unstats.un.org/sdgs/UNSDGAPIV5/swagger/index.html)

**License**: UNdata terms — data may be copied and redistributed free of charge provided UNdata/UNSD is cited (“All data and metadata provided on UNdata’s website are available free of charge and may be copied freely, duplicated and further distributed provided that UNdata is cited as the reference.”).

**Retrieval**: ``workflow/scripts/prepare_food_loss_waste.py`` queries the UNSD SDG API, falling back to global product shares to derive food group–specific loss factors where regional detail is missing.

**Usage**: Supplies per-country loss and waste fractions for food groups, injected into the crop→food conversion efficiencies during ``build_model``.

IFA FUBC — Global Fertilizer Use by Crop and Country
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: International Fertilizer Association (IFA) / Dryad

**Description**: Global dataset on inorganic fertilizer application rates (N, P₂O₅, K₂O) by crop and country based on expert surveys. The dataset includes historical data from 8 previous reports (1986–2014/15) and the most recent survey for the 2017–18 period, covering fertilizer application rates (kg/ha) and total consumption (thousand tonnes) for major crops worldwide.

**Access**: https://datadryad.org/stash/dataset/doi:10.5061/dryad.2rbnzs7qh

**API access**: Dryad API v2 (https://datadryad.org/api/v2/)

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

**Workflow retrieval**: Automatic via the ``download_ifa_fubc`` Snakemake rule using the Dryad API v2. Downloads ``ifa_fubc_1_to_9_data.csv`` and ``ifa_fubc_1_to_9_metadata.csv`` to ``data/downloads/``. No registration or API key required.

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

IHME GBD 2021 — Mortality Rates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Institute for Health Metrics and Evaluation (IHME)

**Description**: Cause-specific mortality rates by country, age, and sex from the Global Burden of Disease Study 2021. Used to calculate baseline disease burden attributable to dietary risk factors.

**Query parameters**:
  * Measure: Deaths (Rate per 100,000 population)
  * Causes: Ischemic heart disease, Stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
  * Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual age bins)
  * Sex: Both
  * Year: 2021

**License**: Free for non-commercial use with attribution (IHME Free-of-Charge Non-commercial User Agreement)

**Citation**: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2021 (GBD 2021) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2024. Available from https://vizhub.healthdata.org/gbd-results/

**Workflow integration**: Automatically processed via ``workflow/scripts/prepare_gbd_mortality.py``

**Manual download steps**:

1. Visit https://vizhub.healthdata.org/gbd-results/ and sign in with your IHME account.
2. Reproduce the query parameters above by following this permanent link: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2021-permalink/90f3c59133738e4b70b91072b6fd0db4
3. Export the results as CSV (allow some time for the IHME to process the query) and save to ``data/manually_downloaded``. Rename the file to ``IHME-GBD_2021-dealth-rates.csv`` to match the name expected by the Snakemake workflow.

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

Food Processing Data
--------------------

data/foods.csv — Crop-to-Food Processing Pathways
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

**Configuration**: Set ``data.usda.retrieve_nutrition: true`` in config to fetch fresh data. By default, the repository includes pre-fetched data in ``data/nutrition.csv``.

**API Key**: The repository includes a shared API key for convenience. Users can optionally obtain their own API key (free, instant signup) at https://fdc.nal.usda.gov/api-key-signup and update the ``data.usda.api_key`` value in the config.

The mapping from model foods to USDA FoodData Central IDs is maintained in ``data/usda_food_mapping.csv``. This file maps internal food names (e.g., "flour (white)", "rice", "chicken meat") to specific FDC IDs from the SR Legacy database (e.g., wheat flour white all-purpose enriched, white rice cooked, chicken breast raw).

FAO Nutrient Conversion Table for SUA (2024)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Provider**: Food and Agriculture Organization of the United Nations (FAO)

**Description**: Official nutrient conversion factors that align FAO Supply Utilization Account (SUA) quantities with macro- and micronutrient totals for hundreds of food items.

**Access**: https://www.fao.org/3/CC9678EN/Nutrient_conversion_table_for_SUA_2024.xlsx

**License**: © FAO 2024. Reuse for private study, research, teaching, or other non-commercial purposes is allowed with acknowledgement of FAO; translation, adaptation, resale, and commercial uses require prior permission via copyright@fao.org.

**Workflow retrieval**: Automatically downloaded to ``data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx`` by the ``download_fao_nutrient_conversion_table`` rule in ``workflow/rules/retrieve.smk``.

**Usage**: Contains data on edible portion of foods as well as water content. ``workflow/scripts/prepare_fao_edible_portion.py`` reads sheet ``03`` to export edible portion coefficients and water content (g/100g) for configured crops into ``processing/{name}/fao_edible_portion.csv``; ``workflow/scripts/build_model.py`` combines these with crop yields to rescale dry harvests to fresh edible food mass. Note that for certain crops (grains: rice, barley, oat, buckwheat; oil crops: rapeseed, olive; sugar crops: sugarcane, sugarbeet), the script overrides FAO's coefficients to 1.0 to match the model's yield units, with processing losses handled separately.

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

Most datasets used in this project require attribution. Some disallow redistribution, meaning that food-opt cannot be distributed together with these datasets. Some furthermore prohibit commercial use without prior agreement or a paid-for license.

* **CC0 1.0 (Public Domain)** (USDA FoodData Central): Public domain, no restrictions; attribution requested
* **CC BY 4.0** (GAEZ, CROPGRIDS, FAOSTAT): Requires attribution
* **CC BY 3.0 IGO** (UN WPP): Requires attribution to UN
* **Academic use only** (GADM, GBD, GDD): Commercial use requires permission or paid licensed.
