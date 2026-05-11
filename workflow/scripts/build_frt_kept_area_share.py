# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute per-country share of GAEZ FRT-module area kept for trio attribution.

The GAEZ v5 Module VI ``FRT`` group (Appendix 4-1, Group_Module_VI == 31)
bundles 45 FAOSTAT QCL items: 34 fruits, grapes, and 10 tree nuts. The
supply side of the model attributes the full FRT raster to the trio
``{citrus, mango, watermelon}`` (see ``harvested_area_shares.shares_for_crop``).
Two sub-categories have only partial overlap with the demand-side
projection in ``vegetable_projection.FRUITS_FRT_RESIDUAL_ITEM_CODES``:

* **Grapes** (QCL 560) — only the non-wine portion (fresh grapes + raisins)
  is a plausible substitute for the modeled fruits. Wine grapes enter
  alcoholic-beverage processing that GBD's ``fruits`` risk factor explicitly
  excludes. FAOSTAT FBS item 2620 ("Grapes and products, *excl. wine*",
  element 5142 "Food") gives the per-country fresh-equivalent mass that
  stayed in the fruits supply chain. The wine fraction is then
  ``1 - FBS2620_food / QCL560_production`` and we exclude only that
  fraction of grape area from the FRT raster.
* **Tree nuts** (QCL 216, 217, 220-226, 234) — belong to the
  ``nuts_seeds`` food group, projected separately onto the
  ``NUTS_PROJECTION_FOODS`` quartet
  ``{groundnut, sesame-seed, coconut, sunflower-seed}``. Excluded in full.

For each country this script computes::

    excluded_area  = wine_fraction[c] * grape_area[c] + tree_nut_area[c]
    kept_share[c]  = (FRT_area[c] - excluded_area[c]) / FRT_area[c]

from FAOSTAT QCL "Area harvested" (element 5312) and FBS Food supply
(element 5142). Countries with no FAOSTAT FRT reporting fall back to the
global kept share; countries with no QCL grape production fall back to
the global wine fraction. The output is consumed by
``build_harvested_area.py`` to deflate the FRT raster area for the trio
before per-country attribution.

Note: a FAOSTAT-anchored *area-scaling* variant of this script was
prototyped (factor = FAOSTAT_target / GAEZ_raster_sum per country) but
gave near-identical results because GAEZ's FRT raster total already
matches FAOSTAT's FRT total globally; the remaining trio under-supply
(~50 Mt of citrus food bus in validation) is structural — caused by the
per-crop yield filter in ``build_model/crops.py`` dropping cells where
the modeled trio crop can't actually grow (apple/peach area in
temperate climates routed to citrus/mango/watermelon at zero yield).
Fixing that requires either (a) per-region yield-weighted trio shares
or (b) a per-crop area dataset such as CROPGRIDS rather than the
FRT-bucket raster.
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

# FAOSTAT QCL element codes.
AREA_HARVESTED_ELEMENT_CODE = 5312  # ha
PRODUCTION_ELEMENT_CODE = 5510  # tonnes
# FAOSTAT FBS element code for "Food" supply in 1000 t primary equivalent
# (excludes wine for item 2620).
FBS_FOOD_ELEMENT_CODE = 5142
# FBS item code for "Grapes and products (excl. wine)".
FBS_GRAPES_EXCL_WINE_ITEM_CODE = 2620

# Source: GAEZ v5 Module VI Appendix 4-1, sheet "FAOSTAT_Crops",
# Group_Module_VI == 31 (FRT — Fruits and Nuts).
GRAPE_ITEM_CODES: tuple[int, ...] = (560,)  # Grapes
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
# QCL items modelled outside the FRT pool. Apples are sourced from CROPGRIDS
# (see config["cropgrids_crops"]) and have their own supply chain, so their
# area must be deflated out of the FRT raster before the citrus/mango/
# watermelon trio absorbs it. Add other CROPGRIDS-modelled fruit items here
# as they come online.
APPLE_ITEM_CODES: tuple[int, ...] = (515,)  # Apples (FBS 2617)

