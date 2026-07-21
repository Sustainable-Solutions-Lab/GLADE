"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Stage 2 (aggregation): reduce the config-independent Stage-1 multi-cropping
baseline rasters to a per-region, per-resource-class, per-water-supply table for
the active config's regions.

Each Stage-1 raster (``baseline/{combination}_{ws}.tif`` under the
``derive_mirca_multicropping`` checkpoint's output directory)
holds the *physical link area* ``A`` (ha) -- the field area that runs the whole
sequence once. This aggregates ``A`` to ``(combination, region, resource_class,
water_supply)`` via ``exact_extract`` against ``resource_classes.nc`` +
``regions.geojson``, reusing the machinery in ``build_multi_cropping``. The result
anchors ``crop_production_multi`` links in production stability.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from workflow.scripts.build_multi_cropping import region_coverage_entries
from workflow.scripts.multi_cropping_combinations import effective_combinations
from workflow.scripts.raster_utils import raster_bounds, read_raster_float

BASELINE_COLUMNS = [
    "combination",
    "region",
    "resource_class",
    "water_supply",
    "baseline_area_ha",
]


def main() -> None:
    inp = dict(snakemake.input.items())  # type: ignore[name-defined]
    output = Path(snakemake.output[0])  # type: ignore[name-defined]

    classes_nc = inp.pop("classes")
    regions_path = inp.pop("regions")
    combinations = effective_combinations(
        snakemake.config,  # type: ignore[name-defined]
        inp.pop("combinations"),
    )

    # Remaining inputs are the per-(combination, ws) Stage-1 baseline rasters,
    # keyed "baseline_{combination}_{ws}".
    raster_paths = {
        key[len("baseline_") :]: path
        for key, path in inp.items()
        if key.startswith("baseline_")
    }

    ds = xr.load_dataset(classes_nc)
    if "resource_class" not in ds:
        raise ValueError("resource_classes.nc is missing 'resource_class' data")
    class_labels = ds["resource_class"].values.astype(np.int16)
    valid_classes = [
        int(cls)
        for cls in np.unique(class_labels[np.isfinite(class_labels)])
        if int(cls) >= 0
    ]

    regions_gdf = gpd.read_file(regions_path)
    regions_for_extract = regions_gdf.reset_index()

    # All Stage-1 rasters share one grid, so the region coverage fractions are
    # extracted once (on the first raster's grid) and every aggregation is a
    # gather + bincount against them; a raster on a different grid fails fast.
    coverage_grid = None
    records: list[pd.DataFrame] = []
    for combo_name, entry in combinations.items():
        if entry is None:
            continue
        for ws in entry["water_supplies"]:
            raster_key = f"{combo_name}_{ws}"
            path = raster_paths.get(raster_key)
            if path is None:
                raise KeyError(
                    f"Missing Stage-1 baseline raster for '{raster_key}'; the "
                    "derive_mirca_multicropping checkpoint output is stale."
                )
            area_arr, src = read_raster_float(path)
            try:
                if area_arr.shape != class_labels.shape:
                    raise ValueError(
                        f"Baseline raster '{raster_key}' shape {area_arr.shape} does "
                        f"not match resource class grid {class_labels.shape}"
                    )
                transform = src.transform
                crs = src.crs
                crs_wkt = crs.to_wkt() if crs else None
                height, width = area_arr.shape
                xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            finally:
                src.close()

            if coverage_grid is None:
                if regions_gdf.crs and crs and regions_gdf.crs != crs:
                    regions_for_extract = regions_gdf.to_crs(crs).reset_index()
                coverage_grid = (transform, crs_wkt)
                region_names, cov_rows, cov_cells, cov_fracs = region_coverage_entries(
                    regions_for_extract,
                    xmin,
                    ymin,
                    xmax,
                    ymax,
                    crs_wkt,
                    area_arr.shape,
                )
                n_regions = len(region_names)
                class_at_entry = class_labels.ravel()[cov_cells]
            elif coverage_grid != (transform, crs_wkt):
                raise ValueError(
                    f"Baseline raster '{raster_key}' is not on the shared "
                    "Stage-1 grid"
                )

            area_flat = np.where(np.isfinite(area_arr), area_arr, 0.0).ravel()
            area_entries = area_flat[cov_cells] * cov_fracs
            for cls in valid_classes:
                sel = class_at_entry == cls
                sums = np.bincount(
                    cov_rows[sel], weights=area_entries[sel], minlength=n_regions
                )
                pos = sums > 0
                if not pos.any():
                    continue
                stats = pd.DataFrame(
                    {
                        "combination": combo_name,
                        "region": region_names[pos],
                        "resource_class": cls,
                        "water_supply": ws,
                        "baseline_area_ha": sums[pos],
                    }
                )
                records.append(stats[BASELINE_COLUMNS])

    if records:
        result = pd.concat(records, ignore_index=True)
        result["resource_class"] = result["resource_class"].astype(int)
        result = result.sort_values(
            ["combination", "water_supply", "region", "resource_class"],
            ignore_index=True,
        )
    else:
        result = pd.DataFrame(columns=BASELINE_COLUMNS)

    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)


main()
