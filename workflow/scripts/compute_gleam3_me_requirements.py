# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compute ME requirements per animal product and country from GLEAM 3.0 data.

Uses GLEAM3 country-level feed intakes and production data, combined with
model feed category energy contents, to derive per-country ME requirements.

For multi-product species (cattle, buffalo, chicken), the total system ME
is split between products using Wirsenius (2000) dairy:meat ratios as
guidance, with the absolute level set by GLEAM3.  Single-product species
(pigs) are computed directly.  For sheep+goats, cattle dairy ME proxies
the milk component; the residual gives meat-sheep ME.

For monogastrics, the "Other non-edible" GLEAM3 category requires special
handling: in Backyard systems GLEAM3 reclassifies regular grains into this
category, so we assign grain-level ME; in other systems it is genuinely
non-feed (swill, minerals, etc.) and receives swill ME.

Countries with insufficient GLEAM3 data for a species receive the
production-weighted global average for that product.

Output: CSV with columns (animal_product, country, ME_MJ_per_kg) at
carcass/farm-gate level (no retail conversion applied here).
"""

import logging

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

_RUMINANT_ANIMALS = {"Cattle", "Buffalo", "Sheep", "Goats"}

# GLEAM3 feed categories -> model feed categories for ME lookup.
_RUMINANT_FEED_ME_MAP = {
    "Grains": "grain",
    "Oil seed cakes": "protein",
    "Crop residues": "roughage",
    "Grass and leaves": "forage",
    "Fodder crop": "forage",
    "By-products": "grain",
}

_MONOGASTRIC_FEED_ME_MAP = {
    "Grains": "grain",
    "Oil seed cakes": "protein",
    "Other edible": "grain",
    "Other non-edible": None,  # handled per-LPS; see _BACKYARD_NON_EDIBLE_ME
    "By-products": "grain",
    "Grass and leaves": "low_quality",
    "Crop residues": "low_quality",
}

# GLEAM3 classifies backyard monogastric feeds differently from other systems:
# regular grains (wheat, maize, barley, millet, rice, sorghum), soy, and
# pulses are reclassified as "Other non-edible" in Backyard systems because
# they're locally sourced/scavenged rather than commercially purchased.
# In non-Backyard systems, "Other non-edible" contains only genuinely
# non-feed items (synthetic amino acids, fishmeal, limestone) plus swill.
#
# Since the intake data is aggregated to category level (no item breakdown),
# we assign an ME proxy per LPS:
#   - Backyard: use "grain" ME (grains dominate the reclassified items)
#   - Non-Backyard: use swill ME from GLEAM Table S.3.4 (the only
#     non-edible item with significant caloric content)
_BACKYARD_NON_EDIBLE_ME = "grain"
_NON_BACKYARD_SWILL_ME_MJ_PER_KG = {
    "Chicken": 13.0,
    "Pigs": 10.5,
}


def _build_me_lookup(
    ruminant_cats: pd.DataFrame, monogastric_cats: pd.DataFrame
) -> dict[tuple[str, str], float]:
    """Build {(animal_type, category): ME_MJ_per_kg_DM} from feed category CSVs."""
    lookup: dict[tuple[str, str], float] = {}
    for _, row in ruminant_cats.iterrows():
        lookup[("ruminant", row["category"])] = row["ME_MJ_per_kg_DM"]
    for _, row in monogastric_cats.iterrows():
        lookup[("monogastric", row["category"])] = row["ME_MJ_per_kg_DM"]
    return lookup


def _assign_feed_me(
    intakes: pd.DataFrame, me_lookup: dict[tuple[str, str], float]
) -> pd.DataFrame:
    """Add feed_ME_MJ column: intake_kg x ME_per_kg_DM for each row.

    For monogastric "Other non-edible", the ME assignment depends on LPS:
    Backyard systems use grain ME (reclassified local grains dominate),
    while other systems use swill ME from GLEAM Table S.3.4.
    """
    is_ruminant = intakes["Animal"].isin(_RUMINANT_ANIMALS)
    atype = pd.Series("monogastric", index=intakes.index)
    atype[is_ruminant] = "ruminant"

    def _me_for_row(
        animal_type: str, feed_category: str, lps: str, animal: str
    ) -> float:
        feed_map = (
            _RUMINANT_FEED_ME_MAP
            if animal_type == "ruminant"
            else _MONOGASTRIC_FEED_ME_MAP
        )
        model_cat = feed_map.get(feed_category)
        if model_cat is None:
            if animal_type == "monogastric" and feed_category == "Other non-edible":
                if lps == "Backyard":
                    return me_lookup[(animal_type, _BACKYARD_NON_EDIBLE_ME)]
                return _NON_BACKYARD_SWILL_ME_MJ_PER_KG.get(animal, 0.0)
            return 0.0
        return me_lookup[(animal_type, model_cat)]

    me_per_kg = pd.Series(
        [
            _me_for_row(at, fc, lps, animal)
            for at, fc, lps, animal in zip(
                atype, intakes["feed_category"], intakes["LPS"], intakes["Animal"]
            )
        ],
        index=intakes.index,
    )
    intakes = intakes.copy()
    intakes["feed_ME_MJ"] = intakes["DM.intake"] * me_per_kg
    return intakes


def _wirsenius_me(
    wirsenius: pd.DataFrame,
    product: str,
    region: str,
    k_m: float,
    k_g: float,
    k_l: float,
) -> float:
    """Wirsenius ME at carcass/farm-gate level for a product-region pair."""
    rows = wirsenius[
        (wirsenius["animal_product"] == product) & (wirsenius["region"] == region)
    ]
    ne = dict(zip(rows["unit"], rows["value"]))
    if product == "dairy":
        return (
            ne.get("NE_l", 0) / k_l + ne.get("NE_m", 0) / k_m + ne.get("NE_g", 0) / k_g
        )
    if product == "meat-cattle":
        return ne.get("NE_m", 0) / k_m + ne.get("NE_g", 0) / k_g
    # Monogastrics already in ME
    return ne.get("ME", 0)


def _sum_feed_me(
    intakes: pd.DataFrame,
    animals: list[str],
    *,
    exclude_lps: list[str] | None = None,
) -> float:
    df = intakes[intakes["Animal"].isin(animals)]
    if exclude_lps:
        df = df[~df["LPS"].isin(exclude_lps)]
    return float(df["feed_ME_MJ"].sum())


def _sum_production(
    production: pd.DataFrame,
    animals: list[str],
    item: str,
    element: str,
    *,
    exclude_lps: list[str] | None = None,
) -> float:
    df = production[
        (production["Animal"].isin(animals))
        & (production["Item"] == item)
        & (production["Element"] == element)
    ]
    if exclude_lps:
        df = df[~df["LPS"].isin(exclude_lps)]
    return float(df["Total"].sum())


def _compute_country_me(
    ci: pd.DataFrame,
    cp: pd.DataFrame,
    wirsenius: pd.DataFrame,
    region: str,
    k_m: float,
    k_g: float,
    k_l: float,
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Compute ME per product for one country.

    Returns (me_dict, f_dict) where me_dict maps product -> ME_MJ_per_kg
    and f_dict maps species group -> scaling factor (for clamping).
    Values are None where data is insufficient.

    The scaling factor f = actual_feed_ME / wirsenius_implied_ME measures
    how much the country's feed intensity deviates from the Wirsenius
    regional expectation.  It is used for post-hoc regional clamping.
    """
    out: dict[str, float | None] = {}
    f_out: dict[str, float | None] = {}

    # --- Cattle (excluding Feedlots -- no production data) ---
    cattle_feed = _sum_feed_me(ci, ["Cattle"], exclude_lps=["Feedlots"])
    cattle_milk = _sum_production(
        cp, ["Cattle"], "Milk", "Weight", exclude_lps=["Feedlots"]
    )
    cattle_meat = _sum_production(
        cp, ["Cattle"], "Meat", "CarcassWeight", exclude_lps=["Feedlots"]
    )

    w_dairy = _wirsenius_me(wirsenius, "dairy", region, k_m, k_g, k_l)
    w_meat_cattle = _wirsenius_me(wirsenius, "meat-cattle", region, k_m, k_g, k_l)

    w_cattle_implied = w_dairy * cattle_milk + w_meat_cattle * cattle_meat
    if w_cattle_implied > 0 and cattle_feed > 0:
        f_cattle = cattle_feed / w_cattle_implied
        f_out["cattle"] = f_cattle
        out["dairy"] = w_dairy * f_cattle
        out["meat-cattle"] = w_meat_cattle * f_cattle
    else:
        f_out["cattle"] = None
        out["dairy"] = None
        out["meat-cattle"] = None

    # --- Buffalo ---
    buffalo_feed = _sum_feed_me(ci, ["Buffalo"])
    buffalo_milk = _sum_production(cp, ["Buffalo"], "Milk", "Weight")
    buffalo_meat = _sum_production(cp, ["Buffalo"], "Meat", "CarcassWeight")

    w_buffalo_implied = w_dairy * buffalo_milk + w_meat_cattle * buffalo_meat
    if w_buffalo_implied > 0 and buffalo_feed > 0:
        f_buffalo = buffalo_feed / w_buffalo_implied
        f_out["buffalo"] = f_buffalo
        out["dairy-buffalo"] = w_dairy * f_buffalo
    else:
        f_out["buffalo"] = None
        out["dairy-buffalo"] = None

    # --- Pigs (direct — no splitting, no f) ---
    pig_feed = _sum_feed_me(ci, ["Pigs"])
    pig_meat = _sum_production(cp, ["Pigs"], "Meat", "CarcassWeight")
    out["meat-pig"] = pig_feed / pig_meat if pig_meat > 0 and pig_feed > 0 else None

    # --- Chicken (combined: Backyard is dual-product) ---
    chicken_feed = _sum_feed_me(ci, ["Chicken"])
    chicken_meat = _sum_production(cp, ["Chicken"], "Meat", "CarcassWeight")
    chicken_eggs = _sum_production(cp, ["Chicken"], "Eggs", "Weight")

    w_chicken = _wirsenius_me(wirsenius, "meat-chicken", region, k_m, k_g, k_l)
    w_eggs = _wirsenius_me(wirsenius, "eggs", region, k_m, k_g, k_l)

    w_chicken_implied = w_chicken * chicken_meat + w_eggs * chicken_eggs
    if w_chicken_implied > 0 and chicken_feed > 0:
        f_chicken = chicken_feed / w_chicken_implied
        f_out["chicken"] = f_chicken
        out["meat-chicken"] = w_chicken * f_chicken
        out["eggs"] = w_eggs * f_chicken
    else:
        f_out["chicken"] = None
        out["meat-chicken"] = None
        out["eggs"] = None

    # --- Sheep + Goats ---
    # Use the same Wirsenius dairy:meat ME ratio to split sheep/goat feed
    # between milk (proxied through cattle dairy) and meat-sheep.
    # This is more stable than the previous residual method, which amplified
    # errors for countries with extreme sheep milk:meat ratios (e.g. MDA 317:1).
    sg_feed = _sum_feed_me(ci, ["Sheep", "Goats"])
    sg_milk = _sum_production(cp, ["Sheep", "Goats"], "Milk", "Weight")
    sg_meat = _sum_production(cp, ["Sheep", "Goats"], "Meat", "CarcassWeight")

    w_sg_implied = w_dairy * sg_milk + w_meat_cattle * sg_meat
    if w_sg_implied > 0 and sg_feed > 0:
        f_sg = sg_feed / w_sg_implied
        f_out["sheep_goat"] = f_sg
        out["meat-sheep"] = w_meat_cattle * f_sg
    else:
        f_out["sheep_goat"] = None
        out["meat-sheep"] = None

    return out, f_out


