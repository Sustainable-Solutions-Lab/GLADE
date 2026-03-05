<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# Datasets Used

Brief descriptions of key external datasets used by this project, with links and license notes. Always verify the current terms on the official sites before (re)distribution or alternative uses.

## GADM — Global Administrative Areas

- Description: Global administrative boundary data (levels ADM_0–ADM_5). This project uses level‑1 (ADM_1) regions (e.g., states/provinces).
- Website: https://gadm.org/
- Version/format: GADM 4.1; multi‑layer GeoPackage (`gadm_410-levels.gpkg`), with `ADM_1` extracted to a lighter GPKG for convenience.
- License/terms (summary): Free for academic and other non‑commercial use with attribution; redistribution of the data is not allowed; commercial use requires permission from GADM. See the official GADM license page for full terms and any updates.
  - GADM License: https://gadm.org/license.html

## GAEZ — Global Agro‑Ecological Zones (FAO/IIASA)

- Description: Global suitability and attainable yield datasets by crop and scenario. This project uses crop yield and suitability rasters (e.g., variables `yc`, `sx1`) under selected climate and management scenarios.
- Website: https://gaez.fao.org/
- Version: GAEZ v5.
- License/terms (summary): Datasets disseminated through FAO corporate statistical databases are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0), complemented by FAO’s additional Statistical Database Terms of Use.
  - FAO Database Terms of Use: https://www.fao.org/contact-us/terms/db-terms-of-use/en/

## FAOSTAT — FAO Statistics Division

- Description: FAO's global statistical database covering food and agriculture domains for 245+ countries and territories from 1961 onward. This project uses the **Food Balance Sheets (FBS)** domain to obtain per-capita food supply quantities (kg/capita/year), which feed the food loss and waste scaling step.
- Website: https://www.fao.org/faostat/en/
- Version/format: Retrieved via the FAOSTAT API using the ``faostat`` Python client (JSON → Pandas DataFrame).
- License/terms (summary): Datasets disseminated through FAO corporate statistical databases are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0), complemented by FAO’s additional Statistical Database Terms of Use.
  - FAO Statistical Database Terms of Use: https://www.fao.org/contact-us/terms/db-terms-of-use/en/

## UNSD SDG Indicator 12.3.1 — Food Loss and Waste

- Description: United Nations Statistics Division SDG Indicator database. We use series ``AG_FLS_PCT`` (Food loss percentage) and ``AG_FOOD_WST_PC`` (Food waste per capita) to parameterize pre- and post-retail losses by country and food group.
- Website: https://unstats.un.org/sdgs/dataportal
- Archive: https://unstats.un.org/sdgs/indicators/database/archive
- Version/format: Bulk CSV downloaded from the UNSD SDG Indicators Database archive (2025 Q4.1 release). The full archive (~56 MB zip, ~1.7 GB uncompressed) is filtered during extraction to retain only the two relevant series (~500 KB).
- License/terms (summary): Data may be copied, duplicated, and redistributed provided UNdata/UNSD is cited as the reference. (UNdata terms: "All data and metadata provided on UNdata's website are available free of charge and may be copied freely, duplicated and further distributed provided that UNdata is cited as the reference.")
- Citation: United Nations Statistics Division. SDG Indicator Database, Goal 12.3.1a/b (Food Loss and Waste). https://unstats.un.org/sdgs/dataportal (accessed YYYY-MM-DD).
- Workflow integration: Retrieved via the ``download_unsd_sdg`` rule (bulk zip download) and ``extract_unsd_sdg`` rule (filter to AG_FLS_PCT and AG_FOOD_WST_PC series). Output: ``data/downloads/unsd/SDG_12_3_1.csv``. Processed by ``prepare_food_loss_waste.py``.

## USDA ERS — Cost and Returns Data

- Description: Economic Research Service (ERS) cost and returns data providing detailed production cost estimates (USD per acre) for major U.S. crops. Data includes operating costs (seed, chemicals, custom services, fuel, repairs, labor) and allocated overhead (machinery depreciation, farm overhead, taxes/insurance). This project uses a 10-year average (2015–2024) to establish baseline production costs per hectare.
- Website: https://www.ers.usda.gov/data-products/commodity-costs-and-returns/
- Data catalog: https://catalog.data.gov/dataset/commodity-costs-and-returns
- Version/format: Individual crop CSV files retrieved via direct download from ERS QuickStats portal. URLs are listed in `data/curated/usda_cost_sources.csv`.
- Coverage: U.S. total (national averages); annual data from 1975 to present depending on crop.
- License/terms (summary): Creative Commons Attribution (CC BY). Free to use with attribution to USDA Economic Research Service.
- Citation: U.S. Department of Agriculture, Economic Research Service. Commodity Costs and Returns. https://www.ers.usda.gov/data-products/commodity-costs-and-returns/
- Workflow integration: Retrieved via the `retrieve_usda_costs` rule, which downloads CSV files, extracts relevant cost categories, applies inflation adjustment using CPI-U, splits costs into per-year (annual fixed) and per-planting (variable per crop) components, and converts from USD/acre to USD/ha. Output: `processing/{name}/usda_costs.csv`. Cost categories are documented in `docs/crop_production.rst` and `docs/data_sources.rst`.

