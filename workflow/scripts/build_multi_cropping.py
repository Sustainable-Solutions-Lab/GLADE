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
from workflow.scripts.water_periods import DAYS_IN_YEAR, calendar_period_shares

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
    split -- and keeps the potential cap aligned with the anchored baseline (design
    section 4 correction; ``p_nom_max = max(potential, anchor)``).
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


def compute_period_water_demand(
    crop_sequence: list[str],
    ws: str,
    mask: np.ndarray,
    start_data: dict,
    length_data: dict,
    water_requirement_data: dict,
    water_periods: int,
    region_index: np.ndarray,
    calendar_tables: dict,
) -> list[np.ndarray]:
    """Per-period irrigation-demand rasters (m3/ha) for a combo, on masked cells.

    Each cycle's net requirement is placed into the intra-year periods by the
    observed MIRCA-OS irrigated calendar for that cycle's crop and the cell's
    region (``calendar_tables[crop]`` indexed by ``region_index``). Cells whose
    region has no MIRCA calendar for the crop fall back to the cycle's GAEZ
    growing season; repeated same-crop cycles in that fallback are staggered by
    ``365/n`` days so the second cycle lands in a different season (MIRCA already
    resolves the seasons where present, so no staggering is applied there). The
    per-cell sum over periods equals the summed cycle requirement (shares sum to
    1 per cycle), so the annual magnitude is preserved.

    Returns a list of ``T`` full-grid rasters; rainfed combos get all-zero rasters.
    """
    periods = int(water_periods)
    demand = [np.zeros(mask.shape, dtype=float) for _ in range(periods)]
    if ws != "i":
        return demand
    idx = np.nonzero(mask)
    if idx[0].size == 0:
        return demand

    cell_regions = region_index[idx]  # (n_masked,) region row, -1 where none
    n_cycles = len(crop_sequence)
    repeated = len(set(crop_sequence)) == 1 and n_cycles >= 2
    for cycle, crop in enumerate(crop_sequence):
        start = start_data[(crop, ws)][idx].astype(float)
        length = length_data[(crop, ws)][idx].astype(float)
        if repeated:
            # GAEZ-fallback stagger for identical windows (mod 365 handled by
            # month_overlaps wrap); overridden per cell where MIRCA is present.
            start = start + cycle * (DAYS_IN_YEAR / n_cycles)
        # Per-cell observed monthly shares for this crop (zeros -> GAEZ fallback).
        table = calendar_tables.get(crop)
        if table is not None:
            monthly = np.where(cell_regions[:, None] >= 0, table[cell_regions], 0.0)
        else:
            monthly = np.zeros((cell_regions.size, 12), dtype=float)
        requirement = water_requirement_data[(crop, ws)][idx].astype(float)
        shares, _ = calendar_period_shares(monthly, start, length, periods)
        for period in range(periods):
            demand[period][idx] += requirement * shares[:, period]
    return demand


if __name__ == "__main__":
    # Parse combinations from config
    combos: list[dict[str, object]] = []
    use_actual_yields = bool(getattr(snakemake.params, "use_actual_yields", False))  # type: ignore[attr-defined]
    water_periods = int(snakemake.params.water_periods)  # type: ignore[attr-defined,name-defined]
    water_cols = [f"water_requirement_m3_per_ha_p{p}" for p in range(water_periods)]

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
    calendar_path = inputs.pop("crop_calendar")
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
                *water_cols,
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

    # Region row position per grid cell (-1 outside any region), derived from
    # the shared cell mapping (a boundary cell resolves to one region), plus
    # per-crop (n_regions, 12) MIRCA-OS monthly share tables, so the per-period
    # water split places each cycle's demand in its observed months per region
    # (build_mirca_crop_calendar). The calendar is area-aggregated downstream,
    # so single-region cell attribution is exact enough for the timing split.
    region_index = np.full(height * width, -1, dtype=np.int32)
    region_index[mapping.cell_ids] = (mapping.group_ids // mapping.n_classes).astype(
        np.int32
    )
    region_index = region_index.reshape(height, width)
    region_pos = {region: i for i, region in enumerate(mapping.regions)}
    calendar_df = pd.read_csv(calendar_path)
    calendar_tables: dict[str, np.ndarray] = {}
    for crop_name, grp in calendar_df.groupby("crop"):
        table = np.zeros((len(mapping.regions), 12), dtype=float)
        rows = grp["region"].map(region_pos)
        valid = rows.notna()
        table[
            rows[valid].astype(int).to_numpy(), grp.loc[valid, "month"].to_numpy() - 1
        ] = grp.loc[valid, "share"].to_numpy()
        calendar_tables[str(crop_name)] = table

    def conversion_factor(crop: str) -> float:
        base_scale = 1.0 if use_actual_yields else KG_TO_TONNE
        if crop in conv_df.index:
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
        combined_mask, min_fraction, _total_water = compute_eligibility_mask(
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

        # Per-cycle, per-period irrigation demand (m3/ha), placed by each cycle's
        # observed MIRCA-OS calendar (GAEZ season, staggered for repeated
        # cycles, where MIRCA is absent). Computed once per combo on the
        # combined mask, then aggregated per period against the shared
        # eligible-area denominator.
        period_demand = compute_period_water_demand(
            crop_sequence,
            ws,
            combined_mask,
            start_data,
            length_data,
            water_requirement_data,
            water_periods,
            region_index,
            calendar_tables,
        )

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

        # Per-period irrigation requirement (m3/ha): aggregate each period's
        # demand*area numerator against the same eligible-area denominator, so
        # the sum over periods reproduces the annual requirement.
        for period, col in enumerate(water_cols):
            if ws != "i":
                area_stats[col] = 0.0
                continue
            volume = np.bincount(
                groups,
                weights=period_demand[period].ravel()[cells] * area_entries,
                minlength=mapping.n_groups,
            )
            area_stats[col] = volume[positive] / area_by_group[positive]

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
                    *water_cols,
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
        for col in water_cols:
            eligible_df[col] = pd.to_numeric(eligible_df[col], errors="coerce").fillna(
                0.0
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
                *water_cols,
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
