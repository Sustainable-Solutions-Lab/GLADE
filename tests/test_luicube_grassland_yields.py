# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import numpy as np
import pytest

from workflow.scripts.build_luicube_grassland_yields import (
    aggregate_grassland_yields,
)
from workflow.scripts.region_class_aggregation import CellMapping


def _mapping() -> CellMapping:
    return CellMapping(
        cell_ids=np.arange(4, dtype=np.int32),
        coverage=np.array([1.0, 0.5, 0.25, 1.0]),
        group_ids=np.array([0, 0, 1, 1], dtype=np.int32),
        regions=np.array(["region0"]),
        n_classes=2,
        shape=(2, 2),
        transform=(0.0, 1.0, 0.0, 2.0, 0.0, -1.0),
        crs_wkt="",
    )


def test_aggregate_grassland_yields_uses_exact_cell_coverage():
    result = aggregate_grassland_yields(
        area_km2=np.array([[1.0, 2.0], [3.0, 4.0]]),
        npp_act=np.array([[10.0, 20.0], [0.0, 40.0]]),
        hanpp_harv=np.array([[5.0, 10.0], [1.0, 80.0]]),
        mapping=_mapping(),
    )

    assert result.loc[("region0", 0), "yield"] == pytest.approx(10.0 / 100.0 / 0.45)
    assert result.loc[("region0", 0), "suitable_area"] == pytest.approx(200.0)
    assert result.loc[("region0", 0), "grazing_intensity"] == pytest.approx(0.5)
    assert result.loc[("region0", 1), "yield"] == pytest.approx(80.25 / 400.0 / 0.45)
    assert result.loc[("region0", 1), "suitable_area"] == pytest.approx(475.0)
    assert result.loc[("region0", 1), "grazing_intensity"] == pytest.approx(1.0)


def test_aggregate_grassland_yields_rejects_grid_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        aggregate_grassland_yields(
            area_km2=np.ones((1, 1)),
            npp_act=np.ones((1, 1)),
            hanpp_harv=np.ones((1, 1)),
            mapping=_mapping(),
        )
