# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production stability constraints and penalties for the food systems model.

This module provides constraint builders that limit production deviation from
baseline levels. Three penalty modes are supported:

- **hard**: Inequality bounds constraining production to within ±delta of baseline
- **quadratic**: Soft penalty via stores with marginal_cost_quadratic on flow (QP)
- **l1**: Linear absolute value penalty via stores with marginal_cost_storage (LP)

For l1 and quadratic modes, stores are created during build time with appropriate
cost attributes. This module adds constraints linking actual production to those
stores:
- L1: Store level e = |deviation|, constrained via e >= ±deviation
- Quadratic: Store flow p = deviation, constrained via p == deviation

The L1 mode is recommended for solver performance as it avoids quadratic terms.
"""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)


def add_production_stability_constraints(
    n: pypsa.Network,
    crop_baseline: pd.DataFrame | None,
    crop_to_fao_item: dict[str, str],
    animal_baseline: pd.DataFrame | None,
    stability_cfg: dict,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    slack_marginal_cost: float,
) -> None:
    """Add constraints limiting production deviation from baseline levels.

    For crops and animal products, applies per-(product, country) bounds:
    ``(1 - delta) * baseline <= production <= (1 + delta) * baseline``

    Alternatively, when ``penalty_mode`` is "l1" or "quadratic", links production
    to stability stores created during model building, whose cost attributes
    penalize deviations.

    Products with zero baseline are constrained to zero production.

    When ``enable_slack`` is set in the config, the minimum production
    constraint uses slack variables: ``production + slack >= lower_bound``
    with a penalty cost of ``slack_marginal_cost`` per Mt shortfall.

    Note: Multi-cropping is disabled when production stability is enabled.

    Parameters
    ----------
    n : pypsa.Network
        The network containing the model.
    crop_baseline : pd.DataFrame | None
        FAO crop production with columns: country, crop, production_tonnes.
    crop_to_fao_item : dict[str, str]
        Mapping from crop names to FAO item names; used to aggregate crops
        that share an FAO item (e.g., dryland-rice and wetland-rice both
        map to "Rice").
    animal_baseline : pd.DataFrame | None
        FAO animal production with columns: country, product, production_mt.
    stability_cfg : dict
        Configuration with enabled, penalty_mode, quadratic_cost,
        deviation_type, crops.max_relative_deviation, etc.
    food_to_group : dict[str, str]
        Mapping from product names to food group names for FLW lookup.
    loss_waste : pd.DataFrame
        Food loss and waste fractions with columns: country, food_group,
        loss_fraction, waste_fraction.
    slack_marginal_cost : float
        Penalty cost in bn USD per Mt for production stability slack.
    """
    if not stability_cfg["enabled"]:
        return

    m = n.model
    link_p = m.variables["Link-p"].sel(snapshot="now")
    links_df = n.links.static

    penalty_mode = stability_cfg["penalty_mode"]

    if penalty_mode == "quadratic":
        deviation_type = stability_cfg["deviation_type"]

        # --- CROP PRODUCTION QUADRATIC PENALTY ---
        crops_cfg = stability_cfg["crops"]
        if crops_cfg["enabled"] and crop_baseline is not None:
            _add_crop_store_constraints(
                n,
                link_p,
                links_df,
                crop_baseline,
                crop_to_fao_item,
                deviation_type,
                penalty_mode,
            )

        # --- ANIMAL PRODUCTION QUADRATIC PENALTY ---
        animals_cfg = stability_cfg["animals"]
        if animals_cfg["enabled"] and animal_baseline is not None:
            _add_animal_store_constraints(
                n,
                link_p,
                links_df,
                animal_baseline,
                food_to_group,
                loss_waste,
                deviation_type,
                penalty_mode,
            )
    elif penalty_mode == "l1":
        deviation_type = stability_cfg["deviation_type"]

        # --- CROP PRODUCTION L1 PENALTY ---
        crops_cfg = stability_cfg["crops"]
        if crops_cfg["enabled"] and crop_baseline is not None:
            _add_crop_store_constraints(
                n,
                link_p,
                links_df,
                crop_baseline,
                crop_to_fao_item,
                deviation_type,
                penalty_mode,
            )

        # --- ANIMAL PRODUCTION L1 PENALTY ---
        animals_cfg = stability_cfg["animals"]
        if animals_cfg["enabled"] and animal_baseline is not None:
            _add_animal_store_constraints(
                n,
                link_p,
                links_df,
                animal_baseline,
                food_to_group,
                loss_waste,
                deviation_type,
                penalty_mode,
            )
    else:
        # Hard constraints mode (existing behavior)
        # --- CROP PRODUCTION BOUNDS ---
        crops_cfg = stability_cfg["crops"]
        if crops_cfg["enabled"] and crop_baseline is not None:
            _add_crop_stability_constraints(
                n,
                link_p,
                links_df,
                crop_baseline,
                crop_to_fao_item,
                crops_cfg,
                slack_marginal_cost,
            )

        # --- ANIMAL PRODUCTION BOUNDS ---
        animals_cfg = stability_cfg["animals"]
        if animals_cfg["enabled"] and animal_baseline is not None:
            _add_animal_stability_constraints(
                n,
                link_p,
                links_df,
                animal_baseline,
                animals_cfg,
                food_to_group,
                loss_waste,
                slack_marginal_cost,
            )


def _add_crop_stability_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    crop_baseline: pd.DataFrame,
    crop_to_fao_item: dict[str, str],
    crops_cfg: dict,
    slack_marginal_cost: float,
) -> None:
    """Add crop production stability bounds.

    Crops that share a FAO item (e.g., dryland-rice and wetland-rice both map
    to "Rice") are aggregated together for the constraint.

    When ``enable_slack`` is True, the minimum production constraint uses
    slack variables: ``production + slack >= lower_bound``.
    """
    m = n.model
    delta = crops_cfg["max_relative_deviation"]

    # Filter to crop production links using the crop column
    # Note: some links have empty string instead of NaN, so check for both
    crop_mask = links_df["crop"].notna() & (links_df["crop"] != "")
    crop_links = links_df[crop_mask].copy()

    if crop_links.empty:
        logger.info(
            "No crop production links found; skipping crop stability constraints"
        )
        return

    crops = crop_links["crop"].astype(str)
    countries = crop_links["country"].astype(str)
    link_names = crop_links.index

    # Map crops to FAO items; use crop name as fallback for unmapped crops
    fao_items = crops.map(lambda c: crop_to_fao_item.get(c, c))
    # Filter out crops with empty/nan FAO item (e.g., alfalfa, biomass-sorghum)
    valid_mask = (
        fao_items.notna() & (fao_items != "") & (fao_items.str.lower() != "nan")
    )

    if not valid_mask.any():
        logger.info(
            "No crops with FAO item mappings; skipping crop stability constraints"
        )
        return

    fao_items = fao_items[valid_mask]
    countries_filtered = countries[valid_mask]
    link_names_filtered = link_names[valid_mask]
    efficiencies_filtered = crop_links.loc[valid_mask, "efficiency"].values

    # Efficiencies (yield: Mt/Mha)
    efficiencies = xr.DataArray(
        efficiencies_filtered, coords={"name": link_names_filtered}, dims="name"
    )

    # Production = p * efficiency (p is land in Mha)
    production_vars = link_p.sel(name=link_names_filtered)

    # Group by (fao_item, country) to aggregate related crops
    grouper = pd.MultiIndex.from_arrays(
        [fao_items.values, countries_filtered.values], names=["fao_item", "country"]
    )
    da_grouper = xr.DataArray(
        grouper, coords={"name": link_names_filtered}, dims="name"
    )

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()

    # Convert baseline to Mt and aggregate by FAO item
    baseline_df = crop_baseline.copy()
    baseline_df["production_mt"] = baseline_df["production_tonnes"] * 1e-6
    # Map baseline crops to FAO items
    baseline_df["fao_item"] = baseline_df["crop"].map(
        lambda c: crop_to_fao_item.get(c, c)
    )
    # Aggregate baseline by (fao_item, country) - this sums the split values back
    baseline_agg = (
        baseline_df.groupby(["fao_item", "country"])["production_mt"]
        .sum()
        .reset_index()
    )
    target_series = baseline_agg.set_index(["fao_item", "country"])["production_mt"]

    # Match to model index
    model_index = pd.Index(total_production.coords["group"].values, name="group")
    common_index = model_index.intersection(target_series.index)

    if common_index.empty:
        logger.warning("No matching crop production targets for stability bounds")
        return

    # Build RHS bounds
    baselines = target_series.loc[common_index].values
    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    rhs_lower = xr.DataArray(lower_bounds, coords={"group": common_index}, dims="group")
    rhs_upper = xr.DataArray(upper_bounds, coords={"group": common_index}, dims="group")

    # Handle zero baselines: force production to zero
    zero_mask = baselines == 0
    nonzero_mask = ~zero_mask

    if zero_mask.any():
        zero_index = common_index[zero_mask]
        lhs_zero = total_production.sel(group=zero_index)
        constr_name = "crop_production_zero"
        m.add_constraints(lhs_zero == 0, name=f"GlobalConstraint-{constr_name}")
        gc_names = [
            f"{constr_name}_{fao_item}_{country}" for fao_item, country in zero_index
        ]
        gc_crops = [fao_item for fao_item, _country in zero_index]
        gc_countries = [country for _fao_item, country in zero_index]
        n.global_constraints.add(
            gc_names,
            sense="==",
            constant=0.0,
            type="production_stability",
            country=gc_countries,
            crop=gc_crops,
        )
        logger.info(
            "Added %d crop production constraints for zero-baseline (fao_item, country) pairs",
            int(zero_mask.sum()),
        )

    if nonzero_mask.any():
        nonzero_index = common_index[nonzero_mask]
        lhs_nonzero = total_production.sel(group=nonzero_index)
        lower_nonzero = rhs_lower.sel(group=nonzero_index)
        upper_nonzero = rhs_upper.sel(group=nonzero_index)

        constr_name_min = "crop_production_min"
        constr_name_max = "crop_production_max"

        enable_slack = crops_cfg.get("enable_slack", False)
        if enable_slack:
            # Add slack variables for minimum production constraint
            # Slack represents shortfall from the minimum bound
            # Create coords matching lhs_nonzero's "group" dimension
            slack_coords = xr.DataArray(
                np.zeros(len(nonzero_index)),
                coords={"group": nonzero_index},
                dims="group",
            ).coords
            crop_slack = m.add_variables(
                lower=0,
                coords=slack_coords,
                name="crop_production_slack",
            )
            # Constraint: production + slack >= lower_bound
            m.add_constraints(
                lhs_nonzero + crop_slack >= lower_nonzero,
                name=f"GlobalConstraint-{constr_name_min}",
            )
            # Add penalty cost to objective (bn USD per Mt)
            m.objective += slack_marginal_cost * crop_slack.sum()
            logger.info(
                "Added crop production slack variables for %d (fao_item, country) pairs "
                "(cost=%.1f bn USD/Mt)",
                len(nonzero_index),
                slack_marginal_cost,
            )
        else:
            # Hard constraint: production >= lower_bound
            m.add_constraints(
                lhs_nonzero >= lower_nonzero, name=f"GlobalConstraint-{constr_name_min}"
            )

        m.add_constraints(
            lhs_nonzero <= upper_nonzero, name=f"GlobalConstraint-{constr_name_max}"
        )

        gc_names_min = [
            f"{constr_name_min}_{fao_item}_{country}"
            for fao_item, country in nonzero_index
        ]
        gc_names_max = [
            f"{constr_name_max}_{fao_item}_{country}"
            for fao_item, country in nonzero_index
        ]
        gc_crops = [fao_item for fao_item, _country in nonzero_index]
        gc_countries = [country for _fao_item, country in nonzero_index]
        n.global_constraints.add(
            gc_names_min,
            sense=">=",
            constant=lower_nonzero.values,
            type="production_stability",
            country=gc_countries,
            crop=gc_crops,
        )
        n.global_constraints.add(
            gc_names_max,
            sense="<=",
            constant=upper_nonzero.values,
            type="production_stability",
            country=gc_countries,
            crop=gc_crops,
        )

        logger.info(
            "Added %d crop production stability constraints (delta=%.0f%%)",
            2 * int(nonzero_mask.sum()),
            delta * 100,
        )

    # Log missing baselines (at FAO item level)
    missing = model_index.difference(target_series.index)
    if len(missing) > 0:
        examples = [f"{item}/{country}" for item, country in list(missing)[:5]]
        logger.warning(
            "Missing crop baseline data for %d (fao_item, country) pairs; examples: %s",
            len(missing),
            ", ".join(examples),
        )


def _add_animal_stability_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    animals_cfg: dict,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    slack_marginal_cost: float,
) -> None:
    """Add animal production stability bounds.

    Reuses the aggregation logic from add_animal_production_constraints()
    but applies inequality bounds instead of equality.

    When ``enable_slack`` is True, the minimum production constraint uses
    slack variables: ``production + slack >= lower_bound``.
    """
    m = n.model
    delta = animals_cfg["max_relative_deviation"]

    # Build FLW lookup (same as add_animal_production_constraints)
    flw_multipliers: dict[tuple[str, str], float] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_frac = float(row["loss_fraction"])
        waste_frac = float(row["waste_fraction"])
        flw_multipliers[key] = (1.0 - loss_frac) * (1.0 - waste_frac)

    # Filter to animal production links using product column
    # Note: some links have empty string instead of NaN, so check for both
    prod_mask = links_df["product"].notna() & (links_df["product"] != "")
    prod_links = links_df[prod_mask]

    if prod_links.empty:
        logger.info(
            "No animal production links found; skipping animal stability constraints"
        )
        return

    products = prod_links["product"].astype(str)
    countries = prod_links["country"].astype(str)
    link_names = prod_links.index

    efficiencies = xr.DataArray(
        prod_links["efficiency"].values, coords={"name": link_names}, dims="name"
    )

    production_vars = link_p.sel(name=link_names)

    grouper = pd.MultiIndex.from_arrays(
        [products.values, countries.values], names=["product", "country"]
    )
    da_grouper = xr.DataArray(grouper, coords={"name": link_names}, dims="name")

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()

    # Build FLW-adjusted targets (same logic as add_animal_production_constraints)
    target_series = animal_baseline.set_index(["product", "country"])[
        "production_mt"
    ].astype(float)

    adjusted_targets = []
    for product, country in target_series.index:
        gross_value = target_series.loc[(product, country)]
        group = food_to_group.get(product, product)
        multiplier = flw_multipliers.get((country, group), 1.0)
        adjusted_targets.append(gross_value * multiplier)
    target_series = pd.Series(adjusted_targets, index=target_series.index)

    model_index = pd.Index(total_production.coords["group"].values, name="group")
    common_index = model_index.intersection(target_series.index)

    if common_index.empty:
        logger.warning("No matching animal production targets for stability bounds")
        return

    # Build bounds
    baselines = target_series.loc[common_index].values
    lower_bounds = np.maximum(0.0, (1.0 - delta) * baselines)
    upper_bounds = (1.0 + delta) * baselines

    rhs_lower = xr.DataArray(lower_bounds, coords={"group": common_index}, dims="group")
    rhs_upper = xr.DataArray(upper_bounds, coords={"group": common_index}, dims="group")

    # Handle zero baselines: force production to zero
    zero_mask = baselines == 0
    nonzero_mask = ~zero_mask

    if zero_mask.any():
        zero_index = common_index[zero_mask]
        lhs_zero = total_production.sel(group=zero_index)
        constr_name = "animal_production_zero"
        m.add_constraints(lhs_zero == 0, name=f"GlobalConstraint-{constr_name}")
        gc_names = [f"{constr_name}_{prod}_{country}" for prod, country in zero_index]
        gc_products = [prod for prod, _country in zero_index]
        gc_countries = [country for _prod, country in zero_index]
        n.global_constraints.add(
            gc_names,
            sense="==",
            constant=0.0,
            type="production_stability",
            country=gc_countries,
            product=gc_products,
        )
        logger.info(
            "Added %d animal production constraints for zero-baseline (product, country) pairs",
            int(zero_mask.sum()),
        )

    if nonzero_mask.any():
        nonzero_index = common_index[nonzero_mask]
        lhs_nonzero = total_production.sel(group=nonzero_index)
        lower_nonzero = rhs_lower.sel(group=nonzero_index)
        upper_nonzero = rhs_upper.sel(group=nonzero_index)

        constr_name_min = "animal_production_min"
        constr_name_max = "animal_production_max"

        enable_slack = animals_cfg.get("enable_slack", False)
        if enable_slack:
            # Add slack variables for minimum production constraint
            # Slack represents shortfall from the minimum bound
            # Create coords matching lhs_nonzero's "group" dimension
            slack_coords = xr.DataArray(
                np.zeros(len(nonzero_index)),
                coords={"group": nonzero_index},
                dims="group",
            ).coords
            animal_slack = m.add_variables(
                lower=0,
                coords=slack_coords,
                name="animal_production_slack",
            )
            # Constraint: production + slack >= lower_bound
            m.add_constraints(
                lhs_nonzero + animal_slack >= lower_nonzero,
                name=f"GlobalConstraint-{constr_name_min}",
            )
            # Add penalty cost to objective (bn USD per Mt)
            m.objective += slack_marginal_cost * animal_slack.sum()
            logger.info(
                "Added animal production slack variables for %d (product, country) pairs "
                "(cost=%.1f bn USD/Mt)",
                len(nonzero_index),
                slack_marginal_cost,
            )
        else:
            # Hard constraint: production >= lower_bound
            m.add_constraints(
                lhs_nonzero >= lower_nonzero, name=f"GlobalConstraint-{constr_name_min}"
            )

        m.add_constraints(
            lhs_nonzero <= upper_nonzero, name=f"GlobalConstraint-{constr_name_max}"
        )

        gc_names_min = [
            f"{constr_name_min}_{prod}_{country}" for prod, country in nonzero_index
        ]
        gc_names_max = [
            f"{constr_name_max}_{prod}_{country}" for prod, country in nonzero_index
        ]
        gc_products = [prod for prod, _country in nonzero_index]
        gc_countries = [country for _prod, country in nonzero_index]
        n.global_constraints.add(
            gc_names_min,
            sense=">=",
            constant=lower_nonzero.values,
            type="production_stability",
            country=gc_countries,
            product=gc_products,
        )
        n.global_constraints.add(
            gc_names_max,
            sense="<=",
            constant=upper_nonzero.values,
            type="production_stability",
            country=gc_countries,
            product=gc_products,
        )

        logger.info(
            "Added %d animal production stability constraints (delta=%.0f%%)",
            2 * int(nonzero_mask.sum()),
            delta * 100,
        )

    # Log missing baselines
    missing = model_index.difference(target_series.index)
    if len(missing) > 0:
        examples = [f"{p}/{c}" for p, c in list(missing)[:5]]
        logger.warning(
            "Missing animal baseline data for %d (product, country) pairs; examples: %s",
            len(missing),
            ", ".join(examples),
        )


def _compute_total_crop_production(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    crop_to_fao_item: dict[str, str],
) -> tuple[xr.DataArray, pd.Index]:
    """Compute total crop production grouped by (fao_item, country).

    Returns
    -------
    total_production
        DataArray with 'group' dimension containing production expressions
    model_index
        Index of (fao_item, country) tuples present in the model
    """
    # Filter to crop production links using the crop column
    crop_mask = links_df["crop"].notna() & (links_df["crop"] != "")
    crop_links = links_df[crop_mask].copy()

    if crop_links.empty:
        return xr.DataArray(), pd.Index([])

    crops = crop_links["crop"].astype(str)
    countries = crop_links["country"].astype(str)
    link_names = crop_links.index

    # Map crops to FAO items
    fao_items = crops.map(lambda c: crop_to_fao_item.get(c, c))
    valid_mask = (
        fao_items.notna() & (fao_items != "") & (fao_items.str.lower() != "nan")
    )

    if not valid_mask.any():
        return xr.DataArray(), pd.Index([])

    fao_items = fao_items[valid_mask]
    countries_filtered = countries[valid_mask]
    link_names_filtered = link_names[valid_mask]
    efficiencies_filtered = crop_links.loc[valid_mask, "efficiency"].values

    efficiencies = xr.DataArray(
        efficiencies_filtered, coords={"name": link_names_filtered}, dims="name"
    )

    production_vars = link_p.sel(name=link_names_filtered)

    grouper = pd.MultiIndex.from_arrays(
        [fao_items.values, countries_filtered.values], names=["fao_item", "country"]
    )
    da_grouper = xr.DataArray(
        grouper, coords={"name": link_names_filtered}, dims="name"
    )

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()
    model_index = pd.Index(total_production.coords["group"].values, name="group")

    return total_production, model_index


def _compute_total_animal_production(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
) -> tuple[xr.DataArray, pd.Index]:
    """Compute total animal production grouped by (product, country).

    Returns
    -------
    total_production
        DataArray with 'group' dimension containing production expressions
    model_index
        Index of (product, country) tuples present in the model
    """
    prod_mask = links_df["product"].notna() & (links_df["product"] != "")
    prod_links = links_df[prod_mask]

    if prod_links.empty:
        return xr.DataArray(), pd.Index([])

    products = prod_links["product"].astype(str)
    countries = prod_links["country"].astype(str)
    link_names = prod_links.index

    efficiencies = xr.DataArray(
        prod_links["efficiency"].values, coords={"name": link_names}, dims="name"
    )

    production_vars = link_p.sel(name=link_names)

    grouper = pd.MultiIndex.from_arrays(
        [products.values, countries.values], names=["product", "country"]
    )
    da_grouper = xr.DataArray(grouper, coords={"name": link_names}, dims="name")

    total_production = (production_vars * efficiencies).groupby(da_grouper).sum()
    model_index = pd.Index(total_production.coords["group"].values, name="group")

    return total_production, model_index


def _add_crop_store_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    crop_baseline: pd.DataFrame,
    crop_to_fao_item: dict[str, str],
    deviation_type: str,
    penalty_mode: str,
) -> None:
    """Add constraints linking crop production to stability stores.

    For L1 mode: store level e = |deviation|
        Constrained via e >= (production - baseline) and e >= -(production - baseline)

    For quadratic mode: store flow p = deviation
        Constrained via p == (production - baseline)

    Zero-baseline products get hard equality constraints forcing production to zero.
    """
    m = n.model
    stores_df = n.stores.static

    # Get crop stability stores created in build phase
    crop_stores = stores_df[
        (stores_df["carrier"] == "production_stability")
        & (stores_df["stability_type"] == "crop")
    ]

    if crop_stores.empty:
        logger.info("No crop stability stores found; skipping crop store constraints")
        return

    # Compute total production by (fao_item, country)
    total_production, model_index = _compute_total_crop_production(
        n, link_p, links_df, crop_to_fao_item
    )

    if total_production.sizes.get("group", 0) == 0:
        logger.info("No crop production to constrain")
        return

    # Build baseline from store metadata (already aggregated and filtered)
    store_fao_items = crop_stores["fao_item"].astype(str)
    store_countries = crop_stores["country"].astype(str)
    store_baselines = crop_stores["baseline_mt"].astype(float)
    store_index = pd.MultiIndex.from_arrays(
        [store_fao_items.values, store_countries.values],
        names=["fao_item", "country"],
    )

    # Find common groups between model production and stores
    common_index = model_index.intersection(store_index)

    if common_index.empty:
        logger.warning("No matching crop production groups for stability stores")
        return

    # Get the store names for common groups
    store_name_lookup = dict(zip(store_index, crop_stores.index))
    common_store_names = [store_name_lookup[idx] for idx in common_index]

    # Build baselines DataArray aligned with common_index
    baseline_lookup = dict(zip(store_index, store_baselines.values))
    baselines = xr.DataArray(
        [baseline_lookup[idx] for idx in common_index],
        coords={"group": common_index},
        dims="group",
    )

    # Get production for common groups
    production = total_production.sel(group=common_index)

    # Compute deviation expression
    if deviation_type == "relative":
        # deviation = (production - baseline) / baseline
        deviation = (production - baselines) / baselines
    else:
        # deviation = production - baseline
        deviation = production - baselines

    # Add hard constraints for zero-baseline products (handled in solve for hard mode)
    # Here we assume stores only exist for non-zero baselines (filtered in build)

    # Add store constraints based on penalty mode
    if penalty_mode == "l1":
        # Store level e = |deviation|
        # Constrain: e >= deviation AND e >= -deviation
        store_e = m.variables["Store-e"].sel(snapshot="now", name=common_store_names)

        # Rename store variable coords to align with deviation
        store_e_aligned = store_e.assign_coords(name=common_index).rename(
            {"name": "group"}
        )

        m.add_constraints(
            store_e_aligned >= deviation,
            name="GlobalConstraint-crop_stability_pos",
        )
        m.add_constraints(
            store_e_aligned >= -deviation,
            name="GlobalConstraint-crop_stability_neg",
        )

        logger.info(
            "Added %d crop production L1 store constraints (mode=%s)",
            len(common_index),
            deviation_type,
        )
    else:  # quadratic
        # Store flow p = deviation
        store_p = m.variables["Store-p"].sel(snapshot="now", name=common_store_names)

        # Rename store variable coords to align with deviation
        store_p_aligned = store_p.assign_coords(name=common_index).rename(
            {"name": "group"}
        )

        m.add_constraints(
            store_p_aligned == deviation,
            name="GlobalConstraint-crop_stability_dev",
        )

        logger.info(
            "Added %d crop production quadratic store constraints (mode=%s)",
            len(common_index),
            deviation_type,
        )

    # Also add zero-production constraints for products in the model but missing from
    # stores (which means they have zero baseline - they were filtered out during build)
    missing_in_stores = model_index.difference(store_index)
    if not missing_in_stores.empty:
        # Get crop baselines to verify these are actually zero
        baseline_df = crop_baseline.copy()
        baseline_df["production_mt"] = baseline_df["production_tonnes"] * 1e-6
        baseline_df["fao_item"] = baseline_df["crop"].map(
            lambda c: crop_to_fao_item.get(c, c)
        )
        baseline_agg = (
            baseline_df.groupby(["fao_item", "country"])["production_mt"]
            .sum()
            .reset_index()
        )
        target_series = baseline_agg.set_index(["fao_item", "country"])["production_mt"]

        # Check which missing groups have zero baseline in the FAO data
        zero_baseline_groups = [
            idx for idx in missing_in_stores if target_series.get(idx, 0.0) == 0.0
        ]

        if zero_baseline_groups:
            zero_index = pd.Index(zero_baseline_groups)
            lhs_zero = total_production.sel(group=zero_index)
            constr_name = "crop_production_zero"
            m.add_constraints(lhs_zero == 0, name=f"GlobalConstraint-{constr_name}")
            gc_names = [
                f"{constr_name}_{fao_item}_{country}"
                for fao_item, country in zero_index
            ]
            gc_crops = [fao_item for fao_item, _country in zero_index]
            gc_countries = [country for _fao_item, country in zero_index]
            n.global_constraints.add(
                gc_names,
                sense="==",
                constant=0.0,
                type="production_stability",
                country=gc_countries,
                crop=gc_crops,
            )
            logger.info(
                "Added %d crop production constraints for zero-baseline (fao_item, country) pairs",
                len(zero_index),
            )


def _add_animal_store_constraints(
    n: pypsa.Network,
    link_p,
    links_df: pd.DataFrame,
    animal_baseline: pd.DataFrame,
    food_to_group: dict[str, str],
    loss_waste: pd.DataFrame,
    deviation_type: str,
    penalty_mode: str,
) -> None:
    """Add constraints linking animal production to stability stores.

    For L1 mode: store level e = |deviation|
        Constrained via e >= (production - baseline) and e >= -(production - baseline)

    For quadratic mode: store flow p = deviation
        Constrained via p == (production - baseline)

    Zero-baseline products get hard equality constraints forcing production to zero.
    """
    m = n.model
    stores_df = n.stores.static

    # Get animal stability stores created in build phase
    animal_stores = stores_df[
        (stores_df["carrier"] == "production_stability")
        & (stores_df["stability_type"] == "animal")
    ]

    if animal_stores.empty:
        logger.info(
            "No animal stability stores found; skipping animal store constraints"
        )
        return

    # Compute total production by (product, country)
    total_production, model_index = _compute_total_animal_production(
        n, link_p, links_df
    )

    if total_production.sizes.get("group", 0) == 0:
        logger.info("No animal production to constrain")
        return

    # Build baseline from store metadata (already aggregated and FLW-adjusted)
    store_products = animal_stores["product"].astype(str)
    store_countries = animal_stores["country"].astype(str)
    store_baselines = animal_stores["baseline_mt"].astype(float)
    store_index = pd.MultiIndex.from_arrays(
        [store_products.values, store_countries.values],
        names=["product", "country"],
    )

    # Find common groups between model production and stores
    common_index = model_index.intersection(store_index)

    if common_index.empty:
        logger.warning("No matching animal production groups for stability stores")
        return

    # Get the store names for common groups
    store_name_lookup = dict(zip(store_index, animal_stores.index))
    common_store_names = [store_name_lookup[idx] for idx in common_index]

    # Build baselines DataArray aligned with common_index
    baseline_lookup = dict(zip(store_index, store_baselines.values))
    baselines = xr.DataArray(
        [baseline_lookup[idx] for idx in common_index],
        coords={"group": common_index},
        dims="group",
    )

    # Get production for common groups
    production = total_production.sel(group=common_index)

    # Compute deviation expression
    if deviation_type == "relative":
        deviation = (production - baselines) / baselines
    else:
        deviation = production - baselines

    # Add store constraints based on penalty mode
    if penalty_mode == "l1":
        store_e = m.variables["Store-e"].sel(snapshot="now", name=common_store_names)
        store_e_aligned = store_e.assign_coords(name=common_index).rename(
            {"name": "group"}
        )

        m.add_constraints(
            store_e_aligned >= deviation,
            name="GlobalConstraint-animal_stability_pos",
        )
        m.add_constraints(
            store_e_aligned >= -deviation,
            name="GlobalConstraint-animal_stability_neg",
        )

        logger.info(
            "Added %d animal production L1 store constraints (mode=%s)",
            len(common_index),
            deviation_type,
        )
    else:  # quadratic
        store_p = m.variables["Store-p"].sel(snapshot="now", name=common_store_names)
        store_p_aligned = store_p.assign_coords(name=common_index).rename(
            {"name": "group"}
        )

        m.add_constraints(
            store_p_aligned == deviation,
            name="GlobalConstraint-animal_stability_dev",
        )

        logger.info(
            "Added %d animal production quadratic store constraints (mode=%s)",
            len(common_index),
            deviation_type,
        )

    # Build FLW lookup for zero-baseline check
    flw_multipliers: dict[tuple[str, str], float] = {}
    for _, row in loss_waste.iterrows():
        key = (str(row["country"]), str(row["food_group"]))
        loss_frac = float(row["loss_fraction"])
        waste_frac = float(row["waste_fraction"])
        flw_multipliers[key] = (1.0 - loss_frac) * (1.0 - waste_frac)

    # Add zero-production constraints for products missing from stores
    missing_in_stores = model_index.difference(store_index)
    if not missing_in_stores.empty:
        # Get animal baselines to verify these are actually zero
        target_series = animal_baseline.set_index(["product", "country"])[
            "production_mt"
        ].astype(float)

        # Adjust by FLW
        adjusted_targets = {}
        for product, country in target_series.index:
            gross_value = target_series.loc[(product, country)]
            group = food_to_group.get(product, product)
            multiplier = flw_multipliers.get((country, group), 1.0)
            adjusted_targets[(product, country)] = gross_value * multiplier

        # Check which missing groups have zero baseline
        zero_baseline_groups = [
            idx for idx in missing_in_stores if adjusted_targets.get(idx, 0.0) == 0.0
        ]

        if zero_baseline_groups:
            zero_index = pd.Index(zero_baseline_groups)
            lhs_zero = total_production.sel(group=zero_index)
            constr_name = "animal_production_zero"
            m.add_constraints(lhs_zero == 0, name=f"GlobalConstraint-{constr_name}")
            gc_names = [
                f"{constr_name}_{prod}_{country}" for prod, country in zero_index
            ]
            gc_products = [prod for prod, _country in zero_index]
            gc_countries = [country for _prod, country in zero_index]
            n.global_constraints.add(
                gc_names,
                sense="==",
                constant=0.0,
                type="production_stability",
                country=gc_countries,
                product=gc_products,
            )
            logger.info(
                "Added %d animal production constraints for zero-baseline (product, country) pairs",
                len(zero_index),
            )
