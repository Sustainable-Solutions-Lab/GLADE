# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract AWARE water-scarcity metrics from solved networks.

The tiered water supply (carrier ``water_supply``) converts a free source bus
into regional water (bus1 = ``water:{region}``) and tallies scarcity onto
``impact:water_scarcity`` via ``efficiency2`` = the tier's marginal
characterisation factor (CF, m3 world-equivalent per m3). Drawing the link
(``p0`` > 0) therefore both supplies irrigation water and accumulates scarcity.

Tiers carry a ``source``: ``renewable`` (surface) and ``groundwater_renewable``
tiers accumulate AWARE scarcity via ``efficiency2`` = the marginal CF (the
latter additionally tally their drawn volume on
``impact:groundwater_renewable``), while ``groundwater_nonrenewable`` tiers
accumulate mined volume 1:1 on ``impact:groundwater_depletion`` (their
``efficiency2`` is 1, not a CF). This module reads those links to report, per
region and in total:
- withdrawn irrigation volume (Mm3), all sources,
- accumulated water scarcity (Mm3 world-equivalent) = sum(CF * draw) over
  CF-carrying (surface + renewable groundwater) tiers,
- the volume-weighted mean CF of the CF-carrying water drawn,
- renewable groundwater volume (Mm3) from ``groundwater_renewable`` tiers,
- non-renewable groundwater depletion (Mm3 mined) from
  ``groundwater_nonrenewable`` tiers.

It also exposes the per-tier (CF, draw, source) table used to bin withdrawn
water by the scarcity of its source (the merit-order / withdrawal-by-CF figure).

Basis: the tier draw is *consumption* C -- crops need their net requirement E
and the ``irrigation_delivery`` link draws ``C = E / eta_c`` from the pool.
``withdrawn_mm3`` therefore reports consumption; the estimated physical
withdrawal is ``withdrawal_reported_mm3 = withdrawn_mm3 / consumed_fraction``
with the consumed fraction (C/W) stashed in ``n.meta`` at build time.

