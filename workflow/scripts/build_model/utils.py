# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
    pivot = pivot.apply(pd.to_numeric, errors="coerce")

    return pivot, units


def _fresh_mass_conversion_factors(
    edible_portion_df: pd.DataFrame,
    moisture_df: pd.DataFrame,
    crops: set[str],
) -> dict[str, float]:
    """Compute crop-to-food conversion factors.

    For each crop in ``crops`` this returns the factor that
    ``build_model/food.py`` multiplies into every food_processing pathway
    efficiency to translate dry-matter crop bus mass into food bus mass:

      * ``inverse_moisture`` crops (the default): factor =
        edible_portion / (1 - moisture). The food bus carries commercial
        commodity weight (storage moisture).
      * ``identity`` crops (currently only tea): factor = edible_portion.
        Used when the moisture entry refers to the as-harvested form
        rather than the as-traded commodity, so that the build_crop_yields
        deflation by ``(1 - moisture)`` already lands the crop bus on a
        commercial-commodity-equivalent dry mass and a further inversion
        would overshoot.

    The policy is encoded in ``crop_moisture_content.csv`` via the
    ``food_conversion`` column so call sites do not need to special-case
    individual crops.
    """
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

    sorted_crops = sorted(crops)
    crop_idx = pd.Index(sorted_crops, name="crop")

    missing_edible = crop_idx.difference(df.index).tolist()
    if missing_edible:
        raise ValueError(
            "Missing edible portion data for crops: "
            + ", ".join(sorted(missing_edible))
        )

    missing_moisture = crop_idx.difference(moisture.index).tolist()
    if missing_moisture:
        raise ValueError(
            "Missing moisture fraction data for crops: "
            + ", ".join(sorted(missing_moisture))
        )

    joined = df.loc[crop_idx, ["edible_portion_coefficient"]].join(
        moisture.loc[crop_idx, ["moisture_fraction", "food_conversion"]]
    )

    na_edible = joined["edible_portion_coefficient"].isna()
    if na_edible.any():
        raise ValueError(
            "Missing edible portion data for crops: "
            + ", ".join(sorted(joined.index[na_edible].tolist()))
        )

    na_moisture = joined["moisture_fraction"].isna()
    if na_moisture.any():
        raise ValueError(
            "Missing moisture fraction data for crops: "
            + ", ".join(sorted(joined.index[na_moisture].tolist()))
        )

    valid = {"inverse_moisture", "identity"}
    bad = joined[~joined["food_conversion"].isin(valid)]
    if not bad.empty:
        raise ValueError(
            "Invalid food_conversion values in crop_moisture_content.csv "
            f"(must be one of {sorted(valid)}): "
            + ", ".join(f"{c}={v!r}" for c, v in bad["food_conversion"].items())
        )

    dry_fraction = 1 - joined["moisture_fraction"]
    inverse_factor = joined["edible_portion_coefficient"] / dry_fraction
    identity_factor = joined["edible_portion_coefficient"]
    factor_series = inverse_factor.where(
        joined["food_conversion"] == "inverse_moisture", identity_factor
    )

    return factor_series.to_dict()


def _build_luc_lef_lookup(df: pd.DataFrame) -> pd.DataFrame:
    """Return LEF (tCO2/ha/yr) as a DataFrame with columns:
    region, resource_class, water_supply, use, lef, conversion_share.
    """

    cols = [
        "region",
        "resource_class",
        "water_supply",
        "use",
        "lef",
        "conversion_share",
    ]

    if df.empty:
        return pd.DataFrame(columns=cols)

    rename_map = {"water": "water_supply", "LEF_tCO2_per_ha_yr": "lef"}
    out = df.rename(columns=rename_map)

    if "conversion_share" not in out.columns:
        out["conversion_share"] = 1.0

    out = out[cols].copy()
    out["resource_class"] = out["resource_class"].astype(int)
    out["lef"] = pd.to_numeric(out["lef"], errors="coerce")
    out["conversion_share"] = pd.to_numeric(
        out["conversion_share"], errors="coerce"
    ).fillna(1.0)
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
        Land-use type to filter (e.g. ``"cropland"``, ``"pasture"``,
        ``"spared_cropland"``, ``"spared_grassland"``).
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


def merge_lef_with_share(
    df: pd.DataFrame,
    lef_df: pd.DataFrame,
    use: str,
    *,
    on: list[str] | None = None,
) -> pd.DataFrame:
    """Merge LEF and conversion_share values onto *df* for a given use type.

    Like :func:`merge_lef` but returns both ``lef`` and ``conversion_share``
    columns in a DataFrame aligned to *df*'s index.  Rows without a matching
    LEF entry are dropped (conversion share is zero → no link needed).
    """
    if on is None:
        on = ["region", "resource_class", "water_supply"]

    subset = lef_df.loc[lef_df["use"] == use, [*on, "lef", "conversion_share"]]
    merged = df[on].merge(subset, on=on, how="left")
    merged["lef"] = merged["lef"].fillna(0.0)
    merged["conversion_share"] = merged["conversion_share"].fillna(0.0)
    return merged[["lef", "conversion_share"]].set_axis(df.index)


