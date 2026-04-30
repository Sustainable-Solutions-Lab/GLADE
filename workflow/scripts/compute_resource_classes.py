"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
import pandas as pd
import rasterio
import rasterio.features as rfeatures
import xarray as xr

from workflow.scripts.harvested_area_shares import (
    RES06_HAR_SCALE_TO_HA,
    load_mapping,
    shares_for_crop,
    shares_from_fdd,
)

# Enable GDAL exceptions for better error messages
gdal.UseExceptions()
osr.UseExceptions()


def read_raster_float(path: str):
    src = rasterio.open(path)
    arr = src.read(1).astype(float)
    if src.nodata is not None:
        arr = np.where(arr == src.nodata, np.nan, arr)
    return arr, src


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    midpoint = 0.5 * weights.sum()
    return float(values[np.searchsorted(np.cumsum(weights), midpoint, side="left")])


def load_yield_conversions(path: str) -> dict[str, float]:
    df = pd.read_csv(path, comment="#").set_index("code")
    return df["factor_to_t_per_ha"].dropna().astype(float).to_dict()


def load_moisture_content(path: str) -> dict[str, float]:
    df = pd.read_csv(path, comment="#").set_index("crop")
    return df["moisture_fraction"].astype(float).to_dict()


def yield_multiplier(
    crop: str,
    *,
    use_actual_yields: bool,
    conversions: dict[str, float],
) -> float:
    if use_actual_yields:
        return 1.0
    kg_to_tonne = 0.001
    return conversions.get(crop, kg_to_tonne)


def scale_yield(
    raw: np.ndarray,
    crop: str,
    *,
    use_actual_yields: bool,
    conversions: dict[str, float],
    moisture: dict[str, float],
) -> np.ndarray:
    # Potential-yield rasters are kg/ha and converted to t DM/ha via
    # yield_unit_conversions.csv (default 1e-3). Actual-yield rasters are
    # assumed to already be t/ha fresh weight; we deduct moisture to reach
    # t DM/ha so both branches return the same unit.
    out = raw * yield_multiplier(
        crop,
        use_actual_yields=use_actual_yields,
        conversions=conversions,
    )
    if use_actual_yields:
        out = out * (1.0 - moisture[crop])
    return out


def validate_raster_shape(
    arr: np.ndarray, expected: tuple[int, int], path: str
) -> None:
    if arr.shape != expected:
        raise ValueError(f"Raster shape mismatch for {path}: {arr.shape} != {expected}")


def crop_water_pairs(
    crops: list[str], water_supplies: list[str]
) -> list[tuple[str, str]]:
    return [(crop, water_supply) for water_supply in water_supplies for crop in crops]


def shares_by_region(
    crop: str,
    regions_gdf: gpd.GeoDataFrame,
    mapping_df: pd.DataFrame,
    production_df: pd.DataFrame,
    fdd_shares_path: Path | None,
    non_food_crops: set[str],
) -> np.ndarray:
    row = mapping_df[mapping_df["crop_name"] == crop]
    if row.empty:
        raise ValueError(f"Crop '{crop}' missing from RES06 mapping table")
    module_code = str(row.iloc[0]["res06_code"]).upper()

    fdd_result = None
    if module_code == "FDD" and fdd_shares_path is not None:
        fdd_result = shares_from_fdd(fdd_shares_path, crop)

    if fdd_result is None:
        lookup, fallback = shares_for_crop(
            crop,
            mapping_df,
            production_df,
            non_food_crops=non_food_crops,
        )
    else:
        lookup, fallback = fdd_result

    countries = regions_gdf["country"].astype(str).str.upper()
    return countries.map(lambda country: lookup.get(country, fallback)).to_numpy(float)


def sum_by_region(
    values: np.ndarray,
    region_raster: np.ndarray,
    n_regions: int,
) -> np.ndarray:
    valid = (region_raster >= 0) & np.isfinite(values)
    if not np.any(valid):
        return np.zeros(n_regions, dtype=float)
    return np.bincount(
        region_raster[valid].ravel(),
        weights=values[valid].ravel(),
        minlength=n_regions,
    )


