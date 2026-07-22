# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the MIRCA-OS multi-cropping baseline.

Covers baseline attribution, repeated-crop supports, residual conservation,
single-crop baseline reconciliation, and validation-mode pinning.
"""

import json

from affine import Affine
import numpy as np
import pandas as pd
import pypsa
import pytest
from rasterio.crs import CRS

from workflow.scripts.build_model.crops import (
    fix_crop_production_to_baseline,
    reconcile_single_crop_baselines,
)
from workflow.scripts.derive_mirca_multicropping import (
    GridSpec,
    _assert_grid,
    allocate,
    candidate_capacity,
    run_derivation,
)
from workflow.scripts.multi_cropping_combinations import (
    closest_mirca_multicropping_year,
    effective_combinations,
    observed_combinations,
)
from workflow.scripts.solve_model.production_stability import _multi_cycle_long

CATALOG = "data/curated/mirca_os_multicropping_combinations.yaml"


def test_grid_validation_allows_only_subpixel_metadata_rounding():
    """Known MIRCA affine rounding passes, while a real cell shift fails."""
    crs = CRS.from_epsg(4326)
    reference = GridSpec((2160, 4320), Affine(1 / 12, 0, -180, 0, -1 / 12, 90), crs)
    rounded = GridSpec(
        (2160, 4320), Affine(0.0833333, 0, -180, 0, -0.0833333, 89.999928), crs
    )
    _assert_grid(rounded, reference, "rounded")

    shifted = GridSpec((2160, 4320), Affine(1 / 12, 0, -179.99, 0, -1 / 12, 90), crs)
    with pytest.raises(ValueError, match="more than 1%"):
        _assert_grid(shifted, reference, "shifted")


def _network_with_links(rows):
    """Minimal network whose links carry the columns reconciliation reads.

    Multi-cropping rows carry their cycles as explicit ``crop_cycles`` metadata.
    Optional ``output_buses`` are populated only to verify that accounting does
    not infer crop identity from bus labels.
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
            "crop_cycles",
            "region",
            "resource_class",
            "water_supply",
            "country",
            *[f"bus{i}" for i in range(1, max_outputs + 1)],
        ]
    }
    n.add("Link", df.index, **kwargs)
    return n


# Baseline attribution


@pytest.mark.parametrize(
    "baseline_year, expected",
    [(2000, 2010), (2012, 2010), (2013, 2015), (2017, 2015), (2024, 2020)],
)
def test_mirca_release_tracks_baseline_year(baseline_year, expected):
    """The closest supported release is selected with a deterministic lower tie."""
    assert closest_mirca_multicropping_year(baseline_year) == expected


def test_combination_catalog_overrides_and_greenfield_additions():
    """Config disables catalog entries and adds zero-baseline greenfield systems."""
    config = {
        "crops": ["wetland-rice", "wheat", "barley"],
        "multiple_cropping": {
            "double_rice": None,
            "barley_wheat": {
                "crops": ["barley", "wheat"],
                "water_supplies": ["r"],
            },
        },
    }

    effective = effective_combinations(config, CATALOG)
    observed = observed_combinations(config, CATALOG)

    assert "rice_wheat" in effective
    assert "double_rice" not in effective
    assert "barley_wheat" in effective
    assert "barley_wheat" not in observed


@pytest.mark.parametrize(
    "name, entry, match",
    [
        (
            "rice_wheat",
            {"crops": ["wetland-rice", "wheat"], "water_supplies": ["r"]},
            "redefines a curated",
        ),
        ("unknown", None, "cannot disable anything"),
    ],
)
def test_combination_catalog_rejects_ambiguous_overrides(name, entry, match):
    """Catalog identities cannot be redefined and unknown names cannot be disabled."""
    config = {
        "crops": ["wetland-rice", "wheat"],
        "multiple_cropping": {name: entry},
    }

    with pytest.raises(ValueError, match=match):
        effective_combinations(config, CATALOG)


def test_allocate_conserves_and_caps():
    """Allocation never exceeds capacity or M_total, and residual closes the sum."""
    m_total = np.array([[100.0]])
    # two candidates: a 2-cycle (cap 30) and a 3-cycle (cap 10 -> extra cap 20)
    caps = [np.array([[30.0]]), np.array([[10.0]])]
    sequences = [["a", "b"], ["c", "d", "e"]]
    support = {crop: np.array([[100.0]]) for crop in "abcde"}
    areas, residual = allocate(m_total, np.array([[100.0]]), caps, sequences, support)
    extra = sum(
        (len(crops) - 1) * area for crops, area in zip(sequences, areas, strict=True)
    )
    # total extra-cycle capacity 30 + 20 = 50 <= 100 -> both filled, residual 50
    assert areas[0][0, 0] == pytest.approx(30.0)
    assert areas[1][0, 0] == pytest.approx(10.0)
    assert extra[0, 0] == pytest.approx(50.0)
    assert residual[0, 0] == pytest.approx(50.0)


def test_allocate_rations_when_capacity_exceeds_magnitude():
    """When capacity exceeds M_total, it is rationed proportionally, residual 0."""
    m_total = np.array([[30.0]])
    caps = [np.array([[40.0]]), np.array([[40.0]])]  # extra cap 40 + 40 = 80 > 30
    sequences = [["a", "b"], ["c", "d"]]
    support = {crop: np.array([[100.0]]) for crop in "abcd"}
    areas, residual = allocate(m_total, np.array([[100.0]]), caps, sequences, support)
    extra = sum(area for area in areas)
    assert extra[0, 0] == pytest.approx(30.0)  # sum equals M_total
    assert residual[0, 0] == pytest.approx(0.0)
    assert areas[0][0, 0] == pytest.approx(15.0)  # split evenly (equal caps)


