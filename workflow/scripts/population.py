# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Population data utilities for the food systems model.

This module provides functions for accessing population data embedded
in PyPSA networks, ensuring consistent population values across build,
solve, and analysis phases.
"""

import pypsa


def get_country_population(n: pypsa.Network) -> dict[str, float]:
    """Get country population from the network metadata.

    Parameters
    ----------
    n : pypsa.Network
        Network with population data in n.meta["population"].

    Returns
    -------
    dict[str, float]
        Mapping from ISO3 country codes to population values.

    Raises
    ------
    KeyError
        If population data is not embedded in the network.
    """
    pop_meta = n.meta.get("population")
    if pop_meta is None:
        raise KeyError(
            "Population data not found in network metadata. "
            "Ensure the model was built with population embedding enabled."
        )
    return dict(pop_meta["country"])


def get_total_population(n: pypsa.Network) -> float:
    """Get total population across all countries in the network.

    Parameters
    ----------
    n : pypsa.Network
        Network with population data in n.meta["population"].

    Returns
    -------
    float
        Total population.
    """
    return sum(get_country_population(n).values())


def get_health_cluster_population(n: pypsa.Network) -> dict[int, float]:
    """Get population by health cluster from the network metadata.

    Parameters
    ----------
    n : pypsa.Network
        Network with population data in n.meta["population"].

    Returns
    -------
    dict[int, float]
        Mapping from health cluster ID to population values.

    Raises
    ------
    KeyError
        If health cluster population data is not available.
    """
    pop_meta = n.meta.get("population")
    if pop_meta is None:
        raise KeyError("Population data not found in network metadata.")

    cluster_pop = pop_meta.get("health_cluster")
    if cluster_pop is None:
        raise KeyError(
            "Health cluster population not available. "
            "This may indicate health stores were not added at build time."
        )
    # Convert string keys back to int (JSON serialization converts int keys to strings)
    return {int(k): float(v) for k, v in cluster_pop.items()}
