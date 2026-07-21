# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the MIRCA-OS multi-cropping baseline.

Covers Stage-1 attribution (magnitude allocation, repeated-crop supports,
residual conservation), the per-cycle seasonal water split (T-column magnitude
preservation, distinct-crop period placement, repeated-crop stagger), and the
single-crop baseline reconciliation (cycle-multiplicity reduction, no negatives,
land balance).
"""

from collections import Counter

import numpy as np
import pandas as pd
import pypsa
import pytest

from workflow.scripts.build_model.crops import (
    fix_crop_production_to_baseline,
    reconcile_single_crop_baselines,
)
from workflow.scripts.derive_mirca_multicropping import (
    allocate,
    candidate_capacity,
    run_derivation,
)
from workflow.scripts.solve_model.production_stability import _multi_cycle_long


def _network_with_links(rows):
    """Minimal network whose links carry the columns reconciliation reads.

    Multi-cropping rows may carry an ``output_buses`` list; those become bus1,
    bus2, ... so reconciliation can count the built crop-output cycles.
    """
    n = pypsa.Network()
    df = pd.DataFrame(rows).set_index("name")
    max_outputs = max((len(r.get("output_buses", [])) for r in rows), default=0)
    for name in [f"bus{i}" for i in range(1, max_outputs + 1)]:
        df[name] = ""
    for row in rows:
        for i, bus in enumerate(row.get("output_buses", []), start=1):
            df.loc[row["name"], f"bus{i}"] = bus
    all_buses = set(df["bus0"])
    for i in range(1, max_outputs + 1):
        all_buses.update(b for b in df[f"bus{i}"] if b)
    for bus in all_buses:
        n.add("Bus", bus)
    kwargs = {
        col: df[col].to_numpy()
        for col in [
            "bus0",
            "carrier",
            "baseline_area_mha",
            "crop",
            "combination",
            "region",
            "resource_class",
            "water_supply",
            "country",
            *[f"bus{i}" for i in range(1, max_outputs + 1)],
        ]
    }
    n.add("Link", df.index, **kwargs)
    return n


# ─── Stage-1 attribution ───────────────────────────────────────────────────


def test_allocate_conserves_and_caps():
    """Allocation never exceeds capacity or M_total, and residual closes the sum."""
    m_total = np.array([[100.0]])
    # two candidates: a 2-cycle (cap 30) and a 3-cycle (cap 10 -> extra cap 20)
    caps = [np.array([[30.0]]), np.array([[10.0]])]
    cycles = [2, 3]
    areas, residual = allocate(m_total, caps, cycles)
    extra = sum((n - 1) * a for n, a in zip(cycles, areas))
    # total extra-cycle capacity 30 + 20 = 50 <= 100 -> both filled, residual 50
    assert areas[0][0, 0] == pytest.approx(30.0)
    assert areas[1][0, 0] == pytest.approx(10.0)
    assert extra[0, 0] == pytest.approx(50.0)
    assert residual[0, 0] == pytest.approx(50.0)


def test_allocate_rations_when_capacity_exceeds_magnitude():
    """When capacity exceeds M_total, it is rationed proportionally, residual 0."""
    m_total = np.array([[30.0]])
    caps = [np.array([[40.0]]), np.array([[40.0]])]  # extra cap 40 + 40 = 80 > 30
    cycles = [2, 2]
    areas, residual = allocate(m_total, caps, cycles)
    extra = sum((n - 1) * a for n, a in zip(cycles, areas))
    assert extra[0, 0] == pytest.approx(30.0)  # sum equals M_total
    assert residual[0, 0] == pytest.approx(0.0)
    assert areas[0][0, 0] == pytest.approx(15.0)  # split evenly (equal caps)


def test_candidate_capacity_repeated_rice_disjoint_supports():
    """Double-rice uses (Rice2 - Rice3); triple-rice uses Rice3 (disjoint)."""
    zone = np.array([[8]])  # zone 8 permits triple wetland rice
    rice_support = {
        "i": np.array([[10.0]]),  # Rice2: area with >= 2 rice cycles
        "i3": np.array([[3.0]]),  # Rice3: area with a 3rd rice cycle
    }
    double = candidate_capacity(
        ["wetland-rice", "wetland-rice"], "i", {}, zone, rice_support
    )
    triple = candidate_capacity(["wetland-rice"] * 3, "i", {}, zone, rice_support)
    assert double[0, 0] == pytest.approx(7.0)  # 10 - 3
    assert triple[0, 0] == pytest.approx(3.0)


def test_candidate_capacity_distinct_crop_min_and_zone():
    """Distinct-crop capacity is min(area) where both observed and zone permits."""
    crop_area = {
        ("wetland-rice", "i"): np.array([[8.0, 0.0]]),
        ("wheat", "i"): np.array([[5.0, 5.0]]),
    }
    zone = np.array([[5, 5]])  # zone 5 permits double with one rice
    cap = candidate_capacity(["wetland-rice", "wheat"], "i", crop_area, zone, {})
    assert cap[0, 0] == pytest.approx(5.0)  # min(8, 5)
    assert cap[0, 1] == pytest.approx(0.0)  # rice absent -> not a candidate


def test_run_derivation_balance_and_residual():
    """Full derivation on a 1-cell grid: attributed + residual == M_total."""
    cell = lambda v: np.array([[v]])  # noqa: E731
    annual = {
        ("Rice", "ir"): cell(20.0),
        ("Wheat", "ir"): cell(15.0),
        ("Rice", "rf"): cell(0.0),
        ("Wheat", "rf"): cell(0.0),
    }
    footprint = {"ir": cell(10.0), "rf": cell(0.0)}
    crop_area = {
        ("wetland-rice", "i"): cell(20.0),
        ("wheat", "i"): cell(15.0),
        ("wetland-rice", "r"): cell(0.0),
        ("wheat", "r"): cell(0.0),
    }
    zone = {"i": cell(5), "r": cell(1)}
    rice_support = {"i": cell(0.0), "i3": cell(0.0), "r": cell(0.0), "r3": cell(0.0)}
    combos = [
        {"name": "rice_wheat", "crops": ["wetland-rice", "wheat"], "water_supply": "i"}
    ]
    areas, residual, _stats = run_derivation(
        annual, footprint, crop_area, zone, rice_support, combos
    )
    m_total = 20.0 + 15.0 - 10.0  # 25
    attributed = (2 - 1) * areas[("rice_wheat", "i")][0, 0]
    # cap A_max = min(20, 15) = 15; extra cap 15 <= 25 -> attributed 15, residual 10
    assert attributed == pytest.approx(15.0)
    assert residual[0, 0] == pytest.approx(m_total - attributed)


# ─── Reconciliation multiplicity ───────────────────────────────────────────


def test_reconciliation_multiplicity_counts():
    """Cycle multiplicity: rice-wheat subtracts X from each; double rice 2X."""
    assert dict(Counter(["wetland-rice", "wheat"])) == {"wetland-rice": 1, "wheat": 1}
    assert dict(Counter(["wetland-rice", "wetland-rice"])) == {"wetland-rice": 2}
    assert dict(Counter(["wetland-rice"] * 3)) == {"wetland-rice": 3}


def _crop_link(name, crop, baseline, region="r0", cls=1):
    return {
        "name": name,
        "bus0": f"land:cropland:{region}_c{cls}_i",
        "carrier": "crop_production",
        "baseline_area_mha": baseline,
        "crop": crop,
        "combination": "",
        "region": region,
        "resource_class": cls,
        "water_supply": "irrigated",
        "country": "IND",
    }


def _multi_link(name, combo, baseline, crops, region="r0", cls=1, country="IND"):
    return {
        "name": name,
        "bus0": f"land:cropland:{region}_c{cls}_i",
        "carrier": "crop_production_multi",
        "baseline_area_mha": baseline,
        "crop": combo,
        "combination": combo,
        "region": region,
        "resource_class": cls,
        "water_supply": "irrigated",
        "country": country,
        # one output bus per built cycle
        "output_buses": [f"crop:{c}:{country}" for c in crops],
    }


def test_reconciliation_reduces_singles_by_multiplicity():
    """Rice-wheat multi (X) subtracts X from rice and X from wheat singles."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _crop_link("wheat", "wheat", 8.0),
            _multi_link("m", "rice_wheat", 3.0, ["wetland-rice", "wheat"]),
        ]
    )
    reconcile_single_crop_baselines(
        n, {"rice_wheat": {"crops": ["wetland-rice", "wheat"]}}
    )
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice"] == pytest.approx(7.0)  # 10 - 3
    assert bl["wheat"] == pytest.approx(5.0)  # 8 - 3
    assert bl["m"] == pytest.approx(3.0)  # multi anchor unchanged