## USDA ERS — Livestock Cost Data

- Description: Economic Research Service (ERS) livestock production cost data providing estimates for major U.S. animal products. Includes milk cost of production estimates (cost per hundredweight of milk), cow-calf cost and returns (cost per head per year), and hog cost and returns (cost per head marketed). Data includes operating costs (labor, veterinary, energy) and allocated overhead (housing, equipment depreciation, interest).
- Website (Milk): https://www.ers.usda.gov/data-products/milk-cost-of-production-estimates/
- Website (Livestock Commodity Costs): https://www.ers.usda.gov/data-products/commodity-costs-and-returns/
- Version/format: Excel files retrieved via direct download. URLs are listed in `data/curated/usda_animal_cost_sources.csv`.
- Coverage: U.S. animal products (milk, cow-calf for beef, hogs for pork). Temporal coverage varies by product (milk: 2005-present, cow-calf: 2008-present, hogs: 2010-present).
- License/terms (summary): Creative Commons Attribution (CC BY). Free to use with attribution to USDA Economic Research Service.
- Citation: U.S. Department of Agriculture, Economic Research Service. Milk Cost of Production Estimates / Commodity Costs and Returns (Livestock).
- Workflow integration: Retrieved via the `retrieve_usda_animal_costs` rule, which downloads Excel files, extracts cost categories excluding feed costs (modeled endogenously), applies inflation adjustment using CPI-U, and converts from per-head or per-cwt to per-tonne-product. Output: `processing/{name}/usda_animal_costs.csv`. Cost categories are documented in `docs/livestock.rst` and `docs/data_sources.rst`.

## BLS — Consumer Price Index for All Urban Consumers (CPI-U)

- Description: Bureau of Labor Statistics (BLS) Consumer Price Index for All Urban Consumers (CPI-U), All items, U.S. city average, not seasonally adjusted. This project uses annual averages to adjust nominal USD values throughout the workflow to real USD in a configurable base year (default: 2024).
- Website: https://www.bls.gov/cpi/
- API documentation: https://www.bls.gov/developers/api_signature_v2.htm
- Series ID: CUUR0000SA0
- Version/format: Retrieved via BLS Public Data API v2.0 (JSON → CSV). Monthly values are averaged to produce annual CPI-U indices.
- Coverage: 1913 to present (this project retrieves 2015 onwards to cover the USDA cost data period).
- License/terms (summary): Public domain.
- Citation: U.S. Bureau of Labor Statistics. Consumer Price Index for All Urban Consumers: All Items in U.S. City Average. https://www.bls.gov/cpi/
- Workflow integration: Retrieved via the `retrieve_cpi_data` rule and stored in `processing/shared/cpi_annual.csv` for reuse across the workflow. Used by `retrieve_usda_costs` and potentially other rules requiring inflation adjustment.

## FAO — Nutrient Conversion Table for SUA (2024)

- Description: Food balance sheet nutrient conversion factors compiled for the FAO Supply Utilization Accounts (SUA), covering macro- and micronutrients for hundreds of commodities.
- Download: https://www.fao.org/3/CC9678EN/Nutrient_conversion_table_for_SUA_2024.xlsx
- Version/format: 2024 Excel workbook (`Nutrient_conversion_table_for_SUA_2024.xlsx`); retrieved automatically to `data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx` via the `download_fao_nutrient_conversion_table` Snakemake rule.
- License/terms (summary): Material may be copied, downloaded, and printed for private study, research, teaching, or other non-commercial uses with proper acknowledgement of FAO as source; translation, adaptation, resale, and other commercial uses require prior permission (copyright@fao.org).
- Notes: `workflow/scripts/prepare_fao_nutritional_content.py` parses sheet `03` to export crop-level edible portion coefficients to `processing/{name}/fao_nutritional_content.csv` for use in the model.

## Water Footprint Network — Monthly Blue Water Availability

