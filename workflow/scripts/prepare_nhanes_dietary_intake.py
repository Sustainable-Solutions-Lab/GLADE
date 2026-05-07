#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Parse the FPED "Mean Amounts of Food Patterns Equivalents Consumed per
Individual, by Male/Female and Age" PDF and emit per-food-group intake for
the United States in the same schema as the GDD and FAOSTAT supplements.

The FPED table presents NHANES "What We Eat in America" 24-hour-recall
results aggregated to USDA Food Pattern equivalents (cup-eq, oz-eq, tsp-eq,
or grams) for the "2 and over" combined male+female population. We extract
that population-mean row from each sub-table (1a fruits, 1b vegetables,
1c grains, 1d dairy, 1e protein foods, 1f legumes, 1g oils/sugars), apply
unit conversions from a curated mapping CSV, and write the result as
g/day for each modelled food group.

The single "All ages" value is replicated across the model's age groups
(matching how `prepare_faostat_food_group_supply.py` propagates a single
country aggregate); finer age stratification can be added later by parsing
additional rows.

Input:
    - FPED PDF (Table_1_FPED_MaleFemale_<cycle>.pdf)
    - Curated mapping CSV (food_group, table, column_index, ..., grams_per_unit)

Output:
    - CSV with columns: unit, item, country, age, year, value (g/day)
