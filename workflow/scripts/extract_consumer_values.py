# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract consumer values (dual variables) from fixed food consumption links.

Consumer values represent the marginal value of consuming one additional unit
of each food, as revealed by the dual variables of p_set constraints on
food_consumption links. These values can be used to construct an objective
function that replicates consumer preferences.

Expects a solved network with:
- validation.enforce_baseline_diet=True (fixed per-food consumption via p_set)
- mu_p_set duals extracted to n.links.dynamic
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def extract_consumer_values(n: pypsa.Network) -> pd.DataFrame:
    """Extract consumer values from p_set duals on food consumption links.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with fixed food consumption links (p_set).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: food, food_group, country, value_bnusd_per_mt,
        adjustment_bnusd_per_mt. The adjustment column is the value with
        sign flipped for direct use as a marginal cost incentive.
    """
    links = n.links.static
    consume = links[links["carrier"] == "food_consumption"]

    if "mu_p_set" not in n.links.dynamic:
        raise ValueError(
            "No p_set duals found in the network. "
            "Ensure the model was solved with validation.enforce_baseline_diet=true"
        )

    mu_p_set = n.links.dynamic.mu_p_set
    snapshot = n.snapshots[-1]
    duals = mu_p_set.loc[snapshot].reindex(consume.index).fillna(0.0)

    # Only include links that actually had p_set (non-NaN duals)
    has_dual = duals != 0.0
    if "p_set" in n.links.dynamic:
        p_set = n.links.dynamic.p_set
        has_p_set = p_set.loc[snapshot].reindex(consume.index).notna()
        has_dual = has_dual | has_p_set

    consume = consume[has_dual]
    duals = duals[has_dual]

    if consume.empty:
        raise ValueError(
            "No fixed food consumption links found. "
            "Ensure the model was solved with validation.enforce_baseline_diet=true"
        )

    df = pd.DataFrame(
        {
            "food": consume["food"].astype(str).values,
            "food_group": consume["food_group"].astype(str).values,
            "country": consume["country"].astype(str).str.upper().values,
            "value_bnusd_per_mt": duals.values,
            "adjustment_bnusd_per_mt": -duals.values,
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