- Description: Monthly blue water availability for 405 GRDC river basins, provided alongside blue-water scarcity indicators as part of the Water Footprint Network’s Appendix to Value of Water Research Report Series No. 53.
- Download: https://www.waterfootprint.org/resources/appendix/Report53_Appendix.zip
- Version/format: Appendix VII of Hoekstra & Mekonnen (2011); data distributed as an ESRI shapefile (`Monthly_WS_GRDC_405_basins.*`) with basin metadata, plus an Excel workbook (`Report53-Appendices-VI-IX.xls`, sheet “Appendix-VII”) containing monthly availability in Mm³/month.
- License/terms (summary): No explicit license accompanies the dataset. The authors request citation as below; users should evaluate whether their use qualifies as fair use (research is probably allowed) and contact the UNESCO-IHE Institute for Water Education for commercial applications.
- Suggested citation (from the dataset readme): Hoekstra, A.Y. and Mekonnen, M.M. (2011) *Global water scarcity: monthly blue water footprint compared to blue water availability for the world's major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, the Netherlands. http://www.waterfootprint.org/Reports/Report53-GlobalBlueWaterScarcity.pdf

## Huang et al. — Global Gridded Monthly Sectoral Water Withdrawals

- Description: Reconstructed global monthly gridded (0.5°) sectoral water withdrawal dataset for 1971–2010, distinguishing six water use sectors: irrigation, domestic, electricity generation (cooling of thermal power plants), livestock, mining, and manufacturing. This project uses the **irrigation** sector data to represent current agricultural water withdrawals for validation scenarios (as opposed to sustainable water availability from the Water Footprint Network dataset above).
- Website: https://hess.copernicus.org/articles/22/2117/2018/
- Download: https://zenodo.org/records/1209296
- Version/format: v2 (Zenodo); 7z-compressed NetCDF bundle (`irrigation water use v2.7z`, ~296 MB) with multiple model variants for irrigation withdrawals/consumption. We extract the irrigation withdrawal WaterGAP file (`withd_irr_watergap.nc`) for processing.
- Coverage: Global; monthly data for 1971-2010. For validation, the final available year (2010) is used as a proxy for current irrigation water use.
- Variables: Monthly irrigation water withdrawal per grid cell.
- License/terms (summary): Open access dataset. Authors request citation as below.
- Citation: Huang, Z., Hejazi, M., Li, X., Tang, Q., Vernon, C., Leng, G., Liu, Y., Döll, P., Eisner, S., Gerten, D., Hanasaki, N., and Wada, Y. (2018). Reconstruction of global gridded monthly sectoral water withdrawals for 1971–2010 and analysis of their spatiotemporal patterns. *Hydrology and Earth System Sciences*, 22, 2117-2133. https://doi.org/10.5194/hess-22-2117-2018
- Workflow integration: Retrieved via the `download_huang_irrigation_water` rule (Zenodo download, 7z extraction). The `process_huang_irrigation_water` rule aggregates gridded monthly data to model regions by area-weighted summation. Outputs: `processing/{name}/water/current_use/monthly_region_water.csv` and `processing/{name}/water/current_use/region_growing_season_water.csv`, which are selected into `processing/{name}/water/*` when `config['water']['supply_scenario']` is `"current_use"` (default for validation configs).

## UN WPP — World Population Prospects 2024 (UN DESA)

- Description: Official United Nations population estimates and projections prepared by UN DESA’s Population Division. This project uses the total population table (medium variant) to obtain planning-horizon population totals by country. Additionally, we use the abridged life table for years-of-life-lost calculations.
- Website: https://population.un.org/wpp/
- Version/format: 2024 Revision; `WPP2024_TotalPopulationBySex.csv.gz` (CSV, medium variant) and `WPP2024_Life_Table_Abridged_Medium_2024-2100` (CSV, medium variant).
- License/terms (summary): UN population data is made available under the Creative Commons Attribution 3.0 IGO license (CC BY 3.0 IGO)
  - See copyright notice at the bottom of https://population.un.org/wpp/downloads

## DIA Health Impact Inputs (Diet Impact Assessment)

- Description: Epidemiological inputs used by the Diet Impact Assessment (DIA) model to translate dietary exposures into health burdens. We copy a minimal subset covering dietary risk relative-risk schedules, baseline consumption, mortality, demographic structure, and regional values of a statistical life year.
- Source repository: https://github.com/marco-spr/WHO-DIA
- Version/format: CSV snapshots dated 2021-05-28 (diet, risk schedules, demographics) and 2021-10-18 (VSL region table).
- License/terms (summary): Whole repository licensed under the GPL-3.0

## IHME GBD — Global Burden of Disease Study 2023

