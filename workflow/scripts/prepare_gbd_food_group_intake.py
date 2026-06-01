#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Process IHME GBD 2023 dietary risk exposure data.

Reads per-risk-factor CSV files from the GBD 2023 dietary risk exposure
release (delivered as two ZIP archives, here unpacked into two sibling
directories), extracts country-level adult intake estimates, maps GBD
risk factor names to model food groups, and outputs a consolidated
exposure file.

Input:
    - Two directories of GBD 2023 risk-exposure CSVs (one file per risk
      factor; the release splits the 15 factors across two archives)
    - The GBD 2023 death-rates CSV, used purely as the canonical list of
      national (level-3) GBD location ids. The bulk exposure files also
      contain subnational units (US states, UK nations, Indian/Pakistani
      provinces, ...) whose names collide with countries (e.g. "Georgia"
      the US state vs. the country); filtering by location_id avoids
      double-counting that name-based matching would introduce.
    - Per-country age-bucket population (population_age.csv), used to
      reconstruct the adult (25+) exposure (see below).
    - Reference year from config

Output:
    - CSV with columns: food_group, country, consumption_g_per_day, lower, upper

GBD 2023 exposure CSV columns:
    age_group_id, age_group_name, sex_id, sex, year_id, location_id,
    location_name, measure_id, measure, mean, lower, upper

Notes on aggregation:
    - Age: the GBD 2019 release shipped a ready-made "25 plus" aggregate
      (age_group_id 157). The 2023 bulk files do NOT; their only age
      aggregates are "All Ages" (id 22) and "Age-standardized" (id 27),
      both of which divide by the FULL population (including <25, for whom
      these adult dietary risks carry zero exposure) and so fall well
      below every adult 5-year bucket - not the adult mean we need. We
      therefore reconstruct the 25+ exposure ourselves by
      population-weighting the adult 5-year buckets (25-29 .. 95+) using
      per-country age-bucket population for the reference year.
    - Sex: the 2023 bulk files no longer ship an IHME-computed "Both"
      aggregate (only Male and Female), and our population table is not
      sex-split. We average the two sexes unweighted within each age
      bucket before age-weighting. Adult sex ratios are close to parity,
      so the error relative to a sex-population-weighted both-sex mean is
      a few percent at most, which the downstream use (averaging with GDD
      intake for anchored food groups, and cross-validation) tolerates.
