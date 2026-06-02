# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute exogenous supplementary-feed supply from a solved validation model.

Mirrors the deficit-side of ``compute_grassland_calibration.py``: for each
country and each supplementary feed category (``monogastric_protein``,
``ruminant_protein``, ``ruminant_roughage``), the positive slack on the
corresponding feed bus is recorded as an exogenous supply that the model can
rely on at solve time.

Rationale: a share of protein- and roughage-feed demand cannot be produced
endogenously by the model. For protein the missing sources are fishmeal
(seafood isn't modelled), synthetic amino acids, and animal by-products (meat
& bone meal, blood, feathers). For roughage, residue-dependent systems
(notably South Asia) demand more crop residues than the modelled domestic
residue supply provides -- partly a real, documented fodder deficit, partly
cut green fodder and residue we don't fully account for. Rather than model
each explicitly, we book the per-country shortfall as an exogenous supply
with the same accounting convention used for forage deficits (see
``exogenous_forage.csv``).

Output: ``exogenous_feed.csv`` with columns ``country,
monogastric_protein_mt_dm, ruminant_protein_mt_dm, ruminant_roughage_mt_dm``.
"""

import logging
from pathlib import Path

import pandas as pd
import pypsa

from workflow.scripts.constants import SPDX_CSV_HEADER
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

EXOGENOUS_FEED_CATEGORIES = (
    "monogastric_protein",
    "ruminant_protein",
    "ruminant_roughage",
)


def _positive_slack_by_country(n: pypsa.Network, feed_category: str) -> pd.Series:
    """Return positive feed slack (Mt DM) by country for one feed bus."""
    bus_prefix = f"feed:{feed_category}:"
    pos_slack = n.generators.static[
        n.generators.static["carrier"] == "slack_positive_feed"
    ]
    if pos_slack.empty:
        return pd.Series(dtype=float, name=feed_category)

    mask = pos_slack["bus"].str.startswith(bus_prefix)
    pos_slack = pos_slack[mask]
    if pos_slack.empty:
        return pd.Series(dtype=float, name=feed_category)

    dispatch = n.generators.dynamic.p[pos_slack.index].loc[n.snapshots[-1]]
    countries = pos_slack["bus"].str.extract(rf"^{bus_prefix}(.+)$")[0].values
    by_country = (
        pd.DataFrame({"country": countries, "deficit": dispatch.values})
        .groupby("country")["deficit"]
        .sum()
    )
    return by_country[by_country > 1e-10].rename(feed_category)


def compute_exogenous_feed_calibration(
    network_path: str,
    output_path: str,
) -> None:
    """Read positive protein-feed slack from solved network and write CSV.

    Parameters
    ----------
    network_path : str
        Path to a solved PyPSA network (.nc) — typically produced by the
        ``validation.yaml`` config with ``enforce_baseline_feed=true``.
    output_path : str
        Path where the calibration CSV is written.
    """
    if Path(network_path).stat().st_size == 0:
        raise ValueError(
            f"Solved network file is empty: {network_path}\n"
            "This usually means the solve step failed (e.g. model was "
            "infeasible). Check the solve log for details."
        )

    n = pypsa.Network(network_path)

    deficits: dict[str, pd.Series] = {
        cat: _positive_slack_by_country(n, cat) for cat in EXOGENOUS_FEED_CATEGORIES
    }

    countries = sorted({c for s in deficits.values() for c in s.index})

    rows = []
    for country in countries:
        row = {"country": country}
        for cat in EXOGENOUS_FEED_CATEGORIES:
            row[f"{cat}_mt_dm"] = round(float(deficits[cat].get(country, 0.0)), 6)
        rows.append(row)

    df = pd.DataFrame(
        rows, columns=["country", *[f"{c}_mt_dm" for c in EXOGENOUS_FEED_CATEGORIES]]
    )

    totals = {cat: float(deficits[cat].sum()) for cat in EXOGENOUS_FEED_CATEGORIES}
    n_nonzero = {
        cat: int((deficits[cat] > 0).sum()) for cat in EXOGENOUS_FEED_CATEGORIES
    }
    logger.info(
        "Protein-feed calibration: %s",
        ", ".join(
            f"{cat}={n_nonzero[cat]} countries / {totals[cat]:.1f} Mt DM"
            for cat in EXOGENOUS_FEED_CATEGORIES
        ),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(SPDX_CSV_HEADER)
        df.to_csv(f, index=False)
    logger.info("Wrote calibration to %s", output_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    compute_exogenous_feed_calibration(
        network_path=snakemake.input.network,  # type: ignore[name-defined]
        output_path=snakemake.output.exogenous_feed,  # type: ignore[name-defined]
    )
