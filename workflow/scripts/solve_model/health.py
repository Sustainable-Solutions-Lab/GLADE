# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Health objective constraints for the food systems optimization model.

This module implements health cost constraints as described in docs/health.rst.
The health objective quantifies the cost of dietary choices in terms of years
of life lost (YLL), using epidemiological dose-response relationships from
the Global Burden of Disease (GBD) Study.

Mathematical Formulation
------------------------

The health cost for cluster c and disease d is (see docs/health.rst):

    Cost_{c,d}(x) = V * (YLL_{c,d} / RR_d(x^base)) * (RR_d(x) - RR_d^ref)

where:
    - V = value per year of life lost (USD/YLL)
    - YLL_{c,d} = baseline years of life lost
    - RR_d(x) = relative risk at intake x (product over risk factors r)
    - RR_d^ref = RR at TMREL (theoretical minimum risk exposure level)
    - x^base = baseline intake

The combined relative risk is multiplicative across risk factors:

    RR_d(x) = ∏_r RR_{r,d}(x_r)

Implementation Strategy
-----------------------

To handle the nonlinear multiplicative combination, we use a two-stage
piecewise-linear approximation:

    Stage 1: Intake x_r → log(RR_{r,d}) for each (cluster, risk) pair
    Stage 2: Σ_r log(RR_{r,d}) → exp(·) → RR_d → YLL store level

**Stage 1** uses delta (incremental) variables with SOS1 segment indicators
because the dose-response curves may be non-convex:

    δ_j ∈ [0,1], δ_j ≤ δ_{j-1} (fill-up ordering)
    x = x_0 + Σ_j δ_j Δx_j
    f(x) = f_0 + Σ_j δ_j Δf_j

Linopy's ``reformulate_sos='auto'`` converts the SOS1 constraints to binary
+ Big-M for solvers that don't support SOS natively (e.g. HiGHS).

**Stage 2** uses an LP-tangent (chord-only) formulation: no auxiliary
variables at all. Because ``exp()`` is convex and the YLL cost minimises
RR, the constraint

    store_var >= scale * (slope_j * log_total + intercept_j - rr_ref)

added for each piece j of the chord PWL approximation of ``exp(log_total)``
collapses at the optimum to the exact chord-PWL value of ``exp(log_total)``.
A domain bound ``log_total ∈ [log_pts[0], log_pts[-1]]`` is added explicitly
(it was implicit in the δ ∈ [0,1] bound of the previous formulation).

Code Organization
-----------------
- Data loading: _load_health_data
- Stage 1 (Intake → log(RR)):
    - _build_store_to_cluster_map: Map stores to clusters with per-capita coefficients
    - _build_intake_breakpoints: Build breakpoint grids per risk factor
    - _group_cluster_risk_pairs: Group pairs by risk_factor for efficient batching
    - _add_stage1_constraints: Main Stage 1 logic
    - _add_stage1_delta: δ variables + segment indicators
- Stage 2 (log(RR) → YLL):
    - _build_cause_breakpoints: Build log-RR breakpoints per cause
    - _group_cluster_cause_pairs: Group pairs by shared log-RR grids
    - _add_stage2_constraints: Main Stage 2 logic (LP-tangent chords + domain bounds)
