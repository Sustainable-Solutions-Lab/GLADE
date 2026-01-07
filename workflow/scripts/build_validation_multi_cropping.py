# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Infer multi-cropping allocations from CROPGRIDS cropping intensity data.

When cropping_intensity > 1 for a crop in a region, some portion of that land
is being multi-cropped. This script allocates the "excess" area to configured
multi-cropping combinations based on which crops have excess and which
combinations are feasible.

Inputs:
    - CROPGRIDS harvested area data with cropping_intensity
    - Multi-cropping eligible areas from GAEZ (for feasibility)
    - Configured multi-cropping combinations

Outputs:
    - inferred_multi_cropping_area.csv: Area allocated to each combination
    - inferred_single_crop_adjustment.csv: Reduction in single-crop areas
"""

from collections import defaultdict
from pathlib import Path

import pandas as pd

# Priority ordering for allocating excess area to combinations
# Higher priority combinations are preferred when multiple are feasible
COMBINATION_PRIORITY = {
    "double_rice": 1,  # Rice-rice is most common double-cropping
    "rice_wheat": 2,  # Rice-wheat rotation
    "maize_soybean": 3,  # Maize-soybean rotation
}


def load_cropgrids_data(harvested_area_dir: Path) -> pd.DataFrame:
    """Load CROPGRIDS data with cropping intensity."""
    records = []
    for csv_path in harvested_area_dir.glob("*.csv"):
        crop_ws = csv_path.stem  # e.g., "wetland-rice_r"
        df = pd.read_csv(csv_path)

        # Pivot to get one row per region/resource_class
        pivot = df.pivot_table(
            index=["region", "resource_class"], columns="variable", values="value"
        ).reset_index()

        if "cropping_intensity" not in pivot.columns:
            continue

        pivot["crop_ws"] = crop_ws
        crop, ws = crop_ws.rsplit("_", 1)
        pivot["crop"] = crop
        pivot["water_supply"] = ws
        records.append(pivot)

    if not records:
        return pd.DataFrame()

    combined = pd.concat(records, ignore_index=True)
    # Calculate excess area (area from multi-cropping)
    combined["excess_area"] = (combined["cropping_intensity"] - 1) * combined[
        "crop_area"
    ]
    combined["excess_area"] = combined["excess_area"].clip(lower=0)
    return combined


def allocate_to_combinations(
    cropgrids_df: pd.DataFrame,
    combinations: dict[str, dict],
    gaez_eligible: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Allocate excess areas to multi-cropping combinations.

    Returns:
        multi_area: DataFrame with columns [combination, region, resource_class,
                                            water_supply, area_ha]
        adjustments: DataFrame with columns [crop_ws, region, resource_class, reduce_area_ha]
    """
    if cropgrids_df.empty:
        empty_multi = pd.DataFrame(
            columns=[
                "combination",
                "region",
                "resource_class",
                "water_supply",
                "area_ha",
            ]
        )
        empty_adj = pd.DataFrame(
            columns=["crop_ws", "region", "resource_class", "reduce_area_ha"]
        )
        return empty_multi, empty_adj

    # Build lookup: crop -> list of combinations it appears in
    crop_to_combos: dict[str, list[str]] = defaultdict(list)
    combo_crops: dict[str, list[str]] = {}
    combo_water: dict[str, list[str]] = {}

    for name, entry in combinations.items():
        crops = [str(c) for c in entry["crops"]]
        water_supplies = entry.get("water_supplies", ["r"])
        if isinstance(water_supplies, str):
            water_supplies = [water_supplies]
        combo_crops[name] = crops
        combo_water[name] = [ws.lower() for ws in water_supplies]
        for crop in crops:
            crop_to_combos[crop].append(name)

    multi_records = []
    adj_records = []

    # Group by region and resource class
    for (region, rc), group in cropgrids_df.groupby(["region", "resource_class"]):
        # Get excess by crop and water supply
        excess_lookup: dict[str, dict[str, float]] = {}
        for _, row in group.iterrows():
            crop = row["crop"]
            ws = row["water_supply"]
            excess = row["excess_area"]
            if excess > 0:
                if crop not in excess_lookup:
                    excess_lookup[crop] = {}
                excess_lookup[crop][ws] = excess

        if not excess_lookup:
            continue

        # Track how much excess we've allocated
        allocated: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Sort combinations by priority
        sorted_combos = sorted(
            combinations.keys(), key=lambda x: COMBINATION_PRIORITY.get(x, 99)
        )

        for combo_name in sorted_combos:
            crops_in_combo = combo_crops[combo_name]
            water_supplies = combo_water[combo_name]

            for ws in water_supplies:
                # Check if all crops in combination have excess in this water supply
                crop_excess = []
                for crop in crops_in_combo:
                    remaining = (
                        excess_lookup.get(crop, {}).get(ws, 0) - allocated[crop][ws]
                    )
                    crop_excess.append(max(0, remaining))

                if not all(e > 0 for e in crop_excess):
                    continue

                # For same-crop combinations (e.g., double_rice), we need half the excess
                # because the link uses land once but produces twice
                unique_crops = set(crops_in_combo)
                if len(unique_crops) == 1:
                    # Same crop repeated (e.g., double_rice)
                    # Allocate based on excess / (n_repeats - 1) to avoid over-allocation
                    crop = crops_in_combo[0]
                    remaining = (
                        excess_lookup.get(crop, {}).get(ws, 0) - allocated[crop][ws]
                    )
                    # Each unit of multi-crop area produces n_repeats harvests
                    # So to get 'remaining' extra harvests, we need remaining/(n_repeats-1) land
                    # because single crop also produces 1 harvest from the same land
                    # Wait, this is confusing. Let me think again:
                    # - Single crop: 1 Mha produces 1 harvest
                    # - Double crop: 1 Mha produces 2 harvests
                    # - Excess = extra harvests = (CI - 1) * crop_area
                    # - If we convert X Mha from single to double, we gain X harvests
                    # - So allocate_area = excess
                    allocate_area = remaining
                    if allocate_area <= 0:
                        continue
                    # But we also need to reduce single-crop area by allocate_area
                    # so total land used stays at crop_area
                    allocated[crop][ws] += allocate_area
                else:
                    # Different crops (e.g., rice_wheat)
                    # Allocate the minimum excess among crops
                    allocate_area = min(crop_excess)
                    if allocate_area <= 0:
                        continue
                    for crop in crops_in_combo:
                        allocated[crop][ws] += allocate_area

                multi_records.append(
                    {
                        "combination": combo_name,
                        "region": region,
                        "resource_class": int(rc),
                        "water_supply": ws,
                        "area_ha": allocate_area,
                    }
                )

        # Create adjustment records for allocated excess
        for crop, ws_alloc in allocated.items():
            for ws, reduce_amt in ws_alloc.items():
                if reduce_amt > 0:
                    adj_records.append(
                        {
                            "crop_ws": f"{crop}_{ws}",
                            "region": region,
                            "resource_class": int(rc),
                            "reduce_area_ha": reduce_amt,
                        }
                    )

    multi_df = pd.DataFrame(multi_records)
    adj_df = pd.DataFrame(adj_records)

    return multi_df, adj_df


