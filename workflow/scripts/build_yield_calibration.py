# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-(country, crop) yield calibration anchored on FBS-corrected FAOSTAT.

For each crop listed under ``config[yield_calibration].crops``, computes
a per-country multiplier such that the model's country-level production
matches the FBS-corrected FAOSTAT production target:

    multiplier_c = target_production_c / model_current_production_c

with both sides on fresh-weight basis (DM-to-fresh conversion cancels in
the ratio, but is applied here so the intermediate values logged for
diagnostics are interpretable). The multiplier is clipped to
``[multiplier_min, multiplier_max]`` for numerical safety.

Output schema matches ``fodder_yield_corrections.csv`` so ``build_model``
applies both corrections through the same per-cell yield-rescaling
mechanism: ``country, crop, yield_correction_factor``.

Use this calibration when a crop's GAEZ yield raster is a proxy (e.g.
plantain uses the banana raster, since GAEZ has no separate plantain
output) and the resulting country-level production systematically
deviates from FAOSTAT. The model build only applies the corrections when
``validation.use_actual_yields`` is true; in optimisation mode the GAEZ
potential yields are kept as-is so the model can express expansion
beyond historical realisations.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from workflow.scripts.build_fodder_yield_corrections import _load_tidy_variable
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _gaez_country_production_dm(
    yield_paths: dict[str, str],
    area_paths: dict[str, str],
    region_to_country: pd.Series,
) -> pd.Series:
    """Per-country GAEZ-derived production in dry-matter tonnes.

    Computed as ``sum over cells of (harvested_area_ha * yield_t_per_ha_dm)``
    aggregated to country level across all provided water supplies.
    """
    records = []
    for ws, yield_path in yield_paths.items():
        yield_df = _load_tidy_variable(yield_path, "yield", "yield_t_per_ha_dm")
        area_path = area_paths.get(ws)
        if not area_path:
            continue
        area_df = _load_tidy_variable(area_path, "harvested_area", "area_ha")
        merged = yield_df.merge(area_df, on=["region", "resource_class"], how="inner")
        if merged.empty:
            continue
        merged["country"] = merged["region"].map(region_to_country)
        merged = merged.dropna(subset=["country"])
        merged["prod_dm_t"] = merged["yield_t_per_ha_dm"] * merged["area_ha"]
        records.append(merged[["country", "prod_dm_t"]])
    if not records:
        return pd.Series(dtype=float, name="prod_dm_t")
    return pd.concat(records, ignore_index=True).groupby("country")["prod_dm_t"].sum()


def _faostat_target_production_fresh(faostat_df: pd.DataFrame, crop: str) -> pd.Series:
    """Per-country FBS-corrected FAOSTAT production in fresh tonnes."""
    df = faostat_df[faostat_df["crop"] == crop]
    if df.empty:
        return pd.Series(dtype=float, name="target_fresh_t")
    s = (
        df.set_index("country")["production_tonnes"]
        .astype(float)
        .rename("target_fresh_t")
    )
    return s[s > 0]


