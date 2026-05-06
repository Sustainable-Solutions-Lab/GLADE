#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare GDD-derived baseline-diet kcal totals against FAOSTAT anchors.

The baseline-diet pipeline anchors per-food consumption to GDD survey
intake (with FAOSTAT supplements). GDD is the right calibration source
for the GBD relative-risk machinery, but its country totals can be
implausible — surveys systematically under-report energy in HICs, and
the GDD-FAOSTAT merge produces a long tail of outliers driven by sparse
underlying data (small island states, conflict zones).

This script joins the per-food baseline diet with FAOSTAT FS anchors
(ADER, MDER, DES) and emits a per-country status report. It is a
diagnostic only — it does not modify the baseline diet. Downstream
analyses (or a future corrective rescaling step) can read the report to
filter outlier countries.

Output columns:
    country, gdd_kcal, ader_kcal, mder_kcal, des_kcal,
    gdd_over_ader, gdd_over_mder, gdd_over_des, status

Status categories: ok | low | below-MDER | high | above-DES | no-anchor.
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# Thresholds expressed relative to FAOSTAT anchors. Tuned against the
# observed baseline_diet distribution (median GDD/ADER ~= 0.88, with a
# long lower tail at ~0.5). MDER is a hard physiological floor; we allow
# a 15% margin to absorb survey/anchor measurement noise.
LOW_RATIO = 0.70  # gdd/ader below this is "low"
HIGH_RATIO = 1.40  # gdd/ader above this is "high"
MDER_MARGIN = 0.85  # gdd below MDER * this is "below-MDER"
DES_MARGIN = 1.05  # gdd above DES * this is "above-DES"


def classify(row: pd.Series) -> str:
    """Assign a status label based on GDD vs FAOSTAT anchors."""
    if pd.isna(row["ader_kcal"]) or pd.isna(row["gdd_kcal"]):
        return "no-anchor"
    if pd.notna(row["mder_kcal"]) and row["gdd_kcal"] < MDER_MARGIN * row["mder_kcal"]:
        return "below-MDER"
    if pd.notna(row["des_kcal"]) and row["gdd_kcal"] > DES_MARGIN * row["des_kcal"]:
        return "above-DES"
    ratio = row["gdd_kcal"] / row["ader_kcal"]
    if ratio < LOW_RATIO:
        return "low"
    if ratio > HIGH_RATIO:
        return "high"
    return "ok"


def main():
    baseline_diet_path = snakemake.input.baseline_diet
    anchors_path = snakemake.input.anchors
    nutrition_path = snakemake.input.nutrition
    output_path = snakemake.output.report
    countries = [str(c).upper() for c in snakemake.params.countries]

    bd = pd.read_csv(baseline_diet_path)
    nut = pd.read_csv(nutrition_path)
    anchors = pd.read_csv(anchors_path)

    # Compute per-country kcal totals from the baseline diet
    cal = nut[nut["nutrient"] == "cal"].set_index("food")["value"]
    missing_cal = sorted(set(bd["food"]) - set(cal.index))
    if missing_cal:
        logger.warning(
            "Foods in baseline_diet without nutrition.cal entry: %s",
            ", ".join(missing_cal),
        )
    bd["kcal"] = bd["consumption_g_per_day_intake"] * bd["food"].map(cal) / 100
    gdd = bd.groupby("country", as_index=False)["kcal"].sum()
    gdd = gdd.rename(columns={"kcal": "gdd_kcal"})

    # Restrict the report to the configured country universe. baseline_diet
    # may carry extra countries left over from upstream FAOSTAT/GDD data
    # that aren't in the model; anchors may miss small territories absent
    # from FAOSTAT (e.g. ASM, GUF). Build the report from the config list
    # so missing-anchor and missing-GDD countries both surface.
    universe = pd.DataFrame({"country": countries})
    report = universe.merge(anchors, on="country", how="left").merge(
        gdd, on="country", how="left"
    )
    report["gdd_over_ader"] = report["gdd_kcal"] / report["ader_kcal"]
    report["gdd_over_mder"] = report["gdd_kcal"] / report["mder_kcal"]
    report["gdd_over_des"] = report["gdd_kcal"] / report["des_kcal"]
    report["status"] = report.apply(classify, axis=1)

    report = report[
        [
            "country",
            "gdd_kcal",
            "ader_kcal",
            "mder_kcal",
            "des_kcal",
            "gdd_over_ader",
            "gdd_over_mder",
            "gdd_over_des",
            "status",
        ]
    ].sort_values("country")
    report.to_csv(output_path, index=False)

    counts = report["status"].value_counts().to_dict()
    logger.info(
        "Baseline-diet validation: ok=%d, low=%d, below-MDER=%d, "
        "high=%d, above-DES=%d, no-anchor=%d (of %d countries)",
        counts.get("ok", 0),
        counts.get("low", 0),
        counts.get("below-MDER", 0),
        counts.get("high", 0),
        counts.get("above-DES", 0),
        counts.get("no-anchor", 0),
        len(report),
    )

    flagged = report[report["status"].isin(["below-MDER", "above-DES"])].copy()
    if not flagged.empty:
        flagged_str = flagged.sort_values("status").to_string(
            index=False,
            float_format=lambda v: "" if pd.isna(v) else f"{v:.2f}",
        )
        logger.warning(
            "Countries with physically implausible baseline-diet kcal totals "
            "(below 0.85 x MDER or above 1.05 x DES):\n%s",
            flagged_str,
        )


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
