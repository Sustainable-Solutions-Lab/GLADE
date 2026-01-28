# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production stability stores for tracking deviation costs.

This module creates PyPSA stores during model building that will track production
deviations from baseline levels. The stores are set up with appropriate cost
attributes so that deviation costs are automatically captured by PyPSA's
statistics module (via marginal_cost_storage for L1 or marginal_cost_quadratic
for quadratic penalties).

Constraints linking actual production to store levels/flows are added during
the solve phase by workflow/scripts/solve_model/production_stability.py.
"""

import logging

import pandas as pd
import pypsa

logger = logging.getLogger(__name__)


def _aggregate_crop_baseline(
    crop_baseline: pd.DataFrame,
    crop_to_fao_item: dict[str, str],
) -> pd.Series:
    """Aggregate crop baseline by (fao_item, country) in Mt.

    Parameters
    ----------
    crop_baseline
        FAO crop production with columns: country, crop, production_tonnes.
    crop_to_fao_item
        Mapping from crop names to FAO item names.

    Returns
    -------
    pd.Series
        Baseline production in Mt indexed by (fao_item, country).
    """
    df = crop_baseline.copy()
    df["production_mt"] = df["production_tonnes"] * 1e-6
    df["fao_item"] = df["crop"].map(lambda c: crop_to_fao_item.get(c, c))
    # Filter out unmapped/empty FAO items
    df = df[df["fao_item"].notna() & (df["fao_item"] != "") & (df["fao_item"] != "nan")]
    baseline_agg = df.groupby(["fao_item", "country"])["production_mt"].sum()
    return baseline_agg


def _aggregate_animal_baseline(
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
) -> pd.Series:
    """Aggregate animal baseline by (product, country) with FLW adjustment.

    Parameters
    ----------
    animal_baseline
        FAO animal production with columns: country, product, production_mt.
    food_to_group
        Mapping from product names to food group names for FLW lookup.
    loss_waste
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction.

    Returns
    -------
    pd.Series
        FLW-adjusted baseline production in Mt indexed by (product, country).
    """
    # Build FLW lookup
    flw_multipliers: dict[tuple[str, str], float] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_frac = float(row["loss_fraction"])
        waste_frac = float(row["waste_fraction"])
        flw_multipliers[key] = (1.0 - loss_frac) * (1.0 - waste_frac)

    target_series = animal_baseline.set_index(["product", "country"])[
        "production_mt"
    ].astype(float)

    # Adjust by FLW
    adjusted_targets = []
    for product, country in target_series.index:
        gross_value = target_series.loc[(product, country)]
        group = food_to_group.get(product, product)
        multiplier = flw_multipliers.get((country, group), 1.0)
        adjusted_targets.append(gross_value * multiplier)

    return pd.Series(adjusted_targets, index=target_series.index)


def add_production_stability_stores(
    n: pypsa.Network,
    stability_cfg: dict,
    crop_baseline: pd.DataFrame | None,
    animal_baseline: pd.DataFrame | None,
    crop_to_fao_item: dict[str, str],
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
) -> None:
    """Add stores for tracking production deviations with penalty costs.

    Creates PyPSA stores during model building that track production deviations
    from baseline levels. The stores are configured with cost attributes:
    - L1 mode: marginal_cost_storage on store level (e)
    - Quadratic mode: marginal_cost_quadratic on store flow (p)

    The actual constraints linking production to store values are added during
    the solve phase.

    Parameters
    ----------
    n
        Network to add stores to.
    stability_cfg
        Configuration with enabled, penalty_mode, l1_cost, quadratic_cost, etc.
    crop_baseline
        FAO crop production with columns: country, crop, production_tonnes.
    animal_baseline
        FAO animal production with columns: country, product, production_mt.
    crop_to_fao_item
        Mapping from crop names to FAO item names.
    food_to_group
        Mapping from product names to food group names for FLW lookup.
    loss_waste
        Food loss and waste fractions.
    """
    if not stability_cfg["enabled"]:
        return

    penalty_mode = stability_cfg["penalty_mode"]
    if penalty_mode not in ("l1", "quadratic"):
        # Hard constraints don't need stores
        return

    # Add carrier for production stability tracking
    if "production_stability" not in n.carriers.static.index:
        n.carriers.add("production_stability", unit="Mt")

    # Add bus for deviation tracking
    n.buses.add("stability:deviation", carrier="production_stability")

    # Add generator to supply/absorb deviation "flow" to/from the bus (bus balance)
    # Same pattern as health stores - generator at zero cost, stores capture penalty
    # p_min_pu=-1 allows negative p (absorption) for quadratic mode where deviations
    # can be negative
    n.generators.add(
        "supply:stability",
        bus="stability:deviation",
        carrier="production_stability",
        p_nom_extendable=True,
        p_min_pu=-1.0,
    )

    crops_cfg = stability_cfg["crops"]
    animals_cfg = stability_cfg["animals"]

    # Process crop stores
    if crops_cfg["enabled"] and crop_baseline is not None:
        crop_targets = _aggregate_crop_baseline(crop_baseline, crop_to_fao_item)
        # Filter to non-zero baselines (zero baselines get hard constraints in solve)
        crop_targets = crop_targets[crop_targets > 0]

        if not crop_targets.empty:
            store_names = pd.Index(
                [
                    f"store:stability:crop:{fao_item}:{country}"
                    for fao_item, country in crop_targets.index
                ]
            )

            store_kwargs: dict = {
                "bus": "stability:deviation",
                "carrier": "production_stability",
                "e_nom_extendable": True,
                "e_cyclic": False,  # Allow p != 0 in single-snapshot model
                "fao_item": [fao_item for fao_item, _ in crop_targets.index],
                "country": [country for _, country in crop_targets.index],
                "baseline_mt": crop_targets.values,
                "stability_type": "crop",
            }

            # Set cost attribute based on penalty mode
            if penalty_mode == "l1":
                store_kwargs["marginal_cost_storage"] = stability_cfg["l1_cost"]
            else:  # quadratic
                store_kwargs["marginal_cost_quadratic"] = stability_cfg[
                    "quadratic_cost"
                ]

            n.stores.add(store_names, **store_kwargs)

            logger.info(
                "Added %d crop production stability stores (mode=%s)",
                len(store_names),
                penalty_mode,
            )

    # Process animal stores
    if animals_cfg["enabled"] and animal_baseline is not None:
        animal_targets = _aggregate_animal_baseline(
            animal_baseline, food_to_group, loss_waste
        )
        # Filter to non-zero baselines
        animal_targets = animal_targets[animal_targets > 0]

        if not animal_targets.empty:
            store_names = pd.Index(
                [
                    f"store:stability:animal:{product}:{country}"
                    for product, country in animal_targets.index
                ]
            )

            store_kwargs = {
                "bus": "stability:deviation",
                "carrier": "production_stability",
                "e_nom_extendable": True,
                "e_cyclic": False,  # Allow p != 0 in single-snapshot model
                "product": [product for product, _ in animal_targets.index],
                "country": [country for _, country in animal_targets.index],
                "baseline_mt": animal_targets.values,
                "stability_type": "animal",
            }

            # Set cost attribute based on penalty mode
            if penalty_mode == "l1":
                store_kwargs["marginal_cost_storage"] = stability_cfg["l1_cost"]
            else:  # quadratic
                store_kwargs["marginal_cost_quadratic"] = stability_cfg[
                    "quadratic_cost"
                ]

            n.stores.add(store_names, **store_kwargs)

            logger.info(
                "Added %d animal production stability stores (mode=%s)",
                len(store_names),
                penalty_mode,
            )
