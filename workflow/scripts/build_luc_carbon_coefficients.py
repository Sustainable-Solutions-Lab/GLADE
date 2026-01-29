"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
import xarray as xr

CO2_PER_C = 44.0 / 12.0
ZONE_ORDER = ["tropical", "temperate", "boreal"]


def _load_transform(ds: xr.Dataset) -> tuple[Affine, int, int, np.ndarray, np.ndarray]:
    try:
        transform = Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc
    height = int(ds.attrs.get("height", ds.sizes["y"]))
    width = int(ds.attrs.get("width", ds.sizes["x"]))

    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e

    return transform, height, width, lon.astype(np.float32), lat.astype(np.float32)


def _zone_index(latitudes: np.ndarray, width: int) -> np.ndarray:
    """Assign coarse climatic zones based on latitude."""
    lat_grid = np.repeat(latitudes[:, np.newaxis], width, axis=1)
    abs_lat = np.abs(lat_grid)
    zone_idx = np.ones(lat_grid.shape, dtype=np.int8)  # Temperate default
    zone_idx[abs_lat < 23.5] = 0  # Tropical
    zone_idx[abs_lat >= 50.0] = 2  # Boreal
    return zone_idx


def _area_matrix(transform: Affine, height: int, width: int) -> np.ndarray:
    pixel_width = abs(transform.a)
    pixel_height = abs(transform.e)
    left = transform.c
    top = transform.f

    geod = Geod(ellps="WGS84")
    row_areas = np.zeros(height, dtype=np.float64)
    for row in range(height):
        lat_center = top + (row + 0.5) * transform.e
        lat_top = lat_center + pixel_height / 2.0
        lat_bottom = lat_center - pixel_height / 2.0
        lon_left = left
        lon_right = left + pixel_width
        lons = [lon_left, lon_right, lon_right, lon_left, lon_left]
        lats = [lat_bottom, lat_bottom, lat_top, lat_top, lat_bottom]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats)
        row_areas[row] = abs(area_m2) / 10000.0  # m² → ha
    return np.repeat(row_areas[:, np.newaxis], width, axis=1).astype(np.float32)


def _zone_parameters(path: str) -> dict[str, np.ndarray]:
    params = pd.read_csv(path, comment="#").set_index("zone")
    missing = [zone for zone in ZONE_ORDER if zone not in params.index]
    if missing:
        raise ValueError(
            "zone parameter table missing entries for: " + ", ".join(missing)
        )
    ordered = params.loc[ZONE_ORDER]
    return {key: ordered[key].to_numpy(dtype=np.float32) for key in ordered.columns}


def _region_name_map(regions_path: str) -> dict[int, str]:
    regions_gdf = gpd.read_file(regions_path)
    if "region" not in regions_gdf.columns:
        raise ValueError("regions.geojson must contain a 'region' column")
    return {idx: str(name) for idx, name in enumerate(regions_gdf["region"].tolist())}


def _ensure_mode_zero(mode: str) -> None:
    if mode.lower() != "zero":
        raise ValueError(
            f"Unsupported managed_flux_mode '{mode}'; only 'zero' is implemented"
        )


