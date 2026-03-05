# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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
    "Grass and leaves": "grassland",
    "Fodder crop": "forage",
    "By-products": "grain",
}

_MONOGASTRIC_FEED_ME_MAP = {
    "Grains": "grain",
    "Oil seed cakes": "protein",
    "Other edible": "grain",
    "Other non-edible": None,  # exogenous -- excluded from ME
    "By-products": "grain",
    "Grass and leaves": "low_quality",
    "Crop residues": "low_quality",
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
    """Add feed_ME_MJ column: intake_kg x ME_per_kg_DM for each row."""
    is_ruminant = intakes["Animal"].isin(_RUMINANT_ANIMALS)
    atype = pd.Series("monogastric", index=intakes.index)
    atype[is_ruminant] = "ruminant"

    def _me_for_row(animal_type: str, feed_category: str) -> float:
        feed_map = (
            _RUMINANT_FEED_ME_MAP
            if animal_type == "ruminant"
            else _MONOGASTRIC_FEED_ME_MAP
        )
        model_cat = feed_map.get(feed_category)
        if model_cat is None:
            return 0.0
        return me_lookup[(animal_type, model_cat)]

    me_per_kg = pd.Series(
        [_me_for_row(at, fc) for at, fc in zip(atype, intakes["feed_category"])],
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
) -> dict[str, float | None]:
    """Compute ME per product for one country.  Returns None for products
    where data is insufficient."""
    out: dict[str, float | None] = {}

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
    if w_cattle_implied > 0:
        f_cattle = cattle_feed / w_cattle_implied
        out["dairy"] = w_dairy * f_cattle
        out["meat-cattle"] = w_meat_cattle * f_cattle
    else:
        out["dairy"] = None
        out["meat-cattle"] = None

    # --- Buffalo ---
    buffalo_feed = _sum_feed_me(ci, ["Buffalo"])
    buffalo_milk = _sum_production(cp, ["Buffalo"], "Milk", "Weight")
    buffalo_meat = _sum_production(cp, ["Buffalo"], "Meat", "CarcassWeight")

    w_buffalo_implied = w_dairy * buffalo_milk + w_meat_cattle * buffalo_meat
    if w_buffalo_implied > 0:
        f_buffalo = buffalo_feed / w_buffalo_implied
        out["dairy-buffalo"] = w_dairy * f_buffalo
    else:
        out["dairy-buffalo"] = None

    # --- Pigs (direct) ---
    pig_feed = _sum_feed_me(ci, ["Pigs"])
    pig_meat = _sum_production(cp, ["Pigs"], "Meat", "CarcassWeight")
    out["meat-pig"] = pig_feed / pig_meat if pig_meat > 0 else None

    # --- Chicken (combined: Backyard is dual-product) ---
    chicken_feed = _sum_feed_me(ci, ["Chicken"])
    chicken_meat = _sum_production(cp, ["Chicken"], "Meat", "CarcassWeight")
    chicken_eggs = _sum_production(cp, ["Chicken"], "Eggs", "Weight")

    w_chicken = _wirsenius_me(wirsenius, "meat-chicken", region, k_m, k_g, k_l)
    w_eggs = _wirsenius_me(wirsenius, "eggs", region, k_m, k_g, k_l)

    w_chicken_implied = w_chicken * chicken_meat + w_eggs * chicken_eggs
    if w_chicken_implied > 0:
        f_chicken = chicken_feed / w_chicken_implied
        out["meat-chicken"] = w_chicken * f_chicken
        out["eggs"] = w_eggs * f_chicken
    else:
        out["meat-chicken"] = None
        out["eggs"] = None

    # --- Sheep + Goats ---
    sg_feed = _sum_feed_me(ci, ["Sheep", "Goats"])
    sg_milk = _sum_production(cp, ["Sheep", "Goats"], "Milk", "Weight")
    sg_meat = _sum_production(cp, ["Sheep", "Goats"], "Meat", "CarcassWeight")

    dairy_proxy = out.get("dairy")
    if sg_meat > 0 and dairy_proxy is not None:
        meat_me_residual = sg_feed - sg_milk * dairy_proxy
        if meat_me_residual > 0:
            out["meat-sheep"] = meat_me_residual / sg_meat
        else:
            total_prod = sg_meat + sg_milk
            out["meat-sheep"] = sg_feed / total_prod if total_prod > 0 else None
    elif sg_meat > 0 and sg_feed > 0:
        # No dairy proxy available; use full system ME
        total_prod = sg_meat + sg_milk
        out["meat-sheep"] = sg_feed / total_prod if total_prod > 0 else None
    else:
        out["meat-sheep"] = None

    return out


def compute_gleam3_me_requirements(
    intakes_file: str,
    production_file: str,
    ruminant_categories_file: str,
    monogastric_categories_file: str,
    wirsenius_file: str,
    country_region_file: str,
    countries: list[str],
    net_to_me_conversion: dict[str, float],
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

    # Phase 1: compute per-country ME where GLEAM3 data is available
    country_me: dict[str, dict[str, float | None]] = {}
    for country in countries:
        ci = intakes[intakes["ISO3"] == country]
        cp = production[production["ISO3"] == country]
        region = c2r.get(country)
        if region is None:
            country_me[country] = dict.fromkeys(products)
            continue
        country_me[country] = _compute_country_me(
            ci, cp, wirsenius, region, k_m, k_g, k_l
        )

    # Phase 2: compute production-weighted global averages as fallback
    global_avg: dict[str, float] = {}
    for product in products:
        total_me = 0.0
        total_prod = 0.0
        for country in countries:
            me_val = country_me[country].get(product)
            if me_val is None:
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
            if me_val is None:
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
        1 for c in countries for p in products if country_me[c].get(p) is None
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
        output_file=snakemake.output[0],
    )