def _has_valid_me(me_val: float | None) -> bool:
    """Return True for finite, strictly positive ME values."""
    return me_val is not None and pd.notna(me_val) and me_val > 0


_SPECIES_PRODUCTS: dict[str, list[str]] = {
    "cattle": ["dairy", "meat-cattle"],
    "buffalo": ["dairy-buffalo"],
    "chicken": ["meat-chicken", "eggs"],
    "sheep_goat": ["meat-sheep"],
}


def _clamp_scaling_factors(
    country_f: dict[str, dict[str, float | None]],
    country_regions: dict[str, str | None],
    clamp_factor: float,
) -> dict[str, dict[str, float]]:
    """Compute clamped scaling factors per country and species group.

    For each Wirsenius region and species group, computes the median f
    across countries with valid data.  Country f-values outside
    [median / clamp_factor, median * clamp_factor] are clamped to the
    nearest bound.

    Returns {country: {species_group: clamped_f / original_f}} ratios
    to apply as multiplicative corrections to the raw ME values.
    """
    import numpy as np

    # Collect f values by (region, species_group)
    region_f: dict[tuple[str, str], list[float]] = {}
    for country, f_dict in country_f.items():
        region = country_regions.get(country)
        if region is None:
            continue
        for species, f_val in f_dict.items():
            if f_val is not None and f_val > 0:
                region_f.setdefault((region, species), []).append(f_val)

    # Compute regional medians
    region_median: dict[tuple[str, str], float] = {}
    for key, vals in region_f.items():
        region_median[key] = float(np.median(vals))

    # Compute correction ratios
    corrections: dict[str, dict[str, float]] = {}
    n_clamped = 0
    for country, f_dict in country_f.items():
        corrections[country] = {}
        region = country_regions.get(country)
        for species, f_val in f_dict.items():
            if f_val is None or region is None:
                corrections[country][species] = 1.0
                continue
            med = region_median.get((region, species))
            if med is None or med <= 0:
                corrections[country][species] = 1.0
                continue
            f_lo = med / clamp_factor
            f_hi = med * clamp_factor
            if f_val < f_lo:
                corrections[country][species] = f_lo / f_val
                n_clamped += 1
                logger.info(
                    "  Clamped %s %s f=%.3f → %.3f (regional median=%.3f)",
                    country,
                    species,
                    f_val,
                    f_lo,
                    med,
                )
            elif f_val > f_hi:
                corrections[country][species] = f_hi / f_val
                n_clamped += 1
                logger.info(
                    "  Clamped %s %s f=%.3f → %.3f (regional median=%.3f)",
                    country,
                    species,
                    f_val,
                    f_hi,
                    med,
                )
            else:
                corrections[country][species] = 1.0
    if n_clamped:
        logger.info(
            "Clamped %d scaling factors (clamp_factor=%.1f)", n_clamped, clamp_factor
        )
    return corrections


