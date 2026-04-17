"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    load_raster_array,
    raster_bounds,
    read_raster_float,
    scale_fraction,
)

ZONE_CAPABILITIES: dict[int, dict[str, int | bool]] = {
    0: {"valid": False, "max_cycles": 0, "max_wetland_rice": 0},
    1: {"valid": True, "max_cycles": 0, "max_wetland_rice": 0},  # no cropping
    2: {"valid": True, "max_cycles": 1, "max_wetland_rice": 1},  # single cropping
    3: {
        "valid": True,
        "max_cycles": 2,
        "max_wetland_rice": 1,
    },  # limited double (may allow one rice)
    4: {
        "valid": True,
        "max_cycles": 2,
        "max_wetland_rice": 0,
    },  # double, no wetland rice sequentially
    5: {"valid": True, "max_cycles": 2, "max_wetland_rice": 1},  # double with rice
    6: {
        "valid": True,
        "max_cycles": 2,
        "max_wetland_rice": 2,
    },  # double rice (ignoring limited triple/relay)
    7: {
        "valid": True,
        "max_cycles": 3,
        "max_wetland_rice": 2,
    },  # triple cropping, ≤2 rice
    8: {"valid": True, "max_cycles": 3, "max_wetland_rice": 3},  # triple rice cropping
}

WETLAND_RICE_CROPS = {"wetland-rice"}


def sequence_feasible(
    starts: list[np.ndarray], lengths: list[np.ndarray]
) -> np.ndarray:
    if not starts or not lengths or len(starts) != len(lengths):
        raise ValueError("Starts and lengths must be equally-sized non-empty lists")

    feasible = np.ones_like(starts[0], dtype=bool)
    for arr in [*starts, *lengths]:
        feasible &= np.isfinite(arr)
    for arr in lengths:
        feasible &= arr > 0

    if not feasible.any():
        return feasible

    prev_end = starts[0] + lengths[0]
    for idx in range(1, len(starts)):
        start = starts[idx]
        length = lengths[idx]
        # Shift later crops forward by full years until they start after the previous crop
        lag = np.maximum(prev_end - start, 0.0)
        shift_cycles = np.ceil(lag / 365.0)
        adjusted_start = start + shift_cycles * 365.0
        prev_end = adjusted_start + length

    total_span = prev_end - starts[0]
    feasible &= np.isfinite(total_span) & (total_span <= 365.0)
    return feasible


def aggregate_raster_by_region(
    data_array: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    crs_wkt: str | None,
    stat: str = "sum",
) -> pd.DataFrame:
    """Aggregate raster data by regions using exact_extract."""
    src = NumPyRasterSource(
        data_array,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        nodata=np.nan,
        srs_wkt=crs_wkt,
    )
    return exact_extract(
        src,
        regions_gdf,
        [stat],
        include_cols=["region"],
        output="pandas",
    )


