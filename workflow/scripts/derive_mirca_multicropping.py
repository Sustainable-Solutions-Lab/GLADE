"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Derive an observed multi-cropping baseline from MIRCA-OS.

For each water system and grid cell, MIRCA annual harvested area above the
maximum-monthly physical footprint is the extra-cycle magnitude. The fixed
combination catalog supplies plausible crop sequences. Candidate sequences are
filled at a common proportional rate subject to four simultaneous budgets:

* the extra-cycle magnitude;
* the physical cropped footprint;
* every constituent crop's observed harvested area; and
* each sequence's MIRCA support and GAEZ cycle-zone capacity.

The shared budgets prevent one observed crop hectare from being reused in
several overlapping sequences. Irrigated and rainfed systems are derived
independently. The resulting physical bundle areas are aggregated directly to
the active configuration's regions and resource classes.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from affine import Affine
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
import xarray as xr

from workflow.scripts.build_multi_cropping import (
    WETLAND_RICE_CROPS,
    ZONE_CAPABILITIES,
    region_coverage_entries,
)
from workflow.scripts.multi_cropping_combinations import load_catalog_combinations
from workflow.scripts.raster_utils import raster_bounds


@dataclass(frozen=True)
class GridSpec:
    """Spatial identity of a two-dimensional raster grid."""

    shape: tuple[int, int]
    transform: Affine
    crs: CRS


def _grid_from_raster(src, path: str) -> GridSpec:
    if src.crs is None:
        raise ValueError(f"Raster '{path}' has no CRS")
    return GridSpec(src.shape, src.transform, src.crs)


def _assert_grid(actual: GridSpec, expected: GridSpec, label: str) -> None:
    if actual.shape != expected.shape:
        raise ValueError(
            f"{label} shape {actual.shape} does not match reference {expected.shape}"
        )
    if actual.crs != expected.crs:
        raise ValueError(f"{label} CRS does not match the reference grid")
    # MIRCA-OS contains one transform rounded at seven decimal places. Accept
    # harmless metadata rounding below 1% of a pixel, but reject a real grid
    # shift, resolution difference, or rotation.
    pixel_tolerance = min(abs(expected.transform.a), abs(expected.transform.e)) * 0.01
    if not np.allclose(
        tuple(actual.transform),
        tuple(expected.transform),
        rtol=0.0,
        atol=pixel_tolerance,
    ):
        raise ValueError(
            f"{label} transform differs from the reference by more than 1% of a pixel"
        )


def load_tif(path: str, expected_grid: GridSpec | None = None) -> np.ndarray:
    """Load a GeoTIFF as float64 and validate its complete grid identity."""
    with rasterio.open(path) as src:
        grid = _grid_from_raster(src, path)
        if expected_grid is not None:
            _assert_grid(grid, expected_grid, path)
        arr = src.read(1).astype(np.float64)
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = 0.0
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    return arr


def _spatial_dims(da: xr.DataArray) -> tuple[str, str]:
    lat_dims = [dim for dim in da.dims if dim.lower() in {"lat", "latitude", "y"}]
    lon_dims = [dim for dim in da.dims if dim.lower() in {"lon", "longitude", "x"}]
    if len(lat_dims) != 1 or len(lon_dims) != 1:
        raise ValueError(
            f"Could not identify one latitude and longitude dimension in {da.dims}"
        )
    return lat_dims[0], lon_dims[0]


def _coordinate_centers(grid: GridSpec) -> tuple[np.ndarray, np.ndarray]:
    if not grid.crs.is_geographic:
        raise ValueError("MIRCA-OS grids must use a geographic CRS")
    height, width = grid.shape
    x = grid.transform.c + (np.arange(width) + 0.5) * grid.transform.a
    y = grid.transform.f + (np.arange(height) + 0.5) * grid.transform.e
    return y, x


