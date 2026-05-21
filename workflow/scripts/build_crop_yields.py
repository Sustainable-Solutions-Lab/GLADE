"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import exact_extract  # noqa: E402
from exactextract.raster import NumPyRasterSource  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402

from workflow.scripts.raster_utils import (  # noqa: E402
    calculate_all_cell_areas,
    raster_bounds,
    read_raster_float,
    scale_fraction,
)

if __name__ == "__main__":
    # Inputs
    classes_nc: str = snakemake.input.classes  # type: ignore[name-defined]
    yield_path: str = snakemake.input.yield_raster  # type: ignore[name-defined]
    suit_path: str = snakemake.input.suitability_raster  # type: ignore[name-defined]
    water_path: str | None = getattr(  # type: ignore[attr-defined]
        snakemake.input, "water_requirement_raster", None
    )
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    gs_start_path: str = snakemake.input.growing_season_start_raster  # type: ignore[name-defined]
    gs_length_path: str = snakemake.input.growing_season_length_raster  # type: ignore[name-defined]
    crop_code: str = snakemake.wildcards.crop  # type: ignore[name-defined]
    conv_csv: str | None = getattr(  # type: ignore[attr-defined]
        snakemake.input, "yield_unit_conversions", None
    )
    moisture_csv: str | None = getattr(  # type: ignore[attr-defined]
        snakemake.input, "moisture_content", None
    )

    KG_TO_TONNE = 0.001

    # Load classes
    ds = xr.load_dataset(classes_nc)
    class_labels = ds["resource_class"].values.astype(np.int16)

    # Load rasters
    y_raw, y_src = read_raster_float(yield_path)
    conversion_overrides: dict[str, float] = {}
    if conv_csv:
        conversion_overrides = (
            pd.read_csv(conv_csv, comment="#")
            .set_index("code")["factor_to_t_per_ha"]
            .to_dict()
        )

    use_actual_yields = bool(getattr(snakemake.params, "use_actual_yields", False))  # type: ignore[attr-defined]

    moisture_lookup: dict[str, float] = {}
    if moisture_csv:
        moisture_lookup = (
            pd.read_csv(moisture_csv, comment="#")
            .set_index("crop")["moisture_fraction"]
            .to_dict()
        )

    def _yield_multiplier(crop: str) -> float:
        # GAEZ publishes RES05 potential yields in kg/ha but the historical
        # “actual yield” variant in t/ha. Validation runs toggle
        # ``use_actual_yields`` so we keep the raster units untouched in that
        # mode while the standard pathway still divides by 1_000.
        base_scale = 1.0 if use_actual_yields else KG_TO_TONNE
        if use_actual_yields:
            return base_scale
        override = conversion_overrides.get(crop)
        if override is None:
            return base_scale
        # Overrides were calibrated under the kg/ha convention (sugar crops and
        # oil-palm report processed output mass). Convert them to a relative
        # multiplier so the same table works for both actual and potential runs.
        return base_scale * (override / KG_TO_TONNE)

    y_tpha = y_raw * _yield_multiplier(crop_code)
    if use_actual_yields:
        moisture_fraction = float(moisture_lookup[crop_code])
        y_tpha = y_tpha * (1.0 - moisture_fraction)
    s_raw, _ = read_raster_float(suit_path)
    s_frac = scale_fraction(s_raw)
    if water_path:
        water_raw_mm, _ = read_raster_float(water_path)
        water_m3_per_ha = water_raw_mm * 10.0  # 1 mm over 1 ha equals 10 m³
    else:
        water_m3_per_ha = np.zeros_like(y_raw)
    gs_start_raw, _ = read_raster_float(gs_start_path)
    gs_length_raw, _ = read_raster_float(gs_length_path)

    height, width = y_tpha.shape
    transform = y_src.transform
    crs = y_src.crs
    crs_wkt = crs.to_wkt() if crs else None
    xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
    # Use 1D cell areas and broadcast to save memory
    cell_area_ha_1d = calculate_all_cell_areas(y_src, repeat=False)

    area_ha = s_frac * cell_area_ha_1d[:, np.newaxis]

    # Regions
    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)
    regions_for_extract = regions_gdf.reset_index()

    # Create raster sources once (before the loop) to avoid repeated allocations
    raster_kwargs = {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "nodata": np.nan,
        "srs_wkt": crs_wkt,
    }
    y_src_np = NumPyRasterSource(y_tpha, **raster_kwargs)
    a_src_np = NumPyRasterSource(area_ha.astype(np.float32), **raster_kwargs)
    water_src_np = NumPyRasterSource(water_m3_per_ha, **raster_kwargs)
    gs_start_src_np = NumPyRasterSource(gs_start_raw, **raster_kwargs)
    gs_length_src_np = NumPyRasterSource(gs_length_raw, **raster_kwargs)

    # Aggregate mean yield and sum area per class using weighted extraction
    out = []
    n_classes = (
        int(np.nanmax(class_labels)) + 1 if np.isfinite(class_labels).any() else 0
    )
    for cls in range(n_classes):
        # Create binary mask as weight (only allocation per iteration)
        mask_float = (class_labels == cls).astype(np.float32)
        if not np.any(mask_float > 0):
            continue

        # Use the mask as weights (0=not this class, 1=this class)
        # Don't set nodata so 0s are treated as zero weight, not missing data
        mask_src = NumPyRasterSource(
            mask_float,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            srs_wkt=crs_wkt,
        )

        # Use weighted operations with class mask as weight
        y_stats = exact_extract(
            y_src_np,
            regions_for_extract,
            ["weighted_mean"],
            weights=mask_src,
            include_cols=["region"],
            output="pandas",
        )
        a_stats = exact_extract(
            a_src_np,
            regions_for_extract,
            ["weighted_sum"],
            weights=mask_src,
            include_cols=["region"],
            output="pandas",
        )
        water_stats = exact_extract(
            water_src_np,
            regions_for_extract,
            ["weighted_mean"],
            weights=mask_src,
            include_cols=["region"],
            output="pandas",
        )
        gs_start_stats = exact_extract(
            gs_start_src_np,
            regions_for_extract,
            ["weighted_mean"],
            weights=mask_src,
            include_cols=["region"],
            output="pandas",
        )
        gs_length_stats = exact_extract(
            gs_length_src_np,
            regions_for_extract,
            ["weighted_mean"],
            weights=mask_src,
            include_cols=["region"],
            output="pandas",
        )
        if y_stats.empty or a_stats.empty:
            continue
        merged = (
            y_stats.rename(columns={"weighted_mean": "yield"})
            .merge(
                a_stats.rename(columns={"weighted_sum": "suitable_area"}),
                on="region",
                how="inner",
            )
            .merge(
                water_stats.rename(
                    columns={"weighted_mean": "water_requirement_m3_per_ha"}
                ),
                on="region",
                how="left",
            )
            .merge(
                gs_start_stats.rename(
                    columns={"weighted_mean": "growing_season_start_day"}
                ),
                on="region",
                how="left",
            )
            .merge(
                gs_length_stats.rename(
                    columns={"weighted_mean": "growing_season_length_days"}
                ),
                on="region",
                how="left",
            )
        )
        merged["resource_class"] = cls
        out.append(merged)

    if out:
        df = (
            pd.concat(out, ignore_index=True)
            .set_index(["region", "resource_class"])
            .sort_index()
        )
    else:
        df = pd.DataFrame(
            columns=[
                "region",
                "resource_class",
                "yield",
                "suitable_area",
                "water_requirement_m3_per_ha",
                "growing_season_start_day",
                "growing_season_length_days",
            ]
        ).set_index(["region", "resource_class"])  # type: ignore[name-defined]

    df_reset = df.reset_index()
    df_reset["resource_class"] = df_reset["resource_class"].astype(int)

    variable_units = {
        "yield": "t/ha (DM)",
        "suitable_area": "ha",
        "water_requirement_m3_per_ha": "m^3/ha",
        "growing_season_start_day": "day-of-year",
        "growing_season_length_days": "days",
    }

    tidy_frames = []
    for variable, unit in variable_units.items():
        if variable not in df_reset.columns:
            continue
        subset = df_reset[["region", "resource_class", variable]].dropna(
            subset=[variable]
        )
        if subset.empty:
            continue
        subset = subset.rename(columns={variable: "value"})
        subset["variable"] = variable
        subset["unit"] = unit
        tidy_frames.append(
            subset[["region", "resource_class", "variable", "unit", "value"]]
        )

    if tidy_frames:
        tidy_df = pd.concat(tidy_frames, ignore_index=True)
    else:
        tidy_df = pd.DataFrame(
            columns=["region", "resource_class", "variable", "unit", "value"]
        )

    if not tidy_df.empty:
        tidy_df["value"] = pd.to_numeric(tidy_df["value"], errors="coerce")
        tidy_df.sort_values(
            ["region", "resource_class", "variable"], inplace=True, ignore_index=True
        )

    # A yield-less output is allowed at the per-(crop, water_supply) level
    # (e.g. wetland-rice rainfed in Europe); ``build_model`` raises a
    # clear error if every water supply for a crop is empty.

    Path(snakemake.output[0]).parent.mkdir(parents=True, exist_ok=True)  # type: ignore[name-defined]
    tidy_df.to_csv(snakemake.output[0], index=False)  # type: ignore[name-defined]
