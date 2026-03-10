# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute per-country yield correction factors for FDD (fodder) crops.

Compares Eurostat-reported yields (production / area, converted to dry matter)
with GAEZ area-weighted average DM yields to derive multiplicative correction
factors.  For silage-maize, uses Eurostat G3000 directly; for alfalfa, uses
(G0000 - G3000) as a proxy for all non-maize green fodder.

Output: CSV with columns (country, crop, yield_correction_factor)
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_tidy_variable(path: str, variable: str, value_name: str) -> pd.DataFrame:
    """Load a tidy regional CSV and return (region, resource_class, value) rows."""
    df = pd.read_csv(path, comment="#")
    if df.empty:
        return pd.DataFrame(columns=["region", "resource_class", value_name])

    if "variable" in df.columns and "value" in df.columns:
        df = df[df["variable"] == variable].copy()
        if df.empty:
            return pd.DataFrame(columns=["region", "resource_class", value_name])
        df = df.rename(columns={"value": value_name})
    elif value_name in df.columns:
        df = df.copy()
    else:
        raise ValueError(
            f"{path} must contain either tidy columns (variable,value) "
            f"or '{value_name}'"
        )

    cols = ["region", "resource_class", value_name]
    return df[cols]


def compute_gaez_country_yield(
    yield_paths: dict[str, str],
    area_paths: dict[str, str],
    region_to_country: pd.Series,
) -> pd.Series:
    """Compute GAEZ area-weighted average DM yield per country.

    Parameters
    ----------
    yield_paths : {water_supply: path} for yield CSVs
    area_paths : {water_supply: path} for harvested area CSVs
    region_to_country : region → ISO3 mapping

    Returns
    -------
    Series indexed by country with area-weighted mean yield (t DM/ha).
    """
    records = []
    for ws, yield_path in yield_paths.items():
        yield_df = _load_tidy_variable(yield_path, "yield", "yield_t_per_ha")
        area_path = area_paths.get(ws)
        if area_path:
            area_df = _load_tidy_variable(area_path, "harvested_area", "area_ha")
        else:
            area_df = _load_tidy_variable(yield_path, "suitable_area", "area_ha")

        merged = yield_df.merge(area_df, on=["region", "resource_class"], how="inner")
        if merged.empty:
            continue
        merged["country"] = merged["region"].map(region_to_country)
        merged = merged[merged["country"].notna()]
        if merged.empty:
            continue
        merged["weighted_yield"] = merged["yield_t_per_ha"] * merged["area_ha"]
        records.append(merged[["country", "weighted_yield", "area_ha"]])

    if not records:
        return pd.Series(dtype=float, name="gaez_yield_t_per_ha")

    combined = pd.concat(records, ignore_index=True)
    grouped = combined.groupby("country").agg(
        weighted_yield=("weighted_yield", "sum"),
        total_area=("area_ha", "sum"),
    )
    result = grouped["weighted_yield"] / grouped["total_area"]
    result = result.replace([np.inf, -np.inf], np.nan).dropna()
    result.name = "gaez_yield_t_per_ha"
    return result


