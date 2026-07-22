# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Basin-aware clustering of GADM level-1 provinces into model regions.

Provinces are first split along hydrological basin boundaries (overlay with the
AWARE basins), so a province straddling an abundant and a scarce basin can be
separated -- otherwise pooling the two averages away the sub-provincial water
scarcity that drives groundwater use. The province-basin pieces are then
clustered per country into exactly ``target_count`` regions under a nesting
invariant: every model region is either contained in one GADM province (a large
province is split into sub-regions) or a union of whole provinces (small
provinces are merged), never a mix of partial pieces across provinces -- so
regions stay cleanly comparable to political units.

Within each country the regions are balanced on geography and basin scarcity
(``basin_scarcity_weight`` sets their relative influence; 0 recovers plain
geographic clustering). A reconciliation step splits the largest splittable
region until the exact target is reached, since a province allocated more
sub-regions than it has basin pieces would otherwise under-produce.
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS, Geod
import shapely
from sklearn.cluster import AgglomerativeClustering, KMeans

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

GEOD = Geod(ellps="WGS84")

# Coordinate grid the GeoJSON writer rounds to (GDAL's default 7 decimals).
GEOJSON_PRECISION = 1e-7


def _compute_country_geodesic_areas(
    gdf_wgs84: gpd.GeoDataFrame, country_col: str = "GID_0"
) -> pd.Series:
    """Compute geodesic area (m^2) per country by summing polygon areas.

    Expects geometries in EPSG:4326. Uses pyproj.Geod for accurate spherical area.
    """
    if gdf_wgs84.crs is None or not CRS(gdf_wgs84.crs).equals(CRS(4326)):
        gdf_wgs84 = gdf_wgs84.to_crs(4326)

    def geom_area(geom) -> float:
        if geom is None or geom.is_empty:
            return 0.0
        area, _ = GEOD.geometry_area_perimeter(geom)
        return abs(area)

    # area per region, then sum by country
    areas = gdf_wgs84.geometry.apply(geom_area)
    return areas.groupby(gdf_wgs84[country_col]).sum()


def _allocate_per_country_targets_by_weight(
    weights: pd.Series, counts: pd.Series, total_target: int
) -> pd.Series:
    """Allocate cluster counts per country proportional to weights (e.g., area).

    - Ensures at least 1 cluster for countries with at least 1 base unit
    - Caps by available units per country
    - Uses largest remainder and capacity-aware fill to match
      min(total_target, sum(counts)) exactly
    """
    # Keep only countries present in counts
    weights = weights.reindex(counts.index).fillna(0.0)

    nonempty = counts[counts > 0]
    if total_target < len(nonempty):
        raise ValueError(
            "target_count is smaller than the number of countries with regions; "
            "every country needs at least one region."
        )

    # Respect global capacity (cannot exceed number of base units)
    feasible_target = int(min(total_target, int(nonempty.sum())))

    total_w = weights.loc[nonempty.index].sum()
    if total_w <= 0:
        # fallback: equal shares
        raw = pd.Series(float(feasible_target) / len(nonempty), index=nonempty.index)
    else:
        raw = weights.loc[nonempty.index] / total_w * float(feasible_target)

    base = np.floor(raw).astype(int)
    base = base.clip(lower=1)
    base = np.minimum(base, nonempty)

    assigned = int(base.sum())
    remaining = feasible_target - assigned

    # Distribute remaining by largest remainder respecting caps
    if remaining > 0:
        remainders = (raw - np.floor(raw)).sort_values(ascending=False)
        # Keep cycling through remainders until filled or no capacity remains
        while remaining > 0:
            progressed = False
            for country in remainders.index:
                if remaining == 0:
                    break
                if base[country] < nonempty[country]:
                    base[country] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                # All countries are at capacity; cannot assign more
                break

    # If overshoot, reduce from countries with largest allocations (>1)
    while remaining < 0:
        candidates = base[base > 1]
        if candidates.empty:
            break
        drop_country = candidates.sort_values(ascending=False).index[0]
        base[drop_country] -= 1
        remaining += 1

    # Ensure full index coverage
    out = pd.Series(0, index=counts.index)
    out.loc[base.index] = base
    return out


def _cluster_coords(
    coords: np.ndarray, k: int, method: str, random_state: int = 0
) -> np.ndarray:
    """Cluster coordinate array into up to k clusters.

    Returns one label per row. If k <= 0 or k >= n, assigns unique labels.
    """
    if coords.shape[0] <= k or k <= 0:
        return np.arange(coords.shape[0])

    method = (method or "kmeans").lower()

    if method == "kmeans":
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        labels = km.fit_predict(coords)
        return labels
    elif method == "agglomerative":
        # Ward linkage minimizes within-cluster variance (good heuristic)
        ac = AgglomerativeClustering(n_clusters=k, linkage="ward")
        labels = ac.fit_predict(coords)
        return labels
    else:
        raise ValueError(f"Unknown clustering method: {method}")


