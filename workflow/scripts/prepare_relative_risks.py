# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Convert manually downloaded IHME GBD RR tables into tidy dietary risk curves."""

import logging
import math
from pathlib import Path
import re

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
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
# The model's "Stroke" cause is restricted to ischemic stroke: CRA evidence
# is robust for ischemic stroke (the atherosclerotic pathway diet acts on)
# but null or weak for intracerebral and subarachnoid hemorrhage. The
# mortality side scales aggregate "Stroke" deaths by health.ischemic_stroke_share
# until the IHME re-download with the "Ischemic stroke" cause filter is in
# place.
CAUSE_MAP = {
    "Ischemic heart disease": "CHD",
    "Ischemic stroke": "Stroke",
    "Diabetes mellitus type 2": "T2DM",
    "Colon and rectum cancer": "CRC",
}


VALUE_REGEX = re.compile(r"[-+]?(?:\d+\.\d+|\d+)")


# Map Excel column indices to GBD adult age bucket labels (25-29 through 95+).
# Columns 5-12 (childhood/adolescent ages) are always NaN for dietary risks.
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
    """Return mapping from IHME risk name row index to slice bounds.

    All rows starting with "Diet" are treated as block boundaries to avoid
    including data from unrecognized risk factors in adjacent blocks.
    """
    # Find ALL "Diet ..." headers to use as boundaries
    all_diet_rows = [
        idx
        for idx, val in df[0].items()
        if isinstance(val, str) and val.startswith("Diet")
    ]

    bounds: dict[str, tuple[int, int]] = {}
    for i, idx in enumerate(all_diet_rows):
        risk_name = df.at[idx, 0]
        # Only include blocks for risk factors we recognize
        if risk_name not in RISK_CONFIG:
            continue
        next_idx = all_diet_rows[i + 1] if i + 1 < len(all_diet_rows) else len(df)
        bounds[risk_name] = (idx + 1, next_idx)
    return bounds


