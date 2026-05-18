# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Helpers for resolving per-commodity trade and marketing costs.

The model uses a single ``commodities`` configuration block that assigns every
modelled crop, food (incl. animal products and byproducts), and feed category
to exactly one class. Each class carries two parameters:

* ``trade_cost_per_t_km`` -- USD per tonne per km, charged on trade links.
* ``marketing_cost_per_t`` -- USD per tonne, one-shot farm-to-wholesale markup
  charged on the relevant production link.

Validation in ``workflow/validation/commodities.py`` guarantees that every
item the build pipeline asks about is assigned to a class, so the lookup
functions here fail loudly on any missing key rather than silently falling
back to a default.
"""

from collections.abc import Iterable

from .. import constants


def _domain_class_for_items(domain_cfg: dict) -> dict[str, str]:
    """Return ``{item: class_name}`` for a single domain (crops/foods/feeds)."""
    out: dict[str, str] = {}
    for class_name, cls in domain_cfg["classes"].items():
        for item in cls["items"]:
            if item in out:
                raise ValueError(
                    f"commodity item '{item}' is assigned to both "
                    f"'{out[item]}' and '{class_name}'"
                )
            out[item] = class_name
    return out


def trade_costs_per_km(domain_cfg: dict, items: Iterable[str]) -> dict[str, float]:
    """Return ``{item: trade_cost_per_t_km}`` in bnUSD per Mt per km.

    Fails fast if any item is not assigned to a class.
    """
    item_to_class = _domain_class_for_items(domain_cfg)
    classes = domain_cfg["classes"]
    out: dict[str, float] = {}
    missing: list[str] = []
    for item in items:
        cls = item_to_class.get(str(item))
        if cls is None:
            missing.append(str(item))
            continue
        cost = float(classes[cls]["trade_cost_per_t_km"])
        out[str(item)] = cost * constants.USD_TO_BNUSD / constants.TONNE_TO_MEGATONNE
    if missing:
        raise KeyError(
            "items not assigned to any commodity class: " + ", ".join(sorted(missing))
        )
    return out


def marketing_costs_per_t(domain_cfg: dict, items: Iterable[str]) -> dict[str, float]:
    """Return ``{item: marketing_cost_per_t}`` in USD per tonne (no unit conversion).

    Convert to model units at the call site:

    * On ``crop_production`` links the link cost is in bnUSD/Mha. Multiply by
      yield (t/ha) and by ``1e6 * USD_TO_BNUSD``.
    * On ``animal_production`` links the cost is in bnUSD/Mt feed. Multiply by
      efficiency (t product / t feed) and by ``USD_TO_BNUSD``.
    * On ``food_processing`` / ``feed_conversion`` links the cost is in
      bnUSD/Mt input. Multiply by the pathway efficiency (t output / t input)
      and by ``USD_TO_BNUSD``.

    Fails fast if any item is not assigned to a class.
    """
    item_to_class = _domain_class_for_items(domain_cfg)
    classes = domain_cfg["classes"]
    out: dict[str, float] = {}
    missing: list[str] = []
    for item in items:
        cls = item_to_class.get(str(item))
        if cls is None:
            missing.append(str(item))
            continue
        out[str(item)] = float(classes[cls]["marketing_cost_per_t"])
    if missing:
        raise KeyError(
            "items not assigned to any commodity class: " + ", ".join(sorted(missing))
        )
    return out


def non_tradable_items(domain_cfg: dict) -> set[str]:
    return {str(x) for x in domain_cfg["non_tradable"]}