- Description: Cause-specific mortality rates and dietary risk relative-risk parameters from the Global Burden of Disease (GBD) studies. Death rates feed the baseline disease burden calculation; Appendix Table 7a provides dietary relative risk rates that we resample into optimization breakpoints.
- Website: https://vizhub.healthdata.org/gbd-results/ (mortality); https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks (relative risks)
- Version/format: GBD 2023 death rates (CSV) and GBD 2019 Appendix Table 7a (XLSX).
- Query configuration for mortality data:
  - Measure: Deaths (Rate per 100,000)
  - Causes: Ischemic heart disease, Stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
  - Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual bins, not aggregates)
  - Sex: Both
  - Year: must match `baseline_year` (default: 2020)
- Permalink (mortality, year 2020): https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/f4c7511d159798f5b8864bc83fa06451
- Permalink (dietary relative risks): https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX
- License/terms (summary): Free for non-commercial use with attribution; GBD data is made available under the IHME Free-of-Charge Non-commercial User Agreement.
  - Terms: https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement
- Citation (mortality): Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2023. Available from https://vizhub.healthdata.org/gbd-results/.
- Citation (relative risks): Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Results. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2020.

## IHME GBD — Dietary Risk Exposure Estimates 1990–2019

- Description: Country-level dietary risk exposure estimates from the Global Burden of Disease Study 2019, covering 15 dietary risk factors with mean exposure, uncertainty, and summary exposure values (SEVs) by country, age, sex, and year. This project uses these estimates to supplement the Global Dietary Database (GDD) baseline dietary intake data.
- Website: https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019
- Download: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip
- Version/format: GBD 2019; ZIP archive containing per-risk-factor CSVs (one CSV per dietary risk factor, ~47 MB each). Risk factors include: calcium, fiber, fruit, legumes, milk, nuts, omega-3, processed meat, PUFA, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains.
- Coverage: 204 countries and territories, 1990–2019, by age group and sex.
- License/terms (summary): Free for non-commercial use with attribution; GBD data is made available under the IHME Free-of-Charge Non-commercial User Agreement.
  - Terms: https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement
- Citation: Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Dietary Risk Exposure Estimates 1990-2019. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2021.

## GDD — Global Dietary Database (Tufts University)

- Description: Country-level estimates of dietary intake for major food groups and dietary risk factors based on systematic review and meta-analysis of national dietary surveys. This project uses GDD data to establish baseline dietary intake patterns by country for health risk assessment.
- Website: https://www.globaldietarydatabase.org/
- Data download: https://globaldietarydatabase.org/data-download
- Version/format: Downloaded as CSV (~1.6 GB); coverage circa 2015-2020 depending on country survey availability.
- Content: Mean daily intake (g/day per capita) for major food groups including vegetables, fruits, whole grains, legumes, nuts & seeds, red meat, processed meat, and seafood, with uncertainty estimates.
- Coverage: 185+ countries
- License/terms (summary): Free for non-commercial research, teaching, and private study with attribution. Requires user registration. Data may not be redistributed, shared with third parties, or used for commercial purposes without written permission from Tufts.
  - Terms and conditions: https://globaldietarydatabase.org/terms-and-conditions-use
- Citation: Global Dietary Database. Dietary intake data by country. https://www.globaldietarydatabase.org/.

## USDA FoodData Central

- Description: USDA's integrated nutritional database providing comprehensive food composition data including macronutrients (protein, carbohydrates, fat) and energy values. This project uses the FoodData Central API to retrieve nutritional data for model foods from the SR Legacy (Standard Reference) database, which provides laboratory-analyzed nutrient data for over 7,000 foods.
- Website: https://fdc.nal.usda.gov/
- API documentation: https://fdc.nal.usda.gov/api-guide.html
- API key: The repository includes a shared API key for convenience. Users can optionally obtain their own API key (free, instant signup) at https://fdc.nal.usda.gov/api-key-signup.
- Version/format: Retrieved via REST API; nutritional values per 100g of food product.
- Content: Macronutrient composition (protein, carbohydrates, total lipid/fat, energy/calories) used to define food nutritional properties in `data/curated/nutrition.csv`.
- License/terms (summary): Public domain data published under CC0 1.0 Universal (CC0 1.0). No permission needed for use, but USDA requests attribution and notification of products using the data.
  - License: https://creativecommons.org/publicdomain/zero/1.0/
- Citation: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. fdc.nal.usda.gov.
- Workflow integration: Optional rule `retrieve_usda_nutrition` (controlled by `config['data']['usda']['retrieve_nutrition']`) retrieves data via API and writes to `data/curated/nutrition.csv`. By default, this rule is disabled and the repository includes pre-fetched nutritional data.

## Copernicus — Satellite Land Cover

