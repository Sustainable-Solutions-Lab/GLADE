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

    population = get_country_population(n)

    rows = []
    for link_name, link in consume_links.iterrows():
        food = str(link["food"])
        food_group = str(link["food_group"])
        country = str(link["country"])

        # Food bus: bus0 of the consume link
        food_bus = link["bus0"]
        price = float(marginal_price.get(food_bus, 0.0))

        # Consumption in Mt
        cons_mt = float(consumption.get(link_name, 0.0))
        if cons_mt < 1e-12:
            continue

        # Total cost in bnUSD
        cost_bnusd = price * cons_mt

        # Per-capita daily cost (USD/person/day)
        pop = population.get(country, 0.0)
        if pop > 0:
            cost_per_person_day = (cost_bnusd * 1e9) / (pop * DAYS_PER_YEAR)
        else:
            cost_per_person_day = 0.0

        rows.append(
            {
                "food": food,
                "food_group": food_group,
                "country": country,
                "price_usd_per_kg": price,
                "consumption_mt": cons_mt,
                "cost_bnusd": cost_bnusd,
                "cost_usd_per_person_per_day": cost_per_person_day,
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)

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
