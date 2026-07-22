# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pandas as pd

from workflow.scripts.process_huang_irrigation_water import (
    load_crop_growing_seasons,
)


def _write_crop_yield(path: Path, *, include_season: bool = True) -> None:
    rows = []
    values = {
        "suitable_area": [1.0, 3.0],
        "growing_season_start_day": [10.0, 30.0],
        "growing_season_length_days": [100.0, 200.0],
        "yield": [2.0, 4.0],
    }
    if not include_season:
        values = {"suitable_area": [1.0, 3.0], "yield": [2.0, 4.0]}
    for variable, variable_values in values.items():
        for resource_class, value in enumerate(variable_values):
            rows.append(
                {
                    "region": "r0",
                    "resource_class": resource_class,
                    "variable": variable,
                    "unit": "test",
                    "value": value,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_crop_growing_seasons_use_irrigated_files_when_available(tmp_path: Path):
    rainfed = tmp_path / "wheat_r.csv"
    irrigated = tmp_path / "wheat_i.csv"
    _write_crop_yield(rainfed)
    _write_crop_yield(irrigated)

    result = load_crop_growing_seasons([rainfed, irrigated])

    assert result.to_dict("records") == [
        {
            "region": "r0",
            "crop": "wheat",
            "water_supply": "i",
            "total_area": 4.0,
            "growing_season_start_day": 25.0,
            "growing_season_length_days": 175.0,
        }
    ]


def test_crop_growing_seasons_fall_back_to_rainfed_files(tmp_path: Path):
    rainfed = tmp_path / "wheat_r.csv"
    irrigated = tmp_path / "wheat_i.csv"
    _write_crop_yield(rainfed)
    _write_crop_yield(irrigated, include_season=False)

    result = load_crop_growing_seasons([rainfed, irrigated])

    assert result["water_supply"].tolist() == ["r"]
    assert result["growing_season_start_day"].tolist() == [25.0]
