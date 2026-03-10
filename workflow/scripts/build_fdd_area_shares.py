# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compute country-level area shares for FDD (fodder) module crops.

Decomposes the aggregate GAEZ RES06 FDD harvested area between alfalfa and
silage-maize using a tiered approach:

1. **Eurostat production data** for EU/EFTA countries (G2100=lucerne,
   G3000=green maize, G0000=total green fodder).
2. **GAEZ RES05 potential yield ratios** as fallback for the rest of the world,
   blended with the global average.

Output: CSV with columns (country, crop, share)
"""

import logging

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.raster_utils import raster_bounds, read_raster_float

logger = logging.getLogger(__name__)

# Map Eurostat crop codes to model crop names
EUROSTAT_TO_MODEL = {
    "G2100": "alfalfa",
    "G3000": "silage-maize",
}


def compute_eurostat_shares(eurostat_df: pd.DataFrame) -> pd.DataFrame:
    """Compute shares from Eurostat production data.

    For each country with G0000 (total green fodder) data:
    - share_alfalfa = G2100 / G0000
    - share_silage_maize = G3000 / G0000
    """
    # Pivot to get one column per crop code
    pivot = eurostat_df.pivot_table(
        index="country", columns="crop_code", values="production_1000t", aggfunc="sum"
    ).fillna(0.0)

    if "G0000" not in pivot.columns:
        return pd.DataFrame(columns=["country", "crop", "share"])

    total = pivot["G0000"]
    records = []
    for code, crop in EUROSTAT_TO_MODEL.items():
        if code not in pivot.columns:
            continue
        share = np.where(total > 0, pivot[code] / total, 0.0)
        for country, s in zip(pivot.index, share):
            if s > 0:
                records.append({"country": country, "crop": crop, "share": float(s)})

    return pd.DataFrame(records)


def aggregate_yield_by_country(
    raster_path: str, regions: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Compute area-weighted mean potential yield by country from a GAEZ raster."""
    arr, src = read_raster_float(raster_path)
    try:
        transform = src.transform
        crs_wkt = src.crs.to_wkt() if src.crs else None
    finally:
        src.close()

    xmin, ymin, xmax, ymax = raster_bounds(transform, arr.shape[1], arr.shape[0])

    raster_src = NumPyRasterSource(
        arr,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        nodata=np.nan,
        srs_wkt=crs_wkt,
    )

    gdf = regions[["country", "geometry"]].copy()
    stats = exact_extract(
        raster_src,
        gdf,
        ["mean"],
        include_cols=["country"],
        output="pandas",
    )
    if stats is None or stats.empty:
        return pd.DataFrame(columns=["country", "mean_yield"])

    # Average across sub-country regions (multiple regions per country)
    agg = stats.groupby("country")["mean"].mean().reset_index()
    agg.columns = ["country", "mean_yield"]
    return agg


def compute_suitability_shares(
    yield_alfalfa_path: str,
    yield_silage_maize_path: str,
    regions: gpd.GeoDataFrame,
    fdd_crops: list[str],
    blend_weight: float,
) -> pd.DataFrame:
    """Compute shares from GAEZ RES05 potential yield ratios.

    For each country:
      share_crop = potential_yield_crop / sum(potential_yields)
    Then blend with the global average:
      share = blend_weight * country_share + (1 - blend_weight) * global_share
    """
    yield_alf = aggregate_yield_by_country(yield_alfalfa_path, regions)
    yield_mzs = aggregate_yield_by_country(yield_silage_maize_path, regions)

    merged = yield_alf.merge(
        yield_mzs, on="country", how="outer", suffixes=("_alfalfa", "_silage_maize")
    ).fillna(0.0)

    total = merged["mean_yield_alfalfa"] + merged["mean_yield_silage_maize"]

    # Country-level shares
    merged["share_alfalfa"] = np.where(
        total > 0, merged["mean_yield_alfalfa"] / total, 0.5
    )
    merged["share_silage_maize"] = np.where(
        total > 0, merged["mean_yield_silage_maize"] / total, 0.5
    )

    # Global average shares
    global_alf = merged["mean_yield_alfalfa"].sum()
    global_mzs = merged["mean_yield_silage_maize"].sum()
    global_total = global_alf + global_mzs
    if global_total > 0:
        global_share_alf = global_alf / global_total
        global_share_mzs = global_mzs / global_total
    else:
        global_share_alf = 0.5
        global_share_mzs = 0.5

    # Blend
    merged["share_alfalfa"] = (
        blend_weight * merged["share_alfalfa"] + (1 - blend_weight) * global_share_alf
    )
    merged["share_silage_maize"] = (
        blend_weight * merged["share_silage_maize"]
        + (1 - blend_weight) * global_share_mzs
    )

    # Melt to long form
    records = []
    crop_map = {"alfalfa": "share_alfalfa", "silage-maize": "share_silage_maize"}
    for crop in fdd_crops:
        col = crop_map.get(crop)
        if col is None:
            continue
        for _, row in merged.iterrows():
            records.append(
                {
                    "country": row["country"],
                    "crop": crop,
                    "share": float(row[col]),
                }
            )

    return pd.DataFrame(records)


def main():
    eurostat_path = str(snakemake.input.eurostat_fodder)  # type: ignore[name-defined]
    regions_path = str(snakemake.input.regions)  # type: ignore[name-defined]
    yield_alf_path = str(snakemake.input.yield_alfalfa)  # type: ignore[name-defined]
    yield_mzs_path = str(snakemake.input.yield_silage_maize)  # type: ignore[name-defined]
    out_path = str(snakemake.output[0])  # type: ignore[name-defined]

    fdd_crops = list(snakemake.params.fdd_crops)  # type: ignore[name-defined]
    blend_weight = float(snakemake.params.suitability_blend_weight)  # type: ignore[name-defined]

    # Load regions
    regions = gpd.read_file(regions_path)[["region", "country", "geometry"]]
    regions["country"] = regions["country"].astype(str).str.upper()

    # Tier 1: Eurostat shares for EU/EFTA countries
    eurostat_df = pd.read_csv(eurostat_path)
    eurostat_df["country"] = eurostat_df["country"].astype(str).str.upper()
    eurostat_shares = compute_eurostat_shares(eurostat_df)
    eurostat_countries = set(eurostat_shares["country"].unique())

    logger.info("Eurostat shares computed for %d countries", len(eurostat_countries))

    # Tier 2: Suitability-based shares for all other countries
    suitability_shares = compute_suitability_shares(
        yield_alf_path,
        yield_mzs_path,
        regions,
        fdd_crops,
        blend_weight,
    )
    # Keep only non-Eurostat countries
    suitability_shares = suitability_shares[
        ~suitability_shares["country"].isin(eurostat_countries)
    ]

    logger.info(
        "Suitability shares computed for %d countries",
        suitability_shares["country"].nunique(),
    )

    # Combine
    combined = pd.concat([eurostat_shares, suitability_shares], ignore_index=True)
    combined = combined.sort_values(["country", "crop"]).reset_index(drop=True)

    combined.to_csv(out_path, index=False)
    logger.info("Wrote FDD area shares to %s (%d rows)", out_path, len(combined))


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
