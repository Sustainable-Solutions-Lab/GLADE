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
        fallback_aliases=config["animal_costs"]["fallback_aliases"],
        fallback_values_usd_per_t=config["animal_costs"]["fallback_values_usd_per_t"],
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


# FBSH = historical Food Balance Sheets (1961-2013, old methodology).
# Used as a fallback for countries that the new FBS dataset (2010-) does
# not cover (Japan, Chad, Mali, Benin, Togo, Burundi, Eritrea, Somalia,
# Central African Republic, etc.). For these countries we use their
# latest available FBSH year (typically 2013) per-capita supply values.
rule download_faostat_fbsh:
    output:
        temp("data/downloads/faostat/FBSH.zip"),
    params:
        url="https://bulks-faostat.fao.org/production/FoodBalanceSheetsHistoric_E_All_Data_(Normalized).zip",
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_faostat_fbsh.log",
    benchmark:
        "<benchmarks>/shared/download_faostat_fbsh.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


rule extract_faostat_fbsh:
    input:
        "data/downloads/faostat/FBSH.zip",
    output:
        "data/downloads/faostat/FBSH.parquet",
    resources:
        runtime="2m",
        mem_mb=4000,
    log:
        "<logs>/shared/extract_faostat_fbsh.log",
    benchmark:
        "<benchmarks>/shared/extract_faostat_fbsh.tsv"
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
            f"{get_gaez_res02_code(w.crop)}.{w.input_level}{w.water_supply.upper()}LM.tif"
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
            f"{get_gaez_res02_code(w.crop)}.{w.input_level}{w.water_supply.upper()}LM.tif"
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


rule download_cropgrids_nc_maps:
    """Download the CROPGRIDS v1.08 NetCDF bundle from Figshare.

    Source: Tang et al. (2023), "CROPGRIDS", https://doi.org/10.6084/m9.figshare.22491997.
    License: CC BY 4.0. The zip ships per-crop 0.05° harvested and physical
    crop area rasters for ~170 crops; we extract only the crops listed in
    ``config["cropgrids_crops"]`` (see ``extract_cropgrids_nc`` below).
    """
    output:
        "data/downloads/cropgrids_v1_08_nc_maps.zip",
    params:
        article_id=22491997,
        file_name="CROPGRIDSv1.08_NC_maps.zip",
        show_progress=config["downloads"]["show_progress"],
    resources:
        runtime="60m",
        mem_mb=500,
    log:
        "<logs>/shared/download_cropgrids_nc_maps.log",
    benchmark:
        "<benchmarks>/shared/download_cropgrids_nc_maps.tsv"
    script:
        "../scripts/download_figshare_file.py"


rule extract_cropgrids_nc:
    """Unpack a single crop's CROPGRIDS NetCDF from the bundle zip.

    The mapping CSV resolves model crop name → CROPGRIDS .nc filename
    (e.g. apple → CROPGRIDSv1.08_apple.nc). Crops listed in
    ``config["cropgrids_crops"]`` must have a non-empty entry there
    (enforced by ``validate_cropgrids_crops``).
    """
    input:
        zip_path="data/downloads/cropgrids_v1_08_nc_maps.zip",
        mapping="data/curated/cropgrids_crop_mapping.csv",
    output:
        "<processing>/shared/cropgrids_nc/CROPGRIDSv1.08_{crop}.nc",
    resources:
        runtime="5m",
        mem_mb=500,
    log:
        "<logs>/shared/extract_cropgrids_{crop}.log",
    shell:
        r"""
        mapped=$(awk -F, -v c="{wildcards.crop}" 'NR>1 && $1==c {{print $2}}' {input.mapping})
        if [ -z "$mapped" ]; then
          echo "No cropgrids_name for crop {wildcards.crop} in {input.mapping}" >&2
          exit 1
        fi
        mkdir -p "$(dirname {output})"
        unzip -oj {input.zip_path} "CROPGRIDSv1.08_NC_maps/CROPGRIDSv1.08_${{mapped}}.nc" -d "$(dirname {output})" > {log} 2>&1
        if [ "$mapped" != "{wildcards.crop}" ]; then
          mv "$(dirname {output})/CROPGRIDSv1.08_${{mapped}}.nc" {output}
        fi
        """


