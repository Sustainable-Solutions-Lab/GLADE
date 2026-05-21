# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract aggregate baseline deviation statistics from solved networks.

Computes total absolute deviation from baseline for crop area, pasture area,
animal feed use, and food consumption. Returns one row per component with
baseline totals, actual totals, and absolute deviation in physical units.

The food-consumption row depends on a ``baseline_consumption_mt`` column on
food_consumption links, stamped at solve time by
:func:`workflow.scripts.solve_model.core._match_baseline_to_consume_links`'s
caller whenever a matched baseline is computed (enforce_baseline_diet or
deviation_penalty.diet.enabled). When the column is absent the row is NaN.
"""

import numpy as np
import pandas as pd
import pypsa


def extract_baseline_deviation(n: pypsa.Network) -> pd.DataFrame:
    """Extract aggregate baseline deviation per component.

    For each component, computes:
    - total baseline (sum of per-link baselines)
    - total actual (sum of per-link dispatch)
    - total absolute deviation (sum of |actual - baseline| per link)

    Parameters
    ----------
    n : pypsa.Network
        Solved network with baseline columns on links.

    Returns
    -------
    pd.DataFrame
        Columns: component, baseline_total, actual_total, abs_deviation, unit.
        Components: crop_area, pasture_area, animal_feed_use, food_consumption.
    """
    links = n.links.static
    p = n.links.dynamic["p0"].loc[n.snapshots[-1]]

    rows = []
    for carrier, baseline_col, label, unit in [
        ("crop_production", "baseline_area_mha", "crop_area", "Mha"),
        ("grassland_production", "baseline_area_mha", "pasture_area", "Mha"),
        ("animal_production", "baseline_feed_use_mt_dm", "animal_feed_use", "Mt DM"),
        ("food_consumption", "baseline_consumption_mt", "food_consumption", "Mt"),
    ]:
        sel = links[links["carrier"] == carrier]
        if sel.empty or baseline_col not in sel.columns:
            rows.append(
                {
                    "component": label,
                    "baseline_total": np.nan,
                    "actual_total": np.nan,
                    "abs_deviation": np.nan,
                    "unit": unit,
                }
            )
            continue

        baseline = sel[baseline_col].values
        actual = p[sel.index].values
        rows.append(
            {
                "component": label,
                "baseline_total": baseline.sum(),
                "actual_total": actual.sum(),
                "abs_deviation": np.abs(actual - baseline).sum(),
                "unit": unit,
            }
        )

    return pd.DataFrame(rows)
