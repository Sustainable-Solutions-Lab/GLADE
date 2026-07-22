# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute grassland yields, suitable area and grazing intensity from LUIcube.

Reads the resampled LUIcube grassland NetCDF and aggregates per
region/resource_class using the shared exact cell-coverage mapping.

Output CSV columns:
    region, resource_class, yield, suitable_area, grazing_intensity

yield is in tDM per managed hectare, computed as
sum(hanpp_harv) / sum(managed_area) / C_FRACTION, where managed_area =
area_ha * grazing_intensity.  suitable_area is the physical grassland area
(ha).  grazing_intensity is the NPP-weighted mean of HANPP_harv / NPP_act,
clipped to [0, 1].
"""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from workflow.scripts.region_class_aggregation import (
    CellMapping,
    load_cell_mapping,
    weighted_sum_by_group,
)

# Carbon content of dry matter (tC per tDM)
C_FRACTION = 0.45


def aggregate_grassland_yields(
    area_km2: np.ndarray,
    npp_act: np.ndarray,
    hanpp_harv: np.ndarray,
    mapping: CellMapping,
) -> pd.DataFrame:
    """Aggregate LUIcube grassland variables by region and resource class."""
    if area_km2.shape != mapping.shape:
        raise ValueError("LUIcube grid does not match region/class cell mapping")
    if npp_act.shape != mapping.shape or hanpp_harv.shape != mapping.shape:
        raise ValueError("LUIcube variables do not share one grid")

    # Convert area to hectares: 1 km2 = 100 ha
    area_ha = area_km2 * 100.0

    # Compute per-cell grazing intensity = HANPP_harv / NPP_act, clipped [0, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        gi_cell = np.where(npp_act > 0, hanpp_harv / npp_act, 0.0)
    gi_cell = np.clip(gi_cell, 0.0, 1.0)

    # Managed pasture area: total grassland scaled by grazing intensity
    managed_area_ha = area_ha * gi_cell

    sums = pd.DataFrame(
        {
            "hanpp_sum": weighted_sum_by_group(hanpp_harv, mapping),
            "managed_area": weighted_sum_by_group(managed_area_ha, mapping),
            "suitable_area": weighted_sum_by_group(area_ha, mapping),
            "npp_sum": weighted_sum_by_group(npp_act, mapping),
            "gi_weighted_sum": weighted_sum_by_group(gi_cell * npp_act, mapping),
        },
        index=pd.MultiIndex.from_product(
            [mapping.regions, range(mapping.n_classes)],
            names=["region", "resource_class"],
        ),
    )

    # yield = sum(hanpp_harv) / sum(managed_area_ha) / C_FRACTION -> tDM/ha managed
    with np.errstate(divide="ignore", invalid="ignore"):
        sums["yield"] = np.where(
            sums["managed_area"] > 0,
            sums["hanpp_sum"] / sums["managed_area"] / C_FRACTION,
            0.0,
        )
    # grazing_intensity = sum(gi * npp) / sum(npp) (diagnostic)
    with np.errstate(divide="ignore", invalid="ignore"):
        sums["grazing_intensity"] = np.where(
            sums["npp_sum"] > 0,
            sums["gi_weighted_sum"] / sums["npp_sum"],
            0.0,
        )
    sums["grazing_intensity"] = sums["grazing_intensity"].clip(0.0, 1.0)
    return sums[["yield", "suitable_area", "grazing_intensity"]].sort_index()


if __name__ == "__main__":
    luicube_path: str = snakemake.input.luicube  # type: ignore[name-defined]
    mapping_path: str = snakemake.input.cell_mapping  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    mapping = load_cell_mapping(mapping_path)
    with xr.open_dataset(luicube_path) as ds:
        area_km2 = ds["area_km2"].load().values.astype(np.float64)
        npp_act = ds["npp_act_tc_yr"].load().values.astype(np.float64)
        hanpp_harv = ds["hanpp_harv_tc_yr"].load().values.astype(np.float64)

    out_df = aggregate_grassland_yields(area_km2, npp_act, hanpp_harv, mapping)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path)
