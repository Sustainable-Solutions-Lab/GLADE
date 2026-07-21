"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Build per (region, crop) monthly irrigated water-demand shares from the
MIRCA-OS 2015 monthly growing-area grids, retimed to WaterGAP's monthly
irrigation requirement.

The water supply envelope is WaterGAP's reservoir-regulated monthly delivery,
but crop water demand was placed in the year by GAEZ growing seasons -- the
potential, yield-maximising calendar, which systematically disagrees with the
observed cropping calendar in the major irrigated systems (Indus, Nile,
Gangetic plain). That demand-calendar mismatch strands deliverable surface
water in months the model does not demand it and mines groundwater in the
months it does.

MIRCA-OS publishes the observed calendar spatialised: monthly growing-area
grids per crop and sub-crop (5 arcmin, ha per cell-month, planting to
maturity). The **2015** vintage is used deliberately: the 2020 crop calendar
misplaces the entire northwest-India wheat belt (~16.5 Mha) into the monsoon
window, and MIRCA2000 v1.1 splits the same belt 50/50 between monsoon and
rabi windows; the 2015 calendar carries the correct rabi timing (verified
against Sacks et al. 2010 and USDA FAS calendars). Calendars are
climatological, so the 2015 timing is valid for the 2020 baseline areas.

Growing-area months are not requirement months, however: a crop's net
irrigation requirement is modulated by evapotranspiration minus effective
precipitation within its season (it collapses during the monsoon and peaks in
the dry shoulder months), while the growing-area profile weights every month
of the season equally -- including dormant winter-wheat months and rain-fed
monsoon months. WaterGAP's ``pirruse`` carries the requirement timing on the
same simulation and basis as the supply envelope. The two are combined by
iterative proportional fitting (``retime_shares_to_demand``): per region, the
crop x month prior (MIRCA area shares weighted by each crop's annual
irrigation water) is scaled so that region-month column totals match the
WaterGAP monthly requirement shape while per-crop annual totals and the
structural zeros of the observed calendar are preserved exactly. Wheat can
shift within its rabi window but never into the monsoon.

Sub-crops of the same class (``Wheat1`` + ``Wheat2``, ``Rice1..3``) are
summed: all cycles of a crop feed the same regional water buses, so only the
summed monthly profile matters. The summed grids are aggregated to model
regions (coverage-weighted, exact_extract) and normalised into monthly
shares. Only irrigated grids are used -- rainfed links carry no water.
A calendar-only supplement mapping adds MIRCA classes excluded from the
multi-cropping concordance (sugar cane, pulses, fodder) so that the large
irrigators they represent are placed by observed timing instead of the flat
GAEZ fallback; a class mapping to several GLADE crops gives each the same
profile with the class area split evenly for weighting.

