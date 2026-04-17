"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import numpy as np
import xarray as xr

COARSEN_FACTOR = 30
ROW_CHUNK_SIZE = COARSEN_FACTOR * 30
NODATA_VALUE = 0

FOREST_CLASSES: tuple[int, ...] = (50, 60, 70, 80, 90, 160, 170, 100)
GRASSLAND_CLASSES: tuple[int, ...] = (110, 130, 140, 150)
CROPLAND_CLASSES: tuple[int, ...] = (10, 20, 30, 40)


def _target_coords(grid_path: str) -> tuple[xr.DataArray, xr.DataArray]:
    ds = xr.load_dataset(grid_path)
    try:
        y = ds["y"].astype(np.float32)
        x = ds["x"].astype(np.float32)
    except KeyError as exc:
        raise ValueError("resolution grid must expose 'y' and 'x' coordinates") from exc
    return y, x


def _build_lookup(classes: tuple[int, ...]) -> np.ndarray:
    lookup = np.zeros(256, dtype=np.uint8)
    for cls in classes:
        lookup[cls] = 1
    return lookup


def _aggregate_fractions(path: str) -> dict[str, np.ndarray]:
    land_cover_path = Path(path)
    forest_lookup = _build_lookup(FOREST_CLASSES)
    cropland_lookup = _build_lookup(CROPLAND_CLASSES)
    grassland_lookup = _build_lookup(GRASSLAND_CLASSES)

    # Open NetCDF file with xarray (avoids needing GDAL HDF5 plugin)
    ds = xr.open_dataset(land_cover_path)
    # Get the lccs_class variable and squeeze out the time dimension
    data = ds["lccs_class"].isel(time=0)

    height = data.sizes["lat"]
    width = data.sizes["lon"]

    if height % COARSEN_FACTOR != 0 or width % COARSEN_FACTOR != 0:
        raise ValueError("land cover grid size must be divisible by the coarsen factor")

    agg_height = height // COARSEN_FACTOR
    agg_width = width // COARSEN_FACTOR

    valid_counts = np.zeros((agg_height, agg_width), dtype=np.uint16)
    forest_counts = np.zeros((agg_height, agg_width), dtype=np.uint16)
    cropland_counts = np.zeros((agg_height, agg_width), dtype=np.uint16)
    grassland_counts = np.zeros((agg_height, agg_width), dtype=np.uint16)

    row_chunk = ROW_CHUNK_SIZE
    if row_chunk % COARSEN_FACTOR != 0:
        raise ValueError("row chunk size must be divisible by the coarsen factor")

    for row_start in range(0, height, row_chunk):
        rows = min(row_chunk, height - row_start)
        if rows % COARSEN_FACTOR != 0:
            raise ValueError("row chunk must align with coarsen factor")

        rows_coarse = rows // COARSEN_FACTOR
        row_slice = slice(
            row_start // COARSEN_FACTOR,
            row_start // COARSEN_FACTOR + rows_coarse,
        )

        # Read chunk using xarray's isel for efficient slicing
        block = data.isel(
            lat=slice(row_start, row_start + rows), lon=slice(0, width)
        ).values.astype(np.uint8)
        block = block.reshape(rows_coarse, COARSEN_FACTOR, agg_width, COARSEN_FACTOR)
        block = block.transpose(0, 2, 1, 3)

        valid_counts[row_slice] = np.count_nonzero(
            block != NODATA_VALUE,
            axis=(2, 3),
        ).astype(np.uint16)

        forest_counts[row_slice] = (
            forest_lookup[block]
            .reshape(
                rows_coarse,
                agg_width,
                -1,
            )
            .sum(axis=2, dtype=np.uint16)
        )

        cropland_counts[row_slice] = (
            cropland_lookup[block]
            .reshape(
                rows_coarse,
                agg_width,
                -1,
            )
            .sum(axis=2, dtype=np.uint16)
        )

        grassland_counts[row_slice] = (
            grassland_lookup[block]
            .reshape(
                rows_coarse,
                agg_width,
                -1,
            )
            .sum(axis=2, dtype=np.uint16)
        )

    valid_counts_f = valid_counts.astype(np.float32)
    nonzero_mask = valid_counts != 0

    fractions: dict[str, np.ndarray] = {}
    for name, counts in (
        ("forest_fraction", forest_counts),
        ("cropland_fraction", cropland_counts),
        ("grassland_fraction", grassland_counts),
    ):
        numerator = counts.astype(np.float32)
        fraction = np.empty_like(valid_counts_f)
        fraction.fill(np.nan)
        np.divide(
            numerator,
            valid_counts_f,
            out=fraction,
            where=nonzero_mask,
        )
        fractions[name] = fraction.clip(0.0, 1.0).astype(np.float32)

    ds.close()
    return fractions


def main() -> None:
    grid_path: str = snakemake.input.grid  # type: ignore[name-defined]
    land_cover_path: str = snakemake.input.land_cover  # type: ignore[name-defined]
    output_path: str = snakemake.output.fractions  # type: ignore[name-defined]

    y_coords, x_coords = _target_coords(grid_path)
    fractions = _aggregate_fractions(land_cover_path)

    ds_out = xr.Dataset(
        {
            "forest_fraction": (("y", "x"), fractions["forest_fraction"]),
            "cropland_fraction": (("y", "x"), fractions["cropland_fraction"]),
            "grassland_fraction": (("y", "x"), fractions["grassland_fraction"]),
        },
        coords={"y": y_coords, "x": x_coords},
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    encoding = {
        "forest_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "cropland_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "grassland_fraction": {"zlib": True, "complevel": 4, "dtype": "float32"},
    }
    ds_out.to_netcdf(output_path, encoding=encoding)


if __name__ == "__main__":
    main()