def _align_spatial_dataarray(
    da: xr.DataArray, grid: GridSpec, label: str
) -> np.ndarray:
    """Align a lat/lon DataArray to ``grid`` or fail on any other mismatch."""
    lat_dim, lon_dim = _spatial_dims(da)
    other_dims = [dim for dim in da.dims if dim not in {lat_dim, lon_dim}]
    if other_dims:
        raise ValueError(f"{label} still has non-spatial dimensions: {other_dims}")
    da = da.transpose(lat_dim, lon_dim)
    if da.shape != grid.shape:
        raise ValueError(f"{label} shape {da.shape} does not match {grid.shape}")

    expected_lat, expected_lon = _coordinate_centers(grid)
    lat = np.asarray(da[lat_dim].values, dtype=float)
    lon = np.asarray(da[lon_dim].values, dtype=float)
    atol = max(abs(grid.transform.a), abs(grid.transform.e)) * 1e-5

    if np.allclose(lat[::-1], expected_lat, atol=atol, rtol=0.0):
        da = da.isel({lat_dim: slice(None, None, -1)})
        lat = lat[::-1]
    if np.allclose(lon[::-1], expected_lon, atol=atol, rtol=0.0):
        da = da.isel({lon_dim: slice(None, None, -1)})
        lon = lon[::-1]
    if not np.allclose(lat, expected_lat, atol=atol, rtol=0.0):
        raise ValueError(
            f"{label} latitude coordinates do not match the reference grid"
        )
    if not np.allclose(lon, expected_lon, atol=atol, rtol=0.0):
        raise ValueError(
            f"{label} longitude coordinates do not match the reference grid"
        )
    return np.asarray(da.values, dtype=np.float64)


def load_subcrop_maxmonth(path: str, grid: GridSpec) -> np.ndarray:
    """Collapse a MIRCA monthly grid to its maximum growing area per cell."""
    with xr.open_dataset(path) as ds:
        if "harvested_area" not in ds:
            raise ValueError(f"{path} is missing the 'harvested_area' variable")
        da = ds["harvested_area"]
        lat_dim, lon_dim = _spatial_dims(da)
        month_dims = [dim for dim in da.dims if dim not in {lat_dim, lon_dim}]
        if len(month_dims) != 1:
            raise ValueError(f"{path} must have exactly one monthly dimension")
        collapsed = da.max(month_dims[0], skipna=True)
        arr = _align_spatial_dataarray(collapsed, grid, path)
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    return arr


def zone_mask(zone_arr: np.ndarray, n_cycles: int, n_rice: int) -> np.ndarray:
    """Return cells whose GAEZ zone permits the requested cycle counts."""
    allowed = [
        code
        for code, cap in ZONE_CAPABILITIES.items()
        if cap.get("valid", False)
        and int(cap.get("max_cycles", 0)) >= n_cycles
        and int(cap.get("max_wetland_rice", 0)) >= n_rice
    ]
    return np.isin(zone_arr, allowed)


def candidate_capacity(
    crops: list[str],
    ws: str,
    crop_area: dict[tuple[str, str], np.ndarray],
    zone_arr: np.ndarray,
    rice_support: dict[str, np.ndarray],
) -> np.ndarray:
    """Return the MIRCA-supported physical area cap for one crop sequence."""
    n_cycles = len(crops)
    n_rice = sum(crop in WETLAND_RICE_CROPS for crop in crops)
    zmask = zone_mask(zone_arr, n_cycles, n_rice)

    if n_rice == n_cycles and n_cycles >= 2:
        rice2 = rice_support[ws]
        rice3 = rice_support[f"{ws}3"]
        if n_cycles == 2:
            area = np.clip(rice2 - rice3, 0.0, None)
        elif n_cycles == 3:
            area = rice3.copy()
        else:
            raise ValueError(f"Unsupported repeated-rice cycle count: {n_cycles}")
        area[~zmask] = 0.0
        return area

    area = crop_area[(crops[0], ws)].copy()
    observed = area > 0
    for crop in crops[1:]:
        support = crop_area[(crop, ws)]
        np.minimum(area, support, out=area)
        observed &= support > 0
    area[~(zmask & observed)] = 0.0
    return area


def _bounded_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.divide(
            numerator,
            denominator,
            out=np.ones_like(numerator, dtype=np.float64),
            where=denominator > 0,
        )
    return np.clip(np.nan_to_num(ratio, nan=0.0, posinf=1.0), 0.0, 1.0)


