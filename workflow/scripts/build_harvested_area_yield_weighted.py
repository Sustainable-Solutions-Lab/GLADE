# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Yield x suitability weighted harvested-area distribution.

For FRT-pool fruits modelled directly (citrus, mango, watermelon), the
GAEZ FRT raster bundles ~45 different fruit + grape + tree-nut items.
The old country-share approach (``build_harvested_area.py`` +
``build_frt_kept_area_share.py``) attributed FRT cells to citrus/mango/
watermelon by a single national scalar, which silently placed citrus
area in temperate cells where GAEZ says citrus has zero yield. Those
cells were then dropped at build time, losing ~13 Mha of area globally
across the trio.

This script replaces that flow: for each crop it takes the per-country
target area from ``build_frt_area_attribution`` and distributes it
across (region, resource_class) cells in proportion to the crop's GAEZ
``yield * suitable_area`` weight at the same aggregation. Cells where
GAEZ yield or suitability is zero get zero area by construction.

Inputs:

* ``crop_yields/{crop}_r.csv``: per-(region, resource_class) GAEZ yield
  (mean) and suitable_area (sum), produced by ``build_crop_yields``.
* ``frt_area_attribution.csv``: per-(country, crop) target_area_ha,
  produced by ``build_frt_area_attribution``.
* ``regions.geojson``: region → country lookup.