def test_allocate_shares_overlapping_crop_budget():
    """The same observed crop area cannot support two full rotations."""
    caps = [np.array([[40.0]]), np.array([[40.0]])]
    sequences = [["wheat", "maize"], ["wheat", "soybean"]]
    support = {
        "wheat": np.array([[30.0]]),
        "maize": np.array([[40.0]]),
        "soybean": np.array([[40.0]]),
    }
    areas, residual = allocate(
        np.array([[100.0]]), np.array([[100.0]]), caps, sequences, support
    )

    assert areas[0][0, 0] == pytest.approx(15.0)
    assert areas[1][0, 0] == pytest.approx(15.0)
    assert sum(area[0, 0] for area in areas) == pytest.approx(30.0)
    assert residual[0, 0] == pytest.approx(70.0)


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
    # The 10 ha physical footprint caps the attributed bundle area.
    assert attributed == pytest.approx(10.0)
    assert residual[0, 0] == pytest.approx(m_total - attributed)


def test_run_derivation_keeps_water_system_budgets_separate():
    """Rainfed extra-cycle area cannot create an irrigated baseline anchor."""
    cell = lambda v: np.array([[v]])  # noqa: E731
    annual = {
        ("Rice", "ir"): cell(20.0),
        ("Wheat", "ir"): cell(15.0),
        ("Rice", "rf"): cell(20.0),
        ("Wheat", "rf"): cell(15.0),
    }
    footprint = {"ir": cell(35.0), "rf": cell(10.0)}
    crop_area = {
        ("wetland-rice", "i"): cell(20.0),
        ("wheat", "i"): cell(15.0),
        ("wetland-rice", "r"): cell(20.0),
        ("wheat", "r"): cell(15.0),
    }
    zone = {"i": cell(5), "r": cell(5)}
    rice_support = {"i": cell(0.0), "i3": cell(0.0), "r": cell(0.0), "r3": cell(0.0)}
    combos = [
        {"name": "rice_wheat", "crops": ["wetland-rice", "wheat"], "water_supply": "i"}
    ]

    areas, residual, _stats = run_derivation(
        annual, footprint, crop_area, zone, rice_support, combos
    )

    assert areas[("rice_wheat", "i")][0, 0] == pytest.approx(0.0)
    assert residual[0, 0] == pytest.approx(25.0)


# Reconciliation multiplicity


def _crop_link(name, crop, baseline, region="r0", cls=1):
    return {
        "name": name,
        "bus0": f"land:cropland:{region}_c{cls}_i",
        "carrier": "crop_production",
        "baseline_area_mha": baseline,
        "crop": crop,
        "combination": "",
        "crop_cycles": "",
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
        "crop_cycles": json.dumps(crops),
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
    reconcile_single_crop_baselines(n)
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
    reconcile_single_crop_baselines(n)
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
    reconcile_single_crop_baselines(n)
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice0"] == pytest.approx(0.0)  # floored, not negative
    assert bl["rice1"] == pytest.approx(4.0)  # absorbed the 1 Mha over-subtraction
    assert (bl.loc[["rice0", "rice1"]] >= 0).all()
    # National total for (rice, IND, irrigated) dropped by exactly 3 (= 2*1.5)
    assert bl[["rice0", "rice1"]].sum() == pytest.approx(2.0 + 5.0 - 3.0)


def test_reconciliation_uses_explicit_cycle_metadata():
    """Cycle accounting does not infer crops from output bus names."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 10.0),
            _crop_link("wheat", "wheat", 8.0),
            _multi_link("m", "rice_wheat", 3.0, ["wetland-rice", "wheat"]),
        ]
    )
    n.links.static.loc["m", ["bus1", "bus2"]] = ["crop:wheat:IND", ""]
    reconcile_single_crop_baselines(n)
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice"] == pytest.approx(7.0)
    assert bl["wheat"] == pytest.approx(5.0)


def test_reconciliation_scales_mirca_anchor_to_faostat_budget():
    """A bundle anchor is conservatively scaled when a crop budget is smaller."""
    n = _network_with_links(
        [
            _crop_link("rice", "wetland-rice", 4.0),
            _multi_link("m", "double_rice", 3.0, ["wetland-rice", "wetland-rice"]),
        ]
    )
    reconcile_single_crop_baselines(n)
    bl = n.links.static["baseline_area_mha"]
    assert bl["rice"] == pytest.approx(0.0)
    assert bl["m"] == pytest.approx(2.0)


def test_reconciliation_scales_overlapping_combinations_jointly():
    """Shared crop budgets scale every overlapping combination consistently."""
    n = _network_with_links(
        [
            _crop_link("a", "a", 10.0),
            _crop_link("b", "b", 100.0),
            _crop_link("c", "c", 100.0),
            _multi_link("ab", "a_b", 10.0, ["a", "b"]),
            _multi_link("ac", "a_c", 10.0, ["a", "c"]),
        ]
    )
    reconcile_single_crop_baselines(n)
    bl = n.links.static["baseline_area_mha"]
    assert bl["ab"] == pytest.approx(5.0)
    assert bl["ac"] == pytest.approx(5.0)
    assert bl["a"] == pytest.approx(0.0)
    assert bl["b"] == pytest.approx(95.0)
    assert bl["c"] == pytest.approx(95.0)


# Validation-mode pinning (use_actual_production)


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
    reconcile_single_crop_baselines(n)
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


def test_multi_cycle_long_counts_explicit_cycles():
    """Cycle metadata preserves repeated-crop multiplicity."""
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