"""

import logging
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# A "cell" in the FPED tables is a "value (SE)" pair, optionally with a
# trailing asterisk on the value (relative SE > 30%). "#" indicates
# non-zero but too-small-to-report; we treat it as 0 with a debug log.
CELL_RE = re.compile(r"(\d+(?:\.\d+)?)\*?\s*\(\s*\d+(?:\.\d+)?\s*\)")
SMALL_RE = re.compile(r"\s#\s|\s#$")

# Header line that introduces the population-mean block in every sub-table.
MF_HEADER_RE = re.compile(r"^\s*Males and females\s*:\s*$")
TWO_PLUS_RE = re.compile(r"^\s*2\s+and\s+over\.+\s+(.*)$")

# Sub-table identifiers as they appear in the PDF. Table 1e spans two pages
# with identical headers; the second page is split as 1e_2 by counting
# occurrences during the scan.
SIMPLE_TABLE_HEADERS = [
    ("1a", re.compile(r"^Table 1a\.\s+Fruit:")),
    ("1b", re.compile(r"^Table 1b\s+Vegetables:")),
    ("1c", re.compile(r"^Table 1c\.\s+Grains:")),
    ("1d", re.compile(r"^Table 1d\.\s+Dairy:")),
    ("1f", re.compile(r"^Table 1f\.\s+Legumes:")),
    ("1g", re.compile(r"^Table 1g\.\s+Oils")),
]
PROTEIN_HEADER_RE = re.compile(r"^Table 1e\.\s+Protein Foods:")
EXPECTED_ORDER = ["1a", "1b", "1c", "1d", "1e_1", "1e_2", "1f", "1g"]


def run_pdftotext(pdf_path: Path) -> str:
    """Convert the FPED PDF to layout-preserving text via pdftotext."""
    cmd = ["pdftotext", "-layout", str(pdf_path), "-"]
    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "pdftotext binary not available; ensure poppler is installed in "
            "the active pixi environment."
        ) from exc
    return result.stdout


def split_into_tables(text: str) -> dict[str, str]:
    """Split the pdftotext output into one block per FPED sub-table.

    Returns a dict mapping table id (e.g. "1a", "1e_2") to the raw text of
    that section. The first sub-table header in the page that doesn't match
    our expected sequence raises an error -- silent reorder is not allowed.
    """
    lines = text.splitlines()

    # Locate every header line. Table 1e appears twice (continued onto a
    # second page); we tag them 1e_1 / 1e_2 in occurrence order.
    matches: list[tuple[int, str]] = []
    protein_count = 0
    for idx, line in enumerate(lines):
        matched = False
        for table_id, pattern in SIMPLE_TABLE_HEADERS:
            if pattern.search(line):
                matches.append((idx, table_id))
                matched = True
                break
        if matched:
            continue
        if PROTEIN_HEADER_RE.search(line):
            protein_count += 1
            if protein_count > 2:
                raise ValueError(
                    "Found more than two 'Table 1e Protein Foods' headers in the PDF; "
                    "FPED layout has changed."
                )
            matches.append((idx, f"1e_{protein_count}"))

    if not matches:
        raise ValueError("No FPED sub-table headers found in the PDF text.")

    seen_order = [tid for _, tid in matches]
    if seen_order != EXPECTED_ORDER:
        raise ValueError(
            f"FPED sub-table sequence in PDF does not match expected order. "
            f"Expected {EXPECTED_ORDER}, got {seen_order}."
        )

    sections: dict[str, str] = {}
    for i, (line_idx, table_id) in enumerate(matches):
        end_idx = matches[i + 1][0] if i + 1 < len(matches) else len(lines)
        sections[table_id] = "\n".join(lines[line_idx:end_idx])

    return sections


def extract_male_female_row(section: str, table_id: str) -> list[float]:
    """Extract the "2 and over" Males-and-Females row values from a section.

    The FPED tables nest three blocks (Males / Females / Males and females),
    each with its own "2 and over" line. We want the third one. Returns the
    list of cell values in left-to-right column order.
    """
    lines = section.splitlines()

    in_mf_block = False
    target_line = None
    for line in lines:
        if MF_HEADER_RE.match(line):
            in_mf_block = True
            continue
        if in_mf_block:
            m = TWO_PLUS_RE.match(line)
            if m:
                target_line = line
                break

    if target_line is None:
        raise ValueError(
            f"Could not find 'Males and females / 2 and over' row in table {table_id}"
        )

    # Replace "#" sentinels with explicit zero-cells so the regex doesn't
    # silently skip them and shift the column indices.
    annotated = SMALL_RE.sub(" 0.0 (0.0) ", " " + target_line + " ")
    if "#" in annotated:
        # End-of-line "#" without trailing whitespace; handle defensively.
        annotated = annotated.replace("#", "0.0 (0.0)")

    cells = [float(m.group(1)) for m in CELL_RE.finditer(annotated)]
    if not cells:
        raise ValueError(
            f"No numeric cells parsed from table {table_id}: {target_line!r}"
        )

    logger.debug("Table %s: extracted %d cells: %s", table_id, len(cells), cells)
    return cells


def build_food_group_intake(
    sections: dict[str, str],
    mapping: pd.DataFrame,
    food_groups_included: list[str],
) -> dict[str, float]:
    """Apply the curated mapping to compute g/day per modelled food group."""
    intake: dict[str, float] = {}
    cell_cache: dict[str, list[float]] = {}

    for _, row in mapping.iterrows():
        food_group = row["food_group"]
        if food_group not in food_groups_included:
            continue

        table_id = row["table"]
        if table_id not in sections:
            raise ValueError(
                f"Mapping references table {table_id} which was not found in the PDF."
            )

        if table_id not in cell_cache:
            cell_cache[table_id] = extract_male_female_row(sections[table_id], table_id)

        cells = cell_cache[table_id]
        col = int(row["column_index"])
        if col >= len(cells):
            raise ValueError(
                f"Mapping requests column {col} of table {table_id} which has only "
                f"{len(cells)} cells."
            )

        sign = 1.0 if str(row["sign"]).strip() == "+" else -1.0
        contribution = sign * cells[col] * float(row["grams_per_unit"])
        intake[food_group] = intake.get(food_group, 0.0) + contribution

        logger.info(
            "NHANES %s += %s%.4f %s x %.2f g/%s = %.2f g/day  (%s, col %d: %s)",
            food_group,
            "" if sign > 0 else "-",
            cells[col],
            row["fped_unit"],
            float(row["grams_per_unit"]),
            row["fped_unit"],
            contribution,
            table_id,
            col,
            row["column_label"],
        )

    # Negative totals indicate a mis-specified mapping; refuse to ship them.
    negative = {fg: v for fg, v in intake.items() if v < 0}
    if negative:
        raise ValueError(f"NHANES intake came out negative for: {negative}")

    return intake


def get_unit(food_group: str) -> str:
    """Return a unit label that matches the GDD / FAOSTAT-supplements schema."""
    if food_group == "dairy":
        return "g/day (milk equiv)"
    if food_group == "sugar":
        return "g/day (refined sugar eq)"
    return "g/day (fresh wt)"


# FAOSTAT FBS item code for Butter and Ghee, with the milk-equivalent
# extraction factor used in `prepare_faostat_food_group_supply.py`. We pull
# butter separately because FPED's "Total Dairy" is the low-fat fraction
# (butterfat is stripped into FPED's Solid Fats accounting axis), so taking
# the FPED value alone would systematically under-count modelled dairy
# mass.
FBS_BUTTER_ITEM_CODE = 2740
BUTTER_MILK_EQUIV_FACTOR = 21.3


def fetch_fbs_butter_supply_g_day(
    countries: list[str],
    reference_year: int,
    fbs_csv: str,
    m49_csv: str,
    fbs_element_code: int,
) -> dict[str, float]:
    """Return raw butter supply (g/day milk-equivalent) per country from FAOSTAT FBS."""
    bulk = load_bulk(fbs_csv)
    m49_to_iso3 = load_m49_to_iso3(m49_csv)
    bulk = add_iso3_column(bulk, m49_to_iso3)
    df = filter_bulk(
        bulk,
        element_codes=[int(fbs_element_code)],
        item_codes=[FBS_BUTTER_ITEM_CODE],
        years=[reference_year],
        iso3_codes=[c.upper() for c in countries],
    )
    if df.empty:
        return dict.fromkeys(countries, 0.0)

    df["iso3"] = df["iso3"].astype(str).str.upper()
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce").fillna(0.0)
    # FAOSTAT supply quantity is in kg/cap/yr; convert to g/day, then to
    # milk-equivalent grams per day.
    by_country = df.groupby("iso3")["Value"].sum() * 1000.0 / 365.0
    return {
        country: float(by_country.get(country, 0.0)) * BUTTER_MILK_EQUIV_FACTOR
        for country in countries
    }


def add_butter_topup_to_dairy(
    intake: dict[str, float],
    country: str,
    fbs_csv: str,
    m49_csv: str,
    flw_csv: str,
    reference_year: int,
    fbs_element_code: int,
) -> None:
    """Add waste-corrected FAOSTAT butter (in milk-equivalent g/day) to NHANES dairy.

    Mutates `intake` in place. No-op if `dairy` is not present in `intake`.
    """
    if "dairy" not in intake:
        return

    butter_raw = fetch_fbs_butter_supply_g_day(
        [country],
        reference_year,
        fbs_csv,
        m49_csv,
        fbs_element_code,
    )[country]

    flw = pd.read_csv(flw_csv)
    waste_row = flw[(flw["country"] == country) & (flw["food_group"] == "dairy")]
    if waste_row.empty:
        raise ValueError(
            f"No food_loss_waste row for country={country}, food_group=dairy; "
            "cannot apply waste correction to butter top-up."
        )
    waste_fraction = float(waste_row["waste_fraction"].iloc[0])
    butter_intake = butter_raw * (1.0 - waste_fraction)

    before = intake["dairy"]
    intake["dairy"] = before + butter_intake
    logger.info(
        "Butter top-up for %s: FAOSTAT FBS butter = %.1f g/day milk-eq raw, "
        "%.1f g/day after waste correction (waste=%.1f%%); dairy %.1f -> %.1f g/day",
        country,
        butter_raw,
        butter_intake,
        waste_fraction * 100,
        before,
        intake["dairy"],
    )


def main():
    pdf_file = Path(snakemake.input.fped_pdf)
    mapping_file = snakemake.input.mapping
    fbs_csv = snakemake.input.fbs_csv
    m49_csv = snakemake.input.m49
    flw_csv = snakemake.input.food_loss_waste
    output_file = snakemake.output.diet
    reference_year = int(snakemake.params.reference_year)
    fbs_element_code = int(snakemake.params.fbs_element_code)
    food_groups_included = list(snakemake.params.food_groups_included)
    country = str(snakemake.params.country)
    baseline_age = str(snakemake.params.baseline_age)

    logger.info("Parsing FPED table %s", pdf_file)
    text = run_pdftotext(pdf_file)
    sections = split_into_tables(text)
    logger.info("Found %d FPED sub-tables: %s", len(sections), sorted(sections))

    mapping = pd.read_csv(mapping_file, comment="#")
    required_cols = {
        "food_group",
        "table",
        "column_index",
        "fped_unit",
        "grams_per_unit",
        "sign",
    }
    missing_cols = required_cols - set(mapping.columns)
    if missing_cols:
        raise ValueError(f"NHANES mapping CSV missing columns: {sorted(missing_cols)}")

    intake = build_food_group_intake(sections, mapping, food_groups_included)

    if not intake:
        logger.error(
            "No NHANES rows produced; check that food_groups_included intersects "
            "the mapping CSV's food_group column."
        )
        sys.exit(1)

    add_butter_topup_to_dairy(
        intake,
        country=country,
        fbs_csv=fbs_csv,
        m49_csv=m49_csv,
        flw_csv=flw_csv,
        reference_year=reference_year,
        fbs_element_code=fbs_element_code,
    )

    rows = []
    for food_group, value in sorted(intake.items()):
        rows.append(
            {
                "unit": get_unit(food_group),
                "item": food_group,
                "country": country,
                "age": baseline_age,
                "year": reference_year,
                "value": value,
            }
        )

    df = pd.DataFrame(rows)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_file, index=False)
    logger.info(
        "Wrote %d rows (%d food groups x %d age groups) to %s",
        len(df),
        df["item"].nunique(),
        df["age"].nunique(),
        output_file,
    )

    # Summary table for quick review in the log.
    for food_group, value in sorted(intake.items()):
        logger.info("USA NHANES intake: %s = %.1f g/day", food_group, value)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
