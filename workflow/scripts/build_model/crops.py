# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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


def add_regional_crop_production_links(
    n: pypsa.Network,
    crop_list: list,
    yields_data: dict,
    region_to_country: pd.Series,
    allowed_countries: set,
    crop_costs_per_year: pd.Series,
    crop_costs_per_planting: pd.Series,
    fertilizer_n_rates: Mapping[str, float],
    rice_methane_factor: float,
    rainfed_wetland_rice_ch4_scaling_factor: float,
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
    harvested_area_data: Mapping[str, pd.DataFrame] | None = None,
    use_actual_production: bool = False,
    *,
    min_yield_t_per_ha: float,
) -> None:
    """Add crop production links per region/resource class and water supply.

    Rainfed yields must be present for every crop; irrigated yields are used when
    provided by the preprocessing pipeline. Output links produce into the same
    crop bus per country; link names encode supply type (i/r) and resource class.
    """
    residue_lookup = residue_lookup or {}
    harvested_area_data = harvested_area_data or {}

    # Add crop production carrier
    if "crop_production" not in n.carriers.static.index:
        n.carriers.add("crop_production", unit="Mt")

    for crop in crop_list:
        # Get fertilizer N application rate (kg N/ha/year) for this crop
        # If crop not in fertilizer data, default to 0 (no fertilizer requirement)
        fert_n_rate_kg_per_ha = float(fertilizer_n_rates.get(crop, 0.0))

        available_supplies = [
            ws for ws in ("r", "i") if f"{crop}_yield_{ws}" in yields_data
        ]

        # Process available water supplies (rainfed always first for stability)
        for ws in available_supplies:
            key = f"{crop}_yield_{ws}"
            crop_yields = yields_data[key].copy()

            if use_actual_production:
                harvest_table = harvested_area_data[f"{crop}_harvested_{ws}"]
                # Only join harvested_area if the table has data (not empty)
                if (
                    not harvest_table.empty
                    and "harvested_area" in harvest_table.columns
                ):
                    crop_yields = crop_yields.join(
                        harvest_table["harvested_area"].rename("harvested_area"),
                        how="left",
                    )

            # Add a unique name per link including water supply and class
            crop_yields["name"] = crop_yields.index.map(
                lambda x,
                crop=crop,
                ws=ws: f"produce:{crop}_{'irrigated' if ws == 'i' else 'rainfed'}:{x[0]}_c{x[1]}"
            )

            # Make index levels columns
            df = crop_yields.reset_index()

            # Set index to "name"
            df.set_index("name", inplace=True)
            df.index.name = None

            # Filter out rows with zero suitable area or zero yield
            df = df[(df["suitable_area"] > 0) & (df["yield"] > 0)]

            # Filter low yields for numerical stability
            if min_yield_t_per_ha > 0:
                low_yield_mask = df["yield"] < min_yield_t_per_ha
                df = df[~low_yield_mask]

            if use_actual_production:
                df["harvested_area"] = pd.to_numeric(
                    df.get("harvested_area"), errors="coerce"
                )
                df["fixed_area_ha"] = pd.to_numeric(
                    df.get("harvested_area"), errors="coerce"
                )
                df = df[df["fixed_area_ha"] > 0]

            # Map regions to countries and filter to allowed countries
            df["country"] = df["region"].map(region_to_country)
            df = df[df["country"].isin(allowed_countries)]

            if df.empty:
                continue

            bus0_series = df.apply(
                lambda r,
                ws=ws: f"land:cropland:{r['region']}_c{int(r['resource_class'])}_{'i' if ws == 'i' else 'r'}",
                axis=1,
            )
            missing_bus_mask = ~bus0_series.isin(n.buses.static.index)
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

            # Cost for this crop: per-year + per-planting costs (USD/ha); if missing, use 0
            cost_year = float(crop_costs_per_year.get(crop, float("nan")))
            cost_planting = float(crop_costs_per_planting.get(crop, float("nan")))

            if not np.isfinite(cost_year):
                cost_year = 0.0
            if not np.isfinite(cost_planting):
                cost_planting = 0.0

            if cost_year == 0.0 and cost_planting == 0.0:
                logger.info(
                    "No USDA cost for crop '%s'; defaulting marginal_cost to 0",
                    crop,
                )

            # For single crops, total cost = per-year + per-planting
            cost_per_ha = cost_year + cost_planting

            # Add links
            # Connect to class-level land bus per region/resource class and water supply
            # Land is now tracked in Mha, so scale yields and areas accordingly
            resource_classes = df["resource_class"].astype(int).to_numpy()
            regions = df["region"].astype(str).to_numpy()
            water_code = "i" if ws == "i" else "r"
            # Cost is per hectare; convert to per Mha in bnUSD/Mha
            base_cost = cost_per_ha * 1e6 * constants.USD_TO_BNUSD

            # Land bus flows are in Mha; yields (t/ha) numerically equal Mt/Mha so
            # the efficiency terms remain the raw yield values.
            link_params = {
                "name": df.index,
                "carrier": "crop_production",
                "bus0": bus0_series,
                "bus1": df["country"].apply(lambda c, crop=crop: f"crop:{crop}:{c}"),
                # Output: Mt crop per Mha land (already Mt-aware)
                "efficiency": df["yield"],
                "bus3": df["country"].apply(lambda c: f"fertilizer:{c}"),
                "efficiency3": -fert_n_rate_kg_per_ha
                * 1e6
                * constants.KG_TO_MEGATONNE,  # kg N/ha → Mt N/Mha
                # Link marginal_cost is per unit of bus0 flow (now Mha).
                "marginal_cost": base_cost,
                "p_nom_max": df["suitable_area"] / 1e6,  # ha → Mha
                "p_nom_extendable": not use_actual_production,
                # Metadata columns
                "crop": crop,
                "country": df["country"],
                "region": df["region"],
                "resource_class": df["resource_class"].astype(int),
                "water_supply": "irrigated" if ws == "i" else "rainfed",
            }

            if use_actual_production:
                fixed_area_mha = df["fixed_area_ha"] / 1e6
                link_params["p_nom"] = fixed_area_mha
                link_params["p_nom_max"] = fixed_area_mha
                link_params["p_nom_min"] = fixed_area_mha
                link_params["p_min_pu"] = 1.0

            if ws == "i":
                water_requirement = pd.to_numeric(
                    df["water_requirement_m3_per_ha"], errors="coerce"
                )

                link_params["bus2"] = df["region"].apply(lambda r: f"water:{r}")
                # Convert m³/ha to Mm³/Mha for compatibility with scaled water units
                link_params["efficiency2"] = -water_requirement

            next_bus_idx = 4
            if residue_lookup:
                residue_feed_items = sorted(
                    {
                        feed_item
                        for region, resource_class in zip(regions, resource_classes)
                        for feed_item in residue_lookup.get(
                            (crop, water_code, region, int(resource_class)), {}
                        )
                    }
                )
                if residue_feed_items:
                    countries_for_rows = df["country"].astype(str).tolist()
                    for feed_item in residue_feed_items:
                        efficiencies = np.zeros(len(df), dtype=float)
                        for idx_row, (region, resource_class) in enumerate(
                            zip(regions, resource_classes)
                        ):
                            residue_dict = residue_lookup.get(
                                (crop, water_code, region, int(resource_class))
                            )
                            if not residue_dict:
                                continue
                            residue_yield = residue_dict.get(feed_item)
                            if residue_yield is None:
                                continue
                            efficiencies[idx_row] = residue_yield
                        if np.allclose(efficiencies, 0.0):
                            continue
                        bus_key = f"bus{next_bus_idx}"
                        eff_key = f"efficiency{next_bus_idx}"
                        link_params[bus_key] = [
                            f"residue:{feed_item}:{country}"
                            for country in countries_for_rows
                        ]
                        link_params[eff_key] = efficiencies
                        next_bus_idx += 1

            emission_outputs: dict[str, np.ndarray] = {}

            if crop == "wetland-rice" and rice_methane_factor > 0:
                # Methane emissions from rice cultivation
                # Factor is kg CH4/ha from IPCC.
                # Link input (bus0) is in Mha; CH4 bus is in tonnes (tCH4).
                # Conversion: kg CH4/ha → t CH4/Mha
                #   = kg/ha * 1e-3 (kg to t) * 1e6 (ha to Mha) = kg/ha * 1e3

                # IPCC 2019 Refinement, Vol 4, Chapter 5, Table 5.12
                # Scaling factor for water regime during cultivation (SFw)
                # Continuously flooded (irrigated baseline): 1.0
                # Regular rainfed: uses config parameter
                scaling_factor = (
                    1.0 if ws == "i" else rainfed_wetland_rice_ch4_scaling_factor
                )

                ch4_emissions = np.full(
                    len(df),
                    rice_methane_factor * scaling_factor * 1e3,  # kg CH4/ha → t CH4/Mha
                    dtype=float,
                )
                emission_outputs["ch4"] = ch4_emissions

            for emission_type in sorted(emission_outputs.keys()):
                values = emission_outputs[emission_type]
                key_bus = f"bus{next_bus_idx}"
                key_eff = f"efficiency{next_bus_idx}"
                link_params[key_bus] = [f"emission:{emission_type}"] * len(values)
                link_params[key_eff] = values
                next_bus_idx += 1

            n.links.add(**link_params)


