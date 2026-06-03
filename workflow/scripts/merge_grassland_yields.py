# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Merge LUIcube and ISIMIP grassland yields.

The output ``yield`` is a per-managed-hectare yield and ``grazing_intensity``
converts physical to managed area; the consumer (``build_model/grassland.py``)
forms the effective per-physical-hectare feed yield as
``grazing_intensity * yield``. Both branches obey that contract:

- **LUIcube rows**: ``yield`` is per managed hectare
  (hanpp_harv / managed_area / C_FRACTION); ``grazing_intensity`` is the
  NPP-weighted harvest fraction. The product is the *actual* harvested
  forage offtake per physical hectare.
- **ISIMIP rows**: ``yield`` is the raw ISIMIP/LPJmL managed-grass yield and
  ``grazing_intensity = isimip_utilization_rate`` is the utilization proxy.

We take, per (region, resource_class), whichever source gives the *larger*
effective yield (``grazing_intensity * yield``). The LUIcube HANPP-based
offtake reflects only biomass *currently* harvested, which collapses toward
zero over lightly/extensively grazed rangeland (e.g. the Tibetan plateau,
Inner Mongolian and Sahelian steppe), implausibly starving modelled grassland
feed there. The ISIMIP/LPJmL managed-grass potential times the utilization
rate provides a process-based, climate-differentiated floor that prevents
that collapse, while LUIcube still wins where intensively grazed land
genuinely harvests more than the potential x utilization estimate. The
downstream grassland-forage calibration then scales each country's yield to
the GLEAM-observed forage demand (surplus -> down; residual deficit ->
exogenous forage), so this floor only sets whether a country *can* supply its
observed grazed-forage offtake at all.

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

    # ISIMIP base (covers all region/class combinations). The raw managed-grass
    # yield is kept and the utilization rate enters as grazing_intensity, so the
    # haircut is applied exactly once downstream (efficiency = GI * yield).
    merged = isimip[["yield", "suitable_area"]].copy()
    merged["grazing_intensity"] = isimip_utilization_rate
    isimip_eff = isimip_utilization_rate * isimip["yield"]

    # Use LUIcube only where it is valid AND its effective offtake is at least
    # the ISIMIP potential x utilization floor (i.e. where intensive grazing
    # genuinely harvests more than the potential estimate). Where the LUIcube
    # HANPP signal collapses below the floor, keep the ISIMIP floor.
    luicube_eff = luicube["grazing_intensity"] * luicube["yield"]
    lui_wins = luicube_valid & (
        luicube_eff >= isimip_eff.reindex(luicube.index).fillna(-np.inf)
    )
    win_idx = lui_wins[lui_wins].index.intersection(merged.index)
    merged.loc[win_idx, "yield"] = luicube.loc[win_idx, "yield"]
    merged.loc[win_idx, "suitable_area"] = luicube.loc[win_idx, "suitable_area"]
    merged.loc[win_idx, "grazing_intensity"] = luicube.loc[win_idx, "grazing_intensity"]

    # Add LUIcube-only rows not present in ISIMIP (no floor available there).
    luicube_only = luicube_valid[luicube_valid].index.difference(merged.index)
    if not luicube_only.empty:
        extra = luicube.loc[
            luicube_only, ["yield", "suitable_area", "grazing_intensity"]
        ].copy()
        merged = pd.concat([merged, extra]).sort_index()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path)
