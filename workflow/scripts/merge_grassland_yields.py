# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge LUIcube and ISIMIP grassland yields.

Prefers LUIcube yields where available (finite yield > 0); falls back to
ISIMIP for gaps.  The output ``yield`` is a per-managed-hectare yield and
``grazing_intensity`` converts physical to managed area; the consumer
(``build_model/grassland.py``) forms the effective per-physical-hectare
efficiency as ``grazing_intensity * yield``. Both branches therefore obey
the same ``efficiency = grazing_intensity * yield`` contract:

- **LUIcube rows**: ``yield`` is per managed hectare
  (hanpp_harv / managed_area / C_FRACTION); ``grazing_intensity`` is the
  NPP-weighted harvest fraction.
- **ISIMIP rows**: ``yield`` is the raw ISIMIP managed-grass yield and
  ``grazing_intensity = isimip_utilization_rate`` is the GI proxy. The
  utilization rate is applied exactly once, via that single multiply in
  the consumer -- do NOT also pre-multiply ``yield`` here.

Output columns: yield, suitable_area, grazing_intensity
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

    # Determine where LUIcube observations are available.
    luicube_valid = luicube["yield"].apply(np.isfinite) & (luicube["yield"] > 0)

    # Start from ISIMIP as the base (covers all region/class combinations).
    # ISIMIP regions use isimip_utilization_rate as their grazing-intensity
    # proxy; the raw managed-grass yield is left untouched so the haircut is
    # applied exactly once in grassland.py via efficiency = GI * yield.
    merged = isimip[["yield", "suitable_area"]].copy()
    merged["grazing_intensity"] = isimip_utilization_rate

    # Overwrite with LUIcube where valid (yields are already per managed hectare)
    valid_idx = luicube_valid[luicube_valid].index.intersection(merged.index)
    merged.loc[valid_idx, "yield"] = luicube.loc[valid_idx, "yield"]
    merged.loc[valid_idx, "suitable_area"] = luicube.loc[valid_idx, "suitable_area"]
    merged.loc[valid_idx, "grazing_intensity"] = luicube.loc[
        valid_idx, "grazing_intensity"
    ]

    # Also add LUIcube-only rows not present in ISIMIP
    luicube_only = luicube_valid[luicube_valid].index.difference(merged.index)
    if not luicube_only.empty:
        extra = luicube.loc[
            luicube_only, ["yield", "suitable_area", "grazing_intensity"]
        ].copy()
        merged = pd.concat([merged, extra]).sort_index()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path)