def _largest_remainder(quota: pd.Series, total: int) -> pd.Series:
    """Integer apportionment summing exactly to ``total`` (floor 0).

    ``quota`` sums (approximately) to ``total``; floors are taken and the
    remaining seats distributed to the largest fractional remainders.
    """
    base = np.floor(quota).astype(int)
    remaining = int(total - base.sum())
    if remaining > 0:
        top = (quota - np.floor(quota)).sort_values(ascending=False).index[:remaining]
        base.loc[top] += 1
    elif remaining < 0:
        # floor already exceeds total (numerical): drop from smallest positive
        drop = base[base > 0].sort_values().index[:-remaining]
        base.loc[drop] -= 1
    return base


def _scarcity_features(
    px: np.ndarray, py: np.ndarray, cf: np.ndarray, weight: float
) -> np.ndarray:
    """Standardised ``[x, y, weight * scarcity]`` feature matrix for clustering."""

    def z(a: np.ndarray) -> np.ndarray:
        a = np.asarray(a, dtype=float)
        s = a.std()
        return (a - a.mean()) / (s if s > 0 else 1.0)

    return np.column_stack([z(px), z(py), weight * z(cf)])


def cluster_country(
    pieces: pd.DataFrame,
    k: int,
    scarcity_weight: float,
    method: str,
    random_state: int,
) -> np.ndarray:
    """Assign a local region id (0..k-1) to each province-basin piece of a country.

    ``pieces`` has one row per (province, basin) piece with columns ``prov``,
    ``px``, ``py`` (equal-area centroid), ``area`` and ``cf`` (basin scarcity).
    Provinces large enough for >=2 regions are *split*: their pieces are
    clustered (on geography + weighted scarcity) into sub-regions that stay
    within the province. All remaining provinces are grouped into whole-province
    regions. Every region is therefore either contained in one province or a
    union of whole provinces, and the total is exactly ``k``.
    """
    prov_area = pieces.groupby("prov")["area"].sum()
    quota = k * prov_area / prov_area.sum()
    n_p = _largest_remainder(quota, k)

    split = n_p[n_p >= 2]
    split_total = int(split.sum())
    remaining = [p for p in prov_area.index if n_p[p] < 2]
    k_rem = k - split_total
    # Ensure remaining provinces have at least one merge group to land in.
    if remaining and k_rem < 1:
        biggest = split.idxmax()
        n_p[biggest] -= 1
        split = n_p[n_p >= 2]
        split_total = int(split.sum())
        remaining = [p for p in prov_area.index if n_p[p] < 2]
        k_rem = k - split_total

    labels = pd.Series(-1, index=pieces.index, dtype=int)
    next_id = 0

    # Split regime: partition each large province's own pieces.
    for prov, n_sub in split.items():
        d = pieces[pieces["prov"] == prov]
        n_sub = min(int(n_sub), len(d))
        feat = _scarcity_features(
            d["px"].to_numpy(), d["py"].to_numpy(), d["cf"].to_numpy(), scarcity_weight
        )
        sub = _cluster_coords(feat, n_sub, method, random_state)
        labels.loc[d.index] = sub + next_id
        next_id += int(sub.max()) + 1

    # Merge regime: group remaining whole provinces.
    if remaining:
        rem = pieces[pieces["prov"].isin(remaining)]
        prov_px = rem.groupby("prov").apply(
            lambda g: np.average(g["px"], weights=g["area"]), include_groups=False
        )
        prov_py = rem.groupby("prov").apply(
            lambda g: np.average(g["py"], weights=g["area"]), include_groups=False
        )
        prov_cf = rem.groupby("prov").apply(
            lambda g: np.average(g["cf"], weights=g["area"]), include_groups=False
        )
        feat = _scarcity_features(
            prov_px.to_numpy(), prov_py.to_numpy(), prov_cf.to_numpy(), scarcity_weight
        )
        kk = min(int(k_rem), len(prov_px))
        grp = _cluster_coords(feat, kk, method, random_state)
        prov_to_group = dict(zip(prov_px.index, grp + next_id))
        labels.loc[rem.index] = rem["prov"].map(prov_to_group).to_numpy()

    # A province allocated more sub-regions than it has basin pieces, or a merge
    # group short of its target, under-produces. Reconcile to exactly k by
    # repeatedly splitting the largest splittable region (a single-province
    # region splits by piece; a whole-province group splits by province), which
    # preserves nesting and also improves size balance.
    labels = _reconcile_to_k(pieces, labels, k, scarcity_weight, method, random_state)
    return labels.to_numpy()