"""

import logging
from pathlib import Path

import pandas as pd
import pycountry

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Reuse country name overrides from prepare_gbd_mortality.py
COUNTRY_NAME_OVERRIDES = {
    "Bolivia (Plurinational State of)": "BOL",
    "Bonaire, Saint Eustatius and Saba": "BES",
    "Cabo Verde": "CPV",
    "Côte d'Ivoire": "CIV",
    "Democratic People's Republic of Korea": "PRK",
    "Democratic Republic of the Congo": "COD",
    "French Guiana": "GUF",
    "Iran (Islamic Republic of)": "IRN",
    "Lao People's Democratic Republic": "LAO",
    "Micronesia (Federated States of)": "FSM",
    "Niger": "NER",
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

# Map GBD 2023 risk factor file tokens to model food group names.
# The token is the part of the filename between "..._DIET_" and the
# "_Y2025..." date suffix (e.g. "LOW_IN_FRUITS", "HIGH_IN_RED_MEAT").
# Only risk factors relevant for food group total estimation are included.
GBD_RISK_TO_FOOD_GROUP = {
    "LOW_IN_FRUITS": "fruits",
    "LOW_IN_VEGETABLES": "vegetables",
    "LOW_IN_WHOLE_GRAINS": "whole_grains",
    "LOW_IN_LEGUMES": "legumes",
    "LOW_IN_NUTS_AND_SEEDS": "nuts_seeds",
    "HIGH_IN_RED_MEAT": "red_meat",
    # HIGH_IN_PROCESSED_MEAT is intentionally not folded into GBD's
    # red_meat: validation against FAOSTAT slaughter-volume production
    # showed that adding it pushes red_meat consumption ~30% above what
    # slaughter can support globally (GBD's Bayesian-smoothed exposures
    # over-estimate processed-meat intake relative to FAOSTAT QCL
    # volumes). GDD-IA's processed-meat ``othr_meat`` already folds into
    # red_meat upstream in prepare_gdd_ia_dietary_intake.py; GBD anchoring
    # on red_meat in estimate_baseline_diet.py overrides that fold for
    # countries where GBD reports red_meat. See docs/health.rst and
    # docs/data_sources.rst for the trade-off.
    "LOW_IN_MILK": "milk",  # Cross-validation only; not used for dairy group total
}

# Adult 5-year GBD age-group ids -> population_age.csv bucket labels.
# These are summed (population-weighted) into the 25+ exposure. The
# aggregate ids "All Ages" (22) and "Age-standardized" (27) are excluded:
# both divide by the full population and so understate adult intake.
ADULT_AGE_ID_TO_LABEL = {
    10: "25-29",
    11: "30-34",
    12: "35-39",
    13: "40-44",
    14: "45-49",
    15: "50-54",
    16: "55-59",
    17: "60-64",
    18: "65-69",
    19: "70-74",
    20: "75-79",
    30: "80-84",
    31: "85-89",
    32: "90-94",
    235: "95+",
}


def map_country_name_to_iso3(name: str) -> str | None:
    """Map GBD location name to ISO3 code using pycountry + manual overrides."""
    if name in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[name]
    try:
        matches = pycountry.countries.search_fuzzy(name)
        if matches:
            return matches[0].alpha_3
    except LookupError:
        pass
    return None


def _wmean(values: pd.Series, weights: pd.Series) -> float:
    """Population-weighted mean, ignoring zero/NaN total weight."""
    total = float(weights.sum())
    if total <= 0:
        return float("nan")
    return float((values * weights).sum() / total)


def parse_risk_token(stem: str) -> str | None:
    """Extract the risk-factor token from a GBD 2023 exposure filename stem.

    Pattern: IHME_GBD_2023_RISK_EXPOSURE_DIET_{RISK}_Y2025M10D10
    The token sits between the "DIET" part and the "Y20..." date suffix.
    """
    parts = stem.split("_")
    if "DIET" not in parts:
        return None
    start = parts.index("DIET") + 1
    for i in range(start, len(parts)):
        if parts[i].startswith("Y20"):
            return "_".join(parts[start:i])
    return None


def build_national_location_map(death_rates_path: str) -> dict[int, str]:
    """Map national GBD location_id -> ISO3 from the death-rates file.

    The death-rates query selects "all countries and territories", so its
    location set is exactly the 204 national (level-3) GBD locations. We
    use it to restrict the bulk exposure files to national rows.
    """
    ref = pd.read_csv(
        death_rates_path, usecols=["location_id", "location_name"]
    ).drop_duplicates()
    loc_to_iso3: dict[int, str] = {}
    for location_id, location_name in ref.itertuples(index=False):
        iso3 = map_country_name_to_iso3(location_name)
        if iso3 is not None:
            loc_to_iso3[int(location_id)] = iso3
    logger.info(
        "Built national location map: %d / %d locations mapped to ISO3",
        len(loc_to_iso3),
        len(ref),
    )
    return loc_to_iso3


def main():
    gbd_dirs = [
        Path(snakemake.input.gbd_dir_1),
        Path(snakemake.input.gbd_dir_2),
    ]
    reference_year = int(snakemake.params.reference_year)
    output_file = snakemake.output.exposure

    logger.info("Processing GBD 2023 dietary risk exposure data")
    logger.info("Reference year: %d", reference_year)

    loc_to_iso3 = build_national_location_map(snakemake.input.national_locations)

    # Per-(country, adult bucket) population for the reference year, used as
    # the age weights when reconstructing the 25+ exposure.
    pop = pd.read_csv(snakemake.input.population_age)
    pop = pop[
        (pop["year"] == reference_year)
        & (pop["age"].isin(ADULT_AGE_ID_TO_LABEL.values()))
    ][["country", "age", "value"]].rename(columns={"value": "population"})
    if pop.empty:
        raise ValueError(
            f"No adult population for reference year {reference_year} in "
            f"{snakemake.input.population_age}"
        )

    # Collect exposure CSVs across both release directories.
    csv_files = []
    for gbd_dir in gbd_dirs:
        csv_files.extend(sorted(gbd_dir.glob("IHME_GBD_2023_RISK_EXPOSURE_DIET_*.CSV")))
    if not csv_files:
        raise FileNotFoundError(
            f"No GBD 2023 risk-exposure CSV files found in {gbd_dirs}"
        )
    logger.info("Found %d GBD 2023 exposure CSV files", len(csv_files))

    all_results = []

    for csv_path in csv_files:
        risk_name = parse_risk_token(csv_path.stem)
        if risk_name is None:
            logger.warning(
                "Could not parse risk factor from filename: %s", csv_path.name
            )
            continue

        if risk_name not in GBD_RISK_TO_FOOD_GROUP:
            logger.info("Skipping risk factor %s (not mapped to food group)", risk_name)
            continue

        food_group = GBD_RISK_TO_FOOD_GROUP[risk_name]
        logger.info("Processing %s -> %s", risk_name, food_group)

        df = pd.read_csv(csv_path)

        # National rows, adult 5-year buckets only; sexes kept separate.
        df = df[
            (df["age_group_id"].isin(ADULT_AGE_ID_TO_LABEL))
            & (df["location_id"].isin(loc_to_iso3))
        ].copy()

        if df.empty:
            logger.warning("No national adult rows in %s", csv_path.name)
            continue

        # Filter to reference year, with fallback to nearest available.
        df_year = df[df["year_id"] == reference_year]
        if df_year.empty:
            available_years = sorted(df["year_id"].unique())
            nearest_year = min(available_years, key=lambda y: abs(y - reference_year))
            logger.warning(
                "No data for year %d in %s; using nearest year %d",
                reference_year,
                csv_path.name,
                nearest_year,
            )
            df_year = df[df["year_id"] == nearest_year]
        df = df_year

        df["country"] = df["location_id"].map(loc_to_iso3)
        df["age"] = df["age_group_id"].map(ADULT_AGE_ID_TO_LABEL)

        # Average the two sexes (unweighted; see module docstring) within
        # each (country, age bucket).
        by_age = df.groupby(["country", "age"], as_index=False).agg(
            mean=("mean", "mean"),
            lower=("lower", "mean"),
            upper=("upper", "mean"),
        )

        # Population-weight the adult buckets into the 25+ aggregate.
        by_age = by_age.merge(pop, on=["country", "age"], how="inner")
        result = (
            by_age.groupby("country")
            .apply(
                lambda g: pd.Series(
                    {
                        "consumption_g_per_day": _wmean(g["mean"], g["population"]),
                        "lower": _wmean(g["lower"], g["population"]),
                        "upper": _wmean(g["upper"], g["population"]),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
            .assign(food_group=food_group)
        )
        all_results.append(result)
        logger.info(
            "  %s: %d countries, mean=%.1f g/day",
            food_group,
            result["country"].nunique(),
            result["consumption_g_per_day"].mean(),
        )

    if not all_results:
        raise ValueError("No GBD dietary risk exposure data extracted")

    combined = pd.concat(all_results, ignore_index=True)

    # Multiple GBD risk factors may map to the same model food group; sum
    # exposures within a (country, food_group) so the file carries one row
    # per combination and downstream averaging is apples-to-apples with GDD.
    combined = (
        combined.groupby(["food_group", "country"], as_index=False)
        .agg(
            consumption_g_per_day=("consumption_g_per_day", "sum"),
            lower=("lower", "sum"),
            upper=("upper", "sum"),
        )
        .sort_values(["food_group", "country"])
        .reset_index(drop=True)
    )

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    combined.to_csv(output_file, index=False)
    logger.info(
        "Wrote %d rows (%d food groups, %d countries) to %s",
        len(combined),
        combined["food_group"].nunique(),
        combined["country"].nunique(),
        output_file,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