def add_multi_cropping_links(
    n: pypsa.Network,
    eligible_area: pd.DataFrame,
    cycle_yields: pd.DataFrame,
    region_to_country: pd.Series,
    allowed_countries: set[str],
    crop_costs_per_year: Mapping[str, float],
    crop_costs_per_planting: Mapping[str, float],
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

    cost_year_series = pd.Series(
        {str(k): float(v) for k, v in crop_costs_per_year.items()}
    )
    cost_planting_series = pd.Series(
        {str(k): float(v) for k, v in crop_costs_per_planting.items()}
    )
    merged["cost_per_year"] = merged["crop"].map(cost_year_series).fillna(0.0)
    merged["cost_per_planting"] = merged["crop"].map(cost_planting_series).fillna(0.0)

    costs = merged.groupby(key_cols).agg(
        total_cost_per_year=("cost_per_year", "sum"),
        total_cost_per_planting=("cost_per_planting", "sum"),
    )
    base = base.join(costs)

    fert_series = pd.Series({str(k): float(v) for k, v in fertilizer_n_rates.items()})
    merged["fertilizer_rate"] = merged["crop"].map(fert_series).fillna(0.0)
    fertilizer_totals = (
        merged.groupby(key_cols)["fertilizer_rate"].sum().rename("fertilizer_total")
    )
    base = base.join(fertilizer_totals)

    base[["total_cost_per_year", "total_cost_per_planting", "fertilizer_total"]] = base[
        ["total_cost_per_year", "total_cost_per_planting", "fertilizer_total"]
    ].fillna(0.0)

    base["avg_cost_per_year"] = base["total_cost_per_year"] / base["crop_count"]
    # Multiple-cropping marginal costs remain in bnUSD per Mha of area used.
    base["marginal_cost"] = (
        (base["avg_cost_per_year"] + base["total_cost_per_planting"])
        * 1e6
        * constants.USD_TO_BNUSD
    )
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

    link_names = link_df.index.tolist()
    all_cols = component_cols + metadata_cols + ["crop"] + bus_cols + eff_cols
    kwargs = {col: link_df[col].tolist() for col in all_cols}
    n.links.add(link_names, **kwargs)


def add_spared_land_links(
    n: pypsa.Network,
    baseline_land_df: pd.DataFrame,
    lef_df: pd.DataFrame,
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
    """

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

    df["lef"] = merge_lef(df, lef_df, "spared", allow_missing=True)

    df = df[(df["lef"] != 0.0) & (df["area_ha"] > 0)].copy()

    if df.empty:
        logger.info("No eligible spared land entries; skipping spared links")
        return

    def _bus0(row: pd.Series) -> str:
        return (
            f"land:existing_cropland:{row['region']}_c{int(row['resource_class'])}_"
            f"{row['water_supply']}"
        )

    def _sink_bus(row: pd.Series) -> str:
        return (
            f"land:spared:{row['region']}_c{int(row['resource_class'])}_"
            f"{row['water_supply']}"
        )

    def _link_name(row: pd.Series) -> str:
        return (
            f"spare:land:{row['region']}_c{int(row['resource_class'])}_"
            f"{row['water_supply']}"
        )

    df["bus0"] = df.apply(_bus0, axis=1)
    df["sink_bus"] = df.apply(_sink_bus, axis=1)
    df["link_name"] = df.apply(_link_name, axis=1)
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
    n_content_lookup = {}

    # First, try ruminant feed categories
    for _, row in ruminant_feed_mapping[
        ruminant_feed_mapping["source_type"] == "residue"
    ].iterrows():
        item = row["feed_item"]
        category = row["category"]
        # Look up N content for this category
        cat_data = ruminant_feed_categories[
            ruminant_feed_categories["category"] == category
        ]
        if not cat_data.empty and "N_g_per_kg_DM" in cat_data.columns:
            n_content = cat_data.iloc[0]["N_g_per_kg_DM"]
            if pd.notna(n_content):
                n_content_lookup[item] = float(n_content)

    # Then try monogastric feed categories (may override or add new entries)
    for _, row in monogastric_feed_mapping[
        monogastric_feed_mapping["source_type"] == "residue"
    ].iterrows():
        item = row["feed_item"]
        if item in n_content_lookup:
            continue  # Already have N content from ruminant data
        category = row["category"]
        cat_data = monogastric_feed_categories[
            monogastric_feed_categories["category"] == category
        ]
        if not cat_data.empty and "N_g_per_kg_DM" in cat_data.columns:
            n_content = cat_data.iloc[0]["N_g_per_kg_DM"]
            if pd.notna(n_content):
                n_content_lookup[item] = float(n_content)

    if not n_content_lookup:
        logger.info(
            "No residue items with N content data; skipping soil incorporation links"
        )
        return

    # Build links for all residue x country combinations
    all_names = []
    all_bus0 = []
    all_efficiency = []

    # Fallback N content for residues without data (g N/kg DM)
    # Conservative estimate based on typical crop straw/stover N content
    fallback_n_content = 8.0

    for item in residue_feed_items:
        # Use fallback if we don't have N content data for this residue
        if item not in n_content_lookup:
            logger.info(
                "No N content data for residue %s; using fallback value %.1f g N/kg DM",
                item,
                fallback_n_content,
            )
            n_content_g_per_kg = fallback_n_content
        else:
            n_content_g_per_kg = n_content_lookup[item]

        # Calculate N₂O emission efficiency (direct + indirect leaching)
        # N content (kg N / kg DM)
        n_content_kg_per_kg = n_content_g_per_kg / 1000.0

        # Direct N₂O (Equation 11.1): kg N₂O-N per kg N
        direct_n2o_n = incorporation_n2o_factor

        # Indirect N₂O from leaching (Equation 11.10): kg N₂O-N per kg N
        indirect_leach_n2o_n = frac_leach * indirect_ef5

        # Total N₂O-N per kg N, converted to N₂O
        total_n2o_n = direct_n2o_n + indirect_leach_n2o_n

        # Total efficiency: tonnes N₂O per Mt residue DM
        # = (kg N / kg DM) * (kg N₂O-N / kg N) * (44/28) * (tonnes per Mt)
        n2o_efficiency = (
            n_content_kg_per_kg
            * total_n2o_n
            * (44.0 / 28.0)
            * constants.MEGATONNE_TO_TONNE
        )

        for country in countries:
            bus_name = f"residue:{item}:{country}"

            # Only add link if the residue bus exists in the network
            if bus_name not in n.buses.static.index:
                continue

            all_names.append(f"incorporate:residue_{item}:{country}")
            all_bus0.append(bus_name)
            all_efficiency.append(n2o_efficiency)

    if not all_names:
        logger.info("No valid residue buses found; skipping soil incorporation links")
        return

    # Add the carrier
    carrier = "residue_incorporation"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")

    # Add the links
    n.links.add(
        all_names,
        bus0=all_bus0,
        bus1="emission:n2o",
        carrier=carrier,
        efficiency=all_efficiency,
        marginal_cost=0.0,  # No cost to incorporate residues
        p_nom_extendable=True,
    )

    logger.info(
        "Created %d residue soil incorporation links for %d residue types",
        len(all_names),
        len(n_content_lookup),
    )
