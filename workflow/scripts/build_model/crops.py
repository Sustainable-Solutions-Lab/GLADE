# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Crop production components for the food systems model.

This module handles all crop-related production links including regional
crop production, multi-cropping systems, grassland feed production, and
spared land allocation with carbon sequestration.
"""

from collections.abc import Mapping
import logging

import numpy as np
import pandas as pd
import pypsa

from .. import constants
from .utils import merge_lef

logger = logging.getLogger(__name__)


def compute_residue_n2o_efficiency_per_dm(
    residue_feed_items: list[str],
    ruminant_feed_mapping: pd.DataFrame,
    ruminant_feed_categories: pd.DataFrame,
    monogastric_feed_mapping: pd.DataFrame,
    monogastric_feed_categories: pd.DataFrame,
    incorporation_n2o_factor: float,
    indirect_ef5: float,
    frac_leach: float,
) -> dict[str, float]:
    """Per-residue-feed_item soil-incorporation N2O efficiency (t N2O / Mt DM).

    Combines direct (IPCC eq. 11.1) and indirect-leaching (eq. 11.10)
    pathways. Used for both the optional ``residue_incorporation`` link
    on the residue bus (LP-controlled fraction) and the mandatory
    (1 - FUE) * gross share that is baked into ``crop_production`` as a
    fixed N2O coefficient per Mha of cropland.
    """
    if not residue_feed_items:
        return {}

    rum_residue = ruminant_feed_mapping[
        ruminant_feed_mapping["source_type"] == "residue"
    ].merge(
        ruminant_feed_categories[["category", "N_g_per_kg_DM"]],
        on="category",
        how="left",
    )
    rum_valid = rum_residue.dropna(subset=["N_g_per_kg_DM"])
    n_content_lookup: dict[str, float] = dict(
        zip(rum_valid["feed_item"], rum_valid["N_g_per_kg_DM"].astype(float))
    )
    mono_residue = monogastric_feed_mapping[
        monogastric_feed_mapping["source_type"] == "residue"
    ].merge(
        monogastric_feed_categories[["category", "N_g_per_kg_DM"]],
        on="category",
        how="left",
    )
    mono_valid = mono_residue.dropna(subset=["N_g_per_kg_DM"])
    for item, n_val in zip(
        mono_valid["feed_item"], mono_valid["N_g_per_kg_DM"].astype(float)
    ):
        n_content_lookup.setdefault(item, float(n_val))

    missing = sorted(set(residue_feed_items) - set(n_content_lookup))
    if missing:
        raise ValueError(
            "Missing N content data for residue items "
            f"{missing}; add an entry to the ruminant or monogastric "
            "feed category tables (column N_g_per_kg_DM)."
        )

    # Total N2O-N per kg residue-N: direct decomposition + leaching/runoff.
    total_n2o_n = incorporation_n2o_factor + frac_leach * indirect_ef5
    # t N2O per Mt residue DM:
    # = (kg N / kg DM) * (kg N2O-N / kg N) * (44/28 N2O/N2O-N) * (1e6 t/Mt)
    coeff = total_n2o_n * (44.0 / 28.0) * constants.MEGATONNE_TO_TONNE
    return {
        item: float(n_content_lookup[item]) / 1000.0 * coeff
        for item in residue_feed_items
    }


def _redistribute_excess_baseline(df: pd.DataFrame) -> pd.Series:
    """Cap baseline_area_mha at p_nom_max, redistributing excess within each crop x country.

    When FAOSTAT harvested area disaggregated to a region exceeds the land
    available there (p_nom_max), the excess is proportionally redistributed
    to other links of the same crop x country that still have spare capacity.
    This preserves national totals as far as capacity allows.
    """
    baseline = df["baseline_area_mha"].copy()
    cap = df["p_nom_max"]

    excess_mask = baseline > cap
    if not excess_mask.any():
        return baseline

    # Cap over-allocated links
    excess = (baseline - cap).clip(lower=0)
    baseline = baseline.clip(upper=cap)

    # Redistribute per (crop, country, water_supply) group. Earlier
    # versions grouped only on (crop, country), which silently moved
    # baseline mass between irrigated and rainfed rows of the same crop
    # and broke (crop, water_supply) production-stability anchors.
    group_keys = (
        df["crop"].astype(str)
        + ":"
        + df["country"].astype(str)
        + ":"
        + df["water_supply"].astype(str)
    )
    total_excess_before = float(excess.sum())
    unplaced = 0.0

    for _key, idx in baseline.groupby(group_keys).groups.items():
        group_excess = float(excess.loc[idx].sum())
        if group_excess <= 0:
            continue

        spare = (cap.loc[idx] - baseline.loc[idx]).clip(lower=0)
        total_spare = float(spare.sum())
        if total_spare <= 0:
            unplaced += group_excess
            continue

        # Distribute proportionally to spare capacity
        allocated = min(group_excess, total_spare)
        baseline.loc[idx] += spare / total_spare * allocated
        if group_excess > total_spare:
            unplaced += group_excess - total_spare

    # Final safety clip (numerical precision)
    baseline = baseline.clip(upper=cap)

    logger.info(
        "Baseline redistribution: capped %.1f Mha excess, "
        "%.1f Mha unplaceable (no spare capacity in same crop x country)",
        total_excess_before,
        unplaced,
    )
    return baseline


def add_regional_crop_production_links(
    n: pypsa.Network,
    crop_list: list,
    yields_data: dict,
    region_to_country: pd.Series,
    allowed_countries: set,
    crop_costs: pd.Series,
    global_median_cost: pd.Series,
    fertilizer_n_rates: Mapping[str, float],
    rice_methane_factor: float,
    rainfed_wetland_rice_ch4_scaling_factor: float,
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
    residue_fue_lookup: Mapping[str, float] | None = None,
    residue_n2o_eff_lookup: Mapping[str, float] | None = None,
    use_actual_production: bool = False,
    *,
    cost_calibration: pd.Series | None = None,
    min_yield_t_per_ha: float,
    seed_kg_dm_per_ha: pd.Series,
    crop_loss_multiplier: pd.Series,
    crop_marketing_cost_usd_per_t: Mapping[str, float],
) -> None:
    """Add crop production links per region/resource class and water supply.

    Rainfed yields must be present for every crop; irrigated yields are used when
    provided by the preprocessing pipeline. Output links produce into the same
    crop bus per country; link names encode supply type (i/r) and resource class.

    Parameters
    ----------
    crop_costs : pd.Series
        MultiIndex (crop, country) → cost USD/ha in base year.
    global_median_cost : pd.Series
        Index crop → global median cost USD/ha (fallback).
    cost_calibration : pd.Series | None
        MultiIndex (crop, country) → correction in bnUSD/Mha (additive).
    seed_kg_dm_per_ha : pd.Series
        Index crop → annualized seed reservation in kg DM per hectare
        planted (already moisture-corrected upstream in build_model.py
        from the fresh-weight values in data/curated/seed_rates.csv).
        Used to deduct a per-link seed share from yield: post-seed yield =
        yield * (1 - seed_kg_dm_per_ha/1000 / yield_t_dm_per_ha). The seed
        share is clipped to [0, 0.5]. Coverage is enforced upstream by
        ``workflow.validation.seed_rates`` — every config crop must have a
        row, so a missing key raises ``KeyError`` here rather than silently
        defaulting to zero.
    crop_loss_multiplier : pd.Series
        MultiIndex (crop, country) → ``1 - loss_fraction`` for that crop's
        primary food group, in the producing country. Applied as an
        additional factor on the crop_production efficiency so the crop
        bus carries post-supply-chain-loss DM. This makes food_processing
        country-neutral (eliminating the cross-country loss-rate
        arbitrage that would otherwise route processing through low-loss
        countries). A missing key falls back to 1.0 (no loss).
    """
    residue_lookup = residue_lookup or {}
    residue_fue_lookup = residue_fue_lookup or {}
    residue_n2o_eff_lookup = residue_n2o_eff_lookup or {}

    # Add crop production carrier
    if "crop_production" not in n.carriers.static.index:
        n.carriers.add("crop_production", unit="Mt")

    all_rows: list[pd.DataFrame] = []
    bus_index = n.buses.static.index

    for crop in crop_list:
        if crop not in fertilizer_n_rates:
            raise KeyError(
                f"Missing fertilizer N rate for crop '{crop}'. Every model "
                "crop must be present in global_fertilizer_n_rates.csv; add a "
                "fertilizer.proxy_rates entry if upstream FUBC data is absent."
            )
        fert_n_rate_kg_per_ha = float(fertilizer_n_rates[crop])

        fert_efficiency = (
            -fert_n_rate_kg_per_ha * 1e6 * constants.KG_TO_MEGATONNE
        )  # kg N/ha -> Mt N/Mha

        available_supplies = [
            ws for ws in ("r", "i") if f"{crop}_yield_{ws}" in yields_data
        ]

        for ws in available_supplies:
            water_label = "irrigated" if ws == "i" else "rainfed"
            key = f"{crop}_yield_{ws}"
            crop_yields = yields_data[key].copy()

            # When every configured region has zero/missing yield for this
            # (crop, ws), the pivot in _load_crop_yield_table drops the
            # "yield" column. Skip; ``build_model`` already raised at the
            # per-crop level if every water supply was empty.
            if "yield" not in crop_yields.columns:
                continue

            df = crop_yields.reset_index()
            df["name"] = (
                "produce:"
                + crop
                + "_"
                + water_label
                + ":"
                + df["region"]
                + "_c"
                + df["resource_class"].astype(int).astype(str)
            )
            df.set_index("name", inplace=True)
            df.index.name = None

            df = df[(df["suitable_area"] > 0) & (df["yield"] > 0)]
            if min_yield_t_per_ha > 0:
                df = df[df["yield"] >= min_yield_t_per_ha]

            if use_actual_production:
                df["fixed_area_ha"] = pd.to_numeric(
                    df["harvested_area"], errors="coerce"
                )
                df = df[df["fixed_area_ha"] > 0]

            df["country"] = df["region"].map(region_to_country)
            df = df[df["country"].isin(allowed_countries)]
            if df.empty:
                continue

            bus0_series = (
                "land:cropland:"
                + df["region"]
                + "_c"
                + df["resource_class"].astype(int).astype(str)
                + "_"
                + ws
            )
            missing_bus_mask = ~bus0_series.isin(bus_index)
            if missing_bus_mask.any():
                missing_buses = bus0_series[missing_bus_mask].unique()
                preview = ", ".join(missing_buses[:5])
                logger.debug(
                    "Skipping %d %s links due to missing land buses (examples: %s)",
                    int(missing_bus_mask.sum()),
                    crop,
                    preview,
                )
                df = df.loc[~missing_bus_mask].copy()
                bus0_series = bus0_series.loc[df.index]
            if df.empty:
                continue

            if ws == "i":
                water_bus = ("water:" + df["region"].astype(str)).to_numpy(dtype=object)
                water_eff = -pd.to_numeric(
                    df["water_requirement_m3_per_ha"], errors="coerce"
                ).to_numpy(dtype=float)
            else:
                water_bus = np.full(len(df), "", dtype=object)
                water_eff = np.zeros(len(df), dtype=float)

            if crop == "wetland-rice" and rice_methane_factor > 0:
                scaling_factor = (
                    1.0 if ws == "i" else rainfed_wetland_rice_ch4_scaling_factor
                )
                ch4_bus = np.full(len(df), "emission:ch4", dtype=object)
                ch4_eff = np.full(
                    len(df),
                    rice_methane_factor
                    * scaling_factor
                    * 1e3,  # kg CH4/ha -> t CH4/Mha
                    dtype=float,
                )
            else:
                ch4_bus = np.full(len(df), "", dtype=object)
                ch4_eff = np.zeros(len(df), dtype=float)

            row_df = pd.DataFrame(index=df.index)
            row_df["crop"] = crop
            row_df["water_code"] = ws
            row_df["country"] = df["country"].astype(str).to_numpy()
            row_df["region"] = df["region"].astype(str).to_numpy()
            row_df["resource_class"] = df["resource_class"].astype(int).to_numpy()
            row_df["water_supply"] = water_label
            row_df["bus0"] = bus0_series.astype(str).to_numpy()
            row_df["bus1"] = (
                "crop:" + crop + ":" + df["country"].astype(str)
            ).to_numpy()
            yield_t_per_ha = pd.to_numeric(df["yield"], errors="coerce").to_numpy(
                dtype=float
            )
            seed_t_per_ha = float(seed_kg_dm_per_ha[crop]) / 1000.0
            with np.errstate(divide="ignore", invalid="ignore"):
                seed_share = np.where(
                    yield_t_per_ha > 0, seed_t_per_ha / yield_t_per_ha, 0.0
                )
            n_seed_clipped = int(np.sum(seed_share > 0.5))
            if n_seed_clipped > 0:
                logger.info(
                    "Clipped seed share to 0.5 for %d %s/%s cells where seed/yield > 0.5",
                    n_seed_clipped,
                    crop,
                    ws,
                )
            seed_share = np.clip(seed_share, 0.0, 0.5)
            # Per-country supply-chain loss multiplier (post-harvest +
            # storage + transport + processing losses); applied here so
            # the crop bus carries post-loss DM and food_processing
            # remains country-neutral.
            loss_keys = pd.MultiIndex.from_arrays(
                [
                    [crop] * len(row_df),
                    row_df["country"].astype(str).values,
                ]
            )
            loss_mults = crop_loss_multiplier.reindex(loss_keys).fillna(1.0).to_numpy()
            row_df["efficiency"] = yield_t_per_ha * (1.0 - seed_share) * loss_mults
            row_df["loss_multiplier"] = loss_mults
            row_df["seed_share"] = seed_share
            ha = pd.to_numeric(df["harvested_area"], errors="coerce").to_numpy(
                dtype=float
            )
            row_df["baseline_area_mha"] = ha / constants.HA_PER_MHA
            row_df["bus2"] = water_bus
            row_df["efficiency2"] = water_eff
            row_df["bus3"] = ("fertilizer:" + df["country"].astype(str)).to_numpy()
            row_df["efficiency3"] = fert_efficiency
            row_df["bus4"] = ch4_bus
            row_df["efficiency4"] = ch4_eff
            row_df["harvested_area_ha"] = ha
            row_df["p_nom_max"] = (
                pd.to_numeric(df["suitable_area"], errors="coerce").to_numpy(
                    dtype=float
                )
                / 1e6
            )

            if use_actual_production:
                fixed_area_mha = (
                    pd.to_numeric(df["fixed_area_ha"], errors="coerce").to_numpy(
                        dtype=float
                    )
                    / 1e6
                )
                row_df["p_nom"] = fixed_area_mha
                row_df["p_nom_max"] = fixed_area_mha
                row_df["p_nom_min"] = fixed_area_mha
                row_df["p_min_pu"] = 1.0

            all_rows.append(row_df)

    if not all_rows:
        return

    all_df = pd.concat(all_rows, axis=0)
    all_df.index = all_df.index.astype(str)

    # Cap baseline_area_mha at p_nom_max and redistribute excess to other
    # links of the same crop x country so that national totals are preserved
    # while respecting per-link land availability.
    all_df["baseline_area_mha"] = _redistribute_excess_baseline(all_df)

    # Look up per-(crop, country) cost, falling back to global median
    cost_keys = list(zip(all_df["crop"].astype(str), all_df["country"].astype(str)))
    per_link_cost = pd.Series(
        [crop_costs.get(k, global_median_cost.get(k[0], 0.0)) for k in cost_keys],
        index=all_df.index,
        dtype=float,
    )
    # Convert USD/ha to bnUSD/Mha
    all_df["marginal_cost"] = per_link_cost * 1e6 * constants.USD_TO_BNUSD

    # Add the farm-to-wholesale marketing markup, charged per tonne of crop
    # output (post-seed, post-loss). The link's marginal_cost is in
    # bnUSD/Mha; marketing_cost_per_t * efficiency (t/ha) -> USD/ha.
    marketing_per_t = all_df["crop"].astype(str).map(crop_marketing_cost_usd_per_t)
    if marketing_per_t.isna().any():
        missing = sorted(
            all_df.loc[marketing_per_t.isna(), "crop"].astype(str).unique()
        )
        raise KeyError(f"Missing crop marketing cost for: {missing}")
    all_df["marginal_cost"] = all_df["marginal_cost"] + (
        marketing_per_t.to_numpy(dtype=float)
        * all_df["efficiency"].to_numpy(dtype=float)
        * 1e6
        * constants.USD_TO_BNUSD
    )

    # Apply additive calibration correction if available.
    #
    # Both directions are bounded at baseline_area_mha so the corrections
    # retain their calibration-time interpretation as *local* marginal-cost
    # gradients at baseline rather than leaking into a flat additive cost
    # that pulls the LP arbitrarily far from baseline:
    #   - Negative corrections (subsidy bringing model cost down) are
    #     stored as ``bounded_subsidy_bnusd_per_mha`` and applied at
    #     solve time only on the first ``baseline_area_mha`` units of
    #     dispatch. Prevents the canonical olive-USA case
    #     (-0.40 bnUSD/Mha on 0.04 Mha) from leaking into runaway
    #     expansion of the crop.
    #   - Positive corrections (cost increase) are stored as
    #     ``bounded_penalty_bnusd_per_mha`` and applied at solve time
    #     only *above* ``baseline_area_mha``. Without this bound, a
    #     large positive correction (e.g. +346 bnUSD/Mha on tomato:BEL
    #     after winsorization made greenhouse tomato look cheap per
    #     tonne) becomes a flat penalty that pushes production to zero,
    #     forcing the L1 production-stability penalty to do the
    #     anchoring work.
    all_df["bounded_subsidy_bnusd_per_mha"] = 0.0
    all_df["bounded_penalty_bnusd_per_mha"] = 0.0
    if cost_calibration is not None:
        missing_cal_keys = [k for k in cost_keys if k not in cost_calibration.index]
        if missing_cal_keys:
            # cost_calibration is generated by a prior solve; mismatched
            # link sets surface as silent zero corrections, which weaken
            # the calibration anchor for whichever (crop, country) pairs
            # got added between calibration and the present solve.
            sample = ", ".join(f"{c}:{n}" for c, n in sorted(missing_cal_keys)[:5])
            logger.warning(
                "cost_calibration missing %d (crop, country) pair(s); "
                "applying zero correction. Examples: %s. Regenerate the "
                "cost calibration if the link set has changed.",
                len(missing_cal_keys),
                sample,
            )
        cal_values = pd.Series(
            [cost_calibration.get(k, 0.0) for k in cost_keys],
            index=all_df.index,
            dtype=float,
        )
        base_cost = all_df["marginal_cost"].copy()

        positive_mask = cal_values > 0
        negative_mask = cal_values < 0

        # Positive corrections: store as bounded penalty (applied above
        # baseline_area at solve time; no effect on marginal_cost).
        all_df.loc[positive_mask, "bounded_penalty_bnusd_per_mha"] = cal_values.loc[
            positive_mask
        ]

        # Negative corrections: bound the subsidy so the underlying base
        # cost cannot go below zero, then store on the link.
        all_df.loc[negative_mask, "bounded_subsidy_bnusd_per_mha"] = np.maximum(
            cal_values.loc[negative_mask], -base_cost.loc[negative_mask]
        )

        n_calibrated = int((cal_values != 0.0).sum())
        n_pos = int(positive_mask.sum())
        n_neg = int(negative_mask.sum())
        logger.info(
            "Applied crop cost calibration: %d/%d links (pos=%d bounded above "
            "baseline_area, neg=%d bounded at baseline_area)",
            n_calibrated,
            len(all_df),
            n_pos,
            n_neg,
        )

    keys = list(
        zip(
            all_df["crop"].astype(str),
            all_df["water_code"].astype(str),
            all_df["region"].astype(str),
            all_df["resource_class"].astype(int),
        )
    )
    countries = all_df["country"].astype(str).to_numpy()
    residue_bus5 = np.empty(len(keys), dtype=object)
    residue_eff5 = np.zeros(len(keys), dtype=float)
    residue_eff6 = np.zeros(len(keys), dtype=float)
    has_residue_n2o = False

    for i, (key, country) in enumerate(zip(keys, countries, strict=False)):
        feed_map = residue_lookup.get(key, {})
        if not feed_map:
            residue_bus5[i] = ""
            continue
        if len(feed_map) > 1:
            feed_items = ", ".join(sorted(feed_map))
            raise ValueError(
                "Expected at most one residue output per crop production link, "
                f"got {len(feed_map)} for key {key}: {feed_items}"
            )
        feed_item, gross_residue_yield = next(iter(feed_map.items()))
        fue = float(residue_fue_lookup.get(feed_item, 1.0))
        residue_bus5[i] = f"residue:{feed_item}:{country}"
        # Residue bus carries NET (feed-usable) DM: gross * FUE. The LP
        # routes this NET pool between the feed_conversion link and the
        # optional residue_incorporation link (the latter prices the
        # marginal N2O if the LP can't place the residue as feed).
        residue_eff5[i] = float(gross_residue_yield) * fue
        # Mandatory soil N2O from the (1 - FUE) gross share that
        # physically must be left on the field. Wired straight onto the
        # crop_production link (bus6 = emission:n2o) so the LP cannot
        # dodge it by re-routing through the feed link. Scales rigidly
        # with Mha of cropland.
        n2o_eff = float(residue_n2o_eff_lookup.get(feed_item, 0.0))
        residue_eff6[i] = float(gross_residue_yield) * (1.0 - fue) * n2o_eff
        if residue_eff6[i] > 0.0:
            has_residue_n2o = True

    all_df["bus5"] = residue_bus5
    # efficiency5 is the NET residue yield (gross * FUE) on the feed-usable
    # residue bus. Don't scale by loss_mults: the crop bus carries post-
    # loss product, but residues stay in the field and don't share the
    # storage / transport / processing loss path of the grain.
    all_df["efficiency5"] = residue_eff5
    if has_residue_n2o:
        all_df["bus6"] = np.where(residue_eff6 > 0.0, "emission:n2o", "")
        all_df["efficiency6"] = residue_eff6

    add_kwargs: dict[str, object] = {
        "carrier": "crop_production",
        "bus0": all_df["bus0"],
        "bus1": all_df["bus1"],
        "efficiency": all_df["efficiency"],
        "bus2": all_df["bus2"],
        "efficiency2": all_df["efficiency2"],
        "bus3": all_df["bus3"],
        "efficiency3": all_df["efficiency3"],
        "bus4": all_df["bus4"],
        "efficiency4": all_df["efficiency4"],
        "bus5": all_df["bus5"],
        "efficiency5": all_df["efficiency5"],
        "marginal_cost": all_df["marginal_cost"],
        "p_nom_max": all_df["p_nom_max"],
        "p_nom_extendable": not use_actual_production,
        "crop": all_df["crop"],
        "country": all_df["country"],
        "region": all_df["region"],
        "resource_class": all_df["resource_class"],
        "water_supply": all_df["water_supply"],
        "baseline_area_mha": all_df["baseline_area_mha"],
        "seed_share": all_df["seed_share"],
        "loss_multiplier": all_df["loss_multiplier"],
        "bounded_subsidy_bnusd_per_mha": all_df["bounded_subsidy_bnusd_per_mha"],
        "bounded_penalty_bnusd_per_mha": all_df["bounded_penalty_bnusd_per_mha"],
    }
    if "bus6" in all_df.columns:
        add_kwargs["bus6"] = all_df["bus6"]
        add_kwargs["efficiency6"] = all_df["efficiency6"]

    if use_actual_production:
        add_kwargs["p_nom"] = all_df["p_nom"]
        add_kwargs["p_nom_min"] = all_df["p_nom_min"]
        add_kwargs["p_min_pu"] = all_df["p_min_pu"]

    n.links.add(all_df.index, **add_kwargs)


def add_multi_cropping_links(
    n: pypsa.Network,
    eligible_area: pd.DataFrame,
    cycle_yields: pd.DataFrame,
    region_to_country: pd.Series,
    allowed_countries: set[str],
    crop_costs: pd.Series,
    global_median_cost: pd.Series,
    fertilizer_n_rates: Mapping[str, float],
    residue_lookup: Mapping[tuple[str, str, str, int], dict[str, float]] | None = None,
    residue_fue_lookup: Mapping[str, float] | None = None,
    residue_n2o_eff_lookup: Mapping[str, float] | None = None,
    *,
    min_yield_t_per_ha: float,
    seed_kg_dm_per_ha: pd.Series,
    crop_loss_multiplier: pd.Series,
    crop_marketing_cost_usd_per_t: Mapping[str, float],
) -> None:
    """Add multi-cropping production links with a vectorised workflow.

    The seed-share deduction (see ``add_regional_crop_production_links``) is
    applied per cycle: each cycle's per-ha yield is reduced by
    ``seed_kg_dm_per_ha[crop] / 1000 / yield_t_per_ha``. The per-country
    supply-chain loss multiplier is applied identically so the multi-crop
    crop bus carries post-loss DM, matching the regular production path.
    """

    if eligible_area.empty or cycle_yields.empty:
        logger.info("No multi-cropping combinations with positive area; skipping")
        return

    residue_lookup = residue_lookup or {}
    residue_fue_lookup = residue_fue_lookup or {}
    residue_n2o_eff_lookup = residue_n2o_eff_lookup or {}

    key_cols = ["combination", "region", "resource_class", "water_supply"]

    area_df = eligible_area.copy()
    area_df["resource_class"] = area_df["resource_class"].astype(int)
    area_df["water_supply"] = area_df["water_supply"].astype(str)
    area_df["eligible_area_ha"] = pd.to_numeric(
        area_df["eligible_area_ha"], errors="coerce"
    )
    area_df["water_requirement_m3_per_ha"] = pd.to_numeric(
        area_df.get("water_requirement_m3_per_ha", 0.0), errors="coerce"
    )

    region_to_country = region_to_country.astype(str)
    area_df["country"] = area_df["region"].map(region_to_country)
    area_df = area_df.dropna(subset=["eligible_area_ha", "country"])
    area_df = area_df[area_df["eligible_area_ha"] > 0]
    if allowed_countries:
        area_df = area_df[area_df["country"].isin(allowed_countries)]

    if area_df.empty:
        logger.info("No eligible multi-cropping areas after filtering; skipping")
        return

    cycle_df = cycle_yields.copy()
    cycle_df["resource_class"] = cycle_df["resource_class"].astype(int)
    cycle_df["water_supply"] = cycle_df["water_supply"].astype(str)
    cycle_df["yield_t_per_ha"] = pd.to_numeric(
        cycle_df["yield_t_per_ha"], errors="coerce"
    )
    cycle_df = cycle_df.dropna(subset=["yield_t_per_ha", "crop"])
    cycle_df = cycle_df[cycle_df["yield_t_per_ha"] > 0]

    # Filter low yields for numerical stability
    if min_yield_t_per_ha > 0:
        low_yield_mask = cycle_df["yield_t_per_ha"] < min_yield_t_per_ha
        cycle_df = cycle_df[~low_yield_mask]

    if cycle_df.empty:
        logger.info("No positive multi-cropping yields; skipping")
        return

    merged = cycle_df.merge(area_df, on=key_cols, how="inner")
    if merged.empty:
        logger.info(
            "No overlapping multi-cropping combinations between area and yield tables"
        )
        return

    merged = merged.sort_values([*key_cols, "cycle_index", "crop"])
    merged["crop"] = merged["crop"].astype(str).str.strip()
    merged["country"] = merged["country"].astype(str).str.strip()
    merged["crop_bus"] = "crop:" + merged["crop"] + ":" + merged["country"]
    seed_t_per_ha = merged["crop"].map(seed_kg_dm_per_ha).astype(float) / 1000.0
    seed_share = (seed_t_per_ha / merged["yield_t_per_ha"]).clip(lower=0.0, upper=0.5)
    merged["seed_share"] = seed_share.to_numpy(dtype=float)
    loss_keys = pd.MultiIndex.from_arrays(
        [merged["crop"].astype(str).values, merged["country"].astype(str).values]
    )
    loss_mults = crop_loss_multiplier.reindex(loss_keys).fillna(1.0).to_numpy()
    merged["loss_mult"] = loss_mults
    merged["yield_efficiency"] = (
        merged["yield_t_per_ha"] * (1.0 - seed_share) * loss_mults
    )
    merged["output_idx"] = merged.groupby(key_cols).cumcount()

    base = (
        merged.loc[
            :,
            [
                *key_cols,
                "eligible_area_ha",
                "water_requirement_m3_per_ha",
                "country",
            ],
        ]
        .drop_duplicates()
        .set_index(key_cols)
    )

    crop_counts = merged.groupby(key_cols)["crop"].size().rename("crop_count")
    base = base.join(crop_counts)
    base = base[base["crop_count"] > 0]
    if base.empty:
        logger.info(
            "Multi-cropping combinations have no positive-yield crops; skipping"
        )
        return

    # Look up per-(crop, country) cost and sum across crops in combination
    merged["cost_usd_per_ha"] = [
        crop_costs.get((c, cc), global_median_cost.get(c, 0.0))
        for c, cc in zip(merged["crop"], merged["country"])
    ]
    # Marketing markup per cycle: marketing_cost_per_t * yield (post seed/loss)
    marketing_per_t = merged["crop"].astype(str).map(crop_marketing_cost_usd_per_t)
    if marketing_per_t.isna().any():
        missing = sorted(merged.loc[marketing_per_t.isna(), "crop"].unique())
        raise KeyError(f"Missing crop marketing cost for: {missing}")
    merged["cost_usd_per_ha"] = merged["cost_usd_per_ha"] + (
        marketing_per_t.to_numpy(dtype=float)
        * merged["yield_efficiency"].to_numpy(dtype=float)
    )
    cost_totals = merged.groupby(key_cols)["cost_usd_per_ha"].sum().rename("total_cost")
    base = base.join(cost_totals)

    fert_series = pd.Series({str(k): float(v) for k, v in fertilizer_n_rates.items()})
    fert_rates = merged["crop"].map(fert_series)
    if fert_rates.isna().any():
        missing = sorted(merged.loc[fert_rates.isna(), "crop"].unique())
        raise KeyError(
            f"Missing fertilizer N rate for multi-cropping crops: {missing}. "
            "Every model crop must be present in global_fertilizer_n_rates.csv; "
            "add a fertilizer.proxy_rates entry if upstream FUBC data is absent."
        )
    merged["fertilizer_rate"] = fert_rates
    fertilizer_totals = (
        merged.groupby(key_cols)["fertilizer_rate"].sum().rename("fertilizer_total")
    )
    base = base.join(fertilizer_totals)

    base[["total_cost", "fertilizer_total"]] = base[
        ["total_cost", "fertilizer_total"]
    ].fillna(0.0)

    # Multiple-cropping marginal costs: sum of per-country crop costs in bnUSD/Mha
    base["marginal_cost"] = base["total_cost"] * 1e6 * constants.USD_TO_BNUSD
    base["p_nom_extendable"] = True
    base["p_nom_max"] = base["eligible_area_ha"] / 1e6

    residue_records: list[dict[str, object]] = []
    for (crop, water, region, res_class), feed_dict in residue_lookup.items():
        if not isinstance(feed_dict, Mapping):
            continue
        for feed_item, value in feed_dict.items():
            gross = float(value)
            fue = float(residue_fue_lookup.get(str(feed_item), 1.0))
            n2o_eff = float(residue_n2o_eff_lookup.get(str(feed_item), 0.0))
            residue_records.append(
                {
                    "crop": str(crop),
                    "water_supply": str(water),
                    "region": str(region),
                    "resource_class": int(res_class),
                    "feed_item": str(feed_item),
                    # NET feed-usable residue (gross * FUE); mandatory N2O
                    # from the (1 - FUE) gross share is summed onto an
                    # emission:n2o bus offset below.
                    "residue_yield": gross * fue,
                    "mandatory_n2o": gross * (1.0 - fue) * n2o_eff,
                }
            )

    if residue_records:
        residue_df = pd.DataFrame(residue_records)
        residue_join = merged.merge(
            residue_df,
            on=["crop", "region", "resource_class", "water_supply"],
            how="left",
        )
        residue_join = residue_join.dropna(subset=["feed_item", "residue_yield"])
        residue_join = residue_join[residue_join["residue_yield"] > 0]
        if residue_join.empty:
            residue_agg = pd.DataFrame(
                columns=[*key_cols, "feed_item", "country", "residue_total"],
            )
            mandatory_n2o_agg = pd.DataFrame(
                columns=[*key_cols, "country", "mandatory_n2o_total"]
            )
        else:
            residue_agg = (
                residue_join.groupby([*key_cols, "feed_item", "country"])[
                    "residue_yield"
                ]
                .sum()
                .rename("residue_total")
                .reset_index()
            )
            mandatory_n2o_agg = (
                residue_join.groupby([*key_cols, "country"])["mandatory_n2o"]
                .sum()
                .rename("mandatory_n2o_total")
                .reset_index()
            )
            mandatory_n2o_agg = mandatory_n2o_agg[
                mandatory_n2o_agg["mandatory_n2o_total"] > 0
            ]
    else:
        residue_agg = pd.DataFrame(
            columns=[*key_cols, "feed_item", "country", "residue_total"],
        )
        mandatory_n2o_agg = pd.DataFrame(
            columns=[*key_cols, "country", "mandatory_n2o_total"]
        )

    residue_counts = (
        residue_agg.groupby(key_cols).size().rename("residue_count")
        if not residue_agg.empty
        else pd.Series(dtype=int)
    )
    base["residue_count"] = 0
    if not residue_counts.empty:
        base.loc[residue_counts.index, "residue_count"] = residue_counts

    base["has_n2o"] = 0
    if not mandatory_n2o_agg.empty:
        n2o_index = pd.MultiIndex.from_frame(mandatory_n2o_agg[key_cols])
        base.loc[base.index.intersection(n2o_index), "has_n2o"] = 1

    index_df = base.reset_index()
    index_df["resource_class"] = index_df["resource_class"].astype(int)
    index_df["carrier"] = "crop_production_multi"
    index_df["bus0"] = (
        "land:cropland:"
        + index_df["region"].astype(str)
        + "_c"
        + index_df["resource_class"].astype(str)
        + "_"
        + index_df["water_supply"].astype(str)
    )
    index_df["link_name"] = (
        "produce:multi_"
        + index_df["combination"].astype(str)
        + "_"
        + index_df["water_supply"].astype(str)
        + ":"
        + index_df["region"].astype(str)
        + "_c"
        + index_df["resource_class"].astype(str)
    )

    missing_land = index_df[~index_df["bus0"].isin(n.buses.static.index)]
    if not missing_land.empty:
        missing_count = missing_land.shape[0]
        missing_preview = ", ".join(missing_land["bus0"].unique()[:5])
        logger.debug(
            "Skipping %d multi-cropping links due to missing land buses (examples: %s)",
            missing_count,
            missing_preview,
        )
        index_df = index_df[index_df["bus0"].isin(n.buses.static.index)]

    if index_df.empty:
        return

    if "crop_production_multi" not in n.carriers.static.index:
        n.carriers.add("crop_production_multi", unit="Mha")

    water_req = index_df["water_requirement_m3_per_ha"].astype(float)
    water_valid = (
        index_df["water_supply"].eq("i") & np.isfinite(water_req) & (water_req > 0)
    )
    # Irrigated rows missing a water requirement would silently get
    # water_efficiency=0 (free irrigation on data-quality holes), letting
    # the LP claim the higher irrigated yield without paying water. Drop
    # those rows so they cannot be built into the network at all.
    water_invalid = index_df["water_supply"].eq("i") & ~np.isfinite(water_req)
    if water_invalid.any():
        logger.warning(
            "Dropping %d irrigated multi-cropping links with missing water requirement",
            int(water_invalid.sum()),
        )
        keep = ~water_invalid
        index_df = index_df[keep].copy()
        merged = merged[merged["link_name"].isin(index_df["link_name"])].copy()
        water_req = index_df["water_requirement_m3_per_ha"].astype(float)
        water_valid = (
            index_df["water_supply"].eq("i") & np.isfinite(water_req) & (water_req > 0)
        )
        if index_df.empty:
            return

    # bus0 is land in Mha, bus2 is water in Mm3, so the coefficient is
    # m3/ha (numerically equal to Mm3/Mha). water_requirement_m3_per_ha is
    # already in m3/ha after build_multi_cropping converts the GAEZ mm raster.
    index_df["water_efficiency"] = np.where(water_valid, -water_req, 0.0)
    index_df["has_water"] = water_valid.astype(int)

    fert_total = index_df["fertilizer_total"].astype(float)
    fert_valid = fert_total > 0
    index_df["fert_efficiency"] = np.where(
        fert_valid, -fert_total * 1e6 * constants.KG_TO_MEGATONNE, 0.0
    )
    index_df["has_fertilizer"] = fert_valid.astype(int)

    outputs = merged.merge(index_df[[*key_cols, "link_name"]], on=key_cols, how="left")
    outputs["offset"] = outputs["output_idx"] + 1
    offset_str = outputs["offset"].astype(int).astype(str)
    outputs["bus_col"] = "bus" + offset_str
    outputs["eff_col"] = np.where(
        outputs["offset"].eq(1),
        "efficiency",
        "efficiency" + offset_str,
    )
    outputs["lm_col"] = np.where(
        outputs["offset"].eq(1),
        "loss_multiplier",
        "loss_multiplier" + offset_str,
    )
    outputs_entries = outputs[
        [
            "link_name",
            "bus_col",
            "crop_bus",
            "eff_col",
            "yield_efficiency",
            "lm_col",
            "loss_mult",
        ]
    ].rename(
        columns={
            "crop_bus": "bus_value",
            "yield_efficiency": "eff_value",
            "loss_mult": "lm_value",
        }
    )

    entry_frames = [outputs_entries]

    water_columns = [*key_cols, "link_name", "water_efficiency", "crop_count"]
    water_entries = index_df.loc[index_df["has_water"] == 1, water_columns].copy()
    if not water_entries.empty:
        water_entries["offset"] = water_entries["crop_count"] + 1
        offset_str = water_entries["offset"].astype(int).astype(str)
        water_entries["bus_col"] = "bus" + offset_str
        water_entries["eff_col"] = "efficiency" + offset_str
        water_entries.loc[water_entries["offset"].eq(1), "eff_col"] = "efficiency"
        water_entries["bus_value"] = "water:" + water_entries["region"].astype(str)
        water_entries = water_entries[
            [
                "link_name",
                "bus_col",
                "bus_value",
                "eff_col",
                "water_efficiency",
            ]
        ].rename(columns={"water_efficiency": "eff_value"})
        entry_frames.append(water_entries)

    fert_entries = index_df[index_df["has_fertilizer"] == 1][
        [
            *key_cols,
            "link_name",
            "country",
            "fert_efficiency",
            "crop_count",
            "has_water",
        ]
    ].copy()
    if not fert_entries.empty:
        fert_entries["offset"] = (
            fert_entries["crop_count"] + fert_entries["has_water"] + 1
        )
        offset_str = fert_entries["offset"].astype(int).astype(str)
        fert_entries["bus_col"] = "bus" + offset_str
        fert_entries["eff_col"] = "efficiency" + offset_str
        fert_entries.loc[fert_entries["offset"].eq(1), "eff_col"] = "efficiency"
        fert_entries["bus_value"] = "fertilizer:" + fert_entries["country"].astype(str)
        fert_entries = fert_entries[
            [
                "link_name",
                "bus_col",
                "bus_value",
                "eff_col",
                "fert_efficiency",
            ]
        ].rename(columns={"fert_efficiency": "eff_value"})
        entry_frames.append(fert_entries)

    if not residue_agg.empty:
        residue_entries = residue_agg.merge(
            index_df[
                [
                    *key_cols,
                    "link_name",
                    "crop_count",
                    "has_water",
                    "has_fertilizer",
                ]
            ],
            on=key_cols,
            how="left",
        )
        residue_entries = residue_entries.dropna(subset=["link_name"])
        if residue_entries.empty:
            residue_entries = pd.DataFrame(columns=residue_entries.columns)
        residue_entries[["crop_count", "has_water", "has_fertilizer"]] = (
            residue_entries[["crop_count", "has_water", "has_fertilizer"]].fillna(0)
        )
        residue_entries = residue_entries.sort_values([*key_cols, "feed_item"])
        residue_entries["entry_order"] = residue_entries.groupby(key_cols).cumcount()
        residue_entries["offset"] = (
            residue_entries["crop_count"]
            + residue_entries["has_water"]
            + residue_entries["has_fertilizer"]
            + residue_entries["entry_order"]
            + 1
        )
        offset_str = residue_entries["offset"].astype(int).astype(str)
        residue_entries["bus_col"] = "bus" + offset_str
        residue_entries["eff_col"] = "efficiency" + offset_str
        residue_entries.loc[residue_entries["offset"].eq(1), "eff_col"] = "efficiency"
        residue_entries["bus_value"] = (
            "residue:"
            + residue_entries["feed_item"].astype(str)
            + ":"
            + residue_entries["country"].astype(str)
        )
        residue_entries["eff_value"] = residue_entries["residue_total"]
        entry_frames.append(
            residue_entries[
                [
                    "link_name",
                    "bus_col",
                    "bus_value",
                    "eff_col",
                    "eff_value",
                ]
            ]
        )

    if not mandatory_n2o_agg.empty:
        n2o_entries = mandatory_n2o_agg.merge(
            index_df[
                [
                    *key_cols,
                    "link_name",
                    "crop_count",
                    "has_water",
                    "has_fertilizer",
                    "residue_count",
                ]
            ],
            on=key_cols,
            how="left",
        )
        n2o_entries = n2o_entries.dropna(subset=["link_name"])
        if not n2o_entries.empty:
            n2o_entries[
                ["crop_count", "has_water", "has_fertilizer", "residue_count"]
            ] = n2o_entries[
                ["crop_count", "has_water", "has_fertilizer", "residue_count"]
            ].fillna(0)
            # Mandatory soil-N2O bus offset: sits after crops, water,
            # fertilizer, and residue feed buses. One entry per link.
            n2o_entries["offset"] = (
                n2o_entries["crop_count"]
                + n2o_entries["has_water"]
                + n2o_entries["has_fertilizer"]
                + n2o_entries["residue_count"]
                + 1
            )
            offset_str = n2o_entries["offset"].astype(int).astype(str)
            n2o_entries["bus_col"] = "bus" + offset_str
            n2o_entries["eff_col"] = "efficiency" + offset_str
            n2o_entries.loc[n2o_entries["offset"].eq(1), "eff_col"] = "efficiency"
            n2o_entries["bus_value"] = "emission:n2o"
            n2o_entries["eff_value"] = n2o_entries["mandatory_n2o_total"]
            entry_frames.append(
                n2o_entries[
                    [
                        "link_name",
                        "bus_col",
                        "bus_value",
                        "eff_col",
                        "eff_value",
                    ]
                ]
            )

    # Strip per-output loss_multiplier info from outputs_entries before
    # concatenating; it only applies to crop output buses and is pivoted
    # separately so the lm columns line up with the eff columns.
    lm_entries = outputs_entries[["link_name", "lm_col", "lm_value"]].copy()
    entry_frames = [
        df.drop(columns=["lm_col", "lm_value"], errors="ignore") for df in entry_frames
    ]

    entries = pd.concat(entry_frames, ignore_index=True)
    bus_wide = entries.pivot_table(
        index="link_name", columns="bus_col", values="bus_value", aggfunc="first"
    )
    eff_wide = entries.pivot_table(
        index="link_name", columns="eff_col", values="eff_value", aggfunc="first"
    )
    lm_wide = lm_entries.pivot_table(
        index="link_name", columns="lm_col", values="lm_value", aggfunc="first"
    )

    link_df = index_df.set_index("link_name")
    component_cols = [
        "carrier",
        "bus0",
        "p_nom_extendable",
        "p_nom_max",
        "marginal_cost",
    ]
    # Metadata columns for filtering
    metadata_cols = [
        "country",
        "region",
        "resource_class",
        "water_supply",
        "combination",
    ]
    # Prepare metadata values
    link_df["water_supply"] = link_df["water_supply"].map(
        {"r": "rainfed", "i": "irrigated"}
    )
    link_df["crop"] = link_df["combination"]  # combination = "maize+soybean" etc.
    link_df = link_df[component_cols + metadata_cols + ["crop"]]
    link_df = (
        link_df.join(bus_wide, how="left")
        .join(eff_wide, how="left")
        .join(lm_wide, how="left")
    )

    bus_cols = sorted(
        [c for c in link_df.columns if c.startswith("bus") and c != "bus0"],
        key=lambda name: int(name[3:]),
    )
    eff_cols = [
        "efficiency",
        *sorted(
            [
                c
                for c in link_df.columns
                if c.startswith("efficiency") and c != "efficiency"
            ],
            key=lambda name: int(name[len("efficiency") :]),
        ),
    ]
    lm_cols = [
        "loss_multiplier",
        *sorted(
            [
                c
                for c in link_df.columns
                if c.startswith("loss_multiplier") and c != "loss_multiplier"
            ],
            key=lambda name: int(name[len("loss_multiplier") :]),
        ),
    ]
    lm_cols = [c for c in lm_cols if c in link_df.columns]

    missing_outputs = link_df["bus1"].isna() | link_df["efficiency"].isna()
    if missing_outputs.any():
        logger.warning(
            "Dropping %d multi-cropping links without valid crop outputs",
            int(missing_outputs.sum()),
        )
        link_df = link_df[~missing_outputs]

    if link_df.empty:
        return

    for col in bus_cols:
        link_df[col] = link_df[col].where(link_df[col].notna(), None)
    for col in eff_cols:
        link_df[col] = link_df[col].fillna(0.0)
    # loss_multiplier columns are NaN for non-crop output positions; leave
    # them NaN so the sensitivity step can distinguish "no loss data" from
    # "loss multiplier 1.0".

    all_cols = component_cols + metadata_cols + ["crop"] + bus_cols + eff_cols + lm_cols
    kwargs = {col: link_df[col] for col in all_cols}
    n.links.add(link_df.index, **kwargs)


def add_spared_land_links(
    n: pypsa.Network,
    baseline_land_df: pd.DataFrame,
    lef_df: pd.DataFrame,
    *,
    disable_spared_cropland: bool = False,
) -> None:
    """Add optional links to allocate spared land and credit CO2 sinks.

    Only baseline cropland (i.e., currently managed area) can be spared. Newly
    converted land must first revert to baseline before becoming eligible.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    baseline_land_df : pd.DataFrame
        Current cropland area by region/water_supply/resource_class.
    lef_df : pd.DataFrame
        LEF lookup from ``_build_luc_lef_lookup`` (columns: region,
        resource_class, water_supply, use, lef).
    disable_spared_cropland : bool, optional
        If True, skip creation of spared-cropland links.
    """

    if disable_spared_cropland:
        logger.info("Spared cropland disabled; skipping spared land links")
        return

    if lef_df.empty:
        logger.info("No LUC LEF entries available for spared land; skipping")
        return

    base_df = baseline_land_df.reset_index()
    base_df["resource_class"] = base_df["resource_class"].astype(int)
    base_df["water_supply"] = base_df["water_supply"].astype(str)
    df = base_df[base_df["area_ha"] > 0].copy()
    if df.empty:
        logger.info("No baseline cropland available for sparing; skipping spared links")
        return

    df["lef"] = merge_lef(df, lef_df, "spared_cropland", allow_missing=True)
    # Sparing must yield a sequestration credit (non-positive emission to
    # emission:co2). build_luc_carbon_coefficients computes lef_spared as
    # -regrowth * CO2_PER_C, but a future regression in that pipeline
    # could flip the sign and quietly make sparing emit at positive GHG
    # prices. Surface that immediately rather than at solve time.
    assert (df["lef"] <= 1e-9).all()

    # Add spared-land routes for all existing cropland buses, even where the
    # spared-land LEF is zero. This keeps land accounting explicit: baseline
    # land must flow either to production or to an explicit spared-land sink,
    # rather than disappearing as unused generator capacity upstream.

    suffix = (
        df["region"]
        + "_c"
        + df["resource_class"].astype(str)
        + "_"
        + df["water_supply"]
    )
    df["bus0"] = "land:existing_cropland:" + suffix
    df["sink_bus"] = "land:spared:" + suffix
    df["link_name"] = "spare:land:" + suffix
    df["area_mha"] = df["area_ha"] / 1e6

    # Filter out links where bus0 doesn't exist (due to area filtering)
    missing_bus_mask = ~df["bus0"].isin(n.buses.static.index)
    if missing_bus_mask.any():
        logger.debug(
            "Skipping %d spared land links due to missing land_existing_cropland buses",
            int(missing_bus_mask.sum()),
        )
        df = df[~missing_bus_mask]

    if df.empty:
        logger.info("No spared land links after filtering for existing buses")
        return

    # Add carriers and sink buses
    n.carriers.add("spared_land", unit="Mha")
    n.carriers.add("spare_land", unit="Mha")  # Link carrier

    # Index by sink_bus for proper alignment with PyPSA component names
    sink_df = df.set_index("sink_bus")
    n.buses.add(sink_df.index, carrier="spared_land", region=sink_df["region"])

    # Add stores for sink buses - index by store name for alignment
    df["store_name"] = (
        "store:spared:"
        + df["region"]
        + "_c"
        + df["resource_class"].astype(str)
        + "_"
        + df["water_supply"]
    )
    store_df = df.set_index("store_name")
    n.stores.add(
        store_df.index,
        bus=store_df["sink_bus"],
        carrier="spared_land",
        e_nom_extendable=True,
        region=store_df["region"],
        resource_class=store_df["resource_class"],
        water_supply=store_df["water_supply"],
    )

    # Add spared land links - index by link_name for alignment
    link_df = df.set_index("link_name")
    n.links.add(
        link_df.index,
        carrier="spare_land",
        bus0=link_df["bus0"],
        bus1=link_df["sink_bus"],
        efficiency=1.0,
        bus2="emission:co2",
        # tCO2/ha = MtCO2/Mha numerically, no conversion needed
        efficiency2=link_df["lef"],
        p_nom_extendable=True,
        p_nom_max=link_df["area_mha"],
        region=link_df["region"],
        resource_class=link_df["resource_class"],
        water_supply=link_df["water_supply"],
    )


def add_residue_soil_incorporation_links(
    n: pypsa.Network,
    residue_feed_items: list[str],
    ruminant_feed_mapping: pd.DataFrame,
    ruminant_feed_categories: pd.DataFrame,
    monogastric_feed_mapping: pd.DataFrame,
    monogastric_feed_categories: pd.DataFrame,
    countries: list[str],
    incorporation_n2o_factor: float,
    indirect_ef5: float,
    frac_leach: float,
) -> None:
    """Add links for crop residue incorporation into soil with N₂O emissions.

    Includes direct and indirect (leaching) N₂O emissions from crop residues
    following IPCC 2019 Refinement methodology (Chapter 11, Equations 11.1, 11.10).
    Note: Volatilization pathway (EF4) is not applicable for incorporated residues.

    Residues left on the field decompose and release N₂O. This function creates
    links that consume residues and produce N₂O emissions based on their N content
    and the IPCC emission factors.

    This processes ALL residues in the model, regardless of whether they're used
    for ruminant or monogastric feed. N content is looked up from whichever feed
    category dataset contains the residue.

    Parameters
    ----------
    n : pypsa.Network
        The network to add links to.
    residue_feed_items : list[str]
        Complete list of all residue items in the model.
    ruminant_feed_mapping : pd.DataFrame
        Ruminant feed mapping (columns: feed_item, category).
    ruminant_feed_categories : pd.DataFrame
        Ruminant feed category properties (column: N_g_per_kg_DM).
    monogastric_feed_mapping : pd.DataFrame
        Monogastric feed mapping (columns: feed_item, category).
    monogastric_feed_categories : pd.DataFrame
        Monogastric feed category properties (column: N_g_per_kg_DM).
    countries : list[str]
        List of country ISO codes.
    incorporation_n2o_factor : float
        IPCC EF1 emission factor for direct emissions (kg N₂O-N per kg N input).
    indirect_ef5 : float
        IPCC EF5 emission factor for leaching/runoff (kg N₂O-N per kg N leached).
    frac_leach : float
        Fraction of applied N lost through leaching/runoff (FracLEACH-(H)).
    """

    if not residue_feed_items:
        logger.info("No residue items found; skipping soil incorporation links")
        return

    n2o_eff_lookup = compute_residue_n2o_efficiency_per_dm(
        residue_feed_items,
        ruminant_feed_mapping,
        ruminant_feed_categories,
        monogastric_feed_mapping,
        monogastric_feed_categories,
        incorporation_n2o_factor,
        indirect_ef5,
        frac_leach,
    )
    if not n2o_eff_lookup:
        logger.info(
            "No residue items with N content data; skipping soil incorporation links"
        )
        return

    items_df = pd.DataFrame(
        {
            "item": list(n2o_eff_lookup),
            "n2o_efficiency": list(n2o_eff_lookup.values()),
        }
    )

    # Build links for all residue x country combinations via cross product
    countries_df = pd.DataFrame({"country": countries})
    cross = items_df.merge(countries_df, how="cross")
    cross["bus_name"] = "residue:" + cross["item"] + ":" + cross["country"]

    # Only add link if the residue bus exists in the network
    cross = cross[cross["bus_name"].isin(n.buses.static.index)]

    if cross.empty:
        logger.info("No valid residue buses found; skipping soil incorporation links")
        return

    cross["link_name"] = "incorporate:residue_" + cross["item"] + ":" + cross["country"]
    cross = cross.set_index("link_name", drop=False)

    # Add the carrier
    carrier = "residue_incorporation"
    if carrier not in n.carriers.static.index:
        n.carriers.add(carrier, unit="MtDM")

    # Add the links
    n.links.add(
        cross.index,
        bus0=cross["bus_name"],
        bus1="emission:n2o",
        carrier=carrier,
        efficiency=cross["n2o_efficiency"],
        marginal_cost=0.0,  # No cost to incorporate residues
        p_nom_extendable=True,
        country=cross["country"],
    )

    logger.info(
        "Created %d residue soil incorporation links for %d residue types",
        len(cross),
        len(n2o_eff_lookup),
    )