Units: the produce-link identity ``m3/ha * Mha = Mm3`` means tier draws and
the scarcity tally are already in Mm3 and Mm3-world-eq (see the AWARE design
note); no scale factor is applied here. The global scarcity and depletion
stores (``store:impact:water_scarcity`` / ``store:impact:groundwater_depletion``)
equal ``scarcity_mm3_eq`` / ``groundwater_depletion_mm3`` summed over regions,
modulo solver tolerance.
"""

import pandas as pd
import pypsa

NONRENEWABLE = "groundwater_nonrenewable"
RENEWABLE_GW = "groundwater_renewable"


def _water_supply_draw(n: pypsa.Network) -> pd.DataFrame:
    """Return a per-tier-link frame with region, CF, drawn volume (Mm3), source."""
    links = n.links.static
    tiers = links[links["carrier"] == "water_supply"]
    if tiers.empty:
        # Configs with no tiered supply: return an empty frame so the analysis
        # pipeline stays config-agnostic; callers that require it check emptiness.
        return pd.DataFrame(columns=["region", "cf", "draw_mm3", "source"])
    draw = n.links.dynamic.p0.iloc[0].reindex(tiers.index).clip(lower=0.0)
    # Pre-source solved networks had only renewable tiers.
    source = tiers["source"].to_numpy() if "source" in tiers.columns else "renewable"
    return pd.DataFrame(
        {
            "region": tiers["region"].to_numpy(),
            "cf": tiers["efficiency2"].to_numpy(),
            "draw_mm3": draw.to_numpy(),
            "source": source,
        },
        index=tiers.index,
    )


def extract_water_tiers(n: pypsa.Network) -> pd.DataFrame:
    """Per-tier (region, cf, draw_mm3) table; the input to CF-binned views.

    One row per ``water_supply`` link. ``cf`` is the tier's marginal
    characterisation factor; ``draw_mm3`` is the volume drawn from it.
    """
    return _water_supply_draw(n).reset_index(names="link")


def extract_water_by_region(n: pypsa.Network) -> pd.DataFrame:
    """Per-region water metrics.

    Columns: region, withdrawn_mm3 (all sources), scarcity_mm3_eq (CF-carrying
    tiers: surface + renewable groundwater), groundwater_renewable_mm3,
    groundwater_depletion_mm3 (non-renewable tiers), mean_cf (the draw-weighted
    mean CF of the CF-carrying water; NaN where none is drawn).
    """
    tiers = _water_supply_draw(n)
    is_nonrenew = tiers["source"] == NONRENEWABLE
    cf_carrying = tiers[~is_nonrenew].copy()
    cf_carrying["scarcity"] = cf_carrying["cf"] * cf_carrying["draw_mm3"]

    regions = pd.Index(sorted(tiers["region"].unique()), name="region")
    withdrawn = tiers.groupby("region")["draw_mm3"].sum()
    cf_withdrawn = cf_carrying.groupby("region")["draw_mm3"].sum()
    scarcity = cf_carrying.groupby("region")["scarcity"].sum()
    renewable_gw = (
        tiers[tiers["source"] == RENEWABLE_GW].groupby("region")["draw_mm3"].sum()
    )
    depletion = tiers[is_nonrenew].groupby("region")["draw_mm3"].sum()

    grouped = pd.DataFrame(index=regions)
    grouped["withdrawn_mm3"] = withdrawn.reindex(regions).fillna(0.0)
    grouped["withdrawal_reported_mm3"] = grouped["withdrawn_mm3"] / _consumed_fraction(
        n
    )
    grouped["scarcity_mm3_eq"] = scarcity.reindex(regions).fillna(0.0)
    grouped["groundwater_renewable_mm3"] = renewable_gw.reindex(regions).fillna(0.0)
    grouped["groundwater_depletion_mm3"] = depletion.reindex(regions).fillna(0.0)
    cfw = cf_withdrawn.reindex(regions).fillna(0.0)
    grouped["mean_cf"] = (grouped["scarcity_mm3_eq"] / cfw).where(cfw > 0)
    return grouped.reset_index()


def _consumed_fraction(n: pypsa.Network) -> float:
    """The build-time consumed fraction C/W used for withdrawal reporting."""
    return float(n.meta["water_consumed_fraction"])


def extract_water_totals(n: pypsa.Network) -> pd.Series:
    """Global totals: withdrawn_mm3, scarcity_mm3_eq, groundwater_renewable_mm3,
    groundwater_depletion_mm3, mean_cf.

    ``scarcity_mm3_eq`` / ``groundwater_depletion_mm3`` match the
    ``store:impact:water_scarcity`` / ``store:impact:groundwater_depletion``
    final values (modulo solver tolerance); we recompute them from the tier
    draws so the same code path also yields the regional and per-tier breakdowns.
    """
    tiers = _water_supply_draw(n)
    is_nonrenew = tiers["source"] == NONRENEWABLE
    cf_carrying = tiers[~is_nonrenew]
    withdrawn = float(tiers["draw_mm3"].sum())
    cf_withdrawn = float(cf_carrying["draw_mm3"].sum())
    scarcity = float((cf_carrying["cf"] * cf_carrying["draw_mm3"]).sum())
    renewable_gw = float(tiers.loc[tiers["source"] == RENEWABLE_GW, "draw_mm3"].sum())
    depletion = float(tiers.loc[is_nonrenew, "draw_mm3"].sum())
    mean_cf = scarcity / cf_withdrawn if cf_withdrawn > 0 else float("nan")
    return pd.Series(
        {
            "withdrawn_mm3": withdrawn,
            "withdrawal_reported_mm3": withdrawn / _consumed_fraction(n),
            "scarcity_mm3_eq": scarcity,
            "groundwater_renewable_mm3": renewable_gw,
            "groundwater_depletion_mm3": depletion,
            "mean_cf": mean_cf,
        }
    )
