# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Parse the IHME GBD 2019 Relative Risks appendix workbook into tidy curves.

This is consumed only by the one-off ``generate_rr_age_attenuation.py`` (the GBD
2019 age structure is the donor for the curated age-attenuation table). The
per-build workflow takes its dose-response curves from GBD 2023 Burden of Proof
instead; see ``prepare_relative_risks.py``.

Output of :func:`parse_gbd2019_rr_appendix`: one row per
``(risk_factor, cause, age, exposure_g_per_day)`` with ``rr_mean/rr_low/rr_high``.
"""

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

# Map IHME dietary risk names to model risk_factor identifiers and exposure conversion factors
RISK_CONFIG = {
    "Diet low in fruits": {"risk_factor": "fruits", "unit": "g/day", "conversion": 1.0},
    "Diet low in vegetables": {
        "risk_factor": "vegetables",
        "unit": "g/day",
        "conversion": 1.0,
    },
    "Diet low in whole grains": {
        "risk_factor": "whole_grains",
        "unit": "g/day",
        "conversion": 1.0,
    },
    "Diet low in legumes": {
        "risk_factor": "legumes",
        "unit": "g/day",
        "conversion": 1.0,
    },
    "Diet low in nuts and seeds": {
        "risk_factor": "nuts_seeds",
        "unit": "g/day",
        "conversion": 1.0,
    },
    "Diet high in red meat": {
        "risk_factor": "red_meat",
        "unit": "g/day",
        "conversion": 1.0,
    },
    "Diet high in sugar-sweetened beverages": {
        "risk_factor": "sugar",
        "unit": "g/day",
        "conversion": None,
    },
}


# Map IHME outcome names to model causes. Any unmapped outcome is ignored.
# The model's "Stroke" cause is restricted to ischemic stroke.
CAUSE_MAP = {
    "Ischemic heart disease": "CHD",
    "Ischemic stroke": "Stroke",
    "Diabetes mellitus type 2": "T2DM",
    "Colon and rectum cancer": "CRC",
}


VALUE_REGEX = re.compile(r"[-+]?(?:\d+\.\d+|\d+)")


# Map Excel column indices to GBD adult age bucket labels (25-29 through 95+).
ADULT_AGE_COLUMNS: dict[int, str] = {
    13: "25-29",
    14: "30-34",
    15: "35-39",
    16: "40-44",
    17: "45-49",
    18: "50-54",
    19: "55-59",
    20: "60-64",
    21: "65-69",
    22: "70-74",
    23: "75-79",
    24: "80-84",
    25: "85-89",
    26: "90-94",
    27: "95+",
}

ADULT_AGE_LABELS: list[str] = list(ADULT_AGE_COLUMNS.values())


def _parse_rr_value(cell: object) -> tuple[float, float | None, float | None]:
    """Return (mean, low, high) RR floats parsed from a string cell."""
    if isinstance(cell, (int, float)):
        value = float(cell)
        return value, None, None

    text = str(cell).strip()
    if not text:
        raise ValueError("Empty RR cell")

    matches = VALUE_REGEX.findall(text)
    if not matches:
        raise ValueError(f"Could not parse RR value from '{text}'")

    numbers = [float(v) for v in matches]
    mean = numbers[0]
    low = numbers[1] if len(numbers) > 1 else None
    high = numbers[2] if len(numbers) > 2 else None
    return mean, low, high


def _normalize_exposure(raw: str, conversion: float | None) -> float:
    """Convert exposure text like '100 g/day' into g/day as float."""
    parts = raw.strip().split()
    if len(parts) < 2:
        raise ValueError(f"Unexpected exposure label '{raw}'")

    value = float(parts[0])
    unit = parts[1].lower()

    if unit not in {"g/day", "%energy/day"}:
        raise ValueError(f"Unsupported exposure unit '{unit}' in '{raw}'")

    if unit == "%energy/day":
        raise ValueError(
            "Energy-based exposures are not supported in the current health module"
        )

    if conversion is None:
        raise ValueError("Missing conversion factor for omega-3 exposure")

    return value * conversion


def _extract_risk_blocks(df: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """Return mapping from IHME risk name row index to slice bounds."""
    all_diet_rows = [
        idx
        for idx, val in df[0].items()
        if isinstance(val, str) and val.startswith("Diet")
    ]

    bounds: dict[str, tuple[int, int]] = {}
    for i, idx in enumerate(all_diet_rows):
        risk_name = df.at[idx, 0]
        if risk_name not in RISK_CONFIG:
            continue
        next_idx = all_diet_rows[i + 1] if i + 1 < len(all_diet_rows) else len(df)
        bounds[risk_name] = (idx + 1, next_idx)
    return bounds


def parse_gbd2019_rr_appendix(
    df: pd.DataFrame,
    ssb_sugar_per_gram: float,
    basis_factor_by_risk: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Parse the Excel sheet into tidy RR records (all 15 adult age buckets)."""
    if basis_factor_by_risk is None:
        basis_factor_by_risk = {}

    records: list[dict[str, float | str]] = []
    skipped_causes: dict[str, set[str]] = {}
    skipped_units: set[str] = set()
    blocks = _extract_risk_blocks(df)

    for risk_name, (start, end) in blocks.items():
        config = RISK_CONFIG[risk_name]
        risk_id = config["risk_factor"]
        conversion = config["conversion"]

        if risk_id == "sugar":
            if ssb_sugar_per_gram <= 0:
                raise ValueError("ssb_sugar_per_gram must be positive")
            conversion = ssb_sugar_per_gram

        basis_factor = float(basis_factor_by_risk.get(risk_id, 1.0))
        effective_conversion = None if conversion is None else conversion * basis_factor

        block = df.iloc[start:end]
        block = block[block[0].notna()]

        for _, row in block.iterrows():
            outcome = str(row[0]).strip()
            exposure_raw = row[1]

            if not isinstance(exposure_raw, str):
                continue

            if outcome not in CAUSE_MAP:
                skipped_causes.setdefault(risk_id, set()).add(outcome)
                continue

            cause = CAUSE_MAP[outcome]

            try:
                exposure = _normalize_exposure(exposure_raw, effective_conversion)
            except ValueError:
                skipped_units.add(exposure_raw)
                continue

            for col_idx, age_label in ADULT_AGE_COLUMNS.items():
                if col_idx >= len(row):
                    continue
                cell = row[col_idx]
                if not (isinstance(cell, str) and cell.strip()):
                    continue

                try:
                    rr_mean, rr_low, rr_high = _parse_rr_value(cell)
                except ValueError:
                    continue

                records.append(
                    {
                        "risk_factor": risk_id,
                        "cause": cause,
                        "age": age_label,
                        "exposure_g_per_day": float(exposure),
                        "rr_mean": rr_mean,
                        "rr_low": rr_low,
                        "rr_high": rr_high,
                    }
                )

    if skipped_causes:
        logger.info("Skipped unmapped outcomes:")
        for risk_id, causes in sorted(skipped_causes.items()):
            logger.info(f"  {risk_id}: {', '.join(sorted(causes))}")
    if skipped_units:
        logger.info("Skipped exposures with unsupported units:")
        for label in sorted(skipped_units):
            logger.info(f"  {label}")

    if not records:
        raise ValueError("No dietary risk records parsed from GBD relative risk file")

    df_out = pd.DataFrame(records)
    df_out = (
        df_out.groupby(
            ["risk_factor", "cause", "age", "exposure_g_per_day"], as_index=False
        )
        .agg({"rr_mean": "mean", "rr_low": "mean", "rr_high": "mean"})
        .sort_values(["risk_factor", "cause", "age", "exposure_g_per_day"])
    )

    return _fill_missing_ages(df_out)


