.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Workflow & Execution
====================

Overview
--------

The ``food-opt`` model uses `Snakemake <https://snakemake.readthedocs.io/>`__ for workflow orchestration. If you have never used Snakemake before, consider having a look at the official `tutotial <https://snakemake.readthedocs.io/en/stable/tutorial/tutorial.html>`__ to get familiar with the basic concepts. The workflow follows these main stages:

1. **Downloads** (GAEZ, GADM, UN WPP, FAOSTAT)
2. **Preprocessing** (regions, resource classes, yields, population, health)
3. **Model Building** (PyPSA network construction)
4. **Solving** (LP optimization with health costs)
5. **Visualization** (plots, maps, CSV exports)

Each stage is defined by Snakemake rules that specify inputs, outputs, and a script or piece of code.

Validation Hook
---------------

Before Snakemake resolves any rules, the ``workflow/Snakefile`` uses the ``onstart`` hook to run configuration/data validation powered by `Pydantic <https://docs.pydantic.dev/>`__ and `Pandera <https://pandera.readthedocs.io/>`__. The checks live in ``workflow/validation/`` and currently ensure, for example, that every category in ``data/curated/food_groups.csv`` is listed under ``config.food_groups.included``. Add new validators by dropping another module in that package and registering it in ``workflow/validation/__init__.py``—the hook aggregates all errors and aborts the workflow if any check fails.

The complete workflow dependency graph is shown below. Each node represents a Snakemake rule, and edges show dependencies between rules.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/workflow_rulegraph.png
   :alt: Workflow dependency graph
   :align: center
   :width: 100%

   Complete workflow dependency graph showing all Snakemake rules and their relationships

Key Snakemake Rules
-------------------

Data Preparation Rules
~~~~~~~~~~~~~~~~~~~~~~

**simplify_gadm**
  * **Input**: ``data/downloads/gadm.gpkg``
  * **Output**: ``processing/shared/gadm-simplified.gpkg``
  * **Script**: ``workflow/scripts/simplify_gadm.py``
  * **Purpose**: Simplify administrative boundaries for faster processing

**build_regions**
  * **Input**: Simplified GADM
  * **Output**: ``processing/{name}/regions.geojson``
  * **Script**: ``workflow/scripts/build_regions.py``
  * **Purpose**: Cluster administrative units into optimization regions

**prepare_population**
  * **Input**: ``data/downloads/WPP_population.csv.gz``
  * **Output**: ``processing/{name}/population.csv``, ``processing/{name}/population_age.csv``
  * **Script**: ``workflow/scripts/prepare_population.py``
  * **Purpose**: Extract population for planning horizon and countries

**compute_resource_classes**
  * **Input**: All GAEZ yield rasters, regions
  * **Output**: ``processing/{name}/resource_classes.nc``
  * **Script**: ``workflow/scripts/compute_resource_classes.py``
  * **Purpose**: Define yield quantile classes within each region

**aggregate_class_areas**
  * **Input**: Resource classes, suitability rasters, regions
  * **Output**: ``processing/{name}/land_area_by_class.csv``
  * **Script**: ``workflow/scripts/aggregate_class_areas.py``
  * **Purpose**: Compute available land area per (region, class, water, crop)

**build_crop_yields**
  * **Wildcards**: ``{crop}`` (crop name), ``{water_supply}`` ("r" or "i")
  * **Input**: Resource classes, GAEZ rasters (yield, suitability, water, growing season)
  * **Output**: ``processing/{name}/crop_yields/{crop}_{water_supply}.csv``
  * **Script**: ``workflow/scripts/build_crop_yields.py``
  * **Purpose**: Aggregate yields by (region, class) for each crop

**build_multi_cropping**
  * **Input**: Resource classes, regions, the RES01 multiple-cropping zone rasters, and the required GAEZ RES05 rasters (yield, suitability, growing season start/length, plus water requirement for irrigated variants) for every crop referenced in ``config.multiple_cropping``
  * **Output**: ``processing/{name}/multi_cropping/eligible_area.csv`` (eligible hectares, irrigated water requirement), ``processing/{name}/multi_cropping/cycle_yields.csv`` (per-cycle yields)
  * **Script**: ``workflow/scripts/build_multi_cropping.py``
  * **Purpose**: Filter pixels by the RES01 class (ignoring relay-only options), confirm crop calendars fit within the year, aggregate eligible hectares to regions/resource classes, and compute per-cycle yields

