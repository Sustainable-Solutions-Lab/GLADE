.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Land Use & Resource Classes
============================

Overview
--------

The land use module translates high-resolution gridded yield potentials (0.05° × 0.05°, approximately 5.6 km at the equator) into aggregated land availability and yield parameters for the optimization model. This aggregation serves two purposes:

1. **Computational efficiency**: Reduces millions of gridcells to hundreds of optimization regions
2. **Yield heterogeneity**: Captures within-region variation in land quality using resource classes

Spatial Aggregation
-------------------

Regional Clustering
~~~~~~~~~~~~~~~~~~~

The model operates at sub-national regional resolution, defined by clustering administrative units (GADM level 1) based on spatial proximity. The clustering process (``workflow/scripts/build_regions.py``) follows these steps:

1. **Simplification**: Simplify GADM geometries to reduce complexity while preserving boundaries

   * Controlled by ``aggregation.simplify_tolerance_km`` and ``aggregation.simplify_min_area_km``
   * Small enclaves are removed to avoid fragmentation

2. **Country selection**: Filter to configured countries (``countries`` list in config)

3. **Clustering**: Aggregate administrative units into target number of regions

   * Method: k-means clustering on region centroids (default)
   * Target count: ``aggregation.regions.target_count`` (e.g., 400)
   * Cross-border clustering: Controlled by ``aggregation.regions.allow_cross_border`` (typically ``false``)

4. **Output**: GeoJSON with region polygons saved to ``processing/{name}/regions.geojson``

The result is a set of contiguous optimization regions that respect national boundaries (unless cross-border clustering is enabled) and balance spatial detail with computational tractability.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/intro_global_coverage.png
   :width: 100%
   :alt: Global model coverage map

   Global model coverage showing optimization regions created by clustering administrative units.

Resource Classes
----------------

Concept
~~~~~~~

Within each optimization region, agricultural land is heterogeneous—some areas have high yield potential, others low. To capture this heterogeneity without creating a separate decision variable for each gridcell, the model groups land into **resource classes** based on yield potential quantiles.

For example, with quantiles ``[0.25, 0.5, 0.75]``, each region has 4 resource classes:

* **Class 0**: Bottom 25% of yield potential (lowest quality land)
* **Class 1**: 25th-50th percentile
* **Class 2**: 50th-75th percentile
* **Class 3**: Top 25% of yield potential (highest quality land)

This allows the model to:

* Preferentially allocate high-value crops to high-quality land
* Avoid optimistic bias from averaging yields across heterogeneous land
* Capture marginal land-use decisions

Computation
~~~~~~~~~~~

Resource classes are computed by ``workflow/scripts/compute_resource_classes.py``:

1. **Load yield rasters**: Read GAEZ yield potentials for all crops (both rainfed and irrigated)

2. **Running maximum**: For each gridcell, compute the maximum attainable yield across all crops and water supplies

   * Converts kg/ha → t/ha (tonnes per hectare)
   * This represents the "best possible use" of each piece of land

