# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute feed efficiency calibration multipliers from a solved model.

Reads the solved network's positive feed slack and the GLEAM feed baseline to
derive per-(country, feed_category) multipliers that would eliminate the slack.
The multiplier is then expanded to per-(country, product, feed_category) rows
so it can be merged directly onto the feed_to_animal_products table.

Algorithm per (country, feed_category) feed bus:

    baseline = total feed baseline for this bus (Mt DM)
    positive_slack = dispatch of slack_positive_feed generator for this bus
    supply = baseline - positive_slack  (feed actually consumed)
    multiplier = min(baseline / supply, max_multiplier)

Multiplier >= 1.0 always.  Entries with no positive slack get 1.0.

Output CSV: country, product, feed_category, multiplier
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.constants import SPDX_CSV_HEADER
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def compute_calibration(
    network_path: str,
    feed_baseline_path: str,
    feed_to_products_path: str,
    max_multiplier: float,
    output_path: str,
) -> None:
    """Compute and write calibration multipliers.

    Parameters
    ----------
    network_path : str
        Path to solved PyPSA network (.nc).
    feed_baseline_path : str
        Path to GLEAM feed baseline CSV.
    feed_to_products_path : str
        Path to feed_to_animal_products CSV (for product expansion).
    max_multiplier : float
        Maximum allowed multiplier (>1).
    output_path : str
        Path for output calibration CSV.
    """
    # Load solved network
    n = pypsa.Network(network_path)

    # Extract positive feed slack dispatch
    slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_positive_feed"
    ]
    if slack_gens.empty:
        logger.warning("No positive feed slack generators found in network")
        # Write empty calibration (all 1.0)
        ftp = pd.read_csv(feed_to_products_path, comment="#")
        out = ftp[["country", "product", "feed_category"]].copy()
        out["multiplier"] = 1.0
        with open(output_path, "w") as f:
            f.write(SPDX_CSV_HEADER)
            out.to_csv(f, index=False)
        return

    dispatch = n.generators.dynamic.p[slack_gens.index].iloc[0]
    slack_by_bus = pd.DataFrame(
        {"bus": slack_gens["bus"].values, "slack": dispatch.values}
    )
    # Only positive slack matters
    slack_by_bus = slack_by_bus[slack_by_bus["slack"] > 1e-10]

    # Parse bus names: feed:{category}:{country}
    parts = slack_by_bus["bus"].str.extract(
        r"^feed:(?P<feed_category>[^:]+):(?P<country>.+)$"
    )
    slack_by_bus = pd.concat([slack_by_bus, parts], axis=1)
    slack_agg = slack_by_bus.groupby(["country", "feed_category"], as_index=False)[
        "slack"
    ].sum()

    logger.info(
        "Found positive slack on %d (country, category) buses, total %.1f Mt DM",
        len(slack_agg),
        slack_agg["slack"].sum(),
    )

    # Load feed baseline and aggregate by (country, feed_category)
    baseline = pd.read_csv(feed_baseline_path, comment="#")
    baseline_agg = baseline.groupby(["country", "feed_category"], as_index=False)[
        "feed_use_mt_dm"
    ].sum()

    # Merge and compute multipliers
    merged = baseline_agg.merge(slack_agg, on=["country", "feed_category"], how="left")
    merged["slack"] = merged["slack"].fillna(0.0)

    # multiplier = baseline / supply, capped at max_multiplier
    merged["multiplier"] = 1.0
    has_baseline = merged["feed_use_mt_dm"] > 1e-10
    supply = merged["feed_use_mt_dm"] - merged["slack"]
    has_supply = supply > 1e-10
    merged.loc[has_supply & has_baseline, "multiplier"] = (
        merged.loc[has_supply & has_baseline, "feed_use_mt_dm"]
        / supply[has_supply & has_baseline]
    ).clip(upper=max_multiplier)
    merged.loc[~has_supply & has_baseline, "multiplier"] = max_multiplier

    n_adjusted = (merged["multiplier"] > 1.0).sum()
    logger.info(
        "Computed multipliers for %d (country, category) pairs: "
        "%d adjusted (median %.3f, max %.3f)",
        len(merged),
        n_adjusted,
        merged.loc[merged["multiplier"] > 1.0, "multiplier"].median()
        if n_adjusted
        else 1.0,
        merged["multiplier"].max(),
    )

    # Expand to per-(country, product, feed_category) using feed_to_products
    ftp = pd.read_csv(feed_to_products_path, comment="#")
    cal_lookup = merged[["country", "feed_category", "multiplier"]]

    result = ftp[["country", "product", "feed_category"]].merge(
        cal_lookup, on=["country", "feed_category"], how="left"
    )
    result["multiplier"] = result["multiplier"].fillna(1.0)

    with open(output_path, "w") as f:
        f.write(SPDX_CSV_HEADER)
        result.to_csv(f, index=False)
    logger.info("Wrote %d calibration entries to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    compute_calibration(
        network_path=snakemake.input.network,  # type: ignore[name-defined]
        feed_baseline_path=snakemake.input.feed_baseline,  # type: ignore[name-defined]
        feed_to_products_path=snakemake.input.feed_to_products,  # type: ignore[name-defined]
        max_multiplier=float(snakemake.params.max_multiplier),  # type: ignore[name-defined]
        output_path=snakemake.output[0],  # type: ignore[name-defined]
    )
