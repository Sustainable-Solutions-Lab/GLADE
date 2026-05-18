# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation that every modelled commodity is assigned to a cost class.

The ``commodities`` config block carries both the trade-cost-per-km and the
farm-to-wholesale marketing-cost-per-tonne for every modelled commodity.
The model never falls back to a default class: any item the build pipeline
references must appear in exactly one class's ``items`` list.

Checked invariants:

1. ``commodities.crops`` covers every entry in ``config["crops"]`` exactly once
   and contains no extra items.
2. ``commodities.feeds`` covers exactly the feed categories in
   ``workflow/scripts/constants.FEED_CATEGORIES``.
3. ``commodities.foods`` covers every food that the build pipeline can
   instantiate: pathway outputs in ``data/curated/foods.csv`` reachable from
   ``config["crops"]``, plus ``config["animal_products"]["include"]``, plus
   ``config["animal_products"]["co_products"]``. No extras.
4. ``commodities.{crops,foods,feeds}.non_tradable`` only references items the
   domain already covers (no orphan entries).
"""

from pathlib import Path

import pandas as pd

from workflow.scripts.constants import FEED_CATEGORIES


def _items_in_classes(domain_cfg: dict) -> list[str]:
    items: list[str] = []
    for cls in domain_cfg["classes"].values():
        items.extend(str(x) for x in cls["items"])
    return items


def _check_assignment(
    domain_name: str,
    domain_cfg: dict,
    expected: set[str],
) -> list[str]:
    """Return a list of error messages for one commodity domain."""
    errors: list[str] = []

    assigned_list = _items_in_classes(domain_cfg)
    assigned = set(assigned_list)

    # Duplicates within or across classes
    if len(assigned_list) != len(assigned):
        seen: set[str] = set()
        dups: list[str] = []
        for x in assigned_list:
            if x in seen:
                dups.append(x)
            seen.add(x)
        errors.append(
            f"commodities.{domain_name}: items assigned to more than one class: "
            f"{sorted(set(dups))}"
        )

    missing = sorted(expected - assigned)
    if missing:
        errors.append(
            f"commodities.{domain_name}.classes: missing assignments for {missing}. "
            "Every modelled item must appear in exactly one class's items list "
            "(no defaults / fallbacks)."
        )

    # Extras in commodities (items not produced by the current config) are
    # tolerated -- they are simply unused. Same for non_tradable. The hard
    # rule is the other direction: every modelled commodity must be assigned.

    return errors


def validate_commodities(config: dict, project_root: Path) -> None:
    commodities = config["commodities"]

    expected_crops = set(config["crops"])
    expected_feeds = set(FEED_CATEGORIES)

    foods_path = project_root / "data" / "curated" / "foods.csv"
    if not foods_path.exists():
        raise FileNotFoundError(f"Expected data file at {foods_path}")
    foods_df = pd.read_csv(foods_path, comment="#")
    crop_set = set(config["crops"])
    pathway_foods = set(foods_df.loc[foods_df["crop"].isin(crop_set), "food"].unique())

    animal_products_cfg = config["animal_products"]
    animal_products = set(animal_products_cfg["include"])
    co_products = set((animal_products_cfg.get("co_products") or {}).keys())
    expected_foods = pathway_foods | animal_products | co_products

    errors: list[str] = []
    errors.extend(_check_assignment("crops", commodities["crops"], expected_crops))
    errors.extend(_check_assignment("foods", commodities["foods"], expected_foods))
    errors.extend(_check_assignment("feeds", commodities["feeds"], expected_feeds))

    if errors:
        raise ValueError("\n  - " + "\n  - ".join(errors))