3. **Regional quantiles**: Within each optimization region, compute yield quantiles

   * Uses ``aggregation.resource_class_quantiles`` from config (e.g., ``[0.25, 0.5, 0.75]``)
   * Only considers gridcells with positive yield potential (deserts/ice don't collapse bins)

4. **Class assignment**: Assign each gridcell to a resource class based on which quantile bin it falls into

5. **Output**: NetCDF file (``processing/{name}/resource_classes.nc``) with:

   * ``resource_class``: Integer class ID for each gridcell
   * ``max_yield``: Maximum yield potential across all crops
   * Coordinate reference system and geotransform for spatial reference

Visual Example
~~~~~~~~~~~~~~

The figure below shows resource class stratification for a region in the western United States. Each colored pixel represents agricultural land classified by its yield potential, with darker blue indicating higher productivity (class 2) and lighter green-blue indicating lower productivity (class 0). The red boundary delineates the optimization region.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/land_resource_classes.png
   :width: 50%
   :alt: Resource class distribution map showing yield potential categories

   Resource class stratification within an example region. The spatial pattern reveals how land quality varies across the landscape, allowing the optimization model to preferentially allocate high-value crops to the most productive land while still utilizing lower-quality land for appropriate crops.

Numerical Example
~~~~~~~~~~~~~~~~~

For a region with yield distribution [0.1, 0.5, 1.2, 2.0, 3.5, 5.0, 8.0] t/ha stratified using quantiles [0.33, 0.67]:

* Class 0: [0.1, 0.5, 1.2] t/ha (bottom third)
* Class 1: [2.0, 3.5] t/ha (middle third)
* Class 2: [5.0, 8.0] t/ha (top third)

Land Area Aggregation
---------------------

Once resource classes are defined, the next step is to compute how much land is available in each region-class-water combination. This is handled by ``workflow/scripts/aggregate_class_areas.py``.

Suitability-Based Limits
~~~~~~~~~~~~~~~~~~~~~~~~~

Land availability is constrained by **suitability** from GAEZ, which indicates what fraction of each gridcell is suitable for agriculture. GAEZ provides separate suitability rasters for rainfed and irrigated production (SX1 variable: share of cell assessed as "very suitable" or "suitable").

The aggregation process:

1. **Load resource classes**: Read the class assignment raster from previous step

2. **Load suitability**: Read GAEZ suitability rasters for each crop and water supply

3. **Compute suitable area**: For each gridcell, multiply:

   * Cell area (in hectares, accounting for Earth's curvature)
   * Suitability fraction (0-1)
   * Irrigated share (for irrigated crops) or ``1 - irrigated_share`` (for rainfed)

4. **Aggregate by region-class**: Sum suitable area across all gridcells in each (region, resource_class, water_supply, crop) combination

5. **Output**: CSV file (``processing/{name}/land_area_by_class.csv``) with columns:

   * ``region``: Optimization region ID
   * ``resource_class``: Class number (0, 1, 2, ...)
   * ``water_supply``: "r" (rainfed) or "i" (irrigated)
   * ``crop``: Crop name
   * ``area_ha``: Available area in hectares

Irrigated Area Source
~~~~~~~~~~~~~~~~~~~~~

The configuration parameter ``aggregation.irrigated_area_source`` controls how irrigated land availability is determined:

* **"current"** (default): Use GAEZ "land equipped for irrigation" dataset

  * Uniform: all crops share the same irrigated area
  * Represents land that is currently equipped for irrigation

* **"potential"**: Use GAEZ irrigated suitability rasters per crop

  * Crop-specific: different crops have different potential irrigated area
  * More conservative: only counts land GAEZ deems suitable for irrigated production of each crop

The "current" option provides a simpler, uniform land base across all crops, while "potential" is more realistic but creates crop-specific heterogeneity in irrigated land availability.

Grazing-only Land
-----------------

Not all grassland can or should compete with cropland. The ``build_grazing_only_land`` rule derives a second land dataset (``processing/{name}/land_grazing_only_by_class.csv``) by combining the ESA CCI land-cover fractions with the GAEZ rainfed suitability maps. For each cell we subtract the cropland-suitable share already accounted for via GAEZ and the current cropland/forest cover reported by ESA; only the residual grassland that lies outside the cropland suitability envelope is aggregated to regions/resource classes. ``build_model`` turns these hectares into ``land_marginal_{region}_class{n}`` buses and adds mirrored ``grassland_marginal_*`` grazing links so ruminant feed can expand on marginal pasture without depleting the cropland land pool.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/grazing_only_land_fraction.png
   :width: 100%
   :alt: Fraction of each gridcell and region classified as grazing-only land

   Global grazing-only land availability. Left panel: fraction of each gridcell that is currently grassland yet unsuitable for any crop, so it can host grazing without competing for cropland. Right panel: share of each optimization region's modeled land budget that falls into this grazing-only pool.

These marginal ``land_marginal_*`` buses feed both grazing links and the ``spared_land`` carrier created by ``workflow.scripts.build_model.add_spared_land_links``. When the optimisation releases marginal pasture, those hectares can be routed through ``spare_marginal_*`` links to earn regrowth credits without competing with cropland-suitable land, ensuring that every hectare is accounted for exactly once.

Irrigated vs. Rainfed Land
---------------------------

The model distinguishes between irrigated and rainfed production:

**Rainfed**
  * Uses rainfall for water supply
  * Available on all suitable cropland not equipped for irrigation
  * Generally lower yields than irrigated

**Irrigated**
  * Uses irrigation infrastructure
  * Only available on land equipped for irrigation (from GAEZ dataset)
  * Higher yields but requires blue water (see :doc:`crop_production` for water constraints)
  * Controlled by ``irrigation.irrigated_crops`` config (can be "all" or a list)

For each optimization region and resource class, the model maintains separate land variables for rainfed and irrigated production. These compete for the same physical land, so the model includes constraints ensuring that total rainfed + irrigated land in a class doesn't exceed available area.

Land Use Change
---------------

When the model allocates more land to agriculture than is currently used, this represents **land use change** (LUC). The environmental impacts of LUC (carbon emissions from clearing vegetation) are captured in the objective function.

The model does not currently distinguish between:

* Expansion vs. intensification
* Different LUC types (forest → cropland vs. grassland → cropland)
* Historical vs. potential land

These are areas for future refinement. See :doc:`environment` for how LUC emissions are calculated.

Configuration Parameters
------------------------

Key configuration parameters for land use aggregation (in ``config/default.yaml``):

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: aggregation ---
   :end-before: # --- section: countries ---

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: land ---
   :end-before: # --- section: fertilizer ---

The ``regional_limit`` parameter applies a global constraint on how much of each region's suitable land can be used (0.7 = 70%). This represents institutional, ecological, or social constraints on agricultural expansion.

Validation runs that pin observed harvested area (``validation.use_actual_production: true``)
may still encounter land-class mismatches. To keep those runs feasible without globally
loosening land limits, each land bus receives a dedicated ``land_slack`` generator whose
marginal cost is set by ``land.slack_marginal_cost`` (units: USD per Mha). Set this
value high enough that slack activates only as a last resort; the default 5e9 corresponds to
roughly 5 000 USD/ha.

.. note::

   GAEZ's RES06-HAR rasters store harvested area directly (in thousand hectares per
   gridcell) and some RES06 crop codes encompass multiple FAOSTAT commodities. For
   example, ``citrus`` and ``mango`` both map to the FRT module, so validation runs that
   fix harvested area for ``citrus`` will effectively pin the combined citrus+mango
   footprint. This means enforced production can exceed FAOSTAT totals until we split
   those shared rasters using external shares.

Workflow Rules
--------------

The land use aggregation workflow consists of three Snakemake rules:

1. **simplify_gadm**: Simplify administrative boundaries

   * Input: ``data/downloads/gadm.gpkg``
   * Output: ``processing/shared/gadm-simplified.gpkg``
   * Script: ``workflow/scripts/simplify_gadm.py``

2. **build_regions**: Cluster into optimization regions

   * Input: Simplified GADM
   * Output: ``processing/{name}/regions.geojson``
   * Script: ``workflow/scripts/build_regions.py``

3. **compute_resource_classes**: Define resource classes

   * Input: Regions + all GAEZ yield rasters
   * Output: ``processing/{name}/resource_classes.nc``
   * Script: ``workflow/scripts/compute_resource_classes.py``

4. **aggregate_class_areas**: Compute land availability

   * Input: Resource classes + suitability rasters + regions
   * Output: ``processing/{name}/land_area_by_class.csv``
   * Script: ``workflow/scripts/aggregate_class_areas.py``

Visualization
-------------

Regional aggregation can be visualized using the regions map plotting rule::

    tools/smk results/{name}/plots/regions_map.pdf

Resource class distribution can be visualized using::

    tools/smk results/{name}/plots/resource_classes_map.pdf

These maps show the spatial distribution of optimization regions and the quality stratification of land within each region.