Output: ``harvested_area/gaez/{crop}_r.csv`` (same schema as the
existing GAEZ-raster pipeline; consumers downstream don't change).
"""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_weights(yields_path: Path, water_supply: str) -> pd.DataFrame:
    """Return (region, resource_class, weight) where weight = yield x suit_area."""
    if not yields_path.exists():
        return pd.DataFrame(
            columns=["region", "resource_class", "weight", "water_supply"]
        )
    y_tidy = pd.read_csv(yields_path)
    if y_tidy.empty:
        return pd.DataFrame(
            columns=["region", "resource_class", "weight", "water_supply"]
        )
    pivot = y_tidy.pivot(
        index=["region", "resource_class"], columns="variable", values="value"
    ).reset_index()
    if "yield" not in pivot.columns or "suitable_area" not in pivot.columns:
        return pd.DataFrame(
            columns=["region", "resource_class", "weight", "water_supply"]
        )
    pivot["yield"] = pd.to_numeric(pivot["yield"], errors="coerce").fillna(0.0)
    pivot["suitable_area"] = pd.to_numeric(
        pivot["suitable_area"], errors="coerce"
    ).fillna(0.0)
    pivot["weight"] = pivot["yield"] * pivot["suitable_area"]
    pivot["water_supply"] = water_supply
    return pivot[["region", "resource_class", "weight", "water_supply"]]


def _emit_tidy(df: pd.DataFrame, path: Path) -> float:
    """Write a tidy harvested_area CSV; return total area allocated (Mha)."""
    out = df.loc[
        df["allocated_ha"] > 0, ["region", "resource_class", "allocated_ha"]
    ].copy()
    out = out.rename(columns={"allocated_ha": "value"})
    out["variable"] = "harvested_area"
    out["unit"] = "ha"
    out["resource_class"] = out["resource_class"].astype(int)
    out = out[["region", "resource_class", "variable", "unit", "value"]].sort_values(
        ["region", "resource_class"], ignore_index=True
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return float(out["value"].sum()) / 1e6


def main() -> None:
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    crop = str(snakemake.wildcards.crop)  # type: ignore[name-defined]
    yields_r_path = Path(snakemake.input.yields_r)  # type: ignore[name-defined]
    yields_i_input = getattr(snakemake.input, "yields_i", None)  # type: ignore[name-defined]
    yields_i_path = Path(yields_i_input) if yields_i_input else Path("")
    attribution_path = Path(snakemake.input.attribution)  # type: ignore[name-defined]
    regions_path = Path(snakemake.input.regions)  # type: ignore[name-defined]
    output_r = Path(snakemake.output.rainfed)  # type: ignore[name-defined]
    output_i_attr = getattr(snakemake.output, "irrigated", None)  # type: ignore[name-defined]
    output_i = Path(output_i_attr) if output_i_attr else None

    weights_r = _load_weights(yields_r_path, "r")
    weights_i = (
        _load_weights(yields_i_path, "i")
        if yields_i_path and yields_i_path.exists()
        else pd.DataFrame(
            columns=["region", "resource_class", "weight", "water_supply"]
        )
    )

    # Also pull per-cell yield (DM, t/ha) so we can rescale target_area per
    # country to deliver the right target *production* under area-prop-to-
    # yield-x-suit weighting.
    def _load_yield_only(p: Path, ws: str) -> pd.DataFrame:
        if not p.exists():
            return pd.DataFrame(
                columns=["region", "resource_class", "yield_dm", "water_supply"]
            )
        y = pd.read_csv(p)
        if y.empty:
            return pd.DataFrame(
                columns=["region", "resource_class", "yield_dm", "water_supply"]
            )
        pv = y.pivot(
            index=["region", "resource_class"], columns="variable", values="value"
        ).reset_index()
        if "yield" not in pv.columns:
            return pd.DataFrame(
                columns=["region", "resource_class", "yield_dm", "water_supply"]
            )
        pv = pv[["region", "resource_class", "yield"]].rename(
            columns={"yield": "yield_dm"}
        )
        pv["yield_dm"] = pd.to_numeric(pv["yield_dm"], errors="coerce").fillna(0.0)
        pv["water_supply"] = ws
        return pv

    yields_only = pd.concat(
        [_load_yield_only(yields_r_path, "r"), _load_yield_only(yields_i_path, "i")],
        ignore_index=True,
    )

    weights = pd.concat([weights_r, weights_i], ignore_index=True)

    # Region -> country lookup
    regions = gpd.read_file(regions_path)[["region", "country"]].copy()
    regions["country"] = regions["country"].astype(str).str.upper()

    df = weights.merge(regions, on="region", how="left")
    df = df.merge(
        yields_only, on=["region", "resource_class", "water_supply"], how="left"
    )
    df["yield_dm"] = df["yield_dm"].fillna(0.0)
    df = df[df["country"].notna()].copy()

    # Per country: rescale target_area so area x yield delivers target_production.
    # With area allocated proportional to weight = yield x suit, total country
    # production = target_area * sum(yield^2 x suit) / sum(yield x suit).
    # We want total production = target_production_DM (= target_production_fresh
    # * (1 - moisture) -- but target_production_tonnes_fresh is consistent
    # because all per-crop conversions cancel out under linear scaling).
    #
    # The shortcut: target_area = target_production_t / weighted_mean_yield,
    # where weighted_mean_yield = sum(yield^2 x suit) / sum(yield x suit).
    #
    # We work in fresh-weight tonnes for the production target (matches
    # FAOSTAT). Convert to DM via the per-cell yield_dm and FAOSTAT yield
    # for consistency: cell production (DM) = area * yield_dm, so total DM
    # production = target_production_fresh * (1 - moisture) when
    # target_area = target_production_fresh / weighted_mean_yield_fresh,
    # or equivalently target_area = target_production_DM / weighted_mean_yield_DM.
    # We use yield_dm everywhere; the FAOSTAT target_production_tonnes is
    # fresh-weight, so first scale by (1 - moisture). Pull moisture from the
    # attribution table by reading the corresponding direct/target lines.
    country_w1 = (
        df.assign(w1=df["weight"]).groupby("country")["w1"].sum()
    )  # sum(yield * suit)
    df["yield_x_weight"] = df["yield_dm"] * df["weight"]  # = yield^2 x suit
    country_w2 = df.groupby("country")["yield_x_weight"].sum()

    # weighted mean yield (DM, t/ha) across allocated cells
    country_mean_yield = (country_w2 / country_w1).replace(
        [float("inf"), -float("inf")], pd.NA
    )

    # Load attribution and the moisture fraction for this crop.
    attribution = pd.read_csv(attribution_path)
    attribution["country"] = attribution["country"].astype(str).str.upper()
    attribution["crop"] = attribution["crop"].astype(str).str.strip()
    target_production_fresh = (
        attribution[attribution["crop"] == crop]
        .set_index("country")["target_production_tonnes"]
        .astype(float)
    )
    moisture = float(snakemake.params.moisture_fraction)  # type: ignore[name-defined]
    target_production_dm = target_production_fresh * (1.0 - moisture)

    # target_area per country = target_production_DM / weighted_mean_yield
    target_area_by_country = pd.Series(0.0, index=country_mean_yield.index, dtype=float)
    mask = country_mean_yield.notna() & (country_mean_yield > 0)
    target_area_by_country.loc[mask] = target_production_dm.reindex(
        country_mean_yield.index
    ).fillna(0.0).loc[mask] / country_mean_yield.loc[mask].astype(float)

    df["target_area"] = df["country"].map(target_area_by_country).fillna(0.0)
    df["country_weight"] = df["country"].map(country_w1).fillna(0.0)
    valid = (df["country_weight"] > 0) & (df["weight"] > 0) & (df["target_area"] > 0)
    df["allocated_ha"] = 0.0
    df.loc[valid, "allocated_ha"] = (
        df.loc[valid, "target_area"]
        * df.loc[valid, "weight"]
        / df.loc[valid, "country_weight"]
    )

    r_mha = _emit_tidy(df[df["water_supply"] == "r"], output_r)
    i_mha = (
        _emit_tidy(df[df["water_supply"] == "i"], output_i)
        if output_i is not None
        else 0.0
    )

    total_alloc = r_mha + i_mha
    target_prod_total = float(target_production_fresh.sum()) / 1e6
    lost_countries = sorted(
        target_production_fresh[
            (target_production_fresh > 0)
            & (~target_production_fresh.index.isin(country_w1[country_w1 > 0].index))
        ].index
    )
    logger.info(
        "%s: target_production=%.2f Mt fresh -> allocated rainfed=%.3f Mha + "
        "irrigated=%.3f Mha = %.3f Mha. Countries with target>0 but no "
        "GAEZ-yield cells: %d (%s%s)",
        crop,
        target_prod_total,
        r_mha,
        i_mha,
        total_alloc,
        len(lost_countries),
        ", ".join(lost_countries[:5]),
        "..." if len(lost_countries) > 5 else "",
    )


if __name__ == "__main__":
    main()
