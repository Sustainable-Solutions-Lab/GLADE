# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute olive's per-country share of FAOSTAT-reported OOC-module area.

The GAEZ Module VI (RES06) harvested-area raster used for the olive crop is
reported at the OOC group level ("Olives and other minor oil crops"), which
bundles olive land with linseed, mustard, safflower, castor, poppy, melon-,
hempseed and several minor tree-oil crops. Olive is the only OOC member the
model represents, so without a deflation step the entire OOC area is
attributed to olive.

For each country, this script computes::

    olive_share = area_harvested[Olives] / Σ area_harvested[OOC items]

from FAOSTAT QCL "Area harvested" (element 5312) for the configured baseline
year. Countries reporting no OOC area fall back to the global olive share.
The output is consumed by ``build_harvested_area.py`` to deflate the olive
raster area before per-country attribution.
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

# FAOSTAT QCL element code for "Area harvested" (hectares).
AREA_HARVESTED_ELEMENT_CODE = 5312

# FAOSTAT QCL item codes for the GAEZ Module VI "OOC" group (Appendix 4-1,
# FAOSTAT_Crops sheet, Group_Module_VI == 21).
OLIVE_ITEM_CODE = 260
OOC_OTHER_ITEM_CODES: tuple[int, ...] = (
    333,  # Linseed
    292,  # Mustard seed
    280,  # Safflower seed
    265,  # Castor oil seeds
    296,  # Poppy seed
    299,  # Melonseed
    336,  # Hempseed
    339,  # Other oil seeds, n.e.c.
    263,  # Karite nuts (sheanuts)
    275,  # Tung nuts
    277,  # Jojoba seeds
    305,  # Tallowtree seeds
)
OOC_ITEM_CODES: tuple[int, ...] = (OLIVE_ITEM_CODE, *OOC_OTHER_ITEM_CODES)


def main() -> None:
    qcl_path = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    baseline_year = int(snakemake.params.baseline_year)  # type: ignore[name-defined]

    logger.info("Loading FAOSTAT QCL bulk data")
    bulk = load_bulk(qcl_path)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    df = filter_bulk(
        bulk,
        element_codes=[AREA_HARVESTED_ELEMENT_CODE],
        item_codes=list(OOC_ITEM_CODES),
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
    ).reindex(columns=list(OOC_ITEM_CODES), fill_value=0.0)

    olive_area = pivot[OLIVE_ITEM_CODE]
    total_area = pivot.sum(axis=1)

    global_olive = float(olive_area.sum())
    global_total = float(total_area.sum())
    global_share = global_olive / global_total if global_total > 0 else 1.0
    logger.info(
        "Global olive share of OOC area: %.3f (olive=%.1f Mha, OOC total=%.1f Mha)",
        global_share,
        global_olive / 1e6,
        global_total / 1e6,
    )

    share = olive_area.where(total_area > 0, other=float("nan")) / total_area.where(
        total_area > 0, other=1.0
    )
    # Countries with no FAOSTAT OOC reporting fall back to the global share so
    # that GAEZ-attributed olive area in such places (typically very small) is
    # still deflated by a plausible factor rather than left at 1.0.
    share = share.fillna(global_share)

    # Include every requested country: those absent from QCL get the fallback.
    full_index = pd.Index(sorted(set(countries)), name="country")
    share = share.reindex(full_index, fill_value=global_share)

    out = share.rename("olive_share").reset_index()
    out["olive_share"] = out["olive_share"].astype(float).clip(lower=0.0, upper=1.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    logger.info("Wrote %s (%d countries)", output_path, len(out))


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