def allocate(
    extra_cycle_area: np.ndarray,
    footprint_area: np.ndarray,
    capacities: list[np.ndarray],
    crop_sequences: list[list[str]],
    crop_support: dict[str, np.ndarray],
) -> tuple[list[np.ndarray], np.ndarray]:
    """Fill candidate capacities proportionally under shared cell budgets.

    A common fill ratio is used for all candidates in a cell. This is the
    max-min fair proportional attribution: no overlapping rotation is favored,
    and all magnitude that cannot be assigned without exceeding a crop or field
    budget remains residual.
    """
    if len(capacities) != len(crop_sequences):
        raise ValueError("capacities and crop_sequences must have equal length")
    if not capacities:
        return [], extra_cycle_area.copy()

    total_physical = np.zeros_like(extra_cycle_area, dtype=np.float64)
    total_extra = np.zeros_like(extra_cycle_area, dtype=np.float64)
    required_by_crop = {
        crop: np.zeros_like(extra_cycle_area, dtype=np.float64)
        for crops in crop_sequences
        for crop in crops
    }
    for capacity, crops in zip(capacities, crop_sequences, strict=True):
        total_physical += capacity
        total_extra += (len(crops) - 1) * capacity
        for crop, multiplicity in Counter(crops).items():
            required_by_crop[crop] += multiplicity * capacity

    scale = np.ones_like(extra_cycle_area, dtype=np.float64)
    np.minimum(scale, _bounded_ratio(extra_cycle_area, total_extra), out=scale)
    np.minimum(scale, _bounded_ratio(footprint_area, total_physical), out=scale)
    for crop, required in required_by_crop.items():
        np.minimum(scale, _bounded_ratio(crop_support[crop], required), out=scale)

    areas: list[np.ndarray] = []
    allocated_extra = np.zeros_like(extra_cycle_area, dtype=np.float64)
    for capacity, crops in zip(capacities, crop_sequences, strict=True):
        area = capacity * scale
        areas.append(area)
        allocated_extra += (len(crops) - 1) * area
    residual = np.clip(extra_cycle_area - allocated_extra, 0.0, None)
    return areas, residual


def run_derivation(
    annual_harvested: dict[tuple[str, str], np.ndarray],
    footprint: dict[str, np.ndarray],
    crop_area: dict[tuple[str, str], np.ndarray],
    zone: dict[str, np.ndarray],
    rice_support: dict[str, np.ndarray],
    combos: list[dict],
    harvested_totals: dict[str, np.ndarray] | None = None,
) -> tuple[dict[tuple[str, str], np.ndarray], np.ndarray, pd.DataFrame]:
    """Run independent rainfed and irrigated baseline attribution."""
    if harvested_totals is None:
        harvested_totals = {
            mws: np.sum(
                [arr for (crop, ws), arr in annual_harvested.items() if ws == mws],
                axis=0,
            )
            for mws in ("ir", "rf")
        }

    area_rasters: dict[tuple[str, str], np.ndarray] = {}
    residual_total = np.zeros_like(next(iter(footprint.values())), dtype=np.float64)
    records: list[dict] = []
    ws_map = {"i": "ir", "r": "rf"}

    for ws, mws in ws_map.items():
        ws_combos = [combo for combo in combos if combo["water_supply"] == ws]
        extra = np.clip(harvested_totals[mws] - footprint[mws], 0.0, None)
        capacities = [
            candidate_capacity(combo["crops"], ws, crop_area, zone[ws], rice_support)
            for combo in ws_combos
        ]
        support = {
            crop: crop_area[(crop, ws)]
            for combo in ws_combos
            for crop in combo["crops"]
        }
        areas, residual = allocate(
            extra,
            footprint[mws],
            capacities,
            [combo["crops"] for combo in ws_combos],
            support,
        )
        residual_total += residual

        for combo, area in zip(ws_combos, areas, strict=True):
            key = (combo["name"], ws)
            area_rasters[key] = area
            n_cycles = len(combo["crops"])
            records.append(
                {
                    "combination": combo["name"],
                    "water_supply": ws,
                    "cycles": n_cycles,
                    "physical_area_mha": float(area.sum()) / 1e6,
                    "extra_cycle_area_mha": float((n_cycles - 1) * area.sum()) / 1e6,
                    "system_extra_cycle_area_mha": float(extra.sum()) / 1e6,
                    "system_residual_area_mha": float(residual.sum()) / 1e6,
                }
            )

    return area_rasters, residual_total, pd.DataFrame.from_records(records)