- Main entry point: add_health_objective
"""

from collections import defaultdict
import itertools
import logging
import math

import linopy
import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from .. import constants
from ..population import get_health_cluster_population

logger = logging.getLogger(__name__)


# =============================================================================
# Module State for Auxiliary Variable Tracking
# =============================================================================

# Auxiliary variables (SOS2 segment binaries) must be removed before
# PyPSA solution assignment to avoid polluting the solved network.
HEALTH_AUX_MAP: dict[int, set[str]] = {}

# Counters for unique variable naming
_LAMBDA_GROUP_COUNTER = itertools.count()


def _register_auxiliary_variable(m: linopy.Model, name: str) -> None:
    """Track an auxiliary variable for post-solve cleanup."""
    aux = HEALTH_AUX_MAP.setdefault(id(m), set())
    aux.add(name)


# =============================================================================
# Data Loading
# =============================================================================


def _load_health_data(
    n: pypsa.Network,
    risk_breakpoints_path: str,
    cluster_cause_path: str,
    cause_log_path: str,
    cluster_summary_path: str,
    clusters_path: str,
) -> dict:
    """Load and preprocess all health-related input data.

    Returns a dictionary with all preprocessed data needed for constraint
    construction.
    """
    risk_breakpoints = pd.read_csv(risk_breakpoints_path)
    cluster_cause = pd.read_csv(cluster_cause_path)
    cause_log_breakpoints = pd.read_csv(cause_log_path)
    cluster_summary = pd.read_csv(cluster_summary_path)
    cluster_summary["health_cluster"] = cluster_summary["health_cluster"].astype(int)
    cluster_map = pd.read_csv(clusters_path)

    # Cluster lookups
    cluster_lookup = cluster_map.set_index("country_iso3")["health_cluster"].to_dict()

    # Cluster-cause metadata (baseline YLL, RR values)
    cluster_cause_metadata = cluster_cause.set_index(["health_cluster", "cause"])

    # Get cluster population from network metadata (computed at build time)
    cluster_population = get_health_cluster_population(n)

    # Sort breakpoint tables (risk breakpoints are cluster-specific)
    risk_breakpoints = risk_breakpoints.sort_values(
        ["health_cluster", "risk_factor", "intake_g_per_day", "cause"]
    )
    cause_log_breakpoints = cause_log_breakpoints.sort_values(["cause", "log_rr_total"])

    return {
        "risk_breakpoints": risk_breakpoints,
        "cluster_cause": cluster_cause,
        "cause_log_breakpoints": cause_log_breakpoints,
        "cluster_summary": cluster_summary,
        "cluster_cause_metadata": cluster_cause_metadata,
        "cluster_lookup": cluster_lookup,
        "cluster_population": cluster_population,
    }


# =============================================================================
# Stage 1: Intake → log(RR)
# =============================================================================


def _build_store_to_cluster_map(
    stores_df: pd.DataFrame,
    risk_factors: list[str],
    cluster_lookup: dict[str, int],
    cluster_population: dict[int, float],
) -> pd.DataFrame:
    """Map food group stores to health clusters with per-capita coefficients.

    For each food group store, computes the coefficient for converting store
    level (Mt/year) to per-capita intake (g/day):

        coeff = 10^12 / (365 * P_c)

    where P_c is the population of cluster c that country belongs to.

    Parameters
    ----------
    stores_df
        DataFrame of stores with 'food_group' and 'country' columns.
    risk_factors
        List of GBD risk factors (e.g., ['fruits', 'vegetables', ...]).
    cluster_lookup
        Mapping from country ISO3 to health cluster.
    cluster_population
        Population by health cluster.

    Returns
    -------
    pd.DataFrame
        Columns: store_name, risk_factor, country, cluster, per_capita_coeff.
    """
    # Filter for food group stores matching risk factors
    fg_stores = stores_df[stores_df["food_group"].isin(risk_factors)].copy()

    if fg_stores.empty:
        return pd.DataFrame()

    # Build mapping DataFrame using food_group column directly
    df = pd.DataFrame(
        {
            "store_name": fg_stores.index,
            "risk_factor": fg_stores["food_group"].values,
            "country": fg_stores["country"].values,
        }
    )

    # Map to cluster - fail if any countries are unmapped
    df["cluster"] = df["country"].map(cluster_lookup)
    unmapped = df[df["cluster"].isna()]["country"].unique()
    if len(unmapped) > 0:
        raise ValueError(f"Countries not mapped to health clusters: {sorted(unmapped)}")
    df["cluster"] = df["cluster"].astype(int)

    # Get cluster population - fail if any clusters have zero/missing population
    df["population"] = df["cluster"].map(cluster_population)
    zero_pop_clusters = df[df["population"].isna() | (df["population"] <= 0)][
        "cluster"
    ].unique()
    if len(zero_pop_clusters) > 0:
        raise ValueError(
            f"Health clusters with zero or missing population: {sorted(zero_pop_clusters)}"
        )

    # Per-capita coefficient: grams/megatonne / (365 * cluster_population)
    df["per_capita_coeff"] = constants.GRAMS_PER_MEGATONNE / (365.0 * df["population"])

    return df


def _build_intake_breakpoints(risk_breakpoints: pd.DataFrame) -> dict:
    """Build intake grids from RR breakpoint data.

    For each (health_cluster, risk_factor), creates:
        - intake_steps: Index of breakpoint positions
        - intake_values: xr.DataArray of intake values (g/day)
        - log_rr: DataFrame with log(RR) by (intake_step, cause)

    Risk breakpoints are cluster-specific due to age-weighted effective RR
    curves, so the output is keyed by (cluster, risk_factor).

    Parameters
    ----------
    risk_breakpoints
        DataFrame with columns: health_cluster, risk_factor, intake_g_per_day,
        cause, log_rr.

    Returns
    -------
    dict
        {(cluster, risk_factor): {intake_steps, intake_values, log_rr}}
    """
    risk_data = {}
    for (cluster, risk), grp in risk_breakpoints.groupby(
        ["health_cluster", "risk_factor"]
    ):
        cluster = int(cluster)
        intakes = pd.Index(sorted(grp["intake_g_per_day"].unique()), name="intake")
        if intakes.empty:
            continue

        # Pivot to get log_rr by (intake, cause)
        pivot = (
            grp.pivot_table(
                index="intake_g_per_day",
                columns="cause",
                values="log_rr",
                aggfunc="first",
            )
            .reindex(intakes, axis=0)
            .sort_index()
        )

        intake_steps = pd.Index(range(len(intakes)), name="intake_step")
        pivot.index = intake_steps

        risk_data[(cluster, risk)] = {
            "intake_steps": intake_steps,
            "intake_values": xr.DataArray(
                intakes.values, coords={"intake_step": intake_steps}, dims="intake_step"
            ),
            "log_rr": pivot,
        }

    return risk_data


def _group_cluster_risk_pairs(
    store_map: pd.DataFrame, intake_data: dict
) -> dict[str, list[tuple[int, str]]]:
    """Group (cluster, risk) pairs by risk_factor.

    All clusters share the same intake grid for a given risk (the grid is
    built once per risk in prepare_health_costs), and risk_cause_map keys
    the cause set by risk, so grouping by risk_factor lets a single SOS2 /
    delta variable array span all clusters for that risk while keeping
    cause columns aligned.
    """
    unique_pairs = store_map[["cluster", "risk_factor"]].drop_duplicates()

    risk_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for _, row in unique_pairs.iterrows():
        cluster = int(row["cluster"])
        risk = row["risk_factor"]

        if (cluster, risk) not in intake_data:
            continue

        risk_groups[risk].append((cluster, risk))

    return risk_groups


def _add_stage1_constraints(
    m: linopy.Model,
    store_map: pd.DataFrame,
    intake_groups: dict[str, list[tuple[int, str]]],
    intake_data: dict,
    store_level_var: xr.DataArray,
    baseline_intakes: dict[tuple[int, str], float],
) -> tuple[dict[tuple[int, str], linopy.LinearExpression], dict[int, float]]:
    """Add Stage 1 constraints: store level → log(RR_{r,d}).

    Stage 1 transforms food group store levels into log relative risk values
    using piecewise-linear interpolation with delta (incremental) variables.

    Uses the delta formulation:
        - δ_j ∈ [0,1], δ_j ≤ δ_{j-1} (fill-up ordering)
        - x = x_0 + Σ_j δ_j Δx_j, log(RR) = f_0 + Σ_j δ_j Δf_j

    To guarantee correct interpolation (only one fractional δ), segment
    indicator variables y_j ∈ [0,1] are added with SOS1 constraints.

    Parameters
    ----------
    m
        The linopy model.
    store_map
        Store mapping from _build_store_to_cluster_map.
    intake_groups
        (cluster, risk) pairs grouped by risk_factor.
    intake_data
        Breakpoint data from _build_intake_breakpoints.
    store_level_var
        Store level variables (food group stores).
    baseline_intakes
        {(cluster, risk_factor): baseline_intake_g_per_day} for MIP starts.

    Returns
    -------
    tuple
        (log_rr_totals, start_entries) where log_rr_totals maps
        (cluster, cause) to Σ_r log(RR_{r,d}) expressions, and
        start_entries maps column indices to MIP start values.
    """
    log_rr_totals: dict[tuple[int, str], linopy.LinearExpression] = {}
    start_entries: dict[int, float] = {}

    # Collect per-risk (cluster, cause)-indexed log(RR) contributions and merge
    # them in one shot at the end of the loop.  Doing element-wise scalar
    # accumulation inside the inner double loop here was the dominant cost in
    # _add_stage1_constraints: each `log_rr_totals[key] = log_rr_totals[key] +
    # expr` triggers a full linopy expression merge.
    per_risk_log_rr: list[linopy.LinearExpression] = []
    present_pairs: set[tuple[int, str]] = set()

    # Process (cluster, risk) pairs grouped by risk_factor. All clusters within
    # one risk share the same intake breakpoints and cause set, so a single
    # variable array can span every (cluster, risk) pair in the group.
    for risk_name, cluster_risk_pairs in intake_groups.items():
        first_cluster, _ = cluster_risk_pairs[0]
        risk_table = intake_data[(first_cluster, risk_name)]
        intake_values = risk_table["intake_values"]

        # Build labels and dataframes for this group
        cluster_risk_labels = [
            f"c{cluster}_r{risk}" for cluster, risk in cluster_risk_pairs
        ]
        cluster_risk_index = pd.Index(cluster_risk_labels, name="cluster_risk")
        pairs_df = pd.DataFrame(cluster_risk_pairs, columns=["cluster", "risk_factor"])
        pairs_df["cluster_risk"] = cluster_risk_labels

        # -----------------------------------------------------------------------
        # Build intake expression from stores
        # -----------------------------------------------------------------------
        # Each country c in cluster C has a food group store with level s_c (Mt/year).
        # Cluster intake I_C is the population-weighted average:
        #
        #     I_C = Σ_{c∈C} s_c * (10^12 g/Mt) / (365 days * P_C persons)
        #
        # where P_C is cluster population.

        stores_with_labels = store_map.merge(
            pairs_df, on=["cluster", "risk_factor"], how="inner"
        )

        if stores_with_labels.empty:
            continue

        store_names = stores_with_labels["store_name"].values
        per_capita_coeffs = xr.DataArray(
            stores_with_labels["per_capita_coeff"].values,
            coords={"name": store_names},
            dims="name",
        )
        grouper = xr.DataArray(
            stores_with_labels["cluster_risk"].values,
            coords={"name": store_names},
            dims="name",
            name="cluster_risk",
        )

        # Aggregated store level expression by cluster_risk (g/person/day)
        store_expr = (
            (store_level_var.sel(name=store_names) * per_capita_coeffs)
            .groupby(grouper)
            .sum()
        )

        # -----------------------------------------------------------------------
        # Build log(RR) breakpoint data
        # -----------------------------------------------------------------------
        log_rr_frames = [
            intake_data[(cluster, risk)]["log_rr"]
            for cluster, risk in cluster_risk_pairs
        ]

        if not log_rr_frames:
            continue

        # Concat along cluster_risk dimension. All frames in the group share
        # the same risk_factor (hence the same cause columns), so the column
        # union is trivial and no NaNs are introduced.
        combined_log_rr = pd.concat(
            log_rr_frames,
            keys=cluster_risk_index,
            names=["cluster_risk", "intake_step"],
        )

        # Convert to DataArray: (cluster_risk, intake_step, cause)
        stacked_log_rr = combined_log_rr.stack()
        stacked_log_rr.index.names = ["cluster_risk", "intake_step", "cause"]
        log_rr_by_intake = xr.DataArray.from_series(stacked_log_rr)

        # -----------------------------------------------------------------------
        # Delta formulation (same structure for both solvers)
        # -----------------------------------------------------------------------
        log_rr_contrib = _add_stage1_delta(
            m=m,
            store_expr=store_expr,
            intake_values=intake_values,
            log_rr_by_intake=log_rr_by_intake,
            cluster_risk_index=cluster_risk_index,
            risk_label=risk_name,
            cluster_risk_pairs=cluster_risk_pairs,
            baseline_intakes=baseline_intakes,
            start_entries=start_entries,
        )

        # -----------------------------------------------------------------------
        # Accumulate log(RR) by cluster
        # -----------------------------------------------------------------------
        # The multiplicative RR relationship becomes additive in log space:
        #     RR_d = ∏_r RR_{r,d}  ⟹  log(RR_d) = Σ_r log(RR_{r,d})

        cluster_by_label = pairs_df.set_index("cluster_risk")["cluster"]
        present_labels = log_rr_contrib.coords["cluster_risk"].values
        cluster_grouper = xr.DataArray(
            cluster_by_label.loc[present_labels].values,
            coords={"cluster_risk": present_labels},
            dims="cluster_risk",
            name="cluster",
        )

        # Sum over r of log(RR_{r,d}) for each (cluster, cause)
        log_rr_by_cluster = log_rr_contrib.groupby(cluster_grouper).sum()

        # Defer the actual cross-risk sum until after the loop: collect the
        # per-risk array and record which (cluster, cause) keys it contributes
        # to, so the downstream dict only contains pairs that were actually
        # observed (matching the original behaviour).
        per_risk_log_rr.append(log_rr_by_cluster)
        risk_clusters = log_rr_by_cluster.coords["cluster"].values
        risk_causes = log_rr_by_cluster.coords["cause"].values
        for c in risk_clusters:
            for cause in risk_causes:
                present_pairs.add((int(c), str(cause)))

    # Cross-risk sum: linopy.merge along the term dimension is equivalent to
    # summing the expressions, but does the work as a single xarray.concat
    # with outer alignment on (cluster, cause) instead of one merge per pair.
    if per_risk_log_rr:
        if len(per_risk_log_rr) == 1:
            combined = per_risk_log_rr[0]
        else:
            combined = linopy.merge(per_risk_log_rr, dim="_term")
        for cluster_key, cause_key in present_pairs:
            log_rr_totals[(cluster_key, cause_key)] = combined.sel(
                cluster=cluster_key, cause=cause_key
            )

    return log_rr_totals, start_entries


def _add_stage1_delta(
    m: linopy.Model,
    store_expr: linopy.LinearExpression,
    intake_values: xr.DataArray,
    log_rr_by_intake: xr.DataArray,
    cluster_risk_index: pd.Index,
    risk_label: str,
    cluster_risk_pairs: list[tuple[int, str]],
    baseline_intakes: dict[tuple[int, str], float],
    start_entries: dict[int, float],
) -> linopy.LinearExpression:
    """Stage 1 delta formulation with segment indicators.

    Creates δ variables with fill-up constraints for piecewise-linear interpolation:
        x = x_0 + Σ_j δ_j Δx_j,  f(x) = f_0 + Σ_j δ_j Δf_j

    Segment indicator variables y_j ∈ [0,1] with SOS1 constraints guarantee
    correct interpolation. Linopy's ``reformulate_sos='auto'`` converts these
    to binary+Big-M constraints for solvers that don't support SOS natively.

    Linking constraints tie δ and y:
        - δ_i ≥ Σ_{k>i} y_k  (δ_i = 1 if active segment is later)
        - δ_i ≤ Σ_{k≥i} y_k  (δ_i = 0 if active segment is earlier)

    Returns log(RR) expression indexed by (cluster_risk, cause).
    """
    intake_steps = intake_values.coords["intake_step"]
    n_points = len(intake_steps)
    n_segments = n_points - 1
    segment_dim = "intake_step_seg"
    segment_coords = pd.Index(range(n_segments), name=segment_dim)

    # Compute segment widths: Δx_j = x_{j+1} - x_j
    delta_x = intake_values.diff("intake_step")
    delta_x = delta_x.rename({"intake_step": segment_dim})
    delta_x = delta_x.assign_coords({segment_dim: segment_coords})

    group_id = next(_LAMBDA_GROUP_COUNTER)

    # Create δ variables
    delta_var = m.add_variables(
        lower=0,
        upper=1,
        coords=[cluster_risk_index, segment_coords],
        name=f"health_delta_group_{group_id}_{risk_label}",
    )
    _register_auxiliary_variable(m, delta_var.name)

    # Fill-up constraints: δ_j ≤ δ_{j-1} for j ≥ 1
    # Vectorized: use roll() to shift values, then compare slices with aligned coords
    if n_segments > 1:
        # Roll shifts values circularly by -1: [δ0, δ1, ..., δn-1] -> [δ1, δ2, ..., δn-1, δ0]
        # Select first n-1 elements to get [δ1, δ2, ..., δn-1] with coords [0, 1, ..., n-2]
        delta_rolled = delta_var.roll({segment_dim: -1})
        delta_current = delta_rolled.isel(
            {segment_dim: slice(0, -1)}
        )  # δ[j] for j=1..n-1
        delta_prev = delta_var.isel({segment_dim: slice(0, -1)})  # δ[j-1] for j=1..n-1

        # Both have same coords [0, 1, ..., n-2], so comparison works directly
        # Constraint: δ[j] ≤ δ[j-1]
        m.add_constraints(
            delta_current <= delta_prev,
            name=f"health_delta_fillup_{group_id}_{risk_label}",
        )

    # -----------------------------------------------------------------------
    # Segment indicator variables for correct interpolation
    # -----------------------------------------------------------------------
    # y_j indicates segment j is "active" (contains the fractional δ)
    # Exactly one segment is active: Σ y_j = 1
    # SOS1 constraint ensures at most one y_j is non-zero per cluster_risk.
    # linopy's reformulate_sos='auto' converts to binary+Big-M for solvers
    # that don't support SOS natively (e.g. HiGHS).
    y_var = m.add_variables(
        lower=0,
        upper=1,
        coords=[cluster_risk_index, segment_coords],
        name=f"health_segment_ind_{group_id}_{risk_label}",
    )
    m.add_sos_constraints(y_var, sos_type=1, sos_dim=segment_dim)

    _register_auxiliary_variable(m, y_var.name)

    # Exactly one segment active
    m.add_constraints(
        y_var.sum(segment_dim) == 1,
        name=f"health_segment_sum_{group_id}_{risk_label}",
    )

    # Linking constraints between δ and y
    # For segment j active (y_j = 1):
    #   - δ_0 = δ_1 = ... = δ_{j-1} = 1 (all before are full)
    #   - δ_j ∈ [0, 1] (the active one is fractional)
    #   - δ_{j+1} = ... = δ_{n-1} = 0 (all after are empty)
    #
    # Constraints:
    #   δ_i ≥ Σ_{k=i+1}^{n-1} y_k  (δ_i = 1 if active segment is later than i)
    #   δ_i ≤ Σ_{k=i}^{n-1} y_k    (δ_i = 0 if active segment is before i)
    #
    # Vectorized implementation using suffix sums computed via matrix multiplication.

    # Build suffix sum coefficient matrix: A[i,j] = 1 if j >= i
    # y_suffix[i] = Σ_{j>=i} y[j] = (A @ y)[i]
    suffix_matrix = np.triu(np.ones((n_segments, n_segments)))
    suffix_coeffs = xr.DataArray(
        suffix_matrix,
        dims=[segment_dim, "sum_over"],
        coords={segment_dim: segment_coords, "sum_over": segment_coords},
    )

    # Convert y_var to LinearExpression and rename dimension for matrix multiply.
    # We use to_linexpr() to avoid sos_dim validation issues with Variable.rename().
    y_linexpr = y_var.to_linexpr()
    y_linexpr_renamed = y_linexpr.rename({segment_dim: "sum_over"})

    # Compute suffix sums: y_suffix[i] = Σ_{j>=i} y[j]
    # Shape: (n_cluster_risk, n_segments)
    y_suffix = (y_linexpr_renamed * suffix_coeffs).sum("sum_over")

    # Upper bound constraints: δ[i] <= y_suffix[i] for all i=0..n-1
    # Both delta_var and y_suffix have same coords, so direct comparison works
    m.add_constraints(
        delta_var <= y_suffix,
        name=f"health_delta_upper_{group_id}_{risk_label}",
    )

    # Lower bound constraints: δ[i] >= y_suffix[i+1] for i=0..n-2
    # y_suffix[i+1] = Σ_{k>i} y_k (the "later" sum)
    if n_segments > 1:
        # Use roll to shift y_suffix by -1, then take first n-1 elements
        # This aligns y_suffix[i+1] with coords [0, 1, ..., n-2]
        y_later_rolled = y_suffix.roll({segment_dim: -1})
        y_later = y_later_rolled.isel({segment_dim: slice(0, -1)})  # y_suffix[i+1]
        delta_for_lower = delta_var.isel({segment_dim: slice(0, -1)})  # δ[i]

        # Both have coords [0, 1, ..., n-2], comparison works directly
        m.add_constraints(
            delta_for_lower >= y_later,
            name=f"health_delta_lower_{group_id}_{risk_label}",
        )

    # Intake balance: I_{c,r} = x_0 + Σ_j δ_j Δx_j
    x_0 = float(intake_values.isel(intake_step=0).values)
    intake_expr = x_0 + (delta_var * delta_x).sum(segment_dim)
    intake_expr = intake_expr.reindex(
        cluster_risk=store_expr.data.coords["cluster_risk"]
    )
    m.add_constraints(
        store_expr == intake_expr,
        name=f"health_delta_intake_balance_{group_id}_{risk_label}",
    )

    # Compute log(RR): log(RR_{c,r,d}) = f_0 + Σ_j δ_j Δf_j
    # Need to compute delta_f for each cause
    #
    # Manually compute differences to ensure coordinate alignment.
    # diff() can produce misaligned indices that cause broadcasting issues.
    causes = log_rr_by_intake.coords["cause"].values
    cluster_risk_vals = cluster_risk_index.values

    # Build delta_log_rr with explicit coordinates
    delta_log_rr_data = np.zeros(
        (len(cluster_risk_vals), len(segment_coords), len(causes))
    )
    for j in range(len(segment_coords)):
        delta_log_rr_data[:, j, :] = (
            log_rr_by_intake.sel(cluster_risk=cluster_risk_vals)
            .isel(intake_step=j + 1)
            .values
            - log_rr_by_intake.sel(cluster_risk=cluster_risk_vals)
            .isel(intake_step=j)
            .values
        )

    delta_log_rr = xr.DataArray(
        delta_log_rr_data,
        coords={
            "cluster_risk": cluster_risk_vals,
            segment_dim: segment_coords.values,
            "cause": causes,
        },
        dims=["cluster_risk", segment_dim, "cause"],
    )

    # f_0 is the constant offset (value at first breakpoint)
    f_0_data = (
        log_rr_by_intake.sel(cluster_risk=cluster_risk_vals).isel(intake_step=0).values
    )
    f_0 = xr.DataArray(
        f_0_data,
        coords={"cluster_risk": cluster_risk_vals, "cause": causes},
        dims=["cluster_risk", "cause"],
    )

    # Compute expression: f_0 + Σ_j δ_j Δf_j
    # Note: Use delta_contrib + f_0 (not f_0 + delta_contrib) so that linopy's
    # __add__ handles the addition properly. DataArray.__add__ doesn't know
    # how to handle LinearExpressions.
    delta_contrib = (delta_var * delta_log_rr).sum(segment_dim)
    log_rr_contrib = delta_contrib + f_0

    # -----------------------------------------------------------------------
    # MIP start values from baseline intake
    # -----------------------------------------------------------------------
    breakpoints = intake_values.values
    for label, (cluster, risk) in zip(cluster_risk_index, cluster_risk_pairs):
        intake = baseline_intakes.get((cluster, risk))
        if intake is None:
            continue
        # Find active segment via searchsorted
        seg = int(np.searchsorted(breakpoints[1:], intake, side="right"))
        seg = min(seg, n_segments - 1)

        # y_var: indicator = 1 for active segment, 0 otherwise
        for j in range(n_segments):
            col = int(y_var.labels.sel(cluster_risk=label, intake_step_seg=j))
            start_entries[col] = 1.0 if j == seg else 0.0

        # delta_var: fill-up pattern
        for j in range(n_segments):
            col = int(delta_var.labels.sel(cluster_risk=label, intake_step_seg=j))
            if j < seg:
                start_entries[col] = 1.0
            elif j == seg:
                bp_lo = float(breakpoints[j])
                bp_hi = float(breakpoints[j + 1])
                frac = (intake - bp_lo) / (bp_hi - bp_lo) if bp_hi > bp_lo else 0.5
                start_entries[col] = max(0.0, min(1.0, frac))
            else:
                start_entries[col] = 0.0

    return log_rr_contrib


# =============================================================================
# Stage 2: log(RR) → YLL Store Level
# =============================================================================


def _build_cause_breakpoints(cause_log_breakpoints: pd.DataFrame) -> dict:
    """Build log-RR breakpoint grids by cause.

    Returns
    -------
    dict
        {cause: DataFrame with columns log_rr_total, rr_total}
    """
    return {
        cause: df.sort_values("log_rr_total")
        for cause, df in cause_log_breakpoints.groupby("cause")
    }


def _group_cluster_cause_pairs(
    cluster_cause_metadata: pd.DataFrame,
    cause_breakpoints: dict,
    cluster_population: dict[int, float],
) -> tuple[dict, dict]:
    """Group (cluster, cause) pairs by shared log-RR coordinate patterns.

    Computes absolute YLL from stored rates using planning-year population.

    Returns
    -------
    tuple
        (log_total_groups, cluster_cause_data) where:
        - log_total_groups: {coords_key: [(cluster, cause), ...]}
        - cluster_cause_data: {(cluster, cause): {yll_total, rr_ref, rr_baseline, cause_bp}}
    """
    log_total_groups: dict[tuple[float, ...], list[tuple[int, str]]] = defaultdict(list)
    cluster_cause_data: dict[tuple[int, str], dict] = {}

    for (cluster, cause), row in cluster_cause_metadata.iterrows():
        cluster = int(cluster)
        cause = str(cause)

        # Reconstruct absolute YLL from rate using planning-year population
        yll_rate_per_100k = float(row["yll_rate_per_100k"])
        pop = cluster_population[cluster]
        yll_total = (yll_rate_per_100k / constants.PER_100K) * pop

        cause_bp = cause_breakpoints.get(cause)
        if cause_bp is None:
            continue

        coords_key = tuple(cause_bp["log_rr_total"].values)
        if len(coords_key) == 1:
            raise ValueError(
                "Need at least two breakpoints for piecewise linear approximation"
            )

        log_total_groups[coords_key].append((cluster, cause))

        # Store metadata for constraint construction
        log_rr_total_ref = float(row["log_rr_total_ref"])
        log_rr_total_baseline = float(row["log_rr_total_baseline"])
        cluster_cause_data[(cluster, cause)] = {
            "yll_total": yll_total,
            "log_rr_total_ref": log_rr_total_ref,
            "rr_ref": math.exp(log_rr_total_ref),
            "rr_baseline": math.exp(log_rr_total_baseline),
            "cause_bp": cause_bp,
        }

    return log_total_groups, cluster_cause_data


def _add_stage2_constraints(
    m: linopy.Model,
    log_rr_totals: dict[tuple[int, str], linopy.LinearExpression],
    log_total_groups: dict[tuple[float, ...], list[tuple[int, str]]],
    cluster_cause_data: dict[tuple[int, str], dict],
    health_stores: pd.DataFrame,
    store_level_var: xr.DataArray,
) -> int:
    """Add Stage 2 constraints: Σ_r log(RR_{r,d}) → YLL store level.

    The chord PWL approximation of exp() through the cause breakpoints is
    convex, so the constraint ``rr >= chord_PWL(log_total)`` is equivalent
    to one chord inequality per piece (the per-piece chord lower bounds
    collapse at the optimum to the exact chord PWL value).

    Substituting into the health cost expression, this gives, for each
    piece j of the cause's breakpoints,

        store_var >= scale_factor * (slope_j * log_total + intercept_j - rr_ref)

    together with the domain bound ``log_total ∈ [log_pts[0], log_pts[-1]]``.

    The store level represents the health cost normalized by V (value per YLL):

        e_{c,d} = (RR_d - RR_d^ref) * (YLL_{c,d} / RR_d^base) * 10^{-6}

    Returns
    -------
    int
        Number of (cluster, cause) pairs handled.
    """
    constraints_added = 0
    for log_rr_grid, cluster_cause_pairs in log_total_groups.items():
        log_pts = np.asarray(log_rr_grid, dtype=float)
        sample_data = cluster_cause_data[cluster_cause_pairs[0]]
        rr_pts = sample_data["cause_bp"]["rr_total"].values.astype(float)

        constraints_added += _add_stage2_lp_tangent(
            m=m,
            log_rr_totals=log_rr_totals,
            cluster_cause_pairs=cluster_cause_pairs,
            cluster_cause_data=cluster_cause_data,
            health_stores=health_stores,
            store_level_var=store_level_var,
            log_pts=log_pts,
            rr_pts=rr_pts,
        )
    return constraints_added


def _add_stage2_lp_tangent(
    m: linopy.Model,
    log_rr_totals: dict[tuple[int, str], linopy.LinearExpression],
    cluster_cause_pairs: list[tuple[int, str]],
    cluster_cause_data: dict[tuple[int, str], dict],
    health_stores: pd.DataFrame,
    store_level_var: xr.DataArray,
    log_pts: np.ndarray,
    rr_pts: np.ndarray,
) -> int:
    """Stage 2 LP-tangent formulation (no auxiliary variables).

    For each piece j of the chord PWL approximation of exp() through the
    cause breakpoints, we add

        store_var >= scale_factor * (slope_j * log_total + intercept_j - rr_ref)

    plus the domain bound log_total ∈ [log_pts[0], log_pts[-1]]. Because
    exp() is convex and store_var carries a non-negative coefficient in the
    objective, the per-piece chord lower bounds collapse at the optimum to
    the exact chord-PWL value of exp(log_total) — mathematically equivalent
    to the previous δ-fill-up formulation.

    Returns the number of (cluster, cause) pairs handled.
    """
    n_seg = len(log_pts) - 1
    slopes = np.diff(rr_pts) / np.diff(log_pts)
    intercepts = rr_pts[:-1] - slopes * log_pts[:-1]
    log_lo = float(log_pts[0])
    log_hi = float(log_pts[-1])

    piece_index = pd.Index(range(n_seg), name="health_chord_piece")
    slopes_xr = xr.DataArray(slopes, coords={"health_chord_piece": piece_index})
    intercepts_xr = xr.DataArray(intercepts, coords={"health_chord_piece": piece_index})

    for cluster, cause in cluster_cause_pairs:
        if (cluster, cause) not in log_rr_totals:
            raise ValueError(
                f"No log_rr total from Stage 1 for cluster {cluster}, cause {cause}. "
                "Check that food group stores exist and map to health clusters."
            )
        total_expr = log_rr_totals[(cluster, cause)]
        data = cluster_cause_data[(cluster, cause)]
        rr_ref = data["rr_ref"]
        scale_factor = (
            data["yll_total"] / data["rr_baseline"] * constants.YLL_TO_MILLION_YLL
        )

        store_name = health_stores.loc[(cluster, cause), "name"]
        store_var = store_level_var.sel(name=store_name)

        m.add_constraints(
            store_var - scale_factor * slopes_xr * total_expr
            >= scale_factor * (intercepts_xr - rr_ref),
            name=f"health_stage2_chord_c{cluster}_cause{cause}",
        )
        m.add_constraints(
            total_expr >= log_lo,
            name=f"health_stage2_dom_lo_c{cluster}_cause{cause}",
        )
        m.add_constraints(
            total_expr <= log_hi,
            name=f"health_stage2_dom_hi_c{cluster}_cause{cause}",
        )

    return len(cluster_cause_pairs)


# =============================================================================
# Main Entry Point
# =============================================================================


def _expand_rr_groups(
    rr_quantiles: dict[str, float],
    risk_breakpoints: pd.DataFrame,
) -> dict[str, float]:
    """Expand grouped RR quantile keys to individual risk factors.

    The keys ``"protective"`` and ``"harmful"`` are expanded to the
    individual risk factors whose dose-response curves decrease or
    increase with intake, respectively. Direction is inferred from
    the data: for each risk factor, compare log_rr at the lowest and
    highest intake — if log_rr increases with intake, the factor is
    harmful; otherwise protective.

    Individual risk factor keys pass through unchanged and take
    precedence over group keys (an overlap raises ``ValueError``).

    Parameters
    ----------
    rr_quantiles
        Mapping that may contain ``"protective"`` / ``"harmful"`` group
        keys and/or individual risk factor keys.
    risk_breakpoints
        DataFrame with columns ``risk_factor``, ``intake_g_per_day``,
        ``log_rr``.

    Returns
    -------
    dict[str, float]
        Expanded mapping from individual risk factor names to quantiles.
    """
    group_keys = {"protective", "harmful"}
    present_groups = group_keys & rr_quantiles.keys()
    if not present_groups:
        return rr_quantiles

    # Classify each risk factor by slope direction (use first cluster's data
    # since the slope direction is the same across all clusters)
    protective, harmful = [], []
    first_cluster = risk_breakpoints["health_cluster"].iloc[0]
    single_cluster_bp = risk_breakpoints[
        risk_breakpoints["health_cluster"] == first_cluster
    ]
    for risk, grp in single_cluster_bp.groupby("risk_factor"):
        sorted_grp = grp.sort_values("intake_g_per_day")
        log_rr_low_intake = sorted_grp["log_rr"].iloc[0]
        log_rr_high_intake = sorted_grp["log_rr"].iloc[-1]
        if log_rr_high_intake > log_rr_low_intake:
            harmful.append(risk)
        else:
            protective.append(risk)

    group_map = {"protective": protective, "harmful": harmful}

    # Build expanded dict: individual keys first, then fill from groups
    individual_keys = {k: v for k, v in rr_quantiles.items() if k not in group_keys}
    expanded = dict(individual_keys)

    for group_key in present_groups:
        q = rr_quantiles[group_key]
        for risk in group_map[group_key]:
            if risk in individual_keys:
                raise ValueError(
                    f"Risk factor '{risk}' is specified both individually and "
                    f"via the '{group_key}' group key"
                )
            expanded[risk] = q

    return expanded


def _apply_rr_quantiles(
    risk_breakpoints: pd.DataFrame,
    rr_quantiles: dict[str, float],
) -> pd.DataFrame:
    """Interpolate log_rr using per-risk-factor quantiles between GBD bounds.

    For each risk factor with a quantile value q in [0, 1]:
        log_rr(q) = (1 - q) * log_rr_low + q * log_rr_high

    Accepts grouped keys (``"protective"``, ``"harmful"``) which are
    expanded to individual risk factors based on slope direction.

    Parameters
    ----------
    risk_breakpoints
        DataFrame with columns: risk_factor, log_rr, log_rr_low, log_rr_high.
    rr_quantiles
        Mapping from risk factor name (or group key) to quantile in [0, 1].

    Returns
    -------
    pd.DataFrame
        Modified risk_breakpoints with interpolated log_rr values.
    """
    rr_quantiles = _expand_rr_groups(rr_quantiles, risk_breakpoints)
    risk_breakpoints = risk_breakpoints.copy()
    for risk, q in rr_quantiles.items():
        mask = risk_breakpoints["risk_factor"] == risk
        if not mask.any():
            logger.warning(
                "RR quantile specified for unknown risk factor '%s'; skipping", risk
            )
            continue
        risk_breakpoints.loc[mask, "log_rr"] = (1 - q) * risk_breakpoints.loc[
            mask, "log_rr_low"
        ] + q * risk_breakpoints.loc[mask, "log_rr_high"]
    logger.info(
        "Applied RR quantiles for %d risk factors: %s",
        len(rr_quantiles),
        ", ".join(f"{r}={q:.3f}" for r, q in rr_quantiles.items()),
    )
    return risk_breakpoints


def _recompute_rr_ref(
    risk_breakpoints: pd.DataFrame,
    tmrel: dict[str, float],
    risk_cause_map: dict[str, list[str]],
    cluster_cause_metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Recompute log_rr_total_ref from interpolated breakpoints at TMREL.

    After quantile interpolation changes the log_rr values, the reference
    RR at TMREL must be recomputed to maintain consistency.

    Parameters
    ----------
    risk_breakpoints
        DataFrame with columns: risk_factor, cause, intake_g_per_day, log_rr.
    tmrel
        TMREL intake per risk factor (g/day).
    risk_cause_map
        Mapping from risk factor to list of affected causes.
    cluster_cause_metadata
        DataFrame indexed by (health_cluster, cause) with log_rr_total_ref column.

    Returns
    -------
    pd.DataFrame
        Modified cluster_cause_metadata with recomputed log_rr_total_ref.
    """
    cluster_cause_metadata = cluster_cause_metadata.copy()

    # Compute log_rr at TMREL for each (cluster, risk_factor, cause)
    # Breakpoints are cluster-specific due to age-weighted effective RR
    log_rr_at_tmrel: dict[tuple[int, str, str], float] = {}
    for risk, causes in risk_cause_map.items():
        tmrel_intake = tmrel.get(risk, 0.0)
        for cause in causes:
            for cluster, _ in cluster_cause_metadata.index:
                cluster = int(cluster)
                bp = risk_breakpoints[
                    (risk_breakpoints["health_cluster"] == cluster)
                    & (risk_breakpoints["risk_factor"] == risk)
                    & (risk_breakpoints["cause"] == cause)
                ].sort_values("intake_g_per_day")
                if bp.empty:
                    continue
                log_rr_val = float(
                    np.interp(
                        tmrel_intake,
                        bp["intake_g_per_day"].values,
                        bp["log_rr"].values,
                    )
                )
                log_rr_at_tmrel[(cluster, risk, cause)] = log_rr_val

    # Sum per (cluster, cause)
    for cluster, cause in cluster_cause_metadata.index:
        cluster = int(cluster)
        cause = str(cause)
        total = 0.0
        for risk, causes in risk_cause_map.items():
            if cause in causes:
                total += log_rr_at_tmrel.get((cluster, risk, cause), 0.0)
        cluster_cause_metadata.at[(cluster, cause), "log_rr_total_ref"] = total

    return cluster_cause_metadata


