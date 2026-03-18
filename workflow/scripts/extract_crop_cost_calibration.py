# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract crop cost calibration corrections from production stability duals.

When the model is solved with hard production stability constraints, the dual
variables (shadow prices) on the min/max bounds indicate how much the model
would gain from relaxing those bounds. This script converts those duals into
additive cost corrections per (crop, country):

- mu on lower bound > 0 → model wants to produce less → cost is too low → positive correction
- mu on upper bound > 0 → model wants to produce more → cost is too high → negative correction

correction = median(mu_lower - mu_upper) per (crop, country)

Output: CSV with columns (crop, country, correction_bnusd_per_mha)
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def extract_crop_cost_calibration(n: pypsa.Network) -> pd.DataFrame:
    """Extract per-(crop, country) cost corrections from stability duals."""

    gc = n.global_constraints.static
    stab = gc[gc["type"] == "production_stability"]

    if stab.empty:
        raise RuntimeError(
            "No production_stability constraints found in solved network. "
            "Ensure production_stability is enabled with penalty_mode='hard'."
        )

    # Split into min and max constraints
    min_mask = stab.index.str.startswith("crop_production_min_")
    max_mask = stab.index.str.startswith("crop_production_max_")

    min_constraints = stab[min_mask].copy()
    max_constraints = stab[max_mask].copy()

    logger.info(
        "Found %d min and %d max crop production stability constraints",
        len(min_constraints),
        len(max_constraints),
    )

    if min_constraints.empty and max_constraints.empty:
        raise RuntimeError(
            "No crop_production stability constraints found. "
            "Ensure production_stability.crops.enabled is true."
        )

    # Extract link names from constraint names
    prefix_min = "crop_production_min_"
    prefix_max = "crop_production_max_"

    min_constraints["link_name"] = min_constraints.index.str[len(prefix_min) :]
    max_constraints["link_name"] = max_constraints.index.str[len(prefix_max) :]

    # Get mu (dual values)
    min_constraints["mu_lower"] = min_constraints["mu"].fillna(0.0).astype(float)
    max_constraints["mu_upper"] = max_constraints["mu"].fillna(0.0).astype(float)

    # Look up crop and country from link metadata
    links = n.links.static
    crop_links = links[links["carrier"] == "crop_production"]

    # Build per-link correction: -(mu_lower + mu_upper)
    # Standard LP dual signs for minimization:
    #   >= constraint: mu_lower >= 0 when binding
    #   <= constraint: mu_upper <= 0 when binding
    # Correction logic:
    #   mu_lower > 0 → model forced to produce more than it wants → decrease cost
    #     → correction = -mu_lower (negative)
    #   mu_upper < 0 → model forced to produce less than it wants → increase cost
    #     → correction = -mu_upper (positive)
    # Combined: correction = -(mu_lower + mu_upper)
    min_duals = min_constraints.set_index("link_name")["mu_lower"]
    max_duals = max_constraints.set_index("link_name")["mu_upper"]

    # Combine duals on link names
    all_links = sorted(set(min_duals.index) | set(max_duals.index))
    corrections = -(
        min_duals.reindex(all_links, fill_value=0.0)
        + max_duals.reindex(all_links, fill_value=0.0)
    )

    # Map link names to (crop, country) using link metadata
    link_meta = crop_links[["crop", "country"]].copy()
    corrections_df = pd.DataFrame(
        {
            "correction": corrections.values,
        },
        index=corrections.index,
    )
    corrections_df = corrections_df.join(link_meta, how="inner")

    if corrections_df.empty:
        logger.warning("No crop production links matched stability constraints")
        return pd.DataFrame(columns=["crop", "country", "correction_bnusd_per_mha"])

    # Aggregate to per (crop, country): take median of per-link corrections
    result = (
        corrections_df.groupby(["crop", "country"])["correction"]
        .median()
        .reset_index()
        .rename(columns={"correction": "correction_bnusd_per_mha"})
    )

    # Log summary statistics
    nonzero = result["correction_bnusd_per_mha"].abs() > 1e-10
    logger.info(
        "Calibration corrections: %d (crop, country) pairs, %d non-zero (%.1f%%)",
        len(result),
        nonzero.sum(),
        100.0 * nonzero.sum() / max(len(result), 1),
    )

    positive = result["correction_bnusd_per_mha"] > 1e-10
    negative = result["correction_bnusd_per_mha"] < -1e-10
    logger.info(
        "  Positive (cost increase): %d, median=%.4f bnUSD/Mha",
        positive.sum(),
        result.loc[positive, "correction_bnusd_per_mha"].median()
        if positive.any()
        else 0,
    )
    logger.info(
        "  Negative (cost decrease): %d, median=%.4f bnUSD/Mha",
        negative.sum(),
        result.loc[negative, "correction_bnusd_per_mha"].median()
        if negative.any()
        else 0,
    )

    return result.sort_values(["crop", "country"]).reset_index(drop=True)


def main() -> None:
    network_path = snakemake.input.network
    output_path = Path(snakemake.output.correction)

    logger.info("Loading solved network from %s", network_path)
    n = pypsa.Network(str(network_path))

    result = extract_crop_cost_calibration(n)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    logger.info("Wrote %d calibration corrections to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