def _fill_missing_ages(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure every (risk_factor, cause, exposure) triple has all 15 adult ages.

    Missing ages are filled by copying from the nearest available age group,
    preferring the closest older age (forward-fill in age order).
    """
    expected_ages = set(ADULT_AGE_LABELS)
    filled_rows: list[dict] = []
    n_filled = 0

    for (risk, cause, exposure), grp in df.groupby(
        ["risk_factor", "cause", "exposure_g_per_day"]
    ):
        present_ages = set(grp["age"].values)
        missing = expected_ages - present_ages

        if not missing:
            continue

        age_to_row = {row["age"]: row for _, row in grp.iterrows()}

        for age_label in ADULT_AGE_LABELS:
            if age_label not in missing:
                continue

            idx = ADULT_AGE_LABELS.index(age_label)
            donor = None
            for j in range(idx - 1, -1, -1):
                if ADULT_AGE_LABELS[j] in age_to_row:
                    donor = age_to_row[ADULT_AGE_LABELS[j]]
                    break
            if donor is None:
                for j in range(idx + 1, len(ADULT_AGE_LABELS)):
                    if ADULT_AGE_LABELS[j] in age_to_row:
                        donor = age_to_row[ADULT_AGE_LABELS[j]]
                        break

            if donor is None:
                raise ValueError(
                    f"Cannot fill missing age '{age_label}' for "
                    f"({risk}, {cause}, {exposure}): no donor ages available"
                )

            filled_rows.append(
                {
                    "risk_factor": risk,
                    "cause": cause,
                    "age": age_label,
                    "exposure_g_per_day": exposure,
                    "rr_mean": donor["rr_mean"],
                    "rr_low": donor["rr_low"],
                    "rr_high": donor["rr_high"],
                }
            )
            n_filled += 1

    if filled_rows:
        logger.info(
            f"Filled {n_filled} missing age entries by extrapolation from nearest age"
        )
        df = pd.concat([df, pd.DataFrame(filled_rows)], ignore_index=True)
        df = df.sort_values(
            ["risk_factor", "cause", "age", "exposure_g_per_day"]
        ).reset_index(drop=True)

    for (risk, cause, exposure), grp in df.groupby(
        ["risk_factor", "cause", "exposure_g_per_day"]
    ):
        present = set(grp["age"].values)
        missing = expected_ages - present
        if missing:
            raise ValueError(
                f"Age completeness check failed for ({risk}, {cause}, {exposure}): "
                f"missing {sorted(missing)}"
            )

    return df
