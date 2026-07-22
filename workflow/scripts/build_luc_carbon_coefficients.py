"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from pathlib import Path  # noqa: E402

from affine import Affine  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402

from workflow.scripts.region_class_aggregation import (  # noqa: E402
    CellMapping,
    load_cell_mapping,
)

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
    df = pd.read_csv(path, comment="#")
    params = df.pivot(index="zone", columns="parameter", values="value")
    missing = [zone for zone in ZONE_ORDER if zone not in params.index]
    if missing:
        raise ValueError(
            "zone parameter table missing entries for: " + ", ".join(missing)
        )
    ordered = params.loc[ZONE_ORDER]
    return {key: ordered[key].to_numpy(dtype=np.float32) for key in ordered.columns}


def _correct_subpixel_soc(
    soc_0_30: np.ndarray,
    cropland_frac: np.ndarray,
    pasture_frac: np.ndarray,
    natural_frac: np.ndarray,
    soc_factor_crop: np.ndarray,
    soc_factor_past: np.ndarray,
) -> np.ndarray:
    """Recover natural-state SOC from observed pixel-average values.

    The observed 0-30 cm SOC is a mixture of natural-state SOC and
    depleted agricultural SOC (``soc_natural * soc_factor``).  We
    invert the mixing to recover the natural-state SOC.

    ``soc_factor_past`` is the grazing-intensity-scaled pasture depletion
    factor (-> 1.0 as GI -> 0), so lightly grazed grassland contributes
    near-natural SOC even though it is counted as pasture, not natural land.
    """
    soc_denom = (
        natural_frac + cropland_frac * soc_factor_crop + pasture_frac * soc_factor_past
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        soc_corrected = np.where(soc_denom > 0, soc_0_30 / soc_denom, soc_0_30)

    return soc_corrected.astype(np.float32)


def _decompose_agb(
    agb_obs: np.ndarray,
    cropland_frac: np.ndarray,
    pasture_frac: np.ndarray,
    forest_frac: np.ndarray,
    nonforest_frac: np.ndarray,
    agb_crop: np.ndarray,
    agb_past: np.ndarray,
    agb_nonforest_zone: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Decompose observed pixel-average AGB into forest and non-forest components.

    Forest AGB is isolated by subtracting the estimated agricultural and
    non-forest natural AGB contributions from the observed pixel average,
    then dividing by the forest fraction.  Non-forest natural AGB uses
    zone-level estimates (shrubland/savanna defaults).

    ``agb_past`` is the grazing-intensity-scaled pasture AGB (-> the
    non-forest natural AGB as GI -> 0), so lightly grazed grassland carries
    near-natural AGB even though it is counted as pasture.

    Returns
    -------
    agb_forest : np.ndarray
        Estimated forest AGB (tC/ha).  Zero where ``forest_frac == 0``.
    agb_nonforest : np.ndarray
        Non-forest natural AGB (tC/ha).  Zone-level value where
        ``nonforest_frac > 0``, zero otherwise.
    """
    ag_agb = cropland_frac * agb_crop + pasture_frac * agb_past
    agb_nonforest = np.where(nonforest_frac > 0, agb_nonforest_zone, 0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        agb_forest = np.where(
            forest_frac > 0,
            np.clip(
                (agb_obs - ag_agb - nonforest_frac * agb_nonforest_zone) / forest_frac,
                0.0,
                None,
            ),
            0.0,
        )

    return agb_forest.astype(np.float32), agb_nonforest.astype(np.float32)


def _ensure_mode_zero(mode: str) -> None:
    if mode.lower() != "zero":
        raise ValueError(
            f"Unsupported managed_flux_mode '{mode}'; only 'zero' is implemented"
        )


def _weighted_mean_by_group(
    values: np.ndarray,
    weights: np.ndarray,
    mapping: CellMapping,
) -> np.ndarray:
    """Return weighted means for every exact region/resource-class group."""
    mapped_values = values.ravel()[mapping.cell_ids]
    mapped_weights = weights.ravel()[mapping.cell_ids] * mapping.coverage
    valid = ~np.isnan(mapped_values) & ~np.isnan(mapped_weights)
    group_ids = mapping.group_ids[valid]
    mapped_weights = mapped_weights[valid]
    numerator = np.bincount(
        group_ids,
        weights=mapped_values[valid] * mapped_weights,
        minlength=mapping.n_groups,
    )
    denominator = np.bincount(
        group_ids,
        weights=mapped_weights,
        minlength=mapping.n_groups,
    )
    return np.divide(
        numerator,
        denominator,
        out=np.full(mapping.n_groups, np.nan),
        where=denominator != 0,
    )


def main() -> None:
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    cell_mapping_path: str = snakemake.input.cell_mapping  # type: ignore[name-defined]
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

    with xr.open_dataset(classes_path) as classes_ds:
        _transform, height, width, lon, lat = _load_transform(classes_ds)

    zone_idx = _zone_index(lat, 1)
    params = _zone_parameters(zone_params_path)

    agb = xr.load_dataset(agb_path)["agb_tc_per_ha"].astype(np.float32).values
    soc_0_30 = xr.load_dataset(soc_path)["soc_0_30_tc_per_ha"].astype(np.float32).values
    regrowth_tc = (
        xr.load_dataset(regrowth_path)["regrowth_tc_per_ha_yr"]
        .astype(np.float32)
        .values
    )

    lc_masks_path: str = snakemake.input.lc_masks  # type: ignore[name-defined]
    with xr.open_dataset(lc_masks_path) as lc_ds:
        cropland_frac = lc_ds["cropland_fraction"].load().values
        pasture_frac = lc_ds["pasture_fraction"].load().values
        forest_frac = lc_ds["forest_fraction"].load().values
        grazing_intensity = lc_ds["grazing_intensity"].load().values
    # Dominant-cover partition (see ``prepare_luc_inputs``): ``pasture_frac`` is
    # the open grazed land (LUIcube grass not overlapping forest/cropland), so
    # genuinely grazed open land is pasture -- not natural land -- for carbon,
    # while silvopasture/stubble grazing stays with its forest/cropland cover.
    # Grazing intensity enters as a per-hectare DEPLETION factor below (lightly
    # grazed -> near-natural stocks), not as an area discount. cropland +
    # pasture + forest + natural <= 1 by construction, so natural_frac (which
    # still includes forest) and nonforest_frac are clean.
    natural_frac = np.clip(1.0 - cropland_frac - pasture_frac, 0.0, 1.0)
    nonforest_frac = np.clip(natural_frac - forest_frac, 0.0, None)

    bgb_ratio_nat = params["bgb_ratio_nat"][zone_idx]
    bgb_ratio_nonforest = params["bgb_ratio_nonforest"][zone_idx]
    soc_depth_factor = params["soc_depth_factor"][zone_idx]
    agb_crop = params["agb_crop_tc_per_ha"][zone_idx]
    bgb_ratio_crop = params["bgb_ratio_ag_crop"][zone_idx]
    agb_past = params["agb_past_tc_per_ha"][zone_idx]
    bgb_ratio_past = params["bgb_ratio_ag_past"][zone_idx]
    soc_factor_crop = params["soc_factor_crop"][zone_idx]
    soc_factor_past = params["soc_factor_past"][zone_idx]
    agb_nonforest_zone = params["agb_nonforest_tc_per_ha"][zone_idx]

    # Grazing intensity scales the per-hectare carbon depletion of pasture, so a
    # lightly grazed hectare (GI -> 0) carries near-natural stocks and an
    # intensively managed one (GI -> 1) the full agricultural depletion. These
    # effective factors are used only for the sub-pixel natural-state recovery
    # below; the conversion target (new managed pasture, ``s_ag_past``) keeps the
    # full zone-level depletion.
    soc_factor_past_eff = 1.0 - grazing_intensity * (1.0 - soc_factor_past)
    agb_past_eff = (
        1.0 - grazing_intensity
    ) * agb_nonforest_zone + grazing_intensity * agb_past

    agb_obs = np.where(np.isfinite(agb), agb, np.nan)
    soc_0_30 = np.where(np.isfinite(soc_0_30), soc_0_30, np.nan)

    # Sub-pixel SOC correction: recover natural-state SOC from observed
    # pixel averages that are depleted by the agricultural portion.
    soc_0_30_nat = _correct_subpixel_soc(
        soc_0_30,
        cropland_frac,
        pasture_frac,
        natural_frac,
        soc_factor_crop,
        soc_factor_past_eff,
    )

    # Decompose observed AGB into forest and non-forest natural components.
    agb_forest, agb_nonforest = _decompose_agb(
        agb_obs,
        cropland_frac,
        pasture_frac,
        forest_frac,
        nonforest_frac,
        agb_crop,
        agb_past_eff,
        agb_nonforest_zone,
    )

    # SOC is not differentiated between forest and non-forest natural land
    # (0-30 cm SOC doesn't vary as dramatically as AGB between cover types).
    soc_nat = soc_0_30_nat * soc_depth_factor

    # --- Forest carbon stocks ---
    bgb_forest = agb_forest * bgb_ratio_nat
    s_forest = agb_forest + bgb_forest + soc_nat

    # --- Non-forest natural carbon stocks ---
    bgb_nonforest = agb_nonforest * bgb_ratio_nonforest
    s_nonforest = agb_nonforest + bgb_nonforest + soc_nat

    # --- Agricultural carbon stocks ---
    bgb_crop = agb_crop * bgb_ratio_crop
    s_ag_crop = agb_crop + bgb_crop + soc_nat * soc_factor_crop

    bgb_past = agb_past * bgb_ratio_past
    s_ag_past = agb_past + bgb_past + soc_nat * soc_factor_past

    # --- Pulse emissions (tCO2/ha) for 4 conversion pathways ---
    p_crop_forest = (s_forest - s_ag_crop) * CO2_PER_C
    p_crop_nonforest = (s_nonforest - s_ag_crop) * CO2_PER_C
    p_past_forest = (s_forest - s_ag_past) * CO2_PER_C
    p_past_nonforest = (s_nonforest - s_ag_past) * CO2_PER_C

    # Convert regrowth rates from tC to tCO2
    regrowth = np.where(np.isfinite(regrowth_tc), regrowth_tc, 0.0) * CO2_PER_C

    # Land conversion LEFs include only pulse emissions (amortized over horizon).
    # Regrowth opportunity cost is NOT included here to avoid double-counting:
    # the model explicitly represents the alternative (spare land for regrowth)
    # via separate spare_land links with lef_spared.
    lef_crop_forest = p_crop_forest / horizon_years
    lef_crop_nonforest = p_crop_nonforest / horizon_years
    lef_past_forest = p_past_forest / horizon_years
    lef_past_nonforest = p_past_nonforest / horizon_years

    # Spared land provides negative emissions (sequestration through regrowth).
    # Cook-Patton regrowth data is zero where no regrowth potential exists,
    # and LEFs are already area-weighted by cropland_frac / pasture_frac.
    lef_spared = -regrowth

    # --- Conversion shares: fraction of convertible land that is forest vs. non-forest ---
    # Convertible = natural land (1 - cropland - pasture). Non-forest
    # natural land now includes natural grassland (savanna, steppe),
    # which is convertible to managed pasture or cropland.
    with np.errstate(divide="ignore", invalid="ignore"):
        share_forest = np.where(
            natural_frac > 0, forest_frac / natural_frac, 0.0
        ).astype(np.float32)
        share_nonforest = np.where(
            natural_frac > 0, nonforest_frac / natural_frac, 0.0
        ).astype(np.float32)

    del (
        agb,
        soc_0_30,
        regrowth_tc,
        grazing_intensity,
        zone_idx,
        bgb_ratio_nat,
        bgb_ratio_nonforest,
        soc_depth_factor,
        agb_crop,
        bgb_ratio_crop,
        agb_past,
        bgb_ratio_past,
        soc_factor_crop,
        soc_factor_past,
        agb_nonforest_zone,
        soc_factor_past_eff,
        agb_past_eff,
        agb_obs,
        soc_0_30_nat,
        agb_forest,
        agb_nonforest,
        soc_nat,
        bgb_forest,
        s_forest,
        bgb_nonforest,
        s_nonforest,
        bgb_crop,
        s_ag_crop,
        bgb_past,
        s_ag_past,
    )

    pulses_ds = xr.Dataset(
        {
            "P_crop_forest_tCO2_per_ha": (
                ("y", "x"),
                p_crop_forest.astype(np.float32),
            ),
            "P_crop_nonforest_tCO2_per_ha": (
                ("y", "x"),
                p_crop_nonforest.astype(np.float32),
            ),
            "P_pasture_forest_tCO2_per_ha": (
                ("y", "x"),
                p_past_forest.astype(np.float32),
            ),
            "P_pasture_nonforest_tCO2_per_ha": (
                ("y", "x"),
                p_past_nonforest.astype(np.float32),
            ),
        },
        coords={"y": lat, "x": lon},
    )
    pulses_ds.to_netcdf(
        pulses_out,
        encoding={v: {"zlib": True, "dtype": "float32"} for v in pulses_ds.data_vars},
    )
    del pulses_ds
    del (
        p_crop_forest,
        p_crop_nonforest,
        p_past_forest,
        p_past_nonforest,
        regrowth,
    )

    use_names = [
        "cropland_forest",
        "cropland_nonforest",
        "pasture_forest",
        "pasture_nonforest",
        "spared",
    ]
    lef_stack = np.stack(
        [
            lef_crop_forest.astype(np.float32),
            lef_crop_nonforest.astype(np.float32),
            lef_past_forest.astype(np.float32),
            lef_past_nonforest.astype(np.float32),
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
            "use": np.array(use_names, dtype="U20"),
            "y": lat,
            "x": lon,
        },
    )
    annual_ds.to_netcdf(
        annual_out,
        encoding={"LEF_tCO2_per_ha_yr": {"zlib": True, "dtype": "float32"}},
    )
    del annual_ds, lef_stack

    # --- Aggregate per-pixel LEFs to per-region/class coefficients ---
    # Reuse the exact fractional cell coverage shared by crop raster rules.
    mapping = load_cell_mapping(cell_mapping_path)
    if mapping.shape != (height, width):
        raise ValueError(
            f"Cell mapping shape {mapping.shape} does not match resource-class "
            f"shape {(height, width)}"
        )
    unit_share = np.ones_like(share_forest)

    # For conversion uses, the LEF is weighted by the relevant land-cover
    # fraction (forest or nonforest), and the conversion_share tracks how
    # much of the convertible (nonag) land each sub-type represents.
    weighted_uses: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {
        # (lef_array, area_weight, conversion_share)
        "cropland_forest": (
            lef_crop_forest,
            forest_frac,
            share_forest,
        ),
        "cropland_nonforest": (
            lef_crop_nonforest,
            nonforest_frac,
            share_nonforest,
        ),
        "pasture_forest": (
            lef_past_forest,
            forest_frac,
            share_forest,
        ),
        "pasture_nonforest": (
            lef_past_nonforest,
            nonforest_frac,
            share_nonforest,
        ),
        "spared_cropland": (
            lef_spared,
            cropland_frac,
            unit_share,
        ),
        "spared_grassland": (
            lef_spared,
            pasture_frac,
            unit_share,
        ),
    }
    water_options = {
        "cropland_forest": ("r", "i"),
        "cropland_nonforest": ("r", "i"),
        "pasture_forest": ("r",),
        "pasture_nonforest": ("r",),
        "spared_cropland": ("r", "i"),
        "spared_grassland": ("r",),
    }

    frames: list[pd.DataFrame] = []
    region_ids = np.repeat(np.arange(len(mapping.regions)), mapping.n_classes)
    class_ids = np.tile(np.arange(mapping.n_classes), len(mapping.regions))
    group_regions = mapping.regions[region_ids]
    for use, (lef_arr, lc_weight, conv_share) in weighted_uses.items():
        lef_stats = _weighted_mean_by_group(lef_arr, lc_weight, mapping)
        share_stats = _weighted_mean_by_group(conv_share, natural_frac, mapping)
        for cls in range(mapping.n_classes):
            class_mask = class_ids == cls
            merged = pd.DataFrame(
                {
                    "region": group_regions[class_mask],
                    "LEF_tCO2_per_ha_yr": lef_stats[class_mask],
                    "conversion_share": share_stats[class_mask],
                }
            )
            merged["resource_class"] = cls
            merged["use"] = use
            merged = merged.dropna(subset=["LEF_tCO2_per_ha_yr"])
            merged = merged[np.isfinite(merged["LEF_tCO2_per_ha_yr"])]
            if merged.empty:
                continue

            # Fill NaN conversion_share with 0 (can happen with zero nonag weight)
            merged["conversion_share"] = (
                merged["conversion_share"].fillna(0.0).clip(0.0, 1.0)
            )

            # Expand water supply options for this use type
            for water in water_options[use]:
                frame = merged[
                    [
                        "region",
                        "resource_class",
                        "use",
                        "LEF_tCO2_per_ha_yr",
                        "conversion_share",
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
                "conversion_share",
            ]
        )
    coeffs_df.sort_values(["region", "resource_class", "water", "use"], inplace=True)
    coeffs_df.to_csv(coeffs_out, index=False)


if __name__ == "__main__":
    main()
