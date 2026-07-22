"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Compose the scenario-agnostic water-supply tables from the per-month
availability curves, grouping months into the model's temporal periods
(``water.temporal_resolution``).

The upstream availability stage (``build_region_water_aware`` /
``process_huang_irrigation_water``) emits a convex tier curve per region *and
month*. This stage groups whole months into ``temporal_resolution`` equal
periods (month ``m`` -> period ``(m - 1) * T // 12``), re-merges the monthly
curves within each period into a single convex merit-order surface curve, and
produces two tables the model build consumes:

- ``region_water_tiers.csv`` (``region, period, tier, capacity_mm3, marginal_cf,
  source``): the **per-period surface** supply. Surface is period-bound -- a
  river cannot be pumped next season without a reservoir -- so each period's cap
  binds its own draw.
- ``region_groundwater_bands.csv`` (``region, source, band, capacity_mm3,
  marginal_cf``): the **annual per-region groundwater** bands, emitted for the
  ``aware`` availability source. Groundwater is an aquifer, an annual buffer
  that can be pumped in any period, so it attaches to a single per-region
  groundwater bus in the model build and is shared across periods rather than
  split per period. The renewable bands (``source = groundwater_renewable``)
  carry the AWARE CF curve slice computed in ``build_region_water_aware`` (the
  upper part of the joint renewable envelope); the non-renewable / mined band
  (``source = groundwater_nonrenewable``) is a generous non-binding aquifer
  ceiling carrying a real pumping cost that orders it last. Because
  groundwater is additive rather than a relabel of surface, mining emerges
  endogenously wherever surface plus renewable groundwater fall short -- it is
  not capped at renewable availability. The ``current_use`` source emits no
  bands: its Huang withdrawal pool already contains the groundwater-supplied
  part, so additive bands would double-count it.

Keeping the month grouping here means changing the temporal resolution does not
re-run the expensive basin overlay.

``supply.scarcity_tiers`` finishes the tables: keep the convex scarcity curves
(per-period surface and annual renewable groundwater), or collapse each pool to
one flat cf = 0 tier/band -- a simple availability cap for studies where water
is not the focus.

The monthly and growing-season availability tables are copied through unchanged.
"""

from pathlib import Path
import shutil

import numpy as np
import pandas as pd

TIER_COLUMNS = ["region", "period", "tier", "capacity_mm3", "marginal_cf", "source"]
GW_BAND_COLUMNS = ["region", "source", "band", "capacity_mm3", "marginal_cf"]

# Number of equal-volume tiers the merged per-period convex curve is binned into
# (matches N_TIERS in build_region_water_aware).
N_TIERS = 8


def month_to_period(months: np.ndarray, temporal_resolution: int) -> np.ndarray:
    """Map calendar months (1..12) to period indices (0..T-1) in equal blocks.

    ``T`` must divide 12 (enforced by the config schema), so each period spans
    ``12 // T`` consecutive months.
    """
    return ((months.astype(int) - 1) * int(temporal_resolution)) // 12


def aggregate_months_to_periods(
    monthly_tiers: pd.DataFrame, temporal_resolution: int
) -> pd.DataFrame:
    """Group per-month tiers into per-period convex curves.

    ``monthly_tiers`` has columns ``region, month, tier, capacity_mm3,
    marginal_cf``. Returns ``region, period, tier, capacity_mm3, marginal_cf``
    with the merged convex curve re-binned to ``N_TIERS`` equal-volume tiers per
    region-period. Fully vectorised: the segments are sorted into merit order,
    an equal-volume tier index is assigned from the grouped cumulative volume,
    and each tier's marginal CF is the volume-weighted mean of its segments.
    """
    tiers = monthly_tiers.copy()
    tiers["period"] = month_to_period(tiers["month"].to_numpy(), temporal_resolution)
    if tiers.empty:
        return pd.DataFrame(
            columns=["region", "period", "tier", "capacity_mm3", "marginal_cf"]
        )

    keys = ["region", "period"]
    tiers = tiers.sort_values([*keys, "marginal_cf"]).reset_index(drop=True)
    volume = tiers["capacity_mm3"].to_numpy(dtype=float)
    grouped_vol = tiers.groupby(keys, sort=False)["capacity_mm3"]
    total = grouped_vol.transform("sum").to_numpy()
    cum_start = grouped_vol.cumsum().to_numpy() - volume
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_tier = np.where(total > 0.0, cum_start / (total / N_TIERS), 0.0)
    tiers["tier"] = np.minimum(raw_tier.astype(int), N_TIERS - 1)
    tiers["cf_volume"] = tiers["marginal_cf"].to_numpy() * volume

    agg = (
        tiers.groupby([*keys, "tier"], sort=True)
        .agg(capacity_mm3=("capacity_mm3", "sum"), cf_volume=("cf_volume", "sum"))
        .reset_index()
    )
    agg = agg[agg["capacity_mm3"] > 0.0].copy()
    agg["marginal_cf"] = agg["cf_volume"] / agg["capacity_mm3"]
    # Renumber tiers densely 0..k-1 within each region-period (bins can be empty).
    agg["tier"] = agg.groupby(keys, sort=False).cumcount()
    return agg[["region", "period", "tier", "capacity_mm3", "marginal_cf"]].reset_index(
        drop=True
    )


def collapse_single(tiers: pd.DataFrame) -> pd.DataFrame:
    """One flat renewable tier per region-period (cf = 0): a simple hard cap."""
    collapsed = tiers.groupby(["region", "period"], as_index=False)[
        "capacity_mm3"
    ].sum()
    collapsed["tier"] = 0
    collapsed["marginal_cf"] = 0.0
    collapsed["source"] = "renewable"
    return collapsed[TIER_COLUMNS]


def build_groundwater_bands(
    renewable_gw_tiers: pd.DataFrame,
    surface_tiers: pd.DataFrame,
    agri_consumption_mm3: pd.Series,
    ceiling_factor: float,
    scarcity_tiers: bool,
) -> pd.DataFrame:
    """Annual per-region groundwater bands (renewable + non-renewable).

    All bands are *annual*: an aquifer integrates recharge over the year and can
    be pumped in any period, so -- unlike surface, which is period-bound -- they
    attach to a single per-region groundwater bus in the model build, shared
    across all periods.

    - ``groundwater_renewable``: the AWARE CF bands of the renewable-groundwater
      slice of the joint renewable envelope (``renewable_gw_tiers``, from
      ``build_region_water_aware``). With ``scarcity_tiers`` off they collapse
      to one flat cf = 0 band per region, mirroring the surface collapse.
    - ``groundwater_nonrenewable``: a generous non-binding aquifer ceiling
      (``ceiling_factor * C`` with C the annual consumption anchor, falling
      back to the region's total surface capacity where C is zero), cf 0,
      ordered last by its pumping cost. The volume actually mined is set
      endogenously by how far surface plus renewable groundwater fall short of
      demand.
    """
    renewable = renewable_gw_tiers.rename(columns={"tier": "band"}).assign(
        source="groundwater_renewable"
    )
    if not scarcity_tiers and not renewable.empty:
        renewable = (
            renewable.groupby("region", as_index=False)["capacity_mm3"]
            .sum()
            .assign(band=0, marginal_cf=0.0, source="groundwater_renewable")
        )

    regions = pd.Index(
        sorted(
            set(renewable["region"])
            | set(agri_consumption_mm3.index)
            | set(surface_tiers["region"])
        ),
        name="region",
    )
    surface_sum = (
        surface_tiers.groupby("region")["capacity_mm3"]
        .sum()
        .reindex(regions)
        .fillna(0.0)
    )
    consumption = agri_consumption_mm3.reindex(regions).fillna(0.0)
    # Ceiling anchor: annual consumption, falling back to surface scale where C = 0.
    anchor = consumption.where(consumption > 0.0, surface_sum)
    non = pd.DataFrame(
        {
            "region": regions,
            "source": "groundwater_nonrenewable",
            "band": 0,
            "capacity_mm3": (ceiling_factor * anchor).to_numpy(),
            "marginal_cf": 0.0,
        }
    )

    bands = pd.concat([renewable, non], ignore_index=True)
    bands = bands[bands["capacity_mm3"] > 0.0]
    return (
        bands[GW_BAND_COLUMNS]
        .sort_values(["region", "source", "band"])
        .reset_index(drop=True)
    )


if __name__ == "__main__":
    scarcity_tiers = bool(snakemake.params.scarcity_tiers)  # type: ignore[name-defined]
    availability: str = snakemake.params.availability  # type: ignore[name-defined]
    temporal_resolution = int(snakemake.params.temporal_resolution)  # type: ignore[name-defined]
    consumed_fraction = float(snakemake.params.consumed_fraction)  # type: ignore[name-defined]
    monthly_tiers = pd.read_csv(snakemake.input.tiers)  # type: ignore[name-defined]

    # The Huang-based current_use pool is a *withdrawal* volume; the model draws
    # water on a consumption basis, so convert the pool via the consumed
    # fraction (C/W) to keep the "cap at current use" semantics.
    if availability == "current_use":
        monthly_tiers["capacity_mm3"] *= consumed_fraction

    tiers = aggregate_months_to_periods(monthly_tiers, temporal_resolution)

    # Surface supply: per-region-period tiers (region_water_tiers.csv), either the
    # convex scarcity curve or one flat cap per region-period.
    if scarcity_tiers:
        surface = tiers.assign(source="renewable")[TIER_COLUMNS]
    else:
        surface = collapse_single(tiers)

    # Groundwater supply: annual per-region bands (region_groundwater_bands.csv),
    # aware source only (the current_use pool already contains the
    # groundwater-supplied part of observed use).
    if availability == "aware":
        ceiling_factor = float(snakemake.params.groundwater_ceiling_factor)  # type: ignore[name-defined]
        renewable_gw_tiers = pd.read_csv(snakemake.input.renewable_gw_tiers)  # type: ignore[name-defined]
        agri_consumption_mm3 = (
            pd.read_csv(snakemake.input.region_agri).set_index("region")[  # type: ignore[name-defined]
                "agri_consumption_m3"
            ]
            * 1e-6
        )
        bands = build_groundwater_bands(
            renewable_gw_tiers,
            tiers,
            agri_consumption_mm3,
            ceiling_factor,
            scarcity_tiers,
        )
    else:
        bands = pd.DataFrame(columns=GW_BAND_COLUMNS)

    out_tiers = Path(snakemake.output.tiers)  # type: ignore[name-defined]
    out_tiers.parent.mkdir(parents=True, exist_ok=True)
    surface.sort_values(["region", "period", "tier"]).to_csv(out_tiers, index=False)

    out_bands = Path(snakemake.output.groundwater_bands)  # type: ignore[name-defined]
    out_bands.parent.mkdir(parents=True, exist_ok=True)
    bands.to_csv(out_bands, index=False)

    # The monthly and growing-season availability tables pass through unchanged.
    shutil.copy(snakemake.input.monthly, snakemake.output.monthly_region)  # type: ignore[name-defined]
    shutil.copy(snakemake.input.growing, snakemake.output.region_growing)  # type: ignore[name-defined]
