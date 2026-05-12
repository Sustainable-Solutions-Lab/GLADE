"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Build crop yield and harvested-area tables from CROPGRIDS + FAOSTAT for
crops listed in ``config["cropgrids_crops"]``.

Crops handled here bypass the GAEZ pipeline entirely:

* harvested_area / suitable_area
    Aggregated from CROPGRIDS' per-crop 0.05° ``harvarea`` raster onto
    (region, resource_class) cells. ``suitable_area`` is set equal to
    ``harvested_area * config["cropgrids"]["suitable_area_expansion"]`` so
    the crop is locked onto its current spatial footprint (no GAEZ
    suitability available).

* yield (DM, t/ha)
    Per-country FAOSTAT QCL yield (element 5419, hg/ha, fresh weight),
    averaged over the configured ``costs.averaging_period`` window for
    stability, converted to dry matter via ``crop_moisture_content``, and
    broadcast to every (region, resource_class) cell within the country.
    Cells with no harvested area get no yield row (they would be filtered
    out downstream anyway, since ``suitable_area = 0``).

Output CSVs match the schema of ``build_crop_yields.py`` /
``build_harvested_area.py`` so downstream loaders need no special-casing.
``water_requirement_m3_per_ha`` and ``growing_season_*`` columns are
omitted; cropgrids crops are rainfed-only and excluded from
``multiple_cropping``.
"""

import logging
from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features as rfeatures
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
import xarray as xr

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


CROPGRIDS_TRANSFORM = from_origin(-180.0, 90.0, 0.05, 0.05)
CROPGRIDS_HEIGHT = 3600
CROPGRIDS_WIDTH = 7200
CROPGRIDS_CRS = CRS.from_epsg(4326)

QCL_YIELD_ELEMENT_CODE = 5412  # Yield, kg/ha (fresh weight)
KG_PER_HA_TO_T_PER_HA = 1e-3  # 1 kg = 0.001 t


def _load_cropgrids_harvarea(nc_path: Path) -> np.ndarray:
    """Return harvested area (ha) per 0.05° cell as a 2D float32 array."""
    if not nc_path.exists():
        raise FileNotFoundError(f"CROPGRIDS NetCDF missing: {nc_path}")
    with rasterio.open(f"NETCDF:{nc_path}:harvarea") as src:
        arr = src.read(1, masked=True).filled(0.0).astype(np.float32)
    if arr.shape != (CROPGRIDS_HEIGHT, CROPGRIDS_WIDTH):
        raise ValueError(
            f"Unexpected CROPGRIDS shape {arr.shape} for {nc_path}; "
            f"expected {(CROPGRIDS_HEIGHT, CROPGRIDS_WIDTH)}"
        )
    return arr


def _reproject_classes_to_cropgrids(classes_ds: xr.Dataset) -> np.ndarray:
    """Resample resource_class labels onto the CROPGRIDS grid (nearest).

    The class raster is at GAEZ 5-arcmin resolution (~0.083°); CROPGRIDS is
    at 0.05°. Each CROPGRIDS cell maps to one GAEZ cell, so nearest-neighbour
    is exact-enough — preserving the categorical class label without
    introducing spurious blends.
    """
    src_arr = classes_ds["resource_class"].values.astype(np.int16)
    src_transform = Affine.from_gdal(*classes_ds.attrs["transform"])
    src_crs_wkt = classes_ds.attrs.get("crs_wkt") or classes_ds.attrs.get("crs")
    src_crs = CRS.from_wkt(src_crs_wkt) if src_crs_wkt else CROPGRIDS_CRS

    dst = np.full((CROPGRIDS_HEIGHT, CROPGRIDS_WIDTH), -1, dtype=np.int16)
    reproject(
        source=src_arr,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=CROPGRIDS_TRANSFORM,
        dst_crs=CROPGRIDS_CRS,
        resampling=Resampling.nearest,
        src_nodata=-1,
        dst_nodata=-1,
    )
    return dst


def _rasterize_regions(regions_gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Rasterize region polygons onto the CROPGRIDS grid (int region id, -1 outside)."""
    shapes = [(geom, idx) for idx, geom in enumerate(regions_gdf.geometry)]
    return rfeatures.rasterize(
        shapes,
        out_shape=(CROPGRIDS_HEIGHT, CROPGRIDS_WIDTH),
        transform=CROPGRIDS_TRANSFORM,
        fill=-1,
        dtype=np.int32,
    )