- Description: Global land cover classification gridded maps from 1992 to present derived from satellite observations. The dataset describes the land surface into 22 classes including various vegetation types, water bodies, built-up areas, and bare land. This project uses land cover data for spatial analysis of agricultural land availability and land use constraints.
- Website: https://cds.climate.copernicus.eu/datasets/satellite-land-cover
- API documentation: https://cds.climate.copernicus.eu/how-to-api
- Version/format: v2.1.1 (2016 onwards); NetCDF format via the Copernicus Climate Data Store API.
- Resolution: 300 m spatial resolution; annual temporal resolution (with approximately one-year publication delay).
- Coverage: Global (Plate Carrée projection).
- License/terms (summary): Multiple licenses apply: the ESA CCI licence, CC-BY licence, and VITO licence. In addition, users must cite the climate data store entry (see below) and provide attribution to the Copernicus program.
  - Terms of use: https://cds.climate.copernicus.eu/terms-of-use
- Citation: Copernicus Climate Change Service, Climate Data Store, (2019): Land cover classification gridded maps from 1992 to present derived from satellite observation. Copernicus Climate Change Service (C3S) Climate Data Store (CDS). DOI: 10.24381/cds.006f2c9a (Accessed on 16-10-2025).
- Workflow integration: Retrieved via the `download_land_cover` Snakemake rule using the `ecmwf-datastores` Python client. The full dataset (~2.2GB) contains multiple variables but only the land cover classification (`lccs_class`) is needed. The `extract_land_cover_class` rule automatically extracts just this variable to `data/downloads/land_cover_lccs_class.nc` (~440MB) and deletes the full download. Manual setup required: (1) Register for a free CDS account at https://cds.climate.copernicus.eu/user/register, (2) Accept the dataset licenses at https://cds.climate.copernicus.eu/datasets/satellite-land-cover?tab=download#manage-licences, (3) Configure your API key in `~/.ecmwfdatastoresrc` or via environment variables (see API documentation). The land cover year is derived from the top-level `baseline_year` parameter; the version can be configured via `config['data']['land_cover']['version']`.

## ESA Biomass CCI — Global Above-Ground Biomass

- Description: Global forest above-ground biomass (AGB) maps derived from satellite observations (Sentinel-1 SAR, Envisat ASAR, ALOS PALSAR). The dataset provides annual AGB estimates in tonnes per hectare, along with per-pixel uncertainty estimates and change maps between consecutive years. This project uses the merged multi-resolution product at 10 km resolution covering the years 2007, 2010, and 2015-2022.
- Website: https://catalogue.ceda.ac.uk/uuid/95913ffb6467447ca72c4e9d8cf30501
- Version/format: v6.0 (released April 2025); NetCDF format via direct download.
- Resolution: 10 km (10,000 m) spatial resolution; annual temporal resolution.
- Coverage: Global (90°N to 90°S, 180°W to 180°E); years 2007, 2010, 2015-2022.
- Variables: Above-ground biomass (tons/ha), per-pixel uncertainty (standard deviation), AGB change maps.
- License/terms (summary): Use is covered by the ESA CCI Biomass Terms and Conditions. Public data available to both registered and non-registered users. Must cite dataset correctly using the citation given on the catalogue record.
  - License: https://artefacts.ceda.ac.uk/licences/specific_licences/esacci_biomass_terms_and_conditions_v2.pdf
- Citation: Santoro, M.; Cartus, O. (2025): ESA Biomass Climate Change Initiative (Biomass_cci): Global datasets of forest above-ground biomass for the years 2007, 2010, 2015, 2016, 2017, 2018, 2019, 2020, 2021 and 2022, v6.0. NERC EDS Centre for Environmental Data Analysis. DOI: 10.5285/95913ffb6467447ca72c4e9d8cf30501
- Workflow integration: Retrieved via the `download_biomass_cci` Snakemake rule using curl. The file downloads directly to `data/downloads/esa_biomass_cci_v6_0.nc`. No registration or API key required.

## ISRIC SoilGrids — Global Soil Organic Carbon Stock

- Description: Global soil organic carbon (SOC) stock predictions for 0-30 cm depth interval based on digital soil mapping using Quantile Random Forest. The dataset provides mean predictions along with quantile estimates (5th, 50th, 95th percentiles) and uncertainty layers derived from the global compilation of soil ground observations (WoSIS).
- Website: https://www.isric.org/explore/soilgrids
- Data catalogue: https://data.isric.org/geonetwork/srv/api/records/713396f4-1687-11ea-a7c0-a0481ca9e724
- FAQ: https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html
- Version/format: SoilGrids250m 2.0 (v2.0); GeoTIFF format via Web Coverage Service (WCS).
- Resolution: Native 250 m; this project retrieves data at configurable resolution (default: 10 km) via WCS scaling.
- Coverage: Global (-180° to 180°, -56° to 84°); Interrupted Goode Homolosine projection (EPSG:152160).
- Temporal coverage: Based on data from April 1905 to July 2016.
- Units: Tonnes per hectare (t/ha) for 0-30 cm depth interval.
- Variables: Mean organic carbon stock (`ocs_0-30cm_mean`), 5th/50th/95th percentile estimates, uncertainty (standard deviation).
- License/terms (summary): Creative Commons Attribution 4.0 International (CC BY 4.0). Free to use with attribution to ISRIC - World Soil Information.
  - License: https://creativecommons.org/licenses/by/4.0/
