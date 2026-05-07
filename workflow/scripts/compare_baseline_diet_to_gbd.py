#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare baseline-diet GBD-risk-factor exposure to GBD's own intake estimates.

The model evaluates dietary disease burden by feeding consumption
through the IHME GBD relative-risk dose-response curves. For the
attributable burden to be consistent with what GBD itself estimates
when applied to a country's diet, the per-country intake of each risk
factor in the baseline diet should not deviate dramatically from
GBD's own intake estimate (after the same basis conversions that the
model applies internally).

This script:

1. Aggregates ``baseline_diet.csv`` per (country, food_group), summing
   each food's ``consumption_g_per_day_intake`` into the food-group it
   belongs to (only the GBD-covered risk groups).
2. Loads ``gbd_dietary_risk_exposure.csv`` (the GBD intake source) and
   applies the same cooked-to-dry conversion the diet pipeline applies
   to it (gated by ``health.gbd_intake_needs_conversion`` in config).
3. Computes the per-country ratio model_intake / gbd_intake for each
   risk factor.
4. Emits a tidy CSV plus a log summary highlighting countries with
   ratios outside [0.5, 2.0] for any risk factor.

Output:
    - CSV ``baseline_diet_risk_comparison.csv`` with columns:
      country, risk_factor, model_g_per_day, gbd_g_per_day, ratio
"""

import logging

import numpy as np
import pandas as pd

from workflow.scripts.diet.basis import (
    build_group_basis,
    convert_intake,
    load_food_basis,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def main():
    baseline_diet_path = snakemake.input.baseline_diet
    food_groups_path = snakemake.input.food_groups
    gbd_path = snakemake.input.gbd_exposure
    output_path = snakemake.output.report

    countries = [str(c).upper() for c in snakemake.params.countries]
    risk_factors = list(snakemake.params.risk_factors)
    source_basis = {
        src: {str(g): str(b) for g, b in groups.items()}
        for src, groups in dict(snakemake.params.source_basis).items()
    }
    source_basis_country_overrides = {
        src: {
            str(country): {str(g): str(b) for g, b in groups.items()}
            for country, groups in countries_overrides.items()
        }
        for src, countries_overrides in dict(
            snakemake.params.source_basis_country_overrides
        ).items()
    }
    weight_conversion = {
        str(table): {str(k): float(v) for k, v in entries.items()}
        for table, entries in dict(snakemake.params.weight_conversion).items()
    }

    food_groups = pd.read_csv(food_groups_path)
    fg_map = food_groups.set_index("food")["group"].to_dict()
    food_basis = load_food_basis(snakemake.input.food_basis)
    group_basis = build_group_basis(food_basis, fg_map)

    bd = pd.read_csv(baseline_diet_path)
    if "food_group" not in bd.columns or bd["food_group"].isna().any():
        bd["food_group"] = bd["food"].map(fg_map)
    bd = bd[bd["food_group"].isin(risk_factors)]
    model_per_group = (
        bd.groupby(["country", "food_group"], as_index=False)[
            "consumption_g_per_day_intake"
        ]
        .sum()
        .rename(columns={"consumption_g_per_day_intake": "model_g_per_day"})
    )

    gbd = pd.read_csv(gbd_path)
    gbd = gbd[gbd["food_group"].isin(risk_factors)]
    # Apply the same basis conversion the pipeline applies to GBD intake
    # so the comparison happens in a consistent basis. Per-country
    # overrides are honoured so the comparison reflects the same
    # conversions used by estimate_baseline_diet.
    gbd = convert_intake(
        gbd,
        source="gbd",
        value_column="consumption_g_per_day",
        group_column="food_group",
        country_column="country",
        source_basis=source_basis,
        source_basis_country_overrides=source_basis_country_overrides,
        group_basis=group_basis,
        factors=weight_conversion,
    )
    gbd_per_group = (
        gbd.groupby(["country", "food_group"], as_index=False)["consumption_g_per_day"]
        .mean()
        .rename(columns={"consumption_g_per_day": "gbd_g_per_day"})
    )

    universe = pd.MultiIndex.from_product(
        [countries, risk_factors], names=["country", "food_group"]
    ).to_frame(index=False)
    report = universe.merge(
        model_per_group, on=["country", "food_group"], how="left"
    ).merge(gbd_per_group, on=["country", "food_group"], how="left")
    report["ratio"] = report["model_g_per_day"] / report["gbd_g_per_day"]
    report = report.rename(columns={"food_group": "risk_factor"})
    report = report.sort_values(["country", "risk_factor"]).reset_index(drop=True)

    report.to_csv(output_path, index=False)

    # Summary statistics
    for rf, sub in report.groupby("risk_factor"):
        valid = sub.dropna(subset=["model_g_per_day", "gbd_g_per_day"])
        if valid.empty:
            continue
        ratio = (valid["ratio"]).replace([float("inf")], pd.NA).dropna()
        if ratio.empty:
            continue
        logger.info(
            "%s: median model/gbd = %.2f (n=%d, p25=%.2f, p75=%.2f)",
            rf,
            ratio.median(),
            len(ratio),
            ratio.quantile(0.25),
            ratio.quantile(0.75),
        )

    flagged = report[(report["ratio"] < 0.5) | (report["ratio"] > 2.0)].dropna(
        subset=["model_g_per_day", "gbd_g_per_day"]
    )
    flagged_n = len(flagged)
    flagged_country_n = flagged["country"].nunique()
    if flagged_n > 0:
        logger.warning(
            "%d (country, risk_factor) pairs have model/gbd ratio outside "
            "[0.5, 2.0] (covering %d countries). Top divergences:",
            flagged_n,
            flagged_country_n,
        )
        # Sort by |log(ratio)| so divergences in either direction rank symmetrically.
        flagged = flagged.copy()
        flagged["abs_log_ratio"] = np.log(flagged["ratio"]).abs()
        flagged = flagged.sort_values("abs_log_ratio", ascending=False)
        sample = flagged.head(15)[
            ["country", "risk_factor", "model_g_per_day", "gbd_g_per_day", "ratio"]
        ]
        logger.warning(
            "\n%s", sample.to_string(index=False, float_format=lambda v: f"{v:.2f}")
        )


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