def _calculate_manure_n_outputs(
    product: str,
    feed_category: str,
    efficiency: float,
    ruminant_n_lookup: dict[str, float],
    monogastric_n_lookup: dict[str, float],
    product_protein_lookup: dict[str, float],
    manure_n2o_lookup: dict[tuple[str, str], tuple[float, float, float]],
    manure_n2o_by_product_lookup: dict[str, tuple[float, float, float]],
    manure_n_to_fertilizer: float,
    indirect_ef4: float,
    indirect_ef5: float,
    organic_n2o_factor: float,
    frac_gasm: float,
    frac_leach: float,
) -> tuple[float, float, float]:
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
    ruminant_n_lookup : dict[str, float]
        Ruminant feed N content lookup (g N/kg DM) keyed by category.
    monogastric_n_lookup : dict[str, float]
        Monogastric feed N content lookup (g N/kg DM) keyed by category.
    product_protein_lookup : dict[str, float]
        Product protein lookup (g protein/100g product) keyed by product.
    manure_n2o_lookup : dict[tuple[str, str], tuple[float, float, float]]
        MMS N2O factors keyed by (product, feed_category) as
        (pasture_fraction, pasture_n2o_ef, storage_n2o_ef).
    manure_n2o_by_product_lookup : dict[str, tuple[float, float, float]]
        Fallback MMS N2O factors keyed by product only.
    manure_n_to_fertilizer : float
        Fraction of managed N available as fertilizer after losses
    indirect_ef4 : float
        kg N2O-N per kg (NH3-N + NOx-N) volatilized (indirect volatilization/deposition)
    indirect_ef5 : float
        kg N2O-N per kg N leached/runoff (indirect leaching)
    organic_n2o_factor : float
        kg N2O-N per kg N applied for organic amendments (IPCC EF1).
        Multiplied with the actual applied-N flow ``n_applied`` so the
        direct-managed N2O stays consistent with manure_n_to_fertilizer.
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
    category_name = feed_category.split("_", 1)[1]
    if feed_category.startswith("ruminant_"):
        feed_n_g_per_kg = ruminant_n_lookup.get(category_name)
    else:
        feed_n_g_per_kg = monogastric_n_lookup.get(category_name)
    if feed_n_g_per_kg is None:
        raise ValueError(f"Missing feed N content for category '{feed_category}'")

    # Get product protein content (g protein/100g product). Missing entries
    # would flow all feed-N to manure (no product-N retained), inflating
    # both manure-N output and the downstream N2O on the link. Require an
    # entry for every animal product.
    protein_g_per_100g = product_protein_lookup.get(product)
    if protein_g_per_100g is None:
        raise ValueError(
            f"Missing protein data for animal product '{product}'; cannot "
            f"compute manure-N balance"
        )

    # Convert protein to N using factor 6.25 (protein = N * 6.25)
    # N (g/kg product) = protein (g/100g) * 10 / 6.25
    product_n_g_per_kg = (protein_g_per_100g * 10) / 6.25

    # Calculate N flows per tonne feed
    feed_n_t_per_t_feed = feed_n_g_per_kg / 1000  # t N/t feed
    product_output_t_per_t_feed = efficiency  # t product/t feed
    product_n_t_per_t_feed = (product_n_g_per_kg / 1000) * product_output_t_per_t_feed

    # N excreted = N in feed - N in product, clamped at zero. High-efficiency,
    # low-feed-N combinations (e.g. high-yield dairy on low-protein roughage)
    # can otherwise yield negative excretion, which would flip the downstream
    # fertilizer output and N2O emissions and let the optimizer exploit
    # animal links as a credit on either bus.
    n_excreted_t_per_t_feed = max(0.0, feed_n_t_per_t_feed - product_n_t_per_t_feed)

    # Look up MMS-based N2O factors for this product and feed category
    mms_factors = manure_n2o_lookup.get((product, feed_category))
    if mms_factors is None:
        mms_factors = manure_n2o_by_product_lookup.get(product)
    if mms_factors is None:
        logger.warning(
            "No manure emission data for %s/%s, using defaults",
            product,
            feed_category,
        )
        pasture_fraction = 0.0
        pasture_n2o_ef = 0.02 if "cattle" in product or "dairy" in product else 0.01
        storage_n2o_ef = 0.005
    else:
        pasture_fraction, pasture_n2o_ef, storage_n2o_ef = mms_factors

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
    # Storage applies to all managed N; application (IPCC EF1) applies to
    # the actual fertilizer flow n_applied = n_managed * manure_n_to_fertilizer.
    # Computing the application term against the actual n_applied keeps the
    # direct-managed N2O consistent with the configured recovery fraction;
    # otherwise a fixed recovery baked into the EF would diverge from the
    # n_applied used for the indirect-loss terms below.
    n_applied = n_fertilizer_t_per_t_feed
    n2o_managed_direct_n = n_managed * storage_n2o_ef + n_applied * organic_n2o_factor

    # Indirect N2O (applies to the applied portion)
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
    manure_ch4_lookup: dict[tuple[str, str, str], float],
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
    manure_ch4_lookup : dict[tuple[str, str, str], float]
        Manure CH4 emission factors keyed by
        (country, product, feed_category) in kg CH4/kg DMI.

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

    # Add manure CH4
    manure_t_per_t = manure_ch4_lookup.get((country, product, feed_category))
    if manure_t_per_t is not None:
        # kg CH4/kg DM = t CH4/t DM (ratio is scale-invariant)
        total_ch4_per_t_feed += manure_t_per_t
        manure_ch4_per_t_feed += manure_t_per_t

    return total_ch4_per_t_feed, manure_ch4_per_t_feed
