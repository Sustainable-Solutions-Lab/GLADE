"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

from typing import NamedTuple

import numpy as np


class CellMapping(NamedTuple):
    """Exact region/class coverage for cells on the common GAEZ grid."""

    cell_ids: np.ndarray
    coverage: np.ndarray
    group_ids: np.ndarray
    regions: np.ndarray
    n_classes: int
    shape: tuple[int, int]
    transform: tuple[float, ...]
    crs_wkt: str

    @property
    def n_groups(self) -> int:
        return len(self.regions) * self.n_classes


def load_cell_mapping(path: str) -> CellMapping:
    """Load the config-specific region/class cell mapping from an NPZ file."""
    with np.load(path, allow_pickle=False) as data:
        return CellMapping(
            cell_ids=data["cell_ids"],
            coverage=data["coverage"],
            group_ids=data["group_ids"],
            regions=data["regions"],
            n_classes=int(data["n_classes"]),
            shape=(int(data["height"]), int(data["width"])),
            transform=tuple(data["transform"]),
            crs_wkt=str(data["crs_wkt"]),
        )


def validate_raster_grid(values: np.ndarray, source, mapping: CellMapping) -> None:
    """Fail if a raster does not use the grid represented by ``mapping``."""
    if values.shape != mapping.shape:
        raise ValueError(
            f"Raster shape {values.shape} does not match cell mapping "
            f"shape {mapping.shape}"
        )
    transform = source.transform.to_gdal()
    if not np.allclose(transform, mapping.transform, rtol=0.0, atol=1e-12):
        raise ValueError(
            f"Raster transform {transform} does not match cell mapping "
            f"transform {mapping.transform}"
        )
    if source.crs is None:
        raise ValueError("Raster CRS does not match cell mapping CRS")
    actual_crs_wkt = source.crs.to_wkt()
    if actual_crs_wkt != mapping.crs_wkt:
        # Avoid importing pyproj when equivalent WKT strings already match.
        from pyproj import CRS

        if CRS.from_wkt(actual_crs_wkt) != CRS.from_wkt(mapping.crs_wkt):
            raise ValueError("Raster CRS does not match cell mapping CRS")


def _mapped_values(values: np.ndarray, mapping: CellMapping) -> np.ndarray:
    if values.shape != mapping.shape:
        raise ValueError(
            f"Raster shape {values.shape} does not match cell mapping "
            f"shape {mapping.shape}"
        )
    return values.ravel()[mapping.cell_ids]


def weighted_mean_by_group(values: np.ndarray, mapping: CellMapping) -> np.ndarray:
    """Return coverage-weighted means for every region/resource-class group."""
    mapped = _mapped_values(values, mapping)
    valid = ~np.isnan(mapped)
    numerator = np.bincount(
        mapping.group_ids[valid],
        weights=mapped[valid] * mapping.coverage[valid],
        minlength=mapping.n_groups,
    )
    denominator = np.bincount(
        mapping.group_ids[valid],
        weights=mapping.coverage[valid],
        minlength=mapping.n_groups,
    )
    return np.divide(
        numerator,
        denominator,
        out=np.full(mapping.n_groups, np.nan),
        where=denominator != 0,
    )


def weighted_sum_by_group(values: np.ndarray, mapping: CellMapping) -> np.ndarray:
    """Return coverage-weighted sums for every region/resource-class group."""
    mapped = _mapped_values(values, mapping)
    valid = ~np.isnan(mapped)
    return np.bincount(
        mapping.group_ids[valid],
        weights=mapped[valid] * mapping.coverage[valid],
        minlength=mapping.n_groups,
    )
