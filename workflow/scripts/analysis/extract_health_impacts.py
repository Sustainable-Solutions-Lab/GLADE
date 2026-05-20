# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract health impacts by food group and country.

This script computes:
1. Marginal YLL per unit of food consumed, based on derivatives of the
   piecewise-linear dose-response curves and the chord PWL of exp() that
   together drive the LP's YLL store cost.
2. Total YLL from the optimization result, read from the network's YLL stores.
3. Attribution of total YLL to risk factors via proportional excess log(RR).

All intake values are read from the food-group store levels (post-waste,
intake-basis), matching what Stage 1 of the LP sees in
``solve_model.health._build_store_to_cluster_map`` and
``evaluate_health_posthoc``. Do not substitute food-bus withdrawals here:
those are pre-waste retail flows and are 10-30% higher than the store level
for groups with a non-trivial consumer waste fraction.

Outputs:
- health_marginals.parquet: Marginal YLL at the food_group level (YLL/Mt, USD/t)
- health_totals.parquet: Total YLL by health cluster (MYLL)
- health_attribution.parquet: YLL by (cluster, cause, food_group)
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


def compute_store_intake_by_cluster_risk(
    n: pypsa.Network,
    risk_factors: list[str],
    cluster_lookup: dict[str, int],
    cluster_population: dict[int, float],
) -> dict[tuple[int, str], float]:
    """Compute per-capita intake in g/capita/day from food-group store levels.

    Reads the final-snapshot energy level of the ``store:group:<group>:<country>``
    stores. These hold post-waste (intake-basis) mass, because the
    ``food_consumption`` link applies ``efficiency = (1 - waste_fraction)`` on
    the leg feeding the group bus (see ``build_model.nutrition``). This is the
    exact intake basis used by ``solve_model.health._build_store_to_cluster_map``,
    so reconstructions here stay consistent with what Stage 1 of the LP sees.

    Parameters
    ----------
    n
        Solved PyPSA network.
    risk_factors
        Food groups treated as GBD risk factors.
    cluster_lookup
        Mapping from country ISO3 to health cluster.
    cluster_population
        Population by health cluster (used for the g/capita/day conversion).

    Returns
    -------
    dict
        ``{(cluster, risk_factor): g/capita/day}``. Missing keys imply zero
        intake (no store, or no store for that country mapped to the cluster).
    """
    stores = n.stores.static
    fg_stores = stores[stores["food_group"].isin(risk_factors)].copy()
    if fg_stores.empty:
        return {}

    fg_stores["cluster"] = fg_stores["country"].str.upper().map(cluster_lookup)
    fg_stores = fg_stores[fg_stores["cluster"].notna()].copy()
    fg_stores["cluster"] = fg_stores["cluster"].astype(int)

    snapshot = n.snapshots[-1]
    store_levels = n.stores.dynamic.e.loc[snapshot]

    fg_stores["level_mt"] = fg_stores.index.map(store_levels)
    grouped = fg_stores.groupby(["cluster", "food_group"], as_index=False)[
        "level_mt"
    ].sum()

    intake_totals: dict[tuple[int, str], float] = {}
    for row in grouped.itertuples(index=False):
        cluster = int(row.cluster)
        cluster_pop = cluster_population[cluster]
        if cluster_pop <= 0:
            continue
        intake_g = (
            float(row.level_mt) * GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * cluster_pop)
        )
        intake_totals[(cluster, str(row.food_group))] = intake_g

    return intake_totals