**build_grassland_yields**
  * **Input**: ISIMIP grassland yield NetCDF, resource classes, regions
  * **Output**: ``processing/{name}/grassland_yields.csv``
  * **Script**: ``workflow/scripts/build_grassland_yields.py``
  * **Purpose**: Aggregate grassland yields for grazing production

**process_blue_water_availability**
  * **Input**: Water Footprint Network shapefile + Excel workbook
  * **Output**: ``processing/{name}/water/blue_water_availability.csv``
  * **Script**: ``workflow/scripts/process_blue_water_availability.py``
  * **Purpose**: Build monthly basin-level blue water availability

**build_region_water_sustainable**
  * **Input**: Blue water availability, regions, crop yields
  * **Output**: ``processing/{name}/water/sustainable/monthly_region_water.csv``, ``processing/{name}/water/sustainable/region_growing_season_water.csv``
  * **Script**: ``workflow/scripts/build_region_water_availability.py``
  * **Purpose**: Allocate basin availability to regions and growing seasons

**build_region_water_current_use**
  * **Input**: Huang irrigation withdrawals, regions, crop yields
  * **Output**: ``processing/{name}/water/current_use/monthly_region_water.csv``, ``processing/{name}/water/current_use/region_growing_season_water.csv``
  * **Script**: ``workflow/scripts/process_huang_irrigation_water.py``
  * **Purpose**: Aggregate current irrigation withdrawals to regions and growing seasons

**select_water_scenario**
  * **Input**: Scenario-specific water outputs
  * **Output**: ``processing/{name}/water/monthly_region_water.csv``, ``processing/{name}/water/region_growing_season_water.csv``
  * **Purpose**: Copy the configured water supply scenario into the unified paths used by the model

**build_current_grassland_area**
  * **Input**: Resource classes, land-cover fractions (``processing/{name}/luc/lc_masks.nc``), regions
  * **Output**: ``processing/{name}/luc/current_grassland_area_by_class.csv``
  * **Script**: ``workflow/scripts/build_current_grassland_area.py``
  * **Purpose**: Derive observed managed grassland area by region/resource class from the same land-cover fractions used for LUC calculations; clamps grazing links during validation runs

**build_grazing_only_land**
  * **Input**: Resource classes, land-cover fractions (``processing/{name}/luc/lc_masks.nc``), rainfed GAEZ suitability rasters
  * **Output**: ``processing/{name}/land_grazing_only_by_class.csv``
  * **Script**: ``workflow/scripts/build_grazing_only_land.py``
  * **Purpose**: Estimate marginal pasture area (grassland that is unsuitable for cropland even after accounting for convertible cropland/forest cover) so grazing can draw from a dedicated land pool without competing with cropland-suitable hectares

