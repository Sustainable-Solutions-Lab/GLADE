# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-(country, modelled-fruit) target harvested area for the FRT pool.

The FAO/GAEZ Module-VI ``FRT`` raster bundles ~45 QCL fruit + grape +
tree-nut items at the cell level. Historically the model attributed FRT
cell area to citrus/mango/watermelon via national crop-mix scalars, which
silently placed e.g. citrus area in cells where GAEZ says citrus cannot
grow; those cells were then dropped at build time, losing ~13 Mha of
fruit area globally.

This script replaces the country-share approach with a per-(country,
modelled-fruit) **target area** table that downstream rules use together
with a cell-level yield x suitability weighting to ensure
agroecologically consistent placement.

For each country and baseline year:

  FRT_total_prod  = Σ FAOSTAT production tonnes of all FRT-pool QCL items
  direct_prod[f]  = Σ FAOSTAT production for the modelled FRT-pool fruits
                    (citrus/mango/watermelon/apple). Banana isn't in FRT,
                    so direct_prod[banana] = 0.
  excluded_prod   = wine_fraction x grape_prod  +  tree_nut_prod
                    (wine grapes feed the alcohol industry, tree nuts
                    belong to the nuts_seeds food group).
  residual_prod   = max(0, FRT_total_prod - Σ direct_prod - excluded_prod)
                    Non-modelled fruits: pears, peaches, plums, dates,
                    pineapples, berries, kiwi, papayas, etc.