def compute_max_yield_score(
    yield_paths: list[str],
    pairs: list[tuple[str, str]],
    expected_shape: tuple[int, int],
    *,
    use_actual_yields: bool,
    conversions: dict[str, float],
    moisture: dict[str, float],
) -> np.ndarray:
    score = np.full(expected_shape, np.nan, dtype=float)
    for path, (crop, _water_supply) in zip(yield_paths, pairs, strict=True):
        raw, src = read_raster_float(path)
        try:
            validate_raster_shape(raw, expected_shape, path)
        finally:
            src.close()
        y = scale_yield(
            raw,
            crop,
            use_actual_yields=use_actual_yields,
            conversions=conversions,
            moisture=moisture,
        )
        score = np.fmax(score, y)
    return score


def compute_regional_harvested_area_score(
    yield_paths: list[str],
    harvested_paths: list[str],
    pairs: list[tuple[str, str]],
    region_raster: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
    expected_shape: tuple[int, int],
    *,
    conversions: dict[str, float],
    moisture: dict[str, float],
    mapping_path: str,
    production_path: str,
    fdd_shares_path: Path | None,
    non_food_crops: set[str],
) -> np.ndarray:
    mapping_df = load_mapping(Path(mapping_path))
    production_df = pd.read_csv(production_path)
    n_regions = len(regions_gdf)
    numerator = np.zeros(expected_shape, dtype=float)
    denominator = np.zeros(expected_shape, dtype=float)
    region_valid = region_raster >= 0
    share_grid_cache: dict[str, np.ndarray] = {}

    for yield_path, harvested_path, (crop, _water_supply) in zip(
        yield_paths, harvested_paths, pairs, strict=True
    ):
        y_raw, y_src = read_raster_float(yield_path)
        try:
            validate_raster_shape(y_raw, expected_shape, yield_path)
        finally:
            y_src.close()
        y = scale_yield(
            y_raw,
            crop,
            use_actual_yields=True,
            conversions=conversions,
            moisture=moisture,
        )

        harvested_raw, harvested_src = read_raster_float(harvested_path)
        try:
            validate_raster_shape(harvested_raw, expected_shape, harvested_path)
        finally:
            harvested_src.close()

        harvested = np.where(
            np.isfinite(harvested_raw) & (harvested_raw > 0.0),
            harvested_raw * RES06_HAR_SCALE_TO_HA,
            0.0,
        )
        if crop not in share_grid_cache:
            region_shares = shares_by_region(
                crop,
                regions_gdf,
                mapping_df,
                production_df,
                fdd_shares_path,
                non_food_crops,
            )
            share_grid = np.zeros(expected_shape, dtype=float)
            share_grid[region_valid] = region_shares[region_raster[region_valid]]
            share_grid_cache[crop] = share_grid
        share_grid = share_grid_cache[crop]
        crop_area = harvested * share_grid

        scale_mask = np.isfinite(y) & (y > 0.0) & (crop_area > 0.0)
        if not np.any(scale_mask):
            continue
        scale = weighted_median(y[scale_mask], crop_area[scale_mask])
        if not np.isfinite(scale) or scale <= 0.0:
            continue

        regional_area = sum_by_region(crop_area, region_raster, n_regions)
        region_weight = np.zeros(expected_shape, dtype=float)
        region_weight[region_valid] = regional_area[region_raster[region_valid]]

        normalized = y / scale
        valid = region_valid & np.isfinite(normalized) & (normalized > 0.0)
        valid &= region_weight > 0.0
        numerator[valid] += region_weight[valid] * normalized[valid]
        denominator[valid] += region_weight[valid]

    return np.divide(
        numerator,
        denominator,
        out=np.full(expected_shape, np.nan, dtype=float),
        where=denominator > 0.0,
    )


