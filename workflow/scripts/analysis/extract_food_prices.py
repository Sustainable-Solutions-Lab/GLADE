# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract food prices from bus marginal prices in solved networks.

Food bus shadow prices (marginal prices on the nodal balance constraint)
represent the marginal system cost of delivering one additional unit of
food at a given location. They naturally incorporate all upstream costs
(production, trade, processing) plus any externality pricing (GHG,
health). Units are bnUSD/Mt = USD/kg.

Outputs:
- food_prices.parquet: Per-food, per-country prices (USD/kg) with
  consumption-weighted averages and per-capita daily diet cost.
"""

import logging

import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.constants import DAYS_PER_YEAR
from workflow.scripts.population import get_country_population

logger = logging.getLogger(__name__)


def extract_food_prices(n: pypsa.Network) -> pd.DataFrame:
    """Extract food prices from bus marginal prices.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with marginal prices computed.

    Returns
    -------
    pd.DataFrame
        Columns: food, food_group, country, price_usd_per_kg,
        consumption_mt, cost_bnusd, cost_usd_per_person_per_day.
    """
    columns = [
        "food",
        "food_group",
        "country",
        "price_usd_per_kg",
        "consumption_mt",
        "cost_bnusd",
        "cost_usd_per_person_per_day",
    ]

    links = n.links.static
    consume_links = links[links["carrier"] == "food_consumption"]

    if consume_links.empty:
        return pd.DataFrame(columns=columns)

    # Get marginal prices on food buses (bnUSD/Mt = USD/kg)
    if "marginal_price" not in n.buses.dynamic:
        logger.warning("No marginal prices found in network; returning empty prices")
        return pd.DataFrame(columns=columns)

    marginal_price = n.buses.dynamic.marginal_price.iloc[0]

    # Get food consumption flows (p0 on consume links)
    p0 = n.links.dynamic.p0
    snapshot = n.snapshots[-1]
    consumption = p0.loc[snapshot].reindex(consume_links.index).fillna(0.0)

    population = pd.Series(get_country_population(n), dtype=float)

    df = consume_links[["food", "food_group", "country", "bus0"]].copy()
    df = df.astype({"food": str, "food_group": str, "country": str})
    df["consumption_mt"] = consumption.to_numpy()
    df = df[df["consumption_mt"] >= 1e-12]
    if df.empty:
        return pd.DataFrame(columns=columns)

    df["price_usd_per_kg"] = df["bus0"].map(marginal_price).fillna(0.0)
    df["cost_bnusd"] = df["price_usd_per_kg"] * df["consumption_mt"]
    pop = df["country"].map(population)
    df["cost_usd_per_person_per_day"] = np.where(
        pop > 0,
        (df["cost_bnusd"] * 1e9) / (pop.to_numpy() * DAYS_PER_YEAR),
        0.0,
    )
    df = df.drop(columns="bus0")

    # If duplicates exist across (food, food_group, country), the price
    # should be identical (it comes from the dual on the same food bus)
    # while flow-quantity columns sum. agg("first") on price would
    # silently drop divergent prices; assert uniqueness instead.
    group_cols = ["food", "food_group", "country"]
    if df.duplicated(subset=group_cols).any():
        prices_per_group = df.groupby(group_cols)["price_usd_per_kg"].nunique()
        if (prices_per_group > 1).any():
            offenders = prices_per_group[prices_per_group > 1].head().to_dict()
            raise ValueError(
                "Duplicate (food, food_group, country) rows with divergent "
                f"prices in food-price extraction: {offenders}"
            )
    df = df.groupby(group_cols, as_index=False).agg(
        {
            "price_usd_per_kg": "first",
            "consumption_mt": "sum",
            "cost_bnusd": "sum",
            "cost_usd_per_person_per_day": "sum",
        }
    )

    return df.sort_values(["country", "food"]).reset_index(drop=True)
