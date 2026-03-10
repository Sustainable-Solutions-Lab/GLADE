"""
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

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

from workflow.scripts.raster_utils import raster_bounds, read_raster_float  # noqa: E402
from workflow.scripts.vegetable_projection import (  # noqa: E402
    OVG_COUNTRY_SHARE_BLEND,
    build_blended_crop_shares,
)

RES06_HAR_SCALE_TO_HA = 1_000.0  # rasters store thousand hectares (kha)
logger = logging.getLogger(__name__)


def _load_mapping(mapping_path: Path) -> pd.DataFrame:
    df = pd.read_csv(mapping_path)
    df["crop_name"] = df["crop_name"].astype(str).str.strip()
    df["res06_code"] = df["res06_code"].astype(str).str.strip().str.upper()
    return df


def _shares_for_crop(
    crop: str,
    mapping_df: pd.DataFrame,
    production_df: pd.DataFrame,
    non_food_crops: set[str] | None = None,
) -> tuple[dict[str, float], float]:
    """Return country-specific shares and fallback share for the given crop."""

    row = mapping_df[mapping_df["crop_name"] == crop]
    if row.empty:
        raise ValueError(f"Crop '{crop}' missing from RES06 mapping table")
    module_code = str(row.iloc[0]["res06_code"]).upper()

    crops_in_module: list[str] = (
        mapping_df[mapping_df["res06_code"].str.upper() == module_code]["crop_name"]
        .astype(str)
        .str.strip()
        .tolist()
    )
    crops_in_module = sorted(set(crops_in_module))
    if crop not in crops_in_module:
        crops_in_module.append(crop)

    if len(crops_in_module) == 1:
        return {}, 1.0

    production_df = production_df.copy()
    production_df["crop"] = production_df["crop"].astype(str).str.strip()
    production_df["country"] = production_df["country"].astype(str).str.upper()
    production_df = production_df[production_df["crop"].isin(crops_in_module)]

    # If this crop has no observed production mapping but sibling crops in the
    # same RES06 module do, avoid assigning synthetic harvested area shares.
    # This prevents non-food proxies (e.g. silage-maize) from inheriting area
    # from food crops (e.g. maize) in validation mode.
    if crop not in set(production_df["crop"].unique()) and len(crops_in_module) > 1:
        mapped_siblings = sorted(set(production_df["crop"].unique()))
        if mapped_siblings:
            logger.warning(
                "Crop '%s' has no FAOSTAT production mapping in RES06 module %s "
                "(mapped siblings: %s). Using harvested-area share 0.0.",
                crop,
                module_code,
                ", ".join(mapped_siblings),
            )
            return {}, 0.0

    if production_df.empty:
        uniform_share = 1.0 / len(crops_in_module)
        return {}, uniform_share

    non_food_set = set(non_food_crops or set())
    available_crops = set(production_df["crop"].unique())
    missing_crops = sorted(set(crops_in_module) - available_crops)
    missing_relevant = [c for c in missing_crops if c not in non_food_set]
    if missing_relevant:
        # If module sibling crops are absent from the FAOSTAT production input
        # (e.g., excluded from model configuration), avoid assigning 100% of the
        # aggregated RES06 harvested area to the remaining crop.
        uniform_share = 1.0 / len(crops_in_module)
        logger.warning(
            "Missing FAOSTAT production for RES06 module siblings of '%s': %s. "
            "Using uniform harvested-area share %.3f across module crops %s.",
            crop,
            ", ".join(missing_relevant),
            uniform_share,
            ", ".join(crops_in_module),
        )
        return {}, uniform_share

    if module_code == "OVG":
        lookup, global_share = build_blended_crop_shares(
            production_df,
            crops_in_module,
            blend_weight=OVG_COUNTRY_SHARE_BLEND,
        )
        shares_lookup = {
            country: share
            for (country, crop_name), share in lookup.items()
            if crop_name == crop
        }
        fallback_share = float(global_share.get(crop, 1.0 / len(crops_in_module)))
        return shares_lookup, fallback_share

    production_df["production_tonnes"] = pd.to_numeric(
        production_df["production_tonnes"], errors="coerce"
    ).fillna(0.0)

    by_country = (
        production_df.groupby(["country", "crop"])["production_tonnes"]
        .sum()
        .rename("crop_total")
        .reset_index()
    )
    country_totals = (
        by_country.groupby("country")["crop_total"].sum().rename("country_total")
    )
    by_country = by_country.merge(country_totals, on="country", how="left")
    by_country["share"] = np.where(
        by_country["country_total"] > 0,
        by_country["crop_total"] / by_country["country_total"],
        np.nan,
    )

    shares_lookup: dict[str, float] = {
        country: float(share)
        for country, share in zip(
            by_country[by_country["crop"] == crop]["country"],
            by_country[by_country["crop"] == crop]["share"],
        )
        if np.isfinite(share)
    }

    global_totals = (
        production_df.groupby("crop")["production_tonnes"].sum().rename("global_total")
    )
    global_denominator = float(global_totals.sum())
    if global_denominator > 0:
        fallback_share = float(global_totals.get(crop, 0.0) / global_denominator)
    else:
        fallback_share = 1.0 / len(crops_in_module)

    if fallback_share == 0.0:
        fallback_share = 1.0 / len(crops_in_module)

    return shares_lookup, fallback_share


def _apply_shares(
    df: pd.DataFrame,
    crop: str,
    shares_lookup: dict[str, float],
    fallback_share: float,
) -> pd.DataFrame:
    def _share(country: str) -> float:
        return shares_lookup.get(country, fallback_share)

    df = df.copy()
    df["share"] = df["country"].map(_share).fillna(fallback_share)
    df["value"] = df["value"] * df["share"]
    return df.drop(columns=["share"])


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


def _shares_from_fdd(
    fdd_shares_path: Path,
    crop: str,
) -> tuple[dict[str, float], float] | None:
    """Load pre-computed FDD area shares for a crop, if available."""
    if not fdd_shares_path.exists():
        return None
    fdd_df = pd.read_csv(fdd_shares_path)
    fdd_df["country"] = fdd_df["country"].astype(str).str.upper()
    crop_shares = fdd_df[fdd_df["crop"] == crop]
    if crop_shares.empty:
        return None
    shares_lookup = dict(zip(crop_shares["country"], crop_shares["share"]))
    fallback_share = float(crop_shares["share"].mean())
    return shares_lookup, fallback_share


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

    mapping_df = _load_mapping(mapping_path)

    # Check if this crop is in the FDD module and has pre-computed shares
    row = mapping_df[mapping_df["crop_name"] == crop]
    module_code = str(row.iloc[0]["res06_code"]).upper() if not row.empty else ""

    fdd_result = None
    if module_code == "FDD":
        fdd_result = _shares_from_fdd(fdd_shares_path, crop)

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
        shares_lookup, fallback_share = _shares_for_crop(
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
    extracted = _apply_shares(extracted, crop, shares_lookup, fallback_share)

    extracted["variable"] = "harvested_area"
    extracted["unit"] = "ha"

    extracted = extracted.loc[
        :, ["region", "resource_class", "variable", "unit", "value"]
    ]
    extracted = extracted[extracted["value"] > 0]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    extracted.to_csv(output_path, index=False)
