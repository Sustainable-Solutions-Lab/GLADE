.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Land Use & Resource Classes
============================

Overview
--------

The land use module manages agricultural land allocation across the global food system. It translates high-resolution gridded data (0.05° × 0.05°, approximately 5.6 km at the equator) into optimization variables that balance:

- **Cropland** for producing grains, oilseeds, fruits, and other crops
- **Pasture** for grassland-based livestock feed
- **Spared land** that can be taken out of production for carbon sequestration

The module uses a dual-pool structure that separates cropland and pasture, enabling independent control over land allocation and expansion for each use type.

Land Pool Structure
-------------------

The model uses separate **land pools** for cropland and pasture, with distinct supply pathways and the option to spare land for carbon sequestration:

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/land_flows.png
   :width: 100%
   :alt: Land flow diagram showing cropland and pasture pool structure

   Land flow structure showing supply paths to cropland and pasture pools, with sparing options for carbon sequestration.

Cropland Pool
~~~~~~~~~~~~~

**Bus pattern:** ``land:cropland:{region}_c{class}_{water}``

The cropland pool serves all crop production. Each pool is specific to a region, resource class, and water supply (rainfed or irrigated). Supply comes from:

- **Existing cropland baseline** (green arrows): Land currently in agricultural use, available without land-use-change emissions
- **New land conversion** (yellow arrows): Expansion onto previously non-agricultural land, incurring LUC emissions

Pasture Pool
~~~~~~~~~~~~

**Bus pattern:** ``land:pasture:{region}_c{class}``

The pasture pool serves grassland production for ruminant feed. Pasture pools are water-agnostic (no irrigated/rainfed distinction). Supply comes from:

- **Existing cropland**: Baseline agricultural land diverted to pasture use
- **New land conversion**: Expansion with LUC emissions
- **Marginal grazing land**: Land suitable only for grazing, not crop production

Spared Land
~~~~~~~~~~~

Both existing cropland and marginal grazing land can be **spared**—taken out of production to earn carbon sequestration credits from vegetation regrowth:

- ``spare_land`` links: Spare existing cropland (purple dashed arrows)
- ``spare_marginal`` links: Spare marginal grazing land

This enables the model to evaluate trade-offs between agricultural production and carbon sequestration. See :ref:`luc-emissions` for how land-use-change emissions are calculated, and :ref:`luc-spared-land-filtering` for details on sequestration credit eligibility.

Validation Controls
~~~~~~~~~~~~~~~~~~~

For validation scenarios, new land supply can be disabled independently:

- ``validation.disable_new_cropland``: Prevents new land from supplying cropland pools
- ``validation.disable_new_pasture``: Prevents new land from supplying pasture pools

This allows validating against historical production patterns without spurious land-use-change from the optimizer reallocating land.

Spatial Resolution
------------------

The model operates at sub-national regional resolution, balancing spatial detail with computational tractability.

Regional Clustering
~~~~~~~~~~~~~~~~~~~

Optimization regions are created by clustering administrative units (GADM level 1) based on spatial proximity:

1. **Simplification**: Simplify GADM geometries to reduce complexity while preserving boundaries
2. **Country selection**: Filter to configured countries (``countries`` list in config)
3. **Clustering**: Aggregate administrative units using k-means clustering on centroids
4. **Output**: GeoJSON with region polygons (``processing/{name}/regions.geojson``)

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/intro_global_coverage.png
   :width: 100%
   :alt: Global model coverage map

   Global model coverage showing optimization regions created by clustering administrative units.

Key configuration parameters:

- ``aggregation.regions.target_count``: Number of regions to create (e.g., 400)
- ``aggregation.regions.allow_cross_border``: Whether regions can span country boundaries (typically ``false``)
- ``aggregation.simplify_tolerance_km``: Geometry simplification tolerance

Resource Classes
----------------

Within each optimization region, agricultural land is heterogeneous—some areas have high yield potential, others low. To capture this heterogeneity without creating a separate decision variable for each gridcell, the model groups land into **resource classes** based on yield potential quantiles.

Concept
~~~~~~~

