# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Calibrate piecewise food-utility blocks from baseline consumer values.

Uses baseline dual-derived consumer values and baseline food consumption levels
to construct a piecewise diminishing marginal utility schedule per
``(food, country)`` pair.
"""

import logging
import math

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_baseline_food_consumption(n: pypsa.Network) -> pd.DataFrame:
    """Load per-food baseline consumption (Mt/year) from p_set on consume links."""
    links = n.links.static
    consume = links[links["carrier"] == "food_consumption"]

    if "p_set" not in n.links.dynamic or n.links.dynamic.p_set.empty:
        raise ValueError(
            "No p_set values found on links. "
            "Ensure baseline scenario uses validation.enforce_baseline_diet=true."
        )

    p_set = n.links.dynamic.p_set
    snapshot = n.snapshots[-1]
    targets = p_set.loc[snapshot].reindex(consume.index)

    # Only include links that had p_set (non-NaN)
    has_target = targets.notna()
    consume = consume[has_target]
    targets = targets[has_target]

    if consume.empty:
        raise ValueError(
            "No fixed food consumption links found. "
            "Ensure baseline scenario uses validation.enforce_baseline_diet=true."
        )

    return pd.DataFrame(
        {
            "food": consume["food"].astype(str).values,
            "country": consume["country"].astype(str).str.upper().values,
            "baseline_mt_per_year": targets.fillna(0.0).values,
        }
    )


def _calibrate_blocks(
    merged: pd.DataFrame,
    n_blocks: int,
    decline_factor: float,
    total_width_multiplier: float,
) -> pd.DataFrame:
    """Build per-block widths and marginal utilities."""
    if n_blocks < 1:
        raise ValueError(f"n_blocks must be >= 1, got {n_blocks}")
    if decline_factor <= 0 or decline_factor > 1:
        raise ValueError(
            f"decline_factor must satisfy 0 < decline_factor <= 1, got {decline_factor}"
        )
    if total_width_multiplier <= 0:
        raise ValueError(
            "total_width_multiplier must be > 0, " f"got {total_width_multiplier}"
        )

    rows = []
    # Baseline quantity sits at fraction 1 / total_width_multiplier of total width.
    # With equal-width blocks, this corresponds to a (possibly fractional) block
    # position p = n_blocks / total_width_multiplier. We anchor the marginal
    # utility at the block containing this position.
    baseline_block_position = n_blocks / total_width_multiplier
    anchor_block_id = min(max(math.ceil(baseline_block_position), 1), n_blocks)

    for row in merged.itertuples(index=False):
        baseline_mt = float(row.baseline_mt_per_year)
        base_utility = float(row.value_bnusd_per_mt)
        width_mt = max(baseline_mt * total_width_multiplier / n_blocks, 1e-12)

        for block_id in range(1, n_blocks + 1):
            if base_utility >= 0:
                exponent = block_id - anchor_block_id
            else:
                exponent = anchor_block_id - block_id
            utility = base_utility * (decline_factor**exponent)

            rows.append(
                {
                    "food": row.food,
                    "country": row.country,
                    "block_id": block_id,
                    "width_mt_per_year": width_mt,
                    "marginal_utility_bnusd_per_mt": utility,
                    "baseline_mt_per_year": baseline_mt,
                    "base_value_bnusd_per_mt": base_utility,
                    "anchor_block_id": anchor_block_id,
                }
            )

    blocks_df = pd.DataFrame.from_records(rows)
    if blocks_df.empty:
        raise ValueError("No utility blocks generated from merged baseline/value data")

    return blocks_df.sort_values(["food", "country", "block_id"]).reset_index(drop=True)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    logger.info("Loading baseline solved network from %s", snakemake.input.network)
    n = pypsa.Network(snakemake.input.network)

    values_df = pd.read_csv(snakemake.input.consumer_values)
    required = {"food", "country", "value_bnusd_per_mt"}
    missing = required - set(values_df.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(
            f"Missing required columns in consumer values file: {missing_text}"
        )

    values_df = values_df[["food", "country", "value_bnusd_per_mt"]].copy()
    values_df["food"] = values_df["food"].astype(str)
    values_df["country"] = values_df["country"].astype(str).str.upper()
    values_df["value_bnusd_per_mt"] = pd.to_numeric(
        values_df["value_bnusd_per_mt"], errors="coerce"
    ).fillna(0.0)

    baseline_df = _load_baseline_food_consumption(n)
    merged = baseline_df.merge(
        values_df,
        on=["food", "country"],
        how="inner",
    )
    if merged.empty:
        raise ValueError(
            "No overlapping (food, country) pairs between baseline and values"
        )

    logger.info(
        "Calibrating piecewise utility for %d (food, country) pairs",
        len(merged),
    )

    n_blocks = int(snakemake.params.n_blocks)
    decline_factor = float(snakemake.params.decline_factor)
    total_width_multiplier = float(snakemake.params.total_width_multiplier)

    blocks_df = _calibrate_blocks(
        merged,
        n_blocks=n_blocks,
        decline_factor=decline_factor,
        total_width_multiplier=total_width_multiplier,
    )
    blocks_df.to_csv(snakemake.output.utility_blocks, index=False)

    logger.info(
        "Wrote %d utility block rows to %s",
        len(blocks_df),
        snakemake.output.utility_blocks,
    )
