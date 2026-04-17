# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS, Geod
from sklearn.cluster import AgglomerativeClustering, KMeans

GEOD = Geod(ellps="WGS84")


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
            "cannot avoid cross-border clustering with this target."
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


def cluster_regions(
    gdf: gpd.GeoDataFrame,
    target_count: int,
    allow_cross_border: bool,
    method: str = "kmeans",
    random_state: int = 0,
) -> gpd.GeoDataFrame:
    """Cluster level-1 administrative regions into target_count clusters.

    Clustering is based on centroids in a projected CRS (EPSG:3857) for a
    reasonable Euclidean approximation. When cross-border clustering is not
    allowed, clustering is performed per country and the per-country targets
    are allocated proportionally to the number of base regions.
    """
    if target_count <= 0:
        raise ValueError("target_count must be positive")

    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)

    # Project to equal-area for reasonable Euclidean approximation
    gdf_proj = gdf.to_crs(6933)
    cent = gdf_proj.geometry.centroid
    coords = np.vstack([cent.x.values, cent.y.values]).T

    if allow_cross_border:
        labels = _cluster_coords(coords, target_count, method, random_state)
        # Create global cluster ids
        cluster_ids = pd.Series(labels, index=gdf.index).astype(int)
        gdf = gdf.assign(_cluster=cluster_ids)
    else:
        # Allocate targets per country and cluster within each
        if "GID_0" not in gdf.columns:
            raise ValueError(
                "Expected GID_0 column for country codes in GADM level 1 data"
            )

        counts = gdf.groupby("GID_0").size()
        # Compute geodesic area per country for proportional allocation
        country_areas = _compute_country_geodesic_areas(gdf[["GID_0", "geometry"]])
        per_country = _allocate_per_country_targets_by_weight(
            country_areas, counts, target_count
        )

        cluster_ids = pd.Series(index=gdf.index, dtype=int)
        next_cluster = 0
        for country, group in gdf.groupby("GID_0"):
            k = int(per_country.get(country, 0))
            idx = group.index.values
            if k <= 0:
                # No clusters allocated (can happen if country had 0 units)
                continue
            sub_coords = coords[gdf.index.get_indexer(idx)]
            labels = _cluster_coords(sub_coords, k, method, random_state)
            # Offset labels to keep them globally unique
            cluster_ids.loc[idx] = labels + next_cluster
            next_cluster += int(labels.max()) + 1

        gdf = gdf.assign(_cluster=cluster_ids.astype(int))

    # Dissolve polygons by cluster id
    # Keep only geometry and the minimal attributes needed to avoid duplicate columns.
    keep_cols = [c for c in ["_cluster", "geometry"] if c in gdf.columns]
    gdf_min = gdf[keep_cols].copy()
    dissolved = gdf_min.dissolve(by="_cluster", as_index=False)

    # Assign unique region identifiers
    dissolved["region"] = [f"region{int(i):04d}" for i in dissolved["_cluster"]]

    # Keep a representative country code if available, under a user-friendly name
    if "GID_0" in gdf.columns:
        # first country code within the cluster as representative
        rep_country = gdf.groupby("_cluster")["GID_0"].first()
        dissolved["country"] = dissolved["_cluster"].map(rep_country)

    dissolved = dissolved.set_index("region")
    dissolved = dissolved.drop(
        columns=[c for c in ["_cluster"] if c in dissolved.columns]
    )
    return dissolved


if __name__ == "__main__":
    # Use GADM level 1 (state/province boundaries). Geometries are pre-simplified.
    gdf = gpd.read_file(snakemake.input.world)

    # Filter out invalid regions
    gdf = gdf.rename({"GID_1": "region"}, axis=1)
    valid_mask = (
        (gdf["region"] != "?")
        & gdf["region"].notna()
        & (gdf["region"] != "")
        & (gdf["region"] != "NA")
    )
    gdf = gdf[valid_mask]

    gdf = gdf.set_index("region", drop=True)

    # Narrowing by configured countries
    if "GID_0" not in gdf.columns:
        raise ValueError("Expected GID_0 column with ISO3 country codes in GADM data")
    gdf = gdf[gdf["GID_0"].isin(list(snakemake.params.countries))]

    gdf = cluster_regions(
        gdf,
        snakemake.params.n_regions,
        snakemake.params.allow_cross_border,
        snakemake.params.cluster_method,
    )

    Path(snakemake.output[0]).parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(snakemake.output[0], driver="GeoJSON")
