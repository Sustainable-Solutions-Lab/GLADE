<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# Manually Downloaded Data

This directory contains datasets that must be manually downloaded because they:
- Require interactive query interfaces (e.g., IHME GBD Results Tool)
- Have terms-of-service that preclude automated bulk downloads
- Require authentication or registration

## Current Files

### IHME-GBD_2023-dealth-rates.csv

**Source:** IHME Global Burden of Disease Study 2023
**Download:** https://vizhub.healthdata.org/gbd-results/

Viewing and downloading these results requires a user account on the healthdata.org website.

**Query parameters:**
- **GBD Estimate:** Cause of death or injury
- **Measure:** Deaths (Rate per 100,000)
- **Metric:** Rate
- **Causes:**
  - All causes
  - Ischemic heart disease
  - Stroke
  - Diabetes mellitus
  - Colon and rectum cancer
  - Chronic respiratory diseases
- **Location:** Choose option to "Select all countries and territories"
- **Age groups:** <1 year, 12-23 months, 2-4 years, 5-9 years, 10-14 years, 15-19 years, ..., 95+ years
- **Sex:** Both
- **Year:** 2023 (or latest available)

This specific query can also be found at the following URL: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/05de3cfb56eafc99f2cc8e135644b81f

**Processing:** The Snakemake workflow automatically processes this file via `workflow/scripts/prepare_gbd_mortality.py` to:
1. Map country names to ISO3 codes
2. Map IHME causes to model cause codes
3. Aggregate sub-buckets (12-23 months + 2-4 years → 1-4)
4. Convert rates from per 100k to per 1k
5. Output to `processing/{name}/gbd_mortality_rates.csv`

**License:** IHME Free-of-Charge Non-commercial User Agreement

**Citation:**
> Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Results. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2025. Available from https://vizhub.healthdata.org/gbd-results/.

---

### GDD-dietary-intake (directory)

**Source:** Global Dietary Database (Tufts University)
**Download:** https://globaldietarydatabase.org/data-download

Downloading requires free user registration and acceptance of terms of use.

**Dataset details:**
- **Content:** Country-level mean daily dietary intake (g/day per capita) for major food groups and dietary risk factors
- **Food groups:** Vegetables, fruits (temperate/tropical/starchy), whole grains, legumes, nuts & seeds, red meat (beef/lamb/pork), processed meat, seafood (fish types + shellfish), grains, dairy, eggs, oils, and others
- **Coverage:** 185+ countries with data circa 2015-2020
- **Format:** CSV (~1.6 GB) with columns for country, food item, mean intake, standard error, and uncertainty bounds
- **Use case:** Baseline dietary patterns for health risk assessment

**Processing:** The Snakemake workflow processes this file via `workflow/scripts/prepare_gdd_dietary_intake.py` to:
1. Filter to baseline (BMK) scenario equivalent
2. Map country names to ISO3 codes
3. Map GDD food items to model dietary risk factors
4. Aggregate multiple food items to risk factor categories
5. Output to `processing/{name}/dietary_intake_baseline.csv`

**License:** Free for non-commercial research, teaching, and private study with attribution. May not be redistributed or used commercially without Tufts permission.

**Citation:**
> Global Dietary Database. Dietary intake data by country. https://www.globaldietarydatabase.org/ [Accessed YYYY-MM-DD].

**Attribution format (when publishing results):**
> Data provided by Global Dietary Database. https://www.globaldietarydatabase.org/ [Date accessed].

---

### IHME_GBD_2019_DIET_RISK_1990_2019_DATA (directory)

**Source:** IHME Global Burden of Disease Study 2019
**Download:** https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019

Direct file link: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip

Downloading requires an IHME account.

**Dataset details:**
- **Content:** Country-level dietary risk exposure estimates (mean exposure, uncertainty bounds, summary exposure values) for 15 dietary risk factors
- **Risk factors:** Calcium, fiber, fruit, legumes, milk, nuts, omega-3, processed meat, PUFA, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains
- **Coverage:** 204 countries and territories, 1990–2019, by age group and sex
- **Format:** ZIP archive (~700 MB) containing one CSV per risk factor (~47 MB each), named `IHME_GBD_2019_DIET_RISK_1990_2019_{RISK}_Y2021M09D27.CSV`
- **Use case:** Supplements GDD baseline dietary intake data with GBD exposure estimates

**Download steps:**
1. Visit https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019
2. Log in to your IHME account
3. Download `IHME_GBD_2019_DIET_RISK_1990_2019_DATA.zip`
4. Extract the ZIP file to get the individual CSV files
5. Place the extracted directory as `data/manually_downloaded/IHME_GBD_2019_DIET_RISK_1990_2019_DATA`

**Processing:** Not yet integrated into the Snakemake workflow.

**License:** IHME Free-of-Charge Non-commercial User Agreement

**Citation:**
> Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Dietary Risk Exposure Estimates 1990-2019. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2021.

---

### IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX

**Source:** IHME Global Burden of Disease Study 2019
**Download:** https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks

Direct file link: https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX

**Query parameters:**
- **Measure:** Relative Risk
- **Risk factors:** Diet-related risks (high red meat, low vegetables, low fruits, etc.)
- **Causes:** Ischemic heart disease, Stroke, Diabetes, Colon and rectum cancer, Chronic respiratory diseases
- **Age groups:** All age groups
- **Sex:** Both
- **Year:** 2019

**Processing:** The Snakemake workflow processes this file via `workflow/scripts/prepare_relative_risks.py` to:
1. Extract relative risk values for dietary risk factors
2. Map age groups to model age buckets
3. Map causes to model cause codes
4. Output to `processing/{name}/relative_risks.csv`

**License:** IHME Free-of-Charge Non-commercial User Agreement

**Citation:**
> Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2019 (GBD 2019) Relative Risks. Seattle, United States: Institute for Health Metrics and Evaluation (IHME), 2020. Available from https://vizhub.healthdata.org/gbd-results/.

---

## Updating Data

### IHME GBD Mortality Data

When new GBD data is released:

1. Visit https://vizhub.healthdata.org/gbd-results/
2. Configure query with parameters above
3. Download as CSV
4. Replace `IHME-GBD_2023-dealth-rates.csv` (or create new file with updated year)
5. Update `workflow/Snakefile` rule `prepare_gbd_mortality` if filename changes
6. Rerun workflow: `tools/smk processing/{name}/gbd_mortality_rates.csv`

### GDD Dietary Data

When updating GDD data:

1. Visit https://globaldietarydatabase.org/data-download
2. Log in with user account
3. Download the complete dataset CSV
4. Replace `GDD-dietary-intake.csv` in this directory
5. Update access date in citations and documentation
6. Rerun workflow: `tools/smk processing/{name}/dietary_intake_baseline.csv`

### IHME GBD Dietary Risk Exposure Estimates

When new GBD dietary risk exposure data is released:

1. Visit https://ghdx.healthdata.org/record/ihme-data/gbd-2019-dietary-risk-exposure-estimates-1990-2019
2. Log in and download the ZIP file
3. Extract and replace `IHME_GBD_2019_DIET_RISK_1990_2019_DATA` directory in this directory
4. Update filenames in workflow rules if naming convention changes

### IHME GBD Relative Risks

When new GBD relative risks data is released:

1. Visit https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks
2. Download the XLSX file (Appendix Table 7a)
3. Replace `IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX` (or create new file with updated year)
4. Update `workflow/Snakefile` rule `prepare_relative_risks` if filename changes
5. Rerun workflow: `tools/smk processing/{name}/relative_risks.csv`
