# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensitivity adjustment module for parameter uncertainty analysis.

This module applies multiplicative adjustment factors to network component
properties after the model is built but before export. This enables sensitivity
analysis by varying parameters within their uncertainty bounds.

Supported adjustments:
- Crop yields (efficiency on crop_production links)
- Emission factors: CH4 (animal_production + rice crop_production),
  N2O (animal_production + fertilizer_distribution + residue_incorporation),
  CO2 (land_conversion)
- Food loss (loss_fraction on crop_production / crop_production_multi /
  animal_production links)
- Food waste (waste_fraction on food_consumption links)
- Food loss + waste bundle (food_loss_waste applies the same factor to
  both unless an explicit food_loss / food_waste key overrides it)
- Feed conversion ratios (efficiency on animal_production links)
- Production costs (marginal_cost on crop_production, animal_production)

Convention for loss and waste factors: ``factor`` multiplies the
underlying *fraction*. ``factor = 1.5`` raises the loss/waste fraction
by 50% (e.g. 10% loss becomes 15%); ``factor = 0.5`` halves it (10%
becomes 5%); ``factor = 1.0`` is a no-op. Fractions are clipped to
[0, 0.99] after scaling.

Note: food_loss is mathematically indistinguishable from crop_yields
on crop links and from feed_conversion on animal links because all
three scale the same link efficiency. Enabling them in the same
sensitivity sample produces confounded effects.

