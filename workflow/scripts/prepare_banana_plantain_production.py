# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Derive a corrected banana/plantain production split using FAOSTAT FBS.

FAOSTAT QCL classifies cooking bananas inconsistently across countries:
Nigeria, Burundi, Rwanda and several others report all their production
under "Bananas" (item code 486) with the "Plantains and cooking bananas"
entry (item code 489) missing entirely, even though plantain/cooking
banana is the dominant local food (e.g. Matoke in East Africa, plantain
in Nigeria). FAOSTAT FBS performs its own per-country reconciliation
between items 2615 (Bananas) and 2616 (Plantains), and the FBS split is
substantially more aligned with dietary reality.

The override only acts on suspected misclassifications: countries that
report banana production but no plantain entry in QCL. Countries that
already report both items in QCL are trusted (e.g. Cuba, Uganda).

For candidate countries:

- If FBS has direct data for the country and FBS plantain > 0: use FBS
  Production values directly (e.g. Nigeria: 5.58 Mt banana → 0 banana
  + 3.13 Mt plantain).
- Else, if a proxy country in ``FBS_COUNTRY_FALLBACKS`` has FBS data:
  apply the proxy's plantain share to the country's QCL banana+plantain
  total (e.g. Burundi → Rwanda 45% plantain).
- Else: keep QCL values unchanged.

