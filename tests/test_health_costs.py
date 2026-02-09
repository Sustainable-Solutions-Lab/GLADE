# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for health cost preparation functions."""

import math

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.prepare_health_costs import (
    RelativeRiskTable,
    _age_bucket_min,
    _build_intake_caps,
    _build_rr_tables,
    _derive_tmrel_from_rr,
    _evaluate_rr,
)

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

    def test_bare_zero(self):
        """A bare '0' with no range marker falls through to the default."""
        assert _age_bucket_min("0") == 0


# ---------------------------------------------------------------------------
# Helpers for RR tests
# ---------------------------------------------------------------------------


def _make_simple_rr_table():
    """Build a minimal RelativeRiskTable for 'fruits' and 'CHD'.

    Exposures: [0, 100, 200]
    RR values: [1.5, 1.0, 0.8]
    """
    table = RelativeRiskTable()
    rr_values = np.array([1.5, 1.0, 0.8])
    table[("fruits", "CHD")] = {
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
        rr = _evaluate_rr(table, "fruits", "CHD", 100.0)
        assert rr == pytest.approx(1.0)

    def test_below_minimum_clamps(self):
        """Intake below the lowest exposure clamps to the first RR value."""
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", -10.0)
        assert rr == pytest.approx(1.5)

    def test_above_maximum_clamps(self):
        """Intake above the highest exposure clamps to the last RR value."""
        table = _make_simple_rr_table()
        rr = _evaluate_rr(table, "fruits", "CHD", 300.0)
        assert rr == pytest.approx(0.8)

    def test_interpolation_between_knots(self):
        """Intake between knots is log-linearly interpolated."""
        table = _make_simple_rr_table()
        intake = 50.0
        rr = _evaluate_rr(table, "fruits", "CHD", intake)

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
        rr = _evaluate_rr(table, "fruits", "CHD", 50.0)
        assert rr == pytest.approx(math.sqrt(1.5))


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
        table[("fruits", "CHD")] = {
            "exposures": np.array([0.0, 100.0, 200.0]),
            "log_rr_mean": np.log(rr_chd),
            "log_rr_low": np.log(rr_chd),
            "log_rr_high": np.log(rr_chd),
        }

        # T2DM: RR decreases from 1.3 to 0.7 (minimum at 200)
        rr_t2dm = np.array([1.3, 0.9, 0.7])
        table[("fruits", "T2DM")] = {
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
        table[("sugar", "T2DM")] = {
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


class TestBuildRRTables:
    """Tests for constructing RR lookup tables from a DataFrame."""

    @pytest.fixture
    def small_rr_df(self):
        """A minimal relative risk DataFrame with two (risk, cause) pairs."""
        rows = []
        for exposure, rr_mean in [(0.0, 1.5), (100.0, 1.0), (200.0, 0.8)]:
            rows.append(
                {
                    "risk_factor": "fruits",
                    "cause": "CHD",
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
                    "exposure_g_per_day": exposure,
                    "rr_mean": rr_mean,
                    "rr_low": rr_mean * 0.9,
                    "rr_high": rr_mean * 1.1,
                }
            )
        return pd.DataFrame(rows)

    def test_correct_number_of_pairs(self, small_rr_df):
        """Output table contains exactly the expected (risk, cause) pairs."""
        risk_cause_map = {"fruits": ["CHD", "T2DM"]}
        table, _ = _build_rr_tables(small_rr_df, ["fruits"], risk_cause_map)
        assert len(table) == 2
        assert ("fruits", "CHD") in table
        assert ("fruits", "T2DM") in table

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
        data = table[("fruits", "CHD")]
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