Output ``mirca_crop_calendar.csv``: ``region, crop, month, share, area_ha``
with one row per (region, mapped GLADE crop, month) where the region grows
the crop under irrigation in MIRCA-OS. ``share`` is the retimed demand share
(summing to 1 over the year); ``area_ha`` is the raw MIRCA-OS growing area
(provenance, not rescaled). Consumers (single-crop links in
``build_model.crops`` and the multi-cropping cycle split in
``build_multi_cropping``) fall back to the GAEZ growing season where a
(region, crop) is absent.
"""

import logging
from pathlib import Path

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = ["region", "crop", "month", "share", "area_ha"]

# exactextract's NumPyRasterSource retains, at the C++ level, the numpy array it
# is handed, and that memory is never reclaimed even after the source is GC'd.
# Because this module wraps a fresh full-grid stack for every MIRCA crop, that
# leak would accumulate to many GB. Reusing a single buffer per grid shape means
# only one array is ever retained -- copying the transient stack into the buffer
# lets the transient be freed normally.
_EXTRACT_BUFFERS: dict[tuple[int, ...], np.ndarray] = {}


def get_extract_buffer(shape: tuple[int, ...]) -> np.ndarray:
    """Return the reused float64 exact_extract buffer for ``shape``."""
    buffer = _EXTRACT_BUFFERS.get(shape)
    if buffer is None:
        buffer = np.empty(shape, dtype=np.float64)
        _EXTRACT_BUFFERS[shape] = buffer
    return buffer


def aggregate_raster_stack_by_region(
    stack: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    crs_wkt: str | None,
    stat: str = "sum",
) -> pd.DataFrame:
    """Aggregate a (band, H, W) raster stack by regions in one exact_extract call.

    All bands share one grid, so exact_extract computes each region's cell
    coverage fractions once and reuses them across bands -- roughly a
    band-count speedup over per-band calls. The stack is copied into a reused
    per-shape buffer (see ``_EXTRACT_BUFFERS``).

    Returns a DataFrame with ``region`` and one ``b{i}_{stat}`` column per band.
    """
    buffer = get_extract_buffer(stack.shape)
    if stack is not buffer:
        buffer[:] = stack
    sources = [
        NumPyRasterSource(
            buffer[i],
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            nodata=np.nan,
            name=f"b{i}",
            srs_wkt=crs_wkt,
        )
        for i in range(stack.shape[0])
    ]
    return exact_extract(
        sources,
        regions_gdf,
        [stat],
        include_cols=["region"],
        output="pandas",
    )


def load_crop_monthly_area(paths: list[str]) -> tuple[np.ndarray, tuple]:
    """Sum a crop's sub-crop monthly growing-area grids into one (12, H, W).

    Returns the summed array (ha per cell-month, NaN treated as zero) and the
    grid bounds ``(xmin, ymin, xmax, ymax)``. All grids must share the MIRCA-OS
    5-arcmin north-up layout. The sum is accumulated directly in the shared
    exact_extract buffer to avoid holding a second full-grid copy.
    """
    total = None
    bounds = None
    for path in paths:
        ds = xr.open_dataset(path)
        da = ds["harvested_area"]  # (month, latitude, longitude), ha
        lat = ds["latitude"].values.astype(float)
        lon = ds["longitude"].values.astype(float)
        if lat[0] < lat[-1]:
            raise ValueError(f"Expected north-up latitude axis in {path}")
        arr = np.nan_to_num(da.values, nan=0.0, copy=False)
        ds.close()
        if total is None:
            total = get_extract_buffer(arr.shape)
            total[:] = arr
            res = float(abs(lon[1] - lon[0]))
            bounds = (
                float(lon.min()) - res / 2,
                float(lat.min()) - res / 2,
                float(lon.max()) + res / 2,
                float(lat.max()) + res / 2,
            )
        else:
            if arr.shape != total.shape:
                raise ValueError(f"Grid shape mismatch in {path}")
            total += arr
    return total, bounds


def monthly_shares(
    monthly_area: pd.DataFrame, min_area_ha: float = 1.0
) -> pd.DataFrame:
    """Normalise per-(region, crop) monthly areas into demand shares.

    ``monthly_area`` has columns ``region, crop, month, area_ha``. Regions with
    an annual total below ``min_area_ha`` are dropped (no reliable calendar
    signal); the remaining shares sum to 1 over the 12 months.
    """
    annual = monthly_area.groupby(["region", "crop"])["area_ha"].transform("sum")
    out = monthly_area[annual >= min_area_ha].copy()
    out["share"] = out["area_ha"] / annual[annual >= min_area_ha]
    return out[OUTPUT_COLUMNS].sort_values(["crop", "region", "month"])


def water_weights(yield_paths: dict[str, str], crops: set[str]) -> pd.DataFrame:
    """Per-(region, crop) annual irrigation water requirement rates (m3/ha).

    Reads the irrigated GAEZ yield tables (``{crop}_i.csv``) for the requested
    crops and returns the suitable-area-weighted mean of
    ``water_requirement_m3_per_ha`` over resource classes, as a DataFrame with
    columns ``region, crop, req_m3_per_ha``. Crops without an irrigated yield
    table are absent (they build no irrigated links, so they carry no weight).
    """
    frames = []
    for crop in sorted(crops & set(yield_paths)):
        df = pd.read_csv(yield_paths[crop])
        wide = df.pivot_table(
            index=["region", "resource_class"], columns="variable", values="value"
        )
        if {"water_requirement_m3_per_ha", "suitable_area"} - set(wide.columns):
            continue
        wide = wide.reset_index()
        area = wide["suitable_area"].fillna(0.0)
        req = wide["water_requirement_m3_per_ha"].fillna(0.0)
        grouped = (
            pd.DataFrame({"region": wide["region"], "num": req * area, "den": area})
            .groupby("region")
            .sum()
        )
        rate = (grouped["num"] / grouped["den"].replace(0.0, np.nan)).dropna()
        frames.append(rate.rename("req_m3_per_ha").reset_index().assign(crop=crop))
    if not frames:
        return pd.DataFrame(columns=["region", "crop", "req_m3_per_ha"])
    return pd.concat(frames, ignore_index=True)


def retime_shares_to_demand(
    shares: pd.DataFrame,
    weights: pd.Series,
    demand: pd.DataFrame,
    n_iter: int = 200,
    tol: float = 1e-6,
    min_demand_mm3: float = 1.0,
) -> pd.DataFrame:
    """Retime per-(region, crop) monthly shares to a region-month demand shape.

    Iterative proportional fitting on each region's crop x month demand
    matrix: the prior is ``share * weight`` (MIRCA growing-area placement
    scaled by each crop's annual irrigation water), the row marginals are the
    crop weights (annual totals preserved), and the column marginals are the
    region's WaterGAP monthly requirement shape scaled to the same total.
    Multiplicative updates preserve structural zeros, so demand moves within
    each crop's observed season, never outside it. Months WaterGAP serves that
    no crop grows in are left unmet (the column scaling skips empty columns);
    the final row normalisation keeps every profile summing to exactly 1.

    ``shares``: columns ``region, crop, month, share`` (full 12-month
    profiles). ``weights``: (region, crop)-indexed annual water volumes.
    ``demand``: columns ``region, month, irrigation_consumption_mm3``. Rows
    without a positive weight, and regions whose annual demand is below
    ``min_demand_mm3`` (no reliable requirement signal), pass through
    unchanged. Returns ``shares`` with the ``share`` column retimed.
    """
    wide = shares.pivot_table(
        index=["region", "crop"], columns="month", values="share"
    ).reindex(columns=range(1, 13), fill_value=0.0)

    dem = demand.pivot_table(
        index="region", columns="month", values="irrigation_consumption_mm3"
    ).reindex(columns=range(1, 13), fill_value=0.0)
    dem_total = dem.sum(axis=1)
    dem_shape = dem[dem_total >= min_demand_mm3].div(
        dem_total[dem_total >= min_demand_mm3], axis=0
    )

    w = weights.reindex(wide.index).fillna(0.0)
    regions = wide.index.get_level_values("region")
    active = (w > 0) & regions.isin(dem_shape.index)
    if not active.any():
        return shares

    prior = wide[active].mul(w[active], axis=0)
    w_act = w[active]
    reg_act = prior.index.get_level_values("region")
    # Column targets: the region's demand shape scaled to the total prior
    # weight of the participating crops.
    totals = w_act.groupby(reg_act).sum()
    target = dem_shape.reindex(totals.index).mul(totals, axis=0)
    target_rows = target.loc[reg_act].set_axis(prior.index)

    for _ in range(n_iter):
        colsum = prior.groupby(reg_act).transform("sum")
        prior *= (target_rows / colsum).where(colsum > 0, 1.0).to_numpy()
        rowsum = prior.sum(axis=1)
        prior = prior.mul((w_act / rowsum).where(rowsum > 0, 1.0), axis=0)
        col_err = (prior.groupby(reg_act).sum() - target).abs().to_numpy()
        if col_err.max() <= tol * max(totals.max(), 1.0):
            break

    # Profiles whose season does not intersect the region's demand months are
    # zeroed entirely by the column scaling (structurally infeasible to
    # retime); they keep their original observed shares.
    rowsum = prior.sum(axis=1)
    retimed = prior.div(rowsum.where(rowsum > 0, 1.0), axis=0)
    dead = rowsum <= 0
    if dead.any():
        retimed[dead] = wide[active][dead]
        logger.info(
            "%d (region, crop) profiles do not overlap the demand months; "
            "kept their observed shares",
            int(dead.sum()),
        )
    wide.loc[active] = retimed

    out = shares.copy()
    lookup = wide.stack().rename("share")
    out["share"] = lookup.reindex(
        pd.MultiIndex.from_arrays(
            [out["region"], out["crop"], out["month"]],
            names=["region", "crop", "month"],
        )
    ).to_numpy()
    if out["share"].isna().any():
        raise ValueError("Retiming lost shares for some (region, crop, month) rows")
    return out


if __name__ == "__main__":
    inp = dict(snakemake.input.items())  # type: ignore[name-defined]
    regions_path = inp.pop("regions")
    mapping_path = inp.pop("mapping")
    supplement_path = inp.pop("supplement")
    demand_path = inp.pop("demand")
    yield_files = snakemake.input.crop_yields  # type: ignore[name-defined]
    inp.pop("crop_yields", None)
    output = Path(snakemake.output[0])  # type: ignore[name-defined]

    # Remaining inputs are the monthly irrigated grids, keyed "nc_{label}"
    # (label = MIRCA base crop + optional sub-crop digit, e.g. "nc_Wheat1").
    nc_paths = {
        key[len("nc_") :]: path for key, path in inp.items() if key.startswith("nc_")
    }

    mapping = pd.concat(
        [
            pd.read_csv(mapping_path, comment="#"),
            pd.read_csv(supplement_path, comment="#"),
        ],
        ignore_index=True,
    )
    mapping = mapping[mapping["glade_crop"].notna() & (mapping["glade_crop"] != "")]
    # A MIRCA class mapping to several GLADE crops splits its area evenly
    # between them for weighting (the monthly profile is shared).
    n_targets = mapping.groupby("mirca_crop")["glade_crop"].transform("count")

    # Group sub-crop labels under their MIRCA base crop by stripping the
    # trailing cycle digit ("Wheat1" -> "Wheat").
    label_base = {label: label.rstrip("0123456789").strip() for label in nc_paths}

    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]].reset_index()

    base_area: dict[str, pd.DataFrame] = {}
    records = []
    for row, n_tgt in zip(mapping.itertuples(), n_targets, strict=True):
        labels = [lb for lb, base in label_base.items() if base == row.mirca_crop]
        if not labels:
            raise KeyError(
                f"No monthly MIRCA-OS grids supplied for mapped crop "
                f"'{row.mirca_crop}' (GLADE '{row.glade_crop}')"
            )
        if row.mirca_crop not in base_area:
            monthly, (xmin, ymin, xmax, ymax) = load_crop_monthly_area(
                [nc_paths[lb] for lb in labels]
            )
            stats = aggregate_raster_stack_by_region(
                monthly, regions_gdf, xmin, ymin, xmax, ymax, None
            )
            long = stats.melt(id_vars="region", var_name="band", value_name="area_ha")
            long["month"] = (
                long["band"].str.extract(r"b(\d+)_", expand=False).astype(int) + 1
            )
            long = long[long["area_ha"].fillna(0.0) > 0.0]
            base_area[row.mirca_crop] = long[["region", "month", "area_ha"]]
            logger.info(
                "Aggregated MIRCA-OS 2015 irrigated calendar for %s (<- %s)",
                row.mirca_crop,
                " + ".join(sorted(labels)),
            )
        crop_frame = base_area[row.mirca_crop].assign(
            crop=row.glade_crop, weight_divisor=float(n_tgt)
        )
        records.append(
            crop_frame[["region", "crop", "month", "area_ha", "weight_divisor"]]
        )

    monthly_area = (
        pd.concat(records, ignore_index=True)
        if records
        else pd.DataFrame(
            columns=["region", "crop", "month", "area_ha", "weight_divisor"]
        )
    )
    # Complete each (region, crop) to all 12 months (zero growing area in the
    # missing months) so shares are a full monthly profile.
    if not monthly_area.empty:
        divisor = monthly_area.groupby(["region", "crop"])["weight_divisor"].first()
        full = (
            monthly_area.set_index(["region", "crop", "month"])["area_ha"]
            .unstack("month")
            .reindex(columns=range(1, 13), fill_value=0.0)
            .fillna(0.0)
            .stack()
            .rename("area_ha")
            .reset_index()
        )
        shares = monthly_shares(full)

        # Retime the growing-area placement to WaterGAP's monthly requirement
        # shape: weight each crop by its annual irrigation water (MIRCA area x
        # GAEZ requirement rate, class area split between shared targets).
        req = water_weights(
            {
                Path(p).stem[: -len("_i")]: p
                for p in yield_files
                if Path(p).stem.endswith("_i")
            },
            set(shares["crop"].unique()),
        )
        annual_area = full.groupby(["region", "crop"])["area_ha"].sum()
        req_rate = req.set_index(["region", "crop"])["req_m3_per_ha"]
        weights = (annual_area * req_rate.reindex(annual_area.index)).fillna(
            0.0
        ) / divisor.reindex(annual_area.index).fillna(1.0)

        demand = pd.read_csv(demand_path)
        shares = retime_shares_to_demand(shares, weights, demand)
        logger.info(
            "Retimed %d (region, crop) profiles (of %d) to the WaterGAP "
            "requirement shape",
            int((weights > 0).sum()),
            len(annual_area),
        )
    else:
        shares = pd.DataFrame(columns=OUTPUT_COLUMNS)

    output.parent.mkdir(parents=True, exist_ok=True)
    shares[OUTPUT_COLUMNS].to_csv(output, index=False)
