# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Consumption-basis irrigation delivery.

The regional water pool (tiered supply, AWARE scarcity, groundwater bands) is
on a *consumption* basis C -- the basin's net water loss, AWARE's own basis --
while crops need their beneficial evapotranspiration E (the GAEZ net irrigation
requirement). A per-region delivery link bridges the two:

    [water:{region}] --(irrigate:{region}, efficiency = eta_c)-->
        [water_field:{region}] <--(crop links, -E)

The link draws ``C = E / eta_c`` from the pool per unit E delivered; the
``(1 - eta_c)`` difference is non-beneficial consumption (canal and soil
evaporation that genuinely leaves the basin as vapour) and vanishes. Return
flow (withdrawal minus consumption, reused downstream in reality) is handled
implicitly: on a consumption basis it is simply never withdrawn.

``eta_c`` is calibrated per region so the baseline reproduces observed
consumption: ``eta_c = E_baseline / C``, with ``E_baseline`` the model's own
baseline irrigated areas times net requirements (summed over crop-production
links) and ``C`` the observed irrigation-consumption anchor (WaterGAP
``pirruse``, the same simulation and window as the supply envelope). The
ratio is clipped
to ``[eta_min, eta_max]`` and floored at ``E_baseline / pool`` so the
calibrated baseline draw never exceeds the region's pool (in overexploited
basins the pool is clipped below observed consumption); regions where the
floor or clip binds are logged as diagnostics.

