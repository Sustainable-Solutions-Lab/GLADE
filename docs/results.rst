.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Results & Visualization
========================

Overview
--------

After solving, the model produces results in several formats:

1. **PyPSA network** (``results/{name}/solved/model_scen-{scenario}.nc``): Complete optimization results in NetCDF format
2. **Analysis outputs** (``results/{name}/analysis/scen-{scenario}/*.parquet``): Extracted statistics and impact assessments (see :ref:`analysis`)
3. **Visualizations** (``results/{name}/plots/scen-{scenario}/*.pdf``): Publication-quality plots and maps

The various output files are structured as follows::

    results/{name}/
    ├── build/
    │   └── model_scen-{scenario}.nc    # Built model before solving
    ├── solved/
    │   └── model_scen-{scenario}.nc    # Solved model with optimal values
    ├── analysis/
    │   └── scen-{scenario}/
    │       ├── crop_production.parquet     # Production by crop/region
    │       ├── land_use.parquet            # Land allocation
    │       ├── animal_production.parquet   # Livestock production
    │       ├── food_consumption.parquet    # Consumption by food
    │       ├── food_group_consumption.parquet  # Consumption by food group
    │       ├── ghg_attribution.parquet      # Consumption-attributed GHG intensity by food
    │       ├── ghg_attribution_totals.parquet  # Total attributed GHG by country/food group
    │       ├── net_emissions.parquet       # Net GHG emissions by gas
    │       ├── health_marginals.parquet    # Marginal health impacts by food group
    │       ├── health_totals.parquet       # Total YLL by health cluster
    │       ├── health_attribution.parquet  # YLL attributed by cluster/cause/food group
    │       └── objective_breakdown.parquet # Cost categories breakdown
    └── plots/
        └── scen-{scenario}/
            └── *.pdf                   # Visualizations

PyPSA Network Results
---------------------

The solved network (``results/{name}/solved/model_scen-{scenario}.nc``) is a PyPSA Network whose components can be inspected as follows (for example):

.. code-block:: python

   import pypsa

   n = pypsa.Network("results/my_scenario/solved/model_scen-default.nc")

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

  * ``e_nom``: Total capacity (Mha for land, Mm³ for water, Mt for fertilizer)
  * ``e_initial``: Available amount
  * ``e``: Amount used or deposited (after solving)

**n.global_constraints**
  System-wide limits (total fertilizer, emissions caps, nutritional requirements).
