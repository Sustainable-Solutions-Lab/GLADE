# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the basin-aware region clustering in build_regions.py.

Covers the exact-count guarantee, the nesting invariant (each region is either
contained in one GADM province or a union of whole provinces), and scarcity-based
splitting -- all on the pure ``cluster_country`` path, which works on a plain
DataFrame of province-basin pieces (no geometry needed).
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from workflow.scripts.build_regions import (
    _largest_remainder,
    cluster_country,
    cluster_regions,
)


def _pieces() -> pd.DataFrame:
    """One country: a large province P1 straddling an abundant (CF 5) and a
    scarce (CF 90) basin, plus two small provinces P2, P3 in a mid basin."""
    return pd.DataFrame(
        {
            "prov": ["P1", "P1", "P2", "P3"],
            "px": [1.0, 3.0, 4.5, 4.5],
            "py": [1.0, 1.0, 0.5, 1.5],
            "area": [4.0, 4.0, 1.0, 1.0],
            "cf": [5.0, 90.0, 40.0, 40.0],
        }
    )


def _nesting_holds(pieces: pd.DataFrame, labels: np.ndarray) -> bool:
    df = pieces.assign(region=labels)
    for _, d in df.groupby("region"):
        provs = d["prov"].unique()
        if len(provs) == 1:
            continue  # contained in one province -> OK
        # union of provinces: each must be wholly inside this region
        region = d["region"].iloc[0]
        if not all(df.loc[df["prov"] == p, "region"].eq(region).all() for p in provs):
            return False
    return True


def test_largest_remainder_sums_to_total():
    q = pd.Series({"a": 3.2, "b": 0.4, "c": 0.4}, dtype=float)
    out = _largest_remainder(q, 4)
    assert out.sum() == 4
    assert out["a"] == 3  # floor kept
    assert (out >= 0).all()


@pytest.mark.parametrize("k", [2, 3, 4])
def test_cluster_country_exact_count_and_nesting(k):
    pieces = _pieces()
    labels = cluster_country(
        pieces, k, scarcity_weight=5.0, method="kmeans", random_state=0
    )
    assert len(set(labels)) == k  # exact count
    assert _nesting_holds(pieces, labels)


def test_cluster_country_splits_province_by_scarcity():
    pieces = _pieces()
    labels = cluster_country(
        pieces, 4, scarcity_weight=5.0, method="kmeans", random_state=0
    )
    p1 = pieces.assign(region=labels)
    p1 = p1[p1["prov"] == "P1"]
    # P1's abundant and scarce pieces land in different regions.
    assert p1["region"].nunique() == 2
    by_region_cf = p1.groupby("region")["cf"].mean()
    assert by_region_cf.max() - by_region_cf.min() > 50.0


def test_cluster_country_reconciles_when_pieces_scarce():
    # P1's area earns it two sub-regions, but it is a single indivisible piece,
    # so the split regime under-produces; reconciliation must recover the
    # deficit by splitting the merge group (P2's pieces) instead.
    pieces = pd.DataFrame(
        {
            "prov": ["P1", "P2", "P2", "P2"],
            "px": [0.0, 10.0, 12.0, 14.0],
            "py": [0.0, 0.0, 0.0, 0.0],
            "area": [10.0, 1.0, 1.0, 1.0],
            "cf": [5.0, 10.0, 50.0, 90.0],
        }
    )
    labels = cluster_country(
        pieces, 3, scarcity_weight=2.0, method="kmeans", random_state=0
    )
    df = pieces.assign(region=labels)
    assert df["region"].nunique() == 3
    assert _nesting_holds(pieces, labels)
    # The indivisible P1 stays one region; P2's pieces supply the other two.
    assert df.loc[df["prov"] == "P1", "region"].nunique() == 1
    assert df.loc[df["prov"] == "P2", "region"].nunique() == 2


def test_country_budget_uses_full_province_area():
    """A country's region budget follows its land area, not its basin fragmentation.

    ``pieces`` holds province-basin intersections, so a province straddling many
    basins contributes many rows. Summing the area of one row per province would
    starve exactly those countries whose provinces are the most fragmented.
    """
    # Two countries of equal total area and equal piece capacity, differing only
    # in how that area is split between provinces and basins: A is one province
    # cut into 4 basin pieces, B is two provinces cut into 2 pieces each.
    pieces = gpd.GeoDataFrame(
        {
            "GID_0": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "prov": ["A1", "A1", "A1", "A1", "B1", "B1", "B2", "B2"],
            "cf": [5.0, 20.0, 40.0, 90.0, 5.0, 20.0, 40.0, 90.0],
        },
        geometry=[
            box(0, 0, 1, 1),
            box(1, 0, 2, 1),
            box(0, 1, 1, 2),
            box(1, 1, 2, 2),
            box(10, 0, 11, 1),
            box(11, 0, 12, 1),
            box(10, 1, 11, 2),
            box(11, 1, 12, 2),
        ],
        crs="EPSG:4326",
    )
    regions = cluster_regions(pieces, 4, scarcity_weight=3.0)
    per_country = regions["country"].value_counts()
    assert (
        per_country["A"] == 2
    ), f"basin-fragmented country starved of regions: {per_country.to_dict()}"
    assert per_country["B"] == 2


def test_dissolved_regions_are_valid_geometries():
    """Region geometries must be valid: exactextract segfaults on invalid ones.

    Downstream raster aggregation calls into native code that crashes rather
    than raising on a self-intersecting polygon, so one bad region takes out an
    unrelated rule with an empty log. Dissolving province-basin pieces can
    produce them, hence the repair.
    """
    # An asymmetric bowtie: self-intersecting like real dissolve artefacts, and
    # with positive area so it survives the area-weighted clustering.
    bad = Polygon([(0, 0), (4, 4), (4, 0), (0, 3)])
    assert not bad.is_valid, "fixture must actually be invalid"
    pieces = gpd.GeoDataFrame(
        {
            "GID_0": ["A", "A"],
            "prov": ["P1", "P2"],
            "cf": [5.0, 50.0],
        },
        geometry=[bad, box(10, 0, 11, 1)],
        crs="EPSG:4326",
    )
    regions = cluster_regions(pieces, 2, scarcity_weight=3.0)
    assert regions.geometry.is_valid.all(), "invalid geometry escaped build_regions"
