"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import numpy as np
import pandas as pd
import pypsa

from . import primary_resources
from .utils import merge_lef


def add_land_components(
    n: pypsa.Network,
    total_land_area: pd.DataFrame,
    baseline_land_area: pd.DataFrame,
    lef_df: pd.DataFrame,
    *,
    reg_limit: float,
    land_slack_cost: float,
    enable_land_slack: bool,
    min_area_ha: float,
    disable_new_cropland: bool = False,
    disable_new_pasture: bool = False,
) -> None:
    """Add dual-pool land system with separate cropland and pasture pools.

    Creates a land system with:
    - Cropland pools (per region/class/water) for crop production
    - Pasture pools (per region/class, water-agnostic) for grassland production
    - Supply from existing cropland baseline and new land expansion
    - Links routing land to both pools with appropriate LUC emissions

    Parameters
    ----------
    n : pypsa.Network
        Target network.
    total_land_area : pd.DataFrame
        Total suitable land indexed by (region, water_supply, resource_class).
    baseline_land_area : pd.DataFrame
        Currently managed cropland indexed the same way.
    lef_df : pd.DataFrame
        LEF lookup from ``_build_luc_lef_lookup`` (columns: region,
        resource_class, water_supply, use, lef).
    reg_limit : float
        Maximum fraction of total potential land that can be utilized.
    land_slack_cost : float
        Marginal cost (bnUSD/Mha) for slack generators.
    enable_land_slack : bool
        Whether to add slack generators for land constraints.
    min_area_ha : float
        Minimum area threshold (ha). Entries below this are filtered out.
    disable_new_cropland : bool
        If True, no new land can supply the cropland pool.
    disable_new_pasture : bool
        If True, no new land can supply the pasture pool.
    """

    if total_land_area.empty:
        return

    # Ensure carriers exist before adding components
    for carrier_name in (
        "land_cropland",
        "land_pasture",
        "land_existing_cropland",
        "land_new",
        "land_use",
        "land_conversion",
        "existing_to_pasture",
        "new_to_pasture",
    ):
        if carrier_name not in n.carriers.static.index:
            n.carriers.add(carrier_name, unit="Mha")

    baseline_series = (
        baseline_land_area.reindex(total_land_area.index, fill_value=0.0)["area_ha"]
        .astype(float)
        .rename("area_ha")
    )
    total_area = total_land_area["area_ha"].astype(float)
    total_area = np.maximum(total_area, baseline_series)
    total_land_area["area_ha"] = total_area
    expansion_series = (total_area - baseline_series).clip(lower=0.0)

    land_index_df = total_land_area.reset_index()
    land_index_df["resource_class"] = land_index_df["resource_class"].astype(int)
    land_index_df["baseline_area_ha"] = baseline_series.to_numpy()
    land_index_df["expansion_area_ha"] = expansion_series.to_numpy()

    # Apply reg_limit to total potential area, then split between existing and new
    total_available = land_index_df["area_ha"] * reg_limit
    land_index_df["existing_available_ha"] = np.minimum(
        land_index_df["baseline_area_ha"], total_available
    )
    land_index_df["new_available_ha"] = np.maximum(
        0.0, total_available - land_index_df["baseline_area_ha"]
    )

    # Build bus names using ':' delimiter
    # Cropland pools: per region/class/water
    land_index_df["cropland_bus"] = (
        "land:cropland:"
        + land_index_df["region"].astype(str)
        + "_c"
        + land_index_df["resource_class"].astype(str)
        + "_"
        + land_index_df["water_supply"].astype(str)
    )
    land_index_df["existing_bus"] = (
        "land:existing_cropland:"
        + land_index_df["region"].astype(str)
        + "_c"
        + land_index_df["resource_class"].astype(str)
        + "_"
        + land_index_df["water_supply"].astype(str)
    )
    land_index_df["new_bus"] = (
        "land:new:"
        + land_index_df["region"].astype(str)
        + "_c"
        + land_index_df["resource_class"].astype(str)
        + "_"
        + land_index_df["water_supply"].astype(str)
    )
    # Pasture pools: per region/class only (water-agnostic)
    land_index_df["pasture_bus"] = (
        "land:pasture:"
        + land_index_df["region"].astype(str)
        + "_c"
        + land_index_df["resource_class"].astype(str)
    )

    active_mask = (
        (land_index_df["area_ha"] > 0)
        | (land_index_df["baseline_area_ha"] > 0)
        | (land_index_df["expansion_area_ha"] > 0)
    )
    land_index_df = land_index_df[active_mask].copy()
    if land_index_df.empty:
        return

    # Filter small areas for numerical stability
    if min_area_ha > 0:
        small_area_mask = land_index_df["area_ha"] < min_area_ha
        land_index_df = land_index_df[~small_area_mask].copy()
        if land_index_df.empty:
            return

    # Add cropland pool buses (per region/class/water)
    cropland_bus_names = land_index_df["cropland_bus"].tolist()
    cropland_regions = land_index_df["region"].tolist()
    n.buses.add(cropland_bus_names, carrier="land_cropland", region=cropland_regions)

    # Add pasture pool buses (per region/class, water-agnostic)
    # Only create unique pasture buses (deduplicate across water supplies)
    pasture_df = (
        land_index_df.groupby(["region", "resource_class", "pasture_bus"])
        .first()
        .reset_index()
    )
    pasture_bus_names = pasture_df["pasture_bus"].tolist()
    pasture_regions = pasture_df["region"].tolist()
    n.buses.add(pasture_bus_names, carrier="land_pasture", region=pasture_regions)

    # --- Existing cropland supply ---
    baseline_rows = land_index_df[land_index_df["existing_available_ha"] > 0].copy()
    if not baseline_rows.empty:
        # Add existing cropland buses
        n.buses.add(
            baseline_rows["existing_bus"].tolist(),
            carrier="land_existing_cropland",
            region=baseline_rows["region"].tolist(),
        )

        # Add generators for existing cropland
        existing_gen_names = [
            f"supply:land_existing_cropland:{row.region}_c{int(row.resource_class)}_{row.water_supply}"
            for row in baseline_rows.itertuples(index=False)
        ]
        existing_available_mha = baseline_rows["existing_available_ha"].to_numpy() / 1e6
        n.generators.add(
            existing_gen_names,
            bus=baseline_rows["existing_bus"].tolist(),
            carrier="land_existing_cropland",
            p_nom=existing_available_mha,
            p_nom_extendable=False,
            marginal_cost=0.0,
            region=baseline_rows["region"].tolist(),
            resource_class=baseline_rows["resource_class"].tolist(),
            water_supply=baseline_rows["water_supply"].tolist(),
        )

        # Links: existing → cropland pool
        existing_to_cropland_names = [
            f"use:existing_land:{row.region}_c{int(row.resource_class)}_{row.water_supply}"
            for row in baseline_rows.itertuples(index=False)
        ]
        n.links.add(
            existing_to_cropland_names,
            carrier="land_use",
            bus0=baseline_rows["existing_bus"].tolist(),
            bus1=baseline_rows["cropland_bus"].tolist(),
            efficiency=1.0,
            p_nom=existing_available_mha,
            p_nom_extendable=False,
            region=baseline_rows["region"].tolist(),
            resource_class=baseline_rows["resource_class"].tolist(),
            water_supply=baseline_rows["water_supply"].tolist(),
        )

        # Links: existing → pasture pool (rainfed only, no LUC emissions)
        rainfed_baseline = baseline_rows[baseline_rows["water_supply"] == "r"].copy()
        if not rainfed_baseline.empty:
            existing_to_pasture_names = [
                f"use:existing_to_pasture:{row.region}_c{int(row.resource_class)}"
                for row in rainfed_baseline.itertuples(index=False)
            ]
            rainfed_mha = rainfed_baseline["existing_available_ha"].to_numpy() / 1e6
            n.links.add(
                existing_to_pasture_names,
                carrier="existing_to_pasture",
                bus0=rainfed_baseline["existing_bus"].tolist(),
                bus1=rainfed_baseline["pasture_bus"].tolist(),
                efficiency=1.0,
                p_nom=rainfed_mha,
                p_nom_extendable=False,
                region=rainfed_baseline["region"].tolist(),
                resource_class=rainfed_baseline["resource_class"].tolist(),
                water_supply=rainfed_baseline["water_supply"].tolist(),
            )

    # --- New land supply ---
    expansion_rows = land_index_df[land_index_df["new_available_ha"] > 0].copy()
    if not expansion_rows.empty:
        # Add new land buses
        n.buses.add(
            expansion_rows["new_bus"].tolist(),
            carrier="land_new",
            region=expansion_rows["region"].tolist(),
        )

        # Add generators for new land
        new_gen_names = [
            f"supply:land_new:{row.region}_c{int(row.resource_class)}_{row.water_supply}"
            for row in expansion_rows.itertuples(index=False)
        ]
        new_available_mha = expansion_rows["new_available_ha"].to_numpy() / 1e6
        n.generators.add(
            new_gen_names,
            bus=expansion_rows["new_bus"].tolist(),
            carrier="land_new",
            p_nom_extendable=True,
            p_nom_max=new_available_mha,
            marginal_cost=0.0,
            region=expansion_rows["region"].tolist(),
            resource_class=expansion_rows["resource_class"].tolist(),
            water_supply=expansion_rows["water_supply"].tolist(),
        )

        # Links: new → cropland pool (with LUC emissions)
        if not disable_new_cropland:
            if not lef_df.empty:
                luc_cropland = merge_lef(
                    expansion_rows, lef_df, "cropland", allow_missing=False
                )
            else:
                luc_cropland = pd.Series(0.0, index=expansion_rows.index)
            new_to_cropland_names = [
                f"convert:new_land:{row.region}_c{int(row.resource_class)}_{row.water_supply}"
                for row in expansion_rows.itertuples(index=False)
            ]
            # tCO2/ha = MtCO2/Mha numerically, no conversion needed
            n.links.add(
                new_to_cropland_names,
                carrier="land_conversion",
                bus0=expansion_rows["new_bus"].tolist(),
                bus1=expansion_rows["cropland_bus"].tolist(),
                efficiency=1.0,
                bus2="emission:co2",
                efficiency2=luc_cropland.to_numpy(),
                p_nom_extendable=True,
                p_nom_max=new_available_mha,
                region=expansion_rows["region"].tolist(),
                resource_class=expansion_rows["resource_class"].tolist(),
                water_supply=expansion_rows["water_supply"].tolist(),
            )

        # Links: new → pasture pool (rainfed only, with LUC emissions)
        if not disable_new_pasture:
            rainfed_expansion = expansion_rows[
                expansion_rows["water_supply"] == "r"
            ].copy()
            if not rainfed_expansion.empty:
                if not lef_df.empty:
                    luc_pasture = merge_lef(
                        rainfed_expansion, lef_df, "pasture", allow_missing=False
                    )
                else:
                    luc_pasture = pd.Series(0.0, index=rainfed_expansion.index)
                new_to_pasture_names = [
                    f"convert:new_to_pasture:{row.region}_c{int(row.resource_class)}"
                    for row in rainfed_expansion.itertuples(index=False)
                ]
                rainfed_new_mha = rainfed_expansion["new_available_ha"].to_numpy() / 1e6
                n.links.add(
                    new_to_pasture_names,
                    carrier="new_to_pasture",
                    bus0=rainfed_expansion["new_bus"].tolist(),
                    bus1=rainfed_expansion["pasture_bus"].tolist(),
                    efficiency=1.0,
                    bus2="emission:co2",
                    efficiency2=luc_pasture.to_numpy(),
                    p_nom_extendable=True,
                    p_nom_max=rainfed_new_mha,
                    region=rainfed_expansion["region"].tolist(),
                    resource_class=rainfed_expansion["resource_class"].tolist(),
                    water_supply=rainfed_expansion["water_supply"].tolist(),
                )

    # Add slack generators to both pool types if enabled
    if enable_land_slack:
        primary_resources._add_land_slack_generators(
            n, cropland_bus_names, land_slack_cost
        )
        primary_resources._add_land_slack_generators(
            n, pasture_bus_names, land_slack_cost
        )
