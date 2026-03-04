# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

# SPDX-License-Identifier: GPL-3.0-or-later

"""Process UN WPP population data for total and age-specific counts."""

from collections import defaultdict
from collections.abc import Iterable

import pandas as pd

TARGET_AGE_ORDER = [
    "<1",
    "1-4",
    "5-9",
    "10-14",
    "15-19",
    "20-24",
    "25-29",
    "30-34",
    "35-39",
    "40-44",
    "45-49",
    "50-54",
    "55-59",
    "60-64",
    "65-69",
    "70-74",
    "75-79",
    "80-84",
    "85-89",
    "90-94",
    "95+",
]

AGE_OUTPUT_ORDER = [*TARGET_AGE_ORDER, "all-a"]


def _assign_age_bucket(
    label: str,
    start: float | int | None,
    span: float | int | None,
    has_under_one: bool,
    has_one_to_four: bool,
) -> str | None:
    """Map WPP age metadata to the buckets required by the health workflow."""

    normalized = label.strip().lower()

    if normalized in {"all ages", "all-age", "total"}:
        return "all-a"

    if normalized in {"under age 1", "under 1", "<1", "0"}:
        return "<1"

    if normalized in {"1-4", "1 - 4", "1 to 4", "01-04"}:
        return "1-4"

    # Avoid double-counting if both granular and 0-4 buckets are present
    if normalized in {"0-4", "0 - 4", "0 to 4", "00-04"}:
        if has_under_one or has_one_to_four:
            return None
        return "0-4"

    if span is not None:
        try:
            span_val = int(span)
        except (TypeError, ValueError):
            span_val = None
    else:
        span_val = None

    if start is not None:
        try:
            start_val = int(start)
        except (TypeError, ValueError):
            start_val = None
    else:
        start_val = None

    if start_val is not None and span_val is not None:
        if start_val == 0 and span_val == 1:
            return "<1"
        if start_val == 1 and span_val in {4, 5}:
            return "1-4"
        if 5 <= start_val <= 90 and span_val == 5:
            return f"{start_val}-{start_val + 4}"
        if start_val == 95:
            return "95+"
        if start_val >= 100:
            return "95+"

    if normalized in {"95-99", "95 - 99", "95 to 99"}:
        return "95+"
    if normalized in {"100+", "100 plus", "100+ years"}:
        return "95+"
    if normalized in {"95+", "95 plus"}:
        return "95+"

    return None