if __name__ == "__main__":
    harvested_area_dir = Path(snakemake.input.harvested_area_dir)  # type: ignore[name-defined]
    combinations = dict(snakemake.params.combinations)  # type: ignore[name-defined]

    # Load optional GAEZ eligibility for filtering
    gaez_eligible = None
    if hasattr(snakemake.input, "gaez_eligible"):  # type: ignore[name-defined]
        gaez_eligible = pd.read_csv(snakemake.input.gaez_eligible)  # type: ignore[name-defined]

    cropgrids_df = load_cropgrids_data(harvested_area_dir)
    multi_df, adj_df = allocate_to_combinations(
        cropgrids_df, combinations, gaez_eligible
    )

    output_dir = Path(snakemake.output.multi_area).parent  # type: ignore[name-defined]
    output_dir.mkdir(parents=True, exist_ok=True)

    multi_df.to_csv(snakemake.output.multi_area, index=False)  # type: ignore[name-defined]
    adj_df.to_csv(snakemake.output.adjustments, index=False)  # type: ignore[name-defined]

    # Print summary
    if not multi_df.empty:
        print("Inferred multi-cropping allocations:")
        summary = multi_df.groupby(["combination", "water_supply"])["area_ha"].sum()
        print(summary.to_string())
        print(f"\nTotal multi-cropping area: {multi_df['area_ha'].sum() / 1e6:.2f} Mha")
