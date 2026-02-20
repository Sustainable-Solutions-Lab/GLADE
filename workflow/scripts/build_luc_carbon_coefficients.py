"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
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


def _zone_parameters(path: str) -> dict[str, np.ndarray]:
    params = pd.read_csv(path, comment="#").set_index("zone")
    missing = [zone for zone in ZONE_ORDER if zone not in params.index]
    if missing:
        raise ValueError(
            "zone parameter table missing entries for: " + ", ".join(missing)
        )
    ordered = params.loc[ZONE_ORDER]
    return {key: ordered[key].to_numpy(dtype=np.float32) for key in ordered.columns}


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
    resource_class = classes_ds["resource_class"].astype(np.int16).values

    zone_idx = _zone_index(lat, width)
    params = _zone_parameters(zone_params_path)

    agb = xr.load_dataset(agb_path)["agb_tc_per_ha"].astype(np.float32).values
    soc_0_30 = xr.load_dataset(soc_path)["soc_0_30_tc_per_ha"].astype(np.float32).values
    regrowth_tc = (
        xr.load_dataset(regrowth_path)["regrowth_tc_per_ha_yr"]
        .astype(np.float32)
        .values
    )

    lc_masks_path: str = snakemake.input.lc_masks  # type: ignore[name-defined]
    lc_ds = xr.load_dataset(lc_masks_path)
    cropland_frac = lc_ds["cropland_fraction"].astype(np.float32).values
    grassland_frac = lc_ds["grassland_fraction"].astype(np.float32).values
    pasture_frac = lc_ds["pasture_fraction"].astype(np.float32).values
    natural_frac = np.clip(1.0 - cropland_frac - grassland_frac, 0.0, 1.0)

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

    # --- Aggregate per-pixel LEFs to per-region/class coefficients ---
    # Uses exact_extract with region polygons and class masks so that tiny
    # regions that don't cover a full grid cell still get correct
    # area-weighted LEFs via fractional cell overlaps.

    regions_gdf = gpd.read_file(regions_path)
    crs_wkt = classes_ds.attrs.get("crs_wkt")
    if crs_wkt:
        regions_gdf = regions_gdf.to_crs(crs_wkt)
    regions_for_extract = regions_gdf.reset_index()

    xmin = float(transform.c)
    ymax = float(transform.f)
    xmax = xmin + width * transform.a
    ymin = ymax + height * transform.e
    raster_kwargs = {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "nodata": np.nan,
        "srs_wkt": crs_wkt,
    }

    weighted_uses = {
        "cropland": (lef_crop.astype(np.float32), natural_frac),
        "pasture": (lef_past.astype(np.float32), natural_frac),
        "spared_cropland": (lef_spared.astype(np.float32), cropland_frac),
        "spared_grassland": (lef_spared.astype(np.float32), pasture_frac),
    }
    water_options = {
        "cropland": ("r", "i"),
        "pasture": ("r",),
        "spared_cropland": ("r", "i"),
        "spared_grassland": ("r",),
    }

    n_classes = (
        int(np.nanmax(resource_class)) + 1
        if np.isfinite(resource_class.astype(float)).any()
        else 0
    )

    frames: list[pd.DataFrame] = []
    for cls in range(n_classes):
        mask_float = (resource_class == cls).astype(np.float32)
        if not np.any(mask_float > 0):
            continue

        # Area-weighted mean LEF per region for each use type
        for use, (lef_arr, lc_weight) in weighted_uses.items():
            composite_weight = mask_float * lc_weight
            composite_src = NumPyRasterSource(
                composite_weight,
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                srs_wkt=crs_wkt,
            )
            lef_src = NumPyRasterSource(lef_arr, **raster_kwargs)
            lef_stats = exact_extract(
                lef_src,
                regions_for_extract,
                ["weighted_mean"],
                weights=composite_src,
                include_cols=["region"],
                output="pandas",
            )

            merged = lef_stats.rename(columns={"weighted_mean": "LEF_tCO2_per_ha_yr"})
            merged["resource_class"] = cls
            merged["use"] = use
            merged = merged.dropna(subset=["LEF_tCO2_per_ha_yr"])
            merged = merged[np.isfinite(merged["LEF_tCO2_per_ha_yr"])]
            if merged.empty:
                continue

            # Expand water supply options for this use type
            for water in water_options[use]:
                frame = merged[
                    [
                        "region",
                        "resource_class",
                        "use",
                        "LEF_tCO2_per_ha_yr",
                    ]
                ].copy()
                frame["water"] = water
                frames.append(frame)

    if frames:
        coeffs_df = pd.concat(frames, ignore_index=True)
    else:
        coeffs_df = pd.DataFrame(
            columns=[
                "region",
                "resource_class",
                "water",
                "use",
                "LEF_tCO2_per_ha_yr",
            ]
        )
    coeffs_df.sort_values(["region", "resource_class", "water", "use"], inplace=True)
    coeffs_df.to_csv(coeffs_out, index=False)


if __name__ == "__main__":
    main()
