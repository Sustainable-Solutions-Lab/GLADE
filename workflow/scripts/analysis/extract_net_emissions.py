# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract net GHG emissions from emission aggregation links.

Reads the solved network and extracts net emissions from the aggregation
links (aggregate:co2_to_ghg, aggregate:ch4_to_ghg, aggregate:n2o_to_ghg).

For each gas:
- p0 gives the per-gas flow (CO2 in Mt; CH4/N2O in tonnes)
- p1 gives the CO2-equivalent contribution (MtCO2eq) after GWP conversion

Output: net_emissions.csv with columns gas, net_mtco2eq.
"""

import logging

import pandas as pd
import pypsa

logger = logging.getLogger(__name__)

AGGREGATE_LINKS = {
    "co2": "aggregate:co2_to_ghg",
    "ch4": "aggregate:ch4_to_ghg",
    "n2o": "aggregate:n2o_to_ghg",
}


def extract_net_emissions(n: pypsa.Network) -> pd.DataFrame:
    """Extract net emissions per gas from aggregation links.

    Parameters
    ----------
    n : pypsa.Network
        Solved network.

    Returns
    -------
    pd.DataFrame
        Columns: gas, net_mtco2eq. Rows for co2, ch4, n2o, and total.
    """
    snapshot = n.snapshots[-1]

    rows = []
    for gas, link_name in AGGREGATE_LINKS.items():
        if link_name not in n.links.static.index:
            raise ValueError(f"Aggregation link '{link_name}' not found in network")

        # p1 is the CO2-equivalent output (MtCO2eq), sign-flipped by PyPSA
        # (p1 = -efficiency * p0, so p1 is negative when emissions are positive)
        p1 = float(n.links.dynamic.p1.at[snapshot, link_name])
        net_mtco2eq = -p1  # flip sign: positive = net emissions

        rows.append({"gas": gas, "net_mtco2eq": net_mtco2eq})

    total = sum(r["net_mtco2eq"] for r in rows)
    rows.append({"gas": "total", "net_mtco2eq": total})

    return pd.DataFrame(rows)