FRUIT_ITEM_CODES: tuple[int, ...] = (
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
    *APPLE_ITEM_CODES,  # Apples (deflated below; included so FRT_area totals
    # match what GAEZ actually packs into the FRT raster).
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
FRT_ITEM_CODES: tuple[int, ...] = (
    *FRUIT_ITEM_CODES,
    *GRAPE_ITEM_CODES,
    *TREE_NUT_ITEM_CODES,
)


def _compute_wine_fraction(
    qcl_bulk: pd.DataFrame,
    fbs_bulk: pd.DataFrame,
    countries: list[str],
    baseline_year: int,
) -> tuple[pd.Series, float]:
    """Return (per-country wine fraction, global wine fraction).

    ``wine_fraction = 1 - FBS_2620_food / QCL_560_production``, clipped to
    [0, 1]. Countries with no QCL grape production fall back to the
    global fraction (computed from worldwide totals). FBS values are in
    1000 t and converted to tonnes before comparison.
    """
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
        "Global grape wine fraction: %.3f "
        "(QCL production=%.2f Mt, FBS food (excl. wine)=%.2f Mt)",
        global_wine_fraction,
        global_prod / 1e6,
        global_food / 1e6,
    )
    return wine_fraction, global_wine_fraction


def main() -> None:
    qcl_path = snakemake.input.qcl_csv  # type: ignore[name-defined]
    fbs_path = snakemake.input.fbs_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    baseline_year = int(snakemake.params.baseline_year)  # type: ignore[name-defined]

    m49_to_iso3 = load_m49_to_iso3(m49_codes)

    logger.info("Loading FAOSTAT QCL bulk data")
    qcl_bulk = add_iso3_column(load_bulk(qcl_path), m49_to_iso3)
    logger.info("Loading FAOSTAT FBS bulk data")
    fbs_bulk = add_iso3_column(load_bulk(fbs_path), m49_to_iso3)

    wine_fraction, global_wine_fraction = _compute_wine_fraction(
        qcl_bulk, fbs_bulk, countries, baseline_year
    )

    df = filter_bulk(
        qcl_bulk,
        element_codes=[AREA_HARVESTED_ELEMENT_CODE],
        item_codes=list(FRT_ITEM_CODES),
        years=[baseline_year],
        iso3_codes=countries,
    )
    df = df.dropna(subset=["Value"])
    df["country"] = df["iso3"].astype(str).str.upper()
    df["item_code"] = pd.to_numeric(df["Item Code"], errors="coerce").astype("Int64")

    pivot = df.pivot_table(
        index="country",
        columns="item_code",
        values="Value",
        aggfunc="sum",
        fill_value=0.0,
    ).reindex(columns=list(FRT_ITEM_CODES), fill_value=0.0)

    grape_area = pivot[list(GRAPE_ITEM_CODES)].sum(axis=1)
    tree_nut_area = pivot[list(TREE_NUT_ITEM_CODES)].sum(axis=1)
    apple_area = pivot[list(APPLE_ITEM_CODES)].sum(axis=1)
    aligned_wine_fraction = wine_fraction.reindex(
        pivot.index, fill_value=global_wine_fraction
    )
    excluded_grape = grape_area * aligned_wine_fraction
    # Apples are now modeled directly from CROPGRIDS; their area must be
    # stripped from the FRT pool so it is not double-counted by the trio.
    excluded_area = excluded_grape + tree_nut_area + apple_area
    frt_total_area = pivot.sum(axis=1)

    global_excluded = float(excluded_area.sum())
    global_total = float(frt_total_area.sum())
    global_share = (
        (global_total - global_excluded) / global_total if global_total > 0 else 1.0
    )
    logger.info(
        "Global kept share of FRT area: %.3f (excluded wine-grape=%.2f Mha "
        "+ tree-nut=%.2f Mha + apple=%.2f Mha, FRT total=%.2f Mha)",
        global_share,
        float(excluded_grape.sum()) / 1e6,
        float(tree_nut_area.sum()) / 1e6,
        float(apple_area.sum()) / 1e6,
        global_total / 1e6,
    )

    share = (frt_total_area - excluded_area).where(
        frt_total_area > 0, other=float("nan")
    ) / frt_total_area.where(frt_total_area > 0, other=1.0)
    # Countries with no FAOSTAT FRT reporting (typically very small
    # FRT-raster area to begin with) fall back to the global kept share
    # rather than the no-op default of 1.0.
    share = share.fillna(global_share)

    full_index = pd.Index(sorted(set(countries)), name="country")
    share = share.reindex(full_index, fill_value=global_share)

    out = share.rename("kept_share").reset_index()
    out["kept_share"] = out["kept_share"].astype(float).clip(lower=0.0, upper=1.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    logger.info("Wrote %s (%d countries)", output_path, len(out))


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