def test_reconciliation_double_rice_subtracts_2x():
    """Double rice (X) subtracts 2X from the wetland-rice single."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _multi_link("m", "double_rice", 3.0, ["wetland-rice", "wetland-rice"]),
        ]
    )
    reconcile_single_crop_baselines(
        n, {"double_rice": {"crops": ["wetland-rice", "wetland-rice"]}}
    )
    assert n.links.static["baseline_area_mha"]["rice"] == pytest.approx(4.0)  # 10 - 6


def test_reconciliation_redistributes_over_subtraction_no_negative():
    """Over-subtraction in one cell is pushed onto another cell of same crop/country."""
    # Two regions of the same (crop, country, ws); region r0 has too little rice
    # baseline (2) for the 3 the multi anchors, so 1 spills onto r1.
    n = _network_with_links(
        [
            _crop_link("rice0", "wetland-rice", 2.0, region="r0"),
            _crop_link("rice1", "wetland-rice", 5.0, region="r1"),
            _multi_link(
                "m", "double_rice", 1.5, ["wetland-rice", "wetland-rice"], region="r0"
            ),  # subtract 2*1.5=3
        ]
    )
    reconcile_single_crop_baselines(
        n, {"double_rice": {"crops": ["wetland-rice", "wetland-rice"]}}
    )
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice0"] == pytest.approx(0.0)  # floored, not negative
    assert bl["rice1"] == pytest.approx(4.0)  # absorbed the 1 Mha over-subtraction
    assert (bl.loc[["rice0", "rice1"]] >= 0).all()
    # National total for (rice, IND, irrigated) dropped by exactly 3 (= 2*1.5)
    assert bl[["rice0", "rice1"]].sum() == pytest.approx(2.0 + 5.0 - 3.0)


def test_reconciliation_uses_built_cycles_not_config():
    """A combo built with fewer cycles reduces singles only for produced crops.

    rice_wheat configured, but the built link produces only rice (wheat cycle
    dropped, e.g. below min_yield). Only the rice single is reduced; wheat is not.
    """
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _crop_link("wheat", "wheat", 8.0),
            # built with only the rice output bus
            _multi_link("m", "rice_wheat", 3.0, ["wetland-rice"]),
        ]
    )
    reconcile_single_crop_baselines(
        n, {"rice_wheat": {"crops": ["wetland-rice", "wheat"]}}
    )
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice"] == pytest.approx(7.0)  # 10 - 3 (rice produced)
    assert bl["wheat"] == pytest.approx(8.0)  # unchanged: no wheat cycle built


# ─── Validation-mode pinning (use_actual_production) ───────────────────────


def test_fix_pins_both_carriers_at_reconciled_baselines():
    """After reconcile + fix, single and multi links are pinned so that each
    harvested cycle is counted exactly once and the joint per-crop total
    reproduces the original harvested area."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _crop_link("wheat", "wheat", 8.0),
            _multi_link("m", "rice_wheat", 3.0, ["wetland-rice", "wheat"]),
        ]
    )
    reconcile_single_crop_baselines(
        n, {"rice_wheat": {"crops": ["wetland-rice", "wheat"]}}
    )
    fix_crop_production_to_baseline(n)
    links = n.links.static
    for name, expected in [("rice", 7.0), ("wheat", 5.0), ("m", 3.0)]:
        assert links.at[name, "p_nom"] == pytest.approx(expected)
        assert links.at[name, "p_nom_min"] == pytest.approx(expected)
        assert links.at[name, "p_nom_max"] == pytest.approx(expected)
        assert links.at[name, "p_min_pu"] == pytest.approx(1.0)
        assert not links.at[name, "p_nom_extendable"]
    # Joint totals: single pin + multiplicity * multi pin == raw harvested area
    assert links.at["rice", "p_nom"] + links.at["m", "p_nom"] == pytest.approx(10.0)
    assert links.at["wheat", "p_nom"] + links.at["m", "p_nom"] == pytest.approx(8.0)


def test_multi_cycle_long_counts_ports_per_cycle():
    """_multi_cycle_long counts one cycle per crop-output port, so repeated
    crops carry their multiplicity and distinct crops one row each."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _multi_link("rw", "rice_wheat", 3.0, ["wetland-rice", "wheat"]),
            _multi_link("dr", "double_rice", 2.0, ["wetland-rice", "wetland-rice"]),
        ]
    )
    long = _multi_cycle_long(n.links.static)
    got = {
        (row["link"], row["crop"]): row["multiplicity"] for _, row in long.iterrows()
    }
    assert got == {
        ("rw", "wetland-rice"): 1,
        ("rw", "wheat"): 1,
        ("dr", "wetland-rice"): 2,
    }
    dr = long[long["link"] == "dr"].iloc[0]
    assert dr["group"] == "wetland-rice::IND"
    assert dr["baseline_area_mha"] == pytest.approx(2.0)