Health relative risk sensitivity is handled at solve time in
workflow/scripts/solve_model/health.py via per-risk-factor quantile
interpolation between GBD confidence bounds.
"""

import logging
import re

import pandas as pd
import pypsa

logger = logging.getLogger(__name__)

# Loss/waste fractions are clipped to this maximum after scaling so a
# large factor cannot drive the survival multiplier to (or below) zero.
_MAX_LOSS_FRACTION = 0.99


_BUS_COL_PATTERN = re.compile(r"^bus(\d+)$")


def _output_port_columns(links: pd.DataFrame) -> list[tuple[str, str, str]]:
    """List (bus_col, eff_col, suffix) tuples for every output port on `links`.

    Output ports are bus1..busN (the input port lives at bus0 with no
    secondary efficiency column). The matching efficiency column is
    ``efficiency`` for bus1 and ``efficiency{N}`` for N >= 2 -- this is
    the PyPSA convention baked into ``links.add()`` calls in
    ``build_model/*``.

    Returned tuples are ordered by N so iteration is deterministic, and
    only ports whose efficiency column actually exists on the frame are
    included. The ``suffix`` element ("1", "2", ...) is the bare bus
    number as a string, useful for deriving sibling columns like
    ``loss_multiplier{N}``.

    This helper exists so the bus-iteration pattern (which used to be
    duplicated across sensitivity scalers) reads consistently and the
    bus1-vs-busN naming quirk is documented in one place.
    """
    pairs: list[tuple[int, str, str, str]] = []
    for col in links.columns:
        match = _BUS_COL_PATTERN.match(col)
        if match is None:
            continue
        n = int(match.group(1))
        if n < 1:
            continue
        suffix = match.group(1)
        eff_col = "efficiency" if n == 1 else f"efficiency{n}"
        if eff_col not in links.columns:
            continue
        pairs.append((n, col, eff_col, suffix))
    pairs.sort()
    return [(bus_col, eff_col, suffix) for _, bus_col, eff_col, suffix in pairs]


def apply_sensitivity_factors(n: pypsa.Network, sensitivity_cfg: dict) -> None:
    """Apply sensitivity adjustment factors to network components in-place.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify (mutated in place).
    sensitivity_cfg : dict
        Sensitivity configuration with optional keys:
        - crop_yields: {all: float, by_crop: {crop: float}}
        - emission_factors: {ch4: float, n2o: float, luc: float}
        - food_loss: float multiplier on the loss_fraction baked into
          crop_production / crop_production_multi / animal_production
          efficiencies.
        - food_waste: float multiplier on the waste_fraction baked into
          food_consumption efficiencies and the ``flw_multiplier``
          metadata column.
        - food_loss_waste: float bundle convenience key. Applies the
          same factor to both loss and waste, unless an explicit
          food_loss or food_waste key is also set (per-component keys
          override the bundle).
        - feed_conversion: float
        - costs: {crop: float, animal: float}
    """
    if not sensitivity_cfg:
        return

    crop_yields_cfg = sensitivity_cfg.get("crop_yields", {})
    if crop_yields_cfg:
        _apply_crop_yield_factors(n, crop_yields_cfg)

    emission_cfg = sensitivity_cfg.get("emission_factors", {})
    if emission_cfg:
        _apply_emission_factors(n, emission_cfg)

    # food_loss_waste is a bundle: it sets both food_loss and food_waste
    # in one place. Per-component keys (food_loss, food_waste) take
    # precedence when both are supplied.
    bundle_factor = sensitivity_cfg.get("food_loss_waste", 1.0)
    food_loss_factor = sensitivity_cfg.get("food_loss", bundle_factor)
    food_waste_factor = sensitivity_cfg.get("food_waste", bundle_factor)

    if food_loss_factor != 1.0:
        _apply_food_loss_factor(n, food_loss_factor)

    if food_waste_factor != 1.0:
        _apply_food_waste_factor(n, food_waste_factor)

    fcr_factor = sensitivity_cfg.get("feed_conversion", 1.0)
    if fcr_factor != 1.0:
        _apply_fcr_factor(n, fcr_factor)

    costs_cfg = sensitivity_cfg.get("costs", {})
    if costs_cfg:
        _apply_cost_factors(n, costs_cfg)


def _apply_crop_yield_factors(n: pypsa.Network, cfg: dict) -> None:
    """Apply multiplicative factors to crop production yields.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    cfg : dict
        Configuration with optional keys:
        - all: float factor applied to all crops
        - by_crop: {crop_name: float factor} for crop-specific adjustments

    Notes
    -----
    Factors are applied multiplicatively. If both 'all' and 'by_crop' are
    specified, the all factor is applied first, then per-crop factors.
    """
    all_factor = cfg.get("all", 1.0)
    by_crop = cfg.get("by_crop", {})

    # Get crop production links
    mask = n.links.static["carrier"] == "crop_production"
    if not mask.any():
        logger.debug("No crop_production links found for yield adjustment")
        return

    efficiency = n.links.static.loc[mask, "efficiency"].copy()

    # Apply global factor
    if all_factor != 1.0:
        efficiency *= all_factor
        logger.info(
            "Applied global crop yield factor %.3f to %d links",
            all_factor,
            mask.sum(),
        )

    # Apply per-crop factors
    for crop, factor in by_crop.items():
        if factor == 1.0:
            continue
        crop_mask = n.links.static.loc[mask, "crop"] == crop
        if not crop_mask.any():
            logger.warning("No crop_production links found for crop '%s'", crop)
            continue
        efficiency.loc[crop_mask] *= factor
        logger.info(
            "Applied crop-specific yield factor %.3f to %d '%s' links",
            factor,
            crop_mask.sum(),
            crop,
        )

    # Write back
    n.links.static.loc[mask, "efficiency"] = efficiency


def _apply_emission_factors(n: pypsa.Network, cfg: dict) -> None:
    """Apply multiplicative factors to emission efficiencies.

    Scales ALL emission sources for each gas:
    - CH4: animal_production (efficiency2) + rice crop_production (efficiency4)
    - N2O: animal_production (efficiency4) + fertilizer_distribution (efficiency2)
           + residue_incorporation (efficiency, via bus1)
    - LUC: land_conversion + new_to_pasture (efficiency2)

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    cfg : dict
        Configuration with optional keys:
        - ch4: factor for all CH4 emissions
        - n2o: factor for all N2O emissions
        - luc: factor for land-use change CO2
    """
    ch4_factor = cfg.get("ch4", 1.0)
    n2o_factor = cfg.get("n2o", 1.0)
    luc_factor = cfg.get("luc", 1.0)

    if ch4_factor != 1.0:
        # CH4 from animal production (enteric + manure, via efficiency2)
        animal_mask = n.links.static["carrier"] == "animal_production"
        if animal_mask.any():
            n.links.static.loc[animal_mask, "efficiency2"] *= ch4_factor
            logger.info(
                "Applied CH4 factor %.3f to %d animal_production links",
                ch4_factor,
                animal_mask.sum(),
            )

        # CH4 from rice cultivation (via efficiency4 on crop_production links)
        rice_ch4_mask = (n.links.static["carrier"] == "crop_production") & (
            n.links.static.get("bus4", "") == "emission:ch4"
        )
        if rice_ch4_mask.any():
            n.links.static.loc[rice_ch4_mask, "efficiency4"] *= ch4_factor
            logger.info(
                "Applied CH4 factor %.3f to %d rice crop_production links",
                ch4_factor,
                rice_ch4_mask.sum(),
            )

    if n2o_factor != 1.0:
        # N2O from animal production (manure, via efficiency4)
        animal_mask = n.links.static["carrier"] == "animal_production"
        if animal_mask.any():
            n.links.static.loc[animal_mask, "efficiency4"] *= n2o_factor
            logger.info(
                "Applied N2O factor %.3f to %d animal_production links",
                n2o_factor,
                animal_mask.sum(),
            )

        # N2O from synthetic fertiliser (via efficiency2)
        fert_mask = n.links.static["carrier"] == "fertilizer_distribution"
        if fert_mask.any():
            n.links.static.loc[fert_mask, "efficiency2"] *= n2o_factor
            logger.info(
                "Applied N2O factor %.3f to %d fertilizer_distribution links",
                n2o_factor,
                fert_mask.sum(),
            )

        # N2O from residue incorporation (via efficiency, bus1 = emission:n2o)
        residue_mask = n.links.static["carrier"] == "residue_incorporation"
        if residue_mask.any():
            n.links.static.loc[residue_mask, "efficiency"] *= n2o_factor
            logger.info(
                "Applied N2O factor %.3f to %d residue_incorporation links",
                n2o_factor,
                residue_mask.sum(),
            )

    # LUC CO2 from land conversion and sparing (all carriers using LEF data)
    if luc_factor != 1.0:
        luc_mask = n.links.static["carrier"].isin(
            [
                "land_conversion",
                "new_to_pasture",
                "spare_land",
                "spare_existing_grassland",
            ]
        )
        if luc_mask.any():
            n.links.static.loc[luc_mask, "efficiency2"] *= luc_factor
            logger.info(
                "Applied LUC emission factor %.3f to %d land conversion/sparing links",
                luc_factor,
                luc_mask.sum(),
            )
        else:
            logger.debug(
                "No land conversion/sparing links found for LUC emission adjustment"
            )


def _apply_food_loss_factor(n: pypsa.Network, factor: float) -> None:
    """Scale the supply-chain loss fraction on food-producing links.

    ``factor`` multiplies the loss fraction itself: ``factor = 1.5``
    raises a 10% loss to 15%, ``factor = 0.5`` lowers it to 5%, and
    ``factor = 1.0`` is a no-op. Loss fractions are clipped to
    ``[0, _MAX_LOSS_FRACTION]`` after scaling.

    Loss is encoded at build time as ``efficiency *= (1 - loss_fraction)``
    on crop_production / crop_production_multi / animal_production
    links, with the original survival multiplier preserved on a
    per-output ``loss_multiplier[N]`` column. For each affected output
    we recover ``loss_fraction = 1 - loss_multiplier``, rescale, and
    apply the ratio ``new_multiplier / loss_multiplier`` to the
    matching efficiency column. Co-product outputs on animal links
    (bus5+) are scaled alongside the primary product because the build
    derives them from the same adjusted efficiency.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify in place.
    factor : float
        Multiplicative factor on the loss fraction (non-negative).
    """
    crop_carriers = ["crop_production", "crop_production_multi"]
    crop_mask = n.links.static["carrier"].isin(crop_carriers)
    crop_scaled = _scale_loss_on_links(
        n,
        mask=crop_mask,
        factor=factor,
        food_carrier_prefix="crop_",
    )
    if crop_mask.any():
        logger.info(
            "Applied food loss factor %.3f to %d crop outputs on crop production links",
            factor,
            crop_scaled,
        )
    else:
        logger.debug("No crop production links found for food loss adjustment")

    animal_mask = n.links.static["carrier"] == "animal_production"
    animal_scaled = _scale_loss_on_links(
        n,
        mask=animal_mask,
        factor=factor,
        food_carrier_prefix="food_",
    )
    if animal_mask.any():
        logger.info(
            "Applied food loss factor %.3f to %d food outputs on animal_production links",
            factor,
            animal_scaled,
        )
    else:
        logger.debug("No animal_production links found for food loss adjustment")


def _scale_loss_on_links(
    n: pypsa.Network,
    *,
    mask,
    factor: float,
    food_carrier_prefix: str,
) -> int:
    """Rescale efficiency on food-output buses of the masked links.

    For each ``efficiency[N]`` column whose paired ``bus[N]`` carries a
    bus whose ``carrier`` starts with ``food_carrier_prefix`` (e.g.
    ``crop_`` or ``food_``), recover the loss multiplier from
    ``loss_multiplier[N]``, rescale the loss fraction by ``factor``,
    and update both the efficiency and the stored loss multiplier.
    Returns the number of (link, output-bus) pairs that were rescaled.

    Raises
    ------
    ValueError
        If a bus column on the masked links carries a food output but
        the matching ``loss_multiplier[N]`` column is missing. Build code
        is expected to populate ``loss_multiplier{N}`` for every
        food-output bus on crop_production and animal_production links
        (see ``build_model/crops.py`` and ``build_model/animals.py``);
        a missing column would silently leak loss-rescaling on that
        port and bias mass balance under food-loss sweeps.
    """
    if not mask.any():
        return 0

    links = n.links.static.loc[mask]
    bus_carriers = n.buses.static["carrier"]

    scaled_pairs = 0
    for bus_col, eff_col, suffix in _output_port_columns(links):
        # `links[bus_col].map(bus_carriers)` lifts each link's target-bus
        # name to its carrier in one step (empty / NaN -> NaN -> "").
        carriers = links[bus_col].map(bus_carriers).fillna("")
        food_mask = carriers.str.startswith(food_carrier_prefix)
        if not food_mask.any():
            continue

        lm_col = "loss_multiplier" if suffix == "1" else f"loss_multiplier{suffix}"
        if lm_col not in links.columns:
            raise ValueError(
                f"Sensitivity food-loss scaling: links with {bus_col} pointing "
                f"at a {food_carrier_prefix!r} carrier but no '{lm_col}' "
                "column. Build code must populate a per-bus loss_multiplier "
                "for every food-output port so loss sweeps stay mass-consistent."
            )

        target_idx = food_mask.index[food_mask]
        old_mult = n.links.static.loc[target_idx, lm_col].astype(float)
        valid = old_mult.notna() & (old_mult > 0)
        if not valid.any():
            continue
        idx = old_mult.index[valid]
        old_mult = old_mult.loc[idx]
        new_loss = (factor * (1.0 - old_mult)).clip(lower=0.0, upper=_MAX_LOSS_FRACTION)
        new_mult = 1.0 - new_loss
        ratio = new_mult / old_mult
        n.links.static.loc[idx, eff_col] = (
            n.links.static.loc[idx, eff_col].astype(float) * ratio
        )
        n.links.static.loc[idx, lm_col] = new_mult
        scaled_pairs += int(len(idx))

    return scaled_pairs


def _apply_food_waste_factor(n: pypsa.Network, factor: float) -> None:
    """Scale the consumer-side waste fraction on food_consumption links.

    ``factor`` multiplies the waste fraction: ``factor = 1.5`` raises a
    20% waste to 30%, ``factor = 0.5`` lowers it to 10%, and
    ``factor = 1.0`` is a no-op. Waste fractions are clipped to
    ``[0, _MAX_LOSS_FRACTION]`` after scaling.

    Waste is encoded on food_consumption links by multiplying every
    nutrient/group efficiency by ``flw_multiplier = 1 - waste_fraction``
    and storing the multiplier on the link. Here we recover the waste
    fraction from ``flw_multiplier``, rescale, and apply the ratio to
    every efficiency column on the link. The ``flw_multiplier`` column
    is kept in sync so ``_match_baseline_to_consume_links`` reads the
    scaled value when converting intake targets to bus flows.

    Build contract this relies on (see ``build_model/nutrition.py:_add_links``):

    - Every ``efficiency{i}`` on food_consumption links is built as
      ``raw_density * flw_multiplier`` -- nutrient efficiencies on
      bus1..busN-1 carry the multiplier, and the optional group bus
      stores the multiplier directly. There are no non-multiplied
      efficiency columns on these links.

    If a future build change adds an efficiency column to
    food_consumption that is NOT proportional to flw_multiplier (e.g.
    a per-link emission, or a fixed cost-side coefficient), this
    blanket scaling would mis-scale it; consider adding such columns
    to a separately-named field instead so this loop stays correct.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify in place.
    factor : float
        Multiplicative factor on the waste fraction (non-negative).
    """
    mask = n.links.static["carrier"] == "food_consumption"
    if not mask.any():
        logger.debug("No food_consumption links found for food waste adjustment")
        return
    if "flw_multiplier" not in n.links.static.columns:
        logger.debug(
            "food_consumption links lack flw_multiplier metadata; skipping waste scaling"
        )
        return

    old_mult = n.links.static.loc[mask, "flw_multiplier"].astype(float)
    valid = old_mult.notna() & (old_mult > 0)
    if not valid.any():
        logger.debug("No food_consumption links have valid flw_multiplier; skipping")
        return

    idx = old_mult.index[valid]
    old_mult = old_mult.loc[idx]
    new_waste = (factor * (1.0 - old_mult)).clip(lower=0.0, upper=_MAX_LOSS_FRACTION)
    new_mult = 1.0 - new_waste
    ratio = new_mult / old_mult

    eff_cols = [c for c in n.links.static.columns if c.startswith("efficiency")]
    for eff_col in eff_cols:
        n.links.static.loc[idx, eff_col] = (
            n.links.static.loc[idx, eff_col].astype(float) * ratio
        )
    n.links.static.loc[idx, "flw_multiplier"] = new_mult

    logger.info(
        "Applied food waste factor %.3f to %d food_consumption links",
        factor,
        int(len(idx)),
    )


def _apply_fcr_factor(n: pypsa.Network, factor: float) -> None:
    """Scale feed conversion efficiencies on animal production links.

    Higher factor means better conversion (more product per unit feed).

    All food-output buses on animal_production links are scaled by the
    same factor, including any co-product buses (bus5+, e.g. tallow or
    lard), because the build derives their efficiencies from the same
    ``adjusted_efficiency`` as the primary product. Per-feed-unit
    outputs (CH4 on bus2, manure N on bus3, N2O on bus4) are not
    proportional to product yield and are left untouched.

    Build contract this relies on (see ``build_model/animals.py``):

    - Every product output on ``animal_production`` lands on a bus
      whose carrier starts with ``food_`` (primary product on bus1,
      co-products on bus5+).
    - Non-product outputs use distinguishable carriers
      (``emission:ch4`` on bus2, ``fertilizer:<country>`` on bus3,
      ``emission:n2o`` on bus4), so the carrier-prefix filter cleanly
      separates yield-proportional outputs from per-feed outputs.

    If a future build change routes a yield-proportional co-product
    to a non-``food_`` carrier (or vice versa), this scaler will
    silently mis-mass-balance the FCR sweep -- the assertion below
    catches the simplest version of that drift.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    factor : float
        Multiplicative factor for animal product efficiencies.

    Raises
    ------
    ValueError
        If any output port on animal_production links has neither a
        ``food_`` carrier nor one of the known per-feed carriers
        (``emission:ch4``, ``emission:n2o``, ``fertilizer:*``). The
        scaler would otherwise silently skip that port.
    """
    mask = n.links.static["carrier"] == "animal_production"
    if not mask.any():
        logger.debug("No animal_production links found for FCR adjustment")
        return

    links = n.links.static.loc[mask]
    bus_carriers = n.buses.static["carrier"]
    known_per_feed_buses = {"emission:ch4", "emission:n2o"}

    scaled_pairs = 0
    for bus_col, eff_col, _suffix in _output_port_columns(links):
        bus_names = links[bus_col].fillna("")
        nonempty = bus_names.ne("") & bus_names.ne("None")
        carriers = bus_names.map(bus_carriers).fillna("")
        food_mask = nonempty & carriers.str.startswith("food_")

        # Catch drift: any port that is neither a food output nor a
        # known per-feed bus would be silently skipped. The fertilizer
        # bus is per-country so we match it by bus-name prefix, not
        # by enumerating every fertilizer:{country} bus name.
        per_feed_mask = nonempty & (
            bus_names.isin(known_per_feed_buses)
            | bus_names.str.startswith("fertilizer:")
        )
        unclassified = nonempty & ~food_mask & ~per_feed_mask
        if unclassified.any():
            sample_bus = bus_names[unclassified].iloc[0]
            raise ValueError(
                f"Sensitivity FCR scaler: animal_production {bus_col} "
                f"contains an unclassified output bus ({sample_bus!r}). "
                "Either route it as a food_ carrier (yield-proportional, "
                "scaled by FCR) or extend known_per_feed_buses to "
                "cover the new per-feed output type."
            )
        if not food_mask.any():
            continue

        target_idx = food_mask.index[food_mask]
        n.links.static.loc[target_idx, eff_col] = (
            n.links.static.loc[target_idx, eff_col].astype(float) * factor
        )
        scaled_pairs += int(len(target_idx))

    logger.info(
        "Applied FCR factor %.3f to %d food outputs on %d animal_production links",
        factor,
        scaled_pairs,
        int(mask.sum()),
    )


def _apply_cost_factors(n: pypsa.Network, cfg: dict) -> None:
    """Apply multiplicative factors to production costs.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    cfg : dict
        Configuration with optional keys:
        - crop: factor for crop production marginal costs
        - animal: factor for animal production marginal costs
    """
    crop_factor = cfg.get("crop", 1.0)
    animal_factor = cfg.get("animal", 1.0)

    if crop_factor != 1.0:
        crop_mask = n.links.static["carrier"] == "crop_production"
        if crop_mask.any():
            n.links.static.loc[crop_mask, "marginal_cost"] *= crop_factor
            logger.info(
                "Applied crop cost factor %.3f to %d crop_production links",
                crop_factor,
                crop_mask.sum(),
            )
        else:
            logger.debug("No crop_production links found for cost adjustment")

    if animal_factor != 1.0:
        animal_mask = n.links.static["carrier"] == "animal_production"
        if animal_mask.any():
            n.links.static.loc[animal_mask, "marginal_cost"] *= animal_factor
            logger.info(
                "Applied animal cost factor %.3f to %d animal_production links",
                animal_factor,
                animal_mask.sum(),
            )
        else:
            logger.debug("No animal_production links found for cost adjustment")
