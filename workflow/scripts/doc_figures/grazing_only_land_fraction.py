#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visualise grazing-only land availability at gridcell and regional scales."""

from affine import Affine
import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import xarray as xr

from workflow.scripts.doc_figures_config import (
    COLORMAPS,
    FIGURE_WIDTH,
    apply_doc_style,
    save_doc_figure,
)
from workflow.scripts.raster_utils import (
    calculate_all_cell_areas,
    raster_bounds,
    scale_fraction,
)


def _load_transform(ds: xr.Dataset) -> Affine:
    try:
        return Affine.from_gdal(*ds.attrs["transform"])
    except KeyError as exc:  # pragma: no cover
        raise ValueError(
            "resource_classes.nc missing affine transform metadata"
        ) from exc


def _build_dummy_raster(transform: Affine, width: int, height: int):
    class _Dummy:
        def __init__(self, transform: Affine, width: int, height: int) -> None:
            self.transform = transform
            self.shape = (height, width)
            xmin, ymin, xmax, ymax = raster_bounds(transform, width, height)
            self.bounds = (xmin, ymin, xmax, ymax)

    return _Dummy(transform, width, height)


def _max_suitability(raster_paths: list[str]) -> tuple[np.ndarray, Affine, str | None]:
    """Return the per-cell maximum suitability fraction across crops."""
    array: np.ndarray | None = None
    transform: Affine | None = None
    crs_wkt: str | None = None

    for path in raster_paths:
        with rasterio.open(path) as src:
            data = src.read(1, masked=False).astype(np.float32)
            scaled = scale_fraction(data)
            if array is None:
                array = scaled
                transform = src.transform
                crs_wkt = src.crs.to_wkt() if src.crs is not None else None
            else:
                if src.transform != transform:
                    raise ValueError(
                        "GAEZ suitability rasters must share the same transform"
                    )
                np.maximum(array, scaled, out=array)

    if array is None or transform is None:
        raise ValueError("No suitability rasters provided")
    return array, transform, crs_wkt


