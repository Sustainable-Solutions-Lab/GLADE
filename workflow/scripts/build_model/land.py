"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import numpy as np
import pandas as pd
import pypsa

from . import primary_resources
from .utils import merge_lef

HA_PER_MHA = 1e6


def _empty_region_class_series() -> pd.Series:
    idx = pd.MultiIndex.from_arrays([[], []], names=["region", "resource_class"])
    return pd.Series(index=idx, dtype=float, name="area_ha")


def _normalize_region_class_area(
    area: pd.Series | None,
    *,
    source_name: str,
) -> pd.Series:
    """Return non-negative area indexed by (region, resource_class)."""
    if area is None or area.empty:
        return _empty_region_class_series()

    if not isinstance(area.index, pd.MultiIndex) or area.index.nlevels != 2:
        raise ValueError(
            f"{source_name} must be indexed by (region, resource_class), got {type(area.index)}"
        )

    normalized = area.copy()
    normalized.index = normalized.index.set_names(["region", "resource_class"])
    df = normalized.rename("area_ha").reset_index()
    df["region"] = df["region"].astype(str)
    df["resource_class"] = df["resource_class"].astype(int)
    df["area_ha"] = pd.to_numeric(df["area_ha"], errors="coerce").fillna(0.0)

    out = df.groupby(["region", "resource_class"], sort=True)["area_ha"].sum()
    out = out.clip(lower=0.0)
    return out[out > 0.0]