- Citation: Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B., Ribeiro, E., & Rossiter, D. (2021). SoilGrids 2.0: producing soil information for the globe with quantified spatial uncertainty. *SOIL*, 7(1), 217–240. https://doi.org/10.5194/soil-7-217-2021
- Workflow integration: Retrieved via the `download_soilgrids_ocs` Snakemake rule using ISRIC's WCS endpoint. The script downloads the global mean soil carbon stock at the resolution specified by `config['data']['soilgrids']['target_resolution_m']` (default: 10000m = 10km). Output file: `data/downloads/soilgrids_ocs_0-30cm_mean.tif` (~1.2 MB at 10km resolution). No registration or API key required.

## Cook-Patton & Griscom — Forest Carbon Accumulation Potential

- Description: Global map of carbon accumulation potential from natural forest regrowth in forest and savanna biomes. The dataset estimates the rate at which carbon could be sequestered in aboveground and belowground (root) live biomass during the first thirty years of natural forest regrowth, regardless of current land cover or potential for reforestation. Based on a compilation of 13,112 georeferenced measurements combined with 66 environmental covariate layers in a machine learning model (random forest).
- Website: https://data.globalforestwatch.org/documents/f950ea7878e143258a495daddea90cc0
- Source publication: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., ... & Griscom, B. W. (2020). Mapping carbon accumulation potential from natural forest regrowth. *Nature*, 585(7826), 545-550. https://doi.org/10.1038/s41586-020-2686-x
- Version/format: GeoTIFF format via direct download; native resolution 1 km.
- Resolution: Native 1 km (1000 m); this project retrieves data at 1 km and resamples onto the model's 1/12° resource grid (≈9 km at the equator) using an xarray/rasterio script with average resampling.
- Coverage: Global; all forest and savanna biomes (approximately 16% of global land pixels have valid data).
- Projection: ESRI:54034 (World Cylindrical Equal Area).
- Units: Megagrams (Mg) of carbon per hectare per year (Mg C/ha/yr) for the first 30 years of natural regrowth.
- Variables: Total carbon sequestration rate (aboveground + belowground/root biomass) from natural forest regrowth.
- License: Creative Commons Attribution 4.0 International (CC BY 4.0)
- Citation: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., Briggs, R. D., Chazdon, R. L., Crowther, T. W., Ellis, P. W., Griscom, H. P., Herrmann, V., Holl, K. D., Houghton, R. A., Larrosa, C., Lomax, G., Lucas, R., Madsen, P., Malhi, Y., ... Griscom, B. W. (2020). Mapping carbon accumulation potential from global natural forest regrowth. *Nature*, 585(7826), 545-550. https://doi.org/10.1038/s41586-020-2686-x
- Workflow integration: Retrieved via the `download_forest_carbon_accumulation_1km` rule (curl download, temporary file) and the shared `resample_regrowth` rule. The resampling script leverages rasterio to aggregate by area and reproject onto the model grid used by `prepare_luc_inputs`, writing a compressed NetCDF at `processing/shared/luc/regrowth_resampled.nc`. No registration or API key required.

## GLEAM Supplementary Tables — Global Livestock Environmental Assessment Model

- Description: Supplementary tables from FAO's Global Livestock Environmental Assessment Model (GLEAM) providing detailed livestock system parameters including feed composition, digestibility, methane conversion factors, and manure management characteristics by species, production system, and region.
- Download: https://www.fao.org/fileadmin/user_upload/gleam/docs/GLEAM_3.0_Supplement_S1.xlsx
- Version/format: GLEAM 3.0 Supplement S1; Excel workbook (`GLEAM_3.0_Supplement_S1.xlsx`) retrieved automatically to `data/downloads/gleam_3.0_supplement_s1.xlsx` via the `download_gleam_supplement` Snakemake rule.
- License/terms (summary): Material may be copied, downloaded, and printed for private study, research and teaching purposes, or for use in non-commercial products or services, provided that appropriate acknowledgement of FAO as the source and copyright holder is given and that FAO's endorsement of users' views, products or services is not implied in any way.
- Citation: FAO. (2022). Global Livestock Environmental Assessment Model (GLEAM). Rome. https://www.fao.org/gleam/

## GLEAM 3.0 Feed Intake and Production Data