if __name__ == "__main__":
    # Inputs provided by Snakemake
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    # Yield rasters as a list of paths
    yield_paths: list[str] = list(snakemake.input.yields)  # type: ignore[attr-defined]
    crops: list[str] = list(snakemake.params.crops)  # type: ignore[attr-defined]
    water_supplies: list[str] = list(snakemake.params.water_supplies)  # type: ignore[attr-defined]
    pairs = crop_water_pairs(crops, water_supplies)
    if len(yield_paths) != len(pairs):
        raise ValueError(
            f"Expected {len(pairs)} yield rasters for crop/water pairs, "
            f"got {len(yield_paths)}"
        )

    score_method: str = snakemake.params.resource_class_score  # type: ignore[attr-defined]
    use_actual_yields: bool = bool(snakemake.params.use_actual_yields)  # type: ignore[attr-defined]
    if score_method == "regional_crop_mix_actual_yield" and not use_actual_yields:
        raise ValueError(
            "aggregation.resource_class_score="
            "'regional_crop_mix_actual_yield' requires "
            "validation.use_actual_yields=true"
        )

    quantiles: list[float] = [
        0.0,
        *list(snakemake.params.resource_class_quantiles),
        1.0,
    ]  # type: ignore[name-defined]
    conversions = load_yield_conversions(snakemake.input.yield_unit_conversions)  # type: ignore[attr-defined]
    moisture = load_moisture_content(snakemake.input.moisture_content)  # type: ignore[attr-defined]

    # Read regions and use first raster as reference for grid/CRS
    regions_gdf = gpd.read_file(regions_path)

    # Use the first yield raster's grid as reference (metadata only).
    with rasterio.open(yield_paths[0]) as src0:
        height = src0.height
        width = src0.width
        transform = src0.transform
        crs = src0.crs

    # Reproject regions to raster CRS if needed
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)

    # Rasterize regions to integer ids (0..N-1), -1 outside
    region_shapes = [(geom, idx) for idx, geom in enumerate(regions_gdf.geometry)]
    region_raster = rfeatures.rasterize(
        region_shapes,
        out_shape=(height, width),
        transform=transform,
        fill=-1,
        dtype=np.int32,
    )

    if score_method == "max_yield":
        score = compute_max_yield_score(
            yield_paths,
            pairs,
            (height, width),
            use_actual_yields=use_actual_yields,
            conversions=conversions,
            moisture=moisture,
        )
    elif score_method == "regional_crop_mix_actual_yield":
        harvested_paths: list[str] = list(snakemake.input.harvested_area)  # type: ignore[attr-defined]
        if len(harvested_paths) != len(pairs):
            raise ValueError(
                f"Expected {len(pairs)} harvested-area rasters for crop/water pairs, "
                f"got {len(harvested_paths)}"
            )
        fdd_shares_raw = snakemake.input.get("fdd_shares")  # type: ignore[attr-defined]
        fdd_shares_path = Path(fdd_shares_raw) if fdd_shares_raw else None
        score = compute_regional_harvested_area_score(
            yield_paths,
            harvested_paths,
            pairs,
            region_raster,
            regions_gdf,
            (height, width),
            conversions=conversions,
            moisture=moisture,
            mapping_path=snakemake.input.crop_mapping,  # type: ignore[attr-defined]
            production_path=snakemake.input.faostat_production,  # type: ignore[attr-defined]
            fdd_shares_path=fdd_shares_path,
            non_food_crops=set(snakemake.params.non_food_crops),  # type: ignore[attr-defined]
        )
    else:
        raise ValueError(f"Unknown resource class score method: {score_method}")

    # Build xarray DataArrays
    y_da = xr.DataArray(score, dims=("y", "x"))
    reg_da = xr.DataArray(region_raster, dims=("y", "x"))

    # Vectorized per-region quantiles and class assignment
    # Ignore cells with zero/negative scores so unsuitable or uncovered pixels
    # do not collapse the quantile bins.
    positive_y = xr.where((y_da > 0) & np.isfinite(y_da), y_da, np.nan)
    reg_quantiles = positive_y.groupby(reg_da).quantile(quantiles)
    thresholds = reg_quantiles.sel(group=reg_da).reset_coords(drop=True)

    class_da = xr.full_like(y_da, np.nan, dtype=float)
    for ci in range(len(quantiles) - 1):
        lo = thresholds.isel(quantile=ci)
        hi = thresholds.isel(quantile=ci + 1)
        if ci == len(quantiles) - 2:
            sel = (reg_da >= 0) & np.isfinite(y_da) & (y_da >= lo)
        else:
            sel = (reg_da >= 0) & np.isfinite(y_da) & (y_da >= lo) & (y_da < hi)
        class_da = xr.where(sel, float(ci), class_da)

    ds = xr.Dataset(
        {
            "region_id": reg_da.astype(np.int32),
            "resource_class": class_da.fillna(-1).astype(np.int8),
        }
    )
    # Store transform/CRS/bounds as attrs for downstream use
    ds.attrs.update(
        {
            "transform": transform.to_gdal(),
            "crs_wkt": crs.to_wkt() if crs else None,
            "height": int(height),
            "width": int(width),
            "quantiles": tuple(quantiles),
            "score_method": score_method,
        }
    )

    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path)
