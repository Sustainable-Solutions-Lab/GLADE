# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the non-renewable groundwater supply band.

Covers the three pure/near-pure pieces of the groundwater feature without a full
Snakemake build: the depletion-trend computation, the tier decomposition
(carve/collapse), the source-split metrics, and the solve-time levers.
"""

import numpy as np
import pandas as pd
import pypsa
import pytest
import xarray as xr

from workflow.scripts.analysis.extract_water_metrics import extract_water_totals
from workflow.scripts.build_model.irrigation import calibrate_eta_c
from workflow.scripts.build_model.primary_resources import (
    _retain_material_water_capacities,
)
from workflow.scripts.build_region_watergap import (
    SECONDS_PER_MONTH,
    compute_depletion_raster,
    compute_monthly_flux_raster,
)
from workflow.scripts.compose_water_supply import (
    build_groundwater_bands,
    collapse_single,
)
from workflow.scripts.solve_model.core import (
    add_groundwater_depletion_cap,
    add_groundwater_depletion_pricing_to_objective,
)


def _renewable_tiers() -> pd.DataFrame:
    """Two regions of ascending-CF renewable tiers, one period (the per-period
    surface curve build_groundwater_bands / collapse_single consume)."""
    return pd.DataFrame(
        {
            "region": ["r1", "r1", "r1", "r2", "r2"],
            "period": [0, 0, 0, 0, 0],
            "tier": [0, 1, 2, 0, 1],
            "capacity_mm3": [10.0, 20.0, 30.0, 5.0, 15.0],
            "marginal_cf": [0.5, 1.0, 5.0, 0.2, 2.0],
        }
    )


def test_water_supply_omits_sub_cubic_metre_capacities():
    """Roundoff-scale supply bands cannot enter the optimization problem."""
    capacities = pd.DataFrame(
        {
            "region": ["r1", "r1", "r2"],
            "capacity_mm3": [1e-8, 1e-6, 2.0],
        }
    )

    retained = _retain_material_water_capacities(capacities, 1e-6)

    assert retained["capacity_mm3"].tolist() == [1e-6, 2.0]


# --------------------------------------------------------------------------- #
# Depletion trend
# --------------------------------------------------------------------------- #
def test_compute_depletion_only_from_declining_storage(tmp_path):
    """Only cells with a falling storage trend contribute positive depletion."""
    lat = np.array([0.25, -0.25])
    lon = np.array([0.25, 0.75])
    trend_start, trend_end = 2000, 2004
    n_months = (trend_end - 1901 + 1) * 12
    months = np.arange(n_months, dtype=float)
    year = 1901 + months // 12

    data = np.zeros((n_months, 2, 2), dtype=float)
    # (0,0) declining 12 mm/yr; (0,1) flat; (1,0) rising; (1,1) flat.
    data[:, 0, 0] = 1000.0 - 12.0 * (year - 1901)
    data[:, 0, 1] = 500.0
    data[:, 1, 0] = 100.0 + 6.0 * (year - 1901)
    data[:, 1, 1] = 500.0

    ds = xr.Dataset(
        {"groundwstor": (("time", "lat", "lon"), data)},
        coords={"time": months, "lat": lat, "lon": lon},
    )
    path = tmp_path / "groundwstor.nc"
    ds.to_netcdf(path)
    area_path = tmp_path / "continentalarea.nc"
    xr.Dataset(
        {"continentalarea": (("time", "lat", "lon"), np.ones((1, 2, 2)))},
        coords={"time": [-1900.0], "lat": lat, "lon": lon},
    ).to_netcdf(area_path)

    depletion_m3, _out_lat, _out_lon = compute_depletion_raster(
        str(path), str(area_path), trend_start, trend_end
    )
    assert depletion_m3.shape == (2, 2)
    assert depletion_m3[0, 0] > 0.0  # declining -> depletion
    assert depletion_m3[0, 1] == 0.0  # flat
    assert depletion_m3[1, 0] == 0.0  # rising -> no depletion
    assert depletion_m3[1, 1] == 0.0
    # 12 mm/yr over the specified 1 km2 continental area is 12,000 m3/yr.
    assert depletion_m3[0, 0] == pytest.approx(12_000.0)


def test_monthly_flux_uses_watergap_continental_area(tmp_path):
    """Flux conversion uses WaterGAP continental area, not full grid-cell area."""
    lat = np.array([0.25])
    lon = np.array([0.25])
    flux_path = tmp_path / "pirruse.nc"
    xr.Dataset(
        {"pirruse": (("time", "lat", "lon"), np.full((12, 1, 1), 0.001))},
        coords={"time": np.arange(12, dtype=float), "lat": lat, "lon": lon},
    ).to_netcdf(flux_path)
    area_path = tmp_path / "continentalarea.nc"
    xr.Dataset(
        {"continentalarea": (("time", "lat", "lon"), np.array([[[2.5]]]))},
        coords={"time": [-1900.0], "lat": lat, "lon": lon},
    ).to_netcdf(area_path)

    monthly, _out_lat, _out_lon = compute_monthly_flux_raster(
        str(flux_path), "pirruse", str(area_path), 1901, 1901
    )

    expected = 0.001 * SECONDS_PER_MONTH * 0.001 * 2.5e6
    assert np.allclose(monthly[:, 0, 0], expected)


# --------------------------------------------------------------------------- #
# Tier decomposition
# --------------------------------------------------------------------------- #
def _groundwater_bands(mined: dict, renewable_gw: dict) -> pd.DataFrame:
    """Region-indexed band-volume table (the compose groundwater input)."""
    regions = sorted(set(mined) | set(renewable_gw))
    return pd.DataFrame(
        {
            "mined_mm3": [mined.get(r, 0.0) for r in regions],
            "renewable_gw_mm3": [renewable_gw.get(r, 0.0) for r in regions],
        },
        index=pd.Index(regions, name="region"),
    )


def _agri(mm3: dict) -> pd.Series:
    return pd.Series(mm3, name="agri_consumption_mm3")


def test_annual_bands_are_per_region_gw_only():
    """The bands table is annual per-region groundwater only (surface is separate)."""
    tiers = _renewable_tiers()  # r1 surface = 60, r2 surface = 20
    bands_in = _groundwater_bands({"r1": 25.0, "r2": 100.0}, {"r1": 10.0, "r2": 50.0})
    agri = _agri({"r1": 40.0, "r2": 12.0})
    out = build_groundwater_bands(tiers, bands_in, agri, ceiling_factor=3.0)

    # GW-only, annual (no surface, no period/tier columns): one row per source.
    assert set(out.columns) == {"region", "source", "capacity_mm3", "marginal_cf"}
    assert set(out["source"].unique()) == {
        "groundwater_renewable",
        "groundwater_nonrenewable",
    }

    # Renewable groundwater at the full WaterGAP volume (annual, no /T, no clip).
    renew_gw = out[out.source == "groundwater_renewable"].set_index("region")[
        "capacity_mm3"
    ]
    assert renew_gw["r1"] == pytest.approx(10.0)
    assert renew_gw["r2"] == pytest.approx(50.0)

    # Non-renewable ceiling = ceiling_factor * annual consumption (annual, not *T).
    nonrenew = out[out.source == "groundwater_nonrenewable"].set_index("region")[
        "capacity_mm3"
    ]
    assert nonrenew["r1"] == pytest.approx(3.0 * 40.0)
    assert nonrenew["r2"] == pytest.approx(3.0 * 12.0)


def test_annual_bands_merit_cf_and_fallback():
    """Renewable GW at the scarcest surface CF; non-renewable cf 0; the C=0
    fallback sizes the ceiling from the region's surface scale."""
    tiers = _renewable_tiers()
    bands_in = _groundwater_bands({"r1": 25.0, "r2": 0.0}, {"r1": 10.0, "r2": 0.0})
    # r1 has a consumption anchor; r2 has none (C absent) -> surface fallback.
    agri = _agri({"r1": 40.0})
    out = build_groundwater_bands(tiers, bands_in, agri, ceiling_factor=3.0)

    r1 = out[out.region == "r1"].set_index("source")
    # Renewable GW sits at the region's scarcest surface CF (drawn after surface).
    assert r1.loc["groundwater_renewable", "marginal_cf"] == pytest.approx(5.0)
    assert r1.loc["groundwater_nonrenewable", "marginal_cf"] == pytest.approx(0.0)

    # r2 has no consumption anchor: ceiling falls back to surface scale (20 * 3).
    r2_nonrenew = out[(out.region == "r2") & (out.source == "groundwater_nonrenewable")]
    assert r2_nonrenew["capacity_mm3"].iloc[0] == pytest.approx(3.0 * 20.0)


