# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract health impacts by food group and country.

This script computes:
1. Marginal YLL per unit of food consumed, based on derivatives of
   piecewise-linear dose-response curves at current population intake levels.
2. Total YLL from the optimization result, read from the network's YLL stores.

Uses food_group_consumption.parquet from extract_statistics for consumption amounts,
avoiding duplicate extraction of consumption data from the network.

Outputs:
- health_marginals.parquet: Marginal YLL at the food_group level (YLL/Mt, USD/t)
- health_totals.parquet: Total YLL by health cluster (MYLL)
"""

from collections import defaultdict
from dataclasses import dataclass
import logging
from math import exp

import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE, PER_100K

logger = logging.getLogger(__name__)


@dataclass
class HealthData:
    """Container for health-related input data."""

    risk_breakpoints: pd.DataFrame
    cluster_cause: pd.DataFrame
    cause_log_breakpoints: pd.DataFrame
    country_clusters: pd.DataFrame
    population: pd.DataFrame
    tmrel: pd.DataFrame


def load_health_data(inputs: dict) -> HealthData:
    """Load health data files from snakemake inputs."""
    return HealthData(
        risk_breakpoints=pd.read_csv(inputs["risk_breakpoints"]),
        cluster_cause=pd.read_csv(inputs["health_cluster_cause"]),
        cause_log_breakpoints=pd.read_csv(inputs["health_cause_log"]),
        country_clusters=pd.read_csv(inputs["health_clusters"]),
        population=pd.read_csv(inputs["population"]),
        tmrel=pd.read_csv(inputs["derived_tmrel"]),
    )


def get_cluster_population(
    country_clusters: pd.DataFrame,
    population: pd.DataFrame,
) -> dict[int, float]:
    """Compute total population per health cluster."""
    clusters = country_clusters.assign(
        country_iso3=lambda df: df["country_iso3"].str.upper()
    )
    cluster_lookup = (
        clusters.set_index("country_iso3")["health_cluster"].astype(int).to_dict()
    )

    pop = population.assign(iso3=lambda df: df["iso3"].str.upper())
    pop_map = pop.set_index("iso3")["population"].astype(float).to_dict()

    result: dict[int, float] = defaultdict(float)
    for iso3, cluster in cluster_lookup.items():
        result[int(cluster)] += pop_map.get(iso3, 0.0)

    return dict(result)


def get_country_cluster_lookup(country_clusters: pd.DataFrame) -> dict[str, int]:
    """Map country ISO3 codes to health cluster IDs."""
    clusters = country_clusters.assign(
        country_iso3=lambda df: df["country_iso3"].str.upper()
    )
    return clusters.set_index("country_iso3")["health_cluster"].astype(int).to_dict()


def compute_intake_by_cluster_risk(
    food_group_consumption: pd.DataFrame,
    risk_factors: list[str],
    cluster_lookup: dict[str, int],
    cluster_population: dict[int, float],
) -> dict[tuple[int, str], float]:
    """Compute current intake in g/capita/day by (cluster, risk_factor).

    Parameters
    ----------
    food_group_consumption : DataFrame with columns food_group, country, consumption_mt
    risk_factors : List of food groups that are health risk factors
    cluster_lookup : Dict mapping country ISO3 to cluster ID
    cluster_population : Dict mapping cluster ID to total population

    Returns dict mapping (cluster, risk_factor) to intake in g/capita/day
    """
    # Filter to risk factors only
    df = food_group_consumption[
        food_group_consumption["food_group"].isin(risk_factors)
    ].copy()

    if df.empty:
        return {}

    # Normalize country codes
    df["country"] = df["country"].str.upper()

    # Map countries to clusters
    df["cluster"] = df["country"].map(cluster_lookup)

    # Filter to countries with known clusters
    df = df[df["cluster"].notna()].copy()
    df["cluster"] = df["cluster"].astype(int)

    # Aggregate consumption by (cluster, food_group)
    cluster_consumption = (
        df.groupby(["cluster", "food_group"])["consumption_mt"].sum().reset_index()
    )

    # Convert to g/capita/day
    intake_totals: dict[tuple[int, str], float] = {}
    for _, row in cluster_consumption.iterrows():
        cluster = int(row["cluster"])
        food_group = str(row["food_group"])
        consumption_mt = float(row["consumption_mt"])

        cluster_pop = cluster_population.get(cluster, 0.0)
        if cluster_pop <= 0:
            continue

        # Convert Mt/year to g/capita/day
        intake_g = consumption_mt * GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * cluster_pop)
        intake_totals[(cluster, food_group)] = intake_g

    return intake_totals


def compute_health_marginals(
    food_group_consumption: pd.DataFrame,
    health_data: HealthData,
    risk_factors: list[str],
) -> pd.DataFrame:
    """Compute marginal YLL per Mt consumed, by food group and country.

    For each (cluster, cause), the solver's YLL store level is:

        e_{c,d}(x) = (RR_d(x) - RR_d^ref) * (YLL_{c,d} / RR_d^base) * 1e-6

    where RR_d(x) = prod_r RR_{r,d}(x_r). Taking the derivative w.r.t.
    intake of risk factor r:

        d(e)/d(x_r) = (YLL_{c,d} / RR_d^base) * RR_d(x) * d(log RR_{r,d})/d(x_r) * 1e-6

    This function evaluates that derivative at current intake levels,
    sums across causes, and converts to YLL per Mt.

    Returns DataFrame with columns: country, food_group, yll_per_mt
    """
    # Build lookups
    cluster_lookup = get_country_cluster_lookup(health_data.country_clusters)
    cluster_population = get_cluster_population(
        health_data.country_clusters, health_data.population
    )

    # Build risk tables: (cluster, risk_factor) -> DataFrame(intake, cause -> log_rr)
    # Risk breakpoints are cluster-specific due to age-weighted effective RR
    risk_tables: dict[tuple[int, str], pd.DataFrame] = {}
    for (cluster, risk), group in health_data.risk_breakpoints.groupby(
        ["health_cluster", "risk_factor"]
    ):
        pivot = (
            group.sort_values(["intake_g_per_day", "cause"])
            .pivot_table(
                index="intake_g_per_day",
                columns="cause",
                values="log_rr",
                aggfunc="first",
            )
            .sort_index()
        )
        risk_tables[(int(cluster), str(risk))] = pivot

    # Cluster-cause baseline data
    cluster_cause = health_data.cluster_cause.assign(
        health_cluster=lambda df: df["health_cluster"].astype(int)
    ).set_index(["health_cluster", "cause"])

    # Compute current intake per (cluster, risk_factor) from consumption data
    intake_totals = compute_intake_by_cluster_risk(
        food_group_consumption, risk_factors, cluster_lookup, cluster_population
    )

    # Precompute current log(RR) for every (cluster, risk_factor, cause)
    # so we can sum across risk factors to get the total RR per (cluster, cause)
    log_rr_current: dict[tuple[int, str, str], float] = {}
    all_clusters = set(cluster_population.keys())

    for cluster in all_clusters:
        for rf in risk_factors:
            table = risk_tables.get((cluster, rf))
            if table is None:
                continue
            intake_g = intake_totals.get((cluster, rf), 0.0)
            xs = table.index.to_numpy(dtype=float)
            for cause in table.columns:
                ys = table[cause].to_numpy(dtype=float)
                log_rr_current[(cluster, rf, cause)] = float(
                    np.interp(intake_g, xs, ys)
                )

    # Compute marginal YLL per g/day for each (cluster, risk_factor)
    marginal_yll_per_g: dict[tuple[int, str], float] = {}

    for cluster in all_clusters:
        cluster_pop = cluster_population.get(cluster, 0.0)
        if cluster_pop <= 0:
            continue

        for risk in risk_factors:
            risk_table = risk_tables.get((cluster, risk))
            if risk_table is None:
                continue
            intake_g = intake_totals.get((cluster, risk), 0.0)

            total_marginal = 0.0

            for cause in risk_table.columns:
                if (cluster, cause) not in cluster_cause.index:
                    continue

                row = cluster_cause.loc[(cluster, cause)]

                # Use total YLL rate (not attributable), matching solver
                yll_rate = float(row["yll_rate_per_100k"])
                yll_total = (yll_rate / PER_100K) * cluster_pop

                # Divide by RR at baseline (not at TMREL), matching solver
                rr_baseline = exp(float(row["log_rr_total_baseline"]))

                if yll_total <= 0 or rr_baseline <= 0:
                    continue

                # Get breakpoints for this (cluster, risk, cause)
                xs = risk_table.index.to_numpy(dtype=float)
                ys = risk_table[cause].to_numpy(dtype=float)

                if len(xs) < 2:
                    continue

                # Slope of log(RR_r) w.r.t. intake for this risk factor
                d_log_rr = compute_piecewise_slope(xs, ys, intake_g)

                # Total RR across ALL risk factors for this cause
                log_rr_total = sum(
                    log_rr_current.get((cluster, rf, cause), 0.0) for rf in risk_factors
                )
                rr_total = exp(log_rr_total)

                # Chain rule:
                # d(YLL)/d(intake_r) = (YLL_total / RR_base) * RR_total * d(log RR_r)/d(intake_r)
                marginal_yll = (yll_total / rr_baseline) * rr_total * d_log_rr

                total_marginal += marginal_yll

            marginal_yll_per_g[(cluster, risk)] = total_marginal

    # Convert to per-country, per-food_group output
    records = []

    for country, cluster in cluster_lookup.items():
        cluster_pop = cluster_population[cluster]
        if cluster_pop <= 0:
            continue

        for risk in risk_factors:
            marginal_g = marginal_yll_per_g.get((cluster, risk), 0.0)

            # Convert from YLL per (g/capita/day) to YLL per Mt
            # Consumption of 1 Mt affects cluster intake by 1e12 / (365 * cluster_pop)
            intake_per_mt = GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * cluster_pop)
            yll_per_mt = marginal_g * intake_per_mt

            records.append(
                {
                    "country": country,
                    "food_group": risk,
                    "yll_per_mt": yll_per_mt,
                }
            )

    return pd.DataFrame(records)


def compute_piecewise_slope(
    x_breakpoints: np.ndarray,
    y_breakpoints: np.ndarray,
    x_current: float,
) -> float:
    """Compute the slope of a piecewise linear function at a given point.

    Returns the derivative (slope) of the line segment containing x_current.
    """
    if len(x_breakpoints) < 2:
        return 0.0

    # Find the segment containing x_current
    # np.searchsorted returns the index where x_current would be inserted
    idx = np.searchsorted(x_breakpoints, x_current)

    # Clamp to valid segment range
    if idx == 0:
        idx = 1
    elif idx >= len(x_breakpoints):
        idx = len(x_breakpoints) - 1

    # Segment is [idx-1, idx]
    x0, x1 = x_breakpoints[idx - 1], x_breakpoints[idx]
    y0, y1 = y_breakpoints[idx - 1], y_breakpoints[idx]

    dx = x1 - x0
    if abs(dx) < 1e-12:
        return 0.0

    return (y1 - y0) / dx


def add_monetary_value(df: pd.DataFrame, value_per_yll: float) -> pd.DataFrame:
    """Add USD per tonne column for health damages.

    Parameters
    ----------
    df : DataFrame with yll_per_mt column
    value_per_yll : USD per YLL

    Returns DataFrame with additional health_usd_per_t column
    """
    df = df.copy()
    if df.empty:
        df["health_usd_per_t"] = pd.Series(dtype=float)
    else:
        # YLL/Mt to YLL/t: divide by 1e6
        # Then multiply by value_per_yll
        df["health_usd_per_t"] = (df["yll_per_mt"] / 1e6) * value_per_yll
    return df


def extract_yll_totals(n: pypsa.Network) -> pd.DataFrame:
    """Extract total YLL by health cluster from network stores.

    YLL stores have carriers like 'yll_CHD', 'yll_Stroke', etc. and a
    'health_cluster' metadata column. The store's energy level (e) at the
    final snapshot gives total YLL for that (cluster, cause) pair.

    Parameters
    ----------
    n : pypsa.Network
        Solved network with YLL stores.

    Returns
    -------
    DataFrame with columns: health_cluster, yll_myll
        Total YLL in millions (MYLL) by health cluster.
    """
    stores = n.stores.static
    yll_mask = stores["carrier"].str.startswith("yll_")

    if not yll_mask.any():
        logger.warning("No YLL stores found in network")
        return pd.DataFrame(columns=["health_cluster", "yll_myll"])

    yll_stores = stores[yll_mask].copy()

    if "health_cluster" not in yll_stores.columns:
        logger.warning("YLL stores missing 'health_cluster' column")
        return pd.DataFrame(columns=["health_cluster", "yll_myll"])

    # Get energy level at final snapshot
    snapshot = n.snapshots[-1]
    e = n.stores.dynamic.e.loc[snapshot]

    # Collect YLL per store, then aggregate by cluster
    yll_stores["yll"] = yll_stores.index.map(lambda s: e.get(s, 0.0))

    result = (
        yll_stores.groupby("health_cluster")["yll"]
        .sum()
        .reset_index()
        .rename(columns={"yll": "yll_myll"})
    )
    # Convert from model units (million YLL) to MYLL (already in MYLL)
    # Note: model stores are in million YLL, so no conversion needed

    return result.sort_values("health_cluster").reset_index(drop=True)


def compute_health_attribution(
    food_group_consumption: pd.DataFrame,
    health_data: HealthData,
    risk_factors: list[str],
    n: pypsa.Network,
) -> pd.DataFrame:
    """Attribute YLL to each risk factor by health cluster and disease cause.

    Uses proportional allocation based on excess log-relative-risk above the
    theoretical minimum risk exposure level (TMREL).

    Parameters
    ----------
    food_group_consumption : DataFrame
        Columns: food_group, country, consumption_mt
    health_data : HealthData
        Health input data including TMREL values.
    risk_factors : list[str]
        Food groups that are health risk factors.
    n : pypsa.Network
        Solved network (used to read YLL store levels).

    Returns
    -------
    DataFrame with columns: health_cluster, cause, food_group, yll_myll
    """
    cluster_lookup = get_country_cluster_lookup(health_data.country_clusters)
    cluster_population = get_cluster_population(
        health_data.country_clusters, health_data.population
    )

    # Build risk tables: risk_factor -> DataFrame(intake -> cause columns of log_rr)
    risk_tables: dict[str, pd.DataFrame] = {}
    for risk, group in health_data.risk_breakpoints.groupby("risk_factor"):
        pivot = (
            group.sort_values(["intake_g_per_day", "cause"])
            .pivot_table(
                index="intake_g_per_day",
                columns="cause",
                values="log_rr",
                aggfunc="first",
            )
            .sort_index()
        )
        risk_tables[str(risk)] = pivot

    # Build TMREL lookup: risk_factor -> g/day
    tmrel_lookup = dict(
        zip(health_data.tmrel["risk_factor"], health_data.tmrel["tmrel_g_per_day"])
    )

    # Precompute log(RR) at TMREL for each (risk_factor, cause)
    log_rr_at_tmrel: dict[tuple[str, str], float] = {}
    for rf, table in risk_tables.items():
        tmrel_intake = tmrel_lookup.get(rf, 0.0)
        xs = table.index.to_numpy(dtype=float)
        for cause in table.columns:
            ys = table[cause].to_numpy(dtype=float)
            log_rr_at_tmrel[(rf, cause)] = float(np.interp(tmrel_intake, xs, ys))

    # Cluster-cause baseline data
    cluster_cause = health_data.cluster_cause.assign(
        health_cluster=lambda df: df["health_cluster"].astype(int)
    ).set_index(["health_cluster", "cause"])

    # Compute current intake per (cluster, risk_factor)
    intake_totals = compute_intake_by_cluster_risk(
        food_group_consumption, risk_factors, cluster_lookup, cluster_population
    )

    # Compute current log(RR) per (cluster, risk_factor, cause)
    log_rr_values: dict[tuple[int, str, str], float] = {}
    for (cluster, rf), intake_g in intake_totals.items():
        table = risk_tables.get(rf)
        if table is None:
            continue
        xs = table.index.to_numpy(dtype=float)
        for cause in table.columns:
            ys = table[cause].to_numpy(dtype=float)
            log_rr_values[(cluster, rf, cause)] = float(np.interp(intake_g, xs, ys))

    # Attribute YLL to risk factors using proportional excess log(RR)
    records = []

    for cluster in cluster_population:
        cluster_pop = cluster_population[cluster]
        if cluster_pop <= 0:
            continue

        for cause in cluster_cause.index.get_level_values("cause").unique():
            if (cluster, cause) not in cluster_cause.index:
                continue

            row = cluster_cause.loc[(cluster, cause)]
            rr_ref = exp(float(row["log_rr_total_ref"]))
            rr_baseline = exp(float(row["log_rr_total_baseline"]))
            yll_rate_per_100k = float(row["yll_rate_per_100k"])
            yll_total = (yll_rate_per_100k / PER_100K) * cluster_pop

            if yll_total <= 0 or rr_baseline <= 0:
                continue

            # Compute excess log(RR) for each risk factor relative to TMREL
            excess_contributions: dict[str, float] = {}
            for rf in risk_factors:
                log_rr_current = log_rr_values.get((cluster, rf, cause), 0.0)
                log_rr_tmrel = log_rr_at_tmrel.get((rf, cause), 0.0)
                excess = max(0.0, log_rr_current - log_rr_tmrel)
                excess_contributions[rf] = excess

            total_excess = sum(excess_contributions.values())
            if total_excess <= 0:
                continue

            # Compute actual RR and YLL for this (cluster, cause)
            log_rr_total = sum(
                log_rr_values.get((cluster, rf, cause), 0.0) for rf in risk_factors
            )
            rr_total = exp(log_rr_total)
            yll_myll = (rr_total - rr_ref) * (yll_total / rr_baseline) * 1e-6

            if yll_myll <= 0:
                continue

            # Proportionally allocate to risk factors
            for rf, excess in excess_contributions.items():
                if excess > 0:
                    weight = excess / total_excess
                    records.append(
                        {
                            "health_cluster": cluster,
                            "cause": cause,
                            "food_group": rf,
                            "yll_myll": weight * yll_myll,
                        }
                    )

    return pd.DataFrame(records)
