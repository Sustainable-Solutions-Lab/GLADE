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

    @property
    def n_groups(self) -> int:
        return len(self.regions) * self.n_classes


def load_cell_mapping(path: str) -> CellMapping:
    """Load the config-specific region/class cell mapping from an NPZ file."""
    with np.load(path, allow_pickle=False) as data:
        return CellMapping(
            cell_ids=data["cell_ids"],
            coverage=data["coverage"].astype(np.float64),
            group_ids=data["group_ids"],
            regions=data["regions"],
            n_classes=int(data["n_classes"]),
            shape=(int(data["height"]), int(data["width"])),
        )


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
