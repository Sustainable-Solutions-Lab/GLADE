# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract GHG emissions by gas and source category from solved networks.

Reads the solved network and extracts emissions broken down by gas (CO2, CH4,
N2O) and source category (e.g. Land Use Change, Enteric fermentation, etc.)
using ``n.statistics.energy_balance()``.

Output: net_emissions.csv with columns gas, source, mtco2eq.
Per-gas and total net emissions can be derived by summing over sources.
"""

from collections import defaultdict
import logging

import pandas as pd
import pypsa

logger = logging.getLogger(__name__)


def categorize_emission_carrier(carrier: str, bus_carrier: str) -> str:
    """Categorize an emission source by its carrier and gas type.

    Parameters
    ----------
    carrier : str
        Link carrier name
    bus_carrier : str
        The emission bus being fed ("co2", "ch4", "n2o")

    Returns
    -------
    str
        Category name for the source breakdown
    """
    carrier_map = {
        "residue_incorporation": "Crop residue incorporation",
        "spare_land": "Carbon sequestration",
        "spare_existing_grassland": "Carbon sequestration",
        "fertilizer_distribution": "Synthetic fertilizer application",
        "land_conversion": "Land Use Change",
        "new_to_pasture": "Land Use Change",
    }

    if carrier in carrier_map:
        return carrier_map[carrier]

    if carrier == "crop_production":
        if bus_carrier == "ch4":
            return "Rice cultivation"
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Crop production"
    elif carrier == "crop_production_multi":
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Multi-cropping"
    elif carrier == "animal_production":
        if bus_carrier == "n2o":
            return "Manure management & application"
        elif bus_carrier == "ch4":
            return "Enteric fermentation & Manure management"
        return "Livestock production"
    elif carrier == "grassland_production":
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Grassland"
    elif carrier == "food_processing":
        return "Food processing"
    elif carrier.startswith("trade_"):
        return "Trade"
    else:
        return f"Other ({carrier})"


def extract_net_emissions(
    n: pypsa.Network,
    ch4_gwp: float,
    n2o_gwp: float,
) -> pd.DataFrame:
    """Extract emissions by gas and source category from solved network.

    Uses ``n.statistics.energy_balance()`` to extract emission flows grouped
    by ``(bus_carrier, carrier)``, then categorizes carriers into human-readable
    source labels.  CH4 and N2O from animal production are further split into
    sub-categories (enteric vs manure, pasture vs managed) using link-level
    share attributes.

    Parameters
    ----------
    n : pypsa.Network
        Solved network.
    ch4_gwp : float
        Global warming potential for CH4 (kg CO2eq / kg CH4).
    n2o_gwp : float
        Global warming potential for N2O (kg CO2eq / kg N2O).

    Returns
    -------
    pd.DataFrame
        Columns: gas, source, mtco2eq.  All values in MtCO2eq.
    """
    emissions: dict[str, dict[str, float]] = {
        "co2": defaultdict(float),
        "ch4": defaultdict(float),
        "n2o": defaultdict(float),
    }

    gwp_factors = {
        "co2": 1.0,
        "ch4": ch4_gwp,
        "n2o": n2o_gwp,
    }

    conversion_carriers = {"co2", "ch4", "n2o", "emission_aggregation"}

    try:
        balance = n.statistics.energy_balance(groupby=["bus_carrier", "carrier"])
    except Exception as e:
        logger.error("Failed to compute energy balance: %s", e)
        return pd.DataFrame(columns=["gas", "source", "mtco2eq"])

    for (_component, bus_carrier, carrier), value in balance.items():
        if bus_carrier not in gwp_factors:
            continue
        if carrier in conversion_carriers:
            continue
        if abs(value) < 1e-9:
            continue

        gwp_factor = gwp_factors[bus_carrier]

        # CH4 and N2O flows are in tonnes; convert to Mt before applying GWP
        value_mt = value * 1e-6 if bus_carrier in ("ch4", "n2o") else value
        emission_co2eq = value_mt * gwp_factor

        category = categorize_emission_carrier(carrier, bus_carrier)
        emissions[bus_carrier][category] += emission_co2eq

    # --- Split manure N2O into pasture vs managed ---
    if "Manure management & application" in emissions["n2o"]:
        links_df = n.links.static
        produce_mask = links_df.carrier == "animal_production"
        pasture_share = (
            links_df.loc[produce_mask, "pasture_n2o_share"].fillna(0.0).astype(float)
        )

        p4 = n.links.dynamic["p4"].loc[:, produce_mask]
        weights = n.snapshot_weightings["objective"]
        pasture_t_n2o = -(
            p4.multiply(pasture_share, axis=1).multiply(weights, axis=0).sum().sum()
        )
        pasture_mtco2eq = pasture_t_n2o * n2o_gwp * 1e-6

        total_mtco2eq = emissions["n2o"].get("Manure management & application", 0.0)
        managed_mtco2eq = max(total_mtco2eq - pasture_mtco2eq, 0.0)

        emissions["n2o"].pop("Manure management & application", None)
        emissions["n2o"]["Manure: pasture deposition"] = pasture_mtco2eq
        emissions["n2o"]["Manure: managed systems"] = managed_mtco2eq

    # --- Split CH4 into enteric vs manure ---
    if "Enteric fermentation & Manure management" in emissions["ch4"]:
        links_df = n.links.static
        produce_mask = links_df.carrier == "animal_production"
        manure_share = (
            links_df.loc[produce_mask, "manure_ch4_share"].fillna(0.0).astype(float)
        )

        p2 = n.links.dynamic["p2"].loc[:, produce_mask]
        weights = n.snapshot_weightings["objective"]
        manure_t_ch4 = -(
            p2.multiply(manure_share, axis=1).multiply(weights, axis=0).sum().sum()
        )
        manure_mtco2eq = manure_t_ch4 * ch4_gwp * 1e-6

        total_mtco2eq = emissions["ch4"].get(
            "Enteric fermentation & Manure management", 0.0
        )
        enteric_mtco2eq = max(total_mtco2eq - manure_mtco2eq, 0.0)

        emissions["ch4"].pop("Enteric fermentation & Manure management", None)
        emissions["ch4"]["Enteric fermentation"] = enteric_mtco2eq
        emissions["ch4"]["Manure: managed systems"] = manure_mtco2eq

    # Build flat DataFrame
    rows = []
    for gas, sources in emissions.items():
        for source, amount in sources.items():
            rows.append({"gas": gas, "source": source, "mtco2eq": amount})

    if not rows:
        return pd.DataFrame(columns=["gas", "source", "mtco2eq"])

    return pd.DataFrame(rows)
