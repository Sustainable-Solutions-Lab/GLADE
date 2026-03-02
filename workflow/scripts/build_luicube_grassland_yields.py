# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute grassland yields, suitable area and grazing intensity from LUIcube.

Reads the resampled LUIcube grassland NetCDF and aggregates per
region/resource_class using exactextract zonal statistics.

Output CSV columns:
    region, resource_class, yield, suitable_area, grazing_intensity

yield is in tDM per managed hectare, computed as
sum(hanpp_harv) / sum(managed_area) / C_FRACTION, where managed_area =
area_ha * grazing_intensity.  suitable_area is the physical grassland area
(ha).  grazing_intensity is the NPP-weighted mean of HANPP_harv / NPP_act,
clipped to [0, 1].
"""

from pathlib import Path

from affine import Affine
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from workflow.scripts.raster_utils import raster_bounds

# Carbon content of dry matter (tC per tDM)
C_FRACTION = 0.45


if __name__ == "__main__":
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    # Load resource classes grid
    ds_classes = xr.load_dataset(classes_path)
    class_labels = ds_classes["resource_class"].values.astype(np.int16)
    region_id = ds_classes["region_id"].values.astype(np.int32)
    transform = Affine.from_gdal(*ds_classes.attrs["transform"])
    height, width = class_labels.shape
    crs_wkt = ds_classes.attrs.get("crs_wkt")
    xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)

    # Load LUIcube grassland data
    ds = xr.load_dataset(luicube_path)
    area_km2 = ds["area_km2"].values.astype(np.float64)
    npp_act = ds["npp_act_tc_yr"].values.astype(np.float64)
    hanpp_harv = ds["hanpp_harv_tc_yr"].values.astype(np.float64)

    if area_km2.shape != (height, width):
        raise ValueError("LUIcube grid does not match resource_classes grid")

    # Convert area to hectares: 1 km² = 100 ha
    area_ha = area_km2 * 100.0

    # Compute per-cell grazing intensity = HANPP_harv / NPP_act, clipped [0, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        gi_cell = np.where(npp_act > 0, hanpp_harv / npp_act, 0.0)
    gi_cell = np.clip(gi_cell, 0.0, 1.0)

    # Managed pasture area: total grassland scaled by grazing intensity
    managed_area_ha = area_ha * gi_cell

    # Load regions
    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs and regions_gdf.crs.to_epsg() != 4326:
        regions_gdf = regions_gdf.to_crs("EPSG:4326")
    regions_for_extract = regions_gdf.reset_index()

    valid_classes = sorted(
        int(c) for c in np.unique(class_labels) if np.isfinite(c) and c >= 0
    )

    data_frames: list[pd.DataFrame] = []
    for cls in valid_classes:
        mask = class_labels == cls
        if not np.any(mask):
            continue

        # Mask arrays to this resource class
        hanpp_masked = np.where(mask, hanpp_harv, np.nan)
        managed_area_masked = np.where(mask, managed_area_ha, np.nan)
        physical_area_masked = np.where(mask, area_ha, np.nan)
        # NPP_act-weighted grazing intensity (diagnostics): weight = npp_act
        npp_masked = np.where(mask, npp_act, np.nan)
        gi_weighted = np.where(mask, gi_cell * npp_act, np.nan)

        raster_kwargs = {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "nodata": np.nan,
            "srs_wkt": crs_wkt,
        }
        hanpp_src = NumPyRasterSource(hanpp_masked, **raster_kwargs)
        managed_area_src = NumPyRasterSource(managed_area_masked, **raster_kwargs)
        physical_area_src = NumPyRasterSource(physical_area_masked, **raster_kwargs)
        npp_src = NumPyRasterSource(npp_masked, **raster_kwargs)
        gi_w_src = NumPyRasterSource(gi_weighted, **raster_kwargs)

        hanpp_stats = exact_extract(
            hanpp_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        managed_area_stats = exact_extract(
            managed_area_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        physical_area_stats = exact_extract(
            physical_area_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        npp_stats = exact_extract(
            npp_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        gi_w_stats = exact_extract(
            gi_w_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )

        if hanpp_stats.empty or managed_area_stats.empty:
            continue

        merged = (
            hanpp_stats.rename(columns={"sum": "hanpp_sum"})
            .merge(
                managed_area_stats.rename(columns={"sum": "managed_area"}),
                on="region",
            )
            .merge(
                physical_area_stats.rename(columns={"sum": "suitable_area"}),
                on="region",
            )
            .merge(npp_stats.rename(columns={"sum": "npp_sum"}), on="region")
            .merge(gi_w_stats.rename(columns={"sum": "gi_weighted_sum"}), on="region")
        )

        # yield = sum(hanpp_harv) / sum(managed_area_ha) / C_FRACTION → tDM/ha managed
        with np.errstate(divide="ignore", invalid="ignore"):
            merged["yield"] = np.where(
                merged["managed_area"] > 0,
                merged["hanpp_sum"] / merged["managed_area"] / C_FRACTION,
                0.0,
            )
        # grazing_intensity = sum(gi * npp) / sum(npp) (diagnostic)
        with np.errstate(divide="ignore", invalid="ignore"):
            merged["grazing_intensity"] = np.where(
                merged["npp_sum"] > 0,
                merged["gi_weighted_sum"] / merged["npp_sum"],
                0.0,
            )
        merged["grazing_intensity"] = merged["grazing_intensity"].clip(0.0, 1.0)
        merged["resource_class"] = cls
        data_frames.append(
            merged[
                [
                    "region",
                    "resource_class",
                    "yield",
                    "suitable_area",
                    "grazing_intensity",
                ]
            ]
        )

    if data_frames:
        out_df = (
            pd.concat(data_frames, ignore_index=True)
            .set_index(["region", "resource_class"])
            .sort_index()
        )
    else:
        out_df = pd.DataFrame(
            columns=[
                "region",
                "resource_class",
                "yield",
                "suitable_area",
                "grazing_intensity",
            ]
        ).set_index(["region", "resource_class"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path)
