# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for health cost preparation functions."""

import math

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.prepare_health_costs import (
    ADULT_AGES,
    AgeWeights,
    RelativeRiskTable,
    _age_bucket_min,
    _build_intake_caps,
    _build_rr_tables,
    _derive_tmrel_from_rr,
    _evaluate_log_rr_age_weighted,
    _evaluate_rr,
)
from workflow.scripts.prepare_relative_risks import ADULT_AGE_LABELS

# ---------------------------------------------------------------------------
# Tests: _age_bucket_min
# ---------------------------------------------------------------------------


class TestAgeBucketMin:
    """Tests for parsing age bucket labels into lower bounds."""

    def test_less_than_one(self):
        assert _age_bucket_min("<1") == 0

    def test_range_one_to_four(self):
        assert _age_bucket_min("1-4") == 1

    def test_range_twenty_five_to_twenty_nine(self):
        assert _age_bucket_min("25-29") == 25

    def test_ninety_five_plus(self):
        assert _age_bucket_min("95+") == 95

    def test_all_ages_treated_as_adult(self):
        """GDD-IA emits 'All ages' for its adult-equivalent rows; the bucket
        minimum must be high enough to pass the intake_age_min filter."""
        assert _age_bucket_min("All ages") >= 18
        assert _age_bucket_min("all-a") >= 18

    def test_unknown_label_raises(self):
        """Unrecognised age labels must surface as errors rather than
        silently falling through to a zero bucket (which silently dropped
        every diet observation past the intake_age_min filter)."""
        with pytest.raises(ValueError, match="Unrecognised age bucket label"):
            _age_bucket_min("0")


# ---------------------------------------------------------------------------
# Tests: Age bucket alignment
# ---------------------------------------------------------------------------


class TestAgeBucketAlignment:
    """Verify age bucket labels are consistent across the workflow."""

    def test_rr_ages_match_health_costs_ages(self):
        """ADULT_AGE_LABELS from prepare_relative_risks must match ADULT_AGES
        from prepare_health_costs."""
        assert ADULT_AGE_LABELS == ADULT_AGES

    def test_adult_ages_count(self):
        """There should be exactly 15 adult age groups (25-29 through 95+)."""
        assert len(ADULT_AGES) == 15

    def test_adult_ages_start_at_25(self):
        """First adult age group should be 25-29."""
        assert ADULT_AGES[0] == "25-29"

    def test_adult_ages_end_at_95_plus(self):
        """Last adult age group should be 95+."""
        assert ADULT_AGES[-1] == "95+"


# ---------------------------------------------------------------------------
# Helpers for RR tests
# ---------------------------------------------------------------------------


def _make_simple_rr_table():
    """Build a minimal RelativeRiskTable for 'fruits' and 'CHD'.

    Exposures: [0, 100, 200]
    RR values: [1.5, 1.0, 0.8]
    Same values across all age groups (age-constant).
    """
    table = RelativeRiskTable()
    rr_values = np.array([1.5, 1.0, 0.8])
    for age in ADULT_AGES:
        table[("fruits", "CHD", age)] = {
            "exposures": np.array([0.0, 100.0, 200.0]),
            "log_rr_mean": np.log(rr_values),
            "log_rr_low": np.log(rr_values),
            "log_rr_high": np.log(rr_values),
        }
    return table


def _make_age_varying_rr_table():
    """Build an age-varying RR table where younger ages have stronger effects.

    Exposures: [0, 100, 200]
    - Ages 25-29: RR = [1.5, 1.0, 0.7] (strong protective)
    - Ages 75+:   RR = [1.5, 1.0, 0.9] (attenuated protective)
    - Other ages: linear interpolation between the two extremes
    """
    table = RelativeRiskTable()
    rr_at_200_young = 0.7
    rr_at_200_old = 0.9

    for i, age in enumerate(ADULT_AGES):
        # Linear interpolation of RR at 200 g/day from young to old
        frac = i / (len(ADULT_AGES) - 1)
        rr_200 = rr_at_200_young + frac * (rr_at_200_old - rr_at_200_young)
        rr_values = np.array([1.5, 1.0, rr_200])
        table[("fruits", "CHD", age)] = {
            "exposures": np.array([0.0, 100.0, 200.0]),
            "log_rr_mean": np.log(rr_values),
            "log_rr_low": np.log(rr_values),
            "log_rr_high": np.log(rr_values),
        }

    return table