def _build_existing_grassland_supply_df(
    *,
    existing_grassland_convertible_area: pd.Series,
    existing_grassland_marginal_area: pd.Series,
) -> pd.DataFrame:
    """Build tidy DataFrame with existing grassland supply by land type."""
    frames: list[pd.DataFrame] = []

    for land_type, series in (
        ("convertible", existing_grassland_convertible_area),
        ("marginal", existing_grassland_marginal_area),
    ):
        if series.empty:
            continue
        df = series.rename("area_ha").reset_index()
        df["resource_class"] = df["resource_class"].astype(int)
        df["land_type"] = land_type
        frames.append(df)

    if not frames:
        return pd.DataFrame(
            columns=["region", "resource_class", "land_type", "area_ha"]
        )

    supply_df = pd.concat(frames, ignore_index=True)
    supply_df = supply_df[supply_df["area_ha"] > 0.0].copy()
    if supply_df.empty:
        return pd.DataFrame(
            columns=["region", "resource_class", "land_type", "area_ha"]
        )
    return supply_df


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
    land_use_cost_bnusd_per_mha: float,
    disable_new_cropland: bool = False,
    disable_new_pasture: bool = False,
    disable_spared_grassland: bool = False,
    existing_grassland_convertible_area: pd.Series | None = None,
    existing_grassland_marginal_area: pd.Series | None = None,
) -> None:
    """Add dual-pool land system with separate cropland and pasture pools.

    Creates a land system with:
    - Cropland pools (per region/class/water) for crop production
    - Pasture pools (per region/class, water-agnostic) for grassland production
    - Supply from existing cropland baseline and new land expansion
    - Existing grassland split into cropland-suitable and marginal pools
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
    land_use_cost_bnusd_per_mha : float
        Marginal land-use cost applied to land supply dispatch (bnUSD/Mha).
    disable_new_cropland : bool
        If True, no new land can supply the cropland pool.
    disable_new_pasture : bool
        If True, no new land can supply the pasture pool.
    disable_spared_grassland : bool
        If True, existing grassland cannot be allocated to spared-land sinks.
    existing_grassland_convertible_area : pd.Series | None
        Current grassland area that is suitable for crop growth (GAEZ suitable),
        indexed by (region, resource_class) in hectares.
    existing_grassland_marginal_area : pd.Series | None
        Current grazing-only grassland area that is not suitable for crop growth,
        indexed by (region, resource_class) in hectares.
    """

    if total_land_area.empty:
        return

    convertible_grassland = _normalize_region_class_area(
        existing_grassland_convertible_area,
        source_name="existing_grassland_convertible_area",
    )
    marginal_grassland = _normalize_region_class_area(
        existing_grassland_marginal_area,
        source_name="existing_grassland_marginal_area",
    )

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
        "land_existing_grassland_convertible",
        "land_existing_grassland_marginal",
        "existing_grassland_to_pasture",
        "spare_existing_grassland",
        "spared_grassland",
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

    land_index_df = total_land_area.reset_index()
    land_index_df["resource_class"] = land_index_df["resource_class"].astype(int)
    land_index_df["baseline_area_ha"] = baseline_series.to_numpy()

    # Only cropland-suitable current grassland should reduce convertible land.
    land_index_df["convertible_grassland_ha"] = 0.0
    rainfed_rows = land_index_df["water_supply"] == "r"
    if rainfed_rows.any() and not convertible_grassland.empty:
        rainfed_idx = pd.MultiIndex.from_frame(
            land_index_df.loc[rainfed_rows, ["region", "resource_class"]],
            names=["region", "resource_class"],
        )
        aligned_convertible = convertible_grassland.reindex(
            rainfed_idx, fill_value=0.0
        ).to_numpy()
        land_index_df.loc[rainfed_rows, "convertible_grassland_ha"] = (
            aligned_convertible
        )

    adjusted_area = (
        land_index_df["area_ha"] - land_index_df["convertible_grassland_ha"]
    ).clip(lower=0.0)
    land_index_df["adjusted_area_ha"] = adjusted_area
    land_index_df["expansion_area_ha"] = (
        land_index_df["adjusted_area_ha"] - land_index_df["baseline_area_ha"]
    ).clip(lower=0.0)

    # Apply reg_limit to total potential area, then split between existing and new.
    total_available = land_index_df["adjusted_area_ha"] * reg_limit
    land_index_df["existing_available_ha"] = np.minimum(
        land_index_df["baseline_area_ha"], total_available
    )
    land_index_df["new_available_ha"] = np.maximum(
        0.0, total_available - land_index_df["baseline_area_ha"]
    )

    # Build bus names using ':' delimiter
    region_class = (
        land_index_df["region"].astype(str)
        + "_c"
        + land_index_df["resource_class"].astype(str)
    )
    region_class_water = region_class + "_" + land_index_df["water_supply"].astype(str)
    # Cropland pools: per region/class/water
    land_index_df["cropland_bus"] = "land:cropland:" + region_class_water
    land_index_df["existing_bus"] = "land:existing_cropland:" + region_class_water
    land_index_df["new_bus"] = "land:new:" + region_class_water
    # Pasture pools: per region/class only (water-agnostic)
    land_index_df["pasture_bus"] = "land:pasture:" + region_class

    active_mask = (
        (land_index_df["adjusted_area_ha"] > 0)
        | (land_index_df["baseline_area_ha"] > 0)
        | (land_index_df["expansion_area_ha"] > 0)
    )
    land_index_df = land_index_df[active_mask].copy()
    if land_index_df.empty:
        return

    # Filter small areas for numerical stability
    if min_area_ha > 0:
        small_area_mask = land_index_df["adjusted_area_ha"] < min_area_ha
        land_index_df = land_index_df[~small_area_mask].copy()
        if land_index_df.empty:
            return

    # Add cropland pool buses (per region/class/water)
    cropland_df = land_index_df[
        ["cropland_bus", "region", "resource_class", "water_supply"]
    ].drop_duplicates(subset=["cropland_bus"])
    cropland_df = cropland_df.set_index("cropland_bus")
    n.buses.add(
        cropland_df.index,
        carrier="land_cropland",
        region=cropland_df["region"],
        resource_class=cropland_df["resource_class"],
        water_supply=cropland_df["water_supply"],
    )
    cropland_bus_names = list(cropland_df.index)

    # Add pasture pool buses (per region/class, water-agnostic)
    # Only create from rainfed entries, since only rainfed land feeds pasture.
    rainfed_for_pasture = land_index_df[land_index_df["water_supply"] == "r"]
    pasture_bus_names = []
    if not rainfed_for_pasture.empty:
        pasture_df = (
            rainfed_for_pasture.groupby(["region", "resource_class", "pasture_bus"])
            .first()
            .reset_index()
        )
        pasture_df = pasture_df.set_index("pasture_bus")
        n.buses.add(
            pasture_df.index,
            carrier="land_pasture",
            region=pasture_df["region"],
            resource_class=pasture_df["resource_class"],
        )
        pasture_bus_names = list(pasture_df.index)

    # --- Existing cropland supply ---
    baseline_rows = land_index_df[land_index_df["existing_available_ha"] > 0].copy()
    if not baseline_rows.empty:
        # Add existing cropland buses
        baseline_bus_df = baseline_rows[
            ["existing_bus", "region", "resource_class", "water_supply"]
        ].drop_duplicates(subset=["existing_bus"])
        baseline_bus_df = baseline_bus_df.set_index("existing_bus")
        n.buses.add(
            baseline_bus_df.index,
            carrier="land_existing_cropland",
            region=baseline_bus_df["region"],
            resource_class=baseline_bus_df["resource_class"],
            water_supply=baseline_bus_df["water_supply"],
        )

        # Build suffix for name generation
        baseline_suffix = (
            baseline_rows["region"].astype(str)
            + "_c"
            + baseline_rows["resource_class"].astype(int).astype(str)
            + "_"
            + baseline_rows["water_supply"].astype(str)
        )
        baseline_rows["existing_gen_name"] = (
            "supply:land_existing_cropland:" + baseline_suffix
        )
        baseline_rows["existing_to_cropland_name"] = (
            "use:existing_land:" + baseline_suffix
        )
        baseline_rows["existing_available_mha"] = (
            baseline_rows["existing_available_ha"] / HA_PER_MHA
        )

        # Add generators for existing cropland
        existing_gen_df = baseline_rows.set_index("existing_gen_name")
        n.generators.add(
            existing_gen_df.index,
            bus=existing_gen_df["existing_bus"],
            carrier="land_existing_cropland",
            p_nom=existing_gen_df["existing_available_mha"],
            p_nom_extendable=False,
            marginal_cost=land_use_cost_bnusd_per_mha,
            region=existing_gen_df["region"],
            resource_class=existing_gen_df["resource_class"],
            water_supply=existing_gen_df["water_supply"],
        )

        # Links: existing → cropland pool
        existing_to_cropland_df = baseline_rows.set_index("existing_to_cropland_name")
        n.links.add(
            existing_to_cropland_df.index,
            carrier="land_use",
            bus0=existing_to_cropland_df["existing_bus"],
            bus1=existing_to_cropland_df["cropland_bus"],
            efficiency=1.0,
            p_nom=existing_to_cropland_df["existing_available_mha"],
            p_nom_extendable=False,
            region=existing_to_cropland_df["region"],
            resource_class=existing_to_cropland_df["resource_class"],
            water_supply=existing_to_cropland_df["water_supply"],
        )

        # Links: existing → pasture pool (rainfed only, no LUC emissions)
        rainfed_baseline = baseline_rows[baseline_rows["water_supply"] == "r"].copy()
        if not rainfed_baseline.empty:
            rainfed_baseline_suffix = (
                rainfed_baseline["region"].astype(str)
                + "_c"
                + rainfed_baseline["resource_class"].astype(int).astype(str)
            )
            rainfed_baseline["existing_to_pasture_name"] = (
                "use:existing_to_pasture:" + rainfed_baseline_suffix
            )
            rainfed_baseline["existing_available_mha"] = (
                rainfed_baseline["existing_available_ha"] / HA_PER_MHA
            )
            existing_to_pasture_df = rainfed_baseline.set_index(
                "existing_to_pasture_name"
            )
            n.links.add(
                existing_to_pasture_df.index,
                carrier="existing_to_pasture",
                bus0=existing_to_pasture_df["existing_bus"],
                bus1=existing_to_pasture_df["pasture_bus"],
                efficiency=1.0,
                p_nom=existing_to_pasture_df["existing_available_mha"],
                p_nom_extendable=False,
                region=existing_to_pasture_df["region"],
                resource_class=existing_to_pasture_df["resource_class"],
                water_supply=existing_to_pasture_df["water_supply"],
            )

    # --- New land supply ---
    expansion_rows = land_index_df[land_index_df["new_available_ha"] > 0].copy()
    if not expansion_rows.empty:
        # Add new land buses
        expansion_bus_df = expansion_rows[
            ["new_bus", "region", "resource_class", "water_supply"]
        ].drop_duplicates(subset=["new_bus"])
        expansion_bus_df = expansion_bus_df.set_index("new_bus")
        n.buses.add(
            expansion_bus_df.index,
            carrier="land_new",
            region=expansion_bus_df["region"],
            resource_class=expansion_bus_df["resource_class"],
            water_supply=expansion_bus_df["water_supply"],
        )

        # Build suffix for name generation
        expansion_suffix = (
            expansion_rows["region"].astype(str)
            + "_c"
            + expansion_rows["resource_class"].astype(int).astype(str)
            + "_"
            + expansion_rows["water_supply"].astype(str)
        )
        expansion_rows["new_gen_name"] = "supply:land_new:" + expansion_suffix
        expansion_rows["new_to_cropland_name"] = "convert:new_land:" + expansion_suffix
        expansion_rows["new_available_mha"] = (
            expansion_rows["new_available_ha"] / HA_PER_MHA
        )

        # Add generators for new land
        new_gen_df = expansion_rows.set_index("new_gen_name")
        n.generators.add(
            new_gen_df.index,
            bus=new_gen_df["new_bus"],
            carrier="land_new",
            p_nom_extendable=True,
            p_nom_max=new_gen_df["new_available_mha"],
            marginal_cost=land_use_cost_bnusd_per_mha,
            region=new_gen_df["region"],
            resource_class=new_gen_df["resource_class"],
            water_supply=new_gen_df["water_supply"],
        )

        # Links: new → cropland pool (with LUC emissions)
        if not disable_new_cropland:
            if not lef_df.empty:
                luc_cropland = merge_lef(
                    expansion_rows, lef_df, "cropland", allow_missing=False
                )
            else:
                luc_cropland = pd.Series(0.0, index=expansion_rows.index)
            expansion_rows["luc_cropland"] = luc_cropland.to_numpy()
            new_to_cropland_df = expansion_rows.set_index("new_to_cropland_name")
            # tCO2/ha = MtCO2/Mha numerically, no conversion needed
            n.links.add(
                new_to_cropland_df.index,
                carrier="land_conversion",
                bus0=new_to_cropland_df["new_bus"],
                bus1=new_to_cropland_df["cropland_bus"],
                efficiency=1.0,
                bus2="emission:co2",
                efficiency2=new_to_cropland_df["luc_cropland"],
                p_nom_extendable=True,
                p_nom_max=new_to_cropland_df["new_available_mha"],
                region=new_to_cropland_df["region"],
                resource_class=new_to_cropland_df["resource_class"],
                water_supply=new_to_cropland_df["water_supply"],
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

                rainfed_expansion_suffix = (
                    rainfed_expansion["region"].astype(str)
                    + "_c"
                    + rainfed_expansion["resource_class"].astype(int).astype(str)
                )
                rainfed_expansion["new_to_pasture_name"] = (
                    "convert:new_to_pasture:" + rainfed_expansion_suffix
                )
                rainfed_expansion["new_available_mha"] = (
                    rainfed_expansion["new_available_ha"] / HA_PER_MHA
                )
                rainfed_expansion["luc_pasture"] = luc_pasture.to_numpy()
                new_to_pasture_df = rainfed_expansion.set_index("new_to_pasture_name")
                n.links.add(
                    new_to_pasture_df.index,
                    carrier="new_to_pasture",
                    bus0=new_to_pasture_df["new_bus"],
                    bus1=new_to_pasture_df["pasture_bus"],
                    efficiency=1.0,
                    bus2="emission:co2",
                    efficiency2=new_to_pasture_df["luc_pasture"],
                    p_nom_extendable=True,
                    p_nom_max=new_to_pasture_df["new_available_mha"],
                    region=new_to_pasture_df["region"],
                    resource_class=new_to_pasture_df["resource_class"],
                    water_supply=new_to_pasture_df["water_supply"],
                )

    # Add slack generators to both pool types if enabled
    if enable_land_slack:
        primary_resources._add_land_slack_generators(
            n, cropland_bus_names, land_slack_cost
        )
        primary_resources._add_land_slack_generators(
            n, pasture_bus_names, land_slack_cost
        )

    # --- Existing grassland supply (convertible + marginal) ---
    grassland_supply = _build_existing_grassland_supply_df(
        existing_grassland_convertible_area=convertible_grassland,
        existing_grassland_marginal_area=marginal_grassland,
    )
    if not grassland_supply.empty:
        source_name_map = {
            "convertible": "existing_grassland_convertible",
            "marginal": "existing_grassland_marginal",
        }
        source_carrier_map = {
            "convertible": "land_existing_grassland_convertible",
            "marginal": "land_existing_grassland_marginal",
        }

        grassland_supply["area_mha"] = (
            reg_limit * grassland_supply["area_ha"] / HA_PER_MHA
        )
        grassland_supply = grassland_supply[grassland_supply["area_mha"] > 0.0].copy()
        if grassland_supply.empty:
            return

        suffix = (
            grassland_supply["region"]
            + "_c"
            + grassland_supply["resource_class"].astype(str)
        )
        grassland_supply["source_name"] = grassland_supply["land_type"].map(
            source_name_map
        )
        grassland_supply["source_carrier"] = grassland_supply["land_type"].map(
            source_carrier_map
        )
        grassland_supply["existing_bus"] = (
            "land:" + grassland_supply["source_name"] + ":" + suffix
        )
        grassland_supply["generator_name"] = (
            "supply:land_" + grassland_supply["source_name"] + ":" + suffix
        )
        grassland_supply["pasture_bus"] = "land:pasture:" + suffix
        grassland_supply["to_pasture_name"] = (
            "use:" + grassland_supply["source_name"] + "_to_pasture:" + suffix
        )
        grassland_supply["spare_name"] = (
            "spare:" + grassland_supply["source_name"] + ":" + suffix
        )
        grassland_supply["spared_bus"] = (
            "land:spared_" + grassland_supply["source_name"] + ":" + suffix
        )
        grassland_supply["spared_store"] = (
            "store:spared_" + grassland_supply["source_name"] + ":" + suffix
        )

        # Ensure pasture pool buses exist for region/classes that only appear in
        # existing grassland inputs.
        missing_pasture = grassland_supply.loc[
            ~grassland_supply["pasture_bus"].isin(n.buses.static.index),
            ["pasture_bus", "region", "resource_class"],
        ].drop_duplicates(subset=["pasture_bus"])
        if not missing_pasture.empty:
            missing_pasture = missing_pasture.set_index("pasture_bus")
            n.buses.add(
                missing_pasture.index,
                carrier="land_pasture",
                region=missing_pasture["region"],
                resource_class=missing_pasture["resource_class"],
            )

        bus_df = grassland_supply[
            ["existing_bus", "source_carrier", "region", "resource_class", "land_type"]
        ].drop_duplicates(subset=["existing_bus"])
        bus_df = bus_df.set_index("existing_bus")
        n.buses.add(
            bus_df.index,
            carrier=bus_df["source_carrier"],
            region=bus_df["region"],
            resource_class=bus_df["resource_class"],
            land_type=bus_df["land_type"],
        )

        gen_df = grassland_supply.set_index("generator_name")
        n.generators.add(
            gen_df.index,
            bus=gen_df["existing_bus"],
            carrier=gen_df["source_carrier"],
            p_nom_extendable=True,
            p_nom_max=gen_df["area_mha"],
            marginal_cost=land_use_cost_bnusd_per_mha,
            region=gen_df["region"],
            resource_class=gen_df["resource_class"],
            water_supply="rainfed",
            land_type=gen_df["land_type"],
        )

        if enable_land_slack:
            primary_resources._add_land_slack_generators(
                n,
                list(bus_df.index),
                land_slack_cost,
            )

        to_pasture_df = grassland_supply.set_index("to_pasture_name")
        n.links.add(
            to_pasture_df.index,
            carrier="existing_grassland_to_pasture",
            bus0=to_pasture_df["existing_bus"],
            bus1=to_pasture_df["pasture_bus"],
            efficiency=1.0,
            p_nom_extendable=True,
            p_nom_max=to_pasture_df["area_mha"],
            region=to_pasture_df["region"],
            resource_class=to_pasture_df["resource_class"],
            water_supply="rainfed",
            land_type=to_pasture_df["land_type"],
        )

        if not disable_spared_grassland:
            spared_bus_df = grassland_supply[
                ["spared_bus", "region", "resource_class", "land_type"]
            ].drop_duplicates(subset=["spared_bus"])
            spared_bus_df = spared_bus_df.set_index("spared_bus")
            n.buses.add(
                spared_bus_df.index,
                carrier="spared_grassland",
                region=spared_bus_df["region"],
                resource_class=spared_bus_df["resource_class"],
                land_type=spared_bus_df["land_type"],
            )

            store_df = grassland_supply.set_index("spared_store")
            n.stores.add(
                store_df.index,
                bus=store_df["spared_bus"],
                carrier="spared_grassland",
                e_nom_extendable=True,
                region=store_df["region"],
                resource_class=store_df["resource_class"],
                water_supply="rainfed",
                land_type=store_df["land_type"],
            )

            spare_lef_input = grassland_supply[["region", "resource_class"]].copy()
            spare_lef_input["water_supply"] = "r"
            grassland_supply["spare_lef"] = merge_lef(
                spare_lef_input,
                lef_df,
                "spared",
                allow_missing=True,
            ).to_numpy()

            spare_df = grassland_supply.set_index("spare_name")
            n.links.add(
                spare_df.index,
                carrier="spare_existing_grassland",
                bus0=spare_df["existing_bus"],
                bus1=spare_df["spared_bus"],
                efficiency=1.0,
                bus2="emission:co2",
                # tCO2/ha = MtCO2/Mha numerically; spared LEFs are negative
                efficiency2=spare_df["spare_lef"],
                p_nom_extendable=True,
                p_nom_max=spare_df["area_mha"],
                region=spare_df["region"],
                resource_class=spare_df["resource_class"],
                water_supply="rainfed",
                land_type=spare_df["land_type"],
            )
