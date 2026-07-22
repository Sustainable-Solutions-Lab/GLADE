"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import Operation, exact_extract  # noqa: E402
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
    with xr.open_dataset(classes_nc) as ds:
        class_labels = ds["resource_class"].load().values

    # Load rasters
    y_tpha, y_src = read_raster_float(yield_path)
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
        # "actual yield" variant in t/ha. Validation runs toggle
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

    y_tpha *= _yield_multiplier(crop_code)
    if use_actual_yields:
        moisture_fraction = float(moisture_lookup[crop_code])
        y_tpha *= 1.0 - moisture_fraction
    s_raw, _ = read_raster_float(suit_path)
    s_frac = scale_fraction(s_raw)
    if water_path:
        water_m3_per_ha, _ = read_raster_float(water_path)
        water_m3_per_ha *= 10.0  # 1 mm over 1 ha equals 10 m3
    else:
        water_m3_per_ha = np.zeros_like(y_tpha)
    gs_start_raw, _ = read_raster_float(gs_start_path)
    gs_length_raw, _ = read_raster_float(gs_length_path)

    height, width = y_tpha.shape
    transform = y_src.transform
    crs = y_src.crs
    crs_wkt = crs.to_wkt() if crs else None
    xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
    # Use 1D cell areas and broadcast to save memory
    cell_area_ha_1d = calculate_all_cell_areas(y_src, repeat=False)

    s_frac *= cell_area_ha_1d[:, np.newaxis]
    area_ha = s_frac

    # Regions
    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)
    regions_for_extract = regions_gdf.reset_index()

    # Build every class-specific operation up front so exactextract traverses each
    # region geometry only once for all variables and resource classes.
    raster_kwargs = {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "nodata": np.nan,
        "srs_wkt": crs_wkt,
    }
    y_src_np = NumPyRasterSource(y_tpha, name="yield", **raster_kwargs)
    a_src_np = NumPyRasterSource(area_ha, name="suitable_area", **raster_kwargs)
    water_src_np = NumPyRasterSource(
        water_m3_per_ha, name="water_requirement", **raster_kwargs
    )
    gs_start_src_np = NumPyRasterSource(
        gs_start_raw, name="growing_season_start", **raster_kwargs
    )
    gs_length_src_np = NumPyRasterSource(
        gs_length_raw, name="growing_season_length", **raster_kwargs
    )
    value_sources = [
        y_src_np,
        a_src_np,
        water_src_np,
        gs_start_src_np,
        gs_length_src_np,
    ]

    n_classes = (
        int(np.nanmax(class_labels)) + 1 if np.isfinite(class_labels).any() else 0
    )
    # Operation borrows its weight RasterSource, so keep the owners alive.
    class_sources = []
    operations = []
    valid_classes = []
    for cls in range(n_classes):
        class_mask = class_labels == cls
        if not np.any(class_mask):
            continue
        mask_src = NumPyRasterSource(
            class_mask,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            name=f"resource_class_{cls}",
            srs_wkt=crs_wkt,
        )
        class_sources.append(mask_src)
        valid_classes.append(cls)
        operations.extend(
            [
                Operation("weighted_mean", f"yield_{cls}", y_src_np, mask_src),
                Operation("weighted_sum", f"suitable_area_{cls}", a_src_np, mask_src),
                Operation(
                    "weighted_mean",
                    f"water_requirement_m3_per_ha_{cls}",
                    water_src_np,
                    mask_src,
                ),
                Operation(
                    "weighted_mean",
                    f"growing_season_start_day_{cls}",
                    gs_start_src_np,
                    mask_src,
                ),
                Operation(
                    "weighted_mean",
                    f"growing_season_length_days_{cls}",
                    gs_length_src_np,
                    mask_src,
                ),
            ]
        )

    out = []
    if operations:
        stats = exact_extract(
            value_sources,
            regions_for_extract,
            operations,
            include_cols=["region"],
            output="pandas",
        )
        variables = [
            "yield",
            "suitable_area",
            "water_requirement_m3_per_ha",
            "growing_season_start_day",
            "growing_season_length_days",
        ]
        for cls in valid_classes:
            columns = {f"{variable}_{cls}": variable for variable in variables}
            class_stats = stats[["region", *columns]].rename(columns=columns)
            class_stats["resource_class"] = cls
            out.append(class_stats)

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