def add_health_objective(
    n: pypsa.Network,
    risk_breakpoints_path: str,
    cluster_cause_path: str,
    cause_log_path: str,
    cluster_summary_path: str,
    clusters_path: str,
    risk_factors: list[str],
    risk_cause_map: dict[str, list[str]],
    value_per_yll: float,
    cluster_risk_baseline_path: str,
    rr_quantiles: dict[str, float] | None = None,
    tmrel_path: str | None = None,
) -> None:
    """Add health cost constraints to the optimization model.

    This implements the health cost formulation from docs/health.rst:

        Cost_{c,d}(x) = V * (YLL_{c,d} / RR_d(x^base)) * (RR_d(x) - RR_d^ref)

    where:
        - V = value_per_yll (USD per year of life lost)
        - YLL_{c,d} = baseline years of life lost for cluster c, disease d
        - RR_d(x) = relative risk at intake x (product over risk factors)
        - RR_d^ref = RR at TMREL (theoretical minimum risk exposure level)
        - x^base = baseline intake

    The implementation uses two-stage SOS2 interpolation to handle the
    nonlinear multiplicative combination of relative risks:

        Stage 1: Intake x_r → log(RR_{r,d})
        Stage 2: Σ_r log(RR_{r,d}) → RR_d → YLL store level

    Parameters
    ----------
    n
        The PyPSA network with health stores already added. Population data
        for health clusters is read from the network metadata.
    risk_breakpoints_path
        Path to CSV with (risk_factor, intake_g_per_day, cause, log_rr).
    cluster_cause_path
        Path to CSV with (health_cluster, cause, yll_total, log_rr_total_ref,
        log_rr_total_baseline).
    cause_log_path
        Path to CSV with (cause, log_rr_total, rr_total) breakpoints.
    cluster_summary_path
        Path to CSV with cluster metadata.
    clusters_path
        Path to CSV mapping countries to health clusters.
    risk_factors
        List of risk factors to include (e.g., ['fruits', 'vegetables', ...]).
    risk_cause_map
        Mapping from risk factor to list of affected causes.
    value_per_yll
        Monetary value per year of life lost (USD).
    cluster_risk_baseline_path
        Path to CSV with (health_cluster, risk_factor, baseline_intake_g_per_day)
        for computing MIP start values for Stage 1 binary variables.
    rr_quantiles
        Optional per-risk-factor quantile values in [0, 1] for interpolating
        between GBD rr_low (q=0) and rr_high (q=1). When provided, log_rr
        values in risk_breakpoints are replaced with interpolated values,
        and rr_ref is recomputed at TMREL.
    tmrel_path
        Path to CSV with derived TMREL values (risk_factor, tmrel_g_per_day).
        Required when rr_quantiles is provided.
    """
    m = n.model

    # --- Load Data ---
    data = _load_health_data(
        n,
        risk_breakpoints_path,
        cluster_cause_path,
        cause_log_path,
        cluster_summary_path,
        clusters_path,
    )

    risk_breakpoints = data["risk_breakpoints"]
    cause_log_breakpoints = data["cause_log_breakpoints"]
    cluster_cause_metadata = data["cluster_cause_metadata"]
    cluster_lookup = data["cluster_lookup"]
    cluster_population = data["cluster_population"]

    logger.info(
        "Health data: %d risk breakpoints across %d risks / %d causes; %d cause breakpoints",
        len(risk_breakpoints),
        risk_breakpoints["risk_factor"].nunique(),
        risk_breakpoints["cause"].nunique(),
        len(cause_log_breakpoints),
    )

    # --- Validate Risk-Cause Pairs ---
    available_risks = set(risk_breakpoints["risk_factor"].unique())
    risk_cause_map = {
        r: causes for r, causes in risk_cause_map.items() if r in available_risks
    }

    allowed_pairs = {(r, c) for r, causes in risk_cause_map.items() for c in causes}
    rb_pairs = set(zip(risk_breakpoints["risk_factor"], risk_breakpoints["cause"]))
    missing_pairs = sorted(allowed_pairs - rb_pairs)
    if missing_pairs:
        text = ", ".join([f"{r}:{c}" for r, c in missing_pairs])
        raise ValueError(f"Risk breakpoints missing required pairs: {text}")

    # --- Apply RR Quantile Interpolation (Sensitivity) ---
    if rr_quantiles:
        if tmrel_path is None:
            raise ValueError("tmrel_path is required when rr_quantiles is provided")
        risk_breakpoints = _apply_rr_quantiles(risk_breakpoints, rr_quantiles)

        # Load TMREL and recompute rr_ref in cluster_cause_metadata
        tmrel_df = pd.read_csv(tmrel_path)
        tmrel = dict(
            zip(tmrel_df["risk_factor"], tmrel_df["tmrel_g_per_day"].astype(float))
        )
        cluster_cause_metadata = _recompute_rr_ref(
            risk_breakpoints, tmrel, risk_cause_map, cluster_cause_metadata
        )

    # --- Build Store Map ---
    # Map food group stores to health clusters with per-capita coefficients.
    store_level_var = m.variables["Store-e"].sel(snapshot="now")

    store_map = _build_store_to_cluster_map(
        n.stores.static,
        risk_factors,
        cluster_lookup,
        cluster_population,
    )

    if store_map.empty:
        logger.info("No food group stores map to health risk factors; skipping")
        return

    logger.info(
        "Health intake mapping: %d stores -> %d cluster-risk pairs across %d clusters",
        len(store_map),
        len(store_map[["cluster", "risk_factor"]].drop_duplicates()),
        store_map["cluster"].nunique(),
    )

    # --- Load Baseline Intakes for MIP Start ---
    crb = pd.read_csv(cluster_risk_baseline_path)
    baseline_intakes = {
        (int(r.health_cluster), r.risk_factor): r.baseline_intake_g_per_day
        for r in crb.itertuples()
    }

    # --- Stage 1: Store Level → log(RR) ---
    intake_data = _build_intake_breakpoints(risk_breakpoints)
    intake_groups = _group_cluster_risk_pairs(store_map, intake_data)

    log_rr_totals, start_entries = _add_stage1_constraints(
        m,
        store_map,
        intake_groups,
        intake_data,
        store_level_var,
        baseline_intakes,
    )

    # --- Set MIP Start ---
    if start_entries:
        indices = np.array(sorted(start_entries), dtype=np.int32)
        values = np.array([start_entries[i] for i in indices], dtype=np.float64)
        m._mip_start = (len(indices), indices, values)
        logger.info("Set MIP start for %d Stage 1 variables", len(indices))

    # --- Stage 2: log(RR) → YLL Store ---
    cause_breakpoints = _build_cause_breakpoints(cause_log_breakpoints)
    log_total_groups, cluster_cause_data = _group_cluster_cause_pairs(
        cluster_cause_metadata, cause_breakpoints, cluster_population
    )

    logger.info(
        "Health risk aggregation: %d (cluster, cause) pairs grouped into %d log-RR grids",
        len(cluster_cause_data),
        len(log_total_groups),
    )

    # Get health store mapping
    health_stores = (
        n.stores.static[
            n.stores.static["carrier"].notna()
            & n.stores.static["carrier"].str.startswith("yll_")
        ]
        .reset_index()
        .set_index(["health_cluster", "cause"])
    )

    constraints_added = _add_stage2_constraints(
        m,
        log_rr_totals,
        log_total_groups,
        cluster_cause_data,
        health_stores,
        store_level_var,
    )

    logger.info("Added %d health store level constraints", constraints_added)


