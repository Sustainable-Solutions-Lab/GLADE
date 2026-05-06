# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later


rule download_gadm_zip:
    output:
        temp("data/downloads/gadm_410-levels.zip"),
    params:
        url="https://geodata.ucdavis.edu/gadm/gadm4.1/gadm_410-levels.zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gadm_zip.log",
    benchmark:
        "<benchmarks>/shared/download_gadm_zip.tsv"
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
    resources:
        runtime="15m",
        mem_mb=1100,
    log:
        "<logs>/shared/extract_adm1.log",
    benchmark:
        "<benchmarks>/shared/extract_adm1.tsv"
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
        cpi="<processing>/shared/cpi_annual.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/shared/retrieve_cpi_data.log",
    benchmark:
        "<benchmarks>/shared/retrieve_cpi_data.tsv"
    script:
        "../scripts/retrieve_cpi_data.py"


rule retrieve_hicp_data:
    params:
        start_year=2004,  # FADN data starts 2004
        end_year=config["currency_base_year"],
    output:
        hicp="<processing>/shared/hicp_annual.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/shared/retrieve_hicp_data.log",
    benchmark:
        "<benchmarks>/shared/retrieve_hicp_data.tsv"
    script:
        "../scripts/retrieve_hicp_data.py"


rule retrieve_ppp_rates:
    params:
        start_year=2015,  # Average PPP over FADN/USDA cost period
        end_year=2023,  # Latest available PPP data (2024 not yet published)
    output:
        ppp="<processing>/shared/ppp_eur_intl_dollar.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/shared/retrieve_ppp_rates.log",
    benchmark:
        "<benchmarks>/shared/retrieve_ppp_rates.tsv"
    script:
        "../scripts/retrieve_ppp_rates.py"


rule download_fadn_data:
    output:
        data="data/downloads/fadn_nuts0_so.csv",
        variables="data/downloads/fadn_variables.xlsx",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_fadn_data.log",
    benchmark:
        "<benchmarks>/shared/download_fadn_data.tsv"
    shell:
        """
        wget -q -O {output.data} \
            "https://zenodo.org/api/records/10939892/files/NUTS0_EU_agricultural_SO_LAMASUS.csv/content" \
            > {log} 2>&1
        wget -q -O {output.variables} \
            "https://zenodo.org/api/records/10939892/files/variable_description_zenodo.xlsx/content" \
            >> {log} 2>&1
        """


rule retrieve_usda_animal_costs:
    input:
        sources="data/curated/usda_animal_cost_sources.csv",
        cpi="<processing>/shared/cpi_annual.csv",
    params:
        base_year=config["currency_base_year"],
        cost_params=config["animal_costs"]["usda"],
        averaging_period=config["costs"]["averaging_period"],
    output:
        costs="<processing>/{name}/usda_animal_costs.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/{name}/retrieve_usda_animal_costs.log",
    benchmark:
        "<benchmarks>/{name}/retrieve_usda_animal_costs.tsv"
    script:
        "../scripts/retrieve_usda_animal_costs.py"


rule retrieve_fadn_animal_costs:
    input:
        data="data/downloads/fadn_nuts0_so.csv",
        mapping="data/curated/fadn_animal_mapping.yaml",
        hicp="<processing>/shared/hicp_annual.csv",
        ppp="<processing>/shared/ppp_eur_intl_dollar.csv",
        yields="<processing>/{name}/faostat_animal_yields.csv",
    params:
        animal_products=config["animal_products"]["include"],
        base_year=config["currency_base_year"],
        cost_params=config["animal_costs"]["fadn"],
        averaging_period=config["costs"]["averaging_period"],
    output:
        costs="<processing>/{name}/fadn_animal_costs.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/{name}/retrieve_fadn_animal_costs.log",
    benchmark:
        "<benchmarks>/{name}/retrieve_fadn_animal_costs.tsv"
    script:
        "../scripts/retrieve_fadn_animal_costs.py"


rule merge_animal_costs:
    input:
        cost_sources=[
            "<processing>/{name}/usda_animal_costs.csv",
            "<processing>/{name}/fadn_animal_costs.csv",
        ],
    params:
        animal_products=config["animal_products"]["include"],
        base_year=config["currency_base_year"],
    output:
        costs="<processing>/{name}/animal_costs.csv",
    resources:
        runtime="5m",
        mem_mb=200,
    log:
        "<logs>/{name}/merge_animal_costs.log",
    benchmark:
        "<benchmarks>/{name}/merge_animal_costs.tsv"
    script:
        "../scripts/merge_animal_costs.py"


