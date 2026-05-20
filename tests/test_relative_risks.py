# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for relative risk parsing from IHME GBD data."""

import pandas as pd
import pytest

from workflow.scripts.prepare_relative_risks import (
    CAUSE_MAP,
    RISK_CONFIG,
    _extract_risk_blocks,
    _normalize_exposure,
    _parse_relative_risks,
    _parse_rr_value,
)

# ---------------------------------------------------------------------------
# Tests: _parse_rr_value
# ---------------------------------------------------------------------------


class TestParseRrValue:
    """Tests for parsing relative risk values from cell contents."""

    def test_integer_input(self):
        """Integer input returns float mean with no CI bounds."""
        mean, low, high = _parse_rr_value(1)
        assert mean == pytest.approx(1.0)
        assert low is None
        assert high is None

    def test_float_input(self):
        """Float input returns mean with no CI bounds."""
        mean, low, high = _parse_rr_value(1.23)
        assert mean == pytest.approx(1.23)
        assert low is None
        assert high is None

    def test_string_with_ci(self):
        """String with confidence interval parses mean, low, high."""
        mean, low, high = _parse_rr_value("1.23 (1.10, 1.35)")
        assert mean == pytest.approx(1.23)
        assert low == pytest.approx(1.10)
        assert high == pytest.approx(1.35)

    def test_string_with_just_mean(self):
        """String with just a numeric value parses mean only."""
        mean, low, high = _parse_rr_value("1.5")
        assert mean == pytest.approx(1.5)
        assert low is None
        assert high is None

    def test_empty_string_raises(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Empty RR cell"):
            _parse_rr_value("")

    def test_non_parseable_string_raises(self):
        """Non-parseable string raises ValueError."""
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_rr_value("not a number")


# ---------------------------------------------------------------------------
# Tests: _normalize_exposure
# ---------------------------------------------------------------------------


class TestNormalizeExposure:
    """Tests for converting exposure text to g/day float."""

    def test_basic_g_per_day(self):
        """100 g/day with conversion=1.0 returns 100.0."""
        result = _normalize_exposure("100 g/day", 1.0)
        assert result == pytest.approx(100.0)

    def test_g_per_day_with_conversion(self):
        """50 g/day with conversion=2.0 returns 100.0."""
        result = _normalize_exposure("50 g/day", 2.0)
        assert result == pytest.approx(100.0)

    def test_energy_based_raises(self):
        """Energy-based exposure units raise ValueError."""
        with pytest.raises(ValueError, match="Energy-based exposures"):
            _normalize_exposure("5 %energy/day", 1.0)

    def test_no_unit_raises(self):
        """Exposure without unit raises ValueError."""
        with pytest.raises(ValueError, match="Unexpected exposure label"):
            _normalize_exposure("100", 1.0)

    def test_none_conversion_raises(self):
        """None conversion factor raises ValueError."""
        with pytest.raises(ValueError, match="Missing conversion factor"):
            _normalize_exposure("100 g/day", None)


# ---------------------------------------------------------------------------
# Tests: _extract_risk_blocks
# ---------------------------------------------------------------------------


class TestExtractRiskBlocks:
    """Tests for finding dietary risk block boundaries in a DataFrame."""

    def test_recognized_blocks_extracted(self):
        """Recognized Diet headers produce correct block boundaries."""
        df = pd.DataFrame(
            {
                0: [
                    "Diet low in fruits",  # row 0
                    "Ischemic heart disease",  # row 1
                    "Diabetes mellitus type 2",  # row 2
                    "Colon and rectum cancer",  # row 3
                    "Diet high in red meat",  # row 4
                    "Ischemic heart disease",  # row 5
                    "Colon and rectum cancer",  # row 6
                    "Diet low in milk",  # row 7 (unrecognized)
                ]
            }
        )
        blocks = _extract_risk_blocks(df)

        assert "Diet low in fruits" in blocks
        assert blocks["Diet low in fruits"] == (1, 4)

        assert "Diet high in red meat" in blocks
        assert blocks["Diet high in red meat"] == (5, 7)

    def test_unrecognized_diet_header_excluded(self):
        """Unrecognized Diet headers are not included in output."""
        df = pd.DataFrame(
            {
                0: [
                    "Diet low in fruits",
                    "data row",
                    "Diet low in milk",  # not in RISK_CONFIG
                    "more data",
                ]
            }
        )
        blocks = _extract_risk_blocks(df)

        assert "Diet low in milk" not in blocks

    def test_unrecognized_header_still_acts_as_boundary(self):
        """Unrecognized Diet headers still act as block boundaries."""
        df = pd.DataFrame(
            {
                0: [
                    "Diet low in fruits",
                    "data row 1",
                    "data row 2",
                    "Diet low in milk",  # unrecognized, but acts as boundary
                    "data after unrecognized",
                ]
            }
        )
        blocks = _extract_risk_blocks(df)

        # fruits block ends at row 3 (the unrecognized header)
        assert blocks["Diet low in fruits"] == (1, 3)


# ---------------------------------------------------------------------------
# Tests: _parse_relative_risks
# ---------------------------------------------------------------------------


class TestParseRelativeRisks:
    """Tests for the full relative risks parser."""

    @staticmethod
    def _make_mock_df(rows):
        """Build a DataFrame from a list of row dicts with positional columns."""
        # Pad rows to a consistent width
        max_cols = max(max(r.keys()) for r in rows) + 1
        data = []
        for r in rows:
            row = [None] * max_cols
            for k, v in r.items():
                row[k] = v
            data.append(row)
        return pd.DataFrame(data)

    def test_valid_block_with_known_cause(self):
        """A valid block with mapped outcome produces correct output."""
        # RR values go in columns 13-27 (adult age groups)
        row_data = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row_data[col] = "0.80 (0.70, 0.90)"
        rows = [
            {0: "Diet low in fruits"},
            row_data,
        ]
        df = self._make_mock_df(rows)
        result = _parse_relative_risks(df, ssb_sugar_per_gram=0.1)

        # 15 age groups, 1 exposure
        assert len(result) == 15
        row = result.iloc[0]
        assert row["risk_factor"] == "fruits"
        assert row["cause"] == "CHD"
        assert row["exposure_g_per_day"] == pytest.approx(100.0)
        assert row["rr_mean"] == pytest.approx(0.80)
        assert row["rr_low"] == pytest.approx(0.70)
        assert row["rr_high"] == pytest.approx(0.90)

    def test_unmapped_outcome_is_skipped(self):
        """Outcomes not in CAUSE_MAP are silently skipped."""
        breast_row = {0: "Breast cancer", 1: "100 g/day"}
        ihd_row = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            breast_row[col] = "0.95 (0.90, 1.00)"
            ihd_row[col] = "0.80 (0.70, 0.90)"
        rows = [
            {0: "Diet low in fruits"},
            breast_row,
            ihd_row,
        ]
        df = self._make_mock_df(rows)
        result = _parse_relative_risks(df, ssb_sugar_per_gram=0.1)

        # 15 age groups for one cause
        assert len(result) == 15
        assert result.iloc[0]["cause"] == "CHD"

    def test_output_columns(self):
        """Output DataFrame has expected columns."""
        row_data = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row_data[col] = "0.80 (0.70, 0.90)"
        rows = [
            {0: "Diet low in fruits"},
            row_data,
        ]
        df = self._make_mock_df(rows)
        result = _parse_relative_risks(df, ssb_sugar_per_gram=0.1)

        expected_cols = {
            "risk_factor",
            "cause",
            "age",
            "exposure_g_per_day",
            "rr_mean",
            "rr_low",
            "rr_high",
        }
        assert set(result.columns) == expected_cols

    def test_sugar_conversion_applied(self):
        """SSB exposure is multiplied by ssb_sugar_per_gram."""
        ssb_sugar_per_gram = 0.11  # 11g sugar per 100g SSB
        row_data = {0: "Diabetes mellitus type 2", 1: "200 g/day"}
        for col in range(13, 28):
            row_data[col] = "1.30 (1.20, 1.40)"
        rows = [
            {0: "Diet high in sugar-sweetened beverages"},
            row_data,
        ]
        df = self._make_mock_df(rows)
        result = _parse_relative_risks(df, ssb_sugar_per_gram=ssb_sugar_per_gram)

        assert len(result) == 15
        assert result.iloc[0]["risk_factor"] == "sugar"
        assert result.iloc[0]["exposure_g_per_day"] == pytest.approx(
            200.0 * ssb_sugar_per_gram
        )

    def test_no_records_raises(self):
        """If no valid records are parsed, raises ValueError."""
        row_data = {0: "Breast cancer", 1: "100 g/day"}  # unmapped cause
        for col in range(13, 28):
            row_data[col] = "0.95 (0.90, 1.00)"
        rows = [
            {0: "Diet low in fruits"},
            row_data,
        ]
        df = self._make_mock_df(rows)
        with pytest.raises(ValueError, match="No dietary risk records"):
            _parse_relative_risks(df, ssb_sugar_per_gram=0.1)

    def test_duplicate_records_aggregated(self):
        """Duplicate risk/cause/age/exposure records are averaged."""
        row1 = {0: "Ischemic heart disease", 1: "100 g/day"}
        row2 = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row1[col] = "0.80 (0.70, 0.90)"
            row2[col] = "0.90 (0.85, 0.95)"
        rows = [
            {0: "Diet low in fruits"},
            row1,
            row2,
        ]
        df = self._make_mock_df(rows)
        result = _parse_relative_risks(df, ssb_sugar_per_gram=0.1)

        # 15 age groups, averaged across the two duplicate rows
        assert len(result) == 15
        assert result.iloc[0]["rr_mean"] == pytest.approx(0.85)
        assert result.iloc[0]["rr_low"] == pytest.approx(0.775)
        assert result.iloc[0]["rr_high"] == pytest.approx(0.925)


# ---------------------------------------------------------------------------
# Tests: _apply_alternative_rr
# ---------------------------------------------------------------------------


class TestApplyAlternativeRR:
    """Tests for log-linear RR overrides with age correction."""

    @staticmethod
    def _make_gbd_df():
        """Build a minimal GBD-like DataFrame with age-varying RR for red_meat."""
        from workflow.scripts.prepare_relative_risks import ADULT_AGE_LABELS

        rows = []
        # Red meat x CHD: age-varying (youngest=1.34, oldest=1.13 at 100g)
        # Simplified: linear attenuation from 1.34 to 1.13 across 15 ages
        for i, age in enumerate(ADULT_AGE_LABELS):
            frac = i / (len(ADULT_AGE_LABELS) - 1)
            rr_100 = 1.34 - frac * (1.34 - 1.13)
            for exposure, rr in [(0.0, 1.0), (100.0, rr_100), (200.0, rr_100**2)]:
                rows.append(
                    {
                        "risk_factor": "red_meat",
                        "cause": "CHD",
                        "age": age,
                        "exposure_g_per_day": exposure,
                        "rr_mean": rr,
                        "rr_low": rr * 0.9,
                        "rr_high": rr * 1.1,
                    }
                )
        # Red meat x T2DM: age-constant (RR=1.20 at 100g for all ages)
        for age in ADULT_AGE_LABELS:
            for exposure, rr in [(0.0, 1.0), (100.0, 1.20), (200.0, 1.20**2)]:
                rows.append(
                    {
                        "risk_factor": "red_meat",
                        "cause": "T2DM",
                        "age": age,
                        "exposure_g_per_day": exposure,
                        "rr_mean": rr,
                        "rr_low": rr * 0.9,
                        "rr_high": rr * 1.1,
                    }
                )
        return pd.DataFrame(rows)

    @staticmethod
    def _make_alt_csv(tmp_path):
        """Write a minimal alternative RR CSV."""
        csv_path = tmp_path / "alt_rr.csv"
        csv_path.write_text(
            "outcome,rr_central,rr_lower_95ci,rr_upper_95ci,per_unit\n"
            "CHD,1.15,1.08,1.23,100 g/day\n"
            "T2DM,1.10,1.06,1.15,100 g/day\n"
        )
        return str(csv_path)

    def test_log_linear_curve_at_youngest_age(self, tmp_path):
        """At youngest age (attenuation=1), RR should match literature exactly."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        csv_path = self._make_alt_csv(tmp_path)
        result = _apply_alternative_rr(gbd, {"red_meat": csv_path})

        chd_young = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "CHD")
            & (result["age"] == "25-29")
            & (result["exposure_g_per_day"] == 100.0)
        ]
        assert chd_young["rr_mean"].values[0] == pytest.approx(1.15)

    def test_log_linear_curve_at_200g(self, tmp_path):
        """At 200g, log-linear RR should be rr_per_100^2."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        csv_path = self._make_alt_csv(tmp_path)
        result = _apply_alternative_rr(gbd, {"red_meat": csv_path})

        chd_young_200 = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "CHD")
            & (result["age"] == "25-29")
            & (result["exposure_g_per_day"] == 200.0)
        ]
        # 1.15^2 = 1.3225
        assert chd_young_200["rr_mean"].values[0] == pytest.approx(1.15**2)

    def test_bounds_propagation(self, tmp_path):
        """CI bounds should follow the same log-linear formula."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        csv_path = self._make_alt_csv(tmp_path)
        result = _apply_alternative_rr(gbd, {"red_meat": csv_path})

        chd_100 = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "CHD")
            & (result["age"] == "25-29")
            & (result["exposure_g_per_day"] == 100.0)
        ]
        assert chd_100["rr_low"].values[0] == pytest.approx(1.08)
        assert chd_100["rr_high"].values[0] == pytest.approx(1.23)

    def test_age_correction_attenuates_older_ages(self, tmp_path):
        """For age-varying causes, oldest age should have attenuated RR."""

        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        csv_path = self._make_alt_csv(tmp_path)
        result = _apply_alternative_rr(gbd, {"red_meat": csv_path})

        chd_young = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "CHD")
            & (result["age"] == "25-29")
            & (result["exposure_g_per_day"] == 100.0)
        ]["rr_mean"].values[0]
        chd_old = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "CHD")
            & (result["age"] == "95+")
            & (result["exposure_g_per_day"] == 100.0)
        ]["rr_mean"].values[0]

        # Oldest should be closer to 1.0 than youngest
        assert chd_old < chd_young
        assert chd_old > 1.0

    def test_age_constant_cause_unchanged(self, tmp_path):
        """For age-constant causes (T2DM), all ages should have same RR."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        csv_path = self._make_alt_csv(tmp_path)
        result = _apply_alternative_rr(gbd, {"red_meat": csv_path})

        t2dm = result[
            (result["risk_factor"] == "red_meat")
            & (result["cause"] == "T2DM")
            & (result["exposure_g_per_day"] == 100.0)
        ]
        rr_values = t2dm["rr_mean"].unique()
        assert len(rr_values) == 1
        assert rr_values[0] == pytest.approx(1.10)

    def test_empty_alternative_rr_is_noop(self):
        """Empty alternative_rr dict should not modify the DataFrame."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        result = _apply_alternative_rr(gbd.copy(), {})
        pd.testing.assert_frame_equal(result, gbd)

    def test_null_path_is_skipped(self):
        """Null path for a risk factor should be skipped."""
        from workflow.scripts.prepare_relative_risks import _apply_alternative_rr

        gbd = self._make_gbd_df()
        result = _apply_alternative_rr(gbd.copy(), {"red_meat": None})
        pd.testing.assert_frame_equal(result, gbd)


# ---------------------------------------------------------------------------
# Tests: Constants (CAUSE_MAP and RISK_CONFIG)
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for the CAUSE_MAP and RISK_CONFIG constants."""

    def test_cause_map_has_expected_causes(self):
        """CAUSE_MAP covers the core disease outcomes."""
        expected_causes = {"CHD", "Stroke", "T2DM", "CRC"}
        actual_causes = set(CAUSE_MAP.values())
        assert expected_causes == actual_causes

    def test_cause_map_maps_ihme_names(self):
        """CAUSE_MAP maps IHME outcome names to model cause identifiers.

        The model's "Stroke" cause is restricted to ischemic stroke;
        hemorrhagic subtypes are deliberately absent (mortality side
        scales aggregate Stroke deaths by health.ischemic_stroke_share).
        """
        assert CAUSE_MAP["Ischemic heart disease"] == "CHD"
        assert CAUSE_MAP["Ischemic stroke"] == "Stroke"
        assert "Intracerebral hemorrhage" not in CAUSE_MAP
        assert "Subarachnoid hemorrhage" not in CAUSE_MAP
        assert CAUSE_MAP["Diabetes mellitus type 2"] == "T2DM"
        assert CAUSE_MAP["Colon and rectum cancer"] == "CRC"

    def test_risk_config_has_expected_risk_factors(self):
        """RISK_CONFIG covers the expected dietary risk factors."""
        expected_factors = {
            "fruits",
            "vegetables",
            "whole_grains",
            "legumes",
            "nuts_seeds",
            "red_meat",
            "sugar",
        }
        actual_factors = {v["risk_factor"] for v in RISK_CONFIG.values()}
        assert expected_factors == actual_factors

    def test_risk_config_keys_start_with_diet(self):
        """All RISK_CONFIG keys start with 'Diet'."""
        for key in RISK_CONFIG:
            assert key.startswith("Diet"), f"Key '{key}' does not start with 'Diet'"

    def test_risk_config_ssb_has_none_conversion(self):
        """SSB risk factor has None conversion (set dynamically)."""
        ssb_config = RISK_CONFIG["Diet high in sugar-sweetened beverages"]
        assert ssb_config["conversion"] is None

    def test_risk_config_standard_factors_have_unit_conversion(self):
        """Standard risk factors (not SSB) have conversion=1.0."""
        for name, config in RISK_CONFIG.items():
            if config["risk_factor"] != "sugar":
                assert (
                    config["conversion"] == 1.0
                ), f"Expected conversion=1.0 for {name}, got {config['conversion']}"
