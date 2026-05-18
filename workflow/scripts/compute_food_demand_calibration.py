# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute per-food global demand multipliers from food-bus slack.

This generates ``data/curated/calibration/food_demand.csv`` -- a per-food
multiplier on the baseline-diet target_mt applied uniformly across
countries by ``_match_baseline_to_consume_links`` in
``workflow/scripts/solve_model/core.py``.

The multiplier is derived from the global food-bus balance reported by an
uncalibrated validation-mode solve:

    multiplier = clip(consumption_mt / (consumption_mt + net_slack_mt),
                      [min_multiplier, max_multiplier])

where ``net_slack = positive_slack - negative_slack`` aggregated over
countries for each food. Positive net slack (LP filled a shortage with
the slack generator) shrinks the multiplier; negative net slack (LP
absorbed excess into the disposal slack) grows the multiplier.
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _aggregate_per_food(network: pypsa.Network) -> pd.DataFrame:
    """Return per-food consumption (Mt) and net slack (Mt) from a solved network."""
    snap = network.snapshots[-1]
    links = network.links.static
    consume = links[links["carrier"] == "food_consumption"]
    if consume.empty:
        return pd.DataFrame(columns=["consumption_mt", "net_slack_mt"])

    p0 = network.links.dynamic["p0"].loc[snap]
    consume_p0 = p0.reindex(consume.index).fillna(0.0).clip(lower=0.0)
    consumption_per_food = (
        pd.Series(consume_p0.values, index=consume["food"].astype(str).values)
        .groupby(level=0)
        .sum()
        .rename("consumption_mt")
    )

    gens = network.generators.static
    disp = network.generators.dynamic["p"].loc[snap]
    pos_mask = gens["carrier"] == "slack_positive_food"
    neg_mask = gens["carrier"] == "slack_negative_food"

    pos_per_food = pd.Series(dtype=float)
    neg_per_food = pd.Series(dtype=float)
    if pos_mask.any():
        pos_gens = gens[pos_mask]
        pos_vals = disp.reindex(pos_gens.index).fillna(0.0).clip(lower=0.0)
        pos_per_food = (
            pd.Series(pos_vals.values, index=pos_gens["food"].astype(str).values)
            .groupby(level=0)
            .sum()
        )
    if neg_mask.any():
        neg_gens = gens[neg_mask]
        # ``slack_negative_food`` dispatch is reported as a negative number
        # (the generator absorbs excess); flip the sign so neg_per_food is
        # the absolute amount of disposed mass.
        neg_vals = -disp.reindex(neg_gens.index).fillna(0.0)
        neg_vals = neg_vals.clip(lower=0.0)
        neg_per_food = (
            pd.Series(neg_vals.values, index=neg_gens["food"].astype(str).values)
            .groupby(level=0)
            .sum()
        )

    all_foods = sorted(
        set(consumption_per_food.index)
        | set(pos_per_food.index)
        | set(neg_per_food.index)
    )
    df = pd.DataFrame(index=pd.Index(all_foods, name="food"))
    df["consumption_mt"] = consumption_per_food.reindex(all_foods, fill_value=0.0)
    df["positive_slack_mt"] = pos_per_food.reindex(all_foods, fill_value=0.0)
    df["negative_slack_mt"] = neg_per_food.reindex(all_foods, fill_value=0.0)
    # net_slack > 0 -> shortage; multiplier should shrink demand.
    # net_slack < 0 -> excess;   multiplier should grow demand.
    df["net_slack_mt"] = df["positive_slack_mt"] - df["negative_slack_mt"]
    return df


def compute_calibration(
    network: pypsa.Network,
    *,
    min_multiplier: float,
    max_multiplier: float,
    min_consumption_mt: float = 1e-6,
) -> pd.DataFrame:
    """Build the per-food demand multipliers.

    ``min_multiplier`` and ``max_multiplier`` bound the multiplier so a
    pathological mismatch cannot push demand to extreme values. The bounds
    should be tight (defaults 0.5 and 2.0) so they only trip when the
    upstream data has a serious structural problem we want to surface.
    """
    per_food = _aggregate_per_food(network)
    if per_food.empty:
        return per_food.assign(multiplier=pd.Series(dtype=float))

    cons = per_food["consumption_mt"]
    net = per_food["net_slack_mt"]

    # Foods with effectively zero consumption: no multiplier needed.
    has_demand = cons > min_consumption_mt
    raw = pd.Series(1.0, index=per_food.index)
    raw.loc[has_demand] = cons.loc[has_demand] / (
        cons.loc[has_demand] + net.loc[has_demand]
    )

    clipped = raw.clip(lower=min_multiplier, upper=max_multiplier)
    per_food["multiplier"] = clipped
    per_food["raw_multiplier"] = raw

    n_clipped = int(((raw != clipped) & has_demand).sum())
    if n_clipped > 0:
        offenders = per_food.loc[(raw != clipped) & has_demand, "raw_multiplier"]
        logger.warning(
            "Clipped %d food multipliers outside [%.2f, %.2f]: %s",
            n_clipped,
            min_multiplier,
            max_multiplier,
            ", ".join(f"{food}={val:.2f}" for food, val in offenders.round(3).items()),
        )

    return per_food


def main() -> None:
    network_path = snakemake.input.network  # type: ignore[name-defined]
    output_path = Path(snakemake.output.calibration_file)  # type: ignore[name-defined]
    min_multiplier = float(snakemake.params.min_multiplier)  # type: ignore[name-defined]
    max_multiplier = float(snakemake.params.max_multiplier)  # type: ignore[name-defined]

    logger.info("Loading solved network from %s", network_path)
    n = pypsa.Network(str(network_path))

    cal = compute_calibration(
        n,
        min_multiplier=min_multiplier,
        max_multiplier=max_multiplier,
    )

    n_adj = int(((cal["multiplier"] - 1.0).abs() > 0.01).sum())
    logger.info(
        "Food demand calibration: %d / %d foods need >1%% adjustment",
        n_adj,
        len(cal),
    )
    if n_adj > 0:
        adjusted = (
            cal.loc[(cal["multiplier"] - 1.0).abs() > 0.01]
            .sort_values("multiplier")
            .round(4)
        )
        for food, row in adjusted.iterrows():
            logger.info(
                "  %s: multiplier=%.3f (consumption=%.2f Mt, net_slack=%+.2f Mt)",
                food,
                row["multiplier"],
                row["consumption_mt"],
                row["net_slack_mt"],
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out = cal.reset_index()[
        ["food", "multiplier", "net_slack_mt", "consumption_mt"]
    ].sort_values("food")
    out.to_csv(output_path, index=False)
    logger.info("Wrote %d food demand multipliers to %s", len(out), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]
    main()