rule download_grassland_yield_data:
    """Retrieve historical managed-grassland yield from ISIMIP2a / LPJmL.

    Pinned to ISIMIP2a agriculture-sector LPJmL ``yield-mgr-noirr-default``
    (WATCH forcing, no bias correction, variable CO2, 1971-2001, 0.5 deg).
    The full managed-grassland yield catalogue is browsable at
    https://data.isimip.org/search/crop/mgr/variable/yield/irrigation/noirr/.

    License: CC BY 4.0. ISIMIP releases agriculture-sector LPJmL output
    under CC BY 4.0; only LPJ-GUESS in that sector carries CC BY-NC 4.0.
    See https://www.isimip.org/gettingstarted/terms-of-use/licenses-publicly-available-isimip-data/.
    Attribution: cite the ISIMIP2a agriculture data archive
    (https://doi.org/10.5880/PIK.2017.006) and Schaphoff et al. (2018,
    https://doi.org/10.5194/gmd-11-1343-2018) for LPJmL.
    """
    output:
        "data/downloads/grassland_yield_historical.nc4",
    params:
        url="https://files.isimip.org/ISIMIP2a/OutputData/agriculture/LPJmL/watch/historical/lpjml_watch_nobc_hist_co2_yield-mgr-noirr-default_global_annual_1971_2001.nc4",
        expected_size_bytes=6277071,
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
        actual_size=$(stat -c%s "{output}")
        if [ "$actual_size" != "{params.expected_size_bytes}" ]; then
            echo "ISIMIP grassland file size mismatch: got $actual_size, expected {params.expected_size_bytes}." >> {log}
            exit 1
        fi
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
    # Copernicus ESA CCI land cover (lccs_class only) for the baseline year,
    # fetched from our Zenodo mirror (CC-BY-4.0) so builds need no Copernicus
    # CDS API key. Refresh the mirror with tools/mirror_land_cover.py and update
    # config['data']['land_cover']['zenodo_record'] to the new record id.
    output:
        "data/downloads/land_cover_lccs_class.nc",
    params:
        url=(
            f"https://zenodo.org/records/{config['data']['land_cover']['zenodo_record']}"
            f"/files/land_cover_lccs_class_{config['baseline_year']}"
            f"_{config['data']['land_cover']['version']}.nc?download=1"
        ),
    resources:
        runtime="30m",
        mem_mb=500,
    log:
        "<logs>/shared/download_land_cover.log",
    benchmark:
        "<benchmarks>/shared/download_land_cover.tsv"
    shell:
        r"""
        mkdir -p "$(dirname {output})"
        curl -L --fail --progress-bar -o "{output}" "{params.url}" > {log} 2>&1
        """


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


rule retrieve_burden_of_proof:
    """Download GBD 2023 dietary RR curves from the IHME Burden-of-Proof tool.

    Config-independent: fetches every mapped (risk, cause) pair the tool offers
    (config.health.gbd_rei_id x gbd_cause_id). No login required; passes the
    Cloudflare edge check with a browser User-Agent. IHME data are
    non-redistributable, so the output lives in the gitignored download cache.
    Fetched automatically when the cached CSV is missing; since params track
    only the GBD identifiers, it re-fetches only if those identifiers change.
    """
    params:
        gbd_rei_id=config["health"]["gbd_rei_id"],
        gbd_cause_id=config["health"]["gbd_cause_id"],
    output:
        curves="data/downloads/burden_of_proof/bop_rr_curves.csv",
    resources:
        runtime="15m",
        mem_mb=200,
    log:
        "<logs>/shared/retrieve_burden_of_proof.log",
    benchmark:
        "<benchmarks>/shared/retrieve_burden_of_proof.tsv"
    script:
        "../scripts/retrieve_burden_of_proof.py"


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