def build_crop_area(
    annual_harvested: dict[tuple[str, str], np.ndarray],
    glade_to_mirca: dict[str, str],
) -> dict[tuple[str, str], np.ndarray]:
    """Map MIRCA crop/system arrays to GLADE crop/water codes."""
    return {
        (glade_crop, gws): annual_harvested[(mirca_crop, mws)]
        for glade_crop, mirca_crop in glade_to_mirca.items()
        for gws, mws in {"i": "ir", "r": "rf"}.items()
    }


def _annual_key(mirca_crop: str, mws: str) -> str:
    return f"annual_{mirca_crop.replace(' ', '_')}_{mws}"


def _load_inputs(
    inp: dict,
    mapping: pd.DataFrame,
    catalog: dict[str, dict],
    grid: GridSpec,
) -> tuple[
    dict[tuple[str, str], np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
]:
    """Load totals plus only the crop arrays used by the fixed catalog."""
    needed_glade = {crop for entry in catalog.values() for crop in entry["crops"]}
    mirca_to_glade = {
        row.mirca_crop: row.glade_crop
        for row in mapping.itertuples()
        if row.glade_crop in needed_glade
    }
    annual: dict[tuple[str, str], np.ndarray] = {}
    totals = {mws: np.zeros(grid.shape, dtype=np.float64) for mws in ("ir", "rf")}
    for row in mapping.itertuples():
        for mws in ("ir", "rf"):
            arr = load_tif(inp[_annual_key(row.mirca_crop, mws)], grid)
            totals[mws] += arr
            if row.mirca_crop in mirca_to_glade:
                annual[(row.mirca_crop, mws)] = arr

    footprint = {mws: load_tif(inp[f"footprint_{mws}"], grid) for mws in ("ir", "rf")}
    zone = {ws: load_tif(inp[f"zone_{ws}"], grid) for ws in ("i", "r")}
    rice_support: dict[str, np.ndarray] = {}
    for gws, mws in {"i": "ir", "r": "rf"}.items():
        rice_support[gws] = load_subcrop_maxmonth(inp[f"rice2_{mws}"], grid)
        rice_support[f"{gws}3"] = load_subcrop_maxmonth(inp[f"rice3_{mws}"], grid)
    return annual, totals, footprint, zone, rice_support


BASELINE_COLUMNS = [
    "combination",
    "region",
    "resource_class",
    "water_supply",
    "baseline_area_ha",
]


def _load_resource_classes(path: str, grid: GridSpec) -> np.ndarray:
    with xr.open_dataset(path) as ds:
        if "resource_class" not in ds:
            raise ValueError("resource_classes.nc is missing 'resource_class'")
        try:
            transform = Affine.from_gdal(*ds.attrs["transform"])
            crs = CRS.from_wkt(ds.attrs["crs_wkt"])
        except KeyError as exc:
            raise ValueError(
                "resource_classes.nc is missing transform or CRS metadata"
            ) from exc
        labels = ds["resource_class"].values.astype(np.int16)
    _assert_grid(GridSpec(labels.shape, transform, crs), grid, path)
    return labels


def aggregate_baseline(
    area_rasters: dict[tuple[str, str], np.ndarray],
    class_labels: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
    grid: GridSpec,
) -> pd.DataFrame:
    """Aggregate physical bundle areas to region and resource class."""
    regions = regions_gdf
    if regions.crs is None:
        raise ValueError("regions.geojson is missing CRS information")
    if regions.crs != grid.crs:
        regions = regions.to_crs(grid.crs)
    regions_for_extract = regions.reset_index()
    if "region" not in regions_for_extract:
        raise ValueError("regions.geojson must provide a 'region' index or column")

    height, width = grid.shape
    xmin, ymin, xmax, ymax = raster_bounds(grid.transform, width, height)
    region_names, rows, cells, fractions = region_coverage_entries(
        regions_for_extract,
        xmin,
        ymin,
        xmax,
        ymax,
        grid.crs.to_wkt(),
        grid.shape,
    )
    class_at_entry = class_labels.ravel()[cells]
    valid_classes = sorted(
        int(value)
        for value in np.unique(class_labels[np.isfinite(class_labels)])
        if int(value) >= 0
    )
    records: list[pd.DataFrame] = []
    for (name, ws), area in area_rasters.items():
        values = area.ravel()[cells] * fractions
        for resource_class in valid_classes:
            selected = class_at_entry == resource_class
            sums = np.bincount(
                rows[selected], weights=values[selected], minlength=len(region_names)
            )
            positive = sums > 0
            if positive.any():
                records.append(
                    pd.DataFrame(
                        {
                            "combination": name,
                            "region": region_names[positive],
                            "resource_class": resource_class,
                            "water_supply": ws,
                            "baseline_area_ha": sums[positive],
                        }
                    )
                )
    if not records:
        return pd.DataFrame(columns=BASELINE_COLUMNS)
    return pd.concat(records, ignore_index=True)[BASELINE_COLUMNS].sort_values(
        ["combination", "water_supply", "region", "resource_class"],
        ignore_index=True,
    )


def main() -> None:
    inp = dict(snakemake.input.items())  # type: ignore[name-defined]
    params = snakemake.params  # type: ignore[name-defined]

    with rasterio.open(inp["footprint_ir"]) as src:
        grid = _grid_from_raster(src, inp["footprint_ir"])
        raster_profile = src.profile

    mapping = pd.read_csv(inp["concordance"], comment="#")
    mapping["glade_crop"] = mapping["glade_crop"].fillna("").astype(str).str.strip()
    mapping["mirca_crop"] = mapping["mirca_crop"].astype(str).str.strip()
    catalog = load_catalog_combinations(inp["catalog"])
    needed = {crop for entry in catalog.values() for crop in entry["crops"]}
    glade_to_mirca = {
        row.glade_crop: row.mirca_crop
        for row in mapping.itertuples()
        if row.glade_crop in needed
    }
    missing = sorted(needed - set(glade_to_mirca))
    if missing:
        raise ValueError(f"MIRCA catalog crops missing from concordance: {missing}")

    annual, totals, footprint, zone, rice_support = _load_inputs(
        inp, mapping, catalog, grid
    )
    crop_area = build_crop_area(annual, glade_to_mirca)
    combos = [
        {"name": name, "crops": entry["crops"], "water_supply": ws}
        for name, entry in catalog.items()
        for ws in entry["water_supplies"]
    ]
    areas, residual, stats = run_derivation(
        annual,
        footprint,
        crop_area,
        zone,
        rice_support,
        combos,
        harvested_totals=totals,
    )

    classes = _load_resource_classes(inp["classes"], grid)
    regions = gpd.read_file(inp["regions"])
    baseline = aggregate_baseline(areas, classes, regions, grid)

    baseline_path = Path(snakemake.output.baseline)  # type: ignore[name-defined]
    residual_path = Path(snakemake.output.residual)  # type: ignore[name-defined]
    stats_path = Path(snakemake.output.stats)  # type: ignore[name-defined]
    for path in (baseline_path, residual_path, stats_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    baseline.to_csv(baseline_path, index=False)
    stats.insert(0, "source_year", int(params.source_year))
    stats.to_csv(stats_path, index=False)

    profile = {
        **raster_profile,
        "count": 1,
        "dtype": "float32",
        "nodata": 0.0,
        "compress": "deflate",
    }
    with rasterio.open(residual_path, "w", **profile) as dst:
        dst.write(residual.astype(np.float32), 1)

    total_extra = (
        sum(
            float(np.clip(totals[mws] - footprint[mws], 0.0, None).sum())
            for mws in ("ir", "rf")
        )
        / 1e6
    )
    total_residual = float(residual.sum()) / 1e6
    share = 0.0 if total_extra == 0 else total_residual / total_extra * 100
    print(
        f"MIRCA-OS {int(params.source_year)} multi-cropping baseline: "
        f"extra={total_extra:.1f} Mha, attributed={total_extra - total_residual:.1f} "
        f"Mha, residual={total_residual:.1f} Mha ({share:.0f}%)"
    )


if __name__ == "__main__":
    main()