def _aggregate_harvarea(
    harvarea: np.ndarray, region_ids: np.ndarray, class_labels: np.ndarray
) -> pd.DataFrame:
    """Sum harvested area per (region_id, resource_class)."""
    valid = (region_ids >= 0) & (class_labels >= 0) & (harvarea > 0)
    if not np.any(valid):
        return pd.DataFrame(columns=["region_id", "resource_class", "harvested_area"])
    df = pd.DataFrame(
        {
            "region_id": region_ids[valid].astype(np.int32),
            "resource_class": class_labels[valid].astype(np.int16),
            "harvested_area": harvarea[valid].astype(np.float64),
        }
    )
    return df.groupby(["region_id", "resource_class"], as_index=False)[
        "harvested_area"
    ].sum()


def _country_yield_t_per_ha_dm(
    qcl_csv: Path,
    m49_codes_csv: Path,
    crop: str,
    qcl_item_code: int,
    countries: list[str],
    years: list[int],
    moisture_fraction: float,
) -> pd.Series:
    """Per-country FAOSTAT yield averaged over *years*, converted to DM t/ha.

    Missing countries fall back to the global production-weighted mean
    yield over *years*; we cannot drop them silently because the crop
    might genuinely be grown there (CROPGRIDS has harvested area in
    countries that FAOSTAT under-reports).
    """
    bulk = load_bulk(qcl_csv)
    m49_to_iso3 = load_m49_to_iso3(m49_codes_csv)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    df = filter_bulk(
        bulk,
        element_codes=[QCL_YIELD_ELEMENT_CODE],
        item_codes=[qcl_item_code],
        years=years,
    )
    df = df.dropna(subset=["Value"])
    if df.empty:
        raise RuntimeError(
            f"No FAOSTAT QCL yield rows for {crop} (item {qcl_item_code}) "
            f"in years {years}"
        )

    df["country"] = df["iso3"].astype(str).str.upper()
    df["yield_t_per_ha_fresh"] = df["Value"].astype(float) * KG_PER_HA_TO_T_PER_HA

    yields_by_country = df.groupby("country")["yield_t_per_ha_fresh"].mean()

    requested = pd.Index([c.upper() for c in countries], name="country")
    yields_reindexed = yields_by_country.reindex(requested)

    global_mean = float(yields_by_country.mean())
    n_missing = int(yields_reindexed.isna().sum())
    if n_missing:
        logger.info(
            "%s: filling %d/%d countries with the global mean yield (%.2f t/ha fresh)",
            crop,
            n_missing,
            len(yields_reindexed),
            global_mean,
        )
        yields_reindexed = yields_reindexed.fillna(global_mean)

    yields_dm = yields_reindexed * (1.0 - moisture_fraction)
    yields_dm.name = "yield_t_per_ha_dm"
    return yields_dm


def _build_tidy_yields(
    harvarea_df: pd.DataFrame,
    regions_gdf: gpd.GeoDataFrame,
    country_yields_dm: pd.Series,
    suitable_area_expansion: float,
) -> pd.DataFrame:
    """Assemble the tidy crop_yields table from per-cell aggregates."""
    region_lookup = regions_gdf[["region", "country"]].copy()
    region_lookup["region_id"] = np.arange(len(region_lookup), dtype=np.int32)
    region_lookup["country"] = region_lookup["country"].astype(str).str.upper()

    df = harvarea_df.merge(region_lookup, on="region_id", how="left")
    df["yield"] = df["country"].map(country_yields_dm)
    if df["yield"].isna().any():
        missing = sorted(df.loc[df["yield"].isna(), "country"].unique())
        raise RuntimeError(
            f"Country yield missing after fallback (should not happen): {missing}"
        )

    df["suitable_area"] = df["harvested_area"] * float(suitable_area_expansion)

    records: list[pd.DataFrame] = []
    base = df[["region", "resource_class"]].copy()
    for variable, unit, series in (
        ("yield", "t/ha (DM)", df["yield"]),
        ("suitable_area", "ha", df["suitable_area"]),
    ):
        sub = base.copy()
        sub["variable"] = variable
        sub["unit"] = unit
        sub["value"] = pd.to_numeric(series, errors="coerce")
        records.append(sub)

    tidy = pd.concat(records, ignore_index=True)
    tidy["resource_class"] = tidy["resource_class"].astype(int)
    return tidy.sort_values(["region", "resource_class", "variable"], ignore_index=True)