def _reconcile_to_k(
    pieces: pd.DataFrame,
    labels: pd.Series,
    k: int,
    scarcity_weight: float,
    method: str,
    random_state: int,
) -> pd.Series:
    """Split the largest splittable region until exactly ``k`` regions exist."""
    labels = labels.copy()
    area = pieces["area"]
    current = pd.Index(labels.unique())
    next_id = int(labels.max()) + 1
    while len(current) < k:
        # Rank regions by area; split the largest one that can be split.
        region_area = area.groupby(labels).sum().sort_values(ascending=False)
        split_done = False
        for region in region_area.index:
            members = labels.index[labels == region]
            sub = pieces.loc[members]
            provs = sub["prov"].unique()
            if len(provs) > 1:
                # Whole-province group: split provinces into two, keep them whole.
                pcen = sub.groupby("prov").apply(
                    lambda g: pd.Series(
                        {
                            "px": np.average(g["px"], weights=g["area"]),
                            "py": np.average(g["py"], weights=g["area"]),
                            "cf": np.average(g["cf"], weights=g["area"]),
                        }
                    ),
                    include_groups=False,
                )
                feat = _scarcity_features(
                    pcen["px"].to_numpy(),
                    pcen["py"].to_numpy(),
                    pcen["cf"].to_numpy(),
                    scarcity_weight,
                )
                two = _cluster_coords(feat, 2, method, random_state)
                move = pcen.index[two == 1]
                labels.loc[sub.index[sub["prov"].isin(move)]] = next_id
            elif len(sub) > 1:
                # Single province with multiple pieces: split pieces into two.
                feat = _scarcity_features(
                    sub["px"].to_numpy(),
                    sub["py"].to_numpy(),
                    sub["cf"].to_numpy(),
                    scarcity_weight,
                )
                two = _cluster_coords(feat, 2, method, random_state)
                labels.loc[sub.index[two == 1]] = next_id
            else:
                continue  # single indivisible piece
            next_id += 1
            current = pd.Index(labels.unique())
            split_done = True
            break
        if not split_done:
            break  # no region can be split further (pieces exhausted)
    return labels


