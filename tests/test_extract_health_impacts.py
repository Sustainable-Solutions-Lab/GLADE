# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for ``workflow.scripts.analysis.extract_health_impacts``.

These tests pin down three invariants that previously failed silently:

1. Intake fed into the dose-response chain must come from the post-waste
   food-group *store level*, not the pre-waste *food-bus withdrawal*. Earlier
   versions of the analysis used ``food_group_consumption.parquet`` for
   intake, which is the link-level withdrawal at the food bus -- i.e. the
   retail-supply (pre-waste) mass. The LP applies the consumer waste
   multiplier on the food_consumption link's group leg, so the store level
   is lower. Using the wrong basis silently inflated intake by 10-30%.

2. Dose-response curves must be keyed per ``(health_cluster, risk_factor)``.
   ``risk_breakpoints.csv`` carries a ``health_cluster`` column because
   age-weighted effective RR differs across clusters. A bug pooled curves
   across clusters via ``pivot_table(aggfunc='first')``, silently keeping
   one cluster's curve everywhere.

3. ``RR_d(log_total)`` must be evaluated via the chord PWL of exp() through
   ``cause_log_breakpoints``, matching ``_add_stage2_lp_tangent``. Using
   ``exp(log_total)`` directly is the analytic value of a different
   function -- the chord upper-bounds it everywhere except at breakpoints.