def test_collapse_single_flat_cap():
    tiers = _renewable_tiers()
    out = collapse_single(tiers)
    assert (out.groupby("region").size() == 1).all()
    assert (out["marginal_cf"] == 0.0).all()
    assert (out["source"] == "renewable").all()
    assert out.loc[out.region == "r1", "capacity_mm3"].iloc[0] == pytest.approx(60.0)


# --------------------------------------------------------------------------- #
# Source-split metrics + solve-time levers (tiny hand-built network)
# --------------------------------------------------------------------------- #
def _network_with_water_tiers() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.buses.add(
        [
            "water:source",
            "water:r1",
            "impact:water_scarcity",
            "impact:groundwater_depletion",
            "impact:groundwater_renewable",
        ]
    )
    # One surface tier (CF=2), one renewable-groundwater tier (CF=3, tallied on
    # bus3), and one non-renewable tier (eff2=1) in region r1.
    n.links.add(
        ["supply:water:r1:t0", "supply:water:r1:t1", "supply:water:r1:t2"],
        bus0="water:source",
        bus1="water:r1",
        bus2=[
            "impact:water_scarcity",
            "impact:water_scarcity",
            "impact:groundwater_depletion",
        ],
        bus3=["", "impact:groundwater_renewable", ""],
        carrier="water_supply",
        efficiency=1.0,
        efficiency2=[2.0, 3.0, 1.0],
        efficiency3=[0.0, 1.0, 0.0],
        region="r1",
        source=["renewable", "groundwater_renewable", "groundwater_nonrenewable"],
    )
    n.stores.add(
        [
            "store:impact:water_scarcity",
            "store:impact:groundwater_depletion",
            "store:impact:groundwater_renewable",
        ],
        bus=[
            "impact:water_scarcity",
            "impact:groundwater_depletion",
            "impact:groundwater_renewable",
        ],
        e_nom_extendable=True,
    )
    # Hand-set dispatch: surface 100 Mm3, renewable GW 20 Mm3, mined 40 Mm3.
    n.links.dynamic.p0 = pd.DataFrame(
        {
            "supply:water:r1:t0": [100.0],
            "supply:water:r1:t1": [20.0],
            "supply:water:r1:t2": [40.0],
        },
        index=n.snapshots,
    )
    n.meta["water_consumed_fraction"] = 0.5
    return n