**prepare_health_costs**
  * **Input**: Regions, DIA health data, population
  * **Output**: ``processing/{name}/health/*.csv`` (risk breakpoints, dose-response, clusters)
  * **Script**: ``workflow/scripts/prepare_health_costs.py``
  * **Purpose**: Compute health cluster parameters for DALY calculations

Model Building and Solving
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**build_model**
  * **Input**: All crop yields, multi-cropping aggregates, grassland yields, land areas, population, water availability, static data files (crops.csv, foods.csv with pathway-based processing, etc.)
  * **Output**: ``results/{name}/build/model.nc``
  * **Script**: ``workflow/scripts/build_model.py``
  * **Purpose**: Construct PyPSA network with all components, links, and constraints. Creates multi-output links for regular crops, configured multi-cropping sequences, and crop→food processing pathways defined in foods.csv.

**solve_model**
  * **Input**: Built model, health data, food-to-risk mapping
  * **Output**: ``results/{name}/solved/model.nc``
  * **Script**: ``workflow/scripts/solve_model.py``
  * **Purpose**: Add health costs, solve LP, save results. Health impacts are
    encoded as per-cluster, per-cause ``Store`` assets (carrier ``yll_<cause>``)
    whose level equals million YLL derived from dietary risk. The store cost
    encodes the value of a life year so the health burden shows up in standard
    PyPSA statistics and in the objective breakdown plot without custom
    objective edits.

Visualization Rules
~~~~~~~~~~~~~~~~~~~

**Plots and maps** (see ``workflow/rules/plotting.smk``):
  * ``plot_regions_map``: Optimization region boundaries
  * ``plot_resource_classes_map``: Resource class spatial distribution
  * ``plot_crop_production_map``: Land use intensity gridcell map
  * ``plot_water_value_map``: Water shadow prices (economic value)
  * ``plot_health_impacts``: Health risk and baseline maps
  * ``plot_results``: Production, resource usage, objective breakdown
  * ``plot_food_consumption``: Dietary composition
  * ``plot_crop_use_breakdown``: How crops are used (food vs. feed vs. waste)

Execution Commands
------------------

Running the Full Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~

Build, solve, and visualize everything::

    tools/smk -j4 --configfile config/my_scenario.yaml all

* ``-j4``: Use 4 parallel cores (adjust to your CPU count)
* ``--configfile config/my_scenario.yaml``: Specify which configuration file to use
* ``all``: Target rule that depends on all major outputs (strictly speaking optional)

This will:

1. Download datasets (if not already cached)
2. Process data for configured scenario
3. Build and solve the model
4. Generate all plots and exports

Running Specific Stages
~~~~~~~~~~~~~~~~~~~~~~~~

**Build model only** (no solving)::

    tools/smk -j4 --configfile config/my_scenario.yaml -- results/my_scenario/build/model_scen-default.nc

**Solve model**::

    tools/smk -j4 --configfile config/my_scenario.yaml -- results/my_scenario/solved/model_scen-default.nc

**Solve all scenarios (no plots)**::

    tools/smk -j4 --configfile config/my_scenario.yaml solve_all_scenarios

**Regenerate specific plot**::

    tools/smk --configfile config/my_scenario.yaml -- results/my_scenario/plots/scen-default/crop_production.pdf

**Prepare data without building model**::

    tools/smk -j4 --configfile config/my_scenario.yaml -- processing/my_scenario/regions.geojson processing/my_scenario/resource_classes.nc

For any of the above targets, Snakemake will first run any other previous rules in order to generate the necessary inputs for the specified target output/rule.

Checking Workflow Status
~~~~~~~~~~~~~~~~~~~~~~~~~

**Dry-run** (show what would be executed without running)::

    tools/smk --configfile config/my_scenario.yaml -n

**Dependency graph**: See the workflow dependency graph figure at the top of this page. To generate a detailed job-level DAG for a specific configuration::

    tools/smk --dag all | dot -Tpdf > dag.pdf

**List all rules**::

    tools/smk --list

Memory Management
-----------------

The ``tools/smk`` Wrapper
~~~~~~~~~~~~~~~~~~~~~~~~~

It is possible to run the workflow directly with the ``snakemake`` command. Food-opt, however, provides a simple shell script, ``tools/smk``, which:

1. Runs Snakemake in a systemd cgroup with hard memory limit (default 10 GB), killing the process group if memory limit is exceeded
2. Disables swap to prevent system instability
3. Sets the ``-j1`` argument (running only one job at a time) by default unless the user sets the ``-j<n>`` option explicitly.

**Default memory limit**: 10 GB (configurable via ``SMK_MEM_MAX`` environment variable)

**Override memory limit**::

    SMK_MEM_MAX=12G tools/smk -j4 all

Parallelization
---------------

Snakemake automatically runs rules concurrently (e.g., downloading multiple GAEZ files, processing yields for different crops), depending on the configured number of parallel rules allowed. This is set with the ``-j<n>`` option, where ``n`` is the number of parallel rules. Note that individual rules (such as the model solving rule) may use more than one processor core. 

Snakemake automatically detects dependencies and runs tasks in correct order.

Incremental Development
-----------------------

**Workflow philosophy**: Snakemake tracks file modification times and only reruns rules whose inputs changed. This includes rule input files, the script associated with the rule as well as rule parameters (relevant configuration sections).

**Example workflow**:

1. Run full workflow: ``tools/smk -j4 all``
2. Modify crop list in config → only crop yield rules rerun
3. Modify solver options → only ``solve_model`` reruns (build model reused)
4. Modify visualization script → only plotting rules rerun

**Rerun specific rule**::

    tools/smk -j4 --configfile config/my_scenario.yaml --forcerun solve_model -- results/my_scenario/solved/model_scen-default.nc

**Mark all existing outputs as up to date** (preventing rules from being run due to modification times, etc.)::

    tools/smk --configfile config/my_scenario.yaml --touch
