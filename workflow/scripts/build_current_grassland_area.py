"""Aggregate LUIcube grassland to (region, resource_class).

Output schema: ``region, resource_class, area_ha, grazing_intensity``.

``area_ha`` is the **physical** grassland area (km^2 -> ha, summed across
every pixel that overlaps the (region, class) cell), NOT the GI-weighted
managed area. This is intentional and load-bearing:

* The LP's pasture supply pool downstream consumes this as
  ``observed_area``; restricting the pool to GI-weighted managed area
  collapses pasture flexibility and shifts dietary adjustments onto
  cropland deviations, which inflates the calibrated land L1 cost by
  roughly an order of magnitude and pushes the animal-feed L1 cost
  toward zero. See ``docs/land_use.rst``, section "Pasture supply vs
  LUC pasture fraction".
* ``grazing_intensity`` is exported separately as an area-weighted mean
  per aggregate. Downstream, ``build_model/grassland.py`` multiplies the
  per-managed-hectare yield by this GI so the effective per-Mha
  efficiency is ``GI * yield`` -- i.e. total feed capacity is
  ``sum(physical_area * GI * yield)``, matching the underlying managed
  forage productivity even though the LP can choose where within the
  physical pool to allocate that capacity.

This deliberately mismatches the LUC ``pasture_fraction`` (which is
GI-weighted in ``prepare_luc_inputs.py``). The trade-off is documented
in ``docs/land_use.rst``; do NOT "fix" this asymmetry by GI-weighting
``area_ha`` without rerunning the full calibration (``tools/calibrate``)
and confirming the stability L1 cost lands in a balanced regime rather
than spiking land friction.

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

    np.copyto(area_km2, 0.0, where=~np.isfinite(area_km2))
    np.clip(area_km2, 0.0, None, out=area_km2)
    np.copyto(gi, 0.0, where=~np.isfinite(gi))
    np.clip(gi, 0.0, 1.0, out=gi)

    # Physical pasture area in hectares (1 km² = 100 ha).
    grass_area = area_km2 * 100.0

    # Weighted GI contribution for computing area-weighted mean GI per aggregate.
    weighted_gi_area = area_km2 * gi * 100.0

    valid = (
        np.isfinite(grass_area)
        & (grass_area > 0.0)
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
        area_vals = grass_area[valid].astype(np.float64, copy=False)
        weighted_gi_vals = weighted_gi_area[valid].astype(np.float64, copy=False)

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
                    "area_ha": area_vals,
                    "weighted_gi_area": weighted_gi_vals,
                }
            )
            .groupby(["region_id", "resource_class"], as_index=False)[
                ["area_ha", "weighted_gi_area"]
            ]
            .sum()
        )
        df["grazing_intensity"] = np.where(
            df["area_ha"] > 0,
            df["weighted_gi_area"] / df["area_ha"],
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