def _safe_fraction(data: np.ndarray) -> np.ndarray:
    out = np.nan_to_num(data.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(out, 0.0, 1.0, out=out)


def _auto_vmax(values: np.ndarray) -> float:
    # Determine colour scale cap that ignores extreme outliers
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.1
    perc = float(np.nanpercentile(finite, 99))
    return max(0.05, min(1.0, perc))


def _compute_region_fractions(
    region_grid: np.ndarray,
    grazing_area: np.ndarray,
    cell_area: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Aggregate grazing-only share per region."""
    if "region" not in regions_gdf.columns:
        raise ValueError("regions.geojson must contain a 'region' column")

    region_lookup = (
        regions_gdf.reset_index().set_index("index")["region"].astype(str).to_dict()
    )

    valid = (
        np.isfinite(region_grid)
        & (region_grid >= 0)
        & np.isfinite(grazing_area)
        & np.isfinite(cell_area)
        & (cell_area > 0.0)
    )
    if not np.any(valid):
        return pd.DataFrame(columns=["region", "fraction"])

    region_vals = region_grid[valid].astype(np.int32, copy=False)
    grazing_vals = grazing_area[valid].astype(np.float64, copy=False)
    area_vals = cell_area[valid].astype(np.float64, copy=False)

    df = (
        pd.DataFrame(
            {
                "region_id": region_vals,
                "grazing_only_ha": grazing_vals,
                "total_ha": area_vals,
            }
        )
        .groupby("region_id", as_index=False)
        .sum()
    )
    df["region"] = df["region_id"].map(region_lookup)
    df = df.dropna(subset=["region"]).copy()
    df["fraction"] = np.divide(
        df["grazing_only_ha"],
        df["total_ha"],
        out=np.zeros(len(df), dtype=np.float64),
        where=df["total_ha"] > 0.0,
    )
    return df[["region", "fraction"]]


def main(
    classes_path: str,
    lc_masks_path: str,
    regions_path: str,
    suitability_paths: list[str],
    svg_output_path: str,
    png_output_path: str,
) -> None:
    """Generate grazing-only land fraction figure."""
    apply_doc_style()

    classes_ds = xr.load_dataset(classes_path)
    region_grid = classes_ds["region_id"].astype(np.int32).values
    transform = _load_transform(classes_ds)
    height, width = region_grid.shape
    crs_wkt = classes_ds.attrs.get("crs_wkt")

    lc_ds = xr.load_dataset(lc_masks_path)
    grass_frac = _safe_fraction(lc_ds["grassland_fraction"].values)
    crop_frac = _safe_fraction(lc_ds["cropland_fraction"].values)
    forest_frac = _safe_fraction(lc_ds["forest_fraction"].values)
    if grass_frac.shape != region_grid.shape:
        raise ValueError(
            "Land-cover fractions grid does not match resource_classes grid"
        )

    suitability, suit_transform, suit_crs_wkt = _max_suitability(suitability_paths)
    suitability = np.clip(np.nan_to_num(suitability, nan=0.0), 0.0, 1.0)
    if suitability.shape != region_grid.shape or suit_transform != transform:
        raise ValueError("GAEZ suitability grid does not align with resource_classes")
    if suit_crs_wkt and crs_wkt and suit_crs_wkt != crs_wkt:
        raise ValueError(
            "GAEZ suitability grid CRS does not match resource_classes CRS"
        )

    convertible = np.clip(crop_frac + forest_frac, 0.0, 1.0)
    suitability_gap = np.clip(suitability - convertible, 0.0, 1.0)
    grass_candidate = np.clip(grass_frac - suitability_gap, 0.0, 1.0)
    max_unsuited = np.clip(1.0 - suitability, 0.0, 1.0)
    grazing_only_frac = np.minimum(grass_candidate, max_unsuited)

    dummy_raster = _build_dummy_raster(transform, width, height)
    cell_area = calculate_all_cell_areas(dummy_raster, repeat=True)
    grazing_area = grazing_only_frac * cell_area

    regions_gdf = gpd.read_file(regions_path)
    if regions_gdf.crs is None:
        regions_gdf = regions_gdf.set_crs(4326, allow_override=True)
    else:
        regions_gdf = regions_gdf.to_crs(4326)

    region_fraction_df = _compute_region_fractions(
        region_grid, grazing_area, cell_area, regions_gdf
    )
    regions_with_fraction = regions_gdf.merge(
        region_fraction_df, on="region", how="left"
    )

    grid_data = np.where(grazing_only_frac > 1e-3, grazing_only_frac, np.nan)
    vmax = max(
        _auto_vmax(grid_data),
        _auto_vmax(region_fraction_df["fraction"].to_numpy()),
    )
    cmap = COLORMAPS["forest"]
    norm = mcolors.Normalize(vmin=0.0, vmax=vmax)

    fig = plt.figure(figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.45))
    gs = fig.add_gridspec(
        1, 2, wspace=0.025, left=0.02, right=0.98, top=0.9, bottom=0.1
    )
    ax_cells = fig.add_subplot(gs[0, 0], projection=ccrs.EqualEarth())
    ax_regions = fig.add_subplot(gs[0, 1], projection=ccrs.EqualEarth())
    cax = fig.add_axes([0.3, 0.1, 0.4, 0.04])

    for ax in (ax_cells, ax_regions):
        ax.set_global()
        ax.coastlines(linewidth=0.3, color="#666666", alpha=0.6)

    extent = [
        transform.c,
        transform.c + width * transform.a,
        transform.f + height * transform.e,
        transform.f,
    ]

    ax_cells.imshow(
        grid_data,
        cmap=cmap,
        norm=norm,
        extent=extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
    )
    ax_cells.add_geometries(
        regions_gdf.geometry,
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="#000000",
        linewidth=0.2,
        alpha=0.3,
    )
    ax_cells.set_title("Share of each gridcell reserved for grazing-only land")

    for _, row in regions_with_fraction.iterrows():
        frac = row.get("fraction")
        facecolor = "#f0f0f0" if pd.isna(frac) else cmap(norm(float(frac)))
        ax_regions.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=facecolor,
            edgecolor="#333333",
            linewidth=0.2,
            alpha=0.95,
        )
    ax_regions.set_title("Fraction of modeled land per region that is grazing-only")

    cbar = fig.colorbar(
        mappable=plt.cm.ScalarMappable(norm=norm, cmap=cmap),
        cax=cax,
        orientation="horizontal",
    )
    cbar.set_label("Fraction of land that is available only for grazing")

    save_doc_figure(fig, svg_output_path, format="svg")
    save_doc_figure(fig, png_output_path, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main(
        classes_path=snakemake.input.classes,  # type: ignore[name-defined]
        lc_masks_path=snakemake.input.lc_masks,  # type: ignore[name-defined]
        regions_path=snakemake.input.regions,  # type: ignore[name-defined]
        suitability_paths=list(snakemake.input.suitability),  # type: ignore[name-defined]
        svg_output_path=snakemake.output.svg,  # type: ignore[name-defined]
        png_output_path=snakemake.output.png,  # type: ignore[name-defined]
    )
