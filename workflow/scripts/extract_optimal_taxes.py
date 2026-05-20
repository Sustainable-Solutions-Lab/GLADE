# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract optimal taxes from food group equality constraint duals.

Taxes represent the Pigouvian tax (positive) or subsidy (negative) needed
to incentivize the optimal consumption pattern derived in Stage 1.

The dual variable (shadow price) of each consumption constraint indicates
the marginal cost of forcing that consumption level when only production
costs are considered. This is the tax/subsidy that would make the
production-cost-only equilibrium match the health/GHG-optimal outcome.

Interpretation:
- Positive dual: Increasing consumption at this constraint increases production
  cost. The health/GHG-optimal consumption is higher than what pure production
  costs would yield, so a SUBSIDY is needed to encourage more consumption.
- Negative dual: Increasing consumption would decrease production cost (e.g.,
  due to economies of scale or efficient utilization). The optimal consumption
  is lower, so a TAX is needed to discourage consumption.

Therefore: tax = -dual (positive tax discourages, negative tax = subsidy encourages)
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def extract_optimal_taxes(n: pypsa.Network) -> pd.DataFrame:
    """Extract optimal taxes from food group equality constraint duals.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with food group equality constraints.

    Returns
    -------
    pd.DataFrame
        Columns: group, country, tax_bnusd_per_mt, tax_usd_per_kg,
        adjustment_bnusd_per_mt
        Positive values indicate taxes (discourage consumption),
        negative values indicate subsidies (encourage consumption).
    """
    gc_df = n.global_constraints.static

    # Filter to food group equality constraints using the food_group column
    food_group_constraints = gc_df[
        gc_df["food_group"].notna() & gc_df.index.str.startswith("food_group_equal_")
    ]

    if food_group_constraints.empty:
        raise ValueError(
            "No food group equality constraints found in the network. "
            "Ensure the model was solved with fixed consumption constraints."
        )

    # Use columns to get group and country - no name parsing needed
    groups = food_group_constraints["food_group"].astype(str)
    countries = food_group_constraints["country"].astype(str).str.upper()
    duals = food_group_constraints["mu"].fillna(0.0).astype(float)

    # Tax = -dual
    # - If dual > 0: consumption is costly to production → need subsidy (tax < 0)
    # - If dual < 0: consumption saves production cost → need tax (tax > 0)
    taxes_bnusd_per_mt = -duals

    # Unit conversion: bnUSD/Mt = 1e9 USD / 1e9 kg = USD/kg
    df = pd.DataFrame(
        {
            "group": groups.values,
            "country": countries.values,
            "tax_bnusd_per_mt": taxes_bnusd_per_mt.values,
            "tax_usd_per_kg": taxes_bnusd_per_mt.values,
            "adjustment_bnusd_per_mt": taxes_bnusd_per_mt.values,
        }
    )

    logger.info("Extracted taxes for %d (group, country) pairs", len(df))
    return df


if __name__ == "__main__":
    setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    logger.info("Loading solved network from %s", snakemake.input.network)
    n = pypsa.Network(snakemake.input.network)

    df = extract_optimal_taxes(n)

    output_path = snakemake.output.taxes
    df.to_csv(output_path, index=False)
    logger.info("Saved optimal taxes to %s", output_path)
