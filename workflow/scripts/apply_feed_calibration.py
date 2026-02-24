# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Apply feed efficiency calibration to baseline and efficiencies.

Reads uncalibrated feed baseline, uncalibrated feed-to-animal-product
efficiencies, and calibration multipliers.  Produces calibrated versions
of both files:

1. **feed_to_animal_products.csv** — efficiencies multiplied by calibration
   multipliers.
2. **feed_baseline.csv** — feed amounts adjusted so that implied production
   (feed x calibrated efficiency) remains unchanged relative to the
   uncalibrated version.
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def apply_calibration(
    feed_baseline_path: str,
    feed_to_products_path: str,
    calibration_path: str,
    output_baseline_path: str,
    output_efficiencies_path: str,
) -> None:
    """Apply calibration multipliers to feed efficiencies and baseline."""
    baseline = pd.read_csv(feed_baseline_path, comment="#")
    feed_eff = pd.read_csv(feed_to_products_path, comment="#")
    cal = pd.read_csv(calibration_path, comment="#")

    # --- Calibrate efficiencies ---
    feed_eff = feed_eff.merge(
        cal[["country", "product", "feed_category", "multiplier"]],
        on=["country", "product", "feed_category"],
        how="left",
    )
    feed_eff["multiplier"] = feed_eff["multiplier"].fillna(1.0)
    n_cal = int((feed_eff["multiplier"] != 1.0).sum())
    logger.info(
        "Applied calibration to %d/%d feed efficiencies (median mult %.3f)",
        n_cal,
        len(feed_eff),
        feed_eff.loc[feed_eff["multiplier"] != 1.0, "multiplier"].median()
        if n_cal
        else 1.0,
    )
    feed_eff["efficiency"] *= feed_eff["multiplier"]
    feed_eff = feed_eff.drop(columns=["multiplier"])
    feed_eff.to_csv(output_efficiencies_path, index=False)
    logger.info("Wrote calibrated efficiencies to %s", output_efficiencies_path)

    # --- Calibrate baseline ---
    # Compute adjustment factor per (country, product) so that implied
    # production (sum of feed x efficiency) is preserved after calibration.
    # adj = sum(feed * eff_uncal) / sum(feed * eff_cal)
    eff_uncal = pd.read_csv(feed_to_products_path, comment="#")
    merged = baseline.merge(
        eff_uncal[["country", "product", "feed_category", "efficiency"]].rename(
            columns={"efficiency": "eff_uncal"}
        ),
        on=["country", "product", "feed_category"],
        how="left",
    )
    merged = merged.merge(
        feed_eff[["country", "product", "feed_category", "efficiency"]].rename(
            columns={"efficiency": "eff_cal"}
        ),
        on=["country", "product", "feed_category"],
        how="left",
    )
    merged["eff_uncal"] = merged["eff_uncal"].fillna(0.0)
    merged["eff_cal"] = merged["eff_cal"].fillna(0.0)

    merged["implied_uncal"] = merged["feed_use_mt_dm"] * merged["eff_uncal"]
    merged["implied_cal"] = merged["feed_use_mt_dm"] * merged["eff_cal"]

    adj = merged.groupby(["country", "product"])[["implied_uncal", "implied_cal"]].sum()
    adj["adj_factor"] = 1.0
    has_cal = adj["implied_cal"] > 1e-10
    adj.loc[has_cal, "adj_factor"] = (
        adj.loc[has_cal, "implied_uncal"] / adj.loc[has_cal, "implied_cal"]
    )

    n_adjusted = int((adj["adj_factor"] != 1.0).sum())
    logger.info(
        "Baseline adjustment: %d/%d (country, product) pairs adjusted "
        "(median adj %.3f)",
        n_adjusted,
        len(adj),
        adj.loc[adj["adj_factor"] != 1.0, "adj_factor"].median() if n_adjusted else 1.0,
    )

    # Apply adjustment to baseline
    baseline_idx = baseline.set_index(["country", "product"])
    baseline_idx["adj_factor"] = adj["adj_factor"]
    baseline_idx["adj_factor"] = baseline_idx["adj_factor"].fillna(1.0)
    baseline_idx["feed_use_mt_dm"] *= baseline_idx["adj_factor"]
    baseline_idx["exogenous_mt_dm"] *= baseline_idx["adj_factor"]
    baseline = baseline_idx.drop(columns=["adj_factor"]).reset_index()

    baseline.to_csv(output_baseline_path, index=False)
    logger.info("Wrote calibrated baseline to %s", output_baseline_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    apply_calibration(
        feed_baseline_path=snakemake.input.feed_baseline,  # type: ignore[name-defined]
        feed_to_products_path=snakemake.input.feed_to_products,  # type: ignore[name-defined]
        calibration_path=snakemake.input.calibration,  # type: ignore[name-defined]
        output_baseline_path=snakemake.output.feed_baseline,  # type: ignore[name-defined]
        output_efficiencies_path=snakemake.output.feed_to_products,  # type: ignore[name-defined]
    )