def cluster_regions(
    pieces: gpd.GeoDataFrame,
    target_count: int,
    scarcity_weight: float,
    method: str = "kmeans",
    random_state: int = 0,
) -> gpd.GeoDataFrame:
    """Basin-aware clustering of province-basin pieces into ``target_count`` regions.

    ``pieces`` is the overlay of GADM level-1 provinces with AWARE basins, with
    columns ``GID_0`` (country), ``prov`` (GADM province id), ``cf`` (basin
    scarcity) and geometry. Per-country region counts are allocated
    proportionally to area; each country is then partitioned (see
    ``cluster_country``) so every region is either contained in one province or a
    union of whole provinces. Basin scarcity separates regions within a province
    so a scarce sub-basin is not pooled with an abundant one.
    """
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    if "GID_0" not in pieces.columns:
        raise ValueError("Expected GID_0 column for country codes")

    proj = pieces.to_crs(6933)
    cent = proj.geometry.centroid
    pieces = pieces.assign(
        px=cent.x.to_numpy(), py=cent.y.to_numpy(), area=proj.geometry.area.to_numpy()
    )

    # Per-country region budget, proportional to area (>=1 per country). Both
    # inputs are taken over *all* pieces, which partition each province: one
    # piece per province would undercount the area of every province straddling
    # several basins, starving basin-fragmented countries of regions. The
    # capacity cap is the piece count, since a province can now be split into
    # as many regions as it has basin pieces.
    counts = pieces.groupby("GID_0").size()
    country_areas = _compute_country_geodesic_areas(pieces[["GID_0", "geometry"]])
    per_country = _allocate_per_country_targets_by_weight(
        country_areas, counts, target_count
    )

    cluster_ids = pd.Series(-1, index=pieces.index, dtype=int)
    next_cluster = 0
    for country, group in pieces.groupby("GID_0"):
        k = int(per_country.get(country, 0))
        if k <= 0:
            continue
        local = cluster_country(group, k, scarcity_weight, method, random_state)
        cluster_ids.loc[group.index] = local + next_cluster
        next_cluster += int(local.max()) + 1

    pieces = pieces.assign(_cluster=cluster_ids.astype(int))
    dissolved = pieces[["_cluster", "geometry"]].dissolve(by="_cluster", as_index=False)
    dissolved["region"] = [f"region{int(i):04d}" for i in dissolved["_cluster"]]
    rep_country = pieces.groupby("_cluster")["GID_0"].first()
    dissolved["country"] = dissolved["_cluster"].map(rep_country)
    dissolved = dissolved.set_index("region").drop(columns="_cluster")

    # Snap to the GeoJSON writer's coordinate grid, then repair. Order matters.
    # The raw dissolve leaves near-degenerate slivers that are valid in memory
    # but tip into self-intersection once the writer truncates coordinates to 7
    # decimals, so an in-memory validity check passes while the file on disk is
    # invalid. Snapping first makes the in-memory geometry exactly what gets
    # written, so repairing it afterwards actually holds. This matters because
    # exactextract calls into native code that *segfaults* on invalid geometry
    # rather than raising -- one bad region kills an unrelated downstream rule
    # with an empty log and no traceback.
    # buffer(0) first: set_precision raises on genuinely invalid input rather
    # than repairing it. Then snap, then repair again, since snapping can itself
    # introduce self-intersections.
    repaired = dissolved.geometry.buffer(0)
    snapped = shapely.set_precision(repaired.values, GEOJSON_PRECISION)
    dissolved["geometry"] = gpd.GeoSeries(
        snapped, index=dissolved.index, crs=dissolved.crs
    ).buffer(0)
    still_bad = ~dissolved.geometry.is_valid
    if still_bad.any():
        raise ValueError(
            f"{int(still_bad.sum())} region geometries are invalid after "
            "normalisation: " + ", ".join(map(str, dissolved.index[still_bad][:5]))
        )
    return dissolved


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    # GADM level 1 (state/province boundaries); geometries are pre-simplified.
    provinces = gpd.read_file(snakemake.input.world).rename(columns={"GID_1": "prov"})
    valid_mask = (
        (provinces["prov"] != "?")
        & provinces["prov"].notna()
        & (provinces["prov"] != "")
        & (provinces["prov"] != "NA")
    )
    provinces = provinces[valid_mask]
    if "GID_0" not in provinces.columns:
        raise ValueError("Expected GID_0 column with ISO3 country codes in GADM data")
    provinces = provinces[provinces["GID_0"].isin(list(snakemake.params.countries))]
    if provinces.crs is None:
        provinces = provinces.set_crs(4326, allow_override=True)

    # AWARE basin scarcity (agricultural CF); fill missing with the median.
    basins = gpd.read_file(
        snakemake.input.basins, layer="AWARE20_Native_CFs_geospatial"
    )
    basins["cf"] = pd.to_numeric(basins["CF_annual_agri"], errors="coerce")
    basins["cf"] = basins["cf"].fillna(basins["cf"].median())
    basins = basins.to_crs(provinces.crs)

    # Split provinces along basin boundaries: one piece per (province, basin).
    pieces = gpd.overlay(
        provinces[["GID_0", "prov", "geometry"]],
        basins[["cf", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )

    # Coastal slivers and small islands can fall outside every AWARE basin and
    # are dropped by the intersection. That is tolerable in aggregate but must
    # never silently remove a whole country from the model.
    missing = set(provinces["GID_0"]) - set(pieces["GID_0"])
    if missing:
        raise ValueError(
            "No AWARE basin overlaps any province of: "
            f"{', '.join(sorted(missing))}. Check the basin geopackage."
        )
    kept = _compute_country_geodesic_areas(pieces[["GID_0", "geometry"]]).sum()
    total = _compute_country_geodesic_areas(provinces[["GID_0", "geometry"]]).sum()
    logger.info(
        "Basin overlay: %d provinces -> %d province-basin pieces, "
        "%.2f%% of land area outside all basins",
        provinces["prov"].nunique(),
        len(pieces),
        100.0 * (1.0 - kept / total),
    )

    regions = cluster_regions(
        pieces,
        snakemake.params.n_regions,
        snakemake.params.basin_scarcity_weight,
        snakemake.params.cluster_method,
    )

    Path(snakemake.output[0]).parent.mkdir(parents=True, exist_ok=True)
    regions.to_file(snakemake.output[0], driver="GeoJSON")

    # The written file is what every downstream raster aggregation reads, so
    # validate it rather than the in-memory frame.
    written = gpd.read_file(snakemake.output[0])
    bad = ~written.geometry.is_valid
    if bad.any():
        raise ValueError(
            f"{int(bad.sum())} region geometries are invalid as written to "
            f"{snakemake.output[0]}; exactextract would segfault on them."
        )
    logger.info("Wrote %d regions, all geometries valid on disk", len(written))
