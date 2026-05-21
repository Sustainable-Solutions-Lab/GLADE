# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for `prepare_gbd_mortality.scale_stroke_to_ischemic`.

Pins the level-shift semantics applied to aggregate-stroke mortality so
that the cause-map asymmetry (RR pipeline restricts to ischemic stroke;
mortality side scales by a global ischemic share) cannot regress
silently.
"""

import pandas as pd

from workflow.scripts.prepare_gbd_mortality import scale_stroke_to_ischemic


def _df():
    return pd.DataFrame(
        {
            "cause_code": ["Stroke", "Stroke", "CHD", "T2DM"],
            "val": [100.0, 200.0, 300.0, 400.0],
        }
    )


def test_share_1_is_noop():
    df = _df()
    out = scale_stroke_to_ischemic(df, 1.0)
    pd.testing.assert_frame_equal(out, df)


def test_only_stroke_rows_scaled():
    df = _df()
    out = scale_stroke_to_ischemic(df, 0.6)
    assert out.loc[out["cause_code"] == "Stroke", "val"].tolist() == [60.0, 120.0]
    assert out.loc[out["cause_code"] == "CHD", "val"].iat[0] == 300.0
    assert out.loc[out["cause_code"] == "T2DM", "val"].iat[0] == 400.0


def test_idempotent_under_share_1_after_a_scale():
    df = _df()
    once = scale_stroke_to_ischemic(df, 0.6)
    twice = scale_stroke_to_ischemic(once, 1.0)
    pd.testing.assert_frame_equal(twice, once)


def test_does_not_mutate_input():
    df = _df()
    before = df.copy()
    scale_stroke_to_ischemic(df, 0.6)
    pd.testing.assert_frame_equal(df, before)
