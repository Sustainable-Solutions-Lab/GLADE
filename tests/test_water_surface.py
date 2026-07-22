# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the WaterGAP renewable-envelope rescaling and curve split.

The AWARE scarcity (CF) curve is kept, but each (region, month) pool volume is
rescaled to WaterGAP's joint renewable envelope: monthly irrigation surface
consumption plus the annual renewable-groundwater volume. The regional totals
are fixed by the WaterGAP anchors, while direct grid-to-basin overlays set the
within-region basin splits. These tests pin conservation, that the direct
split overrides the AWARE-area split, the no-signal fallbacks, and the
volume-conserving surface/groundwater split of the CF curve.
"""

import numpy as np
import pandas as pd
import pytest

from workflow.scripts.build_region_water_aware import (
    build_region_curves,
    scale_pool_to_watergap,
)


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


def _basin_series(values: dict) -> pd.Series:
    idx = pd.MultiIndex.from_tuples(
        values.keys(), names=["region", "basin_id", "month"]
    )
    return pd.Series(list(values.values()), index=idx)


def _gw(values: dict) -> pd.Series:
    return pd.Series(values, name="renewable_gw").rename_axis("region")


NO_GW = _gw({"r1": 0.0, "r2": 0.0})
NO_BASIN_GW = _basin_series({})


def test_scaled_monthly_totals_match_watergap():
    cells = _cells()
    surface = _surface(
        {("r1", 1): 4.0, ("r1", 2): 40.0, ("r2", 1): 5.0, ("r2", 2): 0.0}
    )
    basin_surface = _basin_series(
        {
            ("r1", 1, 1): 8.0,
            ("r1", 2, 1): 2.0,
            ("r1", 1, 2): 10.0,
            ("r2", 3, 1): 1.0,
        }
    )
    scaled = scale_pool_to_watergap(cells, surface, NO_GW, basin_surface, NO_BASIN_GW)

    monthly = scaled.groupby(["region", "month"])["region_pool"].sum()
    assert monthly[("r1", 1)] == pytest.approx(4.0)  # scaled down 10x
    assert monthly[("r1", 2)] == pytest.approx(40.0)  # scaled up 2x (trusted)
    assert monthly[("r2", 1)] == pytest.approx(5.0)  # unchanged
    assert monthly[("r2", 2)] == pytest.approx(0.0)  # WaterGAP delivers nothing
    # The 8:2 WaterGAP overlay split of (r1, month 1).
    r1m1 = scaled[(scaled["region"] == "r1") & (scaled["month"] == 1)]
    split = r1m1.set_index("basin_id")["region_pool"]
    assert split[1] == pytest.approx(3.2)
    assert split[2] == pytest.approx(0.8)
    # Without groundwater the whole envelope is surface.
    assert (scaled.loc[scaled["region_pool"] > 0, "surface_frac"] == 1.0).all()


def test_direct_basin_overlay_replaces_aware_area_split_and_preserves_amd0():
    cells = _cells()
    surface = _surface(
        {("r1", 1): 4.0, ("r1", 2): 20.0, ("r2", 1): 5.0, ("r2", 2): 5.0}
    )
    basin_surface = _basin_series(
        {
            ("r1", 1, 1): 1.0,
            ("r1", 2, 1): 9.0,
            ("r1", 1, 2): 1.0,
            ("r2", 3, 1): 1.0,
            ("r2", 3, 2): 1.0,
        }
    )
    scaled = scale_pool_to_watergap(cells, surface, NO_GW, basin_surface, NO_BASIN_GW)

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
    scaled = scale_pool_to_watergap(cells, surface, NO_GW, NO_BASIN_GW, NO_BASIN_GW)

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
    basin_surface = _basin_series(
        {
            ("r1", 1, 1): 1.0,
            ("r1", 1, 2): 1.0,
            ("r1", 1, 3): 1.0,
            ("r2", 3, 1): 1.0,
            ("r2", 3, 2): 1.0,
        }
    )

    scaled = scale_pool_to_watergap(cells, surface, NO_GW, basin_surface, NO_BASIN_GW)

    m3 = scaled[(scaled["region"] == "r1") & (scaled["month"] == 3)]
    assert m3["region_pool"].sum() == pytest.approx(8.0)
    assert m3["amd0"].iloc[0] == pytest.approx(0.0)


def test_groundwater_joins_the_renewable_envelope():
    cells = _cells()
    surface = _surface({("r1", 1): 4.0, ("r1", 2): 8.0, ("r2", 1): 5.0, ("r2", 2): 5.0})
    basin_surface = _basin_series(
        {
            ("r1", 1, 1): 1.0,
            ("r1", 2, 1): 1.0,
            ("r1", 1, 2): 2.0,
            ("r2", 3, 1): 1.0,
            ("r2", 3, 2): 1.0,
        }
    )
    # r1's annual renewable GW is 6.0, spread by the pirrusegw overlay 1:2
    # between (basin 1, month 1) and (basin 1, month 2).
    gw = _gw({"r1": 6.0, "r2": 0.0})
    basin_gw = _basin_series({("r1", 1, 1): 1.0, ("r1", 1, 2): 2.0})

    scaled = scale_pool_to_watergap(cells, surface, gw, basin_surface, basin_gw)

    keyed = scaled.set_index(["region", "basin_id", "month"])
    # (r1, basin 1, month 1): surface 2.0 + gw 2.0; (r1, basin 1, month 2):
    # surface 8.0 + gw 4.0. Annual gw = 6.0 conserved.
    assert keyed.at[("r1", 1, 1), "region_pool"] == pytest.approx(4.0)
    assert keyed.at[("r1", 1, 1), "surface_frac"] == pytest.approx(0.5)
    assert keyed.at[("r1", 1, 2), "region_pool"] == pytest.approx(12.0)
    assert keyed.at[("r1", 1, 2), "surface_frac"] == pytest.approx(8.0 / 12.0)
    gw_total = (scaled["region_pool"] * (1.0 - scaled["surface_frac"])).sum()
    assert gw_total == pytest.approx(6.0)
    # r2 has no groundwater: pure surface.
    assert (scaled.loc[scaled["region"] == "r2", "surface_frac"] == 1.0).all()


def test_unmapped_groundwater_gets_an_explicit_ceiling_band():
    cells = _cells()
    surface = _surface({("r1", 1): 4.0, ("r1", 2): 8.0, ("r2", 1): 5.0, ("r2", 2): 5.0})
    basin_surface = _basin_series(
        {
            ("r1", 1, 1): 1.0,
            ("r1", 1, 2): 1.0,
            ("r2", 3, 1): 1.0,
            ("r2", 3, 2): 1.0,
        }
    )
    gw = _gw({"r1": 6.0, "r2": 0.0})

    scaled = scale_pool_to_watergap(cells, surface, gw, basin_surface, NO_BASIN_GW)

    ceiling = scaled[(scaled["region"] == "r1") & (scaled["basin_id"] == -1)]
    assert ceiling["region_pool"].sum() == pytest.approx(6.0)
    assert (ceiling["surface_frac"] == 0.0).all()
    assert (ceiling["amd0"] == 0.0).all()


def test_curve_split_conserves_surface_and_groundwater_volumes():
    long = pd.DataFrame(
        {
            "region": ["r1", "r1", "r2"],
            "month": [1, 2, 1],
            "volume": [30.0e6, 20.0e6, 10.0e6],
            "amd0": [0.05, 0.05, 0.5],
            "surface_frac": [0.6, 1.0, 0.5],
        }
    )
    surface_tiers, gw_bands = build_region_curves(long, 8, 4)

    surface_by_region_month = surface_tiers.groupby(["region", "month"])[
        "capacity_mm3"
    ].sum()
    assert surface_by_region_month[("r1", 1)] == pytest.approx(18.0)
    assert surface_by_region_month[("r1", 2)] == pytest.approx(20.0)
    assert surface_by_region_month[("r2", 1)] == pytest.approx(5.0)
    gw_by_region = gw_bands.groupby("region")["capacity_mm3"].sum()
    assert gw_by_region["r1"] == pytest.approx(12.0)
    assert gw_by_region["r2"] == pytest.approx(5.0)


def test_groundwater_takes_the_upper_curve_slice():
    """GW bands sit above the surface tiers of the same basin curve."""
    long = pd.DataFrame(
        {
            "region": ["r1"],
            "month": [1],
            "volume": [80.0e6],
            "amd0": [0.05],
            "surface_frac": [0.5],
        }
    )
    surface_tiers, gw_bands = build_region_curves(long, 8, 4)

    assert surface_tiers["marginal_cf"].max() <= gw_bands["marginal_cf"].min()
    assert (np.diff(gw_bands["marginal_cf"].to_numpy()) >= 0).all()
    # An abundant basin's GW slice must not saturate at the CF ceiling.
    assert gw_bands["marginal_cf"].iloc[0] < 100.0