def test_metrics_split_three_sources():
    n = _network_with_water_tiers()
    totals = extract_water_totals(n)
    assert totals["withdrawn_mm3"] == pytest.approx(160.0)  # all sources
    # Reported withdrawal = consumption / consumed fraction.
    assert totals["withdrawal_reported_mm3"] == pytest.approx(320.0)
    # Scarcity accrues on CF-carrying tiers: surface (2*100) + renewable GW (3*20).
    assert totals["scarcity_mm3_eq"] == pytest.approx(260.0)
    assert totals["groundwater_renewable_mm3"] == pytest.approx(20.0)
    assert totals["groundwater_depletion_mm3"] == pytest.approx(40.0)
    assert totals["mean_cf"] == pytest.approx(260.0 / 120.0)  # CF-carrying draw


# --------------------------------------------------------------------------- #
# Consumptive-efficiency calibration
# --------------------------------------------------------------------------- #
def test_calibrate_eta_c_anchor_clip_floor():
    """eta_c = E/C, clipped to [eta_min, eta_max] and floored at E/pool."""
    regions = pd.Index(
        ["anchor", "low", "deficit", "noisy", "floored", "dry"], name="region"
    )
    e = pd.Series([50.0, 10.0, 300.0, 800.0, 50.0, 0.0], index=regions)
    c = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 0.0], index=regions)
    # Pools: generous except 'floored' (pool clipped below observed C) and
    # 'dry' (no water at all).
    pool = pd.Series([200.0, 200.0, 1000.0, 1000.0, 80.0, 0.0], index=regions)

    eta = calibrate_eta_c(e, c, pool, eta_min=0.2, eta_max=5.0)
    assert eta["anchor"] == pytest.approx(0.5)  # plain E/C
    assert eta["low"] == pytest.approx(0.2)  # 0.1 clipped up to eta_min
    # Deficit irrigation: E > C is trusted up to eta_max, so the baseline
    # draws the observed consumption C = E / eta_c = 100, not the full E.
    assert eta["deficit"] == pytest.approx(3.0)
    # An unreliably small anchor (E/C = 8) is capped at eta_max.
    assert eta["noisy"] == pytest.approx(5.0)
    # Floor: E/C = 0.5 would give baseline C = 100 > pool = 80; floored to
    # E/pool = 0.625 so the baseline draw exactly fits the pool.
    assert eta["floored"] == pytest.approx(50.0 / 80.0)
    assert eta["dry"] == pytest.approx(1.0)  # no irrigation -> pass-through


def test_calibrate_eta_c_infeasible_region_capped():
    """E above eta_max * pool cannot be fixed by efficiency; eta_c caps there."""
    regions = pd.Index(["overshoot"], name="region")
    eta = calibrate_eta_c(
        pd.Series([600.0], index=regions),
        pd.Series([1200.0], index=regions),
        pd.Series([100.0], index=regions),
        eta_min=0.2,
        eta_max=5.0,
    )
    assert eta["overshoot"] == pytest.approx(5.0)


def test_groundwater_cap_and_pricing_set_store_attributes():
    n = _network_with_water_tiers()
    add_groundwater_depletion_cap(n, 0.0)
    assert n.stores.static.at["store:impact:groundwater_depletion", "e_nom_max"] == 0.0
    add_groundwater_depletion_pricing_to_objective(n, 0.5)
    # 0.5 USD/m3 -> bnUSD/Mm3 = 0.5 / 1e-6 * 1e-9 = 5e-4.
    assert n.stores.static.at[
        "store:impact:groundwater_depletion", "marginal_cost_storage"
    ] == pytest.approx(5e-4)