- Description: Country-level feed intake and animal production data from FAO's Global Livestock Environmental Assessment Model version 3.0. Provides total feed use (kg DM/year) by country, species (cattle, buffalo, sheep, goats, chicken, pigs), production system (grassland, mixed, feedlot, backyard, industrial, intermediate, layer, broiler), and feed category (grains, oil seed cakes, grass and leaves, crop residues, fodder crop, by-products, other edible, other non-edible). Production data provides corresponding animal product outputs (meat carcass weight, milk/egg weight) per country and system.
- Source: Obtained directly from FAO upon request. The data are outputs of the GLEAM 3.0 model.
- Version/format: Two CSV files bundled in `data/bundled/`: `GLEAM3_intakes.csv` (feed intake, 229 countries) and `GLEAM3_production.csv` (animal production, 229 countries).
- Coverage: Global; reference year 2015. 229 countries, 6 animal species, 8 production systems, 8 feed categories.
- License: Creative Commons Attribution 4.0 International (CC BY 4.0), with the additional FAO data terms requiring acknowledgement of FAO as the source and copyright holder, and that FAO's endorsement of users' views, products or services is not implied.
- Citation: FAO. (2022). Global Livestock Environmental Assessment Model (GLEAM). Rome. https://www.fao.org/gleam/
- Workflow integration: Consumed by `prepare_feed_baseline.py` which maps GLEAM3 country-level intakes to model feed categories via `compute_gleam3_feed_fractions.py`, splits multi-product systems using FCR-weighted shares, and scales to the configured reference year.

## IFA FUBC — Global Fertilizer Use by Crop and Country

- Description: Global dataset on inorganic fertilizer application rates (N, P₂O₅, K₂O) by crop and country based on expert surveys conducted by the International Fertilizer Association (IFA) and partners. The dataset includes historical data from 8 previous reports (1986–2014/15) and the most recent survey for the 2017–18 period, covering fertilizer application rates (kg/ha) and total consumption (thousand tonnes) for major crops worldwide.
- Website: https://datadryad.org/stash/dataset/doi:10.5061/dryad.2rbnzs7qh
- Version/format: Version 1 (March 2025); bundled with the repository under `data/bundled/doi_10_5061_dryad_2rbnzs7qh__v20250311/`. The full Dryad dataset is included as-is.
- Coverage: Global; 2017–18 period for latest survey, with historical data from 1986 onwards. Includes data for major crops (cereals, oilseeds, roots & tubers, vegetables, fruits, etc.) across countries with significant fertilizer use.
- License/terms (summary): Creative Commons Zero v1.0 Universal (CC0 1.0). Data is in the public domain and may be used without restriction.
  - License: https://creativecommons.org/publicdomain/zero/1.0/
- Citation: Ludemann, C., Gruere, A., Heffer, P., & Dobermann, A. (2022). Global data on fertilizer use by crop and by country [Dataset]. Dryad. https://doi.org/10.5061/dryad.2rbnzs7qh
- Workflow integration: Bundled dataset (CC0 licensed) included directly in the repository due to Dryad API access restrictions. Data includes nitrogen (N), phosphate (P₂O₅), and potash (K₂O) application rates and quantities by crop, country, and year, enabling calculation of crop-specific fertilizer application rates for N₂O emissions modeling.

## LUIcube — Global Land-Use Intensity Data (Matej et al. 2025)

- Description: Global gridded dataset of land-use intensity indicators at 30 arcsec resolution, covering multiple land-use classes including grasslands. Provides area, harvested HANPP (Human Appropriation of Net Primary Production), and remaining NPP (NPP_eco) per grid cell, enabling computation of grassland yields and grazing intensity. This project uses the GL-owl (grassland with scattered trees) and GL-notrees (open grassland) classes to derive total grassland area, productivity, and utilisation intensity.
- Website: https://doi.org/10.1038/s41597-025-04788-1
- Download: Zenodo records 14137284 (GL-owl) and 14013964 (GL-notrees)
- Version/format: v1.0 (2025); GeoTIFF files within ZIP archives on Zenodo, accessed via HTTP range requests (remotezip).
- Resolution: 30 arcsec (~1 km) spatial resolution; annual temporal resolution.
- Coverage: Global; year derived from the top-level `baseline_year` parameter.
- Variables: Area (km²), HANPP_harv (tC/yr), NPP_eco (tC/yr) per grid cell and land-use class.
- License/terms (summary): Creative Commons Attribution 4.0 International (CC BY 4.0).
  - License: https://creativecommons.org/licenses/by/4.0/
