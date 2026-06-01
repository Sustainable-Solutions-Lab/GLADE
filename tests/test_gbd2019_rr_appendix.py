# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for parsing the GBD 2019 relative-risk appendix workbook.

This logic feeds only the one-off ``generate_rr_age_attenuation.py`` (the GBD
2019 age structure donor); the per-build workflow takes its curves from GBD 2023
Burden of Proof. See ``test_relative_risks.py`` for the build-time transforms.
"""

import pandas as pd
import pytest

from workflow.scripts.gbd2019_rr_appendix import (
    CAUSE_MAP,
    RISK_CONFIG,
    _extract_risk_blocks,
    _normalize_exposure,
    _parse_rr_value,
    parse_gbd2019_rr_appendix,
)


class TestParseRrValue:
    """Tests for parsing relative risk values from cell contents."""

    def test_integer_input(self):
        mean, low, high = _parse_rr_value(1)
        assert mean == pytest.approx(1.0)
        assert low is None
        assert high is None

    def test_float_input(self):
        mean, low, high = _parse_rr_value(1.23)
        assert mean == pytest.approx(1.23)
        assert low is None
        assert high is None

    def test_string_with_ci(self):
        mean, low, high = _parse_rr_value("1.23 (1.10, 1.35)")
        assert mean == pytest.approx(1.23)
        assert low == pytest.approx(1.10)
        assert high == pytest.approx(1.35)

    def test_string_with_just_mean(self):
        mean, low, high = _parse_rr_value("1.5")
        assert mean == pytest.approx(1.5)
        assert low is None
        assert high is None

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty RR cell"):
            _parse_rr_value("")

    def test_non_parseable_string_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_rr_value("not a number")


class TestNormalizeExposure:
    """Tests for converting exposure text to g/day float."""

    def test_basic_g_per_day(self):
        assert _normalize_exposure("100 g/day", 1.0) == pytest.approx(100.0)

    def test_g_per_day_with_conversion(self):
        assert _normalize_exposure("50 g/day", 2.0) == pytest.approx(100.0)

    def test_energy_based_raises(self):
        with pytest.raises(ValueError, match="Energy-based exposures"):
            _normalize_exposure("5 %energy/day", 1.0)

    def test_no_unit_raises(self):
        with pytest.raises(ValueError, match="Unexpected exposure label"):
            _normalize_exposure("100", 1.0)

    def test_none_conversion_raises(self):
        with pytest.raises(ValueError, match="Missing conversion factor"):
            _normalize_exposure("100 g/day", None)


class TestExtractRiskBlocks:
    """Tests for finding dietary risk block boundaries in a DataFrame."""

    def test_recognized_blocks_extracted(self):
        df = pd.DataFrame(
            {
                0: [
                    "Diet low in fruits",
                    "Ischemic heart disease",
                    "Diabetes mellitus type 2",
                    "Colon and rectum cancer",
                    "Diet high in red meat",
                    "Ischemic heart disease",
                    "Colon and rectum cancer",
                    "Diet low in milk",
                ]
            }
        )
        blocks = _extract_risk_blocks(df)
        assert blocks["Diet low in fruits"] == (1, 4)
        assert blocks["Diet high in red meat"] == (5, 7)

    def test_unrecognized_diet_header_excluded(self):
        df = pd.DataFrame(
            {0: ["Diet low in fruits", "data row", "Diet low in milk", "more data"]}
        )
        assert "Diet low in milk" not in _extract_risk_blocks(df)

    def test_unrecognized_header_still_acts_as_boundary(self):
        df = pd.DataFrame(
            {
                0: [
                    "Diet low in fruits",
                    "data row 1",
                    "data row 2",
                    "Diet low in milk",
                    "data after unrecognized",
                ]
            }
        )
        assert _extract_risk_blocks(df)["Diet low in fruits"] == (1, 3)


class TestParseAppendix:
    """Tests for the full GBD 2019 appendix parser."""

    @staticmethod
    def _make_mock_df(rows):
        max_cols = max(max(r.keys()) for r in rows) + 1
        data = []
        for r in rows:
            row = [None] * max_cols
            for k, v in r.items():
                row[k] = v
            data.append(row)
        return pd.DataFrame(data)

    def test_valid_block_with_known_cause(self):
        row_data = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row_data[col] = "0.80 (0.70, 0.90)"
        df = self._make_mock_df([{0: "Diet low in fruits"}, row_data])
        result = parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=0.1)

        assert len(result) == 15
        row = result.iloc[0]
        assert row["risk_factor"] == "fruits"
        assert row["cause"] == "CHD"
        assert row["exposure_g_per_day"] == pytest.approx(100.0)
        assert row["rr_mean"] == pytest.approx(0.80)
        assert row["rr_low"] == pytest.approx(0.70)
        assert row["rr_high"] == pytest.approx(0.90)

    def test_unmapped_outcome_is_skipped(self):
        breast_row = {0: "Breast cancer", 1: "100 g/day"}
        ihd_row = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            breast_row[col] = "0.95 (0.90, 1.00)"
            ihd_row[col] = "0.80 (0.70, 0.90)"
        df = self._make_mock_df([{0: "Diet low in fruits"}, breast_row, ihd_row])
        result = parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=0.1)
        assert len(result) == 15
        assert result.iloc[0]["cause"] == "CHD"

    def test_output_columns(self):
        row_data = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row_data[col] = "0.80 (0.70, 0.90)"
        df = self._make_mock_df([{0: "Diet low in fruits"}, row_data])
        result = parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=0.1)
        assert set(result.columns) == {
            "risk_factor",
            "cause",
            "age",
            "exposure_g_per_day",
            "rr_mean",
            "rr_low",
            "rr_high",
        }

    def test_sugar_conversion_applied(self):
        ssb_sugar_per_gram = 0.11
        row_data = {0: "Diabetes mellitus type 2", 1: "200 g/day"}
        for col in range(13, 28):
            row_data[col] = "1.30 (1.20, 1.40)"
        df = self._make_mock_df(
            [{0: "Diet high in sugar-sweetened beverages"}, row_data]
        )
        result = parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=ssb_sugar_per_gram)
        assert result.iloc[0]["risk_factor"] == "sugar"
        assert result.iloc[0]["exposure_g_per_day"] == pytest.approx(
            200.0 * ssb_sugar_per_gram
        )

    def test_no_records_raises(self):
        row_data = {0: "Breast cancer", 1: "100 g/day"}
        for col in range(13, 28):
            row_data[col] = "0.95 (0.90, 1.00)"
        df = self._make_mock_df([{0: "Diet low in fruits"}, row_data])
        with pytest.raises(ValueError, match="No dietary risk records"):
            parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=0.1)

    def test_duplicate_records_aggregated(self):
        row1 = {0: "Ischemic heart disease", 1: "100 g/day"}
        row2 = {0: "Ischemic heart disease", 1: "100 g/day"}
        for col in range(13, 28):
            row1[col] = "0.80 (0.70, 0.90)"
            row2[col] = "0.90 (0.85, 0.95)"
        df = self._make_mock_df([{0: "Diet low in fruits"}, row1, row2])
        result = parse_gbd2019_rr_appendix(df, ssb_sugar_per_gram=0.1)
        assert result.iloc[0]["rr_mean"] == pytest.approx(0.85)
        assert result.iloc[0]["rr_low"] == pytest.approx(0.775)
        assert result.iloc[0]["rr_high"] == pytest.approx(0.925)


class TestConstants:
    """Tests for the CAUSE_MAP and RISK_CONFIG constants."""

    def test_cause_map_has_expected_causes(self):
        assert set(CAUSE_MAP.values()) == {"CHD", "Stroke", "T2DM", "CRC"}

    def test_cause_map_maps_ihme_names(self):
        assert CAUSE_MAP["Ischemic heart disease"] == "CHD"
        assert CAUSE_MAP["Ischemic stroke"] == "Stroke"
        assert "Intracerebral hemorrhage" not in CAUSE_MAP
        assert "Subarachnoid hemorrhage" not in CAUSE_MAP
        assert CAUSE_MAP["Diabetes mellitus type 2"] == "T2DM"
        assert CAUSE_MAP["Colon and rectum cancer"] == "CRC"

    def test_risk_config_has_expected_risk_factors(self):
        assert {v["risk_factor"] for v in RISK_CONFIG.values()} == {
            "fruits",
            "vegetables",
            "whole_grains",
            "legumes",
            "nuts_seeds",
            "red_meat",
            "sugar",
        }

    def test_risk_config_keys_start_with_diet(self):
        for key in RISK_CONFIG:
            assert key.startswith("Diet"), f"Key '{key}' does not start with 'Diet'"

    def test_risk_config_ssb_has_none_conversion(self):
        assert (
            RISK_CONFIG["Diet high in sugar-sweetened beverages"]["conversion"] is None
        )

    def test_risk_config_standard_factors_have_unit_conversion(self):
        for name, config in RISK_CONFIG.items():
            if config["risk_factor"] != "sugar":
                assert config["conversion"] == 1.0, f"Expected 1.0 for {name}"
