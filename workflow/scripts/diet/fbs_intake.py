"""Derive per-(country, food-group) baseline dietary intake from FAOSTAT FBS.

Group intake mass is derived from the FBS "Food supply (kcal/capita/day)"
element at model-basis energy densities (nutrition.csv):

    intake_g_day(country, group) = sum over the group's FBS items of
        kcal_supply * (1 - waste_fraction) / kcal_per_g(item)

Working from energy rather than the FBS mass element sidesteps the
per-item mass-basis bookkeeping that would otherwise be needed (flour
extraction for cereals, refuse fractions for fresh produce, carcass-to-
retail for meat, milk equivalents for dairy): FAO's nutritive factors
already net these out, so dividing FBS energy by the model-basis density
lands the mass directly in model basis. This is the same convention
``prepare_gdd_ia_dietary_intake`` uses for dairy. The FBS "Food supply"
element is net of supply-chain losses, so only consumer-level waste is
deducted to arrive at consumer-eaten intake.

Item-to-group attribution:

* Every FBS item mapped to a modelled food (faostat_food_item_map.csv,
  byproducts excluded) counts toward that food's group exactly once;
  several foods sharing an item (e.g. pearl- and foxtail-millet) do not
  double-count it. The item's density is the mean density of its mapped
  foods.
* Pool items from the within-group projections
  (:mod:`workflow.scripts.diet.food_group_projection`) that are not
  themselves mapped to a food (pineapples, dates, nuts-other,
  roots-other, vegetables-other) count toward the pool's group at the
  mean density of the pool's recipient foods -- matching how
  ``estimate_baseline_diet`` redistributes their supply across those
  recipients. Pool items that are mapped to a food (e.g. plantains 2616,
  a starchy_vegetable) are attributed via their mapping instead.
* Items mapped to foods in two groups (wheat 2511 and rice 2807: refined
  vs whole-grain milling of the same commodity, which FBS does not
  distinguish) are split by the configured
  ``diet.fbs.whole_grain_shares`` fraction.
* dairy folds butter (2740) and cream (2743) energy into the milk item
  (2848) and derives mass at cow-milk density, mirroring the GDD-IA
  butter/cream fold; the group total is strict milk-equivalent mass.
* oil uses the FBS "Vegetable Oils" aggregate (2914) instead of the
  individual per-oil items so that unmodelled oils (maize germ, rice
  bran, ...) are included in the group total, matching the survey
  sources' coverage of all vegetable oils.
* stimulants are not emitted: the model densities for coffee and tea are
  on a brewed basis, so kcal-derivation is meaningless there, and all
  three stimulants foods are ``diet.fbs_override_foods`` whose per-food
  intake is set directly from FBS mass supply in
  ``estimate_baseline_diet``.

SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later
"""

import logging

import pandas as pd

from workflow.scripts.diet.food_group_projection import (
    FRUITS_BAN_POOL_ITEM_CODES,
    FRUITS_BAN_PROJECTION_FOODS,
    FRUITS_FRT_POOL_ITEM_CODES,
    FRUITS_FRT_PROJECTION_FOODS,
    NUTS_POOL_ITEM_CODES,
    NUTS_PROJECTION_FOODS,
    OVG_CROPS,
    OVG_POOL_ITEM_CODES,
    STARCHY_POOL_ITEM_CODES,
    STARCHY_PROJECTION_FOODS,
)

logger = logging.getLogger(__name__)

# Dairy items folded into one milk-equivalent energy pool at cow-milk
# density: Milk - Excluding Butter, Butter/Ghee, Cream.
DAIRY_ITEM_CODES: tuple[int, ...] = (2848, 2740, 2743)
DAIRY_FOOD = "dairy"

# FBS aggregate covering all vegetable oils, modelled and unmodelled.
OIL_GROUP_ITEM_CODE = 2914

# Items needed by this module beyond the mapped + pool codes fetched for
# the within-group shares; imported by prepare_faostat_fbs_items so the
# fetch list stays in sync.
EXTRA_FETCH_ITEM_CODES: tuple[int, ...] = (2740, 2743, OIL_GROUP_ITEM_CODE)

# Groups this source does not emit (see module docstring).
SKIPPED_GROUPS: tuple[str, ...] = ("stimulants",)

# Per-group projection pools: (pool item codes, recipient foods). Pool
# items not mapped to any modelled food are attributed to the group at
# the mean density of the recipient foods.
POOL_SPECS_BY_GROUP: dict[str, list[tuple[tuple[int, ...], tuple[str, ...]]]] = {
    "vegetables": [(OVG_POOL_ITEM_CODES, OVG_CROPS)],
    "starchy_vegetable": [(STARCHY_POOL_ITEM_CODES, STARCHY_PROJECTION_FOODS)],
    "nuts_seeds": [(NUTS_POOL_ITEM_CODES, NUTS_PROJECTION_FOODS)],
    "fruits": [
        (FRUITS_BAN_POOL_ITEM_CODES, FRUITS_BAN_PROJECTION_FOODS),
        (FRUITS_FRT_POOL_ITEM_CODES, FRUITS_FRT_PROJECTION_FOODS),
    ],
}

