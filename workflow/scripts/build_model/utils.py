# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Utility functions and helpers for food systems model building.

This module contains data loading helpers, unit conversion functions,
and other utility functions used across the model building process.
"""

import logging

import numpy as np
import pandas as pd

from .. import constants

logger = logging.getLogger(__name__)


def _per_capita_mass_to_mt_per_year(
    value_per_person_per_day: float, population: float
) -> float:
    """Convert g/person/day to Mt/year."""

    return (
        value_per_person_per_day
        * population
        * constants.DAYS_PER_YEAR
        / constants.GRAMS_PER_MEGATONNE
    )


def _nutrient_kind(unit: str) -> str:
    try:
        return constants.SUPPORTED_NUTRITION_UNITS[unit]["kind"]
    except KeyError as exc:
        raise ValueError(f"Unsupported nutrition unit '{unit}'") from exc


def _nutrition_efficiency_factor(unit: str) -> float:
    try:
        return constants.SUPPORTED_NUTRITION_UNITS[unit]["efficiency_factor"]
    except KeyError as exc:
        raise ValueError(f"Unsupported nutrition unit '{unit}'") from exc


def _carrier_unit_for_nutrient(unit: str) -> str:
    kind = _nutrient_kind(unit)
    if kind == "mass":
        return "Mt"
    if kind == "energy":
        return "PJ"
    raise ValueError(f"Unsupported nutrient kind '{kind}'")


def _load_crop_yield_table(path: str) -> tuple[pd.DataFrame, dict[str, str | float]]:
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        # Handle completely empty files (no columns to parse)
        empty_pivot = pd.DataFrame(
            index=pd.MultiIndex.from_tuples([], names=["region", "resource_class"])
        )
        return empty_pivot, {}

    # Handle empty DataFrames (only headers, no data rows)
    if df.empty:
        # Create an empty DataFrame with the expected multi-index structure
        empty_pivot = pd.DataFrame(
            index=pd.MultiIndex.from_tuples([], names=["region", "resource_class"])
        )
        return empty_pivot, {}

    grouped_units = (
        df.groupby("variable")["unit"].agg(lambda s: s.dropna().unique()).to_dict()
    )
    units: dict[str, str | float] = {}
    for var, vals in grouped_units.items():
        if len(vals) == 1:
            units[var] = vals[0]
        else:
            units[var] = np.nan

    pivot = (
        df.pivot(index=["region", "resource_class"], columns="variable", values="value")
        .rename_axis(index=("region", "resource_class"), columns=None)
        .sort_index()
    )

    # Ensure resource_class level is integer
    pivot.index = pivot.index.set_levels(
        pivot.index.levels[1].astype(int), level="resource_class"
    )

    # Ensure numeric columns
    for column in pivot.columns:
        pivot[column] = pd.to_numeric(pivot[column], errors="coerce")

    return pivot, units


def _fresh_mass_conversion_factors(
    edible_portion_df: pd.DataFrame,
    moisture_df: pd.DataFrame,
    crops: set[str],
) -> dict[str, float]:
    """Compute fresh mass conversion factors from edible portion and moisture data."""
    df = edible_portion_df.copy()
    df["crop"] = df["crop"].astype(str).str.strip()

    df = df.set_index("crop")
    df["edible_portion_coefficient"] = pd.to_numeric(
        df["edible_portion_coefficient"], errors="coerce"
    )

    moisture = moisture_df.copy()
    moisture["crop"] = moisture["crop"].astype(str).str.strip()
    moisture = moisture.set_index("crop")
    moisture["moisture_fraction"] = pd.to_numeric(
        moisture["moisture_fraction"], errors="coerce"
    )

    factors: dict[str, float] = {}
    missing_edible: list[str] = []
    missing_moisture: list[str] = []
    for crop in sorted(crops):
        if crop not in df.index:
            missing_edible.append(crop)
            continue
        if crop not in moisture.index:
            missing_moisture.append(crop)
            continue
        edible_coeff = df.at[crop, "edible_portion_coefficient"]
        moisture_fraction = moisture.at[crop, "moisture_fraction"]
        if pd.isna(edible_coeff):
            missing_edible.append(crop)
            continue
        if pd.isna(moisture_fraction):
            missing_moisture.append(crop)
            continue

        dry_fraction = 1 - moisture_fraction
        factor = edible_coeff / dry_fraction
        factors[crop] = factor

    if missing_edible:
        raise ValueError(
            "Missing edible portion data for crops: "
            + ", ".join(sorted(missing_edible))
        )
    if missing_moisture:
        raise ValueError(
            "Missing moisture fraction data for crops: "
            + ", ".join(sorted(missing_moisture))
        )

    return factors


def _build_luc_lef_lookup(df: pd.DataFrame) -> pd.DataFrame:
    """Return LEF (tCO2/ha/yr) as a DataFrame with columns:
    region, resource_class, water_supply, use, lef.
    """

    if df.empty:
        return pd.DataFrame(
            columns=["region", "resource_class", "water_supply", "use", "lef"]
        )

    out = df.rename(columns={"water": "water_supply", "LEF_tCO2_per_ha_yr": "lef"})[
        ["region", "resource_class", "water_supply", "use", "lef"]
    ].copy()
    out["resource_class"] = out["resource_class"].astype(int)
    out["lef"] = pd.to_numeric(out["lef"], errors="coerce")
    out = out[np.isfinite(out["lef"])].reset_index(drop=True)
    return out


def merge_lef(
    df: pd.DataFrame,
    lef_df: pd.DataFrame,
    use: str,
    *,
    on: list[str] | None = None,
    allow_missing: bool = False,
) -> pd.Series:
    """Merge LEF values onto *df* for a given land-use type.

    Parameters
    ----------
    df : pd.DataFrame
        Target rows (must contain the *on* columns).
    lef_df : pd.DataFrame
        LEF lookup from :func:`_build_luc_lef_lookup`.
    use : str
        Land-use type to filter (e.g. ``"cropland"``, ``"pasture"``, ``"spared"``).
    on : list[str], optional
        Columns to merge on.  Defaults to ``["region", "resource_class", "water_supply"]``.
    allow_missing : bool
        If ``False`` (default), raise :class:`ValueError` when any merged LEF is NaN.
        If ``True``, fill NaN with ``0.0``.

    Returns
    -------
    pd.Series
        LEF values aligned to *df*'s index.
    """
    if on is None:
        on = ["region", "resource_class", "water_supply"]

    subset = lef_df.loc[lef_df["use"] == use, [*on, "lef"]]
    merged = df[on].merge(subset, on=on, how="left")

    missing_mask = merged["lef"].isna()
    if missing_mask.any():
        n_missing = int(missing_mask.sum())
        n_total = len(df)
        sample = df.loc[missing_mask.values, on].drop_duplicates().head(5)
        if not allow_missing:
            raise ValueError(
                f"Missing LEF data for use={use!r}: {n_missing}/{n_total} rows "
                f"({n_missing / n_total:.1%}). "
                f"Sample unmatched keys:\n{sample.to_string(index=False)}"
            )
        merged["lef"] = merged["lef"].fillna(0.0)

    return merged["lef"].set_axis(df.index)


def _calculate_manure_n_outputs(
    product: str,
    feed_category: str,
    efficiency: float,
    ruminant_categories: pd.DataFrame,
    monogastric_categories: pd.DataFrame,
    nutrition: pd.DataFrame,
    manure_emissions: pd.DataFrame,
    manure_n_to_fertilizer: float,
    indirect_ef4: float,
    indirect_ef5: float,
    frac_gasm: float,
    frac_leach: float,
) -> tuple[float, float]:
    """Calculate manure N fertilizer and N₂O outputs per tonne feed intake.

    Uses MMS-weighted N2O emission factors that account for the distribution of
    manure across different management systems (pasture, storage, etc.).

    Includes direct and indirect (volatilization and leaching) N₂O emissions
    following IPCC 2019 Refinement methodology (Chapter 11, Equations 11.1, 11.9, 11.10).

    Parameters
    ----------
    product : str
        Animal product name
    feed_category : str
        Feed category (e.g., "ruminant_forage", "monogastric_grain")
    efficiency : float
        Feed conversion efficiency (t product / t feed DM)
    ruminant_categories : pd.DataFrame
        Ruminant feed categories with N_g_per_kg_DM
    monogastric_categories : pd.DataFrame
        Monogastric feed categories with N_g_per_kg_DM
    nutrition : pd.DataFrame
        Nutrition data indexed by (food, nutrient)
    manure_emissions : pd.DataFrame
        MMS-weighted emission factors with columns:
        - product, feed_category: identifiers
        - pasture_fraction: fraction of manure deposited on pasture
        - pasture_n2o_ef: EF3PRP (kg N2O-N per kg N) for pasture deposition
        - managed_n2o_ef: combined storage + application EF for managed manure
    manure_n_to_fertilizer : float
        Fraction of managed N available as fertilizer after losses
    indirect_ef4 : float
        kg N2O-N per kg (NH3-N + NOx-N) volatilized (indirect volatilization/deposition)
    indirect_ef5 : float
        kg N2O-N per kg N leached/runoff (indirect leaching)
    frac_gasm : float
        Fraction of organic N volatilized as NH3 and NOx (FracGASM)
    frac_leach : float
        Fraction of applied N lost through leaching/runoff (FracLEACH-(H))

    Returns
    -------
    tuple[float, float, float]
        (N fertilizer t/t feed, total N2O emissions t/t feed, pasture N2O share)
        The pasture N2O share is the fraction of total N2O from pasture deposition
        (vs managed systems), useful for plotting breakdowns.
    """
    # Get feed N content (g N/kg DM)
    category_name = feed_category.split("_", 1)[
        1
    ]  # Extract category from "ruminant_forage" etc.

    if feed_category.startswith("ruminant_"):
        feed_n_g_per_kg = ruminant_categories.loc[
            ruminant_categories["category"] == category_name, "N_g_per_kg_DM"
        ].values[0]
    else:
        feed_n_g_per_kg = monogastric_categories.loc[
            monogastric_categories["category"] == category_name, "N_g_per_kg_DM"
        ].values[0]

    # Get product protein content (g protein/100g product)
    try:
        protein_g_per_100g = nutrition.loc[(product, "protein"), "value"]
    except KeyError:
        logger.warning(f"No protein data for {product}, assuming 0 N in product")
        protein_g_per_100g = 0.0

    # Convert protein to N using factor 6.25 (protein = N * 6.25)
    # N (g/kg product) = protein (g/100g) * 10 / 6.25
    product_n_g_per_kg = (protein_g_per_100g * 10) / 6.25

    # Calculate N flows per tonne feed
    feed_n_t_per_t_feed = feed_n_g_per_kg / 1000  # t N/t feed
    product_output_t_per_t_feed = efficiency  # t product/t feed
    product_n_t_per_t_feed = (product_n_g_per_kg / 1000) * product_output_t_per_t_feed

    # N excreted = N in feed - N in product
    n_excreted_t_per_t_feed = feed_n_t_per_t_feed - product_n_t_per_t_feed

    # Look up MMS-based N2O factors for this product and feed category
    mask = (manure_emissions["product"] == product) & (
        manure_emissions["feed_category"] == feed_category
    )
    if mask.sum() == 0:
        # Fallback: try without feed_category (for products with single category)
        mask = manure_emissions["product"] == product
        if mask.sum() == 0:
            logger.warning(
                f"No manure emission data for {product}/{feed_category}, using defaults"
            )
            pasture_fraction = 1.0 if feed_category.endswith("_grassland") else 0.0
            pasture_n2o_ef = 0.02 if "cattle" in product or "dairy" in product else 0.01
            managed_n2o_ef = 0.0095  # storage (0.005) + application (0.75 * 0.006)
        else:
            row = manure_emissions[mask].iloc[0]
            pasture_fraction = row["pasture_fraction"]
            pasture_n2o_ef = row["pasture_n2o_ef"]
            managed_n2o_ef = row["managed_n2o_ef"]
    else:
        row = manure_emissions[mask].iloc[0]
        pasture_fraction = row["pasture_fraction"]
        pasture_n2o_ef = row["pasture_n2o_ef"]
        managed_n2o_ef = row["managed_n2o_ef"]

    # Split N between pasture and managed fractions
    n_pasture = n_excreted_t_per_t_feed * pasture_fraction
    n_managed = n_excreted_t_per_t_feed * (1 - pasture_fraction)

    # N available as fertilizer (only from managed fraction, after losses)
    n_fertilizer_t_per_t_feed = n_managed * manure_n_to_fertilizer

    # === Pasture N2O emissions (F_PRP in IPCC terminology) ===
    # Direct N2O (EF3PRP)
    n2o_pasture_direct_n = n_pasture * pasture_n2o_ef

    # Indirect N2O from volatilization (Equation 11.9)
    n2o_pasture_vol_n = n_pasture * frac_gasm * indirect_ef4

    # Indirect N2O from leaching (Equation 11.10)
    n2o_pasture_leach_n = n_pasture * frac_leach * indirect_ef5

    # === Managed N2O emissions (storage + application) ===
    # Direct N2O (storage + application EF)
    # Note: managed_n2o_ef already includes storage EF + (recovery * application EF)
    n2o_managed_direct_n = n_managed * managed_n2o_ef

    # Indirect N2O (applies to the applied portion)
    n_applied = n_fertilizer_t_per_t_feed
    n2o_managed_vol_n = n_applied * frac_gasm * indirect_ef4
    n2o_managed_leach_n = n_applied * frac_leach * indirect_ef5

    # Total pasture N2O-N
    n2o_pasture_n = n2o_pasture_direct_n + n2o_pasture_vol_n + n2o_pasture_leach_n

    # Total N2O-N and convert to N2O
    n2o_n_t_per_t_feed = (
        n2o_pasture_n + n2o_managed_direct_n + n2o_managed_vol_n + n2o_managed_leach_n
    )
    n2o_t_per_t_feed = n2o_n_t_per_t_feed * (44.0 / 28.0)

    # Calculate pasture share of N2O for plotting breakdown
    if n2o_n_t_per_t_feed > 0:
        pasture_n2o_share = n2o_pasture_n / n2o_n_t_per_t_feed
    else:
        pasture_n2o_share = 0.0

    return n_fertilizer_t_per_t_feed, n2o_t_per_t_feed, pasture_n2o_share


def _calculate_ch4_per_feed_intake(
    product: str,
    feed_category: str,
    country: str,
    enteric_my_lookup: dict[str, float],
    manure_emissions: pd.DataFrame,
) -> tuple[float, float]:
    """Calculate CH4 emissions (tCH4/t feed DM) split into total and manure.

    Note: This is calculated per tonne of feed intake (bus0), not per product output.

    Parameters
    ----------
    product : str
        Animal product name (e.g., "meat-cattle", "dairy", "meat-pig")
    feed_category : str
        Feed category name (e.g., "ruminant_roughage", "monogastric_grain")
    country : str
        Country code (ISO3)
    enteric_my_lookup : dict[str, float]
        Enteric methane yields by ruminant feed category (g CH4 / kg DMI)
    manure_emissions : pd.DataFrame
        Manure CH4 emission factors with columns: country, product, feed_category,
        manure_ch4_kg_per_kg_DMI

    Returns
    -------
    tuple[float, float]
        (total CH4, manure CH4) in tCH4/t feed DM
    """
    # Initialize total CH4 per tonne feed
    total_ch4_per_t_feed = 0.0
    manure_ch4_per_t_feed = 0.0

    # Add enteric fermentation CH4 (ruminants only)
    if feed_category.startswith("ruminant_"):
        category = feed_category.split("_", 1)[1]
        if category in enteric_my_lookup:
            # Convert from g CH4/kg DM to t CH4/t DM
            enteric_t_per_t = enteric_my_lookup[category] / 1000.0
            total_ch4_per_t_feed += enteric_t_per_t

    # Add manure CH4 (confined systems only, not pasture)
    # For grassland grazing, manure is deposited on pasture where aerobic
    # decomposition results in negligible CH4 (IPCC MCF ~0.5% for PRP).
    # We therefore skip manure CH4 for grassland feed categories.
    if not feed_category.endswith("_grassland"):
        manure_row = manure_emissions[
            (manure_emissions["country"] == country)
            & (manure_emissions["product"] == product)
            & (manure_emissions["feed_category"] == feed_category)
        ]

        if not manure_row.empty:
            # manure_ch4_kg_per_kg_DMI is in kg CH4/kg DM = t CH4/t DM (ratio is invariant)
            manure_t_per_t = manure_row["manure_ch4_kg_per_kg_DMI"].values[0]
            total_ch4_per_t_feed += manure_t_per_t
            manure_ch4_per_t_feed += manure_t_per_t

    return total_ch4_per_t_feed, manure_ch4_per_t_feed