Values of ``eta_c`` above 1 encode **deficit irrigation**: where the GAEZ
full net requirement exceeds the observed consumption (India, Pakistan,
Thailand, Sudan), real irrigation delivers less water than the
yield-maximising requirement, so one unit of pool consumption satisfies more
than one unit of nominal requirement. Capping at 1 instead would force the
model to draw the full GAEZ requirement and mine the difference. ``eta_max``
bounds the ratio where the anchor is unreliably small (tiny-consumption
regions), which would otherwise make marginal irrigation nearly free.
"""

import logging

import numpy as np
import pandas as pd
import pypsa

from .. import constants

logger = logging.getLogger(__name__)


def calibrate_eta_c(
    e_baseline_mm3: pd.Series,
    agri_consumption_mm3: pd.Series,
    pool_mm3: pd.Series,
    eta_min: float,
    eta_max: float,
) -> pd.Series:
    """Per-region consumptive irrigation efficiency ``eta_c`` in ``(0, eta_max]``.

    ``eta_c = clip(E_baseline / C, eta_min, eta_max)``, floored at
    ``E_baseline / pool`` for baseline feasibility. Note the floor and the
    infeasibility check below are both *annual*: at ``temporal_resolution > 1``
    surface supply is period-bound, so a region can clear the annual test while
    an individual period's demand still exceeds that period's surface. Those
    periods are met from the annual groundwater bands; under the current_use
    availability source (no bands) they fall to slack instead. Values above 1 encode
    deficit irrigation (observed consumption below the GAEZ full requirement);
    see the module docstring. Regions without baseline irrigation (E = 0) or
    without observed consumption (C = 0) get 1.0 (no adjustment: the delivery
    link is then pass-through).
    """
    regions = e_baseline_mm3.index
    e = e_baseline_mm3.to_numpy(dtype=float)
    c = agri_consumption_mm3.reindex(regions).fillna(0.0).to_numpy(dtype=float)
    pool = pool_mm3.reindex(regions).fillna(0.0).to_numpy(dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where((e > 0) & (c > 0), e / np.where(c > 0, c, 1.0), 1.0)
        floor = np.where((e > 0) & (pool > 0), e / np.where(pool > 0, pool, 1.0), 0.0)

    clipped_low = (raw < eta_min) & (e > 0)
    clipped_high = (raw > eta_max) & (e > 0)
    deficit = (raw > 1.0) & (e > 0)
    eta = np.clip(raw, eta_min, eta_max)
    floored = floor > eta
    eta = np.minimum(np.maximum(eta, floor), eta_max)

    infeasible = (e > 0) & (e > eta_max * pool)
    if clipped_low.any():
        logger.info(
            "eta_c clipped up to eta_min=%.2f in %d regions (E/C below the "
            "physical range; data-quality flag): %s",
            eta_min,
            int(clipped_low.sum()),
            ", ".join(regions[clipped_low][:10]),
        )
    if deficit.any():
        logger.info(
            "eta_c above 1 (deficit irrigation: observed consumption below the "
            "GAEZ requirement) in %d regions; E-weighted mean ratio %.2f, "
            "capped at eta_max=%.1f in %d regions",
            int(deficit.sum()),
            float(e[deficit].sum() / c[deficit].sum()),
            eta_max,
            int(clipped_high.sum()),
        )
    if floored.any():
        logger.info(
            "eta_c floored at E_baseline/pool in %d regions (pool clipped below "
            "observed consumption; overexploited basins): %s",
            int(floored.sum()),
            ", ".join(regions[floored][:10]),
        )
    if infeasible.any():
        logger.warning(
            "Baseline irrigation requirement E exceeds the annual water pool "
            "even at eta_c=eta_max in %d regions (baseline-infeasible; expect "
            "slack or "
            "reallocation there): %s",
            int(infeasible.sum()),
            ", ".join(regions[infeasible][:10]),
        )
    return pd.Series(eta, index=regions, name="eta_c")


def add_irrigation_delivery(
    n: pypsa.Network,
    agri_consumption_m3: pd.Series,
    water_tiers: pd.DataFrame,
    groundwater_bands: pd.DataFrame,
    water_regions: list[str],
    water_periods: int,
    eta_min: float,
    eta_max: float,
    consumed_fraction: float,
) -> None:
    """Add per-region, per-period ``irrigate:{region}:p{p}`` delivery links.

    Must run after the crop-production links are added: ``E_baseline`` is
    computed from their ``baseline_area_mha`` and water coefficients. Water sits
    on the per-period buses ``bus2 .. bus(1 + T)`` (efficiency = -requirement
    share, m3/ha); summing them recovers the crop's full requirement. One
    delivery link is added per (region, period), each converting pool
    consumption on ``water:{region}:p{p}`` to beneficial ET on
    ``water_field:{region}:p{p}`` at the region's calibrated ``eta_c``. The
    ``consumed_fraction`` (C/W) is stashed in ``n.meta`` for the analysis.
    """
    regions = pd.Index(sorted(water_regions), dtype="object")
    if regions.empty:
        return

    # E_baseline per region: baseline irrigated area x net requirement, summed
    # over all period water buses on the crop-production links (single-crop AND
    # multi-cropping: once irrigated baseline area moves onto multi links, omitting
    # them would under-count E_baseline in double-cropped regions and mis-calibrate
    # eta_c there). The per-port scan already handles the multi links' T-period
    # water block. The Mha * m3/ha product is in Mm3 (design 6.4).
    links = n.links.static
    crop = links[links["carrier"].isin(["crop_production", "crop_production_multi"])]
    water_eff_total = pd.Series(0.0, index=crop.index)
    bus_cols = [c for c in crop.columns if c.startswith("bus") and c[3:].isdigit()]
    for bus_col in bus_cols:
        eff_col = "efficiency" + bus_col[3:]
        if eff_col not in crop.columns:
            continue
        is_field = crop[bus_col].astype(str).str.startswith("water_field:")
        water_eff_total = water_eff_total.add(
            crop[eff_col].where(is_field, 0.0), fill_value=0.0
        )
    e_baseline = (
        (crop["baseline_area_mha"].fillna(0.0) * (-water_eff_total))
        .groupby(crop["region"])
        .sum()
        .reindex(regions)
        .fillna(0.0)
    )

    # Total available water per region = surface tiers + annual groundwater bands
    # (both feed the region's water buses). The eta_c floor E_baseline/pool uses
    # this so the calibrated baseline draw fits the full envelope, not just the
    # now-tightened surface.
    surface_pool = water_tiers.groupby("region")["capacity_mm3"].sum()
    gw_pool = groundwater_bands.groupby("region")["capacity_mm3"].sum()
    pool = surface_pool.add(gw_pool, fill_value=0.0)
    agri_mm3 = agri_consumption_m3 * constants.MM3_PER_M3
    eta_c = calibrate_eta_c(e_baseline, agri_mm3, pool, eta_min, eta_max)

    # One delivery link per (region, period). Built vectorised: regions repeated
    # T-fold, periods tiled, then string-concatenated into the bus/link names.
    n_periods = int(water_periods)
    region_col = pd.Series(np.repeat(regions.to_numpy(), n_periods))
    period_col = pd.Series(np.tile(np.arange(n_periods), len(regions))).astype(str)
    suffix = region_col.astype(str) + ":p" + period_col

    n.carriers.add("irrigation_delivery", unit="Mm^3")
    n.links.add(
        ("irrigate:" + suffix).to_numpy(),
        bus0=("water:" + suffix).to_numpy(),
        bus1=("water_field:" + suffix).to_numpy(),
        carrier="irrigation_delivery",
        efficiency=region_col.map(eta_c).to_numpy(),
        p_nom_extendable=True,
        region=region_col.to_numpy(),
    )

    n.meta["water_consumed_fraction"] = float(consumed_fraction)
    logger.info(
        "Added %d irrigation delivery links (%d regions x %d periods); global "
        "E_baseline %.0f Mm3, implied baseline consumption %.0f Mm3 "
        "(consumption anchor %.0f Mm3)",
        len(suffix),
        len(regions),
        int(water_periods),
        float(e_baseline.sum()),
        float((e_baseline / eta_c).sum()),
        float(agri_mm3.reindex(regions).fillna(0.0).sum()),
    )