For example, with quantiles ``[0.25, 0.5, 0.75]``, each region has 4 resource classes:

- **Class 0**: Bottom 25% of yield potential (lowest quality land)
- **Class 1**: 25th–50th percentile
- **Class 2**: 50th–75th percentile
- **Class 3**: Top 25% of yield potential (highest quality land)

This stratification allows the model to:

- Preferentially allocate high-value crops to high-quality land
- Avoid optimistic bias from averaging yields across heterogeneous land
- Capture marginal land-use decisions at the extensive margin

Resource classes are computed from GAEZ yield potentials by taking the maximum attainable yield across all crops for each gridcell, then binning into quantiles within each region.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/land_resource_classes.png
   :width: 50%
   :alt: Resource class distribution map showing yield potential categories

   Resource class stratification within an example region. Darker colors indicate higher productivity classes. The spatial pattern reveals how land quality varies across the landscape.

Land Availability
-----------------

Land availability is constrained by **suitability** from GAEZ, which indicates what fraction of each gridcell is suitable for agriculture.

Suitability-Based Limits
~~~~~~~~~~~~~~~~~~~~~~~~

GAEZ provides separate suitability rasters for rainfed and irrigated production. The aggregation process:

1. Load suitability fractions (0–1) for each crop and water supply
2. Multiply by cell area (hectares) to get suitable area
3. Aggregate by region, resource class, and water supply

The ``regional_limit`` parameter (e.g., 0.7) caps total usable land at a fraction of the suitable area, representing institutional, ecological, or social constraints on agricultural expansion.

Irrigated vs. Rainfed
~~~~~~~~~~~~~~~~~~~~~

The model distinguishes between irrigated and rainfed production:

**Rainfed**
  - Uses rainfall for water supply
  - Available on suitable cropland not equipped for irrigation
  - Generally lower yields than irrigated

**Irrigated**
  - Requires irrigation infrastructure
  - Only available on land equipped for irrigation (from GAEZ dataset)
  - Higher yields but consumes blue water (see :doc:`crop_production` for water constraints)

For each region and resource class, the model maintains separate land variables for rainfed and irrigated production.

Marginal Grazing Land
---------------------

Not all grassland competes with cropland. **Marginal grazing land** is land that:

- Is currently grassland (from ESA CCI land cover)
- Is unsuitable for any crop production (below GAEZ suitability thresholds)
- Can support grazing without competing for cropland

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/grazing_only_land_fraction.png
   :width: 100%
   :alt: Fraction of each gridcell and region classified as grazing-only land

   Global grazing-only land availability. Left panel: gridcell fraction that is grassland yet unsuitable for crops. Right panel: share of each region's land budget in the grazing-only pool.

Marginal land flows exclusively to the **pasture pool** via ``marginal_to_pasture`` links. It can also be spared for carbon sequestration via ``spare_marginal`` links, earning regrowth credits without affecting the cropland budget.

This separation ensures that:

- Ruminant feed can expand on marginal pasture without depleting cropland
- Every hectare is accounted for exactly once
- Sparing decisions reflect the actual opportunity cost of each land type

Land Use Change
---------------

When the model allocates more land to agriculture than the existing baseline, this represents **land use change** (LUC). The environmental impacts—primarily carbon emissions from clearing vegetation—are captured via efficiency coefficients on the conversion links.

See :doc:`environment` for details on how LUC emissions are calculated from above-ground biomass, soil organic carbon, and foregone regrowth potential.

Configuration Reference
-----------------------

Key configuration parameters for land use (in ``config/default.yaml``):

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: aggregation ---
   :end-before: # --- section: countries ---

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: land ---
   :end-before: # --- section: fertilizer ---

Land Slack
~~~~~~~~~~

Validation runs that pin observed harvested area may encounter land-class mismatches. To maintain feasibility without globally loosening land limits, each land bus can receive a ``land_slack`` generator:

- Controlled by ``validation.land_slack: true``
- Marginal cost set by ``land.slack_marginal_cost`` (USD per Mha)
- Default ~5000 USD/ha ensures slack activates only as a last resort
