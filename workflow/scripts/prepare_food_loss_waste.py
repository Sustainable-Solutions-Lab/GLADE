#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Process SDG 12.3.1 food loss and waste data from UNSD bulk CSV.

Reads pre-filtered bulk CSV from the UNSD SDG Indicators Database for:
- SDG 12.3.1(a): Food loss percentage by country and product type (AG_FLS_PCT)
- SDG 12.3.1(b): Food waste per capita by country and sector (AG_FOOD_WST_PC)

Maps UN SDG food categories to internal model food groups, and converts
food waste from kg/person/year to fractions relative to food supply.

For dairy specifically, the SDG "animal products" loss rate is too high,
so we calculate implicit loss from FAOSTAT production vs food supply data.

Input:
    - SDG bulk CSV (pre-filtered to food loss/waste series)
    - M49 codes (for regional mapping)
    - FAOSTAT animal production data (for dairy loss calculation)
    - FAOSTAT food supply data (for dairy loss calculation)
    - Countries list from config
    - Food groups list from config
    - Reference year from config (for FAOSTAT food supply data)

Output:
    - CSV with columns: country, food_group, loss_fraction, waste_fraction
"""

import logging
import sys

import pandas as pd
import pycountry

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    filter_bulk,
    load_bulk,
    load_m49_to_iso3,
)
from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

FALLBACK_FOOD_SUPPLY: dict[str, list[str]] = {
    # Map territories or small countries to a proxy with similar dietary patterns.
    "ASM": ["USA"],  # American Samoa -> United States
    "BRN": ["MYS", "SGP"],  # Brunei -> Malaysia / Singapore
    "BTN": ["NPL", "IND"],  # Bhutan -> Nepal / India
    "ERI": ["ETH"],  # Eritrea -> Ethiopia
    "GNQ": ["GAB", "CMR"],  # Equatorial Guinea -> Gabon / Cameroon
    "GUF": ["GUY", "SUR"],  # French Guiana -> Guyana / Suriname
    "PRI": ["USA"],  # Puerto Rico -> United States
    "PSE": ["ISR", "JOR"],  # Palestine -> Israel / Jordan
    "SSD": ["SDN", "ETH"],  # South Sudan -> Sudan / Ethiopia
}

UN_TO_MODEL_FOOD_GROUPS: dict[str, list[str] | None] = {
    "CRL_PUL": ["grain", "whole_grains", "legumes", "stimulants"],
    "FRT_VGT": ["fruits", "vegetables"],
    "RT_TBR": ["starchy_vegetable", "oil", "nuts_seeds"],
    "ANMPROD": ["red_meat", "poultry", "dairy", "eggs"],
    "FSH_FSHPROD": None,  # fish/seafood not modelled
    "ALP": None,
}

MODEL_GROUP_TO_PRODUCT: dict[str, str] = {
    group: product
    for product, groups in UN_TO_MODEL_FOOD_GROUPS.items()
    if groups
    for group in groups
}


def load_m49_regions(m49_file: str) -> dict[str, dict]:
    """Load UN M49 region mappings.

    Args:
        m49_file: Path to M49 CSV file

    Returns:
        Dict mapping ISO3 code to region info (region_code, subregion_code, etc.)
    """
    df = pd.read_csv(m49_file, sep=";", encoding="utf-8-sig", comment="#")

    # Build mapping from ISO3 to region info
    mapping = {}
    for _, row in df.iterrows():
        iso3 = row["ISO-alpha3 Code"]
        if pd.notna(iso3):
            # Convert numeric codes to integers first to remove .0 suffix
            region_code = None
            if pd.notna(row["Region Code"]):
                region_code = str(int(float(row["Region Code"])))

            subregion_code = None
            if pd.notna(row["Sub-region Code"]):
                subregion_code = str(int(float(row["Sub-region Code"])))

            mapping[iso3] = {
                "m49_code": str(int(float(row["M49 Code"]))),
                "region_code": region_code,
                "region_name": row["Region Name"]
                if pd.notna(row["Region Name"])
                else None,
                "subregion_code": subregion_code,
                "subregion_name": row["Sub-region Name"]
                if pd.notna(row["Sub-region Name"])
                else None,
            }

    return mapping


def iso3_to_m49(iso3: str) -> str | None:
    """Convert ISO3 country code to M49 numeric code.

    Args:
        iso3: ISO 3166-1 alpha-3 country code (e.g., "USA")

    Returns:
        M49 numeric code as string, or None if not found
    """
    try:
        country = pycountry.countries.get(alpha_3=iso3)
        return country.numeric if country else None
    except (KeyError, AttributeError):
        return None


def load_sdg_series(csv_path: str, series_code: str) -> pd.DataFrame:
    """Load a specific SDG series from the pre-filtered bulk CSV.

    Args:
        csv_path: Path to the extracted SDG CSV (filtered to food loss/waste series)
        series_code: UNSD series code (e.g., "AG_FLS_PCT")

    Returns:
        DataFrame with all observations for the series
    """
    logger.info("Loading SDG series %s from %s", series_code, csv_path)

    df = pd.read_csv(csv_path, low_memory=False)
    df = df[df["SeriesCode"] == series_code].copy()

    if df.empty:
        logger.error("No data found for series %s in %s", series_code, csv_path)
        sys.exit(1)

    logger.info("Loaded %d observations for %s", len(df), series_code)

    return df


def process_food_loss_data(
    df: pd.DataFrame,
    countries: list[str],
    food_groups: list[str],
    m49_regions: dict[str, dict],
) -> pd.DataFrame:
    """Process food loss percentage data using regional aggregates.

    Food loss data is only available at regional/sub-regional level, not country level.
    We map each country to its UN M49 sub-region and use the regional average.

    Args:
        df: Raw UNSD data for AG_FLS_PCT series
        countries: List of ISO3 country codes
        food_groups: List of internal food group names
        m49_regions: Mapping of ISO3 to region info

    Returns:
        DataFrame with columns: country, food_group, loss_fraction, year
    """
    # Product type is a direct column in the bulk CSV
    df["product_type"] = df["Type of product"]

    # Parse year and value
    df["year"] = pd.to_numeric(df["TimePeriod"], errors="coerce")
    df["value_numeric"] = pd.to_numeric(df["Value"], errors="coerce")

    # Build lookup of available regional data: {region_code: {product_type: {year: value}}}
    regional_data = {}
    for _, row in df.iterrows():
        region_code = str(row["GeoAreaCode"])
        product_type = row["product_type"]
        year = row["year"]
        value = row["value_numeric"]

        if pd.isna(value) or pd.isna(year):
            continue

        if region_code not in regional_data:
            regional_data[region_code] = {}
        if product_type not in regional_data[region_code]:
            regional_data[region_code][product_type] = {}

        regional_data[region_code][product_type][year] = value

    logger.info("Found food loss data for %d regions", len(regional_data))

    product_type_summary = {
        region: {ptype for ptype in types if ptype}
        for region, types in regional_data.items()
    }
    available_types = {
        ptype for types in product_type_summary.values() for ptype in types if ptype
    }
    if available_types:
        logger.info(
            "Food loss product types reported: %s",
            ", ".join(sorted(available_types)),
        )
    else:
        logger.warning("No product-specific food loss breakdown found in UNSD response")

    regions_with_breakdown = sum(
        1
        for types in product_type_summary.values()
        if any(ptype and ptype != "ALP" for ptype in types)
    )
    total_regions = len(product_type_summary)
    logger.info(
        "Regions with product-specific breakdown: %d/%d",
        regions_with_breakdown,
        total_regions,
    )

    # Derive world-level product correction factors for disaggregation
    global_shares: dict[str, float] = {}
    world_alp_value: float | None = None
    world_data = df[df["GeoAreaCode"].astype(str) == "1"]
    if not world_data.empty:
        latest_world_year = world_data["year"].max()
        world_latest = world_data[world_data["year"] == latest_world_year]
        product_values: dict[str, float] = {}
        for _, row in world_latest.iterrows():
            ptype = row["product_type"]
            value = row["value_numeric"]
            if ptype == "ALP" and pd.notna(value):
                world_alp_value = value
            if pd.isna(value) or ptype is None or ptype == "ALP":
                continue
            product_values[ptype] = value

        if world_alp_value and world_alp_value > 0:
            global_shares = {
                ptype: v / world_alp_value
                for ptype, v in product_values.items()
                if v > 0 and ptype
            }
            logger.info(
                "Using world food loss shares (%d) for fallback disaggregation: %s",
                int(latest_world_year),
                ", ".join(
                    f"{ptype}={share:.2f}" for ptype, share in global_shares.items()
                ),
            )
    if not global_shares:
        logger.warning(
            "World food loss shares unavailable; ALP totals will remain un-disaggregated"
        )

    global_group_corrections: dict[str, float] = {}
    if global_shares:
        for group, product in MODEL_GROUP_TO_PRODUCT.items():
            ratio = global_shares.get(product)
            if ratio is not None:
                global_group_corrections[group] = ratio
    for group in food_groups:
        global_group_corrections.setdefault(group, 1.0)

    if global_group_corrections:
        logger.info(
            "Applying global loss correction factors per food group: %s",
            ", ".join(
                f"{group}={factor:.2f}"
                for group, factor in sorted(global_group_corrections.items())
            ),
        )

    results = []

    for country_code in countries:
        # Get region info for this country
        region_info = m49_regions.get(country_code)
        if not region_info:
            logger.warning("No M49 region info for %s", country_code)
            continue

        # Try sub-region first, then region
        region_code = None
        if (
            region_info["subregion_code"]
            and region_info["subregion_code"] in regional_data
        ):
            region_code = region_info["subregion_code"]
        elif region_info["region_code"] and region_info["region_code"] in regional_data:
            region_code = region_info["region_code"]

        if not region_code:
            # No regional data available for this country
            continue
        region_entries = regional_data[region_code]

        alp_year_data = region_entries.get("ALP")
        if not alp_year_data:
            continue

        latest_year = max(alp_year_data.keys())
        loss_pct = alp_year_data[latest_year]
        if pd.isna(loss_pct):
            continue

        base_loss_fraction = loss_pct / 100.0

        for food_group in food_groups:
            correction = global_group_corrections.get(food_group, 1.0)
            loss_fraction = base_loss_fraction * correction
            results.append(
                {
                    "country": country_code,
                    "food_group": food_group,
                    "loss_fraction": loss_fraction,
                    "year": int(latest_year),
                }
            )

    return pd.DataFrame(results)


def fetch_faostat_food_supply(
    countries: list[str],
    reference_year: int,
    fbs_csv: str,
    m49_csv: str,
    fbs_element_code: int | str,
) -> pd.DataFrame:
    """Read total food supply per capita from FAOSTAT FBS bulk CSV.

    Args:
        countries: List of ISO3 country codes
        reference_year: Year for which to retrieve data
        fbs_csv: Path to extracted FAOSTAT FBS bulk CSV
        m49_csv: Path to M49 codes CSV for ISO3 mapping
        fbs_element_code: FAOSTAT element code for food supply quantity

    Returns:
        DataFrame with columns: country (ISO3), food_supply_g_day
    """
    logger.info("Reading FAOSTAT food supply data for %d", reference_year)

    bulk = load_bulk(fbs_csv)

    elem_code = int(fbs_element_code)

    # Add ISO3 column
    m49_to_iso3 = load_m49_to_iso3(m49_csv)
    bulk = add_iso3_column(bulk, m49_to_iso3)

    target_countries = [code.upper() for code in countries]

    # Include fallback countries in the filter
    all_fallback_countries = set()
    for proxies in FALLBACK_FOOD_SUPPLY.values():
        all_fallback_countries.update(c.upper() for c in proxies)
    filter_countries = list(set(target_countries) | all_fallback_countries)

    df = filter_bulk(
        bulk,
        element_codes=[elem_code],
        years=[reference_year],
        iso3_codes=filter_countries,
    )

    if df.empty:
        logger.warning("FAOSTAT FBS bulk data returned no food supply data")
        return pd.DataFrame(columns=["country", "food_supply_g_day"])

    df = df.dropna(subset=["Value"])
    if df.empty:
        logger.warning(
            "FAOSTAT food supply data contains no numeric values after parsing"
        )
        return pd.DataFrame(columns=["country", "food_supply_g_day"])

    df["country"] = df["iso3"].astype(str).str.upper()

    # Filter by unit (kg/cap)
    if "Unit" in df.columns:
        df = df[df["Unit"].str.lower() == "kg/cap"]

    if df.empty:
        logger.warning(
            "FAOSTAT returned no records for the requested ISO3 country list"
        )
        return pd.DataFrame(columns=["country", "food_supply_g_day"])

    # Convert from kg/year to g/day: kg/yr * 1000 / 365. Sum across items to obtain total supply.
    total_supply = (
        df.groupby("country", as_index=False)["Value"]
        .sum()
        .rename(columns={"Value": "food_supply_kg_year"})
    )
    total_supply["food_supply_g_day"] = (
        total_supply["food_supply_kg_year"] * 1000.0
    ) / 365.0
    total_supply = total_supply.drop(columns=["food_supply_kg_year"])

    missing_countries = sorted(set(target_countries) - set(total_supply["country"]))
    fallback_rows: list[dict[str, float]] = []
    applied_fallbacks: list[tuple[str, str]] = []

    if missing_countries:
        supply_lookup = dict(
            zip(total_supply["country"], total_supply["food_supply_g_day"])
        )
        for iso_code in missing_countries:
            for candidate in FALLBACK_FOOD_SUPPLY.get(iso_code, []):
                value = supply_lookup.get(candidate.upper())
                if value is not None:
                    fallback_rows.append(
                        {"country": iso_code, "food_supply_g_day": value}
                    )
                    supply_lookup[iso_code] = value
                    applied_fallbacks.append((iso_code, candidate.upper()))
                    break

    if fallback_rows:
        total_supply = pd.concat(
            [total_supply, pd.DataFrame(fallback_rows)],
            ignore_index=True,
        )
        logger.info(
            "Applied food supply fallbacks: %s",
            "; ".join(f"{iso}->{proxy}" for iso, proxy in applied_fallbacks),
        )

    remaining_missing = sorted(set(target_countries) - set(total_supply["country"]))
    if remaining_missing:
        logger.warning(
            "Missing FAOSTAT food supply data for: %s",
            ", ".join(remaining_missing),
        )

    logger.info("Retrieved food supply for %d countries", len(total_supply))
    logger.info(
        "Mean food supply: %.1f g/day",
        total_supply["food_supply_g_day"].mean(),
    )

    return total_supply


def process_food_waste_data(
    df: pd.DataFrame,
    food_supply: pd.DataFrame,
    countries: list[str],
    food_groups: list[str],
    m49_regions: dict[str, dict],
    reference_year: int,
) -> pd.DataFrame:
    """Process food waste per capita data and convert to fractions.

    Args:
        df: Raw UNSD data for AG_FOOD_WST_PC series
        food_supply: FAOSTAT food supply per capita (g/day)
        countries: List of ISO3 country codes
        food_groups: List of internal food group names
        m49_regions: Mapping of ISO3 to region metadata (for fallbacks)
        reference_year: Reference model year for fallback rows

    Returns:
        DataFrame with columns: country, food_group, waste_fraction, year
    """
    # Note: Data is classified as "Global" or "Estimated" reporting type but contains country-level estimates
    # We'll match by GeoAreaCode instead of filtering by Reporting Type

    # Sector is a direct column in the bulk CSV
    df["sector"] = df["Food Waste Sector"]

    # Filter to "ALL" sector totals
    df_total = df[df["sector"] == "ALL"].copy()

    df_total["year"] = pd.to_numeric(df_total["TimePeriod"], errors="coerce")
    df_total["waste_kg_year"] = pd.to_numeric(df_total["Value"], errors="coerce")

    results = []

    for country_code in countries:
        # Convert ISO3 to M49 numeric code
        m49_code = iso3_to_m49(country_code)
        if not m49_code:
            continue

        # Get waste data for this country using M49 code
        country_waste = df_total[df_total["GeoAreaCode"].astype(str) == str(m49_code)]

        if country_waste.empty:
            continue

        # Get latest year
        latest = country_waste.sort_values("year").iloc[-1]

        if pd.isna(latest["waste_kg_year"]):
            continue

        waste_kg_year = latest["waste_kg_year"]
        year = int(latest["year"])

        # Get food supply for this country
        country_supply = food_supply[food_supply["country"] == country_code]

        if country_supply.empty:
            logger.warning(
                "No food supply data for %s, skipping waste calculation",
                country_code,
            )
            continue

        food_supply_g_day = country_supply["food_supply_g_day"].iloc[0]

        # Convert waste kg/year to g/day, then to fraction
        # waste_fraction = waste / food_supply
        waste_g_day = (waste_kg_year / 365.0) * 1000.0
        waste_fraction = waste_g_day / food_supply_g_day if food_supply_g_day > 0 else 0

        # Apply to all food groups (waste is not broken down by food type)
        for food_group in food_groups:
            results.append(
                {
                    "country": country_code,
                    "food_group": food_group,
                    "waste_fraction": waste_fraction,
                    "year": year,
                }
            )

    waste_df = pd.DataFrame(results)

    if waste_df.empty:
        logger.warning(
            "No country-level food waste data available; fallbacks not applied"
        )
        return waste_df

    # Attach regional metadata for fallback calculations
    m49_meta = (
        pd.DataFrame.from_dict(m49_regions, orient="index")[
            ["subregion_code", "region_code"]
        ]
        if m49_regions
        else pd.DataFrame()
    )
    if not m49_meta.empty:
        waste_df = waste_df.merge(
            m49_meta,
            left_on="country",
            right_index=True,
            how="left",
        )
    else:
        waste_df["subregion_code"] = None
        waste_df["region_code"] = None

    subregion_avg = (
        waste_df.dropna(subset=["subregion_code"])
        .groupby(["subregion_code", "food_group"])["waste_fraction"]
        .mean()
    )
    region_avg = (
        waste_df.dropna(subset=["region_code"])
        .groupby(["region_code", "food_group"])["waste_fraction"]
        .mean()
    )
    global_avg = waste_df.groupby("food_group")["waste_fraction"].mean()

    year_candidates = pd.to_numeric(waste_df["year"], errors="coerce").dropna()
    fallback_year = (
        int(year_candidates.median()) if not year_candidates.empty else reference_year
    )

    countries_with_data = set(waste_df["country"].unique())
    missing_countries = [iso for iso in countries if iso not in countries_with_data]

    fallback_rows: list[dict] = []
    fallback_sources: list[str] = []

    for iso_code in missing_countries:
        region_info = m49_regions.get(iso_code, {})
        fallback_series = None
        source_label = None

        subregion_code = region_info.get("subregion_code")
        if subregion_code:
            try:
                fallback_series = subregion_avg.xs(subregion_code)
                source_label = f"subregion {subregion_code}"
            except KeyError:
                fallback_series = None

        if fallback_series is None:
            region_code = region_info.get("region_code")
            if region_code:
                try:
                    fallback_series = region_avg.xs(region_code)
                    source_label = f"region {region_code}"
                except KeyError:
                    fallback_series = None

        if fallback_series is None and not global_avg.empty:
            fallback_series = global_avg
            source_label = "global average"

        if fallback_series is None:
            logger.warning("Unable to determine fallback food waste for %s", iso_code)
            continue

        fallback_values = fallback_series.reindex(food_groups)
        fallback_values = fallback_values.fillna(global_avg.reindex(food_groups))

        if fallback_values.isna().all():
            logger.warning("Fallback food waste values remain NaN for %s", iso_code)
            continue

        for food_group, waste_fraction in fallback_values.items():
            if pd.isna(waste_fraction):
                continue
            fallback_rows.append(
                {
                    "country": iso_code,
                    "food_group": food_group,
                    "waste_fraction": float(waste_fraction),
                    "year": fallback_year,
                    "subregion_code": region_info.get("subregion_code"),
                    "region_code": region_info.get("region_code"),
                }
            )
        fallback_sources.append(f"{iso_code}->{source_label}")

    if fallback_rows:
        waste_df = pd.concat([waste_df, pd.DataFrame(fallback_rows)], ignore_index=True)
        logger.info(
            "Applied food waste fallbacks for %d countries: %s",
            len(fallback_sources),
            ", ".join(fallback_sources),
        )

    return waste_df.drop(columns=["subregion_code", "region_code"], errors="ignore")


def apply_curated_overrides(
    result: pd.DataFrame,
    overrides_file: str,
    food_groups: list[str],
) -> pd.DataFrame:
    """Apply curated loss/waste overrides on top of the SDG/FBS pipeline output.

    Country-specific rows take precedence over rows with country == "*"
    (global default). Empty cells in either fraction column are ignored, so
    a row may override only loss, only waste, or both.

    Every override that lands on a country-group pair is logged with the
    citation from the ``source`` column.
    """
    overrides = pd.read_csv(overrides_file, comment="#")
    if overrides.empty:
        logger.info("No curated overrides defined in %s", overrides_file)
        return result

    required_cols = {
        "country",
        "food_group",
        "loss_fraction",
        "waste_fraction",
        "source",
    }
    missing_cols = required_cols - set(overrides.columns)
    if missing_cols:
        raise ValueError(
            f"Override file {overrides_file} is missing columns: {sorted(missing_cols)}"
        )

    overrides["country"] = overrides["country"].astype(str).str.strip()
    overrides["food_group"] = overrides["food_group"].astype(str).str.strip()

    unknown_groups = set(overrides["food_group"]) - set(food_groups)
    if unknown_groups:
        raise ValueError(
            f"Override file references unknown food groups (not in config): "
            f"{sorted(unknown_groups)}"
        )

    # A row that has neither fraction set is a no-op; flag it so it isn't a
    # silent typo.
    no_op = overrides["loss_fraction"].isna() & overrides["waste_fraction"].isna()
    if no_op.any():
        raise ValueError(
            "Override rows with both loss_fraction and waste_fraction empty are "
            "not allowed (they have no effect): "
            f"{overrides.loc[no_op, ['country', 'food_group']].to_dict('records')}"
        )

    if (
        overrides["source"].isna() | (overrides["source"].astype(str).str.strip() == "")
    ).any():
        raise ValueError("Every override row must include a non-empty source citation.")

    # Sort so that country-specific rows come AFTER global rows. We then apply
    # in order, letting country-specific rows overwrite global ones.
    overrides = overrides.sort_values(
        by="country",
        key=lambda s: s.eq("*"),
        ascending=False,
    ).reset_index(drop=True)

    for _, row in overrides.iterrows():
        food_group = row["food_group"]
        target_countries = (
            list(result["country"].unique())
            if row["country"] == "*"
            else [row["country"]]
        )
        mask = result["food_group"].eq(food_group) & result["country"].isin(
            target_countries
        )
        if not mask.any():
            logger.warning(
                "Override has no effect: country=%s, food_group=%s (no matching rows)",
                row["country"],
                food_group,
            )
            continue

        scope_label = "globally" if row["country"] == "*" else f"for {row['country']}"
        for col, label in (("loss_fraction", "loss"), ("waste_fraction", "waste")):
            new_value = row[col]
            if pd.isna(new_value):
                continue
            new_value = float(new_value)
            if not 0.0 <= new_value <= 1.0:
                raise ValueError(
                    f"Override {col} for {row['country']}/{food_group} is "
                    f"{new_value}; must be in [0, 1]."
                )
            old_mean = result.loc[mask, col].mean()
            n_changed = int(mask.sum())
            result.loc[mask, col] = new_value
            logger.info(
                "Override %s: %s %s -> %.1f%% (was %.1f%% on average across %d rows). "
                "Source: %s",
                food_group,
                label,
                scope_label,
                new_value * 100,
                old_mean * 100,
                n_changed,
                row["source"],
            )

    return result


def main():
    m49_file = snakemake.input["m49"]
    animal_production_file = snakemake.input["animal_production"]
    faostat_food_group_supply_file = snakemake.input["faostat_food_group_supply"]
    faostat_fbs_items_file = snakemake.input["faostat_fbs_items"]
    population_file = snakemake.input["population"]
    fbs_csv = snakemake.input["fbs_csv"]
    overrides_file = snakemake.input["overrides"]
    output_file = snakemake.output["food_loss_waste"]
    countries = snakemake.params["countries"]
    food_groups = snakemake.params["food_groups"]
    reference_year = snakemake.params["baseline_year"]

    logger.info("Processing food loss and waste data")
    logger.info("Countries: %d", len(countries))
    logger.info("Food groups: %s", food_groups)
    logger.info("Reference year: %d", reference_year)

    # Load M49 region mappings
    m49_regions = load_m49_regions(m49_file)
    logger.info("Loaded M49 regions for %d countries", len(m49_regions))

    # Load FAOSTAT data for animal-group implicit-loss calculation.
    # Dairy/oil/sugar live in faostat_food_group_supply.csv; poultry meat
    # supply (FBS item 2734) is read separately from faostat_fbs_items.csv
    # because that file is intentionally not emitted there (the diet
    # pipeline FBS-overrides poultry directly from raw FBS items).
    animal_production = pd.read_csv(animal_production_file)
    faostat_supply = pd.read_csv(faostat_food_group_supply_file)
    fbs_items = pd.read_csv(faostat_fbs_items_file)
    population = pd.read_csv(population_file)

    # Read FAOSTAT food supply data from bulk CSV
    fbs_element_code = snakemake.params["fbs_element_code"]
    food_supply = fetch_faostat_food_supply(
        countries, reference_year, fbs_csv, m49_file, fbs_element_code
    )

    # Load SDG data from bulk CSV
    sdg_csv = snakemake.input["sdg_csv"]
    loss_data = load_sdg_series(sdg_csv, "AG_FLS_PCT")
    waste_data = load_sdg_series(sdg_csv, "AG_FOOD_WST_PC")

    # Process food loss
    loss_df = process_food_loss_data(loss_data, countries, food_groups, m49_regions)
    logger.info("Processed %d food loss observations", len(loss_df))

    # Process food waste
    waste_df = process_food_waste_data(
        waste_data,
        food_supply,
        countries,
        food_groups,
        m49_regions,
        reference_year,
    )
    logger.info("Processed %d food waste observations", len(waste_df))

    # Merge loss and waste data
    if not loss_df.empty and not waste_df.empty:
        result = pd.merge(
            loss_df[["country", "food_group", "loss_fraction"]],
            waste_df[["country", "food_group", "waste_fraction"]],
            on=["country", "food_group"],
            how="outer",
        )
    elif not loss_df.empty:
        result = loss_df[["country", "food_group", "loss_fraction"]].copy()
        result["waste_fraction"] = None
    elif not waste_df.empty:
        result = waste_df[["country", "food_group", "waste_fraction"]].copy()
        result["loss_fraction"] = None
    else:
        logger.error("No food loss or waste data retrieved")
        sys.exit(1)

    # Fill missing values with 0 (no data = assume no loss/waste)
    result = result.infer_objects(copy=False)
    result["loss_fraction"] = result["loss_fraction"].fillna(0.0)
    result["waste_fraction"] = result["waste_fraction"].fillna(0.0)

    # Override animal-food loss with FBS-derived implicit loss where possible.
    # Generic SDG loss factors can be inconsistent with the production/supply
    # definitions used elsewhere in the workflow (notably for dairy and poultry).
    #
    # Supply lookups differ by group:
    #   - dairy: from faostat_food_group_supply.csv in g/day fresh weight
    #     (already milk-equivalent, matches production_mt_fresh_retail).
    #   - poultry: from faostat_fbs_items.csv (FBS item 2734 "Poultry Meat")
    #     in kg/cap/year carcass weight; multiplied by carcass-to-retail to
    #     align with production_mt_fresh_retail (retail meat).
    animal_group_sources = {
        "dairy": {
            "products": ["dairy", "dairy-buffalo"],
            "supply_source": "food_group_supply",
            "fbs_item": "dairy",
        },
        "poultry": {
            "products": ["meat-chicken"],
            "supply_source": "fbs_items",
            "fbs_item_code": 2734,
            "carcass_to_retail": 0.60,
        },
    }

    pop_per_country = population.set_index("iso3")["population"]

    for food_group, source in animal_group_sources.items():
        if food_group not in food_groups:
            continue

        products = source["products"]

        production_total = animal_production[
            animal_production["product"].isin(products)
        ]["production_mt_fresh_retail"].sum()

        if source["supply_source"] == "food_group_supply":
            supply_rows = faostat_supply[faostat_supply["item"] == source["fbs_item"]]
            supply_per_country = supply_rows.drop_duplicates(
                subset=["country"]
            ).set_index("country")["value"]
            # supply_mt = supply_g_day * population * 365 / 1e12
            supply_total = 0.0
            for country in supply_per_country.index:
                if country not in pop_per_country.index:
                    continue
                supply_total += (
                    supply_per_country[country] * pop_per_country[country] * 365 / 1e12
                )
        elif source["supply_source"] == "fbs_items":
            item_rows = fbs_items[fbs_items["item_code"] == source["fbs_item_code"]]
            supply_per_country = item_rows.drop_duplicates(
                subset=["country"]
            ).set_index("country")["supply_kg_per_capita_year"]
            # supply_mt_carcass = supply_kg_per_cap_yr * pop / 1e9
            # supply_mt_retail = supply_mt_carcass * carcass_to_retail
            c2r = float(source["carcass_to_retail"])
            supply_total = 0.0
            for country in supply_per_country.index:
                if country not in pop_per_country.index:
                    continue
                supply_total += (
                    supply_per_country[country] * pop_per_country[country] / 1e9 * c2r
                )
        else:
            raise ValueError(
                f"Unknown supply_source '{source['supply_source']}' for {food_group}"
            )

        implicit_loss = 0.0
        if production_total > 0:
            implicit_loss_raw = 1 - supply_total / production_total
            implicit_loss = max(0.0, implicit_loss_raw)
            if implicit_loss_raw < 0:
                logger.info(
                    "%s FBS supply exceeds production (raw loss %.1f%%); clipping to 0%%",
                    food_group,
                    implicit_loss_raw * 100,
                )

        logger.info(
            "%s FBS comparison: production=%.1f Mt, supply=%.1f Mt",
            food_group.title(),
            production_total,
            supply_total,
        )

        group_mask = result["food_group"] == food_group
        old_avg = result.loc[group_mask, "loss_fraction"].mean()
        result.loc[group_mask, "loss_fraction"] = implicit_loss
        logger.info(
            "%s loss fraction: %.1f%% (SDG) -> %.1f%% (FBS-derived)",
            food_group.title(),
            old_avg * 100,
            implicit_loss * 100,
        )

    # Apply curated overrides last so they win over both SDG defaults and the
    # FBS-implicit loss step above.
    result = apply_curated_overrides(result, overrides_file, food_groups)

    # Sort for readability
    result = result.sort_values(["country", "food_group"]).reset_index(drop=True)

    logger.info("Final output: %d rows", len(result))
    logger.info("Countries with data: %d", result["country"].nunique())
    logger.info("Mean loss fraction: %.3f", result["loss_fraction"].mean())
    logger.info("Mean waste fraction: %.3f", result["waste_fraction"].mean())

    # Validate complete coverage: all country-food_group pairs must be present
    expected_pairs = {(c, g) for c in countries for g in food_groups}
    actual_pairs = {(row.country, row.food_group) for row in result.itertuples()}
    missing_pairs = expected_pairs - actual_pairs

    if missing_pairs:
        # Group by food group to show systematic gaps
        missing_by_group = {}
        for country, group in missing_pairs:
            missing_by_group.setdefault(group, []).append(country)

        error_parts = []
        for group in sorted(missing_by_group.keys()):
            count = len(missing_by_group[group])
            examples = ", ".join(sorted(missing_by_group[group])[:5])
            if count > 5:
                examples += f", ... ({count - 5} more)"
            error_parts.append(f"  {group}: {examples}")

        logger.error(
            "Missing food loss/waste data for %d country-group pairs:\n%s",
            len(missing_pairs),
            "\n".join(error_parts),
        )
        sys.exit(1)

    logger.info(
        "Validation passed: complete coverage for all %d country-food_group pairs",
        len(expected_pairs),
    )

    # Write output
    result.to_csv(output_file, index=False)
    logger.info("Wrote output to %s", output_file)


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