def compute_eurostat_dm_yields(
    eurostat_df: pd.DataFrame, eurostat_moisture: float
) -> pd.DataFrame:
    """Compute Eurostat DM yields per country per model crop.

    Returns DataFrame with columns: country, crop, eurostat_dm_yield_t_per_ha
    """
    pivot_prod = eurostat_df.pivot_table(
        index="country", columns="crop_code", values="production_1000t", aggfunc="sum"
    ).fillna(0.0)
    pivot_area = eurostat_df.pivot_table(
        index="country", columns="crop_code", values="area_1000ha", aggfunc="sum"
    ).fillna(0.0)

    dm_fraction = 1.0 - eurostat_moisture
    records = []

    for country in pivot_prod.index:
        # Silage-maize: G3000
        g3000_prod = pivot_prod.loc[country].get("G3000", 0.0)
        g3000_area = pivot_area.loc[country].get("G3000", 0.0)
        if g3000_area > 0 and g3000_prod > 0:
            # production in 1000t, area in 1000ha → t/ha
            maize_dm_yield = (g3000_prod / g3000_area) * dm_fraction
            records.append(
                {
                    "country": country,
                    "crop": "silage-maize",
                    "eurostat_dm_yield_t_per_ha": maize_dm_yield,
                }
            )

        # Alfalfa: (G0000 - G3000) as proxy for non-maize green fodder
        g0000_prod = pivot_prod.loc[country].get("G0000", 0.0)
        g0000_area = pivot_area.loc[country].get("G0000", 0.0)
        alf_prod = g0000_prod - g3000_prod
        alf_area = g0000_area - g3000_area
        if alf_area > 0 and alf_prod > 0:
            alf_dm_yield = (alf_prod / alf_area) * dm_fraction
            records.append(
                {
                    "country": country,
                    "crop": "alfalfa",
                    "eurostat_dm_yield_t_per_ha": alf_dm_yield,
                }
            )

    return pd.DataFrame(records)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    eurostat_path = str(snakemake.input.eurostat_fodder)  # type: ignore[name-defined]
    regions_path = str(snakemake.input.regions)  # type: ignore[name-defined]
    out_path = str(snakemake.output[0])  # type: ignore[name-defined]

    fdd_crops = list(snakemake.params.fdd_crops)  # type: ignore[name-defined]
    eurostat_moisture = float(snakemake.params.eurostat_moisture)  # type: ignore[name-defined]
    floor = float(snakemake.params.floor)  # type: ignore[name-defined]
    ceiling = float(snakemake.params.ceiling)  # type: ignore[name-defined]

    # Load regions for region→country mapping
    regions = gpd.read_file(regions_path)[["region", "country"]]
    region_to_country = regions.set_index("region")["country"].astype(str)

    # Load Eurostat data and compute DM yields
    eurostat_df = pd.read_csv(eurostat_path)
    eurostat_df["country"] = eurostat_df["country"].astype(str).str.upper()
    eurostat_yields = compute_eurostat_dm_yields(eurostat_df, eurostat_moisture)

    if eurostat_yields.empty:
        logger.warning(
            "No Eurostat yield data available; writing empty corrections CSV"
        )
        pd.DataFrame(columns=["country", "crop", "yield_correction_factor"]).to_csv(
            out_path, index=False
        )
    else:
        # Compute GAEZ country-level average DM yields per crop
        input_keys = set(snakemake.input.keys())  # type: ignore[name-defined]
        results = []
        for crop in fdd_crops:
            yield_paths = {}
            area_paths = {}
            for ws in ("r", "i"):
                yield_key = f"gaez_yield_{crop}_{ws}"
                area_key = f"gaez_harvested_{crop}_{ws}"
                if yield_key in input_keys:
                    yield_paths[ws] = snakemake.input[yield_key]  # type: ignore[name-defined]
                if area_key in input_keys:
                    area_paths[ws] = snakemake.input[area_key]  # type: ignore[name-defined]

            if not yield_paths:
                logger.warning("No GAEZ yield data for crop %s", crop)
                continue

            gaez_yields = compute_gaez_country_yield(
                yield_paths, area_paths, region_to_country
            )

            crop_eurostat = eurostat_yields[eurostat_yields["crop"] == crop].set_index(
                "country"
            )
            common = gaez_yields.index.intersection(crop_eurostat.index)
            if common.empty:
                logger.warning("No overlapping countries for crop %s", crop)
                continue

            for country in common:
                gaez_y = gaez_yields[country]
                euro_y = crop_eurostat.loc[country, "eurostat_dm_yield_t_per_ha"]
                if gaez_y <= 0:
                    continue
                factor = np.clip(euro_y / gaez_y, floor, ceiling)
                results.append(
                    {
                        "country": country,
                        "crop": crop,
                        "yield_correction_factor": round(float(factor), 4),
                    }
                )

        corrections = pd.DataFrame(results)
        corrections = corrections.sort_values(["country", "crop"]).reset_index(
            drop=True
        )
        corrections.to_csv(out_path, index=False)
        logger.info(
            "Wrote %d fodder yield corrections to %s", len(corrections), out_path
        )
        if not corrections.empty:
            for crop in corrections["crop"].unique():
                crop_df = corrections[corrections["crop"] == crop]
                logger.info(
                    "  %s: median factor=%.3f, range=[%.3f, %.3f] (%d countries)",
                    crop,
                    crop_df["yield_correction_factor"].median(),
                    crop_df["yield_correction_factor"].min(),
                    crop_df["yield_correction_factor"].max(),
                    len(crop_df),
                )