rule download_faostat_pp:
    output:
        temp("data/downloads/faostat/PP.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/Prices_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_pp.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_pp.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_pp:
    input:
        "data/downloads/faostat/PP.zip",
    output:
        "data/downloads/faostat/PP.parquet",
    resources:
        runtime="2m",
        mem_mb=2500,
    log:
        "<logs>/shared/extract_faostat_pp.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_pp.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_faostat_qcl:
    output:
        temp("data/downloads/faostat/QCL.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_qcl.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_qcl.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_qcl:
    input:
        "data/downloads/faostat/QCL.zip",
    output:
        "data/downloads/faostat/QCL.parquet",
    resources:
        runtime="2m",
        mem_mb=2500,
    log:
        "<logs>/shared/extract_faostat_qcl.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_qcl.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_faostat_fbs:
    output:
        temp("data/downloads/faostat/FBS.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_fbs.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_fbs.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_fbs:
    input:
        "data/downloads/faostat/FBS.zip",
    output:
        "data/downloads/faostat/FBS.parquet",
    resources:
        runtime="2m",
        mem_mb=3000,
    log:
        "<logs>/shared/extract_faostat_fbs.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_fbs.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_faostat_rl:
    output:
        temp("data/downloads/faostat/RL.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/Inputs_LandUse_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_rl.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_rl.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_rl:
    input:
        "data/downloads/faostat/RL.zip",
    output:
        "data/downloads/faostat/RL.parquet",
    resources:
        runtime="2m",
        mem_mb=2500,
    log:
        "<logs>/shared/extract_faostat_rl.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_rl.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_faostat_gt:
    output:
        temp("data/downloads/faostat/GT.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/Emissions_Totals_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_gt.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_gt.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_gt:
    input:
        "data/downloads/faostat/GT.zip",
    output:
        "data/downloads/faostat/GT.parquet",
    resources:
        runtime="2m",
        mem_mb=1500,
    log:
        "<logs>/shared/extract_faostat_gt.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_gt.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_faostat_fs:
    output:
        temp("data/downloads/faostat/FS.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/Food_Security_Data_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_fs.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_fs.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_fs:
    input:
        "data/downloads/faostat/FS.zip",
    output:
        "data/downloads/faostat/FS.parquet",
    resources:
        runtime="2m",
        mem_mb=1500,
    log:
        "<logs>/shared/extract_faostat_fs.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_fs.tsv"
    script:
        "../scripts/convert_faostat_to_parquet.py"


rule download_nhanes_fped:
    """Download the FPED Mean Amounts of Food Patterns Equivalents
    demographic table (Males/Females × age) for one NHANES cycle.

    The PDF is small (~160 KB) and stable; we cache it under
    `data/downloads/usda_fped/`. The `cycle` config value selects the
    release (e.g. "1720" for 2017-March 2020 Prepandemic).
    """
    output:
        "data/downloads/usda_fped/Table_1_FPED_MaleFemale_{cycle}.pdf",
    params:
        url=lambda wc: config["diet"]["nhanes"]["url"].format(cycle=wc.cycle),
    resources:
        runtime="5m",
        mem_mb=200,
    log:
        "<logs>/shared/download_nhanes_fped_{cycle}.log",
    benchmark:
        "<benchmarks>/shared/download_nhanes_fped_{cycle}.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_unsd_sdg:
    output:
        temp("data/downloads/unsd/SDG.zip"),
    params:
        url="https://unstats.un.org/sdgs/indicators/database/archive/2025_Q4.1_AllData_After_20251212_CSV.zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_unsd_sdg.log",
    benchmark:
        "<benchmarks>/shared/download_unsd_sdg.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_unsd_sdg:
    input:
        "data/downloads/unsd/SDG.zip",
    output:
        "data/downloads/unsd/SDG_12_3_1.csv",
    resources:
        runtime="5m",
        mem_mb=200,
    log:
        "<logs>/shared/extract_unsd_sdg.log",
    benchmark:
        "<benchmarks>/shared/extract_unsd_sdg.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        unzip -p "{input}" "*.csv" | {{ head -1; grep -E "AG_FLS_PCT|AG_FOOD_WST_PC"; }} > "{output}" 2> {log}
        """


rule download_gaez_yield_data:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_yield_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_yield_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_water_requirement_data:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
    output:
        "data/downloads/gaez_water_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/{config['data']['gaez']['water_requirement_var']}/"
            f"GAEZ-V5.{config['data']['gaez']['water_requirement_var']}."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res05')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_water_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_water_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_suitability_data:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
    output:
        "data/downloads/gaez_suitability_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tif",
    params:
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/{config['data']['gaez']['suitability_var']}/"
            f"GAEZ-V5.{config['data']['gaez']['suitability_var']}."
            f"{w.period}.{w.climate_model}.{w.climate_scenario}."
            f"{get_gaez_code(w.crop, 'res05')}.{w.input_level}{w.water_supply.upper()}LM.tif"
        ),
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_suitability_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_suitability_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_multiple_cropping_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_multiple_cropping_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_growing_season_start:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_growing_season_start_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_growing_season_start_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_growing_season_length:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_growing_season_length_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_growing_season_length_{climate_model}_{period}_{climate_scenario}_{input_level}_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_actual_yield:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
    output:
        "data/downloads/gaez_actual_yield_{water_supply}_{crop}.tif",
    params:
        # RES06-YLD: Actual yields, downscaled from FAOSTAT 2019-2021 3-year average
        # INPUT codes: WSI (irrigated), WSR (rainfed), WST (total)
        # Note: Uses different input naming convention than RES05
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES06-YLD/"
            f"GAEZ-V5.RES06-YLD.{get_gaez_code(w.crop, 'res06')}."
            f"WS{w.water_supply.upper()}.tif"
        ),
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_actual_yield_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_actual_yield_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_harvested_area:
    input:
        mapping="data/curated/gaez_crop_code_mapping.csv",
    output:
        "data/downloads/gaez_harvested_area_{water_supply}_{crop}.tif",
    params:
        # RES06-HAR: Harvested area, downscaled from FAOSTAT 2019-2021 3-year average
        # INPUT codes: WSI (irrigated), WSR (rainfed), WST (total)
        gcs_url=lambda w: (
            f"gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES06-HAR/"
            f"GAEZ-V5.RES06-HAR.{get_gaez_code(w.crop, 'res06')}."
            f"WS{w.water_supply.upper()}.tif"
        ),
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_harvested_area_{water_supply}_{crop}.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_harvested_area_{water_supply}_{crop}.tsv"
    shell:
        "pixi run gsutil cp {params.gcs_url} {output} > {log} 2>&1"


rule download_gaez_irrigated_landshare_map:
    output:
        "data/downloads/gaez_land_equipped_for_irrigation_share.tif",
    params:
        # LR-IRR: Share of land area equipped for irrigation
        gcs_url="gs://fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAP/GAEZ-V5.LR-IRR.tif",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gaez_irrigated_landshare_map.log",
    benchmark:
        "<benchmarks>/shared/download_gaez_irrigated_landshare_map.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_grassland_yield_data.log",
    benchmark:
        "<benchmarks>/shared/download_grassland_yield_data.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule retrieve_gdp_per_capita:
    """Retrieve GDP per capita data from IMF World Economic Outlook API.

    Missing data is imputed using UN M49 sub-regional means.
    """
    input:
        m49="data/curated/M49-codes.csv",
    params:
        countries=config["countries"],
        year=config["planning_horizon"],
    output:
        gdp="<processing>/{name}/gdp_per_capita.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/{name}/retrieve_gdp_per_capita.log",
    benchmark:
        "<benchmarks>/{name}/retrieve_gdp_per_capita.tsv"
    script:
        "../scripts/retrieve_gdp_per_capita.py"


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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_wpp_population.log",
    benchmark:
        "<benchmarks>/shared/download_wpp_population.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_waterfootprint_appendix.log",
    benchmark:
        "<benchmarks>/shared/download_waterfootprint_appendix.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_huang_irrigation_water.log",
    benchmark:
        "<benchmarks>/shared/download_huang_irrigation_water.tsv"
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
    resources:
        runtime="5m",
        mem_mb=200,
    log:
        "<logs>/shared/extract_huang_irrigation_water.log",
    benchmark:
        "<benchmarks>/shared/extract_huang_irrigation_water.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_fao_nutrient_conversion_table.log",
    benchmark:
        "<benchmarks>/shared/download_fao_nutrient_conversion_table.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_gleam_supplement.log",
    benchmark:
        "<benchmarks>/shared/download_gleam_supplement.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_luicube_grassland:
    params:
        lu_class=lambda w: w.lu_class,
        variable=lambda w: w.variable,
        year=str(config["baseline_year"]),
    output:
        "data/downloads/luicube/{lu_class}_{variable}.tif",
    resources:
        runtime="30m",
        mem_mb=1000,
    log:
        "<logs>/shared/download_luicube_{lu_class}_{variable}.log",
    benchmark:
        "<benchmarks>/shared/download_luicube_{lu_class}_{variable}.tsv"
    script:
        "../scripts/download_luicube_raster.py"


rule download_land_cover:
    output:
        temp("data/downloads/land_cover.zip"),
    params:
        dataset="satellite-land-cover",
        request={
            "variable": "all",
            "year": [str(config["baseline_year"])],
            "version": [config["data"]["land_cover"]["version"]],
        },
    resources:
        runtime="60m",
        mem_mb=500,
    log:
        "<logs>/shared/download_land_cover.log",
    benchmark:
        "<benchmarks>/shared/download_land_cover.tsv"
    script:
        "../scripts/download_land_cover.py"


rule extract_land_cover_class:
    input:
        "data/downloads/land_cover.zip",
    output:
        "data/downloads/land_cover_lccs_class.nc",
    resources:
        runtime="15m",
        mem_mb=13000,
    log:
        "<logs>/shared/extract_land_cover_class.log",
    benchmark:
        "<benchmarks>/shared/extract_land_cover_class.tsv"
    script:
        "../scripts/extract_land_cover_class.py"


rule download_biomass_cci:
    output:
        "data/downloads/esa_biomass_cci_v6_0.nc",
    params:
        url="https://dap.ceda.ac.uk/neodc/esacci/biomass/data/agb/maps/v6.0/netcdf/ESACCI-BIOMASS-L4-AGB-MERGED-10000m-fv6.0.nc?download=1",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_biomass_cci.log",
    benchmark:
        "<benchmarks>/shared/download_biomass_cci.tsv"
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
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_soilgrids_ocs.log",
    benchmark:
        "<benchmarks>/shared/download_soilgrids_ocs.tsv"
    script:
        "../scripts/download_soilgrids_ocs.py"


rule download_forest_carbon_accumulation_1km:
    output:
        "data/downloads/forest_carbon_accumulation_griscom_1km.tif",
    params:
        url="https://www.arcgis.com/sharing/rest/content/items/f950ea7878e143258a495daddea90cc0/data",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_forest_carbon_accumulation_1km.log",
    benchmark:
        "<benchmarks>/shared/download_forest_carbon_accumulation_1km.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_hayek_reforestation_biomes:
    output:
        "data/downloads/hayek_reforestation/pastures_coi_Geospatial.tif",
    params:
        url="https://zenodo.org/records/12688280/files/pastures_coi_Geospatial.tif",
    resources:
        runtime="10m",
        mem_mb=500,
    log:
        "<logs>/shared/download_hayek_reforestation_biomes.log",
    benchmark:
        "<benchmarks>/shared/download_hayek_reforestation_biomes.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule download_hayek_reforestation_pvc:
    output:
        "data/downloads/hayek_reforestation/pastures_coi_pvC_stack.tif",
    params:
        url="https://zenodo.org/records/12688280/files/pastures_coi_pvC_stack.tif",
    resources:
        runtime="10m",
        mem_mb=500,
    log:
        "<logs>/shared/download_hayek_reforestation_pvc.log",
    benchmark:
        "<benchmarks>/shared/download_hayek_reforestation_pvc.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule retrieve_eurostat_fodder:
    input:
        m49_codes="data/curated/M49-codes.csv",
    params:
        baseline_year_range=[
            config["baseline_year"]
            - config["fodder_decomposition"]["eurostat"]["averaging_years"] // 2,
            config["baseline_year"]
            + config["fodder_decomposition"]["eurostat"]["averaging_years"] // 2,
        ],
    output:
        "data/downloads/eurostat_fodder_production.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/shared/retrieve_eurostat_fodder.log",
    benchmark:
        "<benchmarks>/shared/retrieve_eurostat_fodder.tsv"
    script:
        "../scripts/retrieve_eurostat_fodder.py"


# Conditional rule: retrieve nutrition data from USDA if enabled in config
if config["data"]["usda"]["retrieve_nutrition"]:

    rule retrieve_usda_nutrition:
        input:
            mapping="data/curated/usda_food_mapping.csv",
            food_groups="data/curated/food_groups.csv",
        output:
            "data/curated/nutrition.csv",
        resources:
            runtime="30m",
            mem_mb=200,
        log:
            "<logs>/shared/retrieve_usda_nutrition.log",
        benchmark:
            "<benchmarks>/shared/retrieve_usda_nutrition.tsv"
        script:
            "../scripts/retrieve_usda_nutrition.py"