def main() -> None:
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    agb_path: str = snakemake.input.agb  # type: ignore[name-defined]
    soc_path: str = snakemake.input.soc  # type: ignore[name-defined]
    regrowth_path: str = snakemake.input.regrowth  # type: ignore[name-defined]
    zone_params_path: str = snakemake.input.zone_parameters  # type: ignore[name-defined]

    pulses_out: str = snakemake.output.pulses  # type: ignore[name-defined]
    annual_out: str = snakemake.output.annualized  # type: ignore[name-defined]
    coeffs_out: str = snakemake.output.coefficients  # type: ignore[name-defined]

    horizon_years: int = int(snakemake.params.horizon_years)  # type: ignore[name-defined]
    managed_flux_mode: str = str(snakemake.params.managed_flux_mode)  # type: ignore[name-defined]
    _ensure_mode_zero(managed_flux_mode)
    if horizon_years <= 0:
        raise ValueError("luc.horizon_years must be positive")

    Path(coeffs_out).parent.mkdir(parents=True, exist_ok=True)

    classes_ds = xr.load_dataset(classes_path)
    transform, height, width, lon, lat = _load_transform(classes_ds)
    region_id = classes_ds["region_id"].astype(np.int32).values
    resource_class = classes_ds["resource_class"].astype(np.int16).values

    zone_idx = _zone_index(lat, width)
    params = _zone_parameters(zone_params_path)
    area_matrix = _area_matrix(transform, height, width)

    agb = xr.load_dataset(agb_path)["agb_tc_per_ha"].astype(np.float32).values
    soc_0_30 = xr.load_dataset(soc_path)["soc_0_30_tc_per_ha"].astype(np.float32).values
    regrowth_tc = (
        xr.load_dataset(regrowth_path)["regrowth_tc_per_ha_yr"]
        .astype(np.float32)
        .values
    )

    bgb_ratio_nat = params["bgb_ratio_nat"][zone_idx]
    soc_depth_factor = params["soc_depth_factor"][zone_idx]
    agb_crop = params["agb_crop_tc_per_ha"][zone_idx]
    bgb_ratio_crop = params["bgb_ratio_ag_crop"][zone_idx]
    agb_past = params["agb_past_tc_per_ha"][zone_idx]
    bgb_ratio_past = params["bgb_ratio_ag_past"][zone_idx]
    soc_factor_crop = params["soc_factor_crop"][zone_idx]
    soc_factor_past = params["soc_factor_past"][zone_idx]

    agb = np.where(np.isfinite(agb), agb, np.nan)
    soc_0_30 = np.where(np.isfinite(soc_0_30), soc_0_30, np.nan)

    soc_nat = soc_0_30 * soc_depth_factor
    bgb_nat = agb * bgb_ratio_nat
    s_nat = agb + bgb_nat + soc_nat

    bgb_crop = agb_crop * bgb_ratio_crop
    s_ag_crop = agb_crop + bgb_crop + soc_nat * soc_factor_crop

    bgb_past = agb_past * bgb_ratio_past
    s_ag_past = agb_past + bgb_past + soc_nat * soc_factor_past

    p_crop = (s_nat - s_ag_crop) * CO2_PER_C
    p_past = (s_nat - s_ag_past) * CO2_PER_C

    # Convert regrowth rates from tC to tCO2
    regrowth = np.where(np.isfinite(regrowth_tc), regrowth_tc, 0.0) * CO2_PER_C

    # Land conversion LEFs include only pulse emissions (amortized over horizon).
    # Regrowth opportunity cost is NOT included here to avoid double-counting:
    # the model explicitly represents the alternative (spare land for regrowth)
    # via separate spare_land links with lef_spared.
    lef_crop = p_crop / horizon_years
    lef_past = p_past / horizon_years

    # Spared land provides negative emissions (sequestration through regrowth) if:
    # 1. Cook-Patton regrowth data exists for this cell (potential forest area)
    # 2. Current above-ground biomass is below threshold (recently cleared/degraded)
    # Areas with high existing biomass (mature forest) are already at equilibrium
    # and do not exhibit the rapid early-successional regrowth that Cook-Patton quantifies.
    agb_threshold: float = float(snakemake.params.agb_threshold)  # type: ignore[name-defined]
    lef_spared = np.where(agb <= agb_threshold, -regrowth, 0.0)

    pulses_ds = xr.Dataset(
        {
            "P_crop_tCO2_per_ha": (("y", "x"), p_crop.astype(np.float32)),
            "P_pasture_tCO2_per_ha": (("y", "x"), p_past.astype(np.float32)),
        },
        coords={"y": lat, "x": lon},
    )
    pulses_ds.to_netcdf(
        pulses_out,
        encoding={
            "P_crop_tCO2_per_ha": {"zlib": True, "dtype": "float32"},
            "P_pasture_tCO2_per_ha": {"zlib": True, "dtype": "float32"},
        },
    )

    lef_stack = np.stack(
        [
            lef_crop.astype(np.float32),
            lef_past.astype(np.float32),
            lef_spared.astype(np.float32),
        ],
        axis=0,
    )
    annual_ds = xr.Dataset(
        {
            "LEF_tCO2_per_ha_yr": (
                ("use", "y", "x"),
                lef_stack,
            )
        },
        coords={
            "use": np.array(["cropland", "pasture", "spared"], dtype="U8"),
            "y": lat,
            "x": lon,
        },
    )
    annual_ds.to_netcdf(
        annual_out,
        encoding={"LEF_tCO2_per_ha_yr": {"zlib": True, "dtype": "float32"}},
    )

    region_map = _region_name_map(regions_path)
    valid_cells = (
        (region_id >= 0)
        & (resource_class >= 0)
        & np.isfinite(area_matrix)
        & (area_matrix > 0)
    )

    uses = {
        "cropland": lef_crop,
        "pasture": lef_past,
        "spared": lef_spared,
    }
    water_options = {
        "cropland": ("r", "i"),
        "pasture": ("r",),
        "spared": ("r", "i"),
    }

    rows: list[dict[str, object]] = []
    region_ids = np.unique(region_id[valid_cells])
    for rid in region_ids:
        region_mask = valid_cells & (region_id == rid)
        if not np.any(region_mask):
            continue
        class_ids = np.unique(resource_class[region_mask])
        for cid in class_ids:
            class_mask = region_mask & (resource_class == cid)
            if not np.any(class_mask):
                continue
            weights = area_matrix[class_mask]

            # Compute area-weighted mean AGB for this region/class
            agb_values = agb[class_mask]
            agb_valid = np.isfinite(agb_values)
            if np.any(agb_valid):
                w_agb = weights[agb_valid]
                if w_agb.sum() > 0:
                    mean_agb_tc_per_ha = float(
                        np.sum(agb_values[agb_valid] * w_agb) / np.sum(w_agb)
                    )
                else:
                    mean_agb_tc_per_ha = 0.0
            else:
                mean_agb_tc_per_ha = 0.0

            for use, data in uses.items():
                values = data[class_mask]
                valid = np.isfinite(values)
                if not np.any(valid):
                    continue
                w = weights[valid]
                if w.sum() <= 0:
                    continue
                avg_lef = float(np.sum(values[valid] * w) / np.sum(w))
                for water in water_options[use]:
                    rows.append(
                        {
                            "region": region_map.get(rid, f"region{rid:04d}"),
                            "resource_class": int(cid),
                            "water": water,
                            "use": use,
                            "LEF_tCO2_per_ha_yr": avg_lef,
                            "mean_agb_tc_per_ha": mean_agb_tc_per_ha,
                        }
                    )

    coeffs_df = pd.DataFrame(rows)
    coeffs_df.sort_values(["region", "resource_class", "water", "use"], inplace=True)
    coeffs_df.to_csv(coeffs_out, index=False)


if __name__ == "__main__":
    main()
