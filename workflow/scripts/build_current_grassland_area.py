"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr


def _transform_from_attrs(ds: xr.Dataset) -> Affine:
    try:
        return Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:  # pragma: no cover - sanity guard
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc


if __name__ == "__main__":
    classes_path: str = snakemake.input.classes  # type: ignore[name-defined]
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    classes_ds = xr.load_dataset(classes_path)
    region_id = classes_ds["region_id"].astype(np.int32).values
    resource_class = classes_ds["resource_class"].astype(np.int16).values
    transform = _transform_from_attrs(classes_ds)
    height, width = region_id.shape

    luicube_ds = xr.load_dataset(luicube_path)
    area_km2 = luicube_ds["area_km2"].astype(np.float32).values
    gi = luicube_ds["grazing_intensity"].astype(np.float32).values
    if area_km2.shape != region_id.shape:
        raise ValueError(
            "LUIcube grassland grid does not match the resource_classes grid"
        )
    luicube_transform = _transform_from_attrs(luicube_ds)
    if luicube_transform != transform:
        raise ValueError(
            "LUIcube grassland transform does not match resource_classes transform"
        )

    np.copyto(area_km2, 0.0, where=~np.isfinite(area_km2))
    np.clip(area_km2, 0.0, None, out=area_km2)
    np.copyto(gi, 0.0, where=~np.isfinite(gi))
    np.clip(gi, 0.0, 1.0, out=gi)

    # Physical grassland area in hectares (1 km^2 = 100 ha) and the
    # GI-weighted (managed) area. Output `area_ha` is the managed area:
    # the non-managed portion of LUIcube grassland (savanna, steppe) is
    # treated as natural land elsewhere in the pipeline (LUC
    # `natural_frac`), so including it here would double-count it as
    # both pasture supply and convertible natural land, and would
    # over-credit spared-pasture sequestration for land that was never
    # under management.
    physical_area = area_km2 * 100.0
    managed_area = physical_area * gi

    valid = (
        np.isfinite(managed_area)
        & (managed_area > 0.0)
        & np.isfinite(region_id)
        & np.isfinite(resource_class)
        & (region_id >= 0)
        & (resource_class >= 0)
    )
    if not np.any(valid):
        df = pd.DataFrame(
            columns=["region", "resource_class", "area_ha", "grazing_intensity"]
        )
    else:
        region_vals = region_id[valid].astype(np.int32, copy=False)
        class_vals = resource_class[valid].astype(np.int32, copy=False)
        managed_vals = managed_area[valid].astype(np.float64, copy=False)
        physical_vals = physical_area[valid].astype(np.float64, copy=False)

        regions_gdf = gpd.read_file(regions_path)
        if "region" not in regions_gdf.columns:
            raise ValueError("regions.geojson must contain a 'region' column")
        region_lookup = (
            regions_gdf.reset_index().set_index("index")["region"].astype(str).to_dict()
        )

        df = (
            pd.DataFrame(
                {
                    "region_id": region_vals,
                    "resource_class": class_vals,
                    "area_ha": managed_vals,
                    "physical_area_ha": physical_vals,
                }
            )
            .groupby(["region_id", "resource_class"], as_index=False)[
                ["area_ha", "physical_area_ha"]
            ]
            .sum()
        )
        df["grazing_intensity"] = np.where(
            df["physical_area_ha"] > 0,
            df["area_ha"] / df["physical_area_ha"],
            0.0,
        )
        df["region"] = df["region_id"].map(region_lookup)
        missing = df["region"].isna()
        if missing.any():
            missing_ids = sorted(df.loc[missing, "region_id"].unique().tolist())
            raise ValueError(
                "Region IDs in resource_classes.nc missing from regions.geojson: "
                + ", ".join(str(mid) for mid in missing_ids)
            )
        df = df[["region", "resource_class", "area_ha", "grazing_intensity"]]
        df = df.sort_values(["region", "resource_class"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
