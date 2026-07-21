"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Build region-level irrigation water availability and a convex water-scarcity
supply curve from the AWARE2.0 dataset (Seitfudem et al. 2025, WaterGAP2.2e).

AWARE2.0 supplies, per native basin and month:

- ``NatAvail`` (naturalised availability), ``EWR`` (environmental reserve),
  ``basin_area``, and ``AMD_final`` (availability minus demand, m3/m2/month)
  in ``AWARE20_Intermediate_Variables.xlsx``;
- the irrigation-sector water demand ``2019_agri_pHWC`` in
  ``AWARE20_Native_CFs.xlsx``;
- native-basin polygons in ``AWARE20_Native_CFs_geospatial.gpkg`` whose feature
  id equals ``Basin_ID``.

The published AWARE characterisation factor (CF) is marginal:
``CF = AMD_world_avg / AMD``, clipped to [0.1, 100] (and 100 where AMD <= 0).
That is only valid for small inventories. The model re-decides *all* irrigation,
so we reconstruct the non-marginal curve: as the model draws a volume ``V`` from
a basin's agricultural pool, the basin's AMD falls and the CF rises. Anchoring to
the published ``AMD_final`` (which already carries AWARE's hydrological
corrections) and adding back the agriculture the model re-decides gives, per
basin-month:

    pool = max(area * AMD_final + agri_pHWC, 0)        [m3/month]
    AMD0 = pool / area                                 [m3/m2/month, no-agriculture AMD]
    CF(x) = clip(AMD_world_avg / (AMD0 * (1 - x)), 0.1, 100)

where ``x`` in [0, 1] is the fraction of the pool drawn. At ``x`` corresponding
to AWARE's 2019 irrigation draw this reproduces the published CF. GLADE then
replaces the pool capacity with WaterGAP's surface-delivery envelope while
retaining the AWARE CF curve. The convex CF curve is discretised into
sub-segments (closed-form average CF per segment) and merged across basins into
a **per region-month** merit-order supply curve, binned into ``N_TIERS`` tiers.
The lowest-CF (most abundant) water is drawn first, so a plain LP reproduces the
convex integral with no integrality.

**Monthly resolution.** This script keeps the full monthly signal: it emits one
convex tier curve per region *and month* (12 curves per region). The temporal
resolution the model actually solves at is chosen downstream in
``compose_water_supply`` (``water.temporal_resolution``), which groups whole
months into equal periods and re-merges the monthly curves. Keeping this stage
month-resolved lets the temporal resolution change without re-running the
(expensive) AWARE basin overlay. No demand-based capping is applied here: the
seasonal bind (a month's surface use cannot exceed that month's availability) is
enforced by the LP once the period buses exist, not baked into an annual scalar.

Outputs (all keyed by model ``region``):

- ``monthly_region_water.csv``: agricultural pool per region-month (m3);
- ``region_growing_season_water.csv``: annual availability per region;
- ``region_water_tiers.csv``: ``region, month, tier, capacity_mm3, marginal_cf``
  -- the per-month convex water-supply curves consumed by ``compose_water_supply``;

