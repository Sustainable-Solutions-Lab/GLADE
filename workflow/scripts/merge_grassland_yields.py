# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge LUIcube and ISIMIP grassland yields.

Prefers LUIcube yields where available (finite yield > 0); falls back to
ISIMIP for gaps.  Applies utilization corrections so the output ``yield``
column is effective feed yield ready for direct use:

- **LUIcube rows**: ``yield = raw_yield * grazing_intensity``
- **ISIMIP rows**: ``yield = raw_yield * isimip_utilization_rate``

Output columns: yield, suitable_area
"""

from pathlib import Path

import numpy as np
import pandas as pd

if __name__ == "__main__":
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    isimip_path: str = snakemake.input.isimip  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    isimip_utilization_rate: float = float(snakemake.params.isimip_utilization_rate)  # type: ignore[name-defined]

    idx_cols = ["region", "resource_class"]

    luicube = pd.read_csv(luicube_path, comment="#").set_index(idx_cols).sort_index()
    isimip = pd.read_csv(isimip_path, comment="#").set_index(idx_cols).sort_index()

    # Determine which (region, resource_class) pairs have valid LUIcube yields
    luicube_valid = luicube["yield"].apply(np.isfinite) & (luicube["yield"] > 0)

    # Start from ISIMIP as the base (covers all region/class combinations).
    # Apply isimip_utilization_rate to convert raw ISIMIP yield to effective feed yield.
    merged = isimip[["yield", "suitable_area"]].copy()
    merged["yield"] = merged["yield"] * isimip_utilization_rate

    # Overwrite with LUIcube where valid, applying grazing_intensity
    valid_idx = luicube_valid[luicube_valid].index.intersection(merged.index)
    merged.loc[valid_idx, "yield"] = (
        luicube.loc[valid_idx, "yield"] * luicube.loc[valid_idx, "grazing_intensity"]
    )
    merged.loc[valid_idx, "suitable_area"] = luicube.loc[valid_idx, "suitable_area"]

    # Also add LUIcube-only rows not present in ISIMIP
    luicube_only = luicube_valid[luicube_valid].index.difference(merged.index)
    if not luicube_only.empty:
        extra = luicube.loc[luicube_only, ["yield", "suitable_area"]].copy()
        extra["yield"] = (
            luicube.loc[luicube_only, "yield"]
            * luicube.loc[luicube_only, "grazing_intensity"]
        )
        merged = pd.concat([merged, extra]).sort_index()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path)
