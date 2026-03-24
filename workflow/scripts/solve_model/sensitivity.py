# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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
- Food loss and waste (efficiency on food_processing links)
- Feed conversion ratios (efficiency on animal_production links)
- Production costs (marginal_cost on crop_production, animal_production)

Health relative risk sensitivity is handled at solve time in
workflow/scripts/solve_model/health.py via per-risk-factor quantile
interpolation between GBD confidence bounds.
"""

import logging

import pypsa

logger = logging.getLogger(__name__)


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
        - food_loss_waste: float
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

    flw_factor = sensitivity_cfg.get("food_loss_waste", 1.0)
    if flw_factor != 1.0:
        _apply_flw_factor(n, flw_factor)

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


def _apply_flw_factor(n: pypsa.Network, factor: float) -> None:
    """Scale food processing efficiencies to represent FLW uncertainty.

    The efficiency on food_processing links incorporates food loss and waste
    fractions. Scaling by `factor` adjusts the effective survival rate:
    factor < 1.0 means more loss (FLW underestimated in baseline),
    factor > 1.0 means less loss (FLW overestimated).

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    factor : float
        Multiplicative factor for food_processing link efficiencies.
    """
    mask = n.links.static["carrier"] == "food_processing"
    if not mask.any():
        logger.debug("No food_processing links found for FLW adjustment")
        return
    n.links.static.loc[mask, "efficiency"] *= factor
    logger.info(
        "Applied FLW factor %.3f to %d food_processing links",
        factor,
        mask.sum(),
    )


def _apply_fcr_factor(n: pypsa.Network, factor: float) -> None:
    """Scale feed conversion efficiencies on animal production links.

    Higher factor means better conversion (more product per unit feed).

    Parameters
    ----------
    n : pypsa.Network
        Network to modify.
    factor : float
        Multiplicative factor for animal_production link efficiencies.
    """
    mask = n.links.static["carrier"] == "animal_production"
    if not mask.any():
        logger.debug("No animal_production links found for FCR adjustment")
        return
    n.links.static.loc[mask, "efficiency"] *= factor
    logger.info(
        "Applied FCR factor %.3f to %d animal_production links",
        factor,
        mask.sum(),
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
