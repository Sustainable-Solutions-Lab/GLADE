# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process IHME GBD mortality data into per-country death rates.

Downloads from IHME GBD Results Tool should include:
- Measure: Deaths (Rate)
- Causes: IHD, Stroke, Diabetes, CRC, Chronic respiratory diseases
- Ages: <1 year, 12-23 months, 2-4 years, 5-9 years, ..., 95+ years
- Sex: Both
- Year: matching baseline_year (nearest available used if exact year missing)
- Metric: Rate (per 100,000)
"""

import logging
from pathlib import Path

import pandas as pd
import pycountry

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Manual overrides for country names that pycountry can't match
COUNTRY_NAME_OVERRIDES = {
    "Bolivia (Plurinational State of)": "BOL",
    "Bonaire, Saint Eustatius and Saba": "BES",
    "Cabo Verde": "CPV",
    "Côte d'Ivoire": "CIV",
    "Democratic People's Republic of Korea": "PRK",
    "Democratic Republic of the Congo": "COD",
    "French Guiana": "GUF",  # Use French data for French Guiana
    "Iran (Islamic Republic of)": "IRN",
    "Lao People's Democratic Republic": "LAO",
    "Micronesia (Federated States of)": "FSM",
    "Niger": "NER",  # pycountry fuzzy search confuses with Nigeria (NGA)
    "Republic of Korea": "KOR",
    "Republic of Moldova": "MDA",
    "Republic of the Congo": "COG",
    "Saint Barthélemy": "BLM",
    "Saint Martin (French part)": "MAF",
    "Sint Maarten (Dutch part)": "SXM",
    "The former Yugoslav Republic of Macedonia": "MKD",
    "Türkiye": "TUR",
    "United Kingdom of Great Britain and Northern Ireland": "GBR",
    "United Republic of Tanzania": "TZA",
    "United States of America": "USA",
    "United States Virgin Islands": "VIR",
    "Venezuela (Bolivarian Republic of)": "VEN",
    "Viet Nam": "VNM",
}

# Map IHME cause names to model cause codes. "Stroke" maps to the model's
# ischemic-stroke-only cause (see prepare_relative_risks.CAUSE_MAP); the
# aggregate stroke deaths are scaled down by health.ischemic_stroke_share
# below. When the IHME re-download with "Ischemic stroke" specifically is
# in place, swap this entry to {"Ischemic stroke": "Stroke"} and set
# ischemic_stroke_share to 1.0.
CAUSE_MAP = {
    "Ischemic heart disease": "CHD",
    "Stroke": "Stroke",
    "Diabetes mellitus": "T2DM",
    "Colon and rectum cancer": "CRC",
    "Chronic respiratory diseases": "Resp_Dis",
    "All causes": "all-c",
}

# Map IHME age group names to model age codes
AGE_MAP = {
    "<1 year": "<1",
    "12-23 months": "1-4",  # Map to 1-4 bucket
    "2-4 years": "1-4",  # Map to 1-4 bucket
    "5-9 years": "5-9",
    "10-14 years": "10-14",
    "15-19 years": "15-19",
    "20-24 years": "20-24",
    "25-29 years": "25-29",
    "30-34 years": "30-34",
    "35-39 years": "35-39",
    "40-44 years": "40-44",
    "45-49 years": "45-49",
    "50-54 years": "50-54",
    "55-59 years": "55-59",
    "60-64 years": "60-64",
    "65-69 years": "65-69",
    "70-74 years": "70-74",
    "75-79 years": "75-79",
    "80-84 years": "80-84",
    "85-89 years": "85-89",
    "90-94 years": "90-94",
    "95+ years": "95+",
    "95 plus": "95+",
    "All ages": "all-a",
}


def map_country_name_to_iso3(name: str) -> str | None:
    """Map IHME country name to ISO3 code using pycountry + manual overrides."""
    # Check manual overrides first
    if name in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[name]

    # Try pycountry fuzzy search
    try:
        matches = pycountry.countries.search_fuzzy(name)
        if matches:
            return matches[0].alpha_3
    except LookupError:
        pass

    return None


def main() -> None:
    snakemake = globals().get("snakemake")  # type: ignore
    if snakemake is None:
        raise RuntimeError("This script must run via Snakemake")

    input_path = snakemake.input["gbd_mortality"]
    output_path = snakemake.output["mortality"]
    reference_year = int(snakemake.params["reference_year"])
    ischemic_stroke_share = float(snakemake.params["ischemic_stroke_share"])
    set(snakemake.params["countries"])
    set(snakemake.params["causes"])

    logger.info("Reading GBD mortality data from %s", input_path)
    df = pd.read_csv(input_path)

    # Validate expected columns
    required_cols = {
        "location_name",
        "age_name",
        "cause_name",
        "year",
        "val",
        "metric_name",
    }
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in GBD data: {missing}")

    # Filter to death rate metric
    df = df[df["metric_name"] == "Rate"].copy()
    if df.empty:
        raise ValueError("No 'Rate' metric found in GBD data")

    # Filter to reference year (the downloaded file should contain this year)
    df = df[df["year"] == reference_year].copy()
    if df.empty:
        available_years = sorted(
            pd.read_csv(input_path, usecols=["year"])["year"].unique()
        )
        raise ValueError(
            f"No data for year {reference_year} in {input_path}. "
            f"Available years: {available_years}. "
            f"Download death rates for year {reference_year} from "
            f"https://vizhub.healthdata.org/gbd-results/"
        )

    # Map country names to ISO3 (cache unique values to avoid repeated fuzzy searches)
    logger.info("Mapping country names to ISO3 codes...")
    unique_countries = df["location_name"].unique()
    country_map = {name: map_country_name_to_iso3(name) for name in unique_countries}
    df["country_iso3"] = df["location_name"].map(country_map)

    unmapped = df[df["country_iso3"].isna()]["location_name"].unique()
    if len(unmapped) > 0:
        logger.warning("%d countries could not be mapped to ISO3:", len(unmapped))
        for name in sorted(unmapped)[:10]:
            logger.warning("  - %s", name)
        if len(unmapped) > 10:
            logger.warning("  ... and %d more", len(unmapped) - 10)

    # Drop unmapped countries
    df = df[df["country_iso3"].notna()].copy()

    # Map causes
    df["cause_code"] = df["cause_name"].map(CAUSE_MAP)
    unmapped_causes = df[df["cause_code"].isna()]["cause_name"].unique()
    if len(unmapped_causes) > 0:
        logger.warning("%d causes not mapped:", len(unmapped_causes))
        for cause in sorted(unmapped_causes):
            logger.warning("  - %s", cause)

    df = df[df["cause_code"].notna()].copy()

    # Map age groups
    df["age_code"] = df["age_name"].map(AGE_MAP)
    unmapped_ages = df[df["age_code"].isna()]["age_name"].unique()
    if len(unmapped_ages) > 0:
        logger.warning("%d age groups not mapped:", len(unmapped_ages))
        for age in sorted(unmapped_ages):
            logger.warning("  - %s", age)

    df = df[df["age_code"].notna()].copy()

    # Check for multiple IHME age buckets mapping to same model bucket (e.g., 1-4)
    # We need to aggregate them by taking population-weighted average
    # For death rates, we'll compute a simple average as an approximation
    # (ideally would weight by age-specific population, but we don't have that here)
    logger.info("Checking for age bucket aggregation needs...")
    duplicate_keys = df.groupby(["country_iso3", "cause_code", "age_code"]).size()
    needs_aggregation = duplicate_keys[duplicate_keys > 1]

    if len(needs_aggregation) > 0:
        logger.info(
            "Found %d age buckets needing aggregation (multiple IHME ages -> single model age)",
            len(needs_aggregation),
        )
        logger.info(
            "Aggregating by simple average (assumes roughly equal population in sub-buckets)"
        )

        # Group and average
        df = (
            df.groupby(["country_iso3", "cause_code", "age_code", "year"])
            .agg({"val": "mean"})
            .reset_index()
        )

    # Scale aggregate Stroke deaths down to the ischemic share. The
    # relative-risk pipeline restricts diet-attributable stroke to the
    # ischemic subtype (atherosclerotic pathway diet acts on; null/weak
    # evidence for hemorrhagic subtypes); applying the diet PAF to
    # all-stroke mortality would over-attribute burden. Default value is
    # the GBD global ischemic share; remove after the IHME re-download
    # with the "Ischemic stroke" cause filter lands (set the share to 1.0
    # or drop this block entirely once CAUSE_MAP is keyed on
    # "Ischemic stroke").
    stroke_mask = df["cause_code"] == "Stroke"
    if stroke_mask.any() and ischemic_stroke_share != 1.0:
        df.loc[stroke_mask, "val"] = df.loc[stroke_mask, "val"] * ischemic_stroke_share
        logger.info(
            "Scaled %d aggregate Stroke rows by ischemic_stroke_share=%.3f",
            int(stroke_mask.sum()),
            ischemic_stroke_share,
        )

    # Convert rate from per 100,000 to per 1,000
    df["death_rate_per_1000"] = df["val"] / 100.0

    # Build output dataframe matching old format: age, cause, country, year, value
    output = df[
        ["age_code", "cause_code", "country_iso3", "death_rate_per_1000"]
    ].copy()
    output.columns = ["age", "cause", "country", "value"]
    # Use reference year in output to ensure consistency across datasets
    output.insert(3, "year", reference_year)

    # Sort for readability
    output = output.sort_values(["country", "cause", "age"]).reset_index(drop=True)

    # Fill in missing countries using proxy data from similar countries
    # This is for territories/dependencies that don't have separate IHME data
    COUNTRY_PROXIES = {
        "ASM": "WSM",  # American Samoa -> Samoa (if needed)
        "GUF": "FRA",  # French Guiana -> France
        "PRI": "USA",  # Puerto Rico -> USA (if needed)
        "SOM": "ETH",  # Somalia -> Ethiopia (if needed)
    }

    required_countries = set(snakemake.params["countries"])
    output_countries = set(output["country"].unique())
    missing_countries = required_countries - output_countries

    if missing_countries:
        filled = []
        still_missing = []
        for missing in sorted(missing_countries):
            if missing in COUNTRY_PROXIES:
                proxy = COUNTRY_PROXIES[missing]
                if proxy in output_countries:
                    # Duplicate proxy country's data for the missing country
                    proxy_data = output[output["country"] == proxy].copy()
                    proxy_data["country"] = missing
                    output = pd.concat([output, proxy_data], ignore_index=True)
                    filled.append(f"{missing} (using {proxy} data)")
                else:
                    still_missing.append(missing)
            else:
                still_missing.append(missing)

        if filled:
            logger.info("Filled %d missing countries using proxies:", len(filled))
            for entry in filled:
                logger.info("  - %s", entry)

        # Update missing list after filling
        output_countries = set(output["country"].unique())
        missing_countries = required_countries - output_countries

    # Validate that we have all required countries and causes
    required_causes = set(snakemake.params["causes"])
    output_causes = set(output["cause"].unique())
    if missing_countries:
        raise ValueError(
            f"[prepare_gbd_mortality] ERROR: Mortality data is missing {len(missing_countries)} required countries: "
            f"{sorted(missing_countries)[:20]}{'...' if len(missing_countries) > 20 else ''}. "
            f"Please ensure the IHME GBD download includes all countries listed in config."
        )

    missing_causes = required_causes - output_causes
    if missing_causes:
        raise ValueError(
            f"[prepare_gbd_mortality] ERROR: Mortality data is missing {len(missing_causes)} required causes: "
            f"{sorted(missing_causes)}. Available causes: {sorted(output_causes)}. "
            f"Please ensure the IHME GBD download includes all causes listed in config.health.causes."
        )

    logger.info("✓ Validation passed: all required countries and causes present")

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Save without header to match old format
    output.to_csv(output_path, index=False, header=False)

    logger.info("Wrote %d rows to %s", len(output), output_path)
    logger.info("Countries: %d", output["country"].nunique())
    logger.info("Causes: %s", sorted(output["cause"].unique()))
    logger.info("Age groups: %s", sorted(output["age"].unique()))


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