# Unit labels per group, consistent with gdd_ia_dietary_intake.csv.
UNIT_BY_GROUP = {
    "dairy": "g/day (milk equiv)",
    "sugar": "g/day (refined sugar eq)",
    "animal_fat": "g/day (fresh wt)",
    "oil": "g/day (fresh wt)",
    "grain": "g/day (fresh wt)",
    "whole_grains": "g/day (fresh wt)",
    "legumes": "g/day (fresh wt)",
    "fruits": "g/day (fresh wt)",
    "vegetables": "g/day (fresh wt)",
    "nuts_seeds": "g/day (fresh wt)",
    "starchy_vegetable": "g/day (fresh wt)",
    "red_meat": "g/day (fresh wt)",
    "poultry": "g/day (fresh wt)",
    "eggs": "g/day (fresh wt)",
}

WHOLE_GRAINS_GROUP = "whole_grains"
GRAIN_GROUP = "grain"


def _mean_density(foods: list[str], kcal_per_g_food: dict[str, float]) -> float:
    """Mean model-basis kcal/g over *foods*; fails on missing entries."""
    missing = [f for f in foods if f not in kcal_per_g_food]
    if missing:
        raise ValueError(f"kcal/g missing from nutrition.csv for foods: {missing}")
    densities = [float(kcal_per_g_food[f]) for f in foods]
    return sum(densities) / len(densities)


def build_item_attribution(
    food_item_map: pd.DataFrame,
    food_groups: pd.DataFrame,
    kcal_per_g_food: dict[str, float],
    food_groups_included: list[str],
    byproducts: list[str],
    whole_grain_shares: dict[str, float],
) -> list[tuple[int, str, float, float]]:
    """Attribute FBS items to food groups with energy shares and densities.

    Returns a list of ``(item_code, food_group, kcal_share, kcal_per_g)``
    tuples. An item appears once per group it contributes to; the
    ``kcal_share`` values of one item sum to 1 (wheat and rice appear
    twice via the whole-grain split, every other item once).
    """
    fg_map = food_groups.set_index("food")["group"].to_dict()
    emitted_groups = [g for g in food_groups_included if g not in SKIPPED_GROUPS]
    byproduct_set = set(byproducts)

    unmapped = sorted(
        {str(r["food"]) for _, r in food_item_map.iterrows()}
        - set(fg_map)
        - byproduct_set
    )
    if unmapped:
        raise ValueError(
            f"Foods in faostat_food_item_map.csv missing from "
            f"food_groups.csv: {unmapped}"
        )

    # Foods participating in item attribution. Dairy and oil items are
    # handled explicitly below, so their foods are left out here.
    def participates(food: str) -> bool:
        if food in byproduct_set:
            return False
        group = fg_map[food]
        return group in emitted_groups and group not in ("dairy", "oil")

    foods_by_code: dict[int, list[str]] = {}
    for _, row in food_item_map.iterrows():
        food = str(row["food"])
        if not participates(food):
            continue
        foods_by_code.setdefault(int(row["item_code"]), []).append(food)

    attribution: list[tuple[int, str, float, float]] = []

    for code, foods in sorted(foods_by_code.items()):
        groups = sorted({fg_map[f] for f in foods})
        if len(groups) == 1:
            attribution.append(
                (code, groups[0], 1.0, _mean_density(foods, kcal_per_g_food))
            )
            continue
        if set(groups) != {GRAIN_GROUP, WHOLE_GRAINS_GROUP}:
            raise ValueError(
                f"FBS item {code} maps to foods across groups {groups}; only "
                "the grain/whole_grains split (diet.fbs.whole_grain_shares) "
                "is supported for cross-group items."
            )
        whole_foods = [f for f in foods if fg_map[f] == WHOLE_GRAINS_GROUP]
        refined_foods = [f for f in foods if fg_map[f] == GRAIN_GROUP]
        if len(whole_foods) != 1:
            raise ValueError(
                f"FBS item {code} has {len(whole_foods)} whole-grain foods "
                f"({whole_foods}); expected exactly one to apply "
                "diet.fbs.whole_grain_shares."
            )
        whole_food = whole_foods[0]
        if whole_food not in whole_grain_shares:
            raise ValueError(
                f"diet.fbs.whole_grain_shares has no entry for '{whole_food}' "
                f"(FBS item {code}); required to split the item between "
                "grain and whole_grains."
            )
        share = float(whole_grain_shares[whole_food])
        attribution.append(
            (
                code,
                WHOLE_GRAINS_GROUP,
                share,
                _mean_density([whole_food], kcal_per_g_food),
            )
        )
        attribution.append(
            (
                code,
                GRAIN_GROUP,
                1.0 - share,
                _mean_density(refined_foods, kcal_per_g_food),
            )
        )

    # Pool items not mapped to any modelled food, at the mean density of
    # the pool's recipient foods.
    mapped_codes = set(foods_by_code)
    for group, pools in POOL_SPECS_BY_GROUP.items():
        if group not in emitted_groups:
            continue
        for pool_codes, recipients in pools:
            density = _mean_density(list(recipients), kcal_per_g_food)
            for code in pool_codes:
                if int(code) in mapped_codes:
                    continue
                attribution.append((int(code), group, 1.0, density))

    # Dairy: milk + butter + cream energy at cow-milk density.
    if "dairy" in emitted_groups:
        dairy_density = _mean_density([DAIRY_FOOD], kcal_per_g_food)
        for code in DAIRY_ITEM_CODES:
            attribution.append((code, "dairy", 1.0, dairy_density))

    # Oil: the vegetable-oils aggregate at the mean modelled-oil density.
    if "oil" in emitted_groups:
        oil_foods = sorted(
            f for f, g in fg_map.items() if g == "oil" and f not in byproduct_set
        )
        if not oil_foods:
            raise ValueError("No oil foods found in food_groups.csv")
        attribution.append(
            (
                OIL_GROUP_ITEM_CODE,
                "oil",
                1.0,
                _mean_density(oil_foods, kcal_per_g_food),
            )
        )

    covered = {group for _, group, _, _ in attribution}
    uncovered = [g for g in emitted_groups if g not in covered]
    if uncovered:
        raise ValueError(
            f"No FBS items attributed to food groups {uncovered}; check "
            "faostat_food_item_map.csv and POOL_SPECS_BY_GROUP."
        )
    return attribution