def compute_eligibility_mask(
    crop_sequence: list[str],
    ws: str,
    zone_arr: np.ndarray,
    suitability_data: dict,
    start_data: dict,
    length_data: dict,
    yield_data: dict,
    water_requirement_data: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Compute combined eligibility mask and return mask, min_fraction, total_water."""
    # Zone capability check
    rice_cycles = sum(1 for crop in crop_sequence if crop in WETLAND_RICE_CROPS)
    allowed_zone_codes = [
        code
        for code, cap in ZONE_CAPABILITIES.items()
        if cap.get("valid", False)
        and int(cap.get("max_cycles", 0)) >= len(crop_sequence)
        and int(cap.get("max_wetland_rice", 0)) >= rice_cycles
    ]
    if not allowed_zone_codes:
        return np.zeros_like(zone_arr, dtype=bool), np.array([]), None
    zone_mask = np.isin(zone_arr, allowed_zone_codes)

    # Suitability check
    suit_stack = np.stack(
        [suitability_data[(crop, ws)] for crop in crop_sequence], axis=0
    )
    valid_suit = np.all(np.isfinite(suit_stack), axis=0)
    safe_suit_stack = np.where(np.isfinite(suit_stack), suit_stack, np.inf)
    min_fraction = np.min(safe_suit_stack, axis=0)
    min_fraction[~np.isfinite(min_fraction)] = np.nan
    min_fraction = np.clip(min_fraction, 0.0, 1.0, out=min_fraction)

    # Growing season feasibility
    start_stack = [start_data[(crop, ws)] for crop in crop_sequence]
    length_stack = [length_data[(crop, ws)] for crop in crop_sequence]
    feasible_mask = sequence_feasible(start_stack, length_stack)

    # Yield check
    yield_stack = [yield_data[(crop, ws)] for crop in crop_sequence]
    positive_yield = np.ones_like(min_fraction, dtype=bool)
    for arr in yield_stack:
        positive_yield &= np.isfinite(arr) & (arr > 0)

    # Water requirement check (irrigated only)
    if ws == "i":
        water_arrays = [water_requirement_data[(crop, ws)] for crop in crop_sequence]
        water_stack = np.stack(water_arrays, axis=0)
        valid_water = np.all(np.isfinite(water_stack), axis=0)
        total_water_arr = np.sum(water_stack, axis=0)
    else:
        valid_water = np.ones_like(min_fraction, dtype=bool)
        total_water_arr = None

    combined_mask = (
        feasible_mask & valid_suit & positive_yield & valid_water & zone_mask
    )
    return combined_mask, min_fraction, total_water_arr


if __name__ == "__main__":
    # Parse combinations from config
    combos: list[dict[str, object]] = []
    use_actual_yields = bool(getattr(snakemake.params, "use_actual_yields", False))  # type: ignore[attr-defined]

    for name, entry in snakemake.params.combinations.items():  # type: ignore[attr-defined,name-defined]
        if entry is None:
            continue
        crops = [str(c) for c in entry["crops"]]
        water_supplies = entry.get("water_supplies", ["r"])
        if isinstance(water_supplies, str):
            water_supplies = [water_supplies]
        for ws in water_supplies:
            combos.append({"name": name, "water_supply": ws.lower(), "crops": crops})

    # Parse inputs
    inputs = dict(snakemake.input.items())  # type: ignore[attr-defined]
    zone_paths = {
        ws: str(inputs.pop(f"multiple_cropping_zone_{ws}"))
        for ws in ("r", "i")
        if f"multiple_cropping_zone_{ws}" in inputs
    }
    classes_nc = inputs.pop("classes")
    regions_path = inputs.pop("regions")
    conv_csv = inputs.pop("yield_unit_conversions")
    moisture_csv = inputs.pop("moisture_content")

    # Group crop rasters by (crop, water_supply)
    crop_files: dict[tuple[str, str], dict[str, str]] = {}
    suffixes = {
        "_yield_raster": "yield",
        "_suitability_raster": "suitability",
        "_growing_season_start_raster": "season_start",
        "_growing_season_length_raster": "season_length",
        "_water_requirement_raster": "water_requirement",
    }
    for key, path in inputs.items():
        for suffix, field in suffixes.items():
            if key.endswith(suffix):
                crop_ws = key[: -len(suffix)]
                crop, ws = crop_ws.rsplit("_", 1)
                crop_files.setdefault((crop, ws), {})[field] = path
                break

    if not combos:
        # Write empty outputs and exit
        empty = pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "eligible_area_ha",
                "water_requirement_m3_per_ha",
            ]
        )
        empty_cycles = pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "cycle_index",
                "crop",
                "yield_t_per_ha",
            ]
        )
        Path(snakemake.output.eligible).parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
        Path(snakemake.output.yields).parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
        empty.to_csv(snakemake.output.eligible, index=False)  # type: ignore[attr-defined]
        empty_cycles.to_csv(snakemake.output.yields, index=False)  # type: ignore[attr-defined]
        raise SystemExit(0)

    conv_df = pd.read_csv(conv_csv, comment="#").set_index("code")
    KG_TO_TONNE = 0.001

    # Load moisture content for fresh-to-dry-matter conversion (actual yields only)
    moisture_lookup: dict[str, float] = {}
    if use_actual_yields:
        moisture_df = pd.read_csv(moisture_csv, comment="#").set_index("crop")
        moisture_lookup = moisture_df["moisture_fraction"].astype(float).to_dict()

    ds = xr.load_dataset(classes_nc)
    if "resource_class" not in ds:
        raise ValueError("resource_classes.nc is missing 'resource_class' data")
    class_labels = ds["resource_class"].values.astype(np.int16)

    # Use any available crop/ws pair to get raster dimensions
    sample_crop, sample_ws = next(iter(crop_files.keys()))
    yield_arr_ref, yield_src = read_raster_float(
        crop_files[(sample_crop, sample_ws)]["yield"]
    )
    try:
        height, width = yield_arr_ref.shape
        if class_labels.shape != (height, width):
            raise ValueError(
                "Resource class grid does not match GAEZ raster dimensions for multiple cropping"
            )
        transform = yield_src.transform
        crs = yield_src.crs
        crs_wkt = crs.to_wkt() if crs else None
        xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
        cell_area_ha = calculate_all_cell_areas(yield_src)
    finally:
        yield_src.close()

    zone_arrays: dict[str, np.ndarray] = {}
    for ws, path in zone_paths.items():
        zone_arr = load_raster_array(path)
        if zone_arr.shape != (height, width):
            raise ValueError(
                f"Multiple cropping zone raster for water supply '{ws}' has unexpected dimensions"
            )
        zone_arrays[ws] = zone_arr.astype(np.int16, copy=False)

    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and crs and regions_gdf.crs != crs:
        regions_gdf = regions_gdf.to_crs(crs)
    regions_for_extract = regions_gdf.reset_index()

    def conversion_factor(crop: str) -> float:
        base_scale = 1.0 if use_actual_yields else KG_TO_TONNE
        if crop in conv_df.index and pd.notna(conv_df.at[crop, "factor_to_t_per_ha"]):
            override = float(conv_df.at[crop, "factor_to_t_per_ha"])
            return base_scale * (override / KG_TO_TONNE)
        return base_scale

    yield_data: dict[tuple[str, str], np.ndarray] = {}
    suitability_data: dict[tuple[str, str], np.ndarray] = {}
    start_data: dict[tuple[str, str], np.ndarray] = {}
    length_data: dict[tuple[str, str], np.ndarray] = {}
    water_requirement_data: dict[tuple[str, str], np.ndarray] = {}

    for (crop, ws), files in crop_files.items():
        factor = conversion_factor(crop)
        y_arr = load_raster_array(files["yield"])
        suitability_arr = load_raster_array(files["suitability"])
        start_arr = load_raster_array(files["season_start"])
        length_arr = load_raster_array(files["season_length"])
        if y_arr.shape != (height, width):
            raise ValueError(
                f"Yield raster for '{crop}' ({ws}) has unexpected dimensions"
            )
        if suitability_arr.shape != (height, width):
            raise ValueError(
                f"Suitability raster for '{crop}' ({ws}) has unexpected dimensions"
            )
        if start_arr.shape != (height, width):
            raise ValueError(
                f"Growing season start raster for '{crop}' ({ws}) has unexpected dimensions"
            )
        if length_arr.shape != (height, width):
            raise ValueError(
                f"Growing season length raster for '{crop}' ({ws}) has unexpected dimensions"
            )

        y_scaled = y_arr * factor
        if use_actual_yields and crop in moisture_lookup:
            # GAEZ actual yields are fresh weight; convert to dry matter
            y_scaled = y_scaled * (1.0 - moisture_lookup[crop])
        yield_data[(crop, ws)] = y_scaled
        suitability_data[(crop, ws)] = scale_fraction(suitability_arr)
        start_data[(crop, ws)] = start_arr
        length_data[(crop, ws)] = length_arr

        if ws == "i":
            path = files.get("water_requirement")
            if path is None:
                raise ValueError(
                    f"Missing water requirement raster for irrigated crop '{crop}'"
                )
            water_arr = load_raster_array(path)
            if water_arr.shape != (height, width):
                raise ValueError(
                    f"Water requirement raster for '{crop}' ({ws}) has unexpected dimensions"
                )
            water_requirement_data[(crop, ws)] = water_arr

    valid_classes = [
        int(cls)
        for cls in np.unique(class_labels[np.isfinite(class_labels)])
        if int(cls) >= 0
    ]

    eligible_records: list[pd.DataFrame] = []
    cycle_records: list[pd.DataFrame] = []

    for combo in combos:
        combo_name = str(combo["name"])
        ws = str(combo["water_supply"])
        crop_sequence = [str(crop) for crop in combo["crops"]]  # type: ignore[index]
        yield_stack = [yield_data[(crop, ws)] for crop in crop_sequence]

        zone_arr = zone_arrays[ws]
        combined_mask, min_fraction, total_water_arr = compute_eligibility_mask(
            crop_sequence,
            ws,
            zone_arr,
            suitability_data,
            start_data,
            length_data,
            yield_data,
            water_requirement_data,
        )
        if not np.any(combined_mask):
            continue

        eligible_fraction = np.where(combined_mask, min_fraction, np.nan)
        eligible_area = eligible_fraction * cell_area_ha

        for cls in valid_classes:
            class_mask = (class_labels == cls) & combined_mask
            if not np.any(class_mask):
                continue

            # Aggregate area by region
            area_array = np.where(class_mask, eligible_area, np.nan)
            area_stats = aggregate_raster_by_region(
                area_array, regions_for_extract, xmin, ymin, xmax, ymax, crs_wkt
            )
            if area_stats.empty:
                continue
            area_stats = area_stats.rename(columns={"sum": "eligible_area_ha"})
            area_stats = area_stats.replace([np.inf, -np.inf], np.nan).dropna(
                subset=["eligible_area_ha"]
            )
            area_stats = area_stats[area_stats["eligible_area_ha"] > 0]
            if area_stats.empty:
                continue

            # Calculate water requirements for irrigated crops
            if ws == "i" and total_water_arr is not None:
                water_numerator = np.where(
                    class_mask, total_water_arr * eligible_area, np.nan
                )
                water_stats = aggregate_raster_by_region(
                    water_numerator,
                    regions_for_extract,
                    xmin,
                    ymin,
                    xmax,
                    ymax,
                    crs_wkt,
                )
                if not water_stats.empty:
                    water_stats = water_stats.rename(columns={"sum": "water_volume_m3"})
                    area_stats = area_stats.merge(water_stats, on="region", how="left")
                    area_stats = area_stats.replace([np.inf, -np.inf], np.nan).dropna(
                        subset=["water_volume_m3"]
                    )
                    area_stats["water_requirement_m3_per_ha"] = (
                        area_stats["water_volume_m3"] / area_stats["eligible_area_ha"]
                    )
                    area_stats = area_stats.replace([np.inf, -np.inf], np.nan).dropna(
                        subset=["water_requirement_m3_per_ha"]
                    )
                    area_stats.drop(columns=["water_volume_m3"], inplace=True)
            else:
                area_stats["water_requirement_m3_per_ha"] = 0.0

            area_stats["resource_class"] = cls
            area_stats["combination"] = combo_name
            area_stats["water_supply"] = ws
            area_for_cycles = area_stats.copy()
            eligible_records.append(
                area_stats[
                    [
                        "combination",
                        "region",
                        "resource_class",
                        "water_supply",
                        "eligible_area_ha",
                        "water_requirement_m3_per_ha",
                    ]
                ]
            )

            # Calculate yields for each crop cycle
            for idx, (crop_name, yield_arr) in enumerate(
                zip(crop_sequence, yield_stack), start=1
            ):
                numerator = np.where(class_mask, yield_arr * eligible_area, np.nan)
                numerator_stats = aggregate_raster_by_region(
                    numerator, regions_for_extract, xmin, ymin, xmax, ymax, crs_wkt
                )
                if numerator_stats.empty:
                    continue

                numerator_stats = numerator_stats.rename(
                    columns={"sum": "yield_times_area"}
                )
                merged = area_for_cycles.merge(numerator_stats, on="region", how="left")
                merged = merged.replace([np.inf, -np.inf], np.nan).dropna(
                    subset=["yield_times_area"]
                )
                merged["yield_t_per_ha"] = (
                    merged["yield_times_area"] / merged["eligible_area_ha"]
                )
                merged = (
                    merged.replace([np.inf, -np.inf], np.nan)
                    .dropna(subset=["yield_t_per_ha"])
                    .query("yield_t_per_ha > 0")
                )
                if merged.empty:
                    continue
                merged["combination"] = combo_name
                merged["resource_class"] = cls
                merged["water_supply"] = ws
                merged["cycle_index"] = idx
                merged["crop"] = crop_name
                cycle_records.append(
                    merged[
                        [
                            "combination",
                            "region",
                            "resource_class",
                            "water_supply",
                            "cycle_index",
                            "crop",
                            "yield_t_per_ha",
                        ]
                    ]
                )

    if eligible_records:
        eligible_df = pd.concat(eligible_records, ignore_index=True)
        eligible_df["resource_class"] = eligible_df["resource_class"].astype(int)
        eligible_df["eligible_area_ha"] = pd.to_numeric(
            eligible_df["eligible_area_ha"], errors="coerce"
        )
        eligible_df["water_requirement_m3_per_ha"] = pd.to_numeric(
            eligible_df["water_requirement_m3_per_ha"], errors="coerce"
        )
        eligible_df.sort_values(
            ["combination", "water_supply", "region", "resource_class"],
            inplace=True,
            ignore_index=True,
        )
    else:
        eligible_df = pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "eligible_area_ha",
                "water_requirement_m3_per_ha",
            ]
        )

    if cycle_records:
        cycle_df = pd.concat(cycle_records, ignore_index=True)
        cycle_df["resource_class"] = cycle_df["resource_class"].astype(int)
        cycle_df["yield_t_per_ha"] = pd.to_numeric(
            cycle_df["yield_t_per_ha"], errors="coerce"
        )
        cycle_df.sort_values(
            [
                "combination",
                "water_supply",
                "region",
                "resource_class",
                "cycle_index",
            ],
            inplace=True,
            ignore_index=True,
        )
    else:
        cycle_df = pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "cycle_index",
                "crop",
                "yield_t_per_ha",
            ]
        )

    Path(snakemake.output.eligible).parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
    Path(snakemake.output.yields).parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
    eligible_df.to_csv(snakemake.output.eligible, index=False)  # type: ignore[attr-defined]
    cycle_df.to_csv(snakemake.output.yields, index=False)  # type: ignore[attr-defined]