def _parse_relative_risks(
    df: pd.DataFrame,
    ssb_sugar_per_gram: float,
    basis_factor_by_risk: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Parse the Excel sheet into tidy RR records.

    If *basis_factor_by_risk* is provided, multiply the parsed exposure
    values for each risk factor by the corresponding factor before
    emitting them. This is how the GBD/IHME RR curve x-axis is
    converted from its native cooked-weight basis (for legumes, etc.)
    onto the model's dry/raw basis, so consumption can be looked up
    directly.
    """

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

        # Compose the parsed-exposure conversion (units) with the
        # cooked-to-model-basis factor (no-op for groups whose GBD basis
        # already matches the model).
        basis_factor = float(basis_factor_by_risk.get(risk_id, 1.0))
        effective_conversion = None if conversion is None else conversion * basis_factor
        if basis_factor != 1.0:
            logger.info(
                "Applying GBD->model basis factor %.3f to %s RR exposure axis",
                basis_factor,
                risk_id,
            )

        block = df.iloc[start:end]
        block = block[block[0].notna()]

        for _, row in block.iterrows():
            outcome = str(row[0]).strip()
            exposure_raw = row[1]

            if not isinstance(exposure_raw, str):
                # Skip cases without quantitative exposure (e.g., "Exposed")
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

            # Extract RR values for each adult age group
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
            items = ", ".join(sorted(causes))
            logger.info(f"  {risk_id}: {items}")

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

    # Fill missing age groups by extrapolating from the nearest available age.
    df_out = _fill_missing_ages(df_out)

    return df_out


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

        # Build a lookup from age label to row values
        age_to_row = {row["age"]: row for _, row in grp.iterrows()}

        for age_label in ADULT_AGE_LABELS:
            if age_label not in missing:
                continue

            # Find the nearest available age: prefer the preceding age in order
            idx = ADULT_AGE_LABELS.index(age_label)
            donor = None
            # Search backward first (nearest younger age)
            for j in range(idx - 1, -1, -1):
                if ADULT_AGE_LABELS[j] in age_to_row:
                    donor = age_to_row[ADULT_AGE_LABELS[j]]
                    break
            # If no younger age, search forward
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

    # Assert completeness
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


def _apply_alternative_rr(
    df: pd.DataFrame,
    alternative_rr: dict[str, str | None],
) -> pd.DataFrame:
    """Replace GBD RR entries with age-corrected log-linear curves from CSV.

    For each risk factor with a non-null CSV path, the GBD dose-response
    curves are replaced with log-linear curves: RR(x) = rr_per_unit^(x/unit).
    Age-attenuation factors are extracted from the original GBD data to preserve
    the age structure of the dose-response relationship.

    Parameters
    ----------
    df
        GBD relative risks DataFrame with columns: risk_factor, cause, age,
        exposure_g_per_day, rr_mean, rr_low, rr_high.
    alternative_rr
        Mapping from risk factor name to CSV path (or None to skip).
    """
    for risk_factor, csv_path in alternative_rr.items():
        if not csv_path:
            continue

        logger.info(
            f"Applying alternative log-linear RR for '{risk_factor}' from {csv_path}"
        )
        alt_df = pd.read_csv(csv_path)

        # Validate CSV columns
        required_cols = {
            "outcome",
            "rr_central",
            "rr_lower_95ci",
            "rr_upper_95ci",
            "per_unit",
        }
        missing = required_cols - set(alt_df.columns)
        if missing:
            raise ValueError(
                f"Alternative RR CSV {csv_path} is missing columns: {sorted(missing)}"
            )

        # Extract age-attenuation factors from the original GBD data
        attenuation = _extract_age_attenuation(df, risk_factor)

        # Get the GBD exposure points for this risk factor
        gbd_data = df[df["risk_factor"] == risk_factor]
        if gbd_data.empty:
            raise ValueError(
                f"No GBD data found for risk factor '{risk_factor}' to extract "
                f"exposure grid and age-attenuation factors"
            )
        exposure_points = sorted(gbd_data["exposure_g_per_day"].unique())

        # Generate log-linear curves
        new_rows: list[dict] = []
        for _, row in alt_df.iterrows():
            cause = row["outcome"]
            rr_central = float(row["rr_central"])
            rr_lower = float(row["rr_lower_95ci"])
            rr_upper = float(row["rr_upper_95ci"])
            per_unit = _parse_per_unit(row["per_unit"])

            for age in ADULT_AGE_LABELS:
                att = attenuation.get((cause, age), 1.0)
                for exposure_g in exposure_points:
                    x_ratio = exposure_g / per_unit
                    new_rows.append(
                        {
                            "risk_factor": risk_factor,
                            "cause": cause,
                            "age": age,
                            "exposure_g_per_day": exposure_g,
                            "rr_mean": math.exp(att * math.log(rr_central) * x_ratio),
                            "rr_low": math.exp(att * math.log(rr_lower) * x_ratio),
                            "rr_high": math.exp(att * math.log(rr_upper) * x_ratio),
                        }
                    )

        # Replace GBD entries for this risk factor
        df = df[df["risk_factor"] != risk_factor]
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        df = df.sort_values(
            ["risk_factor", "cause", "age", "exposure_g_per_day"]
        ).reset_index(drop=True)

        logger.info(
            f"  Replaced {risk_factor} with {len(new_rows)} log-linear entries "
            f"({len(alt_df)} causes x {len(ADULT_AGE_LABELS)} ages x "
            f"{len(exposure_points)} exposures)"
        )

    return df


def _extract_age_attenuation(
    df: pd.DataFrame, risk_factor: str
) -> dict[tuple[str, str], float]:
    """Extract age-attenuation factors from GBD data for a risk factor.

    For each (cause, age), computes the ratio of log(RR) at that age to
    log(RR) at the youngest age group, using a mid-range exposure point.
    This ratio captures how much the RR effect attenuates with age.

    Returns
    -------
    dict
        Mapping (cause, age) -> attenuation factor in [0, 1].
    """
    risk_data = df[df["risk_factor"] == risk_factor]
    youngest_age = ADULT_AGE_LABELS[0]
    attenuation: dict[tuple[str, str], float] = {}

    for cause in risk_data["cause"].unique():
        cause_data = risk_data[risk_data["cause"] == cause]

        # Use the highest non-zero exposure for stability
        exposures = sorted(cause_data["exposure_g_per_day"].unique())
        ref_exposure = [x for x in exposures if x > 0]
        if not ref_exposure:
            for age in ADULT_AGE_LABELS:
                attenuation[(cause, age)] = 1.0
            continue
        ref_x = ref_exposure[-1]

        # Get youngest age log(RR) at reference exposure
        youngest_row = cause_data[
            (cause_data["age"] == youngest_age)
            & (cause_data["exposure_g_per_day"] == ref_x)
        ]
        if youngest_row.empty or youngest_row["rr_mean"].values[0] == 1.0:
            for age in ADULT_AGE_LABELS:
                attenuation[(cause, age)] = 1.0
            continue

        log_rr_youngest = math.log(float(youngest_row["rr_mean"].values[0]))
        if abs(log_rr_youngest) < 1e-10:
            for age in ADULT_AGE_LABELS:
                attenuation[(cause, age)] = 1.0
            continue

        for age in ADULT_AGE_LABELS:
            age_row = cause_data[
                (cause_data["age"] == age) & (cause_data["exposure_g_per_day"] == ref_x)
            ]
            if age_row.empty:
                attenuation[(cause, age)] = 1.0
                continue
            log_rr_age = math.log(float(age_row["rr_mean"].values[0]))
            att = log_rr_age / log_rr_youngest
            # Clamp to [0, 1] to avoid sign flips from noise
            attenuation[(cause, age)] = max(0.0, min(1.0, att))

    return attenuation


def _parse_per_unit(per_unit: str) -> float:
    """Parse per_unit string like '100 g/day' into numeric g/day value."""
    parts = str(per_unit).strip().split()
    if len(parts) < 2:
        raise ValueError(f"Cannot parse per_unit: '{per_unit}'")
    return float(parts[0])


def main() -> None:
    snakemake = globals().get("snakemake")  # type: ignore
    if snakemake is None:
        raise RuntimeError("This script must run via Snakemake")

    input_path = Path(snakemake.input["gbd_rr"])
    output_path = Path(snakemake.output["relative_risks"])
    ssb_sugar_g_per_100g = float(snakemake.params["ssb_sugar_g_per_100g"])

    if ssb_sugar_g_per_100g <= 0:
        raise ValueError("ssb_sugar_g_per_100g must be positive")
    ssb_sugar_per_gram = ssb_sugar_g_per_100g / 100.0

    # Build per-risk-factor RR-curve x-axis conversion: each risk
    # factor's GBD basis (declared in diet.source_basis.gbd) is compared
    # to the matching food's basis (food_basis.csv via food_groups.csv);
    # when they differ, the matching factor in the top-level
    # weight_conversion is applied so the curve x-axis lands in the
    # model's consumption basis.
    from workflow.scripts.diet.basis import (
        build_group_basis,
        conversion_factor,
        load_food_basis,
    )

    source_basis = {
        src: {str(g): str(b) for g, b in groups.items()}
        for src, groups in dict(snakemake.params.source_basis).items()
    }
    weight_conversion = {
        str(table): {str(k): float(v) for k, v in entries.items()}
        for table, entries in dict(snakemake.params.weight_conversion).items()
    }
    food_basis = load_food_basis(snakemake.input["food_basis"])
    food_to_group = (
        pd.read_csv(snakemake.input["food_groups"]).set_index("food")["group"].to_dict()
    )
    group_basis = build_group_basis(food_basis, food_to_group)
    gbd_basis = source_basis.get("gbd", {})
    basis_factor_by_risk: dict[str, float] = {}
    for risk_id, src in gbd_basis.items():
        tgt = group_basis.get(risk_id)
        if tgt is None or src == tgt:
            continue
        basis_factor_by_risk[risk_id] = conversion_factor(
            src, tgt, risk_id, weight_conversion
        )

    logger.info(f"Reading {input_path}")
    df = pd.read_excel(input_path, header=None)

    relative_risks = _parse_relative_risks(
        df, ssb_sugar_per_gram, basis_factor_by_risk=basis_factor_by_risk
    )

    # Apply alternative log-linear RR overrides if configured
    alternative_rr = getattr(snakemake.params, "alternative_rr", {})
    if alternative_rr:
        relative_risks = _apply_alternative_rr(relative_risks, alternative_rr)

    # Validate that we have all required risk factors and causes
    required_risk_factors = set(snakemake.params["risk_factors"])
    required_causes = set(snakemake.params["causes"])
    output_risk_factors = set(relative_risks["risk_factor"].unique())
    output_causes = set(relative_risks["cause"].unique())

    missing_risk_factors = required_risk_factors - output_risk_factors
    if missing_risk_factors:
        raise ValueError(
            f"[prepare_relative_risks] ERROR: Relative risks data is missing {len(missing_risk_factors)} required risk factors: "
            f"{sorted(missing_risk_factors)}. Available: {sorted(output_risk_factors)}. "
            f"Please ensure the IHME GBD relative risks file includes all risk factors listed in config.health.risk_factors."
        )

    missing_causes = required_causes - output_causes
    if missing_causes:
        raise ValueError(
            f"[prepare_relative_risks] ERROR: Relative risks data is missing {len(missing_causes)} required causes: "
            f"{sorted(missing_causes)}. Available: {sorted(output_causes)}. "
            f"Please ensure the IHME GBD relative risks file includes all causes listed in config.health.causes."
        )

    logger.info(
        "[prepare_relative_risks] ✓ Validation passed: all required risk factors and causes present"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    relative_risks.to_csv(output_path, index=False)
    logger.info(f"Wrote {len(relative_risks)} records to {output_path}")


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
