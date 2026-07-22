"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from workflow.scripts.multi_cropping_combinations import effective_combinations
from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    load_raster_array,
    scale_fraction,
)
from workflow.scripts.region_class_aggregation import load_cell_mapping

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


def compute_eligibility_mask(
    crop_sequence: list[str],
    ws: str,
    zone_arr: np.ndarray,
    suitability_data: dict,
    yield_data: dict,
    water_requirement_data: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Compute combined eligibility mask and return mask, min_fraction, total_water.

    The eligible (expansion-potential) area for a combination is the GAEZ-suitable
    area where the multiple-cropping zone permits the cycle count (and, for
    irrigated, the water requirement is defined). ``sequence_feasible`` on GAEZ
    windows is deliberately NOT used as a gate: GAEZ attainable season lengths
    overshoot the farmed cycle, so it rejects ~all observed double-cropping
    (including repeated same-crop combos fed identical windows). This mirrors the
    Stage-1 decoupling -- feasibility from observation, GAEZ only for the water
    split -- and keeps the potential cap aligned with the anchored baseline
    (``p_nom_max = max(potential, anchor)``).
    """
    # Zone capability check (enforces cycle count and wetland-rice-cycle limit)
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

    combined_mask = valid_suit & positive_yield & valid_water & zone_mask
    return combined_mask, min_fraction, total_water_arr


if __name__ == "__main__":
    # Parse combinations from config
    combos: list[dict[str, object]] = []
    use_actual_yields = bool(getattr(snakemake.params, "use_actual_yields", False))  # type: ignore[attr-defined]

    combinations = effective_combinations(
        snakemake.config,  # type: ignore[attr-defined,name-defined]
        snakemake.input.combinations,  # type: ignore[attr-defined,name-defined]
    )
    for name, entry in combinations.items():
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
    mapping_path = inputs.pop("cell_mapping")
    inputs.pop("combinations")
    conv_csv = inputs.pop("yield_unit_conversions")
    moisture_csv = inputs.pop("moisture_content")

    # Group crop rasters by (crop, water_supply)
    crop_files: dict[tuple[str, str], dict[str, str]] = {}
    suffixes = {
        "_yield_raster": "yield",
        "_suitability_raster": "suitability",
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
        moisture_lookup = (
            pd.read_csv(moisture_csv, comment="#")
            .set_index("crop")["moisture_fraction"]
            .to_dict()
        )

    mapping = load_cell_mapping(mapping_path)

    # Use any available crop/ws pair to get raster dimensions
    sample_crop, sample_ws = next(iter(crop_files.keys()))
    with rasterio.open(crop_files[(sample_crop, sample_ws)]["yield"]) as yield_src:
        height, width = yield_src.shape
        if mapping.shape != (height, width):
            raise ValueError("Cell mapping does not match GAEZ raster dimensions")
        cell_area_ha = calculate_all_cell_areas(yield_src)

    zone_arrays: dict[str, np.ndarray] = {}
    for ws, path in zone_paths.items():
        zone_arr = load_raster_array(path)
        if zone_arr.shape != (height, width):
            raise ValueError(
                f"Multiple cropping zone raster for water supply '{ws}' has unexpected dimensions"
            )
        zone_arrays[ws] = zone_arr.astype(np.int16, copy=False)

    def conversion_factor(crop: str) -> float:
        base_scale = 1.0 if use_actual_yields else KG_TO_TONNE
        if crop in conv_df.index:
            override = float(conv_df.at[crop, "factor_to_t_per_ha"])
            return base_scale * (override / KG_TO_TONNE)
        return base_scale

    yield_data: dict[tuple[str, str], np.ndarray] = {}
    suitability_data: dict[tuple[str, str], np.ndarray] = {}
    water_requirement_data: dict[tuple[str, str], np.ndarray] = {}

    for (crop, ws), files in crop_files.items():
        factor = conversion_factor(crop)
        y_arr = load_raster_array(files["yield"])
        suitability_arr = load_raster_array(files["suitability"])
        if y_arr.shape != (height, width):
            raise ValueError(
                f"Yield raster for '{crop}' ({ws}) has unexpected dimensions"
            )
        if suitability_arr.shape != (height, width):
            raise ValueError(
                f"Suitability raster for '{crop}' ({ws}) has unexpected dimensions"
            )

        y_scaled = y_arr * factor
        if use_actual_yields and crop in moisture_lookup:
            # GAEZ actual yields are fresh weight; convert to dry matter
            y_scaled = y_scaled * (1.0 - moisture_lookup[crop])
        yield_data[(crop, ws)] = y_scaled
        suitability_data[(crop, ws)] = scale_fraction(suitability_arr)

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
            # GAEZ water rasters are in mm of depth; convert to m^3/ha
            # (1 mm over 1 ha = 10 m^3) so downstream aggregations and the
            # build_model coefficient (Mm3/Mha == m3/ha numerically) line up
            # with the single-crop path in build_crop_yields.
            water_requirement_data[(crop, ws)] = water_arr * 10.0

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
            yield_data,
            water_requirement_data,
        )
        if not np.any(combined_mask):
            continue

        eligible_fraction = np.where(combined_mask, min_fraction, np.nan)
        eligible_area = eligible_fraction * cell_area_ha

        selected = combined_mask.ravel()[mapping.cell_ids]
        cells = mapping.cell_ids[selected]
        groups = mapping.group_ids[selected]
        area_entries = eligible_area.ravel()[cells] * mapping.coverage[selected]
        area_by_group = np.bincount(
            groups, weights=area_entries, minlength=mapping.n_groups
        )
        positive = area_by_group > 0
        if not positive.any():
            continue
        positive_groups = np.flatnonzero(positive)
        region_ids, resource_classes = np.divmod(positive_groups, mapping.n_classes)
        area_stats = pd.DataFrame(
            {
                "region": mapping.regions[region_ids],
                "eligible_area_ha": area_by_group[positive],
            }
        )

        # Annual irrigation requirement (m3/ha): aggregate the summed-cycle
        # demand*area numerator against the same eligible-area denominator.
        if ws == "i" and total_water_arr is not None:
            volume = np.bincount(
                groups,
                weights=total_water_arr.ravel()[cells] * area_entries,
                minlength=mapping.n_groups,
            )
            area_stats["water_requirement_m3_per_ha"] = (
                volume[positive] / area_by_group[positive]
            )
        else:
            area_stats["water_requirement_m3_per_ha"] = 0.0

        area_stats["resource_class"] = resource_classes
        area_stats["combination"] = combo_name
        area_stats["water_supply"] = ws
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
            numerator = np.bincount(
                groups,
                weights=yield_arr.ravel()[cells] * area_entries,
                minlength=mapping.n_groups,
            )
            yield_t_per_ha = numerator[positive] / area_by_group[positive]
            keep = yield_t_per_ha > 0
            if not keep.any():
                continue
            cycle_records.append(
                pd.DataFrame(
                    {
                        "combination": combo_name,
                        "region": mapping.regions[region_ids][keep],
                        "resource_class": resource_classes[keep],
                        "water_supply": ws,
                        "cycle_index": idx,
                        "crop": crop_name,
                        "yield_t_per_ha": yield_t_per_ha[keep],
                    }
                )
            )

    if eligible_records:
        eligible_df = pd.concat(eligible_records, ignore_index=True)
        eligible_df["resource_class"] = eligible_df["resource_class"].astype(int)
        eligible_df["eligible_area_ha"] = pd.to_numeric(
            eligible_df["eligible_area_ha"], errors="coerce"
        )
        eligible_df["water_requirement_m3_per_ha"] = pd.to_numeric(
            eligible_df["water_requirement_m3_per_ha"], errors="coerce"
        ).fillna(0.0)
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