def build_fbs_group_intake(
    kcal_supply: pd.DataFrame,
    food_item_map: pd.DataFrame,
    food_groups: pd.DataFrame,
    nutrition: pd.DataFrame,
    food_loss_waste: pd.DataFrame,
    countries: list[str],
    food_groups_included: list[str],
    byproducts: list[str],
    whole_grain_shares: dict[str, float],
) -> pd.DataFrame:
    """Per-(country, food-group) intake (g/day, model basis) from FBS energy.

    ``kcal_supply`` carries per-(country, item_code) FBS food energy
    supply (``kcal_per_capita_day``, layered FBS/FBSH fallback); missing
    cells are treated as zero supply.

    Returns a DataFrame with columns ``country``, ``food_group``,
    ``value`` (g/day per capita, consumer-eaten intake in model basis).
    """
    kcal_per_g_food = (
        nutrition[nutrition["nutrient"] == "cal"].set_index("food")["value"] / 100.0
    ).to_dict()
    attribution = build_item_attribution(
        food_item_map,
        food_groups,
        kcal_per_g_food,
        food_groups_included,
        byproducts,
        whole_grain_shares,
    )

    kcal_lookup = kcal_supply.set_index(["country", "item_code"])[
        "kcal_per_capita_day"
    ].to_dict()
    waste_lookup = food_loss_waste.set_index(["country", "food_group"])[
        "waste_fraction"
    ].to_dict()

    rows = []
    missing_waste: set[tuple[str, str]] = set()
    for country in countries:
        totals: dict[str, float] = {}
        for code, group, share, density in attribution:
            # Missing (country, item) cells are genuine zero supply (the
            # country does not consume the item), matching how the
            # within-group share machinery treats them.
            kcal = float(kcal_lookup.get((country, code), 0.0))
            if kcal <= 0.0:
                continue
            totals[group] = totals.get(group, 0.0) + kcal * share / density
        for group, g_day_pre_waste in sorted(totals.items()):
            if (country, group) not in waste_lookup:
                missing_waste.add((country, group))
                continue
            waste = float(waste_lookup[(country, group)])
            rows.append(
                {
                    "country": country,
                    "food_group": group,
                    "value": g_day_pre_waste * (1.0 - waste),
                }
            )

    if missing_waste:
        raise ValueError(
            f"food_loss_waste.csv has no waste fraction for "
            f"{len(missing_waste)} (country, group) pairs needed by the FBS "
            f"diet source (e.g. {sorted(missing_waste)[:5]})"
        )

    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("FBS kcal supply produced no dietary intake rows")

    means = result.groupby("food_group")["value"].mean().round(1)
    logger.info("FBS-derived per-group mean intake (g/day) across countries:")
    for group, value in means.sort_index().items():
        logger.info("  %s: %.1f", group, value)
    return result
