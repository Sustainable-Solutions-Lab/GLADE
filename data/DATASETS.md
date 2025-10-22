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

## CROPGRIDS

- Description: Global geo-referenced harvested and physical crop area maps for 173 crops around 2020 at 0.05° (~5.6 km) resolution; compiled from Monfreda et al. (2008) plus 28 newer gridded sources aligned to 2020 FAOSTAT statistics.
- Website: https://figshare.com/articles/dataset/CROPGRIDS/22491997
- Version/format: v1.08 release (Figshare v9); we download the NetCDF package `CROPGRIDSv1.08_NC_maps.zip` alongside accompanying country tables.
- License/terms (summary): Creative Commons Attribution 4.0 International (CC BY 4.0); cite Tang, Nguyen, Conchedda, Casse, Tubiello & Maggi (2023), *Scientific Data*, https://doi.org/10.6084/m9.figshare.22491997.v9.
  - License: https://creativecommons.org/licenses/by/4.0/

## FAOSTAT — FAO Statistics Division

- Description: FAO’s global statistical database covering food and agriculture domains for 245+ countries and territories from 1961 onward. This project currently uses:
  - the **Producer Prices (PP)** domain (2015–2024 USD/tonne crop producer prices) for cost calibration.
  - the **Food Balance Sheets (FBS)** domain to obtain per-capita food supply quantities (kg/capita/year), which feed the food loss and waste scaling step.
- Website: https://www.fao.org/faostat/en/
- Version/format: Retrieved via the FAOSTAT API using the ``faostat`` Python client (JSON → Pandas DataFrame).
- License/terms (summary): Datasets disseminated through FAO corporate statistical databases are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0), complemented by FAO’s additional Statistical Database Terms of Use.
  - FAO Statistical Database Terms of Use: https://www.fao.org/contact-us/terms/db-terms-of-use/en/

## UNSD SDG Indicator 12.3.1 — Food Loss and Waste

- Description: United Nations Statistics Division SDG Indicator database. We use series ``AG_FLS_PCT`` (Food loss percentage) and ``AG_FOOD_WST_PC`` (Food waste per capita) to parameterize pre- and post-retail losses by country and food group.
- Website: https://unstats.un.org/sdgs/dataportal
- API documentation: https://unstats.un.org/sdgs/UNSDGAPIV5/swagger/index.html
- Version/format: JSON retrieved via the UNSD SDG API.
- License/terms (summary): Data may be copied, duplicated, and redistributed provided UNdata/UNSD is cited as the reference. (UNdata terms: “All data and metadata provided on UNdata’s website are available free of charge and may be copied freely, duplicated and further distributed provided that UNdata is cited as the reference.”)
- Citation: United Nations Statistics Division. SDG Indicator Database, Goal 12.3.1a/b (Food Loss and Waste). https://unstats.un.org/sdgs/dataportal (accessed YYYY-MM-DD).

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
- Suggested citation (from the dataset readme): Hoekstra, A.Y. and Mekonnen, M.M. (2011) *Global water scarcity: monthly blue water footprint compared to blue water availability for the world’s major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, the Netherlands. http://www.waterfootprint.org/Reports/Report53-GlobalBlueWaterScarcity.pdf

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

## IHME GBD — Global Burden of Disease Study 2021

- Description: Cause-specific mortality rates and dietary risk relative-risk parameters from the Global Burden of Disease (GBD) studies. Death rates feed the baseline disease burden calculation; Appendix Table 7a provides dietary relative risk rates that we resample into optimization breakpoints.
- Website: https://vizhub.healthdata.org/gbd-results/ (mortality); https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks (relative risks)
- Version/format: GBD 2021 death rates (CSV) and GBD 2019 Appendix Table 7a (XLSX).
- Query configuration for mortality data:
  - Measure: Deaths (Rate per 100,000)
  - Causes: Ischemic heart disease, Stroke, Diabetes mellitus, Colon and rectum cancer, Chronic respiratory diseases, All causes
  - Age groups: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years (individual bins, not aggregates)
  - Sex: Both
  - Year: 2021 (or closest available to reference year)
- Permalink (mortality): https://vizhub.healthdata.org/gbd-results?params=gbd-api-2021-permalink/8e5d55f174855a4e62a0ac13c52acf9c
- Permalink (dietary relative risks): https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX
- License/terms (summary): Free for non-commercial use with attribution; GBD data is made available under the IHME Free-of-Charge Non-commercial User Agreement.
  - Terms: https://www.healthdata.org/data-tools-practices/data-practices/ihme-free-charge-non-commercial-user-agreement
- Citation (mortality): Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2021 (GBD 2021) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2024. Available from https://vizhub.healthdata.org/gbd-results/.
- Citation (relative risks): Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Results. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2020.

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
- Content: Macronutrient composition (protein, carbohydrates, total lipid/fat, energy/calories) used to define food nutritional properties in `data/nutrition.csv`.
- License/terms (summary): Public domain data published under CC0 1.0 Universal (CC0 1.0). No permission needed for use, but USDA requests attribution and notification of products using the data.
  - License: https://creativecommons.org/publicdomain/zero/1.0/
- Citation: U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. fdc.nal.usda.gov.
- Workflow integration: Optional rule `retrieve_usda_nutrition` (controlled by `config['data']['usda']['retrieve_nutrition']`) retrieves data via API and writes to `data/nutrition.csv`. By default, this rule is disabled and the repository includes pre-fetched nutritional data.

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
- Workflow integration: Retrieved via the `download_land_cover` Snakemake rule using the `ecmwf-datastores` Python client. The full dataset (~2.2GB) contains multiple variables but only the land cover classification (`lccs_class`) is needed. The `extract_land_cover_class` rule automatically extracts just this variable to `data/downloads/land_cover_lccs_class.nc` (~440MB) and deletes the full download. Manual setup required: (1) Register for a free CDS account at https://cds.climate.copernicus.eu/user/register, (2) Accept the dataset licenses at https://cds.climate.copernicus.eu/datasets/satellite-land-cover?tab=download#manage-licences, (3) Configure your API key in `~/.ecmwfdatastoresrc` or via environment variables (see API documentation). The year and version can be configured via `config['data']['land_cover']['year']` and `config['data']['land_cover']['version']`.

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
- Source publication: Cook-Patton, S. C., Leavitt, S. M., Gibbs, D., Harris, N. L., Lister, K., Anderson-Teixeira, K. J., ... & Griscom, B. W. (2020). Mapping carbon accumulation potential from global natural forest regrowth. *Nature*, 585(7826), 545-550. https://doi.org/10.1038/s41586-020-2686-x
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
