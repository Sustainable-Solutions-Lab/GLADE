# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the WaterGAP surface-availability rescaling.

The AWARE scarcity (CF) curve is kept, but each (region, month) pool volume is
rescaled to WaterGAP's monthly irrigation surface consumption. The regional
total is fixed by the WaterGAP anchor, while a direct grid-to-basin overlay
sets the within-region basin split. These tests pin conservation, that the
direct split overrides the AWARE-area split, and the no-signal fallbacks.
"""

import pandas as pd
import pytest

from workflow.scripts.build_region_water_aware import scale_pool_to_watergap_surface


def _cells() -> pd.DataFrame:
    """Two regions; r1 has two basins in month 1, one in month 2."""
    return pd.DataFrame(
        {
            "region": ["r1", "r1", "r1", "r2", "r2"],
            "basin_id": [1, 2, 1, 3, 3],
            "month": [1, 1, 2, 1, 2],
            "region_pool": [30.0, 10.0, 20.0, 5.0, 5.0],
            "amd0": [0.1, 0.2, 0.1, 0.3, 0.4],
        }
    )


def _surface(values: dict) -> pd.Series:
    idx = pd.MultiIndex.from_tuples(values.keys(), names=["region", "month"])
    return pd.Series(list(values.values()), index=idx)


def _basin_surface(values: dict) -> pd.Series:
    idx = pd.MultiIndex.from_tuples(
        values.keys(), names=["region", "basin_id", "month"]
    )
    return pd.Series(list(values.values()), index=idx)


def test_scaled_monthly_totals_match_watergap():
    cells = _cells()
    surface = _surface(
        {("r1", 1): 4.0, ("r1", 2): 40.0, ("r2", 1): 5.0, ("r2", 2): 0.0}
    )
    basin_surface = _basin_surface(
        {
            ("r1", 1, 1): 8.0,
            ("r1", 2, 1): 2.0,
            ("r1", 1, 2): 10.0,
            ("r2", 3, 1): 1.0,
        }
    )
    scaled, factor = scale_pool_to_watergap_surface(cells, surface, basin_surface)

    monthly = scaled.groupby(["region", "month"])["region_pool"].sum()
    assert monthly[("r1", 1)] == pytest.approx(4.0)  # scaled down 10x
    assert monthly[("r1", 2)] == pytest.approx(40.0)  # scaled up 2x (trusted)
    assert monthly[("r2", 1)] == pytest.approx(5.0)  # unchanged
    assert monthly[("r2", 2)] == pytest.approx(0.0)  # WaterGAP delivers nothing
    assert factor[("r1", 1, 1)] == pytest.approx(3.2 / 30.0)
    assert factor[("r1", 2, 1)] == pytest.approx(0.8 / 10.0)


def test_direct_basin_overlay_replaces_aware_area_split_and_preserves_amd0():
    cells = _cells()
    surface = _surface(
        {("r1", 1): 4.0, ("r1", 2): 20.0, ("r2", 1): 5.0, ("r2", 2): 5.0}
    )
    basin_surface = _basin_surface(
        {
            ("r1", 1, 1): 1.0,
            ("r1", 2, 1): 9.0,
            ("r1", 1, 2): 1.0,
            ("r2", 3, 1): 1.0,
            ("r2", 3, 2): 1.0,
        }
    )
    scaled, _ = scale_pool_to_watergap_surface(cells, surface, basin_surface)

    # amd0 (the CF driver) is untouched.
    assert list(scaled["amd0"]) == list(cells["amd0"])
    # The WaterGAP basin 1 : basin 2 split within (r1, month 1) is 1:9,
    # rather than the AWARE pool's 30:10 area-weighted split.
    r1m1 = scaled[(scaled["region"] == "r1") & (scaled["month"] == 1)]
    split = r1m1.set_index("basin_id")["region_pool"]
    assert split[1] / split[2] == pytest.approx(1.0 / 9.0)


def test_unmapped_surface_delivery_gets_an_explicit_ceiling_cf_tier():
    cells = _cells()
    surface = _surface(
        {
            ("r1", 1): 40.0,
            ("r1", 2): 20.0,
            ("r2", 1): 5.0,
            ("r2", 2): 5.0,
        }
    )
    basin_surface = _basin_surface({})
    scaled, factor = scale_pool_to_watergap_surface(cells, surface, basin_surface)

    assert factor[("r1", 1, 1)] == pytest.approx(0.0)
    assert factor[("r1", 2, 1)] == pytest.approx(0.0)
    m3 = scaled[(scaled["region"] == "r1") & (scaled["month"] == 1)]
    by_basin = m3.groupby("basin_id")["region_pool"].sum()
    assert by_basin.sum() == pytest.approx(40.0)
    assert by_basin[-1] == pytest.approx(40.0)
    assert m3.set_index("basin_id").at[-1, "amd0"] == pytest.approx(0.0)


def test_delivery_to_zero_aware_pool_gets_the_aware_ceiling_cf():
    cells = pd.concat(
        [
            _cells(),
            pd.DataFrame(
                {
                    "region": ["r1"],
                    "basin_id": [1],
                    "month": [3],
                    "region_pool": [0.0],
                    "amd0": [0.1],
                }
            ),
        ],
        ignore_index=True,
    )
    surface = _surface(
        {
            ("r1", 1): 40.0,
            ("r1", 2): 20.0,
            ("r1", 3): 8.0,
            ("r2", 1): 5.0,
            ("r2", 2): 5.0,
        }
    )
    basin_surface = _basin_surface({("r1", 1, 3): 1.0})

    scaled, _ = scale_pool_to_watergap_surface(cells, surface, basin_surface)

    m3 = scaled[(scaled["region"] == "r1") & (scaled["month"] == 3)]
    assert m3["region_pool"].sum() == pytest.approx(8.0)
    assert m3["amd0"].iloc[0] == pytest.approx(0.0)


def test_region_without_any_aware_pool_keeps_direct_delivery_at_the_ceiling():
    cells = pd.DataFrame(
        {
            "region": ["r1", "r1"],
            "basin_id": [1, 1],
            "month": [1, 2],
            "region_pool": [0.0, 0.0],
            "amd0": [0.1, 0.2],
        }
    )
    surface = _surface({("r1", 1): 5.0, ("r1", 2): 3.0})
    basin_surface = _basin_surface({("r1", 1, 1): 5.0, ("r1", 1, 2): 3.0})
    scaled, factor = scale_pool_to_watergap_surface(cells, surface, basin_surface)
    assert (factor == 0.0).all()
    assert scaled["region_pool"].sum() == pytest.approx(8.0)
    assert (scaled["amd0"] == 0.0).all()