def compute_gleam3_me_requirements(
    intakes_file: str,
    production_file: str,
    ruminant_categories_file: str,
    monogastric_categories_file: str,
    wirsenius_file: str,
    country_region_file: str,
    countries: list[str],
    net_to_me_conversion: dict[str, float],
    me_scaling_clamp_factor: float,
    output_file: str,
) -> None:
    # Load data
    intakes = pd.read_csv(intakes_file, comment="#")
    production = pd.read_csv(production_file, comment="#")
    ruminant_cats = pd.read_csv(ruminant_categories_file)
    monogastric_cats = pd.read_csv(monogastric_categories_file)
    wirsenius = pd.read_csv(wirsenius_file, comment="#")
    country_region = pd.read_csv(country_region_file, comment="#")

    k_m = net_to_me_conversion["k_m"]
    k_g = net_to_me_conversion["k_g"]
    k_l = net_to_me_conversion["k_l"]

    me_lookup = _build_me_lookup(ruminant_cats, monogastric_cats)

    # Filter to config countries
    intakes = intakes[intakes["ISO3"].isin(countries)].copy()
    production = production[production["ISO3"].isin(countries)].copy()

    # Assign ME to each intake row
    intakes = _assign_feed_me(intakes, me_lookup)

    # Filter production: CarcassWeight for meat, Weight for milk/eggs
    production = production[
        production["Element"].isin(["CarcassWeight", "Weight"])
    ].copy()
    production = production[
        ~((production["Item"] == "Meat") & (production["Element"] == "Weight"))
    ]

    # Country -> Wirsenius region (only for ratio guidance)
    c2r = dict(zip(country_region["country"], country_region["wirsenius_region"]))

    products = [
        "dairy",
        "meat-cattle",
        "dairy-buffalo",
        "meat-pig",
        "meat-chicken",
        "eggs",
        "meat-sheep",
    ]

    # Phase 1: compute per-country ME and scaling factors
    country_me: dict[str, dict[str, float | None]] = {}
    country_f: dict[str, dict[str, float | None]] = {}
    for country in countries:
        ci = intakes[intakes["ISO3"] == country]
        cp = production[production["ISO3"] == country]
        region = c2r.get(country)
        if region is None:
            country_me[country] = dict.fromkeys(products)
            country_f[country] = {}
            continue
        me_dict, f_dict = _compute_country_me(ci, cp, wirsenius, region, k_m, k_g, k_l)
        country_me[country] = me_dict
        country_f[country] = f_dict

    # Phase 1b: clamp scaling factors to within-region bounds
    corrections = _clamp_scaling_factors(country_f, c2r, me_scaling_clamp_factor)
    for country in countries:
        for species, prods in _SPECIES_PRODUCTS.items():
            corr = corrections.get(country, {}).get(species, 1.0)
            if corr != 1.0:
                for p in prods:
                    if _has_valid_me(country_me[country].get(p)):
                        country_me[country][p] *= corr

    # Phase 2: compute production-weighted global averages as fallback
    global_avg: dict[str, float] = {}
    for product in products:
        total_me = 0.0
        total_prod = 0.0
        for country in countries:
            me_val = country_me[country].get(product)
            if not _has_valid_me(me_val):
                continue
            # Weight by the production that informed this ME
            cp = production[production["ISO3"] == country]
            if product in ("dairy", "dairy-buffalo"):
                animals = ["Cattle"] if product == "dairy" else ["Buffalo"]
                prod = _sum_production(cp, animals, "Milk", "Weight")
            elif product == "eggs":
                prod = _sum_production(cp, ["Chicken"], "Eggs", "Weight")
            elif product == "meat-cattle":
                prod = _sum_production(
                    cp, ["Cattle"], "Meat", "CarcassWeight", exclude_lps=["Feedlots"]
                )
            elif product == "meat-pig":
                prod = _sum_production(cp, ["Pigs"], "Meat", "CarcassWeight")
            elif product == "meat-chicken":
                prod = _sum_production(cp, ["Chicken"], "Meat", "CarcassWeight")
            elif product == "meat-sheep":
                prod = _sum_production(cp, ["Sheep", "Goats"], "Meat", "CarcassWeight")
            else:
                prod = 1.0
            total_me += me_val * prod
            total_prod += prod
        global_avg[product] = total_me / total_prod if total_prod > 0 else 0.0

    # Phase 3: assemble output, filling gaps with global averages
    results = []
    for country in countries:
        for product in products:
            me_val = country_me[country].get(product)
            if not _has_valid_me(me_val):
                me_val = global_avg[product]
            results.append(
                {
                    "animal_product": product,
                    "country": country,
                    "ME_MJ_per_kg": me_val,
                }
            )

    output = pd.DataFrame(results)
    output = output.sort_values(["animal_product", "country"]).reset_index(drop=True)
    output.to_csv(output_file, index=False)

    logger.info("Wrote %d ME requirement entries to %s", len(output), output_file)
    for product in sorted(output["animal_product"].unique()):
        prod_data = output[output["animal_product"] == product]
        logger.info(
            "  %s: %.1f - %.1f MJ/kg (mean %.1f)",
            product,
            prod_data["ME_MJ_per_kg"].min(),
            prod_data["ME_MJ_per_kg"].max(),
            prod_data["ME_MJ_per_kg"].mean(),
        )
    n_fallback = sum(
        1
        for c in countries
        for p in products
        if not _has_valid_me(country_me[c].get(p))
    )
    if n_fallback:
        logger.info(
            "  %d of %d entries used global-average fallback",
            n_fallback,
            len(results),
        )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    compute_gleam3_me_requirements(
        intakes_file=snakemake.input.gleam3_intakes,
        production_file=snakemake.input.gleam3_production,
        ruminant_categories_file=snakemake.input.ruminant_categories,
        monogastric_categories_file=snakemake.input.monogastric_categories,
        wirsenius_file=snakemake.input.wirsenius,
        country_region_file=snakemake.input.country_wirsenius_region,
        countries=list(snakemake.params.countries),
        net_to_me_conversion=dict(snakemake.params.net_to_me_conversion),
        me_scaling_clamp_factor=float(snakemake.params.me_scaling_clamp_factor),
        output_file=snakemake.output[0],
    )
