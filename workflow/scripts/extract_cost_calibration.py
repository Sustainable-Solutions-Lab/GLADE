# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract production cost calibration corrections from stability constraint duals.

When the model is solved with hard production stability constraints, the dual
variables (shadow prices) on the min/max bounds indicate how much the model
would gain from relaxing those bounds. This script converts those duals into
additive cost corrections per component group:

- mu on lower bound > 0 → model wants to produce less → cost is too low → positive correction
- mu on upper bound > 0 → model wants to produce more → cost is too high → negative correction

correction = median(-(mu_lower + mu_upper)) per group

Outputs three CSVs:
  - crop:      (crop, country, correction_bnusd_per_mha)
  - grassland: (country, correction_bnusd_per_mha)
  - animal:    (product, country, correction_bnusd_per_mt)
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def extract_corrections(
    n: pypsa.Network,
    prefix: str,
    carrier: str,
    group_cols: list[str],
) -> pd.DataFrame:
    """Extract per-group cost corrections from stability constraint duals.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with global constraints.
    prefix : str
        Constraint name prefix, e.g. "crop_production". Constraints are
        expected to be named ``{prefix}_min_{link_name}`` and
        ``{prefix}_max_{link_name}``.
    carrier : str
        Link carrier to filter for metadata lookup.
    group_cols : list[str]
        Metadata columns to aggregate by (e.g. ["crop", "country"]).

    Returns
    -------
    pd.DataFrame
        DataFrame with ``[*group_cols, "correction"]`` columns.
    """
    gc = n.global_constraints.static
    stab = gc[gc["type"] == "production_stability"]

    if stab.empty:
        raise RuntimeError(
            "No production_stability constraints found in solved network. "
            "Ensure production_stability is enabled with penalty_mode='hard'."
        )

    prefix_min = f"{prefix}_min_"
    prefix_max = f"{prefix}_max_"

    min_mask = stab.index.str.startswith(prefix_min)
    max_mask = stab.index.str.startswith(prefix_max)

    min_constraints = stab[min_mask].copy()
    max_constraints = stab[max_mask].copy()

    logger.info(
        "Found %d min and %d max %s stability constraints",
        len(min_constraints),
        len(max_constraints),
        prefix,
    )

    if min_constraints.empty and max_constraints.empty:
        logger.warning("No %s stability constraints found", prefix)
        return pd.DataFrame(columns=[*group_cols, "correction"])

    # Extract link names from constraint names
    min_constraints["link_name"] = min_constraints.index.str[len(prefix_min) :]
    max_constraints["link_name"] = max_constraints.index.str[len(prefix_max) :]

    # Get mu (dual values)
    min_constraints["mu_lower"] = min_constraints["mu"].fillna(0.0).astype(float)
    max_constraints["mu_upper"] = max_constraints["mu"].fillna(0.0).astype(float)

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

    all_links = sorted(set(min_duals.index) | set(max_duals.index))
    corrections = -(
        min_duals.reindex(all_links, fill_value=0.0)
        + max_duals.reindex(all_links, fill_value=0.0)
    )

    # Map link names to group columns using link metadata
    links = n.links.static
    carrier_links = links[links["carrier"] == carrier]
    link_meta = carrier_links[group_cols].copy()

    corrections_df = pd.DataFrame(
        {"correction": corrections.values},
        index=corrections.index,
    )
    corrections_df = corrections_df.join(link_meta, how="inner")

    if corrections_df.empty:
        logger.warning("No %s links matched stability constraints", carrier)
        return pd.DataFrame(columns=[*group_cols, "correction"])

    # Aggregate to per-group: take median of per-link corrections
    result = corrections_df.groupby(group_cols)["correction"].median().reset_index()

    # Log summary statistics
    nonzero = result["correction"].abs() > 1e-10
    logger.info(
        "%s corrections: %d groups, %d non-zero (%.1f%%)",
        prefix,
        len(result),
        nonzero.sum(),
        100.0 * nonzero.sum() / max(len(result), 1),
    )

    positive = result["correction"] > 1e-10
    negative = result["correction"] < -1e-10
    if positive.any():
        logger.info(
            "  Positive (cost increase): %d, median=%.4f",
            positive.sum(),
            result.loc[positive, "correction"].median(),
        )
    if negative.any():
        logger.info(
            "  Negative (cost decrease): %d, median=%.4f",
            negative.sum(),
            result.loc[negative, "correction"].median(),
        )

    return result.sort_values(group_cols).reset_index(drop=True)


def main() -> None:
    network_path = snakemake.input.network

    logger.info("Loading solved network from %s", network_path)
    n = pypsa.Network(str(network_path))

    # --- Crop corrections ---
    crop_result = extract_corrections(
        n,
        prefix="crop_production",
        carrier="crop_production",
        group_cols=["crop", "country"],
    )
    crop_result = crop_result.rename(columns={"correction": "correction_bnusd_per_mha"})

    crop_path = Path(snakemake.output.crop_correction)
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop_result.to_csv(crop_path, index=False)
    logger.info("Wrote %d crop corrections to %s", len(crop_result), crop_path)

    # --- Grassland corrections ---
    grassland_result = extract_corrections(
        n,
        prefix="grassland_production",
        carrier="grassland_production",
        group_cols=["country"],
    )
    grassland_result = grassland_result.rename(
        columns={"correction": "correction_bnusd_per_mha"}
    )

    grassland_path = Path(snakemake.output.grassland_correction)
    grassland_path.parent.mkdir(parents=True, exist_ok=True)
    grassland_result.to_csv(grassland_path, index=False)
    logger.info(
        "Wrote %d grassland corrections to %s",
        len(grassland_result),
        grassland_path,
    )

    # --- Animal corrections ---
    animal_result = extract_corrections(
        n,
        prefix="animal_production",
        carrier="animal_production",
        group_cols=["product", "country"],
    )
    animal_result = animal_result.rename(
        columns={"correction": "correction_bnusd_per_mt"}
    )

    animal_path = Path(snakemake.output.animal_correction)
    animal_path.parent.mkdir(parents=True, exist_ok=True)
    animal_result.to_csv(animal_path, index=False)
    logger.info("Wrote %d animal corrections to %s", len(animal_result), animal_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