def _compute_crop_multipliers(
    crop: str,
    yield_paths: dict[str, str],
    area_paths: dict[str, str],
    moisture_fraction: float,
    region_to_country: pd.Series,
    faostat_df: pd.DataFrame,
    multiplier_min: float,
    multiplier_max: float,
) -> pd.DataFrame:
    """Return tidy rows ``(country, crop, yield_correction_factor)`` for one crop."""
    model_prod_dm = _gaez_country_production_dm(
        yield_paths, area_paths, region_to_country
    )
    if model_prod_dm.empty:
        logger.warning("%s: no GAEZ yield/area rows; skipping calibration", crop)
        return pd.DataFrame(columns=["country", "crop", "yield_correction_factor"])

    model_prod_fresh = model_prod_dm / max(1e-9, 1.0 - moisture_fraction)
    model_prod_fresh.name = "model_fresh_t"

    target = _faostat_target_production_fresh(faostat_df, crop)
    if target.empty:
        logger.warning("%s: no FAOSTAT target production rows; skipping", crop)
        return pd.DataFrame(columns=["country", "crop", "yield_correction_factor"])

    joined = (
        pd.concat([target, model_prod_fresh], axis=1, join="inner")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if joined.empty:
        logger.warning(
            "%s: no countries with both target and model production; skipping", crop
        )
        return pd.DataFrame(columns=["country", "crop", "yield_correction_factor"])

    raw = joined["target_fresh_t"] / joined["model_fresh_t"]
    clipped = raw.clip(multiplier_min, multiplier_max)
    n_clipped = int(((raw < multiplier_min) | (raw > multiplier_max)).sum())
    if n_clipped:
        logger.info(
            "%s: clipped %d/%d multipliers to [%.2f, %.2f] (raw range [%.3f, %.3f])",
            crop,
            n_clipped,
            len(raw),
            multiplier_min,
            multiplier_max,
            float(raw.min()),
            float(raw.max()),
        )

    out = pd.DataFrame(
        {
            "country": clipped.index.astype(str),
            "crop": crop,
            "yield_correction_factor": clipped.values.round(4),
        }
    )
    logger.info(
        "%s: %d corrections, median=%.3f, range=[%.3f, %.3f]",
        crop,
        len(out),
        float(clipped.median()),
        float(clipped.min()),
        float(clipped.max()),
    )
    return out


if __name__ == "__main__":
    logger = setup_script_logging(  # type: ignore[assignment]
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    crops: list[str] = list(snakemake.params.crops)  # type: ignore[name-defined]
    multiplier_min = float(snakemake.params.multiplier_min)  # type: ignore[name-defined]
    multiplier_max = float(snakemake.params.multiplier_max)  # type: ignore[name-defined]
    moisture_by_crop = dict(snakemake.params.moisture_by_crop)  # type: ignore[name-defined]
    regions_path = str(snakemake.input.regions)  # type: ignore[name-defined]
    faostat_path = str(snakemake.input.faostat_production)  # type: ignore[name-defined]
    out_path = str(snakemake.output[0])  # type: ignore[name-defined]

    if not crops:
        pd.DataFrame(columns=["country", "crop", "yield_correction_factor"]).to_csv(
            out_path, index=False
        )
        logger.info("yield_calibration.crops is empty; wrote empty corrections CSV")
    else:
        regions = gpd.read_file(regions_path)[["region", "country"]]
        region_to_country = regions.set_index("region")["country"].astype(str)
        faostat_df = pd.read_csv(faostat_path)

        input_keys = set(snakemake.input.keys())  # type: ignore[name-defined]
        results: list[pd.DataFrame] = []
        for crop in crops:
            if crop not in moisture_by_crop:
                raise KeyError(
                    f"yield_calibration: no moisture entry for {crop}; "
                    "add a row to data/curated/crop_moisture_content.csv"
                )
            yield_paths: dict[str, str] = {}
            area_paths: dict[str, str] = {}
            for ws in ("r", "i"):
                yk = f"gaez_yield_{crop}_{ws}"
                ak = f"gaez_harvested_{crop}_{ws}"
                if yk in input_keys:
                    yield_paths[ws] = snakemake.input[yk]  # type: ignore[name-defined]
                if ak in input_keys:
                    area_paths[ws] = snakemake.input[ak]  # type: ignore[name-defined]
            if not yield_paths:
                logger.warning(
                    "%s: no GAEZ yield inputs wired; skipping calibration", crop
                )
                continue
            df = _compute_crop_multipliers(
                crop,
                yield_paths,
                area_paths,
                moisture_by_crop[crop],
                region_to_country,
                faostat_df,
                multiplier_min,
                multiplier_max,
            )
            if not df.empty:
                results.append(df)

        if results:
            out = pd.concat(results, ignore_index=True).sort_values(["country", "crop"])
        else:
            out = pd.DataFrame(columns=["country", "crop", "yield_correction_factor"])
        out.to_csv(out_path, index=False)
        logger.info("Wrote %d yield-calibration rows to %s", len(out), out_path)
