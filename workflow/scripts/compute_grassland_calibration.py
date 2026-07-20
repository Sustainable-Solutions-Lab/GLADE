# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute forage calibration from a solved model.

Reads the solved network's forage slack to derive per-country corrections
for grassland area, fodder-to-forage conversion efficiency, and exogenous
forage amounts.

**Surplus countries** (negative slack > 0, i.e. supply exceeds demand):
  The grassland yield correction absorbs the surplus (grassland yield is the
  uncertain quantity this step calibrates -- an ISIMIP/LPJmL potential floor),
  while fodder-crop conversion keeps its observed level. Only when fodder alone
  over-supplies demand is fodder scaled down instead.

**Deficit countries** (positive slack > 0, i.e. demand exceeds supply):
  exogenous_forage_mt_dm = positive_slack
  This adds an external forage source to cover the shortfall.

Output: three separate CSVs for grassland area correction, fodder
conversion correction, and exogenous forage supply.
"""

import logging
from pathlib import Path

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
        dispatch = (
            -n.links.dynamic[p_col]
            .loc[n.snapshots[-1]]
            .reindex(links.index)
            .fillna(0.0)
        )

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
        gen_dispatch = (
            n.generators.dynamic["p"]
            .loc[n.snapshots[-1]]
            .reindex(gens.index)
            .fillna(0.0)
        )
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
                        .loc[n.snapshots[-1]]
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
    *,
    grassland_yield_path: str,
    fodder_conversion_path: str,
    exogenous_forage_path: str,
) -> None:
    """Compute and write forage calibration files.

    Produces three separate CSVs:

    1. **Grassland yield correction** — per-country factor to scale grassland
       yield (efficiency on grassland_production links).
    2. **Fodder conversion correction** — per-country factor to scale the
       efficiency of forage-crop → ruminant_forage conversion links.
    3. **Exogenous forage** — per-country Mt DM of external forage for
       deficit countries where supply cannot meet demand.

    Surplus is absorbed by the grassland yield correction (grassland is the
    uncertain supply this step calibrates); fodder conversion stays at 1.0
    unless fodder alone over-supplies demand.

    Parameters
    ----------
    network_path : str
        Path to solved PyPSA network (.nc).
    grassland_yield_path : str
        Output path for grassland yield correction CSV.
    fodder_conversion_path : str
        Output path for fodder conversion correction CSV.
    exogenous_forage_path : str
        Output path for exogenous forage CSV.
    """
    if Path(network_path).stat().st_size == 0:
        raise ValueError(
            f"Solved network file is empty: {network_path}\n"
            "This usually means the solve step failed (e.g. model was infeasible). "
            "Check the solve log for details."
        )

    n = pypsa.Network(network_path)

    # --- Grassland forage output per country ---
    grass_links = n.links.static[n.links.static["carrier"] == "grassland_production"]
    if grass_links.empty:
        logger.warning("No grassland_production links found; writing empty calibration")
        for path in (
            grassland_yield_path,
            fodder_conversion_path,
            exogenous_forage_path,
        ):
            with open(path, "w") as f:
                f.write(SPDX_CSV_HEADER)
            pd.DataFrame(columns=["country"]).to_csv(path, mode="a", index=False)
        return

    # Grassland dispatch is on bus0 (land, in Mha); forage output = dispatch * efficiency
    dispatch = n.links.dynamic.p0[grass_links.index].loc[n.snapshots[-1]].abs()
    efficiency = grass_links["efficiency"].astype(float)
    forage_output = dispatch * efficiency
    grass_country = grass_links["country"].values
    grassland_by_country = (
        pd.DataFrame({"country": grass_country, "forage": forage_output.values})
        .groupby("country")["forage"]
        .sum()
    )
    non_grass_supply_by_country, _demand_by_country = (
        _aggregate_forage_bus_supply_and_demand(n)
    )

    # --- Negative slack (surplus): supply exceeds demand ---
    neg_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_negative_feed"
    ]
    surplus_by_country = pd.Series(dtype=float, name="surplus")
    if not neg_slack_gens.empty:
        forage_mask = neg_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        neg_forage = neg_slack_gens[forage_mask]
        if not neg_forage.empty:
            neg_dispatch = (
                n.generators.dynamic.p[neg_forage.index].loc[n.snapshots[-1]].abs()
            )
            neg_countries = (
                neg_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            surplus_by_country = (
                pd.DataFrame({"country": neg_countries, "surplus": neg_dispatch.values})
                .groupby("country")["surplus"]
                .sum()
            )
            surplus_by_country = surplus_by_country[surplus_by_country > 1e-10]

    # --- Positive slack (deficit): demand exceeds supply ---
    pos_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_positive_feed"
    ]
    deficit_by_country = pd.Series(dtype=float, name="deficit")
    if not pos_slack_gens.empty:
        forage_mask = pos_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        pos_forage = pos_slack_gens[forage_mask]
        if not pos_forage.empty:
            pos_dispatch = n.generators.dynamic.p[pos_forage.index].loc[n.snapshots[-1]]
            pos_countries = (
                pos_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            deficit_by_country = (
                pd.DataFrame({"country": pos_countries, "deficit": pos_dispatch.values})
                .groupby("country")["deficit"]
                .sum()
            )
            deficit_by_country = deficit_by_country[deficit_by_country > 1e-10]

    # --- Build calibration tables with proportional surplus attribution ---
    all_countries = sorted(
        set(grassland_by_country.index)
        | set(surplus_by_country.index)
        | set(deficit_by_country.index)
    )
    grass_rows = []
    fodder_rows = []
    exo_rows = []
    for country in all_countries:
        grass = grassland_by_country.get(country, 0.0)
        surplus = surplus_by_country.get(country, 0.0)
        deficit = deficit_by_country.get(country, 0.0)
        non_grass = non_grass_supply_by_country.get(country, 0.0)

        total_supply = grass + non_grass
        demand = total_supply - surplus  # negative slack dispatched = supply - demand

        # Grassland absorbs the surplus, not fodder. Grassland yield is the
        # uncertain quantity (an ISIMIP/LPJmL potential floor that this step
        # exists to correct); fodder-crop conversion rests on real FAOSTAT crop
        # production and should keep its observed level. So scale grassland down
        # to supply (demand - fodder), leaving fodder_conversion at 1.0. Only
        # when fodder alone over-supplies demand do we scale fodder instead.
        grass_factor = 1.0
        fodder_factor = 1.0
        if surplus > 0:
            if grass >= surplus and grass > 1e-10:
                grass_factor = max(0.0, (grass - surplus) / grass)
            elif non_grass > 1e-10:
                grass_factor = 0.0
                fodder_factor = max(0.0, demand / non_grass)

        grass_rows.append(
            {"country": country, "yield_correction": round(grass_factor, 6)}
        )
        fodder_rows.append(
            {
                "country": country,
                "fodder_conversion_correction": round(fodder_factor, 6),
            }
        )
        exo_rows.append(
            {
                "country": country,
                "exogenous_forage_mt_dm": round(deficit if deficit > 0 else 0.0, 6),
            }
        )

    grass_df = pd.DataFrame(grass_rows)
    fodder_df = pd.DataFrame(fodder_rows)
    exo_df = pd.DataFrame(exo_rows)

    n_surplus = int((grass_df["yield_correction"] < 1.0).sum())
    n_deficit = int((exo_df["exogenous_forage_mt_dm"] > 0).sum())
    total_surplus = surplus_by_country.sum() if not surplus_by_country.empty else 0.0
    total_deficit = deficit_by_country.sum() if not deficit_by_country.empty else 0.0

    logger.info(
        "Forage calibration: %d countries with yield/conversion correction "
        "(%.1f Mt surplus), %d countries with exogenous forage "
        "(%.1f Mt deficit)",
        n_surplus,
        total_surplus,
        n_deficit,
        total_deficit,
    )

    for df, path in (
        (grass_df, grassland_yield_path),
        (fodder_df, fodder_conversion_path),
        (exo_df, exogenous_forage_path),
    ):
        with open(path, "w") as f:
            f.write(SPDX_CSV_HEADER)
            df.to_csv(f, index=False)
    logger.info(
        "Wrote calibration to %s, %s, %s",
        grassland_yield_path,
        fodder_conversion_path,
        exogenous_forage_path,
    )


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    compute_grassland_calibration(
        network_path=snakemake.input.network,  # type: ignore[name-defined]
        grassland_yield_path=snakemake.output.grassland_yield_correction,  # type: ignore[name-defined]
        fodder_conversion_path=snakemake.output.fodder_conversion_correction,  # type: ignore[name-defined]
        exogenous_forage_path=snakemake.output.exogenous_forage,  # type: ignore[name-defined]
    )
