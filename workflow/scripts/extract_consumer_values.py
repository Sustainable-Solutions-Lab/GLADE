# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract consumer values (dual variables) from per-food equality constraints.

Consumer values represent the marginal value of consuming one additional unit
of each food, as revealed by the dual variables of fixed consumption
constraints. These values can be used to construct an objective function that
replicates consumer preferences.

Expects a solved network with:
- validation.enforce_baseline_diet=True (fixed per-food consumption)
- Global constraints with food, food_group, and country columns set
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def extract_consumer_values(n: pypsa.Network) -> pd.DataFrame:
    """Extract consumer values from per-food equality constraint duals.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with per-food equality constraints.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: food, food_group, country, value_bnusd_per_mt,
        adjustment_bnusd_per_mt. The adjustment column is the value with
        sign flipped for direct use as a marginal cost incentive.
    """
    gc_df = n.global_constraints.static

    if gc_df.empty:
        raise ValueError(
            "No food equality constraints found in the network. "
            "Ensure the model was solved with validation.enforce_baseline_diet=true"
        )

    # Filter to per-food equality constraints
    food_constraints = gc_df[
        gc_df.index.str.startswith("food_equal_")
        & (gc_df["type"] == "food_consumption")
    ]

    if food_constraints.empty:
        raise ValueError(
            "No food equality constraints found in the network. "
            "Ensure the model was solved with validation.enforce_baseline_diet=true"
        )

    df = pd.DataFrame(
        {
            "food": food_constraints["food"].astype(str).values,
            "food_group": food_constraints["food_group"].astype(str).values,
            "country": food_constraints["country"].astype(str).str.upper().values,
            "value_bnusd_per_mt": food_constraints["mu"].fillna(0.0).values,
            "adjustment_bnusd_per_mt": -food_constraints["mu"].fillna(0.0).values,
        }
    )

    logger.info(
        "Extracted consumer values for %d (food, country) pairs (%d unique foods)",
        len(df),
        df["food"].nunique(),
    )
    return df


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", snakemake.input.network)
    n = pypsa.Network(snakemake.input.network)

    df = extract_consumer_values(n)

    output_path = snakemake.output.consumer_values
    df.to_csv(output_path, index=False)
    logger.info("Saved consumer values to %s", output_path)
