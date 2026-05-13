# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute per-food-group waste-retention multipliers from food-bus slack.

This generates ``data/curated/calibration/food_waste.yaml``, holding a
single multiplier per calibrated food group that ``prepare_food_loss_waste``
applies uniformly across countries:

    waste_retention_new = (1 - waste_fraction_sdg) * multiplier
    waste_fraction_new  = 1 - waste_retention_new

The multiplier is derived from the global food-bus balance reported by an
uncalibrated validation solve:

    multiplier = consumption_mt / (consumption_mt + slack_net_mt)

where ``slack_net = positive_slack - negative_slack`` is the net excess on
the food bus across all countries in the group. Positive (excess) slack
shrinks the multiplier (more waste at consumption -> more demand);
negative slack would inflate it.

Only food groups listed in ``params.food_groups`` are calibrated; the
output YAML keeps unaffected groups absent so the prepare step leaves
their SDG/FBS defaults untouched.
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa
import yaml

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _aggregate_per_group(network: pypsa.Network) -> pd.DataFrame:
    """Return per-food-group consumption (Mt) and net slack (Mt)."""
    snap = network.snapshots[-1]
    links = network.links.static
    consume_mask = links["carrier"] == "food_consumption"
    consume = links[consume_mask]
    if consume.empty:
        return pd.DataFrame(columns=["consumption_mt", "slack_net_mt"])

    p0 = network.links.dynamic["p0"].loc[snap]
    consume_p0 = p0.reindex(consume.index).fillna(0.0).clip(lower=0.0)
    consumption_per_group = (
        pd.Series(consume_p0.values, index=consume["food_group"].astype(str).values)
        .groupby(level=0)
        .sum()
        .rename("consumption_mt")
    )

    gens = network.generators.static
    disp = network.generators.dynamic.p.loc[snap]
    pos_mask = gens["carrier"] == "slack_positive_food"
    neg_mask = gens["carrier"] == "slack_negative_food"

    pos_per_group = pd.Series(dtype=float)
    neg_per_group = pd.Series(dtype=float)
    if pos_mask.any():
        pos_gens = gens[pos_mask]
        pos_vals = disp.reindex(pos_gens.index).fillna(0.0).clip(lower=0.0)
        pos_per_group = (
            pd.Series(pos_vals.values, index=pos_gens["food_group"].astype(str).values)
            .groupby(level=0)
            .sum()
        )
    if neg_mask.any():
        neg_gens = gens[neg_mask]
        neg_vals = -disp.reindex(neg_gens.index).fillna(0.0)
        neg_vals = neg_vals.clip(lower=0.0)
        neg_per_group = (
            pd.Series(neg_vals.values, index=neg_gens["food_group"].astype(str).values)
            .groupby(level=0)
            .sum()
        )

    all_groups = sorted(
        set(consumption_per_group.index)
        | set(pos_per_group.index)
        | set(neg_per_group.index)
    )
    df = pd.DataFrame(index=pd.Index(all_groups, name="food_group"))
    df["consumption_mt"] = consumption_per_group.reindex(all_groups, fill_value=0.0)
    df["positive_slack_mt"] = pos_per_group.reindex(all_groups, fill_value=0.0)
    df["negative_slack_mt"] = neg_per_group.reindex(all_groups, fill_value=0.0)
    # slack_net > 0 means shortage (positive slack generator dispatched);
    # slack_net < 0 means excess (negative slack absorbed surplus from bus).
    df["slack_net_mt"] = df["positive_slack_mt"] - df["negative_slack_mt"]
    return df


def compute_calibration(
    network: pypsa.Network,
    food_groups: list[str],
    *,
    min_multiplier: float = 0.05,
    max_multiplier: float = 5.0,
) -> dict[str, dict]:
    """Build the per-group waste-retention multipliers.

    ``min_multiplier`` and ``max_multiplier`` bound the multiplier so a
    pathological solve cannot push waste outside a physically plausible
    range (e.g. > 95% waste or < 0% waste). The bounds are wide on purpose
    so that they only trip on outright failures.
    """
    per_group = _aggregate_per_group(network)
    out: dict[str, dict] = {}
    for group in food_groups:
        if group not in per_group.index:
            logger.warning(
                "Food group %s not found on consume links; skipping calibration",
                group,
            )
            continue
        row = per_group.loc[group]
        consumption = float(row["consumption_mt"])
        slack_net = float(row["slack_net_mt"])
        if consumption <= 0.0:
            logger.warning(
                "Food group %s has zero consumption; skipping calibration", group
            )
            continue
        # Goal: new consume.p_set = consumption + neg_slack - pos_slack
        #                          = consumption - slack_net
        # consume.p_set = intake / (1 - waste), so (1 - waste_new) /
        # (1 - waste_old) = old_p_set / new_p_set = consumption / target.
        target_p_set = consumption - slack_net
        if target_p_set <= 0:
            logger.warning(
                "Food group %s target p_set <= 0 (consumption=%.1f, slack_net=%.1f); "
                "skipping calibration",
                group,
                consumption,
                slack_net,
            )
            continue
        raw_multiplier = consumption / target_p_set
        multiplier = max(min_multiplier, min(max_multiplier, raw_multiplier))
        if multiplier != raw_multiplier:
            logger.warning(
                "Food group %s waste multiplier %.3f clipped to [%.3f, %.3f] -> %.3f",
                group,
                raw_multiplier,
                min_multiplier,
                max_multiplier,
                multiplier,
            )
        out[group] = {
            "waste_retention_multiplier": float(multiplier),
            "baseline_consumption_mt": consumption,
            "baseline_positive_slack_mt": float(row["positive_slack_mt"]),
            "baseline_negative_slack_mt": float(row["negative_slack_mt"]),
        }
        logger.info(
            "%s: consumption=%.1f Mt, +slack=%.1f Mt, -slack=%.1f Mt -> retention multiplier %.3f",
            group,
            consumption,
            row["positive_slack_mt"],
            row["negative_slack_mt"],
            multiplier,
        )
    return out


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", snakemake.input.network)
    network = pypsa.Network(snakemake.input.network)

    food_groups = list(snakemake.params.food_groups)
    if not food_groups:
        raise ValueError(
            "food_loss_waste_calibration.food_groups is empty; nothing to calibrate"
        )

    calibration = compute_calibration(network, food_groups)

    output = Path(snakemake.output.calibration_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek\n"
        "#\n"
        "# SPDX-License-Identifier: CC-BY-4.0\n"
        "#\n"
        "# Per-food-group waste-retention multipliers, applied by\n"
        "# prepare_food_loss_waste.py as:\n"
        "#   new_waste = 1 - (1 - sdg_waste) * waste_retention_multiplier\n"
        "# Generated by workflow/scripts/compute_food_waste_calibration.py\n"
        "# from a solved validation scenario. Do not hand-edit; rerun\n"
        "# `tools/calibrate food_waste` instead.\n"
    )
    with output.open("w") as fh:
        fh.write(header)
        yaml.safe_dump(calibration, fh, sort_keys=True, default_flow_style=False)
    logger.info("Wrote calibration to %s", output)