The strategy is a round-trip: build a tiny network and health-data set,
populate the YLL stores via ``evaluate_health_posthoc`` (the LP's reference
post-hoc evaluator), then check that the analysis-side reconstruction sums
back to the same totals per cluster.
"""

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from workflow.scripts.analysis.extract_health_impacts import (
    HealthData,
    compute_health_attribution,
    compute_health_marginals,
    compute_store_intake_by_cluster_risk,
    extract_yll_totals,
)
from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE, PER_100K
from workflow.scripts.solve_model.health import evaluate_health_posthoc

# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------
#
# The fixture builds two clusters with different dose-response shapes for the
# same risk factor (red_meat), and a chord PWL with rr_total values that
# deliberately depart from exp(log_rr_total). Concrete intake values are
# chosen so log_total lands strictly inside a chord segment for both
# clusters, making bug 3 detectable.

# Single risk factor and cause keeps the test focused; the attribution
# function's allocation is trivial in this case but the chord, intake-basis,
# and cluster-keying logic still all run.
RISK_FACTOR = "red_meat"
CAUSE = "CHD"
TMREL = 0.0  # g/day; intakes are well above so the "harmful" branch fires
POP_C1 = 1_000_000_000.0  # 1 billion
POP_C2 = 500_000_000.0  # 500 million

# Intake per cluster (post-waste, store-level): chosen so g/day lands at
# 60 (cluster 1) and 80 (cluster 2). With Mt = g/day * 365 * pop / 1e12:
INTAKE_G_C1 = 60.0
INTAKE_G_C2 = 80.0
STORE_LEVEL_C1 = INTAKE_G_C1 * DAYS_PER_YEAR * POP_C1 / GRAMS_PER_MEGATONNE
STORE_LEVEL_C2 = INTAKE_G_C2 * DAYS_PER_YEAR * POP_C2 / GRAMS_PER_MEGATONNE

# Per-cluster dose-response: deliberately different slopes. Cluster 1 sees
# log_rr = 0.005 * (intake - TMREL); cluster 2 sees 0.010 * (intake - TMREL).
# Same intake would give very different log_rr if curves were correctly keyed.
RR_BREAKPOINTS_G = np.array([0.0, 50.0, 100.0, 150.0])
LOG_RR_C1 = 0.005 * RR_BREAKPOINTS_G  # at 60: 0.30
LOG_RR_C2 = 0.010 * RR_BREAKPOINTS_G  # at 80: 0.80


def _interp(x, xs, ys):
    return float(np.interp(x, xs, ys))


# Chord PWL for the cause. The "honest" exp curve would put rr = exp(log).
# We deliberately give rr values that are NOT exp(log), so the chord
# differs from exp() inside every segment. This makes bug 3 detectable.
CAUSE_LOG_PTS = np.array([0.0, 0.5, 1.0, 1.5])
CAUSE_RR_PTS = np.array([1.0, 2.0, 4.0, 8.0])  # convex, not exp(log_pts)


def _make_network() -> pypsa.Network:
    """Minimal network with one food-group store per cluster and one YLL store.

    Two countries (one per cluster) so that aggregation across countries is
    also exercised lightly. ``stores.dynamic.e`` is initialised explicitly so
    callers can read store levels without running a solver.
    """
    n = pypsa.Network()
    n.set_snapshots(["now"])

    n.carriers.add(f"group_{RISK_FACTOR}", unit="Mt")
    n.carriers.add(f"yll_{CAUSE}", unit="million YLL")

    n.buses.add(
        [
            f"group:{RISK_FACTOR}:USA",
            f"group:{RISK_FACTOR}:CAN",
            "health:cluster:001",
            "health:cluster:002",
        ],
        carrier=[
            f"group_{RISK_FACTOR}",
            f"group_{RISK_FACTOR}",
            "health",
            "health",
        ],
    )

    fg_store_names = [
        f"store:group:{RISK_FACTOR}:USA",
        f"store:group:{RISK_FACTOR}:CAN",
    ]
    n.stores.add(
        fg_store_names,
        bus=[f"group:{RISK_FACTOR}:USA", f"group:{RISK_FACTOR}:CAN"],
        carrier=f"group_{RISK_FACTOR}",
        country=["USA", "CAN"],
        food_group=[RISK_FACTOR, RISK_FACTOR],
    )

    yll_store_names = [
        f"store:yll:{CAUSE}:cluster001",
        f"store:yll:{CAUSE}:cluster002",
    ]
    n.stores.add(
        yll_store_names,
        bus=["health:cluster:001", "health:cluster:002"],
        carrier=f"yll_{CAUSE}",
        health_cluster=[1, 2],
        cause=[CAUSE, CAUSE],
        yll_rate_per_100k=[200.0, 300.0],
        yll_attrib_rate_per_100k=[40.0, 60.0],
        rr_ref=[1.0, 1.0],
    )

    # Set food-group store levels (post-waste, intake-basis) for the
    # final snapshot.
    n.stores.dynamic.e = pd.DataFrame(
        {
            fg_store_names[0]: [STORE_LEVEL_C1],
            fg_store_names[1]: [STORE_LEVEL_C2],
            yll_store_names[0]: [0.0],
            yll_store_names[1]: [0.0],
        },
        index=n.snapshots,
    )

    # Population metadata; evaluate_health_posthoc reads cluster pop from here.
    n.meta = {
        "population": {
            "country": {"USA": POP_C1, "CAN": POP_C2},
            "health_cluster": {1: POP_C1, 2: POP_C2},
        }
    }

    return n


def _write_csvs(tmp_path):
    """Write the five CSVs that the health pipeline expects."""
    rb_rows = []
    for cluster, log_rr in [(1, LOG_RR_C1), (2, LOG_RR_C2)]:
        for x, y in zip(RR_BREAKPOINTS_G, log_rr):
            rb_rows.append(
                {
                    "health_cluster": cluster,
                    "risk_factor": RISK_FACTOR,
                    "intake_g_per_day": float(x),
                    "cause": CAUSE,
                    "log_rr": float(y),
                }
            )
    risk_bp = pd.DataFrame(rb_rows)
    risk_bp_path = tmp_path / "risk_breakpoints.csv"
    risk_bp.to_csv(risk_bp_path, index=False)

    # cluster_cause: log_rr_total_ref/baseline pinned to log(1)=0 so the
    # chord lookup at log_total > 0 produces a clean above-reference
    # increment. yll_rate_per_100k differs per cluster to confirm
    # cluster-specific YLL totals propagate end-to-end.
    cluster_cause = pd.DataFrame(
        [
            {
                "health_cluster": 1,
                "cause": CAUSE,
                "yll_rate_per_100k": 200.0,
                "yll_attrib_rate_per_100k": 40.0,
                "log_rr_total_ref": 0.0,
                "log_rr_total_baseline": 0.0,
            },
            {
                "health_cluster": 2,
                "cause": CAUSE,
                "yll_rate_per_100k": 300.0,
                "yll_attrib_rate_per_100k": 60.0,
                "log_rr_total_ref": 0.0,
                "log_rr_total_baseline": 0.0,
            },
        ]
    )
    cluster_cause_path = tmp_path / "cluster_cause.csv"
    cluster_cause.to_csv(cluster_cause_path, index=False)

    cause_log = pd.DataFrame(
        {
            "cause": [CAUSE] * len(CAUSE_LOG_PTS),
            "log_rr_total": CAUSE_LOG_PTS,
            "rr_total": CAUSE_RR_PTS,
        }
    )
    cause_log_path = tmp_path / "cause_log.csv"
    cause_log.to_csv(cause_log_path, index=False)

    clusters = pd.DataFrame({"country_iso3": ["USA", "CAN"], "health_cluster": [1, 2]})
    clusters_path = tmp_path / "clusters.csv"
    clusters.to_csv(clusters_path, index=False)

    population = pd.DataFrame({"iso3": ["USA", "CAN"], "population": [POP_C1, POP_C2]})
    population_path = tmp_path / "population.csv"
    population.to_csv(population_path, index=False)

    tmrel = pd.DataFrame({"risk_factor": [RISK_FACTOR], "tmrel_g_per_day": [TMREL]})
    tmrel_path = tmp_path / "tmrel.csv"
    tmrel.to_csv(tmrel_path, index=False)

    # cluster_risk_baseline -- only consumed by evaluate_health_posthoc
    # to anchor log_rr_total_baseline under quantile shifts. A zero
    # baseline intake reproduces the pre-shift behaviour for these
    # tests (which do not exercise rr_quantiles).
    crb = pd.DataFrame(
        {
            "health_cluster": [1, 2],
            "risk_factor": [RISK_FACTOR, RISK_FACTOR],
            "baseline_intake_g_per_day": [0.0, 0.0],
        }
    )
    crb_path = tmp_path / "cluster_risk_baseline.csv"
    crb.to_csv(crb_path, index=False)

    # cluster_summary not used by the analysis code path, but
    # evaluate_health_posthoc indirectly needs nothing from it; we still
    # return its path for symmetry with the production interface.
    return {
        "risk_breakpoints": str(risk_bp_path),
        "cluster_cause": str(cluster_cause_path),
        "cause_log": str(cause_log_path),
        "clusters": str(clusters_path),
        "population": str(population_path),
        "tmrel": str(tmrel_path),
        "cluster_risk_baseline": str(crb_path),
    }


def _load_health_data(paths) -> HealthData:
    return HealthData(
        risk_breakpoints=pd.read_csv(paths["risk_breakpoints"]),
        cluster_cause=pd.read_csv(paths["cluster_cause"]),
        cause_log_breakpoints=pd.read_csv(paths["cause_log"]),
        country_clusters=pd.read_csv(paths["clusters"]),
        population=pd.read_csv(paths["population"]),
        tmrel=pd.read_csv(paths["tmrel"]),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compute_store_intake_uses_post_waste_store_level():
    """Bug 1: intake must come from the food-group store, not food-bus flow.

    The fixture sets the store level directly; if the implementation read the
    food-bus withdrawal instead it would be zero (no consume links exist).
    The expected value is the analytical conversion of the chosen store
    levels to g/capita/day.
    """
    n = _make_network()
    cluster_lookup = {"USA": 1, "CAN": 2}
    cluster_pop = {1: POP_C1, 2: POP_C2}

    intake = compute_store_intake_by_cluster_risk(
        n, [RISK_FACTOR], cluster_lookup, cluster_pop
    )

    assert intake[(1, RISK_FACTOR)] == pytest.approx(INTAKE_G_C1, rel=1e-12)
    assert intake[(2, RISK_FACTOR)] == pytest.approx(INTAKE_G_C2, rel=1e-12)


def test_attribution_sums_to_store_levels(tmp_path):
    """The per-cluster sum of attributed YLL must equal the YLL store level.

    This invariant catches all three bugs simultaneously: any of (wrong
    intake basis, wrong cluster curve, exp() instead of chord) makes the
    reconstructed yll_myll diverge from the value evaluate_health_posthoc
    wrote into the store.
    """
    paths = _write_csvs(tmp_path)
    n = _make_network()

    evaluate_health_posthoc(
        n,
        risk_breakpoints_path=paths["risk_breakpoints"],
        cluster_cause_path=paths["cluster_cause"],
        cause_log_path=paths["cause_log"],
        clusters_path=paths["clusters"],
        cluster_risk_baseline_path=paths["cluster_risk_baseline"],
        risk_factors=[RISK_FACTOR],
        risk_cause_map={RISK_FACTOR: [CAUSE]},
    )

    totals = extract_yll_totals(n).set_index("health_cluster")["yll_myll"]
    # Sanity: the YLL stores should have been populated to non-trivial values
    # (intakes are well above TMREL so the harmful branch fires).
    assert (totals > 0).all()

    health_data = _load_health_data(paths)
    attribution = compute_health_attribution(n, health_data, [RISK_FACTOR])
    attrib_per_cluster = attribution.groupby("health_cluster")["yll_myll"].sum()

    for cluster in totals.index:
        assert attrib_per_cluster.loc[cluster] == pytest.approx(
            totals.loc[cluster], rel=1e-9, abs=1e-12
        )


def test_attribution_uses_cluster_specific_dose_response(tmp_path):
    """Bug 2: with the SAME intake in two clusters, different curves must
    yield different attributed YLL.

    We override the network's store levels so both clusters see the same
    per-capita intake (g/day), then check that cluster 2 (steeper curve)
    gets a larger log_rr -- and therefore a larger ``yll_myll`` per unit
    of ``yll_total`` -- than cluster 1.
    """
    paths = _write_csvs(tmp_path)
    n = _make_network()

    # Force both clusters to the SAME g/day intake by adjusting store levels.
    same_intake_g = 80.0
    new_levels = {
        f"store:group:{RISK_FACTOR}:USA": (
            same_intake_g * DAYS_PER_YEAR * POP_C1 / GRAMS_PER_MEGATONNE
        ),
        f"store:group:{RISK_FACTOR}:CAN": (
            same_intake_g * DAYS_PER_YEAR * POP_C2 / GRAMS_PER_MEGATONNE
        ),
    }
    for name, val in new_levels.items():
        n.stores.dynamic.e.loc[n.snapshots[-1], name] = val

    evaluate_health_posthoc(
        n,
        risk_breakpoints_path=paths["risk_breakpoints"],
        cluster_cause_path=paths["cluster_cause"],
        cause_log_path=paths["cause_log"],
        clusters_path=paths["clusters"],
        cluster_risk_baseline_path=paths["cluster_risk_baseline"],
        risk_factors=[RISK_FACTOR],
        risk_cause_map={RISK_FACTOR: [CAUSE]},
    )

    health_data = _load_health_data(paths)
    attribution = compute_health_attribution(n, health_data, [RISK_FACTOR])

    # Expected log_rr at same_intake_g per cluster (cluster-specific curves):
    log_rr_c1 = _interp(same_intake_g, RR_BREAKPOINTS_G, LOG_RR_C1)
    log_rr_c2 = _interp(same_intake_g, RR_BREAKPOINTS_G, LOG_RR_C2)
    assert log_rr_c2 > log_rr_c1  # sanity for the test setup

    # Expected yll_myll per cluster (only one risk -> single record per cluster).
    def expected_yll(log_rr_total: float, yll_rate: float, pop: float) -> float:
        rr = _interp(log_rr_total, CAUSE_LOG_PTS, CAUSE_RR_PTS)
        yll_total = (yll_rate / PER_100K) * pop
        return (rr - math.exp(0.0)) * (yll_total / math.exp(0.0)) * 1e-6

    expected_c1 = expected_yll(log_rr_c1, 200.0, POP_C1)
    expected_c2 = expected_yll(log_rr_c2, 300.0, POP_C2)

    by_cluster = attribution.set_index("health_cluster")["yll_myll"]
    assert by_cluster.loc[1] == pytest.approx(expected_c1, rel=1e-9)
    assert by_cluster.loc[2] == pytest.approx(expected_c2, rel=1e-9)
    # If the buggy version pooled curves, both clusters would see the same
    # log_rr -- this would still differ between clusters because yll_rate
    # and population differ, but log_rr would be identical. The strongest
    # discriminator is comparing per-capita: ratio of yll_myll / yll_total
    # must differ across clusters (because the dose-response differs).
    yll_total_c1 = (200.0 / PER_100K) * POP_C1
    yll_total_c2 = (300.0 / PER_100K) * POP_C2
    ratio_c1 = by_cluster.loc[1] / yll_total_c1
    ratio_c2 = by_cluster.loc[2] / yll_total_c2
    assert ratio_c2 > ratio_c1 * 1.5  # cluster 2's steeper curve dominates


def test_attribution_uses_chord_not_exp(tmp_path):
    """Bug 3: rr_total must come from chord PWL, not exp(log_total).

    The chord ``[1.0, 2.0, 4.0, 8.0]`` over ``[0.0, 0.5, 1.0, 1.5]`` is
    convex and tracks an exp-like shape, but at log_total in (0, 0.5)
    the chord value is ``1.0 + 2.0 * log_total``, which differs from
    exp(log_total) by ~5-10%. We pick an intake that places log_total
    inside that segment and confirm the analysis uses the chord value.
    """
    paths = _write_csvs(tmp_path)
    n = _make_network()

    # Cluster 1 intake of 60 g/day -> log_rr = 0.30 (inside segment 0).
    # Chord at 0.30: 1.0 + 2.0 * 0.30 = 1.6  vs exp(0.30) = 1.34986.
    log_rr_c1 = 0.30
    chord_value = 1.0 + 2.0 * log_rr_c1
    exp_value = math.exp(log_rr_c1)
    assert abs(chord_value - exp_value) > 0.2  # safety: clearly distinguishable

    evaluate_health_posthoc(
        n,
        risk_breakpoints_path=paths["risk_breakpoints"],
        cluster_cause_path=paths["cluster_cause"],
        cause_log_path=paths["cause_log"],
        clusters_path=paths["clusters"],
        cluster_risk_baseline_path=paths["cluster_risk_baseline"],
        risk_factors=[RISK_FACTOR],
        risk_cause_map={RISK_FACTOR: [CAUSE]},
    )

    health_data = _load_health_data(paths)
    attribution = compute_health_attribution(n, health_data, [RISK_FACTOR])

    yll_total_c1 = (200.0 / PER_100K) * POP_C1
    expected_chord = (chord_value - 1.0) * yll_total_c1 * 1e-6
    expected_exp = (exp_value - 1.0) * yll_total_c1 * 1e-6

    actual_c1 = attribution.set_index("health_cluster").loc[1, "yll_myll"]
    assert actual_c1 == pytest.approx(expected_chord, rel=1e-9)
    assert abs(actual_c1 - expected_exp) > 0.5 * abs(
        expected_chord - expected_exp
    )  # actual matches chord, not exp


def test_marginals_use_chord_slope(tmp_path):
    """``compute_health_marginals`` must use the chord-PWL slope, not exp().

    At log_total in segment 0 the chord slope is constant
    ``(2.0 - 1.0) / (0.5 - 0.0) = 2.0``. exp() would give a slope of
    exp(log_total). With cluster 1 at log_rr = 0.30, exp = 1.35, the two
    differ by ~33%. We compute the expected ``yll_per_mt`` analytically and
    verify it matches the chord-slope formulation.
    """
    paths = _write_csvs(tmp_path)
    n = _make_network()
    health_data = _load_health_data(paths)

    marginals = compute_health_marginals(n, health_data, [RISK_FACTOR])

    # Expected for cluster 1, USA:
    # yll_total = 200/100k * POP_C1 = 2_000_000
    # rr_baseline = 1; chord slope at log_rr=0.30 = 2.0;
    # d(log_rr)/d(intake) on segment [0,50] is (LOG_RR_C1[1] - LOG_RR_C1[0]) / 50
    #   = 0.25 / 50 = 0.005
    # marginal (YLL per g/day) = yll_total * chord_slope * 0.005 = 20_000
    # 1 Mt -> intake delta = 1e12 / (365 * POP_C1) g/day
    # yll_per_mt = 20_000 * 1e12 / (365 * POP_C1)
    chord_slope = (CAUSE_RR_PTS[1] - CAUSE_RR_PTS[0]) / (
        CAUSE_LOG_PTS[1] - CAUSE_LOG_PTS[0]
    )
    d_log_rr_per_g = (LOG_RR_C1[1] - LOG_RR_C1[0]) / (
        RR_BREAKPOINTS_G[1] - RR_BREAKPOINTS_G[0]
    )
    yll_total = (200.0 / PER_100K) * POP_C1
    marg_per_g = yll_total * chord_slope * d_log_rr_per_g
    intake_per_mt = GRAMS_PER_MEGATONNE / (DAYS_PER_YEAR * POP_C1)
    expected_yll_per_mt = marg_per_g * intake_per_mt

    actual = marginals[
        (marginals["country"] == "USA") & (marginals["food_group"] == RISK_FACTOR)
    ]["yll_per_mt"].iloc[0]
    assert actual == pytest.approx(expected_yll_per_mt, rel=1e-9)

    # Sanity: this also differs significantly from the exp-based value the
    # buggy code would have produced.
    buggy_marg_per_g = yll_total * math.exp(0.30) * d_log_rr_per_g
    buggy_yll_per_mt = buggy_marg_per_g * intake_per_mt
    assert abs(actual - buggy_yll_per_mt) > 0.05 * abs(actual)
