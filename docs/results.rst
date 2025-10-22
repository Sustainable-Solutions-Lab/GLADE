.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Results & Visualization
========================

Overview
--------

After solving, the model produces results in three formats:

1. **PyPSA network** (``results/{name}/solved/model.nc``): Complete optimization results in NetCDF format
2. **Visualizations** (``results/{name}/plots/*.pdf``): Publication-quality plots and maps
3. **Data tables** (``results/{name}/plots/*.csv``): Tabular exports for custom analysis

The various output files are structured as follows::

    results/{name}/
    ├── build/
    │   └── model.nc           # Built model before solving
    ├── solved/
    │   └── model.nc           # Solved model with optimal values
    └── plots/
        ├── *.pdf              # Visualizations
        └── *.csv              # Data exports

PyPSA Network Results
---------------------

The solved network (``results/{name}/solved/model.nc``) is a PyPSA Network whose components can be inspected as follows (for example):

.. code-block:: python

   import pypsa

   n = pypsa.Network("results/my_scenario/solved/model.nc")

   # Access component data
   links_df = n.links  # All links (production, processing, trade)
   buses_df = n.buses  # All buses (crops, foods, nutrients, land)
   stores_df = n.stores  # Resource availability (land, water)

   # Optimal flows
   link_flows = n.links_t.p0  # Power/flow on each link (time series if multi-period)

   # Shadow prices (marginal costs)
   bus_prices = n.buses_t.marginal_price  # Marginal value of each commodity

Key Data Structures
~~~~~~~~~~~~~~~~~~~

**n.links**
  Production, processing, and trade links with optimal flows. Columns:

  * ``bus0``, ``bus1``, ``bus2``, ...: Connected buses
  * ``p_nom_opt``: Optimal capacity
  * ``p0``: Flow balance at ``bus0`` (withdrawing or adding material)
  * ``efficiency``, ``efficiency2``, ...: Conversion factors relative to withdrawal from ``bus0``
  * ``marginal_cost``: Cost per unit flow relative to ``bus0``

**n.buses**
  Commodity buses (crops, foods, nutrients) with prices. Columns:

  * ``carrier``: Commodity type (e.g., ``crop_wheat``, ``nutrient_protein``)
  * ``marginal_price``: Shadow price (USD/unit) — economic value of one more unit

**n.stores**
  Resource stores (land, water, fertilizer) with usage. Columns:

  * ``e_nom``: Total capacity (Mha for land, km³ for water, Mt for fertilizer)
  * ``e_initial``: Available amount
  * ``e``: Amount used or deposited (after solving)

**n.global_constraints**
  System-wide limits (total fertilizer, emissions caps, nutritional requirements).