# ---------------------------------------------------------------------------
# Tests: _evaluate_rr
# ---------------------------------------------------------------------------


class TestEvaluateRR:
    """Tests for log-linear relative risk interpolation."""

    def test_exact_knot(self):
        """Intake at an exact exposure knot returns the corresponding RR."""
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", "25-29", 100.0)
        assert rr == pytest.approx(1.0)

    def test_below_minimum_clamps(self):
        """Intake below the lowest exposure clamps to the first RR value."""
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", "25-29", -10.0)
        assert rr == pytest.approx(1.5)

    def test_above_maximum_clamps(self):
        """Intake above the highest exposure clamps to the last RR value."""
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", "25-29", 300.0)
        assert rr == pytest.approx(0.8)

    def test_interpolation_between_knots(self):
        """Intake between knots is log-linearly interpolated."""
        table = _make_simple_rr_table()
        intake = 50.0
        rr = _evaluate_rr(table, "fruits", "CHD", "25-29", intake)

        # Manual computation: interp in log space
        exposures = np.array([0.0, 100.0, 200.0])
        log_rr_vals = np.log(np.array([1.5, 1.0, 0.8]))
        expected_log = float(np.interp(intake, exposures, log_rr_vals))
        expected_rr = math.exp(expected_log)
        assert rr == pytest.approx(expected_rr)

    def test_interpolation_formula(self):
        """Verify the log-linear interpolation formula explicitly.

        At intake=50 (midpoint of [0, 100]):
            log_rr = 0.5 * log(1.5) + 0.5 * log(1.0) = 0.5 * log(1.5)
            rr = exp(0.5 * log(1.5)) = 1.5^0.5
        """
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", "25-29", 50.0)
        assert rr == pytest.approx(math.sqrt(1.5))


# ---------------------------------------------------------------------------
# Tests: _evaluate_log_rr_age_weighted
# ---------------------------------------------------------------------------


class TestEvaluateLogRRAgeWeighted:
    """Tests for YLL-weighted effective log(RR) computation."""

    def test_age_constant_returns_log(self):
        """When all ages have identical RR, age-weighting returns log(RR)."""
        table = _make_simple_rr_table()
        weights: AgeWeights = {
            (0, "CHD", age): 1.0 / len(ADULT_AGES) for age in ADULT_AGES
        }
        log_rr_eff = _evaluate_log_rr_age_weighted(
            table, "fruits", "CHD", 200.0, weights, cluster_id=0
        )
        assert log_rr_eff == pytest.approx(math.log(0.8))

    def test_age_varying_old_weighted(self):
        """All weight on oldest -> log(RR_oldest)."""
        table = _make_age_varying_rr_table()
        weights: AgeWeights = {(0, "CHD", age): 0.0 for age in ADULT_AGES}
        weights[(0, "CHD", "95+")] = 1.0

        log_rr_eff = _evaluate_log_rr_age_weighted(
            table, "fruits", "CHD", 200.0, weights, cluster_id=0
        )
        assert log_rr_eff == pytest.approx(math.log(0.9))

    def test_age_varying_young_weighted(self):
        """All weight on youngest -> log(RR_youngest)."""
        table = _make_age_varying_rr_table()
        weights: AgeWeights = {(0, "CHD", age): 0.0 for age in ADULT_AGES}
        weights[(0, "CHD", "25-29")] = 1.0

        log_rr_eff = _evaluate_log_rr_age_weighted(
            table, "fruits", "CHD", 200.0, weights, cluster_id=0
        )
        assert log_rr_eff == pytest.approx(math.log(0.7))

    def test_age_varying_mixed_weights_is_log_geometric_mean(self):
        """50/50 split: log_rr_eff = 0.5*log(0.7) + 0.5*log(0.9)
        = log(sqrt(0.7*0.9)) (geometric mean in log space)."""
        table = _make_age_varying_rr_table()
        weights: AgeWeights = {(0, "CHD", age): 0.0 for age in ADULT_AGES}
        weights[(0, "CHD", "25-29")] = 0.5
        weights[(0, "CHD", "95+")] = 0.5

        log_rr_eff = _evaluate_log_rr_age_weighted(
            table, "fruits", "CHD", 200.0, weights, cluster_id=0
        )
        expected = 0.5 * math.log(0.7) + 0.5 * math.log(0.9)
        assert log_rr_eff == pytest.approx(expected)
        # And distinct from the (incorrect) arithmetic-mean approach:
        assert log_rr_eff != pytest.approx(math.log(0.8))


