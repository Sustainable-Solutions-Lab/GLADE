# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute grassland forage calibration from a solved model.

Reads the solved network's forage slack to derive per-country grassland yield
corrections and exogenous forage amounts.

**Surplus countries** (negative slack > 0, i.e. grassland output exceeds demand):
  yield_correction = max(0, grassland_output - surplus) / grassland_output
  This scales grassland yields down so supply matches demand.

**Deficit countries** (positive slack > 0, i.e. demand exceeds grassland supply):
  exogenous_forage_mt_dm = positive_slack
  This adds an external forage source to cover the shortfall.

Output CSV: country, yield_correction, exogenous_forage_mt_dm
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.constants import SPDX_CSV_HEADER
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _aggregate_forage_bus_supply_and_demand(
    n: pypsa.Network,
) -> tuple[pd.Series, pd.Series]:
    """Return non-grass forage supply and forage demand by country.

    The ``feed:ruminant_forage:{country}`` bus mixes grassland production with
    other forage sources such as alfalfa/fodder crop conversion and exogenous
    generators.  For grassland calibration we only want to reduce the portion of
    surplus that is actually attributable to grassland, not surplus caused by
    these other sources.
    """
    forage_buses = n.buses.static.index[
        n.buses.static.index.str.startswith("feed:ruminant_forage:")
    ]
    if forage_buses.empty:
        empty = pd.Series(dtype=float)
        return empty, empty

    country_by_bus = pd.Series(
        forage_buses.str.extract(r"^feed:ruminant_forage:(.+)$")[0].values,
        index=forage_buses,
    )
    non_grass_supply_by_country = pd.Series(0.0, index=country_by_bus.unique())
    demand_by_country = pd.Series(0.0, index=country_by_bus.unique())

    # Supply from link output ports on the forage bus, excluding grassland.
    for port in ("1", "2", "3", "4"):
        bus_col = f"bus{port}"
        p_col = f"p{port}"
        if bus_col not in n.links.static.columns or p_col not in n.links.dynamic:
            continue

        mask = n.links.static[bus_col].isin(forage_buses)
        if not mask.any():
            continue

        links = n.links.static.loc[mask]
        dispatch = -n.links.dynamic[p_col].iloc[0].reindex(links.index).fillna(0.0)

        is_grass = links["carrier"] == "grassland_production"
        if (~is_grass).any():
            grouped = (
                pd.DataFrame(
                    {
                        "country": country_by_bus.loc[
                            links.loc[~is_grass, bus_col]
                        ].values,
                        "supply": dispatch.loc[~is_grass].values,
                    }
                )
                .groupby("country")["supply"]
                .sum()
            )
            non_grass_supply_by_country = non_grass_supply_by_country.add(
                grouped, fill_value=0.0
            )

    # Supply from generators on the forage bus, excluding slack generators.
    gen_mask = n.generators.static["bus"].isin(forage_buses)
    if gen_mask.any():
        gens = n.generators.static.loc[gen_mask]
        gen_dispatch = n.generators.dynamic["p"].iloc[0].reindex(gens.index).fillna(0.0)
        is_slack = gens["carrier"].isin(["slack_positive_feed", "slack_negative_feed"])
        if (~is_slack).any():
            grouped = (
                pd.DataFrame(
                    {
                        "country": country_by_bus.loc[
                            gens.loc[~is_slack, "bus"]
                        ].values,
                        "supply": gen_dispatch.loc[~is_slack].values,
                    }
                )
                .groupby("country")["supply"]
                .sum()
            )
            non_grass_supply_by_country = non_grass_supply_by_country.add(
                grouped, fill_value=0.0
            )

    # Demand from animal production links drawing from the forage bus at bus0.
    if "p0" in n.links.dynamic:
        demand_links = n.links.static[
            (n.links.static["bus0"].isin(forage_buses))
            & (n.links.static["carrier"] == "animal_production")
        ]
        if not demand_links.empty:
            grouped = (
                pd.DataFrame(
                    {
                        "country": country_by_bus.loc[demand_links["bus0"]].values,
                        "demand": n.links.dynamic["p0"]
                        .iloc[0]
                        .reindex(demand_links.index)
                        .abs()
                        .values,
                    }
                )
                .groupby("country")["demand"]
                .sum()
            )
            demand_by_country = demand_by_country.add(grouped, fill_value=0.0)

    return non_grass_supply_by_country, demand_by_country