The residual *tonnage* is projected onto all five modelled fruits
(citrus, mango, watermelon, apple, banana) proportional to each fruit's
per-country FAOSTAT production. Tropical countries push residual onto
banana/mango; temperate countries push it onto apple; etc. Tonnes are
then converted back to area using each modelled fruit's per-country
FAOSTAT yield (falling back to its global mean yield when the country
doesn't report it), so a hectare added to a high-yield crop like
banana represents less residual tonnage than a hectare added to apple.
This avoids the over-production that occurs when residual area is
distributed by FAOSTAT-area weight and then multiplied through each
modelled fruit's own yield at solve time.

  weight_prod[f]            = FAOSTAT production tonnes for the crop's
                              QCL items (banana includes plantains).
  residual_tonnes[f]        = residual_prod x weight_prod[f] / Σ weight_prod
  residual_share_ha[f]      = residual_tonnes[f] / yield_fresh[country, f]
  target_area_ha[f]         = direct_area[f] + residual_share_ha[f]

Output: one row per (country, crop) with columns
``faostat_direct_ha``, ``residual_share_ha``, ``target_area_ha``.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


# FAOSTAT element codes
AREA_HARVESTED_ELEMENT_CODE = 5312  # ha
PRODUCTION_ELEMENT_CODE = 5510  # tonnes
YIELD_ELEMENT_CODE = 5412  # kg/ha (fresh)
FBS_FOOD_ELEMENT_CODE = 5142  # food supply (excl. wine for grapes)

# Grapes (wine-deflated) and tree nuts share the FRT raster but belong to
# other food groups in the model; subtract them from the residual pool.
GRAPE_ITEM_CODES: tuple[int, ...] = (560,)
FBS_GRAPES_EXCL_WINE_ITEM_CODE = 2620
TREE_NUT_ITEM_CODES: tuple[int, ...] = (
    216,  # Brazil nuts, in shell
    217,  # Cashew nuts, in shell
    220,  # Chestnuts, in shell
    221,  # Almonds, in shell
    222,  # Walnuts, in shell
    223,  # Pistachios, in shell
    224,  # Kola nuts
    225,  # Hazelnuts, in shell
    226,  # Areca nuts
    234,  # Other nuts (excluding wild edible nuts and groundnuts), n.e.c.
)

# All FRT-pool QCL items (fruits + grapes + tree nuts). Mirrors
# build_frt_kept_area_share.FRT_ITEM_CODES at the time this file replaced it.
FRT_FRUIT_ITEM_CODES: tuple[int, ...] = (
    # Melons, tropical and subtropical fruits
    567,  # Watermelons
    568,  # Cantaloupes and other melons
    569,  # Figs
    571,  # Mangoes, guavas and mangosteens
    572,  # Avocados
    574,  # Pineapples
    577,  # Dates
    600,  # Papayas
    603,  # Other tropical fruits, n.e.c.
    # Citrus
    490,  # Oranges
    495,  # Tangerines, mandarins, clementines
    497,  # Lemons and limes
    507,  # Pomelos and grapefruits
    512,  # Other citrus fruit, n.e.c.
    # Pome and stone fruits
    515,  # Apples
    521,  # Pears
    523,  # Quinces
    526,  # Apricots
    530,  # Sour cherries
    531,  # Cherries
    534,  # Peaches and nectarines
    536,  # Plums and sloes
    541,  # Other stone fruits
    542,  # Other pome fruits
    # Berries and other minor fruits
    544,  # Strawberries
    547,  # Raspberries
    549,  # Gooseberries
    550,  # Currants
    552,  # Blueberries
    554,  # Cranberries
    558,  # Other berries and fruits of the genus vaccinium n.e.c.
    587,  # Persimmons
    591,  # Cashewapple
    592,  # Kiwi fruit
    619,  # Other fruits, n.e.c.
)
ALL_FRT_ITEM_CODES: tuple[int, ...] = (
    *FRT_FRUIT_ITEM_CODES,
    *GRAPE_ITEM_CODES,
    *TREE_NUT_ITEM_CODES,
)

# Modelled fruits → QCL item codes used for FAOSTAT-direct area and for
# residual-projection weights. Banana includes plantains; both share the
# GAEZ BAN raster on the supply side. Apple is sourced spatially from
# CROPGRIDS but its country-level direct area still comes from FAOSTAT.
MODELLED_FRUIT_QCL_CODES: dict[str, tuple[int, ...]] = {
    "citrus": (490, 495, 497, 507, 512),
    "mango": (571,),
    "watermelon": (567,),
    "apple": (515,),
    "banana": (486, 489),  # bananas + plantains
}

# Subset of modelled fruits that physically share the GAEZ FRT raster.
# Their FAOSTAT areas are subtracted from FRT_total when computing the
# residual (banana is NOT in FRT and so isn't subtracted here).
DIRECT_FRT_FRUITS: tuple[str, ...] = ("citrus", "mango", "watermelon", "apple")


def _compute_wine_fraction(
    qcl_bulk: pd.DataFrame,
    fbs_bulk: pd.DataFrame,
    countries: list[str],
    baseline_year: int,
) -> tuple[pd.Series, float]:
    """Per-country wine fraction (= 1 - FBS food / QCL grape production), clipped to [0, 1]."""
    grape_prod_df = filter_bulk(
        qcl_bulk,
        element_codes=[PRODUCTION_ELEMENT_CODE],
        item_codes=list(GRAPE_ITEM_CODES),
        years=[baseline_year],
        iso3_codes=countries,
    ).dropna(subset=["Value"])
    grape_prod = (
        grape_prod_df.assign(country=grape_prod_df["iso3"].astype(str).str.upper())
        .groupby("country")["Value"]
        .sum()
    )

    grape_food_df = filter_bulk(
        fbs_bulk,
        element_codes=[FBS_FOOD_ELEMENT_CODE],
        item_codes=[FBS_GRAPES_EXCL_WINE_ITEM_CODE],
        years=[baseline_year],
        iso3_codes=countries,
    ).dropna(subset=["Value"])
    grape_food = (
        grape_food_df.assign(country=grape_food_df["iso3"].astype(str).str.upper())
        .groupby("country")["Value"]
        .sum()
        * 1000.0  # 1000 t -> t
    )

    global_prod = float(grape_prod.sum())
    global_food = float(grape_food.sum())
    global_wine_fraction = (
        max(0.0, min(1.0, 1.0 - global_food / global_prod)) if global_prod > 0 else 0.5
    )

    full = pd.Index(sorted(set(countries)), name="country")
    prod = grape_prod.reindex(full, fill_value=0.0)
    food = grape_food.reindex(full, fill_value=0.0)
    wine_fraction = pd.Series(global_wine_fraction, index=full, dtype=float)
    has_prod = prod > 0
    wine_fraction.loc[has_prod] = (1.0 - food.loc[has_prod] / prod.loc[has_prod]).clip(
        lower=0.0, upper=1.0
    )

    logger.info(
        "Global grape wine fraction: %.3f (QCL production=%.2f Mt, FBS food (excl. wine)=%.2f Mt)",
        global_wine_fraction,
        global_prod / 1e6,
        global_food / 1e6,
    )
    return wine_fraction, global_wine_fraction


def _element_pivot(
    qcl_bulk: pd.DataFrame,
    element_code: int,
    item_codes: tuple[int, ...],
    countries: list[str],
    baseline_year: int,
) -> pd.DataFrame:
    """country x item_code → Value pivot for one FAOSTAT element."""
    df = filter_bulk(
        qcl_bulk,
        element_codes=[element_code],
        item_codes=list(item_codes),
        years=[baseline_year],
        iso3_codes=countries,
    ).dropna(subset=["Value"])
    df["country"] = df["iso3"].astype(str).str.upper()
    df["item_code"] = pd.to_numeric(df["Item Code"], errors="coerce").astype("Int64")
    return df.pivot_table(
        index="country",
        columns="item_code",
        values="Value",
        aggfunc="sum",
        fill_value=0.0,
    )


def _sum_present(pivot: pd.DataFrame, codes: tuple[int, ...]) -> pd.Series:
    present = [c for c in codes if c in pivot.columns]
    if not present:
        return pd.Series(0.0, index=pivot.index, dtype=float)
    return pivot[present].sum(axis=1).astype(float)


def _per_country_yield(
    qcl_bulk: pd.DataFrame,
    item_codes: tuple[int, ...],
    countries: list[str],
    baseline_year: int,
) -> tuple[pd.Series, float]:
    """Production-weighted FAOSTAT yield (kg/ha fresh) per country.

    For crops mapped to several QCL items (e.g. citrus = 5 items),
    aggregate production and area first, then yield = prod_tonnes / area_ha.
    Countries reporting no production or no area fall back to NaN; the
    caller substitutes the global mean.
    """
    prod = _element_pivot(
        qcl_bulk, PRODUCTION_ELEMENT_CODE, item_codes, countries, baseline_year
    )
    area = _element_pivot(
        qcl_bulk, AREA_HARVESTED_ELEMENT_CODE, item_codes, countries, baseline_year
    )
    prod_total = prod.sum(axis=1) if not prod.empty else pd.Series(dtype=float)
    area_total = area.sum(axis=1) if not area.empty else pd.Series(dtype=float)
    full = sorted(set(prod_total.index) | set(area_total.index))
    prod_total = prod_total.reindex(full, fill_value=0.0)
    area_total = area_total.reindex(full, fill_value=0.0)

    global_yield_kg_per_ha = (
        1000.0 * float(prod_total.sum()) / float(area_total.sum())
        if area_total.sum() > 0
        else float("nan")
    )

    # Per-country yield (kg/ha fresh): tonnes_to_kg * tonnes / ha
    with pd.option_context("mode.use_inf_as_na", True):
        yld = (1000.0 * prod_total / area_total).replace(
            [float("inf"), -float("inf")], pd.NA
        )
    yld = yld.where(area_total > 0)
    return yld, global_yield_kg_per_ha


def main() -> None:
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    qcl_path = snakemake.input.qcl_csv  # type: ignore[name-defined]
    fbs_path = snakemake.input.fbs_csv  # type: ignore[name-defined]
    m49_path = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]

    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    baseline_year = int(snakemake.params.baseline_year)  # type: ignore[name-defined]

    qcl = load_bulk(qcl_path)
    fbs = load_bulk(fbs_path)
    m49 = load_m49_to_iso3(m49_path)
    qcl = add_iso3_column(qcl, m49)
    fbs = add_iso3_column(fbs, m49)

    all_codes: tuple[int, ...] = (
        *ALL_FRT_ITEM_CODES,
        *MODELLED_FRUIT_QCL_CODES["banana"],
    )
    # FAOSTAT area pivot
    area_pivot = _element_pivot(
        qcl, AREA_HARVESTED_ELEMENT_CODE, all_codes, countries, baseline_year
    )
    # FAOSTAT production pivot
    prod_pivot = _element_pivot(
        qcl, PRODUCTION_ELEMENT_CODE, all_codes, countries, baseline_year
    )

    full_index = pd.Index(sorted(set(countries)), name="country")
    area_pivot = area_pivot.reindex(full_index, fill_value=0.0)
    prod_pivot = prod_pivot.reindex(full_index, fill_value=0.0)

    # Aggregate FRT pool totals (production, the basis for the residual)
    frt_total_prod = _sum_present(prod_pivot, ALL_FRT_ITEM_CODES)
    grape_prod = _sum_present(prod_pivot, GRAPE_ITEM_CODES)
    tree_nut_prod = _sum_present(prod_pivot, TREE_NUT_ITEM_CODES)

    wine_fraction, global_wine_fraction = _compute_wine_fraction(
        qcl, fbs, countries, baseline_year
    )
    aligned_wine = wine_fraction.reindex(
        prod_pivot.index, fill_value=global_wine_fraction
    )
    wine_excluded_prod = grape_prod * aligned_wine

    # Per-modelled-fruit direct area + direct production + projection-weight area.
    # Use FAOSTAT *area* (not production) as the residual-projection weight: a
    # production weight pushes too much residual onto high-yield fruits like
    # watermelon (whose absolute production share is large but whose role as
    # a "stand-in" for unmodelled fruits like dates/pineapples is small).
    # Banana includes plantain because BAN raster supply absorbs both.
    direct_areas: dict[str, pd.Series] = {}
    direct_prods: dict[str, pd.Series] = {}
    weight_areas: dict[str, pd.Series] = {}
    for crop, codes in MODELLED_FRUIT_QCL_CODES.items():
        weight_areas[crop] = _sum_present(area_pivot, codes)
        if crop in DIRECT_FRT_FRUITS:
            direct_areas[crop] = _sum_present(area_pivot, codes)
            direct_prods[crop] = _sum_present(prod_pivot, codes)
        else:
            # Banana isn't in the FRT pool; its FAOSTAT direct area comes
            # in through the GAEZ BAN raster downstream, not from this
            # attribution table.
            direct_areas[crop] = pd.Series(0.0, index=area_pivot.index, dtype=float)
            direct_prods[crop] = pd.Series(0.0, index=area_pivot.index, dtype=float)

    direct_prod_total = sum(direct_prods.values())
    excluded_prod_total = wine_excluded_prod + tree_nut_prod
    residual_prod = (frt_total_prod - direct_prod_total - excluded_prod_total).clip(
        lower=0.0
    )

    weight_area_total = sum(weight_areas.values())

    # Per-(country, crop) yields (kg/ha fresh) with global-mean fallback.
    yields_by_crop: dict[str, pd.Series] = {}
    global_yields: dict[str, float] = {}
    for crop, codes in MODELLED_FRUIT_QCL_CODES.items():
        yld, gy = _per_country_yield(qcl, codes, countries, baseline_year)
        yields_by_crop[crop] = yld.reindex(area_pivot.index)
        global_yields[crop] = gy

    rows: list[dict[str, object]] = []
    unallocated_residual_t = 0.0
    for country in area_pivot.index:
        w_tot = float(weight_area_total.get(country, 0.0))
        r_tot = float(residual_prod.get(country, 0.0))
        if r_tot > 0 and w_tot <= 0:
            unallocated_residual_t += r_tot
        for crop in MODELLED_FRUIT_QCL_CODES:
            d_area = float(direct_areas[crop].get(country, 0.0))
            w_area = float(weight_areas[crop].get(country, 0.0))
            share = (w_area / w_tot) if w_tot > 0 else 0.0
            r_tonnes = r_tot * share
            country_yield = yields_by_crop[crop].get(country)
            yld_kg_per_ha = (
                float(country_yield)
                if (
                    country_yield is not None
                    and pd.notna(country_yield)
                    and country_yield > 0
                )
                else (
                    global_yields[crop]
                    if global_yields[crop] and global_yields[crop] > 0
                    else float("nan")
                )
            )
            if pd.isna(yld_kg_per_ha) or yld_kg_per_ha <= 0:
                r_area_ha = 0.0
            else:
                # tonnes / (kg/ha) -> need to convert: 1 t = 1000 kg, so
                # area_ha = (tonnes * 1000) / (kg/ha) = tonnes / (kg/ha / 1000)
                r_area_ha = (r_tonnes * 1000.0) / yld_kg_per_ha
            d_prod = float(direct_prods[crop].get(country, 0.0))
            rows.append(
                {
                    "country": country,
                    "crop": crop,
                    "faostat_direct_ha": d_area,
                    "faostat_direct_tonnes": d_prod,
                    "residual_share_ha": r_area_ha,
                    "residual_share_tonnes": r_tonnes,
                    "target_area_ha": d_area + r_area_ha,
                    "target_production_tonnes": d_prod + r_tonnes,
                }
            )

    out = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)

    by_crop = out.groupby("crop").agg(
        direct_ha=("faostat_direct_ha", "sum"),
        residual_ha=("residual_share_ha", "sum"),
        residual_tonnes=("residual_share_tonnes", "sum"),
        target_ha=("target_area_ha", "sum"),
    )
    logger.info("FRT area attribution by modelled fruit (global totals):")
    for crop, row in by_crop.iterrows():
        logger.info(
            "  %-11s: direct=%6.2f Mha + residual=%6.2f Mha (%6.2f Mt) = target=%6.2f Mha",
            crop,
            row.direct_ha / 1e6,
            row.residual_ha / 1e6,
            row.residual_tonnes / 1e6,
            row.target_ha / 1e6,
        )
    logger.info(
        "Excluded from residual (Mt): wine_grape=%.2f, tree_nut=%.2f. "
        "Unallocated residual (no modelled fruit in country): %.2f Mt",
        float(wine_excluded_prod.sum()) / 1e6,
        float(tree_nut_prod.sum()) / 1e6,
        unallocated_residual_t / 1e6,
    )


if __name__ == "__main__":
    main()
