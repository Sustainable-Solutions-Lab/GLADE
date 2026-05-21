"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
import xarray as xr

from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    raster_bounds,
)

RES06_HAR_SCALE_TO_HA = 1_000.0  # RES06-HAR stores thousand hectares (kha)


def _build_dummy_raster(transform: Affine, width: int, height: int):
    class _DummyRaster:
        def __init__(self, transform: Affine, width: int, height: int) -> None:
            self.transform = transform
            self.shape = (height, width)
            xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            self.bounds = (xmin, ymin, xmax, ymax)

    return _DummyRaster(transform, width, height)


def _transform_from_attrs(ds: xr.Dataset) -> Affine:
    try:
        return Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:  # pragma: no cover - sanity guard
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc


def _load_and_align_raster(
    path: str,
    target_shape: tuple[int, int],
    target_transform: Affine,
    *,
    target_crs: CRS,
) -> np.ndarray:
    """Load a raster and reproject/resample to the target grid."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        src_transform = src.transform
        src_crs = src.crs
        nodata = src.nodata

    if nodata is not None:
        arr[arr == np.float32(nodata)] = np.nan

    needs_resample = (
        arr.shape != target_shape
        or src_transform != target_transform
        or (src_crs is not None and src_crs != target_crs)
    )

    if needs_resample:
        if src_crs is None:
            raise ValueError(f"Raster {path} missing CRS information")
        # RES06-HAR values are extensive (kha per source pixel). The target
        # grid is built from the first RES06 yield raster in
        # compute_resource_classes.py, so under the standard pipeline
        # source and target are byte-identical and this branch never
        # runs. If it does run with a CRS or transform change,
        # Resampling.sum is not area-conserving across reprojection: it
        # sums source pixels falling into each destination cell without
        # weighting by fractional overlap, so totals drift. Convert kha
        # per source-pixel to a density, resample with average, and
        # re-multiply by the destination cell area before relying on
        # this path.
        raise NotImplementedError(
            f"Raster {path} requires reprojection or grid change "
            f"(src shape={arr.shape}, dst shape={target_shape}; "
            f"src crs={src_crs}, dst crs={target_crs}). "
            "Resampling extensive harvested-area values across CRS is "
            "not area-conserving; rebuild the target grid from a "
            "RES06 raster or implement density-based resampling."
        )

    return arr


if __name__ == "__main__":
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    crop_mapping_path: str = snakemake.input.crop_mapping  # type: ignore[name-defined]
    irrigated_share_path: str = snakemake.input.irrigated_share  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    # Get raster paths from snakemake inputs (unpacked with keys like "WHE_i", "MZE_r", etc.)
    # Skip known non-raster inputs
    known_inputs = {"classes", "regions", "crop_mapping", "irrigated_share"}
    raster_paths: dict[str, str] = {
        key: path
        for key, path in snakemake.input.items()  # type: ignore[name-defined]
        if key not in known_inputs
    }

    # Load grid information from resource classes
    classes_ds = xr.load_dataset(classes_path)
    region_id = classes_ds["region_id"].astype(np.int32).values
    resource_class = classes_ds["resource_class"].astype(np.int16).values
    transform = _transform_from_attrs(classes_ds)
    height, width = region_id.shape
    target_crs = CRS.from_wkt(classes_ds.attrs["crs_wkt"])

    # Calculate cell areas in hectares
    dummy_raster = _build_dummy_raster(transform, width, height)
    cell_area_ha = calculate_all_cell_areas(dummy_raster)

    # Accumulate total harvested area per water supply across all modules
    total_harvested_i = np.zeros((height, width), dtype=np.float32)
    total_harvested_r = np.zeros((height, width), dtype=np.float32)

    # Process each unique module raster
    unique_modules = set()
    for key in raster_paths:
        # Key format: "MODULE_WATER" e.g., "WHE_i" or "MZE_r"
        parts = key.rsplit("_", 1)
        if len(parts) == 2:
            module, water = parts
            unique_modules.add(module)

    for module in unique_modules:
        for water in ["i", "r"]:
            key = f"{module}_{water}"
            if key not in raster_paths:
                continue

            raster_path = raster_paths[key]
            arr = _load_and_align_raster(
                raster_path, (height, width), transform, target_crs=target_crs
            )

            # Convert from kha to ha
            arr = arr * RES06_HAR_SCALE_TO_HA

            # Set NaN to 0 for summation
            np.copyto(arr, 0.0, where=~np.isfinite(arr))

            if water == "i":
                total_harvested_i += arr
            else:
                total_harvested_r += arr

    # Clamp harvested area to physical cell area (handle multi-cropping)
    # First clamp individual water supplies, then ensure total doesn't exceed cell area
    total_harvested = total_harvested_i + total_harvested_r

    # Where total harvested exceeds cell area, scale proportionally
    excess_mask = total_harvested > cell_area_ha
    if np.any(excess_mask):
        # Use np.divide with where/out to avoid divide-by-zero warnings
        scale_factor = np.ones_like(total_harvested)
        np.divide(
            cell_area_ha,
            total_harvested,
            out=scale_factor,
            where=excess_mask,
        )
        total_harvested_i = total_harvested_i * scale_factor
        total_harvested_r = total_harvested_r * scale_factor

    # Load regions for mapping IDs to names
    regions_gdf = gpd.read_file(regions_path)
    if "region" not in regions_gdf.columns:
        raise ValueError("regions.geojson must contain a 'region' column")
    region_lookup = (
        regions_gdf.reset_index().set_index("index")["region"].astype(str).to_dict()
    )

    # Build output dataframes for each water supply
    frames: list[pd.DataFrame] = []

    for water_supply, harvested_area in [
        ("i", total_harvested_i),
        ("r", total_harvested_r),
    ]:
        valid = (
            np.isfinite(region_id)
            & np.isfinite(resource_class)
            & (region_id >= 0)
            & (resource_class >= 0)
            & (harvested_area > 0.0)
        )
        if not np.any(valid):
            continue

        region_vals = region_id[valid].astype(np.int32, copy=False)
        class_vals = resource_class[valid].astype(np.int32, copy=False)
        area_vals = harvested_area[valid].astype(np.float64, copy=False)

        df = (
            pd.DataFrame(
                {
                    "region_id": region_vals,
                    "resource_class": class_vals,
                    "area_ha": area_vals,
                }
            )
            .groupby(["region_id", "resource_class"], as_index=False)["area_ha"]
            .sum()
        )
        df["region"] = df["region_id"].map(region_lookup)
        missing = df["region"].isna()
        if missing.any():
            missing_ids = sorted(df.loc[missing, "region_id"].unique().tolist())
            raise ValueError(
                "Region IDs in resource_classes.nc missing from regions.geojson: "
                + ", ".join(str(mid) for mid in missing_ids)
            )
        df["water_supply"] = water_supply
        frames.append(df[["region", "resource_class", "water_supply", "area_ha"]])

    if frames:
        result = (
            pd.concat(frames, ignore_index=True)
            .sort_values(["region", "water_supply", "resource_class"])
            .reset_index(drop=True)
        )
    else:
        result = pd.DataFrame(
            columns=["region", "resource_class", "water_supply", "area_ha"]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
