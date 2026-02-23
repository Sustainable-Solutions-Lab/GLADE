# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge LUIcube and ISIMIP grassland yields.

Prefers LUIcube yields where available (finite yield > 0); falls back to
ISIMIP for gaps.  Applies utilization corrections so the output ``yield``
column is effective feed yield ready for direct use:

- **LUIcube rows**: ``yield`` is already effective per managed hectare
  (hanpp_harv / managed_area / C_FRACTION), used directly.
- **ISIMIP rows**: ``yield = raw_yield * isimip_utilization_rate``

Output columns: yield, suitable_area
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def _load_tidy_variable(path: str, variable: str, value_name: str) -> pd.DataFrame:
    """Load a tidy regional table and return (region, class, value) rows."""
    df = pd.read_csv(path, comment="#")
    if df.empty:
        return pd.DataFrame(columns=["region", "resource_class", value_name])

    if "variable" in df.columns and "value" in df.columns:
        df = df[df["variable"] == variable].copy()
        if df.empty:
            return pd.DataFrame(columns=["region", "resource_class", value_name])
        df = df.rename(columns={"value": value_name})
    elif value_name in df.columns:
        df = df.copy()
    else:
        raise ValueError(
            f"{path} must contain either tidy columns (variable,value) "
            f"or '{value_name}'"
        )

    cols = ["region", "resource_class", value_name]
    missing_cols = [col for col in cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"{path} missing required columns: {', '.join(missing_cols)}")
    return df[cols]


def _compute_forage_supply_by_country(
    snakemake_input,
    forage_crops: list[str],
    region_to_country: pd.Series,
) -> pd.Series:
    """Return modeled forage-crop supply in Mt DM per country."""
    records: list[pd.DataFrame] = []
    input_keys = set(snakemake_input.keys())
    for crop in forage_crops:
        for water_supply in ("r", "i"):
            yield_key = f"forage_yield_{crop}_{water_supply}"
            area_key = f"forage_harvested_{crop}_{water_supply}"
            if yield_key not in input_keys or area_key not in input_keys:
                continue

            yield_df = _load_tidy_variable(
                snakemake_input[yield_key],
                variable="yield",
                value_name="yield_t_per_ha",
            )
            area_df = _load_tidy_variable(
                snakemake_input[area_key],
                variable="harvested_area",
                value_name="harvested_area_ha",
            )
            merged = yield_df.merge(
                area_df, on=["region", "resource_class"], how="inner"
            )
            if merged.empty:
                continue

            merged["production_mt_dm"] = (
                merged["yield_t_per_ha"] * merged["harvested_area_ha"] * 1e-6
            )
            merged["country"] = merged["region"].map(region_to_country)
            merged = merged[merged["country"].notna()]
            if merged.empty:
                continue
            records.append(merged[["country", "production_mt_dm"]])

    if not records:
        return pd.Series(dtype=float, name="forage_crop_mt_dm")

    out = pd.concat(records, ignore_index=True)
    return out.groupby("country", as_index=True)["production_mt_dm"].sum()


if __name__ == "__main__":
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    isimip_path: str = snakemake.input.isimip  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    isimip_utilization_rate: float = float(snakemake.params.isimip_utilization_rate)  # type: ignore[name-defined]
    forage_overlap_subtraction_alpha: float = float(  # type: ignore[name-defined]
        snakemake.params.forage_overlap_subtraction_alpha
    )
    forage_overlap_crops: list[str] = list(snakemake.params.forage_overlap_crops)  # type: ignore[name-defined]

    idx_cols = ["region", "resource_class"]

    luicube = pd.read_csv(luicube_path, comment="#").set_index(idx_cols).sort_index()
    isimip = pd.read_csv(isimip_path, comment="#").set_index(idx_cols).sort_index()

    # Determine where LUIcube observations are available before any correction.
    luicube_valid = luicube["yield"].apply(np.isfinite) & (luicube["yield"] > 0)

    if forage_overlap_subtraction_alpha > 0:
        regions = gpd.read_file(regions_path)[["region", "country"]]
        region_to_country = regions.set_index("region")["country"].astype(str)

        grass = luicube.loc[luicube_valid, ["yield", "suitable_area"]].reset_index()
        grass["country"] = grass["region"].map(region_to_country)
        grass = grass[grass["country"].notna()].copy()
        grass["grass_mt_dm"] = grass["yield"] * grass["suitable_area"] * 1e-6
        grass_by_country = grass.groupby("country", as_index=True)["grass_mt_dm"].sum()

        forage_by_country = _compute_forage_supply_by_country(
            snakemake.input,
            forage_overlap_crops,
            region_to_country,
        )
        countries = grass_by_country.index.union(forage_by_country.index)
        overlap = pd.DataFrame(index=countries)
        overlap["grass_mt_dm"] = grass_by_country.reindex(countries, fill_value=0.0)
        overlap["forage_mt_dm"] = forage_by_country.reindex(countries, fill_value=0.0)

        overlap["target_grass_mt_dm"] = np.maximum(
            overlap["grass_mt_dm"]
            - forage_overlap_subtraction_alpha * overlap["forage_mt_dm"],
            0.0,
        )
        overlap["yield_factor"] = np.where(
            overlap["grass_mt_dm"] > 0,
            overlap["target_grass_mt_dm"] / overlap["grass_mt_dm"],
            1.0,
        )

        factor_by_region = (
            luicube.reset_index()["region"]
            .map(region_to_country)
            .map(overlap["yield_factor"])
            .fillna(1.0)
            .to_numpy(dtype=float)
        )
        luicube["yield"] = luicube["yield"].to_numpy(dtype=float) * factor_by_region

        total_grass_before = float(overlap["grass_mt_dm"].sum())
        total_grass_after = float(overlap["target_grass_mt_dm"].sum())
        total_forage = float(overlap["forage_mt_dm"].sum())
        print(
            "Applied forage-overlap correction to LUIcube yields: "
            f"alpha={forage_overlap_subtraction_alpha:.3f}, "
            f"forage crops={', '.join(forage_overlap_crops)}"
        )
        print(
            "  Grass supply before/after: "
            f"{total_grass_before:.2f} -> {total_grass_after:.2f} Mt DM "
            f"(forage-crop supply: {total_forage:.2f} Mt DM)"
        )

    # Start from ISIMIP as the base (covers all region/class combinations).
    # Apply isimip_utilization_rate to convert raw ISIMIP yield to effective feed yield.
    merged = isimip[["yield", "suitable_area"]].copy()
    merged["yield"] = merged["yield"] * isimip_utilization_rate

    # Overwrite with LUIcube where valid (yields are already per managed hectare)
    valid_idx = luicube_valid[luicube_valid].index.intersection(merged.index)
    merged.loc[valid_idx, "yield"] = luicube.loc[valid_idx, "yield"]
    merged.loc[valid_idx, "suitable_area"] = luicube.loc[valid_idx, "suitable_area"]

    # Also add LUIcube-only rows not present in ISIMIP
    luicube_only = luicube_valid[luicube_valid].index.difference(merged.index)
    if not luicube_only.empty:
        extra = luicube.loc[luicube_only, ["yield", "suitable_area"]].copy()
        merged = pd.concat([merged, extra]).sort_index()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path)
