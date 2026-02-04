# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


rule download_gadm_zip:
    output:
        temp("data/downloads/gadm_410-levels.zip"),
    params:
        url="https://geodata.ucdavis.edu/gadm/gadm4.1/gadm_410-levels.zip",
    log:
        "logs/shared/download_gadm_zip.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_adm1:
    input:
        zip="data/downloads/gadm_410-levels.zip",
    output:
        "data/downloads/gadm.gpkg",
    log:
        "logs/shared/extract_adm1.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        ogr2ogr -f GPKG "{output}" "/vsizip/{input.zip}/gadm_410-levels.gpkg" ADM_1 > {log} 2>&1
        """


rule retrieve_cpi_data:
    params:
        start_year=2015,
        end_year=config["currency_base_year"],
    output:
        cpi="processing/shared/cpi_annual.csv",
    log:
        "logs/shared/retrieve_cpi_data.log",
    script:
        "../scripts/retrieve_cpi_data.py"


rule retrieve_hicp_data:
    params:
        start_year=2004,  # FADN data starts 2004
        end_year=config["currency_base_year"],
    output:
        hicp="processing/shared/hicp_annual.csv",
    log:
        "logs/shared/retrieve_hicp_data.log",
    script:
        "../scripts/retrieve_hicp_data.py"


rule retrieve_ppp_rates:
    params:
        start_year=2015,  # Average PPP over FADN/USDA cost period
        end_year=2023,  # Latest available PPP data (2024 not yet published)
    output:
        ppp="processing/shared/ppp_eur_intl_dollar.csv",
    log:
        "logs/shared/retrieve_ppp_rates.log",
    script:
        "../scripts/retrieve_ppp_rates.py"


rule download_fadn_data:
    output:
        data="data/downloads/fadn_nuts0_so.csv",
        variables="data/downloads/fadn_variables.xlsx",
    log:
        "logs/shared/download_fadn_data.log",
    shell:
        """
        wget -q -O {output.data} \
            "https://zenodo.org/api/records/10939892/files/NUTS0_EU_agricultural_SO_LAMASUS.csv/content" \
            > {log} 2>&1
        wget -q -O {output.variables} \
            "https://zenodo.org/api/records/10939892/files/variable_description_zenodo.xlsx/content" \
            >> {log} 2>&1
        """


rule retrieve_usda_costs:
    input:
        sources="data/usda_cost_sources.csv",
        cpi="processing/shared/cpi_annual.csv",
    params:
        base_year=config["currency_base_year"],
        cost_params=config["crop_costs"]["usda"],
        averaging_period=config["crop_costs"]["averaging_period"],
    output:
        costs="processing/{name}/usda_costs.csv",
    log:
        "logs/{name}/retrieve_usda_costs.log",
    script:
        "../scripts/retrieve_usda_costs.py"


rule retrieve_fadn_costs:
    input:
        data="data/downloads/fadn_nuts0_so.csv",
        mapping="data/fadn_crop_mapping.yaml",
        hicp="processing/shared/hicp_annual.csv",
        ppp="processing/shared/ppp_eur_intl_dollar.csv",
    params:
        crops=config["crops"],
        base_year=config["currency_base_year"],
        cost_params=config["crop_costs"]["fadn"],
        averaging_period=config["crop_costs"]["averaging_period"],
    output:
        costs="processing/{name}/fadn_costs.csv",
    log:
        "logs/{name}/retrieve_fadn_costs.log",
    script:
        "../scripts/retrieve_fadn_costs.py"


rule merge_crop_costs:
    input:
        cost_sources=[
            "processing/{name}/usda_costs.csv",
            "processing/{name}/fadn_costs.csv",
        ],
        fallbacks="data/crop_cost_fallbacks.yaml",
    params:
        crops=config["crops"],
        base_year=config["currency_base_year"],
    output:
        costs="processing/{name}/crop_costs.csv",
    log:
        "logs/{name}/merge_crop_costs.log",
    script:
        "../scripts/merge_crop_costs.py"


rule retrieve_usda_animal_costs:
    input:
        sources="data/usda_animal_cost_sources.csv",
        cpi="processing/shared/cpi_annual.csv",
    params:
        base_year=config["currency_base_year"],
        cost_params=config["animal_costs"]["usda"],
        averaging_period=config["animal_costs"]["averaging_period"],
    output:
        costs="processing/{name}/usda_animal_costs.csv",
    log:
        "logs/{name}/retrieve_usda_animal_costs.log",
    script:
        "../scripts/retrieve_usda_animal_costs.py"


rule retrieve_faostat_yields:
    input:
        mapping="data/faostat_animal_yield_mapping.yaml",
    params:
        cost_params=config["animal_costs"]["faostat"],
        averaging_period=config["animal_costs"]["averaging_period"],
    output:
        "processing/{name}/faostat_animal_yields.csv",
    log:
        "logs/{name}/retrieve_faostat_yields.log",
    script:
        "../scripts/retrieve_faostat_yields.py"


rule retrieve_fadn_animal_costs:
    input:
        data="data/downloads/fadn_nuts0_so.csv",
        mapping="data/fadn_animal_mapping.yaml",
        hicp="processing/shared/hicp_annual.csv",
        ppp="processing/shared/ppp_eur_intl_dollar.csv",
        yields="processing/{name}/faostat_animal_yields.csv",
    params:
        animal_products=config["animal_products"]["include"],
        base_year=config["currency_base_year"],
        cost_params=config["animal_costs"]["fadn"],
        averaging_period=config["animal_costs"]["averaging_period"],
    output:
        costs="processing/{name}/fadn_animal_costs.csv",
    log:
        "logs/{name}/retrieve_fadn_animal_costs.log",
    script:
        "../scripts/retrieve_fadn_animal_costs.py"


rule merge_animal_costs:
    input:
        cost_sources=[
            "processing/{name}/usda_animal_costs.csv",
            "processing/{name}/fadn_animal_costs.csv",
        ],
    params:
        animal_products=config["animal_products"]["include"],
        base_year=config["currency_base_year"],
    output:
        costs="processing/{name}/animal_costs.csv",
    log:
        "logs/{name}/merge_animal_costs.log",
    script:
        "../scripts/merge_animal_costs.py"


rule retrieve_faostat_crop_production:
    input:
        mapping="data/faostat_crop_item_map.csv",
    params:
        countries=config["countries"],
        production_year=config["validation"]["production_year"],
    output:
        "processing/{name}/faostat_crop_production.csv",
    log:
        "logs/{name}/retrieve_faostat_crop_production.log",
    script:
        "../scripts/retrieve_faostat_crop_production.py"


rule retrieve_faostat_animal_production:
    params:
        production_year=config["validation"]["production_year"],
        countries=config["countries"],
        carcass_to_retail_meat=config["animal_products"]["carcass_to_retail_meat"],
    output:
        "processing/{name}/faostat_animal_production.csv",
    log:
        "logs/{name}/retrieve_faostat_animal_production.log",
    script:
        "../scripts/retrieve_faostat_animal_production.py"


rule retrieve_faostat_emissions:
    output:
        "processing/{name}/faostat_emissions.csv",
    params:
        year=config["validation"]["production_year"],
    log:
        "logs/{name}/retrieve_faostat_emissions.log",
    script:
        "../scripts/retrieve_faostat_emissions.py"


rule download_gaez_yield_data:
    output:
        "data/downloads/gaez_yield_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        # GAEZ v5 filename: GAEZ-V5.{VARIABLE}.{PERIOD}.{CLIMATE}.{SCENARIO}.{CROP}.{INPUT}.tif
        # INPUT = {input_level}{water_supply}LM (e.g., HILM, HRLM)
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/{config['data']['gaez']['yield_var']}/"
            f"GAEZ-V5.{config['data']['gaez']['yield_var']}."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res05')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    log:
        "logs/shared/download_gaez_yield_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_water_requirement_data:
    output:
        "data/downloads/gaez_water_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/{config['data']['gaez']['water_requirement_var']}/"
            f"GAEZ-V5.{config['data']['gaez']['water_requirement_var']}."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res05')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    log:
        "logs/shared/download_gaez_water_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_suitability_data:
    output:
        "data/downloads/gaez_suitability_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/{config['data']['gaez']['suitability_var']}/"
            f"GAEZ-V5.{config['data']['gaez']['suitability_var']}."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res05')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    log:
        "logs/shared/download_gaez_suitability_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_multiple_cropping_zone:
    output:
        "data/downloads/gaez_multiple_cropping_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}.tif",
    params:
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES01-MCR/"
            f"GAEZ-V5.RES01-MCR.{w.period}.{w.climate_model}.{w.climate_scenario}.tif"
            if w.water_supply.lower() == "r"
            else f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES01-MCI/"
            f"GAEZ-V5.RES01-MCI.{w.period}.{w.climate_model}.{w.climate_scenario}.tif"
        ),
    log:
        "logs/shared/download_gaez_multiple_cropping_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_growing_season_start:
    output:
        "data/downloads/gaez_growing_season_start_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        # RES02-CBD: Beginning of crop growth cycle (day)
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES02-CBD/"
            f"GAEZ-V5.RES02-CBD."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res02')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    log:
        "logs/shared/download_gaez_growing_season_start_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_growing_season_length:
    output:
        "data/downloads/gaez_growing_season_length_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        # RES02-CYL: Length of crop growth cycle (days)
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES02-CYL/"
            f"GAEZ-V5.RES02-CYL."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res02')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    log:
        "logs/shared/download_gaez_growing_season_length_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_actual_yield:
    output:
        "data/downloads/gaez_actual_yield_{water_supply}_{crop}.tif",
    params:
        # RES06-YLD: Actual yields (2010-2019 average)
        # INPUT codes: WSI (irrigated), WSR (rainfed), WST (total)
        # Note: Uses different input naming convention than RES05
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES06-YLD/"
            f"GAEZ-V5.RES06-YLD.{get_gaez_code(w.crop, 'res06')}."
            f"WS{w.water_supply.upper()}.tif"
        ),
    log:
        "logs/shared/download_gaez_actual_yield_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_harvested_area:
    output:
        "data/downloads/gaez_harvested_area_{water_supply}_{crop}.tif",
    params:
        # RES06-HAR: Harvested area (2010-2019 average)
        # INPUT codes: WSI (irrigated), WSR (rainfed), WST (total)
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES06-HAR/"
            f"GAEZ-V5.RES06-HAR.{get_gaez_code(w.crop, 'res06')}."
            f"WS{w.water_supply.upper()}.tif"
        ),
    log:
        "logs/shared/download_gaez_harvested_area_{water_supply}_{crop}.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_irrigated_landshare_map:
    output:
        "data/downloads/gaez_land_equipped_for_irrigation_share.tif",
    params:
        # LR-IRR: Share of land area equipped for irrigation
        gcs_url="gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAP/GAEZ-V5.LR-IRR.tif",
    log:
        "logs/shared/download_gaez_irrigated_landshare_map.log",
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


# TODO: license. Different variations?

# See https://data.isimip.org/search/crop/mgr/variable/yield/irrigation/noirr/


# The following is a future projection, but not about yields but primary productivity
# See https://data.isimip.org/search/simulation_round/ISIMIP2b/sector/biomes/model/lpjml/pft/mgr-rainfed/
# url="https://files.isimip.org/ISIMIP2b/OutputData/biomes/LPJmL/gfdl-esm2m/future/lpjml_gfdl-esm2m_ewembi_rcp26_2005soc_2005co2_gpp-mgr-irrigated_global_annual_2006_2099.nc4",
rule download_grassland_yield_data:
    output:
        "data/downloads/grassland_yield_historical.nc4",
    params:
        url="https://files.isimip.org/ISIMIP2a/OutputData/agriculture/LPJmL/watch/historical/lpjml_watch_nobc_hist_co2_yield-mgr-noirr-default_global_annual_1971_2001.nc4",
    log:
        "logs/shared/download_grassland_yield_data.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_wpp_population:
    output:
        population="data/downloads/WPP_population.csv.gz",
        life_table="data/downloads/WPP_life_table.csv.gz",
    params:
        population_url=(
            "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz"
        ),
        life_table_url=(
            "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/WPP2024_Life_Table_Abridged_Medium_2024-2100.csv.gz"
        ),
    log:
        "logs/shared/download_wpp_population.log",
    shell:
        r"""
        mkdir -p "$(dirname {output.population})"
        curl -L --fail --progress-bar -o "{output.population}" "{params.population_url}" > {log} 2>&1
        curl -L --fail --progress-bar -o "{output.life_table}" "{params.life_table_url}" >> {log} 2>&1
        """


rule download_waterfootprint_appendix:
    output:
        "data/downloads/Report53_Appendix.zip",
    params:
        url="https://www.waterfootprint.org/resources/appendix/Report53_Appendix.zip",
    log:
        "logs/shared/download_waterfootprint_appendix.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_huang_irrigation_water:
    output:
        temp("data/downloads/huang_irrigation_water_v2.7z"),
    params:
        url="https://zenodo.org/records/1209296/files/irrigation%20water%20use%20v2.7z?download=1",
    log:
        "logs/shared/download_huang_irrigation_water.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_huang_irrigation_water:
    input:
        "data/downloads/huang_irrigation_water_v2.7z",
    output:
        "data/downloads/huang_irrigation_water.nc",
    params:
        filename="withd_irr_watergap.nc",
    log:
        "logs/shared/extract_huang_irrigation_water.log",
    shell:
        r"""
        # Extract the NetCDF file from the 7z archive
        # Extract the selected irrigation withdrawal file from the archive
        7z e -y -o"$(dirname {output})" "{input}" "{params.filename}" > {log} 2>&1
        # Rename the extracted file to a simpler name
        mv "$(dirname {output})/{params.filename}" "{output}" >> {log} 2>&1
        """


rule download_fao_nutrient_conversion_table:
    output:
        "data/downloads/fao_nutrient_conversion_table_for_sua_2024.xlsx",
    params:
        url="https://www.fao.org/3/CC9678EN/Nutrient_conversion_table_for_SUA_2024.xlsx",
    log:
        "logs/shared/download_fao_nutrient_conversion_table.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_gleam_supplement:
    output:
        "data/downloads/gleam_3.0_supplement_s1.xlsx",
    params:
        url="https://www.fao.org/fileadmin/user_upload/gleam/docs/GLEAM_3.0_Supplement_S1.xlsx",
    log:
        "logs/shared/download_gleam_supplement.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_land_cover:
    output:
        temp("data/downloads/land_cover.zip"),
    params:
        dataset="satellite-land-cover",
        request={
            "variable": "all",
            "year": [config["data"]["land_cover"]["year"]],
            "version": [config["data"]["land_cover"]["version"]],
        },
    log:
        "logs/shared/download_land_cover.log",
    script:
        "../scripts/download_land_cover.py"


rule extract_land_cover_class:
    input:
        "data/downloads/land_cover.zip",
    output:
        "data/downloads/land_cover_lccs_class.nc",
    log:
        "logs/shared/extract_land_cover_class.log",
    script:
        "../scripts/extract_land_cover_class.py"


rule download_biomass_cci:
    output:
        "data/downloads/esa_biomass_cci_v6_0.nc",
    params:
        url="https://dap.ceda.ac.uk/neodc/esacci/biomass/data/agb/maps/v6.0/netcdf/ESACCI-BIOMASS-L4-AGB-MERGED-10000m-fv6.0.nc?download=1",
    log:
        "logs/shared/download_biomass_cci.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_soilgrids_ocs:
    output:
        "data/downloads/soilgrids_ocs_0-30cm_mean.tif",
    params:
        coverage_id="ocs_0-30cm_mean",
        target_resolution_m=config["data"]["soilgrids"]["target_resolution_m"],
    log:
        "logs/shared/download_soilgrids_ocs.log",
    script:
        "../scripts/download_soilgrids_ocs.py"


rule download_forest_carbon_accumulation_1km:
    output:
        "data/downloads/forest_carbon_accumulation_griscom_1km.tif",
    params:
        url="https://www.arcgis.com/sharing/rest/content/items/f950ea7878e143258a495daddea90cc0/data",
    log:
        "logs/shared/download_forest_carbon_accumulation_1km.log",
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_ifa_fubc:
    output:
        data="data/downloads/ifa_fubc_1_to_9_data.csv",
        metadata="data/downloads/ifa_fubc_1_to_9_metadata.csv",
    params:
        data_file_id=3940355,
        metadata_file_id=3940358,
    log:
        "logs/shared/download_ifa_fubc.log",
    shell:
        r"""
        mkdir -p "$(dirname {output.data})"
        curl -L --fail --progress-bar \
            -o "{output.data}" \
            "https://datadryad.org/api/v2/files/{params.data_file_id}/download" \
            > {log} 2>&1
        curl -L --fail --progress-bar \
            -o "{output.metadata}" \
            "https://datadryad.org/api/v2/files/{params.metadata_file_id}/download" \
            >> {log} 2>&1
        """


rule retrieve_faostat_fbs_items:
    """Retrieve raw item-level supply data from FAOSTAT Food Balance Sheets.

    Fetches supply data (kg/capita/year) for all items in the food item mapping,
    used for calculating within-group food consumption ratios.
    """
    input:
        food_item_map="data/faostat_food_item_map.csv",
    params:
        countries=config["countries"],
        reference_year=config["food_groups"]["fix_within_group_ratios"][
            "reference_year"
        ],
    output:
        fbs_items="processing/{name}/faostat_fbs_items.csv",
    log:
        "logs/{name}/retrieve_faostat_fbs_items.log",
    script:
        "../scripts/retrieve_faostat_fbs_items.py"


# Conditional rule: retrieve nutrition data from USDA if enabled in config
if config["data"]["usda"]["retrieve_nutrition"]:

    rule retrieve_usda_nutrition:
        input:
            mapping="data/usda_food_mapping.csv",
            food_groups="data/food_groups.csv",
        output:
            "data/nutrition.csv",
        log:
            "logs/shared/retrieve_usda_nutrition.log",
        script:
            "../scripts/retrieve_usda_nutrition.py"
