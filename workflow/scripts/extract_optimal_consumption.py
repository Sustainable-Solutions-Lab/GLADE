# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract optimal consumption from a solved model's food group stores.

Reads the store levels for food group carriers and converts them to
per-capita g/day format for use as fixed consumption constraints in
the Stage 2 tax extraction solve.
"""

import pandas as pd
import pypsa

from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.population import get_country_population


def extract_optimal_consumption(
    n: pypsa.Network,
    population: dict[str, float],
    food_groups: list[str],
) -> pd.DataFrame:
    """Extract per-country consumption by food group from store levels.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with food group stores.
    population : dict[str, float]
        Population by country (ISO3 -> persons).
    food_groups : list[str]
        List of food group names to extract.

    Returns
    -------
    pd.DataFrame
        Columns: group, country, consumption_g_per_day
    """
    snapshot = "now" if "now" in n.snapshots else n.snapshots[-1]
    store_levels = n.stores.dynamic.e.loc[snapshot]

    pop_map = {k.upper(): v for k, v in population.items()}

    stores_df = n.stores.static

    records = []
    for group in food_groups:
        # Filter stores by carrier; use 'country' column for metadata
        group_stores = stores_df[stores_df["carrier"] == f"group_{group}"]

        for store_name in group_stores.index:
            country = str(group_stores.loc[store_name, "country"]).upper()
            if country not in pop_map:
                logger.warning(
                    "Country %s not found in population data, skipping store %s",
                    country,
                    store_name,
                )
                continue

            # Store level is in Mt/year
            level_mt = float(store_levels.get(store_name, 0.0))

            # Convert to g/person/day
            population = pop_map[country]
            g_per_day = level_mt * GRAMS_PER_MEGATONNE / (population * DAYS_PER_YEAR)

            records.append(
                {
                    "group": group,
                    "country": country,
                    "consumption_g_per_day": g_per_day,
                }
            )

    if not records:
        raise ValueError(
            "No food group consumption could be extracted. "
            "Check that the network has food group stores."
        )

    df = pd.DataFrame.from_records(records)
    logger.info(
        "Extracted consumption for %d (group, country) pairs",
        len(df),
    )
    return df


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    logger.info("Loading solved network from %s", snakemake.input.network)
    n = pypsa.Network(snakemake.input.network)

    population = get_country_population(n)
    food_groups_df = pd.read_csv(snakemake.input.food_groups)
    food_groups = food_groups_df["group"].unique().tolist()

    df = extract_optimal_consumption(n, population, food_groups)

    output_path = snakemake.output.consumption
    df.to_csv(output_path, index=False)
    logger.info(
        "Saved optimal consumption for %d (group, country) pairs to %s",
        len(df),
        output_path,
    )