def build_cluster_risk_tables(
    risk_breakpoints: pd.DataFrame,
) -> dict[tuple[int, str], pd.DataFrame]:
    """Pivot risk breakpoints into per-(cluster, risk_factor) lookup tables.

    Risk breakpoints are cluster-specific (age-weighted effective RR curves),
    so the table must be keyed by ``(health_cluster, risk_factor)``. Pooling
    across clusters silently drops curves and misattributes risk.

    Returns
    -------
    dict
        ``{(cluster, risk_factor): pivot}`` where ``pivot`` is indexed by
        ``intake_g_per_day`` with one column per cause holding ``log_rr``.
    """
    tables: dict[tuple[int, str], pd.DataFrame] = {}
    for (cluster, risk), group in risk_breakpoints.groupby(
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
        tables[(int(cluster), str(risk))] = pivot
    return tables


def build_cause_chord_tables(
    cause_log_breakpoints: pd.DataFrame,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Build per-cause (log_pts, rr_pts) arrays for chord-PWL evaluation.

    The LP's Stage 2 (``_add_stage2_lp_tangent``) constrains the YLL store
    with the chord PWL of exp() through these points; since exp is convex
    and the YLL coefficient is non-negative, the store collapses to the
    chord value at the optimum. Post-hoc reconstruction must therefore use
    ``np.interp(log_total, log_pts, rr_pts)`` rather than ``exp(log_total)``.
    """
    tables: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for cause, grp in cause_log_breakpoints.groupby("cause"):
        sorted_grp = grp.sort_values("log_rr_total")
        tables[str(cause)] = (
            sorted_grp["log_rr_total"].to_numpy(dtype=float),
            sorted_grp["rr_total"].to_numpy(dtype=float),
        )
    return tables


def compute_health_marginals(
    n: pypsa.Network,
    health_data: HealthData,
    risk_factors: list[str],
) -> pd.DataFrame:
    """Compute marginal YLL per Mt consumed, by food group and country.

    For each (cluster, cause), the LP's Stage 2 sets the YLL store to the
    chord-PWL value

        e_{c,d}(x) = (chord_PWL(log_total_d(x)) - RR_d^ref)
                     * (YLL_{c,d} / RR_d^base) * 1e-6

    where ``log_total_d(x) = sum_r log RR_{r,d}(x_r)``. The LP-effective
    derivative w.r.t. intake_r is therefore

        d(e)/d(x_r) = chord_slope_at(log_total_d) * d(log RR_{r,d})/d(x_r)
                     * (YLL_{c,d} / RR_d^base) * 1e-6

    where ``chord_slope_at(.)`` is the slope of the chord-PWL piece
    containing ``log_total_d``. Using ``exp(log_total_d)`` here would be the
    analytic derivative of exp(), not what the LP shadow-prices, and the two
    differ by a few percent within a piece.

    Returns DataFrame with columns: country, food_group, yll_per_mt
    """
    cluster_lookup = get_country_cluster_lookup(health_data.country_clusters)
    cluster_population = get_cluster_population(
        health_data.country_clusters, health_data.population
    )
    risk_tables = build_cluster_risk_tables(health_data.risk_breakpoints)
    cause_chord = build_cause_chord_tables(health_data.cause_log_breakpoints)
    cluster_cause = health_data.cluster_cause.assign(
        health_cluster=lambda df: df["health_cluster"].astype(int)
    ).set_index(["health_cluster", "cause"])

    intake_totals = compute_store_intake_by_cluster_risk(
        n, risk_factors, cluster_lookup, cluster_population
    )

    # log(RR) at current intake for every (cluster, risk_factor, cause).
    log_rr_current: dict[tuple[int, str, str], float] = {}
    for cluster in cluster_population:
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

    # log_total per (cluster, cause): sum across all risk factors that map
    # to the cause. The LP's chord-PWL slope is evaluated at this point.
    log_total: dict[tuple[int, str], float] = defaultdict(float)
    for (cluster, _rf, cause), log_rr in log_rr_current.items():
        log_total[(cluster, cause)] += log_rr

    marginal_yll_per_g: dict[tuple[int, str], float] = {}
    for cluster, cluster_pop in cluster_population.items():
        if cluster_pop <= 0:
            continue

        for risk in risk_factors:
            risk_table = risk_tables.get((cluster, risk))
            if risk_table is None:
                continue
            intake_g = intake_totals.get((cluster, risk), 0.0)
            xs = risk_table.index.to_numpy(dtype=float)
            if len(xs) < 2:
                continue

            total_marginal = 0.0
            for cause in risk_table.columns:
                if (cluster, cause) not in cluster_cause.index:
                    continue
                chord = cause_chord.get(str(cause))
                if chord is None:
                    continue

                row = cluster_cause.loc[(cluster, cause)]
                yll_total = (float(row["yll_rate_per_100k"]) / PER_100K) * cluster_pop
                rr_baseline = exp(float(row["log_rr_total_baseline"]))
                if yll_total <= 0 or rr_baseline <= 0:
                    continue

                # d(log RR_r)/d(intake_r) at current intake.
                ys = risk_table[cause].to_numpy(dtype=float)
                d_log_rr = compute_piecewise_slope(xs, ys, intake_g)

                # Chord-PWL slope of exp() at log_total (the LP's effective
                # multiplier; equals exp(log_total) only at breakpoints).
                log_pts, rr_pts = chord
                chord_slope = compute_piecewise_slope(
                    log_pts, rr_pts, log_total[(cluster, cause)]
                )

                marginal_yll = (yll_total / rr_baseline) * chord_slope * d_log_rr
                total_marginal += marginal_yll

            marginal_yll_per_g[(cluster, risk)] = total_marginal

    records = []
    for country, cluster in cluster_lookup.items():
        cluster_pop = cluster_population[cluster]
        if cluster_pop <= 0:
            continue
        # 1 Mt consumed in this country shifts the cluster's per-capita intake
        # by GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * cluster_pop) g/day.
        intake_per_mt = GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * cluster_pop)
        for risk in risk_factors:
            marginal_g = marginal_yll_per_g.get((cluster, risk), 0.0)
            records.append(
                {
                    "country": country,
                    "food_group": risk,
                    "yll_per_mt": marginal_g * intake_per_mt,
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
    n: pypsa.Network,
    health_data: HealthData,
    risk_factors: list[str],
) -> pd.DataFrame:
    """Attribute YLL to each risk factor by health cluster and disease cause.

    Reconstructs the LP's per-(cluster, cause) YLL via the same chain the
    solver uses, then distributes it across risk factors by proportional
    excess log-RR over the joint TMREL:

        share_r = max(0, log RR_{r,d}(x_r) - log RR_{r,d}(TMREL_r)) / sum_r' ...

    To stay consistent with the solver we must:

    1. Read intake from food-group store levels (post-waste), not from
       food-bus withdrawals (pre-waste). These differ by the consumer waste
       multiplier, typically 10-30%.
    2. Use cluster-specific dose-response curves. ``risk_breakpoints``
       carries a ``health_cluster`` column and must be grouped by it.
    3. Evaluate ``RR(log_total)`` via the chord PWL of exp() through
       ``cause_log_breakpoints``, matching ``_add_stage2_lp_tangent``.
       Using ``exp()`` directly creates a small but systematic bias.

    Parameters
    ----------
    n
        Solved PyPSA network (provides food-group store levels and the
        downstream YLL stores).
    health_data
        Health input data including TMREL values.
    risk_factors
        Food groups that are health risk factors.

    Returns
    -------
    DataFrame
        Columns: ``health_cluster``, ``cause``, ``food_group``, ``yll_myll``.
        Rows with zero or negative attributed YLL are omitted.
    """
    cluster_lookup = get_country_cluster_lookup(health_data.country_clusters)
    cluster_population = get_cluster_population(
        health_data.country_clusters, health_data.population
    )
    risk_tables = build_cluster_risk_tables(health_data.risk_breakpoints)
    cause_chord = build_cause_chord_tables(health_data.cause_log_breakpoints)
    cluster_cause = health_data.cluster_cause.assign(
        health_cluster=lambda df: df["health_cluster"].astype(int)
    ).set_index(["health_cluster", "cause"])

    tmrel_lookup = dict(
        zip(health_data.tmrel["risk_factor"], health_data.tmrel["tmrel_g_per_day"])
    )

    intake_totals = compute_store_intake_by_cluster_risk(
        n, risk_factors, cluster_lookup, cluster_population
    )

    # log(RR) at current intake and at TMREL, per (cluster, risk_factor, cause).
    # TMREL is per risk factor but the curve is cluster-specific, so log(RR)
    # at TMREL must also be computed per cluster.
    log_rr_current: dict[tuple[int, str, str], float] = {}
    log_rr_at_tmrel: dict[tuple[int, str, str], float] = {}
    for (cluster, rf), table in risk_tables.items():
        xs = table.index.to_numpy(dtype=float)
        intake_g = intake_totals.get((cluster, rf), 0.0)
        tmrel_intake = float(tmrel_lookup[rf]) if rf in tmrel_lookup else 0.0
        for cause in table.columns:
            ys = table[cause].to_numpy(dtype=float)
            log_rr_current[(cluster, rf, str(cause))] = float(
                np.interp(intake_g, xs, ys)
            )
            log_rr_at_tmrel[(cluster, rf, str(cause))] = float(
                np.interp(tmrel_intake, xs, ys)
            )

    records = []
    for cluster, cluster_pop in cluster_population.items():
        if cluster_pop <= 0:
            continue

        for cause in cluster_cause.index.get_level_values("cause").unique():
            if (cluster, cause) not in cluster_cause.index:
                continue
            chord = cause_chord.get(str(cause))
            if chord is None:
                continue

            row = cluster_cause.loc[(cluster, cause)]
            rr_ref = exp(float(row["log_rr_total_ref"]))
            rr_baseline = exp(float(row["log_rr_total_baseline"]))
            yll_total = (float(row["yll_rate_per_100k"]) / PER_100K) * cluster_pop
            if yll_total <= 0 or rr_baseline <= 0:
                continue

            # Excess log(RR) above TMREL per risk factor (clamped at zero so
            # protective intakes above TMREL do not get "negative" attribution).
            excess: dict[str, float] = {}
            for rf in risk_factors:
                key = (cluster, rf, str(cause))
                if key not in log_rr_current:
                    continue
                excess[rf] = max(0.0, log_rr_current[key] - log_rr_at_tmrel[key])
            total_excess = sum(excess.values())
            if total_excess <= 0:
                continue

            # Total log(RR) for the cause and chord-PWL evaluation. This
            # mirrors evaluate_health_posthoc and the LP's Stage 2 exactly.
            log_total = sum(
                log_rr_current.get((cluster, rf, str(cause)), 0.0)
                for rf in risk_factors
            )
            log_pts, rr_pts = chord
            rr_total = float(np.interp(log_total, log_pts, rr_pts))

            yll_myll = (rr_total - rr_ref) * (yll_total / rr_baseline) * 1e-6
            if yll_myll <= 0:
                continue

            for rf, e in excess.items():
                if e <= 0:
                    continue
                records.append(
                    {
                        "health_cluster": cluster,
                        "cause": cause,
                        "food_group": rf,
                        "yll_myll": (e / total_excess) * yll_myll,
                    }
                )

    return pd.DataFrame(records)
