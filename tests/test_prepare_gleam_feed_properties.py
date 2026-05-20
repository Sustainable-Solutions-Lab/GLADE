# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for GLEAM monogastric feed-properties parsing.

The original implementation parsed the chicken ME column from GLEAM
Table S.3.4 but then discarded it, silently leaving the generic
`ME_MJ_per_kg_DM` column populated by pig ME only. These tests guard
against that regression by checking the parsed schema and the
species-mean derivation.
"""

from unittest.mock import patch

import pandas as pd

from workflow.scripts.prepare_gleam_feed_properties import (
    parse_gleam_monogastric_nutrition,
)


def _fake_raw_monogastric_table() -> pd.DataFrame:
    """Mimic the raw shape returned by pd.read_excel for Tab. S.3.4.

    `parse_gleam_monogastric_nutrition` reads with header=None and
    skiprows=2, then drops the first column and assigns its own column
    names. We supply 8 columns so the leading drop keeps 7 usable ones.
    The values follow Table S.3.4 conventions: GE and ME columns
    nominally in kJ/kg DM but the code converts both to MJ.
    """
    return pd.DataFrame(
        [
            [None, "1", "MAIZE", 18800, 14.0, 14200, 14500, 88.0],
            [None, "2", "WHEAT", 18500, 21.0, 13500, 13900, 87.0],
            [None, "3", "RICEBR", 18900, 22.0, 11000, 12500, 75.0],
            # non-uppercase row should be dropped
            [None, "x", "footnote", None, None, None, None, None],
        ]
    )


def test_parse_monogastric_emits_per_species_me_columns():
    """Both ME_pigs and ME_chickens columns must survive parsing."""
    with patch("pandas.read_excel", return_value=_fake_raw_monogastric_table()):
        df = parse_gleam_monogastric_nutrition("dummy.xlsx")

    assert "ME_pigs_MJ_per_kg_DM" in df.columns
    assert "ME_chickens_MJ_per_kg_DM" in df.columns


def test_parse_monogastric_converts_me_units_per_species():
    """ME values must be divided by 1000 (kJ -> MJ) for both species."""
    with patch("pandas.read_excel", return_value=_fake_raw_monogastric_table()):
        df = parse_gleam_monogastric_nutrition("dummy.xlsx")

    maize = df.loc[df["gleam_code"] == "MAIZE"].iloc[0]
    assert maize["ME_pigs_MJ_per_kg_DM"] == 14.5
    assert maize["ME_chickens_MJ_per_kg_DM"] == 14.2


def test_parse_monogastric_distinguishes_species_when_different():
    """RICEBR has pig ME 12.5 vs chicken ME 11.0; mean is 11.75.

    A regression where chicken ME silently equals pig ME would make the
    species columns identical and any downstream mean would collapse.
    """
    with patch("pandas.read_excel", return_value=_fake_raw_monogastric_table()):
        df = parse_gleam_monogastric_nutrition("dummy.xlsx")

    ricebr = df.loc[df["gleam_code"] == "RICEBR"].iloc[0]
    assert ricebr["ME_pigs_MJ_per_kg_DM"] != ricebr["ME_chickens_MJ_per_kg_DM"]
    mean_me = (ricebr["ME_pigs_MJ_per_kg_DM"] + ricebr["ME_chickens_MJ_per_kg_DM"]) / 2
    assert mean_me == 11.75