# =============================================================================
# Post-hoc Health Evaluation (when value_per_yll == 0)
# =============================================================================


def evaluate_health_posthoc(
    n: pypsa.Network,
    risk_breakpoints_path: str,
    cluster_cause_path: str,
    cause_log_path: str,
    clusters_path: str,
    risk_factors: list[str],
    risk_cause_map: dict[str, list[str]],
    rr_quantiles: dict[str, float] | None = None,
    tmrel_path: str | None = None,
) -> None:
    """Evaluate health impacts numerically from the solved network.

    When ``value_per_yll == 0`` the health piecewise-linear constraints are
    skipped to keep the model as a pure LP. This function replicates the
    same dose-response chain as numpy arithmetic and writes the resulting
    YLL values into the network's store energy levels.

    Parameters
    ----------
    n
        Solved PyPSA network with food group and YLL stores.
    risk_breakpoints_path
        CSV with (risk_factor, intake_g_per_day, cause, log_rr).
    cluster_cause_path
        CSV with (health_cluster, cause, yll_rate_per_100k,
        yll_attrib_rate_per_100k, log_rr_total_ref, log_rr_total_baseline).
    cause_log_path
        CSV with (cause, log_rr_total, rr_total) breakpoints.
    clusters_path
        CSV mapping countries to health clusters.
    risk_factors
        List of GBD risk factors.
    risk_cause_map
        Mapping from risk factor to list of affected causes.
    rr_quantiles
        Optional per-risk-factor quantile values for RR interpolation.
    tmrel_path
        Path to derived TMREL CSV; required when rr_quantiles is provided.
    """
    risk_breakpoints = pd.read_csv(risk_breakpoints_path)
    cluster_cause_df = pd.read_csv(cluster_cause_path)
    cause_log_breakpoints = pd.read_csv(cause_log_path)
    cluster_map = pd.read_csv(clusters_path)

    # Sort breakpoint tables (risk breakpoints are cluster-specific)
    risk_breakpoints = risk_breakpoints.sort_values(
        ["health_cluster", "risk_factor", "intake_g_per_day", "cause"]
    )
    cause_log_breakpoints = cause_log_breakpoints.sort_values(["cause", "log_rr_total"])

    cluster_lookup = cluster_map.set_index("country_iso3")["health_cluster"].to_dict()
    cluster_population = get_health_cluster_population(n)

    cluster_cause_metadata = cluster_cause_df.set_index(["health_cluster", "cause"])

    # Filter risk_cause_map to available risk factors
    available_risks = set(risk_breakpoints["risk_factor"].unique())
    risk_cause_map = {
        r: causes for r, causes in risk_cause_map.items() if r in available_risks
    }

    # Apply RR quantile interpolation if requested
    if rr_quantiles:
        if tmrel_path is None:
            raise ValueError("tmrel_path is required when rr_quantiles is provided")
        risk_breakpoints = _apply_rr_quantiles(risk_breakpoints, rr_quantiles)
        tmrel_df = pd.read_csv(tmrel_path)
        tmrel = dict(
            zip(tmrel_df["risk_factor"], tmrel_df["tmrel_g_per_day"].astype(float))
        )
        cluster_cause_metadata = _recompute_rr_ref(
            risk_breakpoints, tmrel, risk_cause_map, cluster_cause_metadata
        )

    # --- Step 1: Get per-capita intake by (cluster, risk_factor) ---
    fg_stores = n.stores.static[n.stores.static["food_group"].isin(risk_factors)]
    snapshot = "now" if "now" in n.snapshots else n.snapshots[-1]
    store_levels = n.stores.dynamic.e.loc[snapshot]

    # Aggregate store levels to cluster-level intake (g/person/day)
    cluster_intake: dict[tuple[int, str], float] = {}
    for risk in risk_factors:
        risk_stores = fg_stores[fg_stores["food_group"] == risk]
        for cluster in sorted(cluster_population):
            # Sum store levels across countries in this cluster
            countries_in_cluster = [
                c for c, cl in cluster_lookup.items() if cl == cluster
            ]
            mask = risk_stores["country"].isin(countries_in_cluster)
            total_mt = float(store_levels[risk_stores.index[mask]].sum())
            pop = cluster_population[cluster]
            intake_g = total_mt * constants.GRAMS_PER_MEGATONNE / (365.0 * pop)
            cluster_intake[(cluster, risk)] = intake_g

    # --- Step 2: Evaluate log(RR) per (cluster, risk, cause) ---
    # Build breakpoint lookup: {(cluster, risk, cause): (intake_array, log_rr_array)}
    rr_bp: dict[tuple[int, str, str], tuple[np.ndarray, np.ndarray]] = {}
    for (cluster, risk, cause), grp in risk_breakpoints.groupby(
        ["health_cluster", "risk_factor", "cause"]
    ):
        sorted_grp = grp.sort_values("intake_g_per_day")
        rr_bp[(int(cluster), risk, cause)] = (
            sorted_grp["intake_g_per_day"].values,
            sorted_grp["log_rr"].values,
        )

    # --- Step 3: Sum log(RR) across risk factors per (cluster, cause) ---
    log_rr_total: dict[tuple[int, str], float] = {}
    for (cluster, cause), _row in cluster_cause_metadata.iterrows():
        cluster = int(cluster)
        cause = str(cause)
        total = 0.0
        for risk, causes in risk_cause_map.items():
            if cause not in causes:
                continue
            intake = cluster_intake.get((cluster, risk), 0.0)
            bp = rr_bp.get((cluster, risk, cause))
            if bp is None:
                continue
            total += float(np.interp(intake, bp[0], bp[1]))
        log_rr_total[(cluster, cause)] = total

    # --- Step 4: Interpolate RR from total log(RR) using cause breakpoints ---
    cause_bp: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for cause, grp in cause_log_breakpoints.groupby("cause"):
        sorted_grp = grp.sort_values("log_rr_total")
        cause_bp[str(cause)] = (
            sorted_grp["log_rr_total"].values,
            sorted_grp["rr_total"].values,
        )

    # --- Step 5: Compute YLL and write to stores ---
    yll_stores = n.stores.static[
        n.stores.static["carrier"].notna()
        & n.stores.static["carrier"].str.startswith("yll_")
    ]

    n_filled = 0
    for store_name, store_row in yll_stores.iterrows():
        cluster = int(store_row["health_cluster"])
        cause = str(store_row["cause"])

        if (cluster, cause) not in cluster_cause_metadata.index:
            continue

        meta = cluster_cause_metadata.loc[(cluster, cause)]
        log_rr = log_rr_total.get((cluster, cause), 0.0)

        bp = cause_bp.get(cause)
        if bp is None:
            continue
        rr = float(np.interp(log_rr, bp[0], bp[1]))

        # Reference RR at TMREL
        rr_ref = math.exp(float(meta["log_rr_total_ref"]))

        # Baseline RR for normalisation
        rr_baseline = math.exp(float(meta["log_rr_total_baseline"]))

        # Absolute YLL from rate
        yll_rate_per_100k = float(meta["yll_rate_per_100k"])
        pop = cluster_population[cluster]
        yll_total = (yll_rate_per_100k / constants.PER_100K) * pop

        # YLL in million YLL
        yll_myll = (
            (rr - rr_ref) * (yll_total / rr_baseline) * constants.YLL_TO_MILLION_YLL
        )

        n.stores.dynamic.e.loc[snapshot, store_name] = yll_myll
        n_filled += 1

    logger.info(
        "Post-hoc health evaluation: filled %d YLL stores across %d clusters",
        n_filled,
        len(cluster_population),
    )
