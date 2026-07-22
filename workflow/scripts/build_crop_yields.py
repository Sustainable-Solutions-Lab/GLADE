"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    read_raster_float,
    scale_fraction,
)
from workflow.scripts.region_class_aggregation import (
    load_cell_mapping,
    validate_raster_grid,
    weighted_mean_by_group,
    weighted_sum_by_group,
)

if __name__ == "__main__":
    # Inputs
    mapping_path: str = snakemake.input.cell_mapping  # type: ignore[name-defined]
    yield_path: str = snakemake.input.yield_raster  # type: ignore[name-defined]
    suit_path: str = snakemake.input.suitability_raster  # type: ignore[name-defined]
    water_path: str | None = getattr(  # type: ignore[attr-defined]
        snakemake.input, "water_requirement_raster", None
    )
    gs_start_path: str = snakemake.input.growing_season_start_raster  # type: ignore[name-defined]
    gs_length_path: str = snakemake.input.growing_season_length_raster  # type: ignore[name-defined]
    crop_code: str = snakemake.wildcards.crop  # type: ignore[name-defined]
    conv_csv: str = snakemake.input.yield_unit_conversions  # type: ignore[name-defined]
    moisture_csv: str = snakemake.input.moisture_content  # type: ignore[name-defined]

    KG_TO_TONNE = 0.001

    mapping = load_cell_mapping(mapping_path)

    conversion_overrides: dict[str, float] = (
        pd.read_csv(conv_csv, comment="#")
        .set_index("code")["factor_to_t_per_ha"]
        .to_dict()
    )

    use_actual_yields = bool(snakemake.params.use_actual_yields)  # type: ignore[name-defined]

    moisture_lookup: dict[str, float] = (
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

    y_raw, y_src = read_raster_float(yield_path)
    validate_raster_grid(y_raw, y_src, mapping)
    y_tpha = y_raw * _yield_multiplier(crop_code)
    if use_actual_yields:
        moisture_fraction = float(moisture_lookup[crop_code])
        y_tpha = y_tpha * (1.0 - moisture_fraction)
    yield_by_group = weighted_mean_by_group(y_tpha, mapping)
    cell_area_ha_1d = calculate_all_cell_areas(y_src, repeat=False)
    y_src.close()
    del y_raw, y_tpha

    s_raw, s_src = read_raster_float(suit_path)
    validate_raster_grid(s_raw, s_src, mapping)
    s_src.close()
    s_frac = scale_fraction(s_raw)
    area_ha = s_frac * cell_area_ha_1d[:, np.newaxis]
    area_by_group = weighted_sum_by_group(area_ha, mapping)
    del s_raw, s_frac, area_ha

    if water_path:
        water_raw_mm, water_src = read_raster_float(water_path)
        validate_raster_grid(water_raw_mm, water_src, mapping)
        water_src.close()
        water_m3_per_ha = water_raw_mm * 10.0  # 1 mm over 1 ha equals 10 m3
        water_by_group = weighted_mean_by_group(water_m3_per_ha, mapping)
        del water_raw_mm, water_m3_per_ha
    else:
        weight = np.bincount(
            mapping.group_ids,
            weights=mapping.coverage,
            minlength=mapping.n_groups,
        )
        water_by_group = np.full(mapping.n_groups, np.nan)
        water_by_group[weight != 0] = 0.0

    gs_start_raw, gs_start_src = read_raster_float(gs_start_path)
    validate_raster_grid(gs_start_raw, gs_start_src, mapping)
    gs_start_src.close()
    gs_start_by_group = weighted_mean_by_group(gs_start_raw, mapping)
    del gs_start_raw
    gs_length_raw, gs_length_src = read_raster_float(gs_length_path)
    validate_raster_grid(gs_length_raw, gs_length_src, mapping)
    gs_length_src.close()
    gs_length_by_group = weighted_mean_by_group(gs_length_raw, mapping)
    del gs_length_raw

    variable_values_and_units = {
        "yield": (yield_by_group, "t/ha (DM)"),
        "suitable_area": (area_by_group, "ha"),
        "water_requirement_m3_per_ha": (water_by_group, "m^3/ha"),
        "growing_season_start_day": (gs_start_by_group, "day-of-year"),
        "growing_season_length_days": (gs_length_by_group, "days"),
    }

    region_index = np.repeat(mapping.regions, mapping.n_classes)
    class_index = np.tile(np.arange(mapping.n_classes), len(mapping.regions))
    tidy_frames = []
    for variable, (values, unit) in variable_values_and_units.items():
        valid = ~np.isnan(values)
        if not np.any(valid):
            continue
        tidy_frames.append(
            pd.DataFrame(
                {
                    "region": region_index[valid],
                    "resource_class": class_index[valid],
                    "variable": variable,
                    "unit": unit,
                    "value": values[valid],
                }
            )
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
