# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract consumption-attributed GHG intensity and totals by food and country.

This script computes consumption-attributed GHG emissions by tracing emissions
through trade and processing networks back to production using flow-based
attribution via sparse matrix algebra.

Uses food_consumption.parquet from extract_statistics for consumption amounts,
avoiding duplicate extraction of consumption data from the network.

Outputs:
- ghg_attribution.parquet: Intensity at the food level (kgCO2e/kg, USD/t)
- ghg_attribution_totals.parquet: Total emissions by country and food_group (MtCO2eq)

Sequestration scope (important)
-------------------------------
These outputs report **gross** consumption-attributed emissions. The
model's spare-land sequestration credit (negative CO2 from regrowth on
land freed by reduced cropland/pasture demand) consumes from a land
bus and writes to a dead-end "spared" sink that no food consumes from,
so the flow-based attribution naturally orphans the credit and it does
NOT propagate into per-food intensities or per-(country, food_group)
totals.

Sequestration is a system-level benefit of reduced land use, not a
property of any individual food, so any per-food allocation would be
arbitrary. To reconcile with the model objective, read the
"Carbon sequestration" row from ``net_emissions.parquet`` and combine
it with these gross totals at the analysis level.

The downstream consequence is that ``ghg_attribution_totals.sum()``
will generally NOT equal ``net_emissions.sum()``: the difference is
the (negative) carbon-sequestration credit reported separately by
``extract_net_emissions``.
"""

import logging
import re

import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.constants import TONNE_TO_MEGATONNE

_BUS_COL_PATTERN = re.compile(r"^bus(\d+)$")


def _emission_bus_columns(links: pd.DataFrame) -> list[tuple[str, str]]:
    """List (bus_col, eff_col) pairs for every secondary port on a link table.

    Secondary buses are bus2, bus3, ..., busN — i.e. every ``bus{N}`` column
    with N >= 2 that exists on the DataFrame. The matching efficiency column
    is ``efficiency{N}``. The primary port (bus0/bus1, efficiency) is excluded:
    emissions are always wired as secondary outputs in this model.

    Returned pairs are ordered by N so iteration is deterministic.
    """
    pairs: list[tuple[int, str, str]] = []
    for col in links.columns:
        match = _BUS_COL_PATTERN.match(col)
        if match is None:
            continue
        n = int(match.group(1))
        if n < 2:
            continue
        eff_col = f"efficiency{n}"
        if eff_col in links.columns:
            pairs.append((n, col, eff_col))
    pairs.sort()
    return [(bus_col, eff_col) for _, bus_col, eff_col in pairs]


logger = logging.getLogger(__name__)


def compute_bus_intensities(
    n: pypsa.Network,
    ch4_gwp: float,
    n2o_gwp: float,
) -> dict[str, float]:
    """Compute GHG emission intensity at each bus via flow-based attribution.

    Uses sparse matrix solve: (I - M) * rho = e where:
    - rho[b] = emission intensity at bus b (MtCO2e per Mt flow)
    - M = weighted adjacency matrix (flow fractions)
    - e = direct emission intensities

    Sequestration intensities are computed at the spared-land sink bus
    (where the spare_land link writes its negative efficiency2 to
    emission:co2), but those sink buses are terminal in the network
    topology, so the negative intensity does NOT propagate downstream to
    food buses. Per-food intensities returned here are therefore gross;
    see the module docstring for how to obtain net (including
    sequestration) values.

    Returns dict mapping bus name to intensity (MtCO2e/Mt = kgCO2e/kg).
    """
    # Get snapshot and flows
    snapshot = n.snapshots[-1]
    p0 = (
        n.links.dynamic.p0.loc[snapshot]
        if snapshot in n.links.dynamic.p0.index
        else pd.Series(dtype=float)
    )

    # Build links DataFrame with flows and emissions
    links_df = build_ghg_links_dataframe(n, p0, ch4_gwp, n2o_gwp)

    if links_df.empty:
        logger.warning("No links with positive flow found")
        return {}

    # Compute emission intensities at each bus via sparse matrix solve
    return solve_emission_intensities(links_df)


def build_ghg_links_dataframe(
    n: pypsa.Network,
    p0: pd.Series,
    ch4_gwp: float,
    n2o_gwp: float,
) -> pd.DataFrame:
    """Build DataFrame of links with flows and GHG emissions.

    Returns DataFrame with columns:
    - link_name, bus0, bus1, flow, efficiency, emissions_co2e
    """
    # GWP factors (CH4/N2O are in tonnes, convert to MtCO2e)
    # Bus names include "emission:" prefix (e.g., "emission:co2")
    gwp = {
        "emission:co2": 1.0,
        "emission:ch4": ch4_gwp * TONNE_TO_MEGATONNE,
        "emission:n2o": n2o_gwp * TONNE_TO_MEGATONNE,
    }

    links = n.links.static.copy()
    links["link_name"] = links.index
    links["flow"] = p0.reindex(links.index).fillna(0.0)

    # Filter to positive flows only
    links = links[links["flow"] > 1e-12].copy()

    if links.empty:
        return pd.DataFrame()

    # Ensure efficiency is filled
    links["efficiency"] = links["efficiency"].fillna(1.0)

    # Compute emissions per unit of input flow by summing every secondary
    # bus (bus2..busN) that targets an emission carrier. Iterating all
    # secondary ports is essential because crop_production carries residue
    # soil N2O on bus6 and crop_production_multi places residue N2O on a
    # dynamically-numbered high bus; a hard-coded bus2/3/4 sweep would
    # silently drop those contributions.
    # Positive efficiency to an emission bus = emissions (e.g. CH4 from
    # enteric fermentation); negative efficiency = sequestration (e.g. CO2
    # credits from spared land). Sequestration credits land at the
    # spared-land sink bus and do not propagate to food buses (see module
    # docstring).
    links["emissions_co2e"] = 0.0

    for bus_col, eff_col in _emission_bus_columns(links):
        emission_bus = links[bus_col].fillna("")
        eff = links[eff_col].fillna(0.0)

        for gas, gwp_factor in gwp.items():
            mask = emission_bus == gas
            links.loc[mask, "emissions_co2e"] += eff[mask] * gwp_factor

    # Exclude links with zero primary efficiency: they consume from bus0 but
    # deliver nothing to bus1, so they cannot participate in the flow-weighted
    # intensity calculation (and would cause division by zero).
    links = links[links["efficiency"].abs() > 1e-12]

    result = links[
        ["link_name", "bus0", "bus1", "flow", "efficiency", "emissions_co2e"]
    ]

    # Include generator dispatches as virtual zero-emission links so that
    # generator inflows (e.g. slack generators on food buses) correctly dilute
    # the emission intensity at the receiving bus.
    gen_virtual = _generator_virtual_links(n, n.snapshots[-1])
    if not gen_virtual.empty:
        result = pd.concat([result, gen_virtual], ignore_index=True)

    return result


def _generator_virtual_links(n: pypsa.Network, snapshot) -> pd.DataFrame:
    """Create virtual zero-emission link rows for generators with positive dispatch.

    Generators inject flow into buses but are invisible to the link-based
    attribution system. Representing them as virtual links from a zero-intensity
    source bus ensures their contribution dilutes bus-level intensities correctly.
    """
    cols = ["link_name", "bus0", "bus1", "flow", "efficiency", "emissions_co2e"]
    gen_p = n.generators.dynamic.p
    if gen_p.empty:
        return pd.DataFrame(columns=cols)

    dispatch = (
        gen_p.loc[snapshot] if snapshot in gen_p.index else pd.Series(dtype=float)
    )
    pos_dispatch = dispatch[dispatch > 1e-12]
    if pos_dispatch.empty:
        return pd.DataFrame(columns=cols)

    gen_static = n.generators.static
    buses = gen_static.loc[pos_dispatch.index, "bus"]

    return pd.DataFrame(
        {
            "link_name": "__gen__" + pos_dispatch.index,
            "bus0": "__zero_emission_source__",
            "bus1": buses.values,
            "flow": pos_dispatch.values,
            "efficiency": 1.0,
            "emissions_co2e": 0.0,
        }
    )


def solve_emission_intensities(links_df: pd.DataFrame) -> dict[str, float]:
    """Solve for emission intensity at each bus using sparse matrix.

    The intensity rho[b] at bus b satisfies:
        rho[b] = e[b] + sum over incoming links l: w[l] * rho[bus0[l]]

    Where:
        w[l] = flow[l] / total_outflow[bus1[l]]  (weight of link)
        e[b] = sum(flow[l] * emissions[l]) / total_outflow[b]  (direct emissions)

    This gives: (I - M) * rho = e, solved via sparse linear algebra.
    """
    import warnings

    from scipy import sparse
    from scipy.sparse.linalg import MatrixRankWarning, spsolve

    # Get unique buses and create integer indices
    all_buses = pd.concat([links_df["bus0"], links_df["bus1"]]).unique()
    bus_to_idx = {bus: i for i, bus in enumerate(all_buses)}
    n_buses = len(all_buses)

    # Map bus names to indices (vectorized)
    links_df = links_df.copy()
    links_df["idx0"] = links_df["bus0"].map(bus_to_idx)
    links_df["idx1"] = links_df["bus1"].map(bus_to_idx)

    # Compute total outflow at each destination bus: sum(flow * efficiency)
    links_df["outflow"] = links_df["flow"] * links_df["efficiency"]
    total_outflow = links_df.groupby("idx1")["outflow"].transform("sum")

    # Compute weights: flow / total_outflow[bus1]
    links_df["weight"] = links_df["flow"] / total_outflow

    # Compute direct emission contribution: flow * emissions / total_outflow
    links_df["emission_contrib"] = (
        links_df["flow"] * links_df["emissions_co2e"] / total_outflow
    )

    # Build sparse matrix M where M[i, j] = sum of weights for links from j to i
    # Using COO format for efficient construction
    row = links_df["idx1"].values
    col = links_df["idx0"].values
    data = links_df["weight"].values

    adj_matrix = sparse.coo_matrix((data, (row, col)), shape=(n_buses, n_buses))
    adj_matrix = adj_matrix.tocsr()  # Convert to CSR for efficient arithmetic

    # Build emission vector e[i] = sum of emission contributions to bus i
    e = np.zeros(n_buses)
    np.add.at(e, links_df["idx1"].values, links_df["emission_contrib"].values)

    # Solve (I - M) * rho = e
    identity = sparse.eye(n_buses, format="csr")
    system_matrix = identity - adj_matrix

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MatrixRankWarning)
        rho = spsolve(system_matrix, e)

    # Singular components (closed cycles with no emissions) produce NaN;
    # their true intensity is zero since no emissions enter the cycle.
    nan_count = np.count_nonzero(~np.isfinite(rho))
    if nan_count:
        logger.debug(
            "GHG attribution: %d/%d buses in emission-free cycles (set to 0)",
            nan_count,
            n_buses,
        )
        rho = np.where(np.isfinite(rho), rho, 0.0)

    # Map back to bus names
    idx_to_bus = {i: bus for bus, i in bus_to_idx.items()}
    return {idx_to_bus[i]: float(rho[i]) for i in range(n_buses)}


def join_intensities_to_consumption(
    food_consumption: pd.DataFrame,
    food_groups: pd.DataFrame,
    bus_intensities: dict[str, float],
) -> pd.DataFrame:
    """Join bus intensities to food consumption data.

    Parameters
    ----------
    food_consumption : DataFrame with columns food, country, consumption_mt
    food_groups : DataFrame with columns food, group
    bus_intensities : dict mapping bus name to GHG intensity

    Returns DataFrame with columns: country, food, food_group, consumption_mt,
    ghg_kgco2e_per_kg
    """
    # Build food -> food_group mapping
    food_to_group = food_groups.set_index("food")["group"].to_dict()

    # Select relevant columns and add food_group
    df = food_consumption[["food", "country", "consumption_mt"]].copy()
    df["food_group"] = df["food"].map(food_to_group)

    # Filter to foods with known food_group
    df = df[df["food_group"].notna()].copy()

    # Construct food bus name and look up intensity
    # Food buses are named: food:{food}:{country}
    df["food_bus"] = "food:" + df["food"] + ":" + df["country"]
    df["ghg_kgco2e_per_kg"] = df["food_bus"].map(bus_intensities).fillna(0.0)

    # Select output columns
    result = df[
        ["country", "food", "food_group", "consumption_mt", "ghg_kgco2e_per_kg"]
    ].copy()

    return result


def add_monetary_value(df: pd.DataFrame, ghg_price: float) -> pd.DataFrame:
    """Add USD per tonne column for GHG damages.

    Parameters
    ----------
    df : DataFrame with ghg_kgco2e_per_kg column
    ghg_price : USD per tonne CO2e

    Returns DataFrame with additional ghg_usd_per_t column
    """
    df = df.copy()
    if df.empty:
        df["ghg_usd_per_t"] = pd.Series(dtype=float)
    else:
        # kgCO2e/kg = tCO2e/t (same ratio), then multiply by price
        df["ghg_usd_per_t"] = df["ghg_kgco2e_per_kg"] * ghg_price
    return df


def compute_ghg_totals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute total GHG emissions by country and food_group.

    Parameters
    ----------
    df : DataFrame with columns consumption_mt, ghg_kgco2e_per_kg, country, food_group

    Returns
    -------
    DataFrame with columns: country, food_group, ghg_mtco2eq
    """
    if df.empty:
        return pd.DataFrame(columns=["country", "food_group", "ghg_mtco2eq"])

    # Total emissions = consumption_mt * kgCO2e/kg
    # Since kgCO2e/kg = MtCO2e/Mt, this gives MtCO2e directly
    df = df.copy()
    df["ghg_mtco2eq"] = df["consumption_mt"] * df["ghg_kgco2e_per_kg"]

    # Aggregate by country and food_group
    totals = (
        df.groupby(["country", "food_group"], as_index=False)["ghg_mtco2eq"]
        .sum()
        .sort_values(["country", "food_group"])
        .reset_index(drop=True)
    )

    return totals
