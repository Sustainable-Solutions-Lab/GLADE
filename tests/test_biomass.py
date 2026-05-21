# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for biomass-routing helpers in `workflow.scripts.build_model.biomass`.

Pins the skip-on-missing-supply behaviour of `add_biofuel_links` so
regional configs without all global producers (e.g. Europe-only without
sugarcane) do not silently introduce infeasible fixed-demand links.
Also exercises the food-bus DM deflation introduced for biomass routing.
"""

import pandas as pd
import pypsa

from workflow.scripts.build_model import biomass


def _build_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(["now"])
    n.add(
        "Bus",
        ["food:wheat:USA", "crop:wheat:USA", "biomass:USA", "land:USA"],
        carrier=["food", "crop", "biomass", "land"],
    )
    # Only wheat has a crop_production link; sugarcane and maize do not.
    n.add(
        "Link",
        "produce:wheat",
        bus0="land:USA",
        bus1="crop:wheat:USA",
        carrier="crop_production",
        crop="wheat",
    )
    return n


def test_biofuel_skips_crops_without_production(caplog):
    n = _build_network()
    df = pd.DataFrame(
        {
            "source_item": ["wheat", "sugarcane"],
            "crop": ["wheat", "sugarcane"],
            "country": ["USA", "USA"],
            "bus_type": ["food", "food"],
            "demand_mt": [10.0, 5.0],
        }
    )
    # bus0 for sugarcane does not exist either, but the no-supply check
    # fires first so we test the *no-supply* skip path independently
    # of the bus-not-found path.
    n.add("Bus", "food:sugarcane:USA", carrier="food")

    with caplog.at_level("WARNING"):
        biomass.add_biofuel_links(
            n, df, crop_moisture={"wheat": 0.13, "sugarcane": 0.7}
        )

    names = n.links.static.index.tolist()
    assert any("biofuel:wheat:" in s for s in names)
    assert not any("biofuel:sugarcane:" in s for s in names)
    assert any("no production anywhere" in rec.message for rec in caplog.records)


def test_biofuel_food_path_deflates_with_moisture():
    """p_nom is fresh-equivalent; bus1 flow at p == p_nom equals demand_mt (DM)."""
    n = _build_network()
    df = pd.DataFrame(
        {
            "source_item": ["wheat"],
            "crop": ["wheat"],
            "country": ["USA"],
            "bus_type": ["food"],
            "demand_mt": [10.0],  # MtDM
        }
    )
    biomass.add_biofuel_links(n, df, crop_moisture={"wheat": 0.13})

    link = n.links.static.loc["biofuel:wheat:USA"]
    expected_eff = 1.0 - 0.13
    assert link["efficiency"] == expected_eff
    # p_nom * efficiency must equal demand (MtDM)
    assert abs(link["p_nom"] * link["efficiency"] - 10.0) < 1e-9