# ---------------------------------------------------------------------------
# Tests: _derive_tmrel_from_rr
# ---------------------------------------------------------------------------


class TestDeriveTmrelFromRR:
    """Tests for TMREL derivation from RR curves."""

    def test_tmrel_at_known_minimum(self):
        """TMREL is the exposure where the product of RRs is minimised.

        Both causes have their minimum RR at exposure=200, so TMREL should
        be 200.
        """
        table = RelativeRiskTable()

        # CHD: RR decreases from 1.5 to 0.6 (minimum at 200)
        rr_chd = np.array([1.5, 1.0, 0.6])
        for age in ADULT_AGES:
            table[("fruits", "CHD", age)] = {
                "exposures": np.array([0.0, 100.0, 200.0]),
                "log_rr_mean": np.log(rr_chd),
                "log_rr_low": np.log(rr_chd),
                "log_rr_high": np.log(rr_chd),
            }

        # T2DM: RR decreases from 1.3 to 0.7 (minimum at 200)
        rr_t2dm = np.array([1.3, 0.9, 0.7])
        for age in ADULT_AGES:
            table[("fruits", "T2DM", age)] = {
                "exposures": np.array([0.0, 100.0, 200.0]),
                "log_rr_mean": np.log(rr_t2dm),
                "log_rr_low": np.log(rr_t2dm),
                "log_rr_high": np.log(rr_t2dm),
            }

        risk_to_causes = {"fruits": ["CHD", "T2DM"]}
        tmrel = _derive_tmrel_from_rr(table, risk_to_causes)

        assert tmrel["fruits"] == pytest.approx(200.0)

    def test_tmrel_with_single_cause(self):
        """With a single cause the TMREL is at the minimum RR exposure."""
        table = RelativeRiskTable()
        rr_vals = np.array([1.2, 0.8, 1.0])
        for age in ADULT_AGES:
            table[("sugar", "T2DM", age)] = {
                "exposures": np.array([0.0, 50.0, 100.0]),
                "log_rr_mean": np.log(rr_vals),
                "log_rr_low": np.log(rr_vals),
                "log_rr_high": np.log(rr_vals),
            }
        risk_to_causes = {"sugar": ["T2DM"]}
        tmrel = _derive_tmrel_from_rr(table, risk_to_causes)
        # Minimum RR is 0.8 at exposure=50
        assert tmrel["sugar"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tests: _build_rr_tables
# ---------------------------------------------------------------------------


def _make_rr_df_with_ages():
    """A minimal relative risk DataFrame with age column."""
    rows = []
    for age in ADULT_AGES:
        for exposure, rr_mean in [(0.0, 1.5), (100.0, 1.0), (200.0, 0.8)]:
            rows.append(
                {
                    "risk_factor": "fruits",
                    "cause": "CHD",
                    "age": age,
                    "exposure_g_per_day": exposure,
                    "rr_mean": rr_mean,
                    "rr_low": rr_mean * 0.9,
                    "rr_high": rr_mean * 1.1,
                }
            )
        for exposure, rr_mean in [(0.0, 1.3), (150.0, 0.9)]:
            rows.append(
                {
                    "risk_factor": "fruits",
                    "cause": "T2DM",
                    "age": age,
                    "exposure_g_per_day": exposure,
                    "rr_mean": rr_mean,
                    "rr_low": rr_mean * 0.9,
                    "rr_high": rr_mean * 1.1,
                }
            )
    return pd.DataFrame(rows)


class TestBuildRRTables:
    """Tests for constructing RR lookup tables from a DataFrame."""

    @pytest.fixture
    def small_rr_df(self):
        return _make_rr_df_with_ages()

    def test_correct_number_of_pairs(self, small_rr_df):
        """Output table has entries for all (risk, cause, age) triples."""
        risk_cause_map = {"fruits": ["CHD", "T2DM"]}
        table, _ = _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)
        # 2 causes x 15 ages = 30 entries
        assert len(table) == 30
        assert ("fruits", "CHD", "25-29") in table
        assert ("fruits", "T2DM", "95+") in table

    def test_exposures_are_sorted(self, small_rr_df):
        """Exposure arrays in each entry are sorted ascending."""
        risk_cause_map = {"fruits": ["CHD", "T2DM"]}
        table, _ = _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)
        for key, data in table.items():
            exposures = data["exposures"]
            assert list(exposures) == sorted(exposures), f"Unsorted exposures for {key}"

    def test_log_rr_values_are_log_transformed(self, small_rr_df):
        """The stored log_rr_mean values are the natural log of the input rr_mean."""
        risk_cause_map = {"fruits": ["CHD", "T2DM"]}
        table, _ = _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)
        data = table[("fruits", "CHD", "25-29")]
        # Input RR values for CHD: [1.5, 1.0, 0.8]
        expected_log = np.log(np.array([1.5, 1.0, 0.8]))
        np.testing.assert_allclose(data["log_rr_mean"], expected_log)

    def test_max_exposure_computed(self, small_rr_df):
        """max_exposure_g_per_day reports the maximum exposure per risk."""
        risk_cause_map = {"fruits": ["CHD", "T2DM"]}
        _, max_exp = _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)
        # CHD goes up to 200, T2DM goes up to 150 -> max is 200
        assert max_exp["fruits"] == pytest.approx(200.0)

    def test_missing_pair_raises_error(self, small_rr_df):
        """If a required (risk, cause) pair is absent, raise ValueError."""
        # Require a cause "stroke" that is not in the data
        risk_cause_map = {"fruits": ["CHD", "T2DM", "stroke"]}
        with pytest.raises(ValueError, match="missing risk-cause pairs"):
            _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)