All volumes are WaterGAP's: its regional totals are allocated directly to the
intersecting AWARE basins before the CF tiers are built. AWARE contributes the
scarcity (CF) curve. The eta_c consumption anchor comes from
``build_region_watergap.py`` on the same basis.
"""

import logging
from pathlib import Path

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd

from workflow.scripts.build_region_watergap import (
    _WGS84_WKT,
    compute_monthly_flux_raster,
)

logger = logging.getLogger(__name__)

MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# AWARE2.0 constants (Seitfudem et al. 2025).
AMD_WORLD_AVG = 0.02410  # m3 / m2 / month, world-average availability minus demand
CF_MIN = 0.1
CF_MAX = 100.0

# Discretisation of the convex per-basin scarcity curve. Each basin-month pool is
# split into N_SUBSEGMENTS sub-segments along the cumulative-draw axis; the merged
# region-month merit-order curve is binned into N_TIERS equal-volume tiers.
N_SUBSEGMENTS = 8
N_TIERS = 8

MM3_PER_M3 = 1e-6


# ---------------------------------------------------------------------------
# AWARE2.0 basin loading and basin -> region crosswalk.
# ---------------------------------------------------------------------------
def load_basin_pool(intermediate_path: str, native_cfs_path: str) -> pd.DataFrame:
    """Return per-basin agricultural pool and no-agriculture AMD.

    Index is ``Basin_ID``; columns are ``area`` (m2) plus, for each month,
    ``pool_{Mon}`` (m3/month) and ``amd0_{Mon}`` (m3/m2/month).
    """
    amd_final = pd.read_excel(intermediate_path, sheet_name="AMD_final").set_index(
        "Basin_ID"
    )[MONTHS]
    area = pd.read_excel(intermediate_path, sheet_name="basin_area").set_index(
        "Basin_ID"
    )["area"]
    agri = pd.read_excel(native_cfs_path, sheet_name="2019_agri_pHWC").set_index(
        "Basin_ID"
    )[MONTHS]

    basins = amd_final.index.intersection(area.index).intersection(agri.index)
    area = area.loc[basins]
    # Agricultural pool = current AMD headroom plus the irrigation demand the
    # model re-decides. Clip negatives (basins already over-allocated): no
    # agricultural water is available there.
    pool = (amd_final.loc[basins].mul(area, axis=0) + agri.loc[basins]).clip(lower=0.0)
    amd0 = pool.div(area, axis=0)

    out = pd.DataFrame(index=basins)
    out["area"] = area
    out[[f"pool_{m}" for m in MONTHS]] = pool.to_numpy()
    out[[f"amd0_{m}" for m in MONTHS]] = amd0.to_numpy()
    return out


def build_basin_region_cells(
    basins_path: str, regions: gpd.GeoDataFrame, basin_ids: pd.Index
) -> gpd.GeoDataFrame:
    """Return model-region intersections of the AWARE basins.

    The AWARE geospatial layer encodes ``Basin_ID`` as the feature id; only
    basins present in ``basin_ids`` (those with pool data) are retained. The
    ``share`` column is the intersection's area share of its native basin and
    is used to apportion AWARE's basin-level AMD pool. Its geometry supports a
    separate, direct overlay of WaterGAP surface delivery.
    """
    basins = gpd.read_file(
        basins_path,
        layer="AWARE20_Native_CFs_geospatial",
        columns=[],
        fid_as_index=True,
    )
    basins.index.name = "basin_id"
    basins = basins[basins.index.isin(basin_ids)].reset_index()

    area_crs = "EPSG:6933"
    basins_eq = basins.to_crs(area_crs)
    regions_eq = regions.to_crs(area_crs)

    basin_area = basins_eq.set_index("basin_id").geometry.area
    intersections = gpd.overlay(regions_eq, basins_eq, how="intersection")
    if intersections.empty:
        return gpd.GeoDataFrame(
            columns=["region", "basin_id", "share", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    intersections = intersections.dissolve(["region", "basin_id"], as_index=False)
    intersections["inter_area"] = intersections.geometry.area
    intersections["share"] = (
        intersections["inter_area"]
        / basin_area.loc[intersections["basin_id"]].to_numpy()
    )
    intersections = intersections[intersections["share"] > 1e-6]
    return intersections[["region", "basin_id", "share", "geometry"]].to_crs(
        "EPSG:4326"
    )


# ---------------------------------------------------------------------------
# Convex tier construction.
# ---------------------------------------------------------------------------
def _subsegment_cf_factors(n_subsegments: int) -> np.ndarray:
    """Average of ``1 / (1 - x)`` over each equal-width cumulative-draw segment.

    ``CF(x) = AMD_world_avg / (AMD0 * (1 - x))``, so the average CF of a segment
    is ``(AMD_world_avg / AMD0) * factor`` with ``factor`` the closed-form mean
    of ``1 / (1 - x)``: ``ln((1 - a) / (1 - b)) / (b - a)``. The final segment
    (b -> 1) is capped just below 1 to keep the integral finite; the CF clip to
    ``CF_MAX`` handles the divergence.
    """
    edges = np.linspace(0.0, 1.0, n_subsegments + 1)
    a = edges[:-1]
    b = np.minimum(edges[1:], 1.0 - 1e-9)
    return np.log((1.0 - a) / (1.0 - b)) / (b - a)


def build_region_month_tiers(
    long: pd.DataFrame, n_subsegments: int, n_tiers: int
) -> pd.DataFrame:
    """Merit-order convex water-supply tiers per (region, month).

    ``long`` holds one row per (region, month, basin) with columns ``region``,
    ``month``, ``volume`` (m3 of agricultural pool in that month) and ``amd0``
    (m3/m2/month). Each row is expanded into ``n_subsegments`` segments of the
    convex CF curve, merged per (region, month) in ascending-CF order, and
    binned into ``n_tiers`` equal-volume tiers.
    """
    factors = _subsegment_cf_factors(n_subsegments)

    long = long[long["volume"] > 0]
    if long.empty:
        return pd.DataFrame(
            columns=["region", "month", "tier", "capacity_mm3", "marginal_cf"]
        )

    amd0 = long["amd0"].to_numpy()[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        cf = np.where(amd0 > 0, AMD_WORLD_AVG / amd0 * factors[None, :], CF_MAX)
    cf = np.clip(cf, CF_MIN, CF_MAX)
    seg_volume = (long["volume"].to_numpy()[:, None] / n_subsegments) * np.ones_like(cf)

    seg = pd.DataFrame(
        {
            "region": np.repeat(long["region"].to_numpy(), n_subsegments),
            "month": np.repeat(long["month"].to_numpy(), n_subsegments),
            "cf": cf.ravel(),
            "volume": seg_volume.ravel(),
        }
    )

    tiers = []
    for (region, month), group in seg.groupby(["region", "month"], sort=False):
        group = group.sort_values("cf")
        volume = group["volume"].to_numpy()
        cfs = group["cf"].to_numpy()
        total = volume.sum()
        if total <= 0:
            continue
        bin_size = total / n_tiers
        cum_start = np.cumsum(volume) - volume
        tier_idx = np.minimum((cum_start / bin_size).astype(int), n_tiers - 1)
        for tier in range(n_tiers):
            mask = tier_idx == tier
            cap = volume[mask].sum()
            if cap <= 0:
                continue
            marginal_cf = float(np.average(cfs[mask], weights=volume[mask]))
            tiers.append(
                {
                    "region": region,
                    "month": int(month),
                    "tier": tier,
                    "capacity_mm3": cap * MM3_PER_M3,
                    "marginal_cf": marginal_cf,
                }
            )

    if not tiers:
        return pd.DataFrame(
            columns=["region", "month", "tier", "capacity_mm3", "marginal_cf"]
        )
    return (
        pd.DataFrame(tiers)
        .sort_values(["region", "month", "tier"])
        .reset_index(drop=True)
    )


def aggregate_watergap_surface_by_basin(
    pirruse_path: str,
    pirrusegw_path: str,
    continental_area_path: str,
    basin_cells: gpd.GeoDataFrame,
    reference_start: int,
    reference_end: int,
) -> pd.Series:
    """Aggregate WaterGAP surface irrigation delivery to region-basin cells.

    Surface delivery is ``max(pirruse - pirrusegw, 0)`` on WaterGAP's native
    grid. Aggregating it directly to the AWARE basin intersections preserves
    WaterGAP's spatial allocation within each model region before the AWARE CF
    tiers are built.
    """
    irr_total, lat, lon = compute_monthly_flux_raster(
        pirruse_path,
        "pirruse",
        continental_area_path,
        reference_start,
        reference_end,
    )
    irr_gw, gw_lat, gw_lon = compute_monthly_flux_raster(
        pirrusegw_path,
        "pirrusegw",
        continental_area_path,
        reference_start,
        reference_end,
    )
    if not np.array_equal(lat, gw_lat) or not np.array_equal(lon, gw_lon):
        raise ValueError("WaterGAP irrigation fields do not share a grid")

    surface = np.clip(irr_total - irr_gw, 0.0, None)
    res = float(np.abs(np.diff(np.sort(np.unique(lon)))).min())
    if lat[0] < lat[-1]:
        surface = np.flip(surface, axis=1)

    sources = [
        NumPyRasterSource(
            surface[month],
            xmin=float(lon.min()) - res / 2,
            xmax=float(lon.max()) + res / 2,
            ymin=float(lat.min()) - res / 2,
            ymax=float(lat.max()) + res / 2,
            srs_wkt=_WGS84_WKT,
            name=f"m{month + 1}",
        )
        for month in range(12)
    ]
    aggregated = exact_extract(
        sources,
        basin_cells,
        ["sum"],
        include_cols=["region", "basin_id"],
        output="pandas",
    )
    long = aggregated.melt(
        id_vars=["region", "basin_id"],
        value_vars=[f"m{month}_sum" for month in range(1, 13)],
        var_name="month",
        value_name="surface_m3",
    )
    long["month"] = long["month"].str.extract(r"m(\d+)_sum").astype(int)
    return long.set_index(["region", "basin_id", "month"])["surface_m3"]


def scale_pool_to_watergap_surface(
    cells: pd.DataFrame,
    surface_m3: pd.Series,
    basin_surface_m3: pd.Series,
) -> tuple[pd.DataFrame, pd.Series]:
    """Rescale AWARE pools to WaterGAP surface delivery by region-basin-month.

    AWARE ``availability`` is basin river discharge, which counts through-flow
    discharge as divertible and hugely overstates the surface water accessible
    to irrigation in groundwater-dependent basins (e.g. the Ogallala: an AWARE
    pool ~100x the surface WaterGAP's detailed allocation actually supplies).
    Its *monthly shape* is unregulated discharge timing, while WaterGAP's
    ``histsoc`` runs operate every GRanD reservoir >= 0.5 km3: the monthly
    profile of its irrigation surface consumption (``pirruse - pirrusegw``) is
    regulated, demand-timed delivery. We keep AWARE's scarcity structure -- the
    per-basin CF curve (a function of ``amd0``, not volume), but replace the
    old area-weighted basin split with WaterGAP's direct grid-cell overlay. The
    regional WaterGAP total remains the conservation anchor, while the direct
    overlay supplies its within-region basin shares. Delivery without a mapped
    AWARE basin is retained on an explicit ceiling-CF tier.

    ``surface_m3`` is indexed by ``(region, month)`` and ``basin_surface_m3``
    by ``(region, basin_id, month)``. The returned factors use the latter index.
    """
    region_key = ["region", "month"]
    basin_key = ["region", "basin_id", "month"]
    cell_index = pd.MultiIndex.from_frame(cells[basin_key])
    region_index = pd.MultiIndex.from_frame(cells[region_key])
    pool = cells["region_pool"].to_numpy()
    direct = basin_surface_m3.reindex(cell_index, fill_value=0.0).to_numpy()

    direct_total = (
        pd.Series(direct, index=region_index).groupby(level=[0, 1]).transform("sum")
    )
    regional_surface = surface_m3.reindex(region_index, fill_value=0.0).to_numpy()

    shares = np.zeros_like(pool)
    direct_available = direct_total.to_numpy() > 0.0
    shares[direct_available] = (
        direct[direct_available] / direct_total.to_numpy()[direct_available]
    )
    target = regional_surface * shares
    factor = np.divide(target, pool, out=np.zeros_like(target), where=pool > 0.0)
    scaled = cells.copy()
    scaled["region_pool"] = target
    scaled.loc[(pool <= 0.0) & (target > 0.0), "amd0"] = 0.0

    # AWARE's zero agricultural pool means that its AMD is non-positive after
    # re-adding irrigation, so any WaterGAP delivery mapped there carries the
    # method's maximum CF. If no WaterGAP basin intersection receives delivery
    # despite a positive regional anchor, retain the volume on an explicit
    # ceiling-CF tier instead of inventing a lower-scarcity basin allocation.
    unmapped = (
        pd.Series(
            regional_surface[~direct_available], index=region_index[~direct_available]
        )
        .groupby(level=[0, 1])
        .first()
    )
    unmapped = unmapped[unmapped > 0.0]
    if not unmapped.empty:
        ceiling = unmapped.rename("region_pool").reset_index()
        ceiling["basin_id"] = -1
        ceiling["amd0"] = 0.0
        scaled = pd.concat(
            [scaled, ceiling[["region", "basin_id", "month", "amd0", "region_pool"]]],
            ignore_index=True,
        )
        logger.warning(
            "WaterGAP delivery lacks an AWARE basin intersection in %d "
            "region-months: %.0f Mm3 assigned the AWARE ceiling CF",
            len(unmapped),
            float(unmapped.sum()) * MM3_PER_M3,
        )

    return scaled, pd.Series(factor, index=cell_index)


if __name__ == "__main__":
    intermediate_path: str = snakemake.input.intermediate  # type: ignore[name-defined]
    native_cfs_path: str = snakemake.input.native_cfs  # type: ignore[name-defined]
    basins_path: str = snakemake.input.basins  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]

    basin_pool = load_basin_pool(intermediate_path, native_cfs_path)

    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]]
    regions_list = sorted(regions_gdf["region"].tolist())

    basin_cells = build_basin_region_cells(basins_path, regions_gdf, basin_pool.index)
    shares = basin_cells[["region", "basin_id", "share"]]

    # Long table: one row per (region, basin, month) with agricultural pool and
    # no-agriculture AMD. Shares apportion each basin to its overlapping regions.
    pool_long = (
        basin_pool[[f"pool_{m}" for m in MONTHS]]
        .rename(columns={f"pool_{m}": i + 1 for i, m in enumerate(MONTHS)})
        .rename_axis("basin_id")
        .reset_index()
        .melt(id_vars="basin_id", var_name="month", value_name="basin_pool")
    )
    amd0_long = (
        basin_pool[[f"amd0_{m}" for m in MONTHS]]
        .rename(columns={f"amd0_{m}": i + 1 for i, m in enumerate(MONTHS)})
        .rename_axis("basin_id")
        .reset_index()
        .melt(id_vars="basin_id", var_name="month", value_name="amd0")
    )
    cells = shares.merge(pool_long, on="basin_id").merge(
        amd0_long, on=["basin_id", "month"]
    )
    cells["region_pool"] = cells["share"] * cells["basin_pool"]

    # Replace AWARE's basin-discharge availability volume and timing with
    # WaterGAP's monthly irrigation surface consumption. WaterGAP determines
    # both the regional surface envelope and its basin allocation, so the CF
    # tiers retain AWARE's scarcity structure without using an area-share proxy
    # for delivery within a model region.
    surface_m3 = (
        pd.read_csv(snakemake.input.watergap_surface).set_index(  # type: ignore[name-defined]
            ["region", "month"]
        )["surface_consumption_mm3"]
        / MM3_PER_M3
    )
    basin_surface_m3 = aggregate_watergap_surface_by_basin(
        snakemake.input.watergap_pirruse,  # type: ignore[name-defined]
        snakemake.input.watergap_pirrusegw,  # type: ignore[name-defined]
        snakemake.input.watergap_continentalarea,  # type: ignore[name-defined]
        basin_cells,
        int(snakemake.params.surface_start),  # type: ignore[name-defined]
        int(snakemake.params.surface_end),  # type: ignore[name-defined]
    )
    cells, _ = scale_pool_to_watergap_surface(cells, surface_m3, basin_surface_m3)

    # Monthly region pool (m3) -- full agricultural availability per region-month.
    monthly_region = (
        cells.groupby(["region", "month"], as_index=False)["region_pool"]
        .sum()
        .rename(columns={"region_pool": "water_available_m3"})
        .sort_values(["region", "month"])
    )

    # Per (region, month) convex tiers from the full monthly pool. The seasonal
    # bind is applied downstream by the LP's period buses, not here.
    tier_input = cells.rename(columns={"region_pool": "volume"})[
        ["region", "month", "volume", "amd0"]
    ]
    tiers = build_region_month_tiers(tier_input, N_SUBSEGMENTS, N_TIERS)

    annual = (
        monthly_region.groupby("region")["water_available_m3"]
        .sum()
        .reindex(regions_list, fill_value=0.0)
    )
    region_growing = pd.DataFrame(
        {
            "region": regions_list,
            "annual_water_available_m3": annual.to_numpy(),
        }
    )

    monthly_out = Path(snakemake.output.monthly_region)  # type: ignore[name-defined]
    monthly_out.parent.mkdir(parents=True, exist_ok=True)
    monthly_region.to_csv(monthly_out, index=False)

    growing_out = Path(snakemake.output.region_growing)  # type: ignore[name-defined]
    growing_out.parent.mkdir(parents=True, exist_ok=True)
    region_growing.to_csv(growing_out, index=False)

    tiers_out = Path(snakemake.output.tiers)  # type: ignore[name-defined]
    tiers_out.parent.mkdir(parents=True, exist_ok=True)
    tiers.to_csv(tiers_out, index=False)
