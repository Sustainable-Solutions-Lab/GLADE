#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Aggregate per-crop country yield fractions into a single per-country average.

Input (Snakemake):
 - Multiple CSVs: processing/{name}/yield_gap_by_country_{crop}_{water_supply}.csv,
   each with columns: country,fraction_achieved

Output:
 - CSV: processing/{name}/yield_gap_by_country_all_crops_{water_supply}.csv
   Columns: country, fraction_achieved_mean
"""

from pathlib import Path

import pandas as pd


def aggregate(inputs: list[str], output: str) -> None:
    frames: list[pd.DataFrame] = []
    crop_names: list[str] = []
    # Try to infer crop name from filename suffix before .csv
    for path in inputs:
        df = pd.read_csv(path)
        # Expect 'country' and 'fraction_achieved'
        # Derive column name for this crop
        stem = Path(path).stem  # yield_gap_by_country_{crop}_{water_supply}
        # Strip the water_supply suffix (e.g., "_r" or "_i")
        crop = stem.replace("yield_gap_by_country_", "").rsplit("_", 1)[0]
        crop_names.append(crop)
        frames.append(df.rename(columns={"fraction_achieved": crop}))

    if not frames:
        out = pd.DataFrame(columns=["country", "fraction_achieved_mean"])
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(output, index=False)
        return

    merged = frames[0]
    for df in frames[1:]:
        merged = merged.merge(df, on="country", how="outer")

    merged = merged.sort_values("country").reset_index(drop=True)
    # Row-wise mean ignoring NaNs
    merged["fraction_achieved_mean"] = merged[crop_names].mean(axis=1, skipna=True)
    out = merged[["country", "fraction_achieved_mean"]]

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)


if __name__ == "__main__":
    aggregate(list(snakemake.input), snakemake.output.csv)
