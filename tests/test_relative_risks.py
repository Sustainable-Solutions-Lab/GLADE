# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the build-time relative-risk transforms (GBD 2023 Burden of Proof).

Covers the per-(risk, cause) steps in ``prepare_relative_risks``: the literature
log-linear override, TMREL clipping, and age expansion. The GBD 2019 appendix
parser that feeds the curated age-attenuation table is tested separately in
``test_gbd2019_rr_appendix.py``.
"""

import math

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.prepare_relative_risks import (
    ADULT_AGE_LABELS,
    _age_expand,
    _clip_at_tmrel,
    _ensure_knot,
    _override_all_ages,
)


def _curve(risk, cause, xs, rr):
    """Build an all-ages curve frame with symmetric +/-10% UI bounds."""
    return pd.DataFrame(
        {
            "risk_factor": risk,
            "cause": cause,
            "exposure_g_per_day": [float(x) for x in xs],
            "rr_mean": list(rr),
            "rr_low": [r * 0.9 for r in rr],
            "rr_high": [r * 1.1 for r in rr],
        }
    )


# ---------------------------------------------------------------------------
# _override_all_ages
# ---------------------------------------------------------------------------


class TestOverrideAllAges:
    @staticmethod
    def _csv(tmp_path):
        p = tmp_path / "alt_rr.csv"
        p.write_text(
            "outcome,rr_central,rr_lower_95ci,rr_upper_95ci,per_unit\n"
            "CHD,1.15,1.08,1.23,100 g/day\n"
            "T2DM,1.10,1.06,1.15,100 g/day\n"
        )
        return str(p)

    def test_log_linear_values(self, tmp_path):
        df = _override_all_ages(
            self._csv(tmp_path), "red_meat", ["CHD", "T2DM"], [0.0, 100.0, 200.0]
        )
        chd = df[df["cause"] == "CHD"].set_index("exposure_g_per_day")
        assert chd.loc[0.0, "rr_mean"] == pytest.approx(1.0)  # RR(0) = 1
        assert chd.loc[100.0, "rr_mean"] == pytest.approx(1.15)  # at per_unit
        assert chd.loc[200.0, "rr_mean"] == pytest.approx(1.15**2)  # log-linear

    def test_bounds_propagate(self, tmp_path):
        df = _override_all_ages(self._csv(tmp_path), "red_meat", ["CHD"], [100.0])
        row = df[df["cause"] == "CHD"].iloc[0]
        assert row["rr_low"] == pytest.approx(1.08)
        assert row["rr_high"] == pytest.approx(1.23)

    def test_only_requested_causes(self, tmp_path):
        df = _override_all_ages(self._csv(tmp_path), "red_meat", ["CHD"], [100.0])
        assert set(df["cause"]) == {"CHD"}

    def test_missing_cause_raises(self, tmp_path):
        with pytest.raises(ValueError, match="missing causes"):
            _override_all_ages(
                self._csv(tmp_path), "red_meat", ["CHD", "Stroke"], [100.0]
            )


# ---------------------------------------------------------------------------
# _ensure_knot / _clip_at_tmrel
# ---------------------------------------------------------------------------


class TestEnsureKnot:
    def test_inserts_log_interpolated_knot(self):
        g = _curve("fruits", "CHD", [0, 100, 200], [1.0, 0.9, 0.82])
        out = _ensure_knot(g, 150.0)
        assert 150.0 in set(out["exposure_g_per_day"])
        rr150 = out.loc[out["exposure_g_per_day"] == 150.0, "rr_mean"].iloc[0]
        assert rr150 == pytest.approx(
            math.sqrt(0.9 * 0.82)
        )  # geometric mean in log space

    def test_existing_knot_unchanged(self):
        g = _curve("fruits", "CHD", [0, 100, 200], [1.0, 0.9, 0.82])
        assert len(_ensure_knot(g, 100.0)) == len(g)


class TestClipAtTmrel:
    def test_protective_truncates_above_tmrel(self):
        g = _curve("fruits", "CHD", [0, 100, 200, 300], [1.0, 0.9, 0.82, 0.78])
        out = _clip_at_tmrel(g, 150.0, "protective")
        assert out["exposure_g_per_day"].max() == pytest.approx(150.0)
        assert (out["exposure_g_per_day"] <= 150.0 + 1e-9).all()

    def test_protective_tmrel_below_range_raises(self):
        g = _curve("fruits", "CHD", [10, 100, 200], [0.95, 0.9, 0.82])
        with pytest.raises(ValueError, match="Protective TMREL"):
            _clip_at_tmrel(g, 5.0, "protective")

    def test_harmful_truncates_below_tmrel(self):
        g = _curve("red_meat", "CHD", [0, 50, 100, 150], [1.0, 1.1, 1.2, 1.3])
        out = _clip_at_tmrel(g, 25.0, "harmful")
        assert out["exposure_g_per_day"].min() == pytest.approx(25.0)
        assert (out["exposure_g_per_day"] >= 25.0 - 1e-9).all()

    def test_harmful_tmrel_zero_is_noop(self):
        g = _curve("red_meat", "CHD", [0, 50, 100], [1.0, 1.1, 1.2])
        out = _clip_at_tmrel(g, 0.0, "harmful")
        assert len(out) == len(g)

    def test_harmful_tmrel_above_range_raises(self):
        g = _curve("red_meat", "CHD", [0, 50, 100], [1.0, 1.1, 1.2])
        with pytest.raises(ValueError, match="Harmful TMREL"):
            _clip_at_tmrel(g, 200.0, "harmful")

    def test_unknown_risk_type_raises(self):
        g = _curve("fruits", "CHD", [0, 100], [1.0, 0.9])
        with pytest.raises(ValueError, match="Unknown risk_type"):
            _clip_at_tmrel(g, 50.0, "weird")


# ---------------------------------------------------------------------------
# _age_expand
# ---------------------------------------------------------------------------


class TestAgeExpand:
    @staticmethod
    def _beta(value_by_age):
        return {("wg", "Stroke", a): value_by_age.get(a, 1.0) for a in ADULT_AGE_LABELS}

    def test_all_ages_emitted(self):
        g = _curve("wg", "Stroke", [0, 100], [1.0, 0.8])
        out = _age_expand(g, "wg", "Stroke", self._beta({}))
        assert set(out["age"]) == set(ADULT_AGE_LABELS)
        assert len(out) == len(ADULT_AGE_LABELS) * 2

    def test_beta_one_is_identity(self):
        g = _curve("wg", "Stroke", [0, 100], [1.0, 0.8])
        out = _age_expand(g, "wg", "Stroke", self._beta({}))
        row = out[(out["age"] == "50-54") & (out["exposure_g_per_day"] == 100.0)].iloc[
            0
        ]
        assert row["rr_mean"] == pytest.approx(0.8)

    def test_beta_amplifies_younger(self):
        g = _curve("wg", "Stroke", [0, 100], [1.0, 0.8])
        out = _age_expand(g, "wg", "Stroke", self._beta({"25-29": 2.0}))
        young = out[
            (out["age"] == "25-29") & (out["exposure_g_per_day"] == 100.0)
        ].iloc[0]
        # RR_age = exp(beta * log RR) = 0.8**2
        assert young["rr_mean"] == pytest.approx(0.8**2)
        assert young["rr_low"] == pytest.approx((0.8 * 0.9) ** 2)
        assert young["rr_high"] == pytest.approx((0.8 * 1.1) ** 2)

    def test_reference_value_preserved_at_beta_one(self):
        # beta == 1 reproduces the BoP curve exactly (the 60-64 reference behaviour).
        g = _curve("wg", "Stroke", [0, 50, 100], [1.0, 0.9, 0.8])
        out = _age_expand(g, "wg", "Stroke", self._beta({}))
        got = (
            out[out["age"] == "60-64"]
            .sort_values("exposure_g_per_day")["rr_mean"]
            .to_numpy()
        )
        assert np.allclose(got, [1.0, 0.9, 0.8])