# ---------------------------------------------------------------------------
# Tests: _build_intake_caps
# ---------------------------------------------------------------------------


class TestBuildIntakeCaps:
    """Tests for applying intake cap limits across risk factors."""

    def test_cap_limit_positive_enforces_minimum(self):
        """When cap_limit > 0, each cap is at least cap_limit."""
        max_exposure = {"fruits": 200.0, "sugar": 50.0}
        caps = _build_intake_caps(max_exposure, intake_cap_limit=300.0)
        assert caps["fruits"] == pytest.approx(300.0)
        assert caps["sugar"] == pytest.approx(300.0)

    def test_cap_limit_positive_keeps_larger_exposure(self):
        """When max_exposure exceeds cap_limit, the exposure value is kept."""
        max_exposure = {"fruits": 500.0, "sugar": 50.0}
        caps = _build_intake_caps(max_exposure, intake_cap_limit=300.0)
        assert caps["fruits"] == pytest.approx(500.0)
        assert caps["sugar"] == pytest.approx(300.0)

    def test_cap_limit_zero_returns_original(self):
        """When cap_limit is zero, the original values are returned unchanged."""
        max_exposure = {"fruits": 200.0, "sugar": 50.0}
        caps = _build_intake_caps(max_exposure, intake_cap_limit=0.0)
        assert caps["fruits"] == pytest.approx(200.0)
        assert caps["sugar"] == pytest.approx(50.0)

    def test_cap_limit_negative_returns_original(self):
        """When cap_limit is negative, the original values are returned unchanged."""
        max_exposure = {"fruits": 200.0, "sugar": 50.0}
        caps = _build_intake_caps(max_exposure, intake_cap_limit=-10.0)
        assert caps["fruits"] == pytest.approx(200.0)
        assert caps["sugar"] == pytest.approx(50.0)

    def test_returns_new_dict(self):
        """The function returns a new dict, not modifying the input."""
        max_exposure = {"fruits": 200.0}
        caps = _build_intake_caps(max_exposure, intake_cap_limit=300.0)
        assert caps is not max_exposure