def compute_grassland_calibration(
    network_path: str,
    output_path: str,
) -> None:
    """Compute and write grassland forage calibration.

    Parameters
    ----------
    network_path : str
        Path to solved PyPSA network (.nc).
    output_path : str
        Path for output calibration CSV.
    """
    n = pypsa.Network(network_path)

    # --- Grassland forage output per country ---
    grass_links = n.links.static[n.links.static["carrier"] == "grassland_production"]
    if grass_links.empty:
        logger.warning("No grassland_production links found; writing empty calibration")
        pd.DataFrame(
            columns=["country", "yield_correction", "exogenous_forage_mt_dm"]
        ).to_csv(output_path, index=False)
        return

    # Grassland dispatch is on bus0 (land, in Mha); forage output = dispatch * efficiency
    dispatch = n.links.dynamic.p0[grass_links.index].iloc[0].abs()
    efficiency = grass_links["efficiency"].astype(float)
    forage_output = dispatch * efficiency
    grass_country = grass_links["country"].values
    grassland_by_country = (
        pd.DataFrame({"country": grass_country, "forage": forage_output.values})
        .groupby("country")["forage"]
        .sum()
    )
    non_grass_supply_by_country, demand_by_country = (
        _aggregate_forage_bus_supply_and_demand(n)
    )

    # --- Negative slack (surplus): grassland output exceeds demand ---
    neg_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_negative_feed"
    ]
    surplus_by_country = pd.Series(dtype=float, name="surplus")
    if not neg_slack_gens.empty:
        # Filter to ruminant_forage buses
        forage_mask = neg_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        neg_forage = neg_slack_gens[forage_mask]
        if not neg_forage.empty:
            neg_dispatch = n.generators.dynamic.p[neg_forage.index].iloc[0].abs()
            neg_countries = (
                neg_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            surplus_by_country = (
                pd.DataFrame({"country": neg_countries, "surplus": neg_dispatch.values})
                .groupby("country")["surplus"]
                .sum()
            )
            surplus_by_country = surplus_by_country[surplus_by_country > 1e-10]

    # --- Positive slack (deficit): demand exceeds grassland supply ---
    pos_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_positive_feed"
    ]
    deficit_by_country = pd.Series(dtype=float, name="deficit")
    if not pos_slack_gens.empty:
        forage_mask = pos_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        pos_forage = pos_slack_gens[forage_mask]
        if not pos_forage.empty:
            pos_dispatch = n.generators.dynamic.p[pos_forage.index].iloc[0]
            pos_countries = (
                pos_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            deficit_by_country = (
                pd.DataFrame({"country": pos_countries, "deficit": pos_dispatch.values})
                .groupby("country")["deficit"]
                .sum()
            )
            deficit_by_country = deficit_by_country[deficit_by_country > 1e-10]

    # --- Build calibration table ---
    all_countries = sorted(
        set(grassland_by_country.index)
        | set(surplus_by_country.index)
        | set(deficit_by_country.index)
    )
    rows = []
    for country in all_countries:
        grass = grassland_by_country.get(country, 0.0)
        surplus = surplus_by_country.get(country, 0.0)
        deficit = deficit_by_country.get(country, 0.0)
        non_grass_supply = non_grass_supply_by_country.get(country, 0.0)
        demand = demand_by_country.get(country, 0.0)

        # Only reduce the portion of forage surplus that is attributable to
        # grassland itself. If non-grass forage supply already overfills the
        # bus, grassland should not be driven to zero by that unrelated surplus.
        non_grass_surplus = max(0.0, non_grass_supply - demand)
        grass_surplus = max(0.0, surplus - non_grass_surplus)

        if grass_surplus > 0 and grass > 1e-10:
            yield_correction = max(0.0, grass - grass_surplus) / grass
        else:
            yield_correction = 1.0

        exogenous = deficit if deficit > 0 else 0.0

        rows.append(
            {
                "country": country,
                "yield_correction": round(yield_correction, 6),
                "exogenous_forage_mt_dm": round(exogenous, 6),
            }
        )

    result = pd.DataFrame(rows)

    n_surplus = int((result["yield_correction"] < 1.0).sum())
    n_deficit = int((result["exogenous_forage_mt_dm"] > 0).sum())
    total_surplus = surplus_by_country.sum() if not surplus_by_country.empty else 0.0
    total_deficit = deficit_by_country.sum() if not deficit_by_country.empty else 0.0

    logger.info(
        "Grassland calibration: %d countries with yield correction "
        "(%.1f Mt surplus removed), %d countries with exogenous forage "
        "(%.1f Mt deficit covered)",
        n_surplus,
        total_surplus,
        n_deficit,
        total_deficit,
    )

    with open(output_path, "w") as f:
        f.write(SPDX_CSV_HEADER)
        result.to_csv(f, index=False)
    logger.info("Wrote %d calibration entries to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    compute_grassland_calibration(
        network_path=snakemake.input.network,  # type: ignore[name-defined]
        output_path=snakemake.output[0],  # type: ignore[name-defined]
    )