- Citation: Matej, S., et al. (2025). LUIcube — a global gridded dataset of land-use intensity for 2000–2020 at 30 arcsec resolution. *Scientific Data*, 12, 289. https://doi.org/10.1038/s41597-025-04788-1
- Workflow integration: Retrieved via the `download_luicube_grassland` rule using `remotezip` to extract individual GeoTIFFs from Zenodo ZIP archives without downloading the full archive. Six GeoTIFFs are downloaded (area, HANPPharv, NPPeco for GL-owl and GL-notrees). The `resample_luicube_grassland` rule sums the two grassland classes, reprojects to the model grid using sum resampling, and outputs `processing/shared/luc/luicube_grassland.nc` with area, HANPP, NPP_act, and grassland_fraction variables. The `build_luicube_grassland_yields` rule computes per-region/class yields (tDM/ha) and grazing intensity.

## FADN — Farm Accountancy Data Network (EU)

- Description: European Union farm-level accounting database providing economic and structural data for agricultural holdings across EU member states. This project uses the LAMASUS-processed NUTS-level agricultural dataset derived from the FADN Public Database, which aggregates farm accounting data (crop outputs, production costs, areas) into standard results by country, year, and farm typology. FADN collects detailed cost breakdowns including seeds, crop protection, machinery, energy, labor, and other production expenses.
- Website (FADN): https://agriculture.ec.europa.eu/data-and-analysis/farm-structures-and-economics/fsdn_en
- Website (LAMASUS dataset): https://zenodo.org/records/10939892
- Version/format: LAMASUS v1.0 (March 2025); FADN Standard Results (SO classification, 2004-2020); CSV files for NUTS0 (country-level) aggregation retrieved automatically to `data/downloads/fadn_nuts0_so.csv` and `data/downloads/fadn_variables.xlsx` via the `download_fadn_data` Snakemake rule.
- Coverage: EU-27 member states; annual data from 2004 to 2020; crop categories include cereals, protein crops, potatoes, sugar beet, oilseeds, vegetables, fruits, citrus, and olives.
- Cost variables: Seeds and plants (SE285), crop protection (SE300), other crop-specific costs (SE305), machinery costs (SE340), energy (SE345), contract work (SE350), depreciation (SE360), wages (SE370), interest (SE380). Fertilizer costs (SE295) and rent (SE375) are excluded as they are modeled endogenously.
- License/terms (summary): LAMASUS dataset is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0). Original FADN data © European Union; free for non-commercial use with attribution. FADN Public Database aggregates individual farm data into standard results; individual farm details remain confidential.
  - License (LAMASUS): https://creativecommons.org/licenses/by/4.0/
- Citation: Wögerer, M. (2024). LAMASUS NUTS-level agricultural data derived from public FADN 1989-2009 (SGM) & 2004-2020 (SO) (0.1) [Data set]. Zenodo. https://doi.org/10.5281/zenodo.10939892
- Workflow integration: Retrieved via the `download_fadn_data` rule (Zenodo direct download) and processed by `retrieve_fadn_costs` script. Costs are allocated to model crops proportionally by output value share within FADN crop categories (e.g., cereals, vegetables), inflation-adjusted using EU HICP, converted to USD, and averaged over 2015-2020. Output: `processing/{name}/fadn_costs.csv` with per-year and per-planting cost components. Costs are merged with USDA data via `merge_crop_costs` rule to provide comprehensive global cost coverage.

## IMF — World Economic Outlook (GDP per Capita)

- Description: International Monetary Fund World Economic Outlook (WEO) database providing GDP per capita estimates (current prices, USD) for countries worldwide. This project uses GDP per capita data to inform multi-objective health clustering, grouping countries with similar economic development levels together.
- Website: https://www.imf.org/external/datamapper/NGDPDPC@WEO
- API documentation: https://www.imf.org/external/datamapper/api/help
- Version/format: Retrieved via IMF DataMapper API (JSON → CSV). Indicator `NGDPDPC` (GDP per capita, current prices, U.S. dollars).
- Coverage: Global; annual data from 1980 to present with projections.
- License/terms (summary): IMF data is free to use with attribution. Data may be downloaded, copied, and redistributed provided the IMF is cited as the source. Transformation or adaptation of data must be explicitly stated alongside the source citation.
  - Terms of use: https://www.imf.org/en/about/copyright-and-terms#data
- Citation: International Monetary Fund, World Economic Outlook Database. https://www.imf.org/external/datamapper/NGDPDPC@WEO
- Workflow integration: Retrieved via the `retrieve_gdp_per_capita` rule using the IMF DataMapper API. No API key required. Output: `data/downloads/gdp_per_capita.csv` with columns `iso3`, `gdp_per_capita`, `year`. Used by `prepare_health_costs` for multi-objective country clustering based on geography, GDP similarity, and population balance.
