"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import logging
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

from exactextract import exact_extract  # noqa: E402
from exactextract.raster import NumPyRasterSource  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402

from workflow.scripts.harvested_area_shares import (  # noqa: E402
    RES06_HAR_SCALE_TO_HA,
    apply_country_shares,
    load_mapping,
    shares_for_crop,
    shares_from_fdd,
)
from workflow.scripts.raster_utils import raster_bounds, read_raster_float  # noqa: E402

logger = logging.getLogger(__name__)


def _extract_harvested_area(
    raster: np.ndarray,
    transform,
    crs_wkt: str | None,
    class_labels: np.ndarray,
    regions: gpd.GeoDataFrame,
) -> pd.DataFrame:
    xmin, ymin, xmax, ymax = raster_bounds(transform, raster.shape[1], raster.shape[0])

    regions_for_extract = regions.reset_index(drop=True)

    records: list[pd.DataFrame] = []
    n_classes = (
        int(np.nanmax(class_labels)) + 1 if np.isfinite(class_labels).any() else 0
    )
    for cls in range(n_classes):
        mask = class_labels == cls
        if not np.any(mask):
            continue
        masked = np.where(mask, raster, np.nan)
        raster_src = NumPyRasterSource(
            masked,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            nodata=np.nan,
            srs_wkt=crs_wkt,
        )
        stats = exact_extract(
            raster_src,
            regions_for_extract,
            ["sum"],
            include_cols=["region"],
            output="pandas",
        )
        if stats.empty:
            continue
        stats = stats.rename(columns={"sum": "value"})
        stats["resource_class"] = cls
        records.append(stats)

    if not records:
        return pd.DataFrame(columns=["region", "resource_class", "value"])

    combined = pd.concat(records, ignore_index=True)
    combined["resource_class"] = combined["resource_class"].astype(int)
    return combined


if __name__ == "__main__":
    classes_nc = Path(snakemake.input.classes)  # type: ignore[name-defined]
    raster_path = Path(snakemake.input.harvested_area_raster)  # type: ignore[name-defined]
    regions_path = Path(snakemake.input.regions)  # type: ignore[name-defined]
    mapping_path = Path(snakemake.input.crop_mapping)  # type: ignore[name-defined]
    production_path = Path(snakemake.input.faostat_production)  # type: ignore[name-defined]
    fdd_shares_raw = snakemake.input.get("fdd_shares")  # type: ignore[name-defined]
    if isinstance(fdd_shares_raw, list):
        fdd_shares_path = Path(fdd_shares_raw[0]) if fdd_shares_raw else Path("")
    elif fdd_shares_raw:
        fdd_shares_path = Path(fdd_shares_raw)
    else:
        fdd_shares_path = Path("")
    ooc_olive_share_raw = snakemake.input.get("ooc_olive_share")  # type: ignore[name-defined]
    if isinstance(ooc_olive_share_raw, list):
        ooc_olive_share_path = (
            Path(ooc_olive_share_raw[0]) if ooc_olive_share_raw else Path("")
        )
    elif ooc_olive_share_raw:
        ooc_olive_share_path = Path(ooc_olive_share_raw)
    else:
        ooc_olive_share_path = Path("")
    frt_kept_share_raw = snakemake.input.get("frt_kept_share")  # type: ignore[name-defined]
    if isinstance(frt_kept_share_raw, list):
        frt_kept_share_path = (
            Path(frt_kept_share_raw[0]) if frt_kept_share_raw else Path("")
        )
    elif frt_kept_share_raw:
        frt_kept_share_path = Path(frt_kept_share_raw)
    else:
        frt_kept_share_path = Path("")
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    crop = str(snakemake.wildcards.crop)  # type: ignore[name-defined]

    ds = xr.load_dataset(classes_nc)
    class_labels = ds["resource_class"].values.astype(np.int16)

    harvested_raw, src = read_raster_float(raster_path)
    try:
        harvested_raw = harvested_raw * RES06_HAR_SCALE_TO_HA
        transform = src.transform
        crs_wkt = src.crs.to_wkt() if src.crs else None
    finally:
        src.close()

    regions = gpd.read_file(regions_path)[["region", "country", "geometry"]]
    regions["country"] = regions["country"].astype(str).str.upper()

    mapping_df = load_mapping(mapping_path)

    # Check if this crop is in the FDD module and has pre-computed shares
    row = mapping_df[mapping_df["crop_name"] == crop]
    module_code = str(row.iloc[0]["res06_code"]).upper() if not row.empty else ""

    fdd_result = None
    if module_code == "FDD":
        fdd_result = shares_from_fdd(fdd_shares_path, crop)

    if fdd_result is not None:
        shares_lookup, fallback_share = fdd_result
        logger.info(
            "Using pre-computed FDD shares for '%s' (fallback=%.3f)",
            crop,
            fallback_share,
        )
    else:
        production_df = pd.read_csv(production_path)
        non_food_crops = set(getattr(snakemake.params, "non_food_crops", []))  # type: ignore[attr-defined]
        shares_lookup, fallback_share = shares_for_crop(
            crop,
            mapping_df,
            production_df,
            non_food_crops=non_food_crops,
        )

    extracted = _extract_harvested_area(
        harvested_raw,
        transform,
        crs_wkt,
        class_labels,
        regions,
    )

    extracted = extracted.merge(regions[["region", "country"]], on="region", how="left")
    extracted = apply_country_shares(extracted, shares_lookup, fallback_share)

    if crop == "olive" and ooc_olive_share_path and ooc_olive_share_path.exists():
        # GAEZ Module VI OOC raster mixes olive with other minor oilseed land
        # (linseed, mustard, etc.). Deflate per-country to the FAOSTAT-derived
        # olive share within OOC so non-olive area is not attributed to olive.
        olive_shares = pd.read_csv(ooc_olive_share_path)
        olive_shares["country"] = olive_shares["country"].astype(str).str.upper()
        share_map = dict(
            zip(olive_shares["country"], olive_shares["olive_share"], strict=False)
        )
        global_share = (
            float(olive_shares["olive_share"].mean()) if not olive_shares.empty else 1.0
        )
        deflation = extracted["country"].map(share_map).fillna(global_share)
        extracted["value"] = extracted["value"] * deflation

    if (
        crop in ("citrus", "mango", "watermelon")
        and frt_kept_share_path
        and frt_kept_share_path.exists()
    ):
        # GAEZ Module VI FRT raster bundles wine grapes (excluded from the
        # demand-side fruits projection: most grape harvest enters
        # wine/raisin processing) and tree nuts (which belong to the
        # nuts_seeds food group with its own NUTS projection) with the
        # fruits the trio actually absorbs. Deflate per-country to the
        # FAOSTAT "kept" share so supply and demand absorb the same
        # unmodeled basket.
        frt_shares = pd.read_csv(frt_kept_share_path)
        frt_shares["country"] = frt_shares["country"].astype(str).str.upper()
        share_map = dict(
            zip(frt_shares["country"], frt_shares["kept_share"], strict=False)
        )
        global_share = (
            float(frt_shares["kept_share"].mean()) if not frt_shares.empty else 1.0
        )
        deflation = extracted["country"].map(share_map).fillna(global_share)
        extracted["value"] = extracted["value"] * deflation

    extracted["variable"] = "harvested_area"
    extracted["unit"] = "ha"

    extracted = extracted.loc[
        :, ["region", "resource_class", "variable", "unit", "value"]
    ]
    extracted = extracted[extracted["value"] > 0]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    extracted.to_csv(output_path, index=False)
