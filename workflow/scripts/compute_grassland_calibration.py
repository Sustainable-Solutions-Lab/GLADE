# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute grassland forage calibration from a solved model.

Reads the solved network's forage slack to derive per-country grassland yield
corrections and exogenous forage amounts.

**Surplus countries** (negative slack > 0, i.e. grassland output exceeds demand):
  yield_correction = max(0, grassland_output - surplus) / grassland_output
  This scales grassland yields down so supply matches demand.

**Deficit countries** (positive slack > 0, i.e. demand exceeds grassland supply):
  exogenous_forage_mt_dm = positive_slack
  This adds an external forage source to cover the shortfall.

Output CSV: country, yield_correction, exogenous_forage_mt_dm
"""

import logging

import pandas as pd
import pypsa

from workflow.scripts.constants import SPDX_CSV_HEADER
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def compute_grassland_calibration(
    network_path: str,
    output_path: str,
) -> None:
    """Compute and write grassland forage calibration.

    Parameters
    ----------
    network_path : str
        Path to solved PyPSA network (.nc).
    output_path : str
        Path for output calibration CSV.
    """
    n = pypsa.Network(network_path)

    # --- Grassland forage output per country ---
    grass_links = n.links.static[n.links.static["carrier"] == "grassland_production"]
    if grass_links.empty:
        logger.warning("No grassland_production links found; writing empty calibration")
        pd.DataFrame(
            columns=["country", "yield_correction", "exogenous_forage_mt_dm"]
        ).to_csv(output_path, index=False)
        return

    # Grassland dispatch is on bus0 (land, in Mha); forage output = dispatch * efficiency
    dispatch = n.links.dynamic.p0[grass_links.index].iloc[0].abs()
    efficiency = grass_links["efficiency"].astype(float)
    forage_output = dispatch * efficiency
    grass_country = grass_links["country"].values
    grassland_by_country = (
        pd.DataFrame({"country": grass_country, "forage": forage_output.values})
        .groupby("country")["forage"]
        .sum()
    )

    # --- Negative slack (surplus): grassland output exceeds demand ---
    neg_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_negative_feed"
    ]
    surplus_by_country = pd.Series(dtype=float, name="surplus")
    if not neg_slack_gens.empty:
        # Filter to ruminant_forage buses
        forage_mask = neg_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        neg_forage = neg_slack_gens[forage_mask]
        if not neg_forage.empty:
            neg_dispatch = n.generators.dynamic.p[neg_forage.index].iloc[0].abs()
            neg_countries = (
                neg_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            surplus_by_country = (
                pd.DataFrame({"country": neg_countries, "surplus": neg_dispatch.values})
                .groupby("country")["surplus"]
                .sum()
            )
            surplus_by_country = surplus_by_country[surplus_by_country > 1e-10]

    # --- Positive slack (deficit): demand exceeds grassland supply ---
    pos_slack_gens = n.generators.static[
        n.generators.static["carrier"] == "slack_positive_feed"
    ]
    deficit_by_country = pd.Series(dtype=float, name="deficit")
    if not pos_slack_gens.empty:
        forage_mask = pos_slack_gens["bus"].str.startswith("feed:ruminant_forage:")
        pos_forage = pos_slack_gens[forage_mask]
        if not pos_forage.empty:
            pos_dispatch = n.generators.dynamic.p[pos_forage.index].iloc[0]
            pos_countries = (
                pos_forage["bus"].str.extract(r"^feed:ruminant_forage:(.+)$")[0].values
            )
            deficit_by_country = (
                pd.DataFrame({"country": pos_countries, "deficit": pos_dispatch.values})
                .groupby("country")["deficit"]
                .sum()
            )
            deficit_by_country = deficit_by_country[deficit_by_country > 1e-10]

    # --- Build calibration table ---
    all_countries = sorted(
        set(grassland_by_country.index)
        | set(surplus_by_country.index)
        | set(deficit_by_country.index)
    )
    rows = []
    for country in all_countries:
        grass = grassland_by_country.get(country, 0.0)
        surplus = surplus_by_country.get(country, 0.0)
        deficit = deficit_by_country.get(country, 0.0)

        if surplus > 0 and grass > 1e-10:
            yield_correction = max(0.0, grass - surplus) / grass
        else:
            yield_correction = 1.0

        exogenous = deficit if deficit > 0 else 0.0

        rows.append(
            {
                "country": country,
                "yield_correction": round(yield_correction, 6),
                "exogenous_forage_mt_dm": round(exogenous, 6),
            }
        )

    result = pd.DataFrame(rows)

    n_surplus = int((result["yield_correction"] < 1.0).sum())
    n_deficit = int((result["exogenous_forage_mt_dm"] > 0).sum())
    total_surplus = surplus_by_country.sum() if not surplus_by_country.empty else 0.0
    total_deficit = deficit_by_country.sum() if not deficit_by_country.empty else 0.0

    logger.info(
        "Grassland calibration: %d countries with yield correction "
        "(%.1f Mt surplus removed), %d countries with exogenous forage "
        "(%.1f Mt deficit covered)",
        n_surplus,
        total_surplus,
        n_deficit,
        total_deficit,
    )

    with open(output_path, "w") as f:
        f.write(SPDX_CSV_HEADER)
        result.to_csv(f, index=False)
    logger.info("Wrote %d calibration entries to %s", len(result), output_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )

    compute_grassland_calibration(
        network_path=snakemake.input.network,  # type: ignore[name-defined]
        output_path=snakemake.output[0],  # type: ignore[name-defined]
    )