def _build_tidy_harvested_area(
    harvarea_df: pd.DataFrame, regions_gdf: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Assemble the tidy harvested_area table from per-cell aggregates."""
    region_lookup = regions_gdf[["region"]].copy()
    region_lookup["region_id"] = np.arange(len(region_lookup), dtype=np.int32)

    df = harvarea_df.merge(region_lookup, on="region_id", how="left")
    df = df[df["harvested_area"] > 0]
    out = df[["region", "resource_class"]].copy()
    out["variable"] = "harvested_area"
    out["unit"] = "ha"
    out["value"] = df["harvested_area"].to_numpy(dtype=float)
    out["resource_class"] = out["resource_class"].astype(int)
    return out.sort_values(["region", "resource_class"], ignore_index=True)


def _apply_frt_residual(
    harvarea_df: pd.DataFrame,
    regions_gdf: gpd.GeoDataFrame,
    attribution_path: Path,
    crop: str,
) -> pd.DataFrame:
    """Add per-country FRT residual area to harvarea_df, distributed
    proportional to existing CROPGRIDS density.

    The residual represents non-modelled FRT-pool fruits (pears, peaches,
    plums for apple; cantaloupes, papayas for banana) that the model
    routes onto a CROPGRIDS-backed crop on the supply side. Distributing
    the residual via the CROPGRIDS footprint keeps the addition on cells
    where the crop is already grown.
    """
    attribution = pd.read_csv(attribution_path)
    attribution["country"] = attribution["country"].astype(str).str.upper()
    attribution["crop"] = attribution["crop"].astype(str).str.strip()
    residual_by_country = (
        attribution[attribution["crop"] == crop]
        .set_index("country")["residual_share_ha"]
        .astype(float)
    )
    if residual_by_country.sum() <= 0:
        return harvarea_df

    region_lookup = regions_gdf[["region", "country"]].copy()
    region_lookup["region_id"] = np.arange(len(region_lookup), dtype=np.int32)
    region_lookup["country"] = region_lookup["country"].astype(str).str.upper()

    df = harvarea_df.merge(region_lookup, on="region_id", how="left")
    df = df[df["country"].notna()].copy()
    country_total = df.groupby("country")["harvested_area"].sum()

    df["residual_share"] = df["country"].map(residual_by_country).fillna(0.0)
    df["country_total"] = df["country"].map(country_total).fillna(0.0)
    valid = (
        (df["residual_share"] > 0)
        & (df["country_total"] > 0)
        & (df["harvested_area"] > 0)
    )
    df["addition"] = 0.0
    df.loc[valid, "addition"] = (
        df.loc[valid, "residual_share"]
        * df.loc[valid, "harvested_area"]
        / df.loc[valid, "country_total"]
    )
    df["harvested_area"] = df["harvested_area"] + df["addition"]

    base_total_mha = float(country_total.sum()) / 1e6
    added_total_mha = float(df["addition"].sum()) / 1e6
    logger.info(
        "%s: CROPGRIDS area=%.3f Mha + FRT residual addition=%.3f Mha = %.3f Mha",
        crop,
        base_total_mha,
        added_total_mha,
        base_total_mha + added_total_mha,
    )

    return df[["region_id", "resource_class", "harvested_area"]].reset_index(drop=True)


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    cropgrids_nc = Path(snakemake.input.cropgrids_nc)  # type: ignore[name-defined]
    classes_nc = Path(snakemake.input.classes)  # type: ignore[name-defined]
    regions_path = Path(snakemake.input.regions)  # type: ignore[name-defined]
    mapping_path = Path(snakemake.input.cropgrids_mapping)  # type: ignore[name-defined]
    moisture_path = Path(snakemake.input.moisture_content)  # type: ignore[name-defined]
    qcl_csv = Path(snakemake.input.qcl_csv)  # type: ignore[name-defined]
    m49_codes = Path(snakemake.input.m49_codes)  # type: ignore[name-defined]

    crop = str(snakemake.wildcards.crop)  # type: ignore[name-defined]
    countries = [
        str(c).upper()
        for c in snakemake.params.countries  # type: ignore[name-defined]
    ]
    averaging_period = snakemake.params.averaging_period  # type: ignore[name-defined]
    years = list(
        range(
            int(averaging_period["start_year"]), int(averaging_period["end_year"]) + 1
        )
    )
    suitable_area_expansion = float(
        snakemake.params.suitable_area_expansion  # type: ignore[name-defined]
    )

    yields_out = Path(snakemake.output.crop_yields)  # type: ignore[name-defined]
    harvest_out = Path(snakemake.output.harvested_area)  # type: ignore[name-defined]

    mapping = pd.read_csv(mapping_path, comment="#")
    mapping["crop"] = mapping["crop"].astype(str).str.strip()
    row = mapping[mapping["crop"] == crop]
    if row.empty:
        raise RuntimeError(f"cropgrids_crop_mapping.csv has no row for crop '{crop}'")
    qcl_item_code = int(row.iloc[0]["faostat_qcl_item_code"])

    moisture_df = pd.read_csv(moisture_path, comment="#").set_index("crop")
    moisture_fraction = float(moisture_df.loc[crop, "moisture_fraction"])

    logger.info(
        "Building %s yields/harvested area from CROPGRIDS (item %d, moisture %.2f, "
        "years %d-%d, expansion %.2f)",
        crop,
        qcl_item_code,
        moisture_fraction,
        years[0],
        years[-1],
        suitable_area_expansion,
    )

    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and regions_gdf.crs != CROPGRIDS_CRS:
        regions_gdf = regions_gdf.to_crs(CROPGRIDS_CRS)
    regions_gdf = regions_gdf.reset_index(drop=True)
    regions_gdf["country"] = regions_gdf["country"].astype(str).str.upper()

    harvarea = _load_cropgrids_harvarea(cropgrids_nc)
    with xr.open_dataset(classes_nc) as classes_ds:
        class_labels_cg = _reproject_classes_to_cropgrids(classes_ds)
    region_ids = _rasterize_regions(regions_gdf)
    harvarea_df = _aggregate_harvarea(harvarea, region_ids, class_labels_cg)

    # Optional: absorb a share of the GAEZ-FRT residual (non-modelled fruits)
    # onto the crop, distributed proportional to its existing CROPGRIDS density.
    frt_attribution_input = getattr(snakemake.input, "frt_attribution", None)  # type: ignore[name-defined]
    if frt_attribution_input:
        frt_attribution_path = Path(frt_attribution_input)
        if frt_attribution_path.exists():
            harvarea_df = _apply_frt_residual(
                harvarea_df, regions_gdf, frt_attribution_path, crop
            )

    country_yields_dm = _country_yield_t_per_ha_dm(
        qcl_csv,
        m49_codes,
        crop,
        qcl_item_code,
        countries,
        years,
        moisture_fraction,
    )

    yields_tidy = _build_tidy_yields(
        harvarea_df,
        regions_gdf,
        country_yields_dm,
        suitable_area_expansion,
    )
    harvest_tidy = _build_tidy_harvested_area(harvarea_df, regions_gdf)

    yields_out.parent.mkdir(parents=True, exist_ok=True)
    harvest_out.parent.mkdir(parents=True, exist_ok=True)
    yields_tidy.to_csv(yields_out, index=False)
    harvest_tidy.to_csv(harvest_out, index=False)

    logger.info(
        "%s: wrote %d yield rows and %d harvested_area rows (total area %.1f Mha)",
        crop,
        len(yields_tidy),
        len(harvest_tidy),
        float(harvarea_df["harvested_area"].sum()) / 1e6,
    )