def _process_population(
    df: pd.DataFrame,
    countries: Iterable[str],
    year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (population_totals, population_by_age) DataFrames."""

    if "Variant" in df.columns:
        df = df[df["Variant"].astype(str).str.lower() == "medium"]

    if "Sex" in df.columns:
        df = df[df["Sex"].astype(str).str.lower() == "both sexes"]

    if "Time" not in df.columns:
        raise ValueError("WPP population dataset missing 'Time' column")
    df = df[df["Time"] == year]

    if "ISO3_code" not in df.columns:
        raise ValueError("WPP population dataset missing 'ISO3_code' column")

    df = df[df["ISO3_code"].notna()].copy()
    df["ISO3_code"] = df["ISO3_code"].astype(str).str.upper()

    country_set = set(countries)
    df = df[df["ISO3_code"].isin(country_set)]

    if df.empty:
        raise ValueError(
            "Filtered WPP population dataset is empty for the configured countries"
        )

    if "PopTotal" not in df.columns:
        raise ValueError("WPP population dataset missing 'PopTotal' column")

    df["PopTotal"] = pd.to_numeric(df["PopTotal"], errors="coerce")
    df = df.dropna(subset=["PopTotal"]).copy()
    df["PopTotal"] = (
        df["PopTotal"].astype(float) * 1_000.0
    )  # convert from thousands to persons

    if "AgeGrp" not in df.columns:
        raise ValueError("WPP population dataset missing 'AgeGrp' column")

    df["AgeGrp"] = df["AgeGrp"].astype(str)

    if "AgeGrpStart" in df.columns:
        df["AgeGrpStart"] = pd.to_numeric(df["AgeGrpStart"], errors="coerce")
    else:
        df["AgeGrpStart"] = pd.NA

    if "AgeGrpSpan" in df.columns:
        df["AgeGrpSpan"] = pd.to_numeric(df["AgeGrpSpan"], errors="coerce")
    else:
        df["AgeGrpSpan"] = pd.NA

    totals_records: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    country_names: dict[str, str] = {}

    for (iso3, location), group in df.groupby(["ISO3_code", "Location"]):
        labels = group["AgeGrp"].str.strip().str.lower()
        has_under_one = labels.isin({"under age 1", "under 1", "<1", "0"}).any()
        has_one_to_four = labels.isin({"1-4", "1 - 4", "1 to 4", "01-04"}).any()

        for _, row in group.iterrows():
            bucket = _assign_age_bucket(
                row["AgeGrp"],
                row.get("AgeGrpStart"),
                row.get("AgeGrpSpan"),
                has_under_one,
                has_one_to_four,
            )
            if bucket is None:
                continue
            totals_records[iso3][bucket] += float(row["PopTotal"])
        country_names[iso3] = str(location)

    missing_countries = sorted(country_set - set(totals_records.keys()))
    if missing_countries:
        raise ValueError(
            "WPP population data missing entries for configured countries: "
            + ", ".join(missing_countries)
        )

    missing_buckets: dict[str, set[str]] = {}
    age_records = []
    totals_records_final = []

    for iso3 in sorted(country_set & set(totals_records.keys())):
        buckets = totals_records[iso3]

        # Drop any pre-supplied aggregate to recompute after adjustments
        buckets.pop("all-a", None)

        combined_zero_four = buckets.pop("0-4", 0.0)
        under_one = buckets.get("<1", 0.0)
        one_to_four = buckets.get("1-4", 0.0)

        if combined_zero_four > 0.0:
            remainder = max(combined_zero_four - under_one - one_to_four, 0.0)
            missing_under_one = under_one == 0.0
            missing_one_to_four = one_to_four == 0.0

            if missing_under_one and missing_one_to_four:
                under_one = remainder * 0.2
                one_to_four = remainder - under_one
            elif missing_under_one:
                under_one = remainder
            elif missing_one_to_four:
                one_to_four = remainder
            elif remainder > 0.0:
                denom = under_one + one_to_four
                share_under = under_one / denom if denom > 0 else 0.2
                under_one += remainder * share_under
                one_to_four += remainder * (1 - share_under)

        buckets["<1"] = under_one
        buckets["1-4"] = one_to_four

        for required in TARGET_AGE_ORDER:
            if required not in buckets:
                missing_buckets.setdefault(iso3, set()).add(required)
                buckets.setdefault(required, 0.0)

        # Ensure 95+ captures any 95-99 / 100+ residuals
        buckets["95+"] = sum(
            value for key, value in buckets.items() if key in {"95+", "95-99", "100+"}
        )
        # Remove aliases if they linger
        for alias in ["95-99", "100+"]:
            buckets.pop(alias, None)

        total_population = sum(buckets[age] for age in TARGET_AGE_ORDER)
        buckets["all-a"] = total_population

        for age in AGE_OUTPUT_ORDER:
            age_records.append(
                {
                    "age": age,
                    "country": iso3,
                    "year": year,
                    "value": buckets.get(age, 0.0),
                }
            )

        totals_records_final.append(
            {
                "iso3": iso3,
                "country": country_names.get(iso3, iso3),
                "year": year,
                "population": total_population,
            }
        )

    if missing_buckets:
        details = ", ".join(
            f"{iso3}: {sorted(buckets)}"
            for iso3, buckets in sorted(missing_buckets.items())
        )
        raise ValueError(
            "WPP population data lacks required age buckets for: " + details
        )

    totals_df = pd.DataFrame(totals_records_final).sort_values("iso3")
    age_df = pd.DataFrame(age_records)
    age_df["age"] = pd.Categorical(
        age_df["age"], categories=AGE_OUTPUT_ORDER, ordered=True
    )
    age_df = age_df.sort_values(["country", "age"]).reset_index(drop=True)
    age_df["age"] = age_df["age"].astype(str)

    return totals_df, age_df


if __name__ == "__main__":
    params = snakemake.params  # type: ignore[name-defined]
    planning_year = int(params["planning_horizon"])
    health_year = int(params["baseline_year"])
    countries = list(params["countries"])

    df = pd.read_csv(snakemake.input.population_gz, compression="gzip")  # type: ignore[name-defined]

    totals_planning, _ = _process_population(df, countries, planning_year)
    _, age_health = _process_population(df, countries, health_year)

    totals_planning.to_csv(snakemake.output.population, index=False)  # type: ignore[name-defined]
    age_health.to_csv(snakemake.output.population_age, index=False)  # type: ignore[name-defined]
