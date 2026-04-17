# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Crop production components for the food systems model.

This module handles all crop-related production links including regional
crop production, multi-cropping systems, grassland feed production, and
spared land allocation with carbon sequestration.
"""

from collections.abc import Mapping
import logging

import numpy as np
import pandas as pd
import pypsa

from .. import constants
from .utils import merge_lef

logger = logging.getLogger(__name__)


def _redistribute_excess_baseline(df: pd.DataFrame) -> pd.Series:
    """Cap baseline_area_mha at p_nom_max, redistributing excess within each crop x country.

    When FAOSTAT harvested area disaggregated to a region exceeds the land
    available there (p_nom_max), the excess is proportionally redistributed
    to other links of the same crop x country that still have spare capacity.
    This preserves national totals as far as capacity allows.
    """
    baseline = df["baseline_area_mha"].copy()
    cap = df["p_nom_max"]

    excess_mask = baseline > cap
    if not excess_mask.any():
        return baseline

    # Cap over-allocated links
    excess = (baseline - cap).clip(lower=0)
    baseline = baseline.clip(upper=cap)

    # Redistribute per crop x country group
    group_keys = df["crop"].astype(str) + ":" + df["country"].astype(str)
    total_excess_before = float(excess.sum())
    unplaced = 0.0

    for _key, idx in baseline.groupby(group_keys).groups.items():
        group_excess = float(excess.loc[idx].sum())
        if group_excess <= 0:
            continue

        spare = (cap.loc[idx] - baseline.loc[idx]).clip(lower=0)
        total_spare = float(spare.sum())
        if total_spare <= 0:
            unplaced += group_excess
            continue

        # Distribute proportionally to spare capacity
        allocated = min(group_excess, total_spare)
        baseline.loc[idx] += spare / total_spare * allocated
        if group_excess > total_spare:
            unplaced += group_excess - total_spare

    # Final safety clip (numerical precision)
    baseline = baseline.clip(upper=cap)

    logger.info(
        "Baseline redistribution: capped %.1f Mha excess, "
        "%.1f Mha unplaceable (no spare capacity in same crop x country)",
        total_excess_before,
        unplaced,
    )
    return baseline


def add_regional_crop_production_links(
    n: pypsa.Network,
    crop_list: list,
    yields_data: dict,
    region_to_country: pd.Series,
    allowed_countries: set,
    crop_costs: pd.Series,
    global_median_cost: pd.Series,
    fertilizer_n_rates: Mapping[str, float],
    rice_methane_factor: float,
    rainfed_wetland_rice_ch4_scaling_factor: float,
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
    use_actual_production: bool = False,
    *,
    cost_calibration: pd.Series | None = None,
    min_yield_t_per_ha: float,
) -> None:
    """Add crop production links per region/resource class and water supply.

    Rainfed yields must be present for every crop; irrigated yields are used when
    provided by the preprocessing pipeline. Output links produce into the same
    crop bus per country; link names encode supply type (i/r) and resource class.

    Parameters
    ----------
    crop_costs : pd.Series
        MultiIndex (crop, country) → cost USD/ha in base year.
    global_median_cost : pd.Series
        Index crop → global median cost USD/ha (fallback).
    cost_calibration : pd.Series | None
        MultiIndex (crop, country) → correction in bnUSD/Mha (additive).
    """
    residue_lookup = residue_lookup or {}

    # Add crop production carrier
    if "crop_production" not in n.carriers.static.index:
        n.carriers.add("crop_production", unit="Mt")

    all_rows: list[pd.DataFrame] = []
    bus_index = n.buses.static.index

    for crop in crop_list:
        fert_n_rate_kg_per_ha = float(fertilizer_n_rates.get(crop, 0.0))

        fert_efficiency = (
            -fert_n_rate_kg_per_ha * 1e6 * constants.KG_TO_MEGATONNE
        )  # kg N/ha -> Mt N/Mha

        available_supplies = [
            ws for ws in ("r", "i") if f"{crop}_yield_{ws}" in yields_data
        ]

        for ws in available_supplies:
            water_label = "irrigated" if ws == "i" else "rainfed"
            key = f"{crop}_yield_{ws}"
            crop_yields = yields_data[key].copy()

            df = crop_yields.reset_index()
            df["name"] = (
                "produce:"
                + crop
                + "_"
                + water_label
                + ":"
                + df["region"]
                + "_c"
                + df["resource_class"].astype(int).astype(str)
            )
            df.set_index("name", inplace=True)
            df.index.name = None

            df = df[(df["suitable_area"] > 0) & (df["yield"] > 0)]
            if min_yield_t_per_ha > 0:
                df = df[df["yield"] >= min_yield_t_per_ha]

            if use_actual_production:
                df["fixed_area_ha"] = pd.to_numeric(
                    df["harvested_area"], errors="coerce"
                )
                df = df[df["fixed_area_ha"] > 0]

            df["country"] = df["region"].map(region_to_country)
            df = df[df["country"].isin(allowed_countries)]
            if df.empty:
                continue

            bus0_series = (
                "land:cropland:"
                + df["region"]
                + "_c"
                + df["resource_class"].astype(int).astype(str)
                + "_"
                + ws
            )
            missing_bus_mask = ~bus0_series.isin(bus_index)
            if missing_bus_mask.any():
                missing_buses = bus0_series[missing_bus_mask].unique()
                preview = ", ".join(missing_buses[:5])
                logger.debug(
                    "Skipping %d %s links due to missing land buses (examples: %s)",
                    int(missing_bus_mask.sum()),
                    crop,
                    preview,
                )
                df = df.loc[~missing_bus_mask].copy()
                bus0_series = bus0_series.loc[df.index]
            if df.empty:
                continue

            if ws == "i":
                water_bus = ("water:" + df["region"].astype(str)).to_numpy(dtype=object)
                water_eff = -pd.to_numeric(
                    df["water_requirement_m3_per_ha"], errors="coerce"
                ).to_numpy(dtype=float)
            else:
                water_bus = np.full(len(df), "", dtype=object)
                water_eff = np.zeros(len(df), dtype=float)

            if crop == "wetland-rice" and rice_methane_factor > 0:
                scaling_factor = (
                    1.0 if ws == "i" else rainfed_wetland_rice_ch4_scaling_factor
                )
                ch4_bus = np.full(len(df), "emission:ch4", dtype=object)
                ch4_eff = np.full(
                    len(df),
                    rice_methane_factor
                    * scaling_factor
                    * 1e3,  # kg CH4/ha -> t CH4/Mha
                    dtype=float,
                )
            else:
                ch4_bus = np.full(len(df), "", dtype=object)
                ch4_eff = np.zeros(len(df), dtype=float)

            row_df = pd.DataFrame(index=df.index)
            row_df["crop"] = crop
            row_df["water_code"] = ws
            row_df["country"] = df["country"].astype(str).to_numpy()
            row_df["region"] = df["region"].astype(str).to_numpy()
            row_df["resource_class"] = df["resource_class"].astype(int).to_numpy()
            row_df["water_supply"] = water_label
            row_df["bus0"] = bus0_series.astype(str).to_numpy()
            row_df["bus1"] = (
                "crop:" + crop + ":" + df["country"].astype(str)
            ).to_numpy()
            row_df["efficiency"] = pd.to_numeric(df["yield"], errors="coerce").to_numpy(
                dtype=float
            )
            ha = pd.to_numeric(df["harvested_area"], errors="coerce").to_numpy(
                dtype=float
            )
            row_df["baseline_area_mha"] = ha / constants.HA_PER_MHA
            row_df["bus2"] = water_bus
            row_df["efficiency2"] = water_eff
            row_df["bus3"] = ("fertilizer:" + df["country"].astype(str)).to_numpy()
            row_df["efficiency3"] = fert_efficiency
            row_df["bus4"] = ch4_bus
            row_df["efficiency4"] = ch4_eff
            row_df["harvested_area_ha"] = ha
            row_df["p_nom_max"] = (
                pd.to_numeric(df["suitable_area"], errors="coerce").to_numpy(
                    dtype=float
                )
                / 1e6
            )

            if use_actual_production:
                fixed_area_mha = (
                    pd.to_numeric(df["fixed_area_ha"], errors="coerce").to_numpy(
                        dtype=float
                    )
                    / 1e6
                )
                row_df["p_nom"] = fixed_area_mha
                row_df["p_nom_max"] = fixed_area_mha
                row_df["p_nom_min"] = fixed_area_mha
                row_df["p_min_pu"] = 1.0

            all_rows.append(row_df)

    if not all_rows:
        return

    all_df = pd.concat(all_rows, axis=0)
    all_df.index = all_df.index.astype(str)

    # Cap baseline_area_mha at p_nom_max and redistribute excess to other
    # links of the same crop x country so that national totals are preserved
    # while respecting per-link land availability.
    all_df["baseline_area_mha"] = _redistribute_excess_baseline(all_df)

    # Look up per-(crop, country) cost, falling back to global median
    cost_keys = list(zip(all_df["crop"].astype(str), all_df["country"].astype(str)))
    per_link_cost = pd.Series(
        [crop_costs.get(k, global_median_cost.get(k[0], 0.0)) for k in cost_keys],
        index=all_df.index,
        dtype=float,
    )
    # Convert USD/ha to bnUSD/Mha
    all_df["marginal_cost"] = per_link_cost * 1e6 * constants.USD_TO_BNUSD

    # Apply additive calibration correction if available
    if cost_calibration is not None:
        cal_values = pd.Series(
            [cost_calibration.get(k, 0.0) for k in cost_keys],
            index=all_df.index,
            dtype=float,
        )
        all_df["marginal_cost"] += cal_values
        n_negative = int((all_df["marginal_cost"] < 0).sum())
        if n_negative > 0:
            all_df["marginal_cost"] = all_df["marginal_cost"].clip(lower=0.0)
            logger.info(
                "Clipped %d links with negative marginal_cost to zero",
                n_negative,
            )
        n_calibrated = int((cal_values != 0.0).sum())
        logger.info(
            "Applied crop cost calibration: %d/%d links adjusted",
            n_calibrated,
            len(all_df),
        )

    keys = list(
        zip(
            all_df["crop"].astype(str),
            all_df["water_code"].astype(str),
            all_df["region"].astype(str),
            all_df["resource_class"].astype(int),
        )
    )
    countries = all_df["country"].astype(str).to_numpy()
    residue_bus5 = np.empty(len(keys), dtype=object)
    residue_eff5 = np.zeros(len(keys), dtype=float)

    for i, (key, country) in enumerate(zip(keys, countries, strict=False)):
        feed_map = residue_lookup.get(key, {})
        if not feed_map:
            residue_bus5[i] = ""
            continue
        if len(feed_map) > 1:
            feed_items = ", ".join(sorted(feed_map))
            raise ValueError(
                "Expected at most one residue output per crop production link, "
                f"got {len(feed_map)} for key {key}: {feed_items}"
            )
        feed_item, residue_yield = next(iter(feed_map.items()))
        residue_bus5[i] = f"residue:{feed_item}:{country}"
        residue_eff5[i] = float(residue_yield)

    all_df["bus5"] = residue_bus5
    all_df["efficiency5"] = residue_eff5

    add_kwargs: dict[str, object] = {
        "carrier": "crop_production",
        "bus0": all_df["bus0"],
        "bus1": all_df["bus1"],
        "efficiency": all_df["efficiency"],
        "bus2": all_df["bus2"],
        "efficiency2": all_df["efficiency2"],
        "bus3": all_df["bus3"],
        "efficiency3": all_df["efficiency3"],
        "bus4": all_df["bus4"],
        "efficiency4": all_df["efficiency4"],
        "bus5": all_df["bus5"],
        "efficiency5": all_df["efficiency5"],
        "marginal_cost": all_df["marginal_cost"],
        "p_nom_max": all_df["p_nom_max"],
        "p_nom_extendable": not use_actual_production,
        "crop": all_df["crop"],
        "country": all_df["country"],
        "region": all_df["region"],
        "resource_class": all_df["resource_class"],
        "water_supply": all_df["water_supply"],
        "baseline_area_mha": all_df["baseline_area_mha"],
    }

    if use_actual_production:
        add_kwargs["p_nom"] = all_df["p_nom"]
        add_kwargs["p_nom_min"] = all_df["p_nom_min"]
        add_kwargs["p_min_pu"] = all_df["p_min_pu"]

    n.links.add(all_df.index, **add_kwargs)


def add_multi_cropping_links(
    n: pypsa.Network,
    eligible_area: pd.DataFrame,
    cycle_yields: pd.DataFrame,
    region_to_country: pd.Series,
    allowed_countries: set[str],
    crop_costs: pd.Series,
    global_median_cost: pd.Series,
    fertilizer_n_rates: Mapping[str, float],
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
    *,
    min_yield_t_per_ha: float,
) -> None:
    """Add multi-cropping production links with a vectorised workflow."""

    if eligible_area.empty or cycle_yields.empty:
        logger.info("No multi-cropping combinations with positive area; skipping")
        return

    residue_lookup = residue_lookup or {}

    key_cols = ["combination", "region", "resource_class", "water_supply"]

    area_df = eligible_area.copy()
    area_df["resource_class"] = area_df["resource_class"].astype(int)
    area_df["water_supply"] = area_df["water_supply"].astype(str)
    area_df["eligible_area_ha"] = pd.to_numeric(
        area_df["eligible_area_ha"], errors="coerce"
    )
    area_df["water_requirement_m3_per_ha"] = pd.to_numeric(
        area_df.get("water_requirement_m3_per_ha", 0.0), errors="coerce"
    )

    region_to_country = region_to_country.astype(str)
    area_df["country"] = area_df["region"].map(region_to_country)
    area_df = area_df.dropna(subset=["eligible_area_ha", "country"])
    area_df = area_df[area_df["eligible_area_ha"] > 0]
    if allowed_countries:
        area_df = area_df[area_df["country"].isin(allowed_countries)]

    if area_df.empty:
        logger.info("No eligible multi-cropping areas after filtering; skipping")
        return

    cycle_df = cycle_yields.copy()
    cycle_df["resource_class"] = cycle_df["resource_class"].astype(int)
    cycle_df["water_supply"] = cycle_df["water_supply"].astype(str)
    cycle_df["yield_t_per_ha"] = pd.to_numeric(
        cycle_df["yield_t_per_ha"], errors="coerce"
    )
    cycle_df = cycle_df.dropna(subset=["yield_t_per_ha", "crop"])
    cycle_df = cycle_df[cycle_df["yield_t_per_ha"] > 0]

    # Filter low yields for numerical stability
    if min_yield_t_per_ha > 0:
        low_yield_mask = cycle_df["yield_t_per_ha"] < min_yield_t_per_ha
        cycle_df = cycle_df[~low_yield_mask]

    if cycle_df.empty:
        logger.info("No positive multi-cropping yields; skipping")
        return

    merged = cycle_df.merge(area_df, on=key_cols, how="inner")
    if merged.empty:
        logger.info(
            "No overlapping multi-cropping combinations between area and yield tables"
        )
        return

    merged = merged.sort_values([*key_cols, "cycle_index", "crop"])
    merged["crop"] = merged["crop"].astype(str).str.strip()
    merged["country"] = merged["country"].astype(str).str.strip()
    merged["crop_bus"] = "crop:" + merged["crop"] + ":" + merged["country"]
    merged["yield_efficiency"] = merged["yield_t_per_ha"]
    merged["output_idx"] = merged.groupby(key_cols).cumcount()

    base = (
        merged.loc[
            :,
            [
                *key_cols,
                "eligible_area_ha",
                "water_requirement_m3_per_ha",
                "country",
            ],
        ]
        .drop_duplicates()
        .set_index(key_cols)
    )

    crop_counts = merged.groupby(key_cols)["crop"].size().rename("crop_count")
    base = base.join(crop_counts)
    base = base[base["crop_count"] > 0]
    if base.empty:
        logger.info(
            "Multi-cropping combinations have no positive-yield crops; skipping"
        )
        return

    # Look up per-(crop, country) cost and sum across crops in combination
    merged["cost_usd_per_ha"] = [
        crop_costs.get((c, cc), global_median_cost.get(c, 0.0))
        for c, cc in zip(merged["crop"], merged["country"])
    ]
    cost_totals = merged.groupby(key_cols)["cost_usd_per_ha"].sum().rename("total_cost")
    base = base.join(cost_totals)

    fert_series = pd.Series({str(k): float(v) for k, v in fertilizer_n_rates.items()})
    merged["fertilizer_rate"] = merged["crop"].map(fert_series).fillna(0.0)
    fertilizer_totals = (
        merged.groupby(key_cols)["fertilizer_rate"].sum().rename("fertilizer_total")
    )
    base = base.join(fertilizer_totals)

    base[["total_cost", "fertilizer_total"]] = base[
        ["total_cost", "fertilizer_total"]
    ].fillna(0.0)

    # Multiple-cropping marginal costs: sum of per-country crop costs in bnUSD/Mha
    base["marginal_cost"] = base["total_cost"] * 1e6 * constants.USD_TO_BNUSD
    base["p_nom_extendable"] = True
    base["p_nom_max"] = base["eligible_area_ha"] / 1e6

    residue_records: list[dict[str, object]] = []
    for (crop, water, region, res_class), feed_dict in residue_lookup.items():
        if not isinstance(feed_dict, Mapping):
            continue
        for feed_item, value in feed_dict.items():
            residue_records.append(
                {
                    "crop": str(crop),
                    "water_supply": str(water),
                    "region": str(region),
                    "resource_class": int(res_class),
                    "feed_item": str(feed_item),
                    "residue_yield": float(value),
                }
            )

    if residue_records:
        residue_df = pd.DataFrame(residue_records)
        residue_join = merged.merge(
            residue_df,
            on=["crop", "region", "resource_class", "water_supply"],
            how="left",
        )
        residue_join = residue_join.dropna(subset=["feed_item", "residue_yield"])
        residue_join = residue_join[residue_join["residue_yield"] > 0]
        if residue_join.empty:
            residue_agg = pd.DataFrame(
                columns=[*key_cols, "feed_item", "country", "residue_total"],
            )
        else:
            residue_agg = (
                residue_join.groupby([*key_cols, "feed_item", "country"])[
                    "residue_yield"
                ]
                .sum()
                .rename("residue_total")
                .reset_index()
            )
    else:
        residue_agg = pd.DataFrame(
            columns=[*key_cols, "feed_item", "country", "residue_total"],
        )

    residue_counts = (
        residue_agg.groupby(key_cols).size().rename("residue_count")
        if not residue_agg.empty
        else pd.Series(dtype=int)
    )
    base["residue_count"] = 0
    if not residue_counts.empty:
        base.loc[residue_counts.index, "residue_count"] = residue_counts

    index_df = base.reset_index()
    index_df["resource_class"] = index_df["resource_class"].astype(int)
    index_df["carrier"] = "crop_production_multi"
    index_df["bus0"] = (
        "land:cropland:"
        + index_df["region"].astype(str)
        + "_c"
        + index_df["resource_class"].astype(str)
        + "_"
        + index_df["water_supply"].astype(str)
    )
    index_df["link_name"] = (
        "produce:multi_"
        + index_df["combination"].astype(str)
        + "_"
        + index_df["water_supply"].astype(str)
        + ":"
        + index_df["region"].astype(str)
        + "_c"
        + index_df["resource_class"].astype(str)
    )

    missing_land = index_df[~index_df["bus0"].isin(n.buses.static.index)]
    if not missing_land.empty:
        missing_count = missing_land.shape[0]
        missing_preview = ", ".join(missing_land["bus0"].unique()[:5])
        logger.debug(
            "Skipping %d multi-cropping links due to missing land buses (examples: %s)",
            missing_count,
            missing_preview,
        )
        index_df = index_df[index_df["bus0"].isin(n.buses.static.index)]

    if index_df.empty:
        return

    if "crop_production_multi" not in n.carriers.static.index:
        n.carriers.add("crop_production_multi", unit="Mha")

    water_req = index_df["water_requirement_m3_per_ha"].astype(float)
    water_valid = (
        index_df["water_supply"].eq("i") & np.isfinite(water_req) & (water_req > 0)
    )
    water_invalid = index_df["water_supply"].eq("i") & ~np.isfinite(water_req)
    if water_invalid.any():
        logger.warning(
            "Ignoring invalid irrigation requirements for %d multi-cropping links",
            int(water_invalid.sum()),
        )

    index_df["water_efficiency"] = np.where(water_valid, -water_req * 1e-3, 0.0)
    index_df["has_water"] = water_valid.astype(int)

    fert_total = index_df["fertilizer_total"].astype(float)
    fert_valid = fert_total > 0
    index_df["fert_efficiency"] = np.where(
        fert_valid, -fert_total * 1e6 * constants.KG_TO_MEGATONNE, 0.0
    )
    index_df["has_fertilizer"] = fert_valid.astype(int)

    outputs = merged.merge(index_df[[*key_cols, "link_name"]], on=key_cols, how="left")
    outputs["offset"] = outputs["output_idx"] + 1
    offset_str = outputs["offset"].astype(int).astype(str)
    outputs["bus_col"] = "bus" + offset_str
    outputs["eff_col"] = np.where(
        outputs["offset"].eq(1),
        "efficiency",
        "efficiency" + offset_str,
    )
    outputs_entries = outputs[
        [
            "link_name",
            "bus_col",
            "crop_bus",
            "eff_col",
            "yield_efficiency",
        ]
    ].rename(columns={"crop_bus": "bus_value", "yield_efficiency": "eff_value"})

    entry_frames = [outputs_entries]

    water_columns = [*key_cols, "link_name", "water_efficiency", "crop_count"]
    water_entries = index_df.loc[index_df["has_water"] == 1, water_columns].copy()
    if not water_entries.empty:
        water_entries["offset"] = water_entries["crop_count"] + 1
        offset_str = water_entries["offset"].astype(int).astype(str)
        water_entries["bus_col"] = "bus" + offset_str
        water_entries["eff_col"] = "efficiency" + offset_str
        water_entries.loc[water_entries["offset"].eq(1), "eff_col"] = "efficiency"
        water_entries["bus_value"] = "water:" + water_entries["region"].astype(str)
        water_entries = water_entries[
            [
                "link_name",
                "bus_col",
                "bus_value",
                "eff_col",
                "water_efficiency",
            ]
        ].rename(columns={"water_efficiency": "eff_value"})
        entry_frames.append(water_entries)

    fert_entries = index_df[index_df["has_fertilizer"] == 1][
        [
            *key_cols,
            "link_name",
            "country",
            "fert_efficiency",
            "crop_count",
            "has_water",
        ]
    ].copy()
    if not fert_entries.empty:
        fert_entries["offset"] = (
            fert_entries["crop_count"] + fert_entries["has_water"] + 1
        )
        offset_str = fert_entries["offset"].astype(int).astype(str)
        fert_entries["bus_col"] = "bus" + offset_str
        fert_entries["eff_col"] = "efficiency" + offset_str
        fert_entries.loc[fert_entries["offset"].eq(1), "eff_col"] = "efficiency"
        fert_entries["bus_value"] = "fertilizer:" + fert_entries["country"].astype(str)
        fert_entries = fert_entries[
            [
                "link_name",
                "bus_col",
                "bus_value",
                "eff_col",
                "fert_efficiency",
            ]
        ].rename(columns={"fert_efficiency": "eff_value"})
        entry_frames.append(fert_entries)

    if not residue_agg.empty:
        residue_entries = residue_agg.merge(
            index_df[
                [
                    *key_cols,
                    "link_name",
                    "crop_count",
                    "has_water",
                    "has_fertilizer",
                ]
            ],
            on=key_cols,
            how="left",
        )
        residue_entries = residue_entries.dropna(subset=["link_name"])
        if residue_entries.empty:
            residue_entries = pd.DataFrame(columns=residue_entries.columns)
        residue_entries[["crop_count", "has_water", "has_fertilizer"]] = (
            residue_entries[["crop_count", "has_water", "has_fertilizer"]].fillna(0)
        )
        residue_entries = residue_entries.sort_values([*key_cols, "feed_item"])
        residue_entries["entry_order"] = residue_entries.groupby(key_cols).cumcount()
        residue_entries["offset"] = (
            residue_entries["crop_count"]
            + residue_entries["has_water"]
            + residue_entries["has_fertilizer"]
            + residue_entries["entry_order"]
            + 1
        )
        offset_str = residue_entries["offset"].astype(int).astype(str)
        residue_entries["bus_col"] = "bus" + offset_str
        residue_entries["eff_col"] = "efficiency" + offset_str
        residue_entries.loc[residue_entries["offset"].eq(1), "eff_col"] = "efficiency"
        residue_entries["bus_value"] = (
            "residue:"
            + residue_entries["feed_item"].astype(str)
            + ":"
            + residue_entries["country"].astype(str)
        )
        residue_entries["eff_value"] = residue_entries["residue_total"]
        entry_frames.append(
            residue_entries[
                [
                    "link_name",
                    "bus_col",
                    "bus_value",
                    "eff_col",
                    "eff_value",
                ]
            ]
        )

    entries = pd.concat(entry_frames, ignore_index=True)
    bus_wide = entries.pivot_table(
        index="link_name", columns="bus_col", values="bus_value", aggfunc="first"
    )
    eff_wide = entries.pivot_table(
        index="link_name", columns="eff_col", values="eff_value", aggfunc="first"
    )

    link_df = index_df.set_index("link_name")
    component_cols = [
        "carrier",
        "bus0",
        "p_nom_extendable",
        "p_nom_max",
        "marginal_cost",
    ]
    # Metadata columns for filtering
    metadata_cols = [
        "country",
        "region",
        "resource_class",
        "water_supply",
        "combination",
    ]
    # Prepare metadata values
    link_df["water_supply"] = link_df["water_supply"].map(
        {"r": "rainfed", "i": "irrigated"}
    )
    link_df["crop"] = link_df["combination"]  # combination = "maize+soybean" etc.
    link_df = link_df[component_cols + metadata_cols + ["crop"]]
    link_df = link_df.join(bus_wide, how="left").join(eff_wide, how="left")

    bus_cols = sorted(
        [c for c in link_df.columns if c.startswith("bus") and c != "bus0"],
        key=lambda name: int(name[3:]),
    )
    eff_cols = [
        "efficiency",
        *sorted(
            [
                c
                for c in link_df.columns
                if c.startswith("efficiency") and c != "efficiency"
            ],
            key=lambda name: int(name[len("efficiency") :]),
        ),
    ]

    missing_outputs = link_df["bus1"].isna() | link_df["efficiency"].isna()
    if missing_outputs.any():
        logger.warning(
            "Dropping %d multi-cropping links without valid crop outputs",
            int(missing_outputs.sum()),
        )
        link_df = link_df[~missing_outputs]

    if link_df.empty:
        return

    for col in bus_cols:
        link_df[col] = link_df[col].where(link_df[col].notna(), None)
    for col in eff_cols:
        link_df[col] = link_df[col].fillna(0.0)

    all_cols = component_cols + metadata_cols + ["crop"] + bus_cols + eff_cols
    kwargs = {col: link_df[col] for col in all_cols}
    n.links.add(link_df.index, **kwargs)


def add_spared_land_links(
    n: pypsa.Network,
    baseline_land_df: pd.DataFrame,
    lef_df: pd.DataFrame,
    *,
    disable_spared_cropland: bool = False,
) -> None:
    """Add optional links to allocate spared land and credit CO2 sinks.

    Only baseline cropland (i.e., currently managed area) can be spared. Newly
    converted land must first revert to baseline before becoming eligible.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    baseline_land_df : pd.DataFrame
        Current cropland area by region/water_supply/resource_class.
    lef_df : pd.DataFrame
        LEF lookup from ``_build_luc_lef_lookup`` (columns: region,
        resource_class, water_supply, use, lef).
    disable_spared_cropland : bool, optional
        If True, skip creation of spared-cropland links.
    """

    if disable_spared_cropland:
        logger.info("Spared cropland disabled; skipping spared land links")
        return

    if lef_df.empty:
        logger.info("No LUC LEF entries available for spared land; skipping")
        return

    base_df = baseline_land_df.reset_index()
    base_df["resource_class"] = base_df["resource_class"].astype(int)
    base_df["water_supply"] = base_df["water_supply"].astype(str)
    df = base_df[base_df["area_ha"] > 0].copy()
    if df.empty:
        logger.info("No baseline cropland available for sparing; skipping spared links")
        return

    df["lef"] = merge_lef(df, lef_df, "spared_cropland", allow_missing=True)

    # Add spared-land routes for all existing cropland buses, even where the
    # spared-land LEF is zero. This keeps land accounting explicit: baseline
    # land must flow either to production or to an explicit spared-land sink,
    # rather than disappearing as unused generator capacity upstream.

    suffix = (
        df["region"]
        + "_c"
        + df["resource_class"].astype(str)
        + "_"
        + df["water_supply"]
    )
    df["bus0"] = "land:existing_cropland:" + suffix
    df["sink_bus"] = "land:spared:" + suffix
    df["link_name"] = "spare:land:" + suffix
    df["area_mha"] = df["area_ha"] / 1e6

    # Filter out links where bus0 doesn't exist (due to area filtering)
    missing_bus_mask = ~df["bus0"].isin(n.buses.static.index)
    if missing_bus_mask.any():
        logger.debug(
            "Skipping %d spared land links due to missing land_existing_cropland buses",
            int(missing_bus_mask.sum()),
        )
        df = df[~missing_bus_mask]

    if df.empty:
        logger.info("No spared land links after filtering for existing buses")
        return

    # Add carriers and sink buses
    n.carriers.add("spared_land", unit="Mha")
    n.carriers.add("spare_land", unit="Mha")  # Link carrier

    # Index by sink_bus for proper alignment with PyPSA component names
    sink_df = df.set_index("sink_bus")
    n.buses.add(sink_df.index, carrier="spared_land", region=sink_df["region"])

    # Add stores for sink buses - index by store name for alignment
    df["store_name"] = (
        "store:spared:"
        + df["region"]
        + "_c"
        + df["resource_class"].astype(str)
        + "_"
        + df["water_supply"]
    )
    store_df = df.set_index("store_name")
    n.stores.add(
        store_df.index,
        bus=store_df["sink_bus"],
        carrier="spared_land",
        e_nom_extendable=True,
        region=store_df["region"],
        resource_class=store_df["resource_class"],
        water_supply=store_df["water_supply"],
    )

    # Add spared land links - index by link_name for alignment
    link_df = df.set_index("link_name")
    n.links.add(
        link_df.index,
        carrier="spare_land",
        bus0=link_df["bus0"],
        bus1=link_df["sink_bus"],
        efficiency=1.0,
        bus2="emission:co2",
        # tCO2/ha = MtCO2/Mha numerically, no conversion needed
        efficiency2=link_df["lef"],
        p_nom_extendable=True,
        p_nom_max=link_df["area_mha"],
        region=link_df["region"],
        resource_class=link_df["resource_class"],
        water_supply=link_df["water_supply"],
    )


def add_residue_soil_incorporation_links(
    n: pypsa.Network,
    residue_feed_items: list[str],
    ruminant_feed_mapping: pd.DataFrame,
    ruminant_feed_categories: pd.DataFrame,
    monogastric_feed_mapping: pd.DataFrame,
    monogastric_feed_categories: pd.DataFrame,
    countries: list[str],
    incorporation_n2o_factor: float,
    indirect_ef5: float,
    frac_leach: float,
) -> None:
    """Add links for crop residue incorporation into soil with N₂O emissions.

    Includes direct and indirect (leaching) N₂O emissions from crop residues
    following IPCC 2019 Refinement methodology (Chapter 11, Equations 11.1, 11.10).
    Note: Volatilization pathway (EF4) is not applicable for incorporated residues.

    Residues left on the field decompose and release N₂O. This function creates
    links that consume residues and produce N₂O emissions based on their N content
    and the IPCC emission factors.

    This processes ALL residues in the model, regardless of whether they're used
    for ruminant or monogastric feed. N content is looked up from whichever feed
    category dataset contains the residue.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    residue_feed_items : list[str]
        Complete list of all residue items in the model.
    ruminant_feed_mapping : pd.DataFrame
        Ruminant feed mapping (columns: feed_item, category).
    ruminant_feed_categories : pd.DataFrame
        Ruminant feed category properties (column: N_g_per_kg_DM).
    monogastric_feed_mapping : pd.DataFrame
        Monogastric feed mapping (columns: feed_item, category).
    monogastric_feed_categories : pd.DataFrame
        Monogastric feed category properties (column: N_g_per_kg_DM).
    countries : list[str]
        List of country ISO codes.
    incorporation_n2o_factor : float
        IPCC EF1 emission factor for direct emissions (kg N₂O-N per kg N input).
    indirect_ef5 : float
        IPCC EF5 emission factor for leaching/runoff (kg N₂O-N per kg N leached).
    frac_leach : float
        Fraction of applied N lost through leaching/runoff (FracLEACH-(H)).
    """

    if not residue_feed_items:
        logger.info("No residue items found; skipping soil incorporation links")
        return

    # Build lookup for N content from both ruminant and monogastric feed data

    # First, try ruminant feed categories
    residue_mapping = ruminant_feed_mapping[
        ruminant_feed_mapping["source_type"] == "residue"
    ]
    merged = residue_mapping.merge(
        ruminant_feed_categories[["category", "N_g_per_kg_DM"]],
        on="category",
        how="left",
    )
    valid = merged.dropna(subset=["N_g_per_kg_DM"])
    n_content_lookup = dict(
        zip(valid["feed_item"], valid["N_g_per_kg_DM"].astype(float))
    )

    # Then try monogastric feed categories (only adding items not already present)
    mono_residue_mapping = monogastric_feed_mapping[
        monogastric_feed_mapping["source_type"] == "residue"
    ]
    mono_merged = mono_residue_mapping.merge(
        monogastric_feed_categories[["category", "N_g_per_kg_DM"]],
        on="category",
        how="left",
    )
    mono_valid = mono_merged.dropna(subset=["N_g_per_kg_DM"])
    for item, n_val in zip(
        mono_valid["feed_item"], mono_valid["N_g_per_kg_DM"].astype(float)
    ):
        if item not in n_content_lookup:
            n_content_lookup[item] = n_val

    if not n_content_lookup:
        logger.info(
            "No residue items with N content data; skipping soil incorporation links"
        )
        return

    # Fallback N content for residues without data (g N/kg DM)
    # Conservative estimate based on typical crop straw/stover N content
    fallback_n_content = 8.0

    # Build per-item N₂O efficiency via vectorized computation
    items_df = pd.DataFrame({"item": residue_feed_items})
    items_df["n_content_g_per_kg"] = items_df["item"].map(n_content_lookup)

    # Log fallback usage for items without N content data
    missing_mask = items_df["n_content_g_per_kg"].isna()
    for item in items_df.loc[missing_mask, "item"]:
        logger.info(
            "No N content data for residue %s; using fallback value %.1f g N/kg DM",
            item,
            fallback_n_content,
        )
    items_df["n_content_g_per_kg"] = items_df["n_content_g_per_kg"].fillna(
        fallback_n_content
    )

    # Calculate N₂O emission efficiency (direct + indirect leaching)
    # N content (kg N / kg DM)
    n_content_kg_per_kg = items_df["n_content_g_per_kg"] / 1000.0

    # Direct N₂O (Equation 11.1): kg N₂O-N per kg N
    direct_n2o_n = incorporation_n2o_factor

    # Indirect N₂O from leaching (Equation 11.10): kg N₂O-N per kg N
    indirect_leach_n2o_n = frac_leach * indirect_ef5

    # Total N₂O-N per kg N, converted to N₂O
    total_n2o_n = direct_n2o_n + indirect_leach_n2o_n

    # Total efficiency: tonnes N₂O per Mt residue DM
    # = (kg N / kg DM) * (kg N₂O-N / kg N) * (44/28) * (tonnes per Mt)
    items_df["n2o_efficiency"] = (
        n_content_kg_per_kg * total_n2o_n * (44.0 / 28.0) * constants.MEGATONNE_TO_TONNE
    )

    # Build links for all residue x country combinations via cross product
    countries_df = pd.DataFrame({"country": countries})
    cross = items_df.merge(countries_df, how="cross")
    cross["bus_name"] = "residue:" + cross["item"] + ":" + cross["country"]

    # Only add link if the residue bus exists in the network
    cross = cross[cross["bus_name"].isin(n.buses.static.index)]

    if cross.empty:
        logger.info("No valid residue buses found; skipping soil incorporation links")
        return

    cross["link_name"] = "incorporate:residue_" + cross["item"] + ":" + cross["country"]
    cross = cross.set_index("link_name", drop=False)

    # Add the carrier
    carrier = "residue_incorporation"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")

    # Add the links
    n.links.add(
        cross.index,
        bus0=cross["bus_name"],
        bus1="emission:n2o",
        carrier=carrier,
        efficiency=cross["n2o_efficiency"],
        marginal_cost=0.0,  # No cost to incorporate residues
        p_nom_extendable=True,
        country=cross["country"],
    )

    logger.info(
        "Created %d residue soil incorporation links for %d residue types",
        len(cross),
        len(n_content_lookup),
    )
