# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process UN WPP life table data to extract life expectancy by age.

Downloads from UN World Population Prospects should include:
- Abridged life tables for Medium variant
- Both sexes combined (Total)
- Life expectancy (ex) by age group
- Global (World) aggregate

Output format:
- CSV with columns: age, life_exp
- Age groups standardized to model age buckets
"""

import contextlib
import logging
from pathlib import Path
import re

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Standard age buckets used across the model
AGE_BUCKETS = [
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

_AGE_BUCKET_PATTERN = re.compile(r"^(?P<start>\d+)\s*-\s*(?P<end>\d+)$")


def normalize_age_bucket(label: object) -> str | None:
    """Normalize WPP age group labels to standard model age buckets."""
    text = str(label).strip().lower()

    # Handle special cases for youngest age groups
    if text in {"<1", "under age 1", "under 1", "0", "0-0", "0 - 0"}:
        return "<1"
    if text in {"1-4", "1 - 4", "01-04", "1 to 4"}:
        return "1-4"

    # Parse age ranges with regex
    match = _AGE_BUCKET_PATTERN.match(text.replace("-", "-").replace("to", "-"))
    if match:
        start = int(match.group("start"))
        end = int(match.group("end"))
        if start == 0:
            return "<1"
        if start == 1 and end in {4, 5}:
            return "1-4"
        if start >= 5 and end == start + 4 and start <= 90:
            return f"{start}-{end}"
        if start >= 95:
            return "95+"

    # Handle oldest age group variations
    if text in {"95-99", "95 - 99", "100+", "95+", "95 plus", "100 plus", "100+ years"}:
        return "95+"

    return None


def main() -> None:
    """Extract and standardize WPP life table data."""
    snakemake = globals().get("snakemake")  # type: ignore
    if snakemake is None:
        raise RuntimeError("This script must run via Snakemake")

    input_path = snakemake.input["wpp_life_table"]
    output_path = snakemake.output["life_table"]
    reference_year = int(snakemake.params["reference_year"])

    logger.info("Reading WPP life table from %s", input_path)
    df = pd.read_csv(input_path, low_memory=False)

    if df.empty:
        raise ValueError("WPP life table file is empty")

    # Filter to Medium variant
    variant_col = df["Variant"].astype(str).str.lower()
    df = df[variant_col == "medium"]
    if df.empty:
        raise ValueError("WPP life table missing 'Medium' variant entries")

    # Filter to Total (both sexes)
    sex_col = df["Sex"].astype(str).str.lower()
    df = df[sex_col == "total"]
    if df.empty:
        raise ValueError("WPP life table missing 'Total' sex entries")

    # Parse and filter by year
    df["Time"] = pd.to_numeric(df["Time"], errors="coerce")
    df = df.dropna(subset=["Time"])
    if df.empty:
        raise ValueError("WPP life table missing valid 'Time' values")

    available_years = sorted({int(value) for value in df["Time"].unique()})
    logger.info("Available years: %s", available_years)

    if reference_year in available_years:
        target_year = reference_year
    else:
        target_year = min(
            available_years, key=lambda year: (abs(year - reference_year), year)
        )
        logger.info(
            "Reference year %d not found; using closest year %d",
            reference_year,
            target_year,
        )

    df = df[df["Time"].astype(int) == int(target_year)]
    if df.empty:
        raise ValueError(f"WPP life table has no data for year {target_year}")

    # Filter to World aggregate
    df = df[df["Location"].astype(str) == "World"]
    if df.empty:
        raise ValueError("WPP life table missing 'World' aggregate records")

    # Extract life expectancy by age group
    age_to_life_exp = {}
    for _, row in df.iterrows():
        bucket = normalize_age_bucket(row.get("AgeGrp"))
        if bucket is None or bucket in age_to_life_exp:
            continue
        try:
            age_to_life_exp[bucket] = float(row["ex"])
        except (TypeError, ValueError):
            continue

    # Handle 95+ age group if missing (may be labeled as 95-99 or 100+)
    if "95+" not in age_to_life_exp:
        candidates = df[df["AgeGrp"].astype(str).isin(["95-99", "95+", "100+"])]
        if not candidates.empty:
            first = candidates.iloc[0]
            with contextlib.suppress(TypeError, ValueError):
                age_to_life_exp["95+"] = float(first["ex"])

    # Validate completeness
    missing = [bucket for bucket in AGE_BUCKETS if bucket not in age_to_life_exp]
    if missing:
        raise ValueError(
            "WPP life table missing life expectancy entries for age buckets: "
            + ", ".join(missing)
        )

    # Build output dataframe
    ordered = {bucket: age_to_life_exp[bucket] for bucket in AGE_BUCKETS}
    output = pd.DataFrame(
        {"age": ordered.keys(), "life_exp": ordered.values()}
    ).reset_index(drop=True)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Save with header
    output.to_csv(output_path, index=False)

    logger.info("Wrote %d age groups to %s", len(output), output_path)
    logger.info("Year: %d", target_year)
    logger.info(
        "Life expectancy range: %.1f - %.1f years",
        output["life_exp"].min(),
        output["life_exp"].max(),
    )


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