Output schema matches ``faostat_crop_production.csv`` so the downstream
rule can apply it as a row-level override for banana and plantain.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.faostat_bulk import (
    FBS_COUNTRY_FALLBACKS,
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# FBS item codes
FBS_BANANA_ITEM_CODE = 2615
FBS_PLANTAIN_ITEM_CODE = 2616

# QCL item codes
QCL_BANANA_ITEM_CODE = 486
QCL_PLANTAIN_ITEM_CODE = 489


def _load_fbs_production(
    fbs_csv: str,
    m49_codes: str,
    year: int,
    countries: list[str],
    fbs_production_element_code: int,
) -> pd.DataFrame:
    """Return a (country, banana_kt, plantain_kt) frame from FBS Production.

    Values are in 1000 t (kt) as reported by FBS. NaNs are treated as 0.
    """
    bulk = load_bulk(fbs_csv)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    fbs = filter_bulk(
        bulk,
        element_codes=[fbs_production_element_code],
        item_codes=[FBS_BANANA_ITEM_CODE, FBS_PLANTAIN_ITEM_CODE],
        years=[year],
        iso3_codes=countries,
    )

    if fbs.empty:
        raise RuntimeError("FAOSTAT FBS returned no banana/plantain production rows")

    fbs["country"] = fbs["iso3"].astype(str).str.upper()
    fbs["Value"] = pd.to_numeric(fbs["Value"], errors="coerce").fillna(0.0)

    pivot = fbs.pivot_table(
        index="country", columns="Item Code", values="Value", aggfunc="sum"
    )
    pivot = pivot.rename(
        columns={
            FBS_BANANA_ITEM_CODE: "fbs_banana_kt",
            FBS_PLANTAIN_ITEM_CODE: "fbs_plantain_kt",
        }
    ).reset_index()
    for col in ("fbs_banana_kt", "fbs_plantain_kt"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    return pivot[["country", "fbs_banana_kt", "fbs_plantain_kt"]].fillna(0.0)


def _load_qcl_totals(
    qcl_csv: str,
    m49_codes: str,
    year: int,
    countries: list[str],
    qcl_production_element_code: int,
) -> pd.DataFrame:
    """Return per-country QCL banana and plantain production in tonnes."""
    bulk = load_bulk(qcl_csv)
    m49_to_iso3 = load_m49_to_iso3(m49_codes)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    qcl = filter_bulk(
        bulk,
        element_codes=[qcl_production_element_code],
        item_codes=[QCL_BANANA_ITEM_CODE, QCL_PLANTAIN_ITEM_CODE],
        years=[year],
        iso3_codes=countries,
    )

    qcl["country"] = qcl["iso3"].astype(str).str.upper()
    qcl["Value"] = pd.to_numeric(qcl["Value"], errors="coerce").fillna(0.0)

    pivot = qcl.pivot_table(
        index="country", columns="Item Code", values="Value", aggfunc="sum"
    )
    pivot = pivot.rename(
        columns={
            QCL_BANANA_ITEM_CODE: "qcl_banana_t",
            QCL_PLANTAIN_ITEM_CODE: "qcl_plantain_t",
        }
    ).reset_index()
    for col in ("qcl_banana_t", "qcl_plantain_t"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    return pivot[["country", "qcl_banana_t", "qcl_plantain_t"]].fillna(0.0)


def _compute_plantain_share(fbs_row: pd.Series) -> float | None:
    """Plantain / (Banana + Plantain) FBS share, or None when both are 0."""
    b = float(fbs_row.get("fbs_banana_kt", 0.0) or 0.0)
    p = float(fbs_row.get("fbs_plantain_kt", 0.0) or 0.0)
    total = b + p
    if total <= 0:
        return None
    return p / total


def main() -> None:
    fbs_csv = snakemake.input.fbs_csv  # type: ignore[name-defined]
    qcl_csv = snakemake.input.qcl_csv  # type: ignore[name-defined]
    m49_codes = snakemake.input.m49_codes  # type: ignore[name-defined]
    output_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    countries = [str(c).upper() for c in snakemake.params.countries]  # type: ignore[name-defined]
    year = int(snakemake.params.production_year)  # type: ignore[name-defined]
    fbs_production_element_code = int(
        snakemake.params.fbs_production_element_code  # type: ignore[name-defined]
    )
    qcl_production_element_code = int(
        snakemake.params.qcl_production_element_code  # type: ignore[name-defined]
    )

    # FBS may not cover all proxies; include them in the FBS fetch so we can
    # use proxy splits where direct data is missing.
    all_proxies: set[str] = set()
    for proxies in FBS_COUNTRY_FALLBACKS.values():
        all_proxies.update(proxies)
    fbs_countries = sorted(set(countries) | all_proxies)

    fbs = _load_fbs_production(
        fbs_csv, m49_codes, year, fbs_countries, fbs_production_element_code
    )
    qcl = _load_qcl_totals(
        qcl_csv, m49_codes, year, countries, qcl_production_element_code
    )

    fbs_by_country = fbs.set_index("country")
    qcl_by_country = qcl.set_index("country")

    records: list[dict] = []
    counts = {"keep_qcl": 0, "fbs_direct": 0, "proxy": 0, "no_signal": 0, "empty": 0}

    for country in countries:
        qcl_b = (
            float(qcl_by_country.loc[country, "qcl_banana_t"])
            if country in qcl_by_country.index
            else 0.0
        )
        qcl_p = (
            float(qcl_by_country.loc[country, "qcl_plantain_t"])
            if country in qcl_by_country.index
            else 0.0
        )
        qcl_total = qcl_b + qcl_p

        if qcl_total <= 0 and country not in fbs_by_country.index:
            counts["empty"] += 1
            continue

        # The override only acts on suspected QCL misclassifications:
        # countries with QCL banana production but no QCL plantain entry
        # (a common signal that "Bananas" actually contains plantain mass,
        # e.g. Nigeria, Burundi, Rwanda). Countries that already report
        # both items in QCL are trusted.
        if qcl_p > 0:
            new_banana = qcl_b
            new_plantain = qcl_p
            counts["keep_qcl"] += 1
        elif qcl_b > 0:
            # QCL banana with no plantain entry — candidate for reclassification.
            new_banana = qcl_b
            new_plantain = 0.0
            if country in fbs_by_country.index:
                row = fbs_by_country.loc[country]
                fbs_b = float(row.get("fbs_banana_kt", 0.0) or 0.0) * 1000.0
                fbs_p = float(row.get("fbs_plantain_kt", 0.0) or 0.0) * 1000.0
                if fbs_p > 0:
                    # FBS reconciles plantain explicitly; trust FBS values.
                    new_banana = fbs_b
                    new_plantain = fbs_p
                    counts["fbs_direct"] += 1
                else:
                    counts["no_signal"] += 1
            else:
                share = None
                for proxy in FBS_COUNTRY_FALLBACKS.get(country, []):
                    if proxy in fbs_by_country.index:
                        share = _compute_plantain_share(fbs_by_country.loc[proxy])
                        if share is not None and share > 0:
                            logger.info(
                                "Using %s FBS plantain share %.2f for %s (no QCL plantain entry)",
                                proxy,
                                share,
                                country,
                            )
                            counts["proxy"] += 1
                            break
                if share is not None and share > 0:
                    new_plantain = share * qcl_total
                    new_banana = (1.0 - share) * qcl_total
                else:
                    counts["no_signal"] += 1
        else:
            # No QCL banana or plantain — country in FBS only. Skip override
            # (the QCL row simply doesn't exist and we have nothing to fix).
            counts["no_signal"] += 1
            continue

        records.append(
            {
                "country": country,
                "crop": "banana",
                "year": year,
                "production_tonnes": new_banana,
            }
        )
        records.append(
            {
                "country": country,
                "crop": "plantain",
                "year": year,
                "production_tonnes": new_plantain,
            }
        )

    logger.info(
        "Banana/plantain split decisions: %d keep QCL (plantain reported), "
        "%d FBS-direct (Nigeria-like), %d proxy share, %d no signal, %d empty",
        counts["keep_qcl"],
        counts["fbs_direct"],
        counts["proxy"],
        counts["no_signal"],
        counts["empty"],
    )

    result = pd.DataFrame.from_records(records).sort_values(["country", "crop"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
