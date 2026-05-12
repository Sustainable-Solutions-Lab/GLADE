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


def _optional_path(value) -> Path:
    """Coerce snakemake.input.get('foo') → Path (handles list / str / None)."""
    if isinstance(value, list):
        return Path(value[0]) if value else Path("")
    if value:
        return Path(value)
    return Path("")


def _yield_weighted_residual_addition(
    yields_path: Path,
    attribution_path: Path,
    crop: str,
    regions: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Distribute per-country residual area across cells using yield x suit weights.

    Returns a per-(region, resource_class) frame with column ``value`` in ha.
    Empty if no residual is allocated to this crop.
    """
    attribution = pd.read_csv(attribution_path)
    attribution["country"] = attribution["country"].astype(str).str.upper()
    attribution["crop"] = attribution["crop"].astype(str).str.strip()
    residual_by_country = (
        attribution[attribution["crop"] == crop]
        .set_index("country")["residual_share_ha"]
        .astype(float)
    )
    if residual_by_country.sum() <= 0:
        return pd.DataFrame(columns=["region", "resource_class", "value"])

    y_tidy = pd.read_csv(yields_path)
    if y_tidy.empty:
        return pd.DataFrame(columns=["region", "resource_class", "value"])
    pivot = y_tidy.pivot(
        index=["region", "resource_class"], columns="variable", values="value"
    ).reset_index()
    if "yield" not in pivot.columns or "suitable_area" not in pivot.columns:
        return pd.DataFrame(columns=["region", "resource_class", "value"])

    pivot["yield"] = pd.to_numeric(pivot["yield"], errors="coerce").fillna(0.0)
    pivot["suitable_area"] = pd.to_numeric(
        pivot["suitable_area"], errors="coerce"
    ).fillna(0.0)
    pivot["weight"] = pivot["yield"] * pivot["suitable_area"]

    df = pivot.merge(regions[["region", "country"]], on="region", how="left")
    df = df[df["country"].notna()].copy()
    country_weight = df.groupby("country")["weight"].sum()

    df["target"] = df["country"].map(residual_by_country).fillna(0.0)
    df["cw"] = df["country"].map(country_weight).fillna(0.0)
    valid = (df["target"] > 0) & (df["cw"] > 0) & (df["weight"] > 0)
    df["value"] = 0.0
    df.loc[valid, "value"] = (
        df.loc[valid, "target"] * df.loc[valid, "weight"] / df.loc[valid, "cw"]
    )
    out = df.loc[df["value"] > 0, ["region", "resource_class", "value"]].copy()
    out["resource_class"] = out["resource_class"].astype(int)
    return out


if __name__ == "__main__":
    classes_nc = Path(snakemake.input.classes)  # type: ignore[name-defined]
    raster_path = Path(snakemake.input.harvested_area_raster)  # type: ignore[name-defined]
    regions_path = Path(snakemake.input.regions)  # type: ignore[name-defined]
    mapping_path = Path(snakemake.input.crop_mapping)  # type: ignore[name-defined]
    production_path = Path(snakemake.input.faostat_production)  # type: ignore[name-defined]
    fdd_shares_path = _optional_path(snakemake.input.get("fdd_shares"))  # type: ignore[name-defined]
    ooc_olive_share_path = _optional_path(snakemake.input.get("ooc_olive_share"))  # type: ignore[name-defined]
    # Banana absorbs a share of the GAEZ-FRT residual via build_frt_area_attribution.
    # The yields CSV is needed to weight that residual share by yield x suit.
    frt_attribution_path = _optional_path(snakemake.input.get("frt_attribution"))  # type: ignore[name-defined]
    crop_yields_path = _optional_path(snakemake.input.get("crop_yields"))  # type: ignore[name-defined]
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

    extracted = extracted.loc[:, ["region", "resource_class", "country", "value"]]

    # Banana (and other BAN-module crops added later) absorb a share of the
    # GAEZ-FRT residual (non-modelled fruits: pears, peaches, plums, pineapples,
    # papayas, kiwi, etc.) through build_frt_area_attribution. Distribute that
    # residual across cells weighted by the crop's GAEZ yield x suitable_area
    # so it lands on agroecologically suitable cells only.
    if (
        crop == "banana"
        and frt_attribution_path
        and frt_attribution_path.exists()
        and crop_yields_path
        and crop_yields_path.exists()
    ):
        addition = _yield_weighted_residual_addition(
            crop_yields_path, frt_attribution_path, crop, regions
        )
        if not addition.empty:
            combined = (
                pd.concat([extracted, addition.assign(country=None)], ignore_index=True)
                .groupby(["region", "resource_class"], as_index=False)["value"]
                .sum()
            )
            ba = float(extracted["value"].sum()) / 1e6
            ra = float(addition["value"].sum()) / 1e6
            logger.info(
                "banana: BAN raster=%.3f Mha + FRT residual addition=%.3f Mha = %.3f Mha",
                ba,
                ra,
                ba + ra,
            )
            extracted = combined

    extracted["variable"] = "harvested_area"
    extracted["unit"] = "ha"
    extracted = extracted.loc[
        :, ["region", "resource_class", "variable", "unit", "value"]
    ]
    extracted = extracted[extracted["value"] > 0]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    extracted.to_csv(output_path, index=False)
