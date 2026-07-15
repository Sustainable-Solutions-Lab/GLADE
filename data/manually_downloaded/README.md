<!--
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# Manually Downloaded Data

This directory contains datasets that must be manually downloaded because they:
- Require interactive query interfaces (e.g., IHME GBD Results Tool)
- Have terms-of-service that preclude automated bulk downloads
- Require authentication or registration

All of these are IHME GBD datasets, and all are needed **only** when the
health module is enabled (`health.enabled: true`) or when the baseline diet
anchors to GBD (`diet.anchor_groups_to_gbd`). With both off — the default —
the workflow runs without any manually-downloaded data. The baseline diet
itself needs none: the default GDD-IA source is fetched automatically from
Zenodo (see `docs/data_sources.rst`).

## Current Files

### IHME-GBD_2023-death-rates-2020.csv

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
  - Ischemic stroke
  - Diabetes mellitus
  - Colon and rectum cancer
  - Chronic respiratory diseases
- **Location:** Choose option to "Select all countries and territories"
- **Age groups:** <1 year, 12-23 months, 2-4 years, 5-9 years, 10-14 years, 15-19 years, ..., 95+ years
- **Sex:** Both
- **Year:** Must match `baseline_year` in the config (default: 2020)

The following permalink reproduces this query for year 2020: https://vizhub.healthdata.org/gbd-results?params=gbd-api-2023-permalink/ab3e7b526315599bf5cabbfe6c34e104. Adjust the year if using a different `baseline_year`.

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

### IHME_GBD_2023_RISK_EXPOSURE_DIET_1 / _2 (directories)

**Source:** IHME Global Burden of Disease Study 2023
**Download:** https://ghdx.healthdata.org/record/ihme-data/gbd-2023-dietary-risk-exposure-estimates

Direct file links:
- https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2023_RISK_EXPOSURE_DIET_1.zip
- https://ghdx.healthdata.org/sites/default/files/record-attached-files/IHME_GBD_2023_RISK_EXPOSURE_DIET_2.zip

Downloading requires an IHME account (the direct links redirect to an IHME
login, so an authenticated browser session is needed; the workflow cannot
fetch them automatically).

**Dataset details:**
- **Content:** Country-level dietary risk exposure estimates (mean exposure + uncertainty bounds) for 15 dietary risk factors, split across two archives (8 + 7 factors)
- **Risk factors:** Calcium, fiber, fruits, legumes, milk, nuts and seeds, seafood omega-3, omega-6 PUFA, processed meat, red meat, sodium, sugar-sweetened beverages, trans fat, vegetables, whole grains
- **Coverage:** 204 countries and territories (plus subnational units), 1990-2023, by 5-year age bucket and sex
- **Format:** Two ZIP archives, each one CSV per risk factor (~90 MB each), named `IHME_GBD_2023_RISK_EXPOSURE_DIET_{RISK}_Y2025M10D10.CSV`
- **Use case:** Anchor source for GBD risk-factor food groups in the baseline diet (fruits, vegetables, whole_grains, legumes, nuts_seeds, red_meat)

**Download steps:**
1. Visit https://ghdx.healthdata.org/record/ihme-data/gbd-2023-dietary-risk-exposure-estimates
2. Log in to your IHME account
3. Download both `IHME_GBD_2023_RISK_EXPOSURE_DIET_1.zip` and `_2.zip`
4. Extract each ZIP to get the individual CSV files
5. Place the extracted directories as `data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_1` and `_2`

**Processing:** Processed via `workflow/scripts/prepare_gbd_food_group_intake.py`.
Unlike the GBD 2019 release, the 2023 files provide no ready-made "25 plus"
both-sex aggregate, so the script reconstructs the adult (25+) both-sex
exposure by population-weighting the adult 5-year age buckets (using
per-country age-bucket population) and averaging the two sexes. National
locations are selected by `location_id` (from the death-rates file) because
the bulk files also contain subnational units whose names collide with
countries (e.g. "Georgia").

**License:** IHME Free-of-Charge Non-commercial User Agreement

**Citation:**
> Global Burden of Disease Collaborative Network. Global Burden of Disease Study 2023 (GBD 2023) Dietary Risk Exposure Estimates. Seattle, United States of America: Institute for Health Metrics and Evaluation (IHME), 2025.

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
4. Save as `IHME-GBD_2023-death-rates-{year}.csv` (the year in the filename must match `baseline_year`)
6. Rerun workflow: `tools/smk processing/{name}/gbd_mortality_rates.csv`

### IHME GBD Dietary Risk Exposure Estimates

When new GBD dietary risk exposure data is released:

1. Visit the GBD dietary risk exposure record (e.g. https://ghdx.healthdata.org/record/ihme-data/gbd-2023-dietary-risk-exposure-estimates)
2. Log in and download the ZIP archive(s)
3. Extract and replace the `IHME_GBD_2023_RISK_EXPOSURE_DIET_1` / `_2` directories
4. Update the directory names and filename token (`Y2025M10D10`) in the `prepare_gbd_food_group_intake` rule and the `ADULT_AGE_ID_TO_LABEL` / risk-factor map in `prepare_gbd_food_group_intake.py` if the schema or naming convention changes

### IHME GBD Relative Risks

When new GBD relative risks data is released:

1. Visit https://ghdx.healthdata.org/record/ihme-data/gbd-2019-relative-risks
2. Download the XLSX file (Appendix Table 7a)
3. Replace `IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX` (or create new file with updated year)
4. Update `workflow/Snakefile` rule `prepare_relative_risks` if filename changes
5. Rerun workflow: `tools/smk processing/{name}/relative_risks.csv`
