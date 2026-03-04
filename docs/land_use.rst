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
- **Current cropland-suitable grassland**: Existing grassland on land suitable for crops (GAEZ)
- **Current marginal grassland**: Existing grazing-only grassland that is not suitable for crops

Spared Land
~~~~~~~~~~~

Both existing cropland and both current grassland pools can be **spared**—taken out of production to earn carbon sequestration credits from vegetation regrowth:

- ``spare_land`` links: Spare existing cropland (purple dashed arrows)
- ``spare_existing_grassland`` links: Spare existing grassland

This enables the model to evaluate trade-offs between agricultural production and carbon sequestration. See :ref:`luc-emissions` for how land-use-change emissions are calculated, and :ref:`luc-spared-land-filtering` for details on sequestration credit eligibility.

Validation Controls
~~~~~~~~~~~~~~~~~~~

For validation scenarios, new land supply can be disabled independently:

- ``validation.disable_new_cropland``: Prevents new land from supplying cropland pools
- ``validation.disable_new_pasture``: Prevents new land from supplying pasture pools
- ``validation.disable_spared_cropland``: Prevents baseline cropland from being spared
- ``validation.disable_spared_grassland``: Prevents existing grassland pools from being spared

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

Current Grassland: Convertible vs Marginal
-------------------------------------------

The model splits current grassland into two explicit pools within each ``(region, resource_class)``:

- **Cropland-suitable current grassland**: Current grassland area that lies on land suitable for crop growth (GAEZ suitable)
- **Marginal current grassland**: Current grazing-only grassland that is not suitable for crop growth

Data sources and split:

- ``build_current_grassland_area`` provides total current grassland from ESA CCI land-cover fractions
- ``build_grazing_only_land`` estimates the marginal (grazing-only) subset
- Cropland-suitable current grassland is computed as ``max(current_grassland - grazing_only, 0)``

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/grazing_only_land_fraction.png
   :width: 100%
   :alt: Fraction of each gridcell and region classified as grassland

   Global grassland availability. Left panel: gridcell fraction that is grassland. Right panel: share of each region's land budget in the grassland pool.

Both current grassland pools flow to the **pasture pool** via ``existing_grassland_to_pasture`` links. Both can also be spared for carbon sequestration via ``spare_existing_grassland`` links.

Only the **cropland-suitable current grassland** pool is subtracted from the rainfed new-land expansion potential, preventing double-counting while preserving the distinct marginal grassland pool.

This separation ensures that:

- Ruminant feed has a realistic existing grassland supply without depleting cropland
- Every hectare is accounted for exactly once
- Sparing decisions reflect the actual opportunity cost of each land type

Land Use Change
---------------

When the model allocates more land to agriculture than the existing baseline, this represents **land use change** (LUC). The environmental impacts—primarily carbon emissions from clearing vegetation—are captured via efficiency coefficients on the conversion links.

See :doc:`environment` for details on how LUC emissions are calculated from above-ground biomass, soil organic carbon, and foregone regrowth potential.

Land Conversion Costs
~~~~~~~~~~~~~~~~~~~~~

Expanding agriculture onto new land incurs physical investment costs for clearing vegetation, stumping, grading, and initial soil preparation. These costs are applied as ``marginal_cost`` on ``land_conversion`` (cropland expansion) and ``new_to_pasture`` (pasture expansion) links, differentiated by cover type:

- **Forest → agriculture**: ``conversion_cost_forest_usd_per_ha`` (default: 8,000 USD/ha) — covers clearing dense vegetation, stumping, grading, and soil preparation.
- **Non-forest → agriculture**: ``conversion_cost_nonforest_usd_per_ha`` (default: 2,000 USD/ha) — lighter clearing of brush and grassland with soil preparation.

These values are in the model's currency base year (2024 USD).

Since these are one-time investment costs, they are annualized using a **capital recovery factor** (CRF):

.. math::

   \text{CRF} = \frac{r}{1 - (1 + r)^{-n}}

where *r* is the discount rate (``discount_rate``, default 0.05) and *n* is the investment horizon (``investment_horizon``, default 25 years, matching the LUC emissions horizon). The annualized cost is then converted to model units (bnUSD/Mha) and applied to the conversion links.

**Sources and rationale for default cost values.**
Direct estimates of the full private cost of converting land to crop-ready agriculture are sparse in the academic literature, which tends to focus on opportunity costs and returns from conversion rather than upfront clearing expenditure. The default values are chosen as middle-of-the-road estimates based on the following sources (original values inflation-adjusted to approximate 2024 USD where needed):

*Forest clearing:*

- The FAO Soils Bulletin 19 (FAO, 1979) reports that manual clearing of tropical high forest requires ~86 man-days/ha, while previously logged forest requires ~50 man-days/ha. At modern developing-country wages, this implies $430--1,300/ha for labor alone, before stumping, grading, and soil preparation.
- Margulis (2004), in World Bank Working Paper No. 22, reports ~$500/ha (~$830/ha in 2024 USD) for basic slash-and-burn clearing in the Brazilian Amazon — the cheapest method, representing a lower bound.
- An IUCN workshop on land clearing economics (IUCN, 2002) found that mechanized clearing of secondary forest in Indonesia costs $600--1,800/ha (~$1,050--3,150/ha in 2024 USD), while fire-based clearing costs $200--595/ha.
- Nhiuane et al. (2024), in *Trees, Forests and People*, report clearing costs of $302--508/ha for slash-and-burn and $1,662/ha for conventional logging-based clearing in Mozambique.
- Oil palm plantation development in Southeast Asia, which includes clearing, stumping, terracing, and establishment, costs $3,000--8,000/ha (Sumarga & Hein, 2014; industry reports).

The default of 8,000 USD/ha represents the full cost of bringing forested land to a crop-ready state at commercial scale, including clearing, stumping, root removal, grading, soil preparation, and basic access infrastructure. This is above the cost of tree-felling alone, but below the total cost of plantation establishment which includes planting and multi-year maintenance.

*Non-forest clearing:*

- The FAO Soils Bulletin 19 (FAO, 1979) notes that jungle clearing costs up to 120 times as much as light brush clearing.
- The IUCN (2002) workshop describes alang-alang grassland clearing costs as "substantially lower" than secondary forest, implying a ratio of roughly 3:1 for the same clearing method.
- Nhiuane et al. (2024) report $302--508/ha for slash-and-burn clearing of savanna woodland in Mozambique.

The default of 2,000 USD/ha covers mechanized clearing of brush and grassland plus soil preparation, consistent with a 4:1 forest-to-non-forest ratio that falls within the range supported by the FAO and IUCN data.

**References:**

- FAO (1979). *Land Clearing and Development*. FAO Soils Bulletin 19, Chapters II--III. Rome: FAO. [`Manual methods <https://www.fao.org/4/ad083e/AD083e03.htm>`__] [`Mechanized methods <https://www.fao.org/4/ad083e/AD083e04.htm>`__]
- Margulis, S. (2004). *Causes of Deforestation of the Brazilian Amazon*. World Bank Working Paper No. 22. Washington, DC: World Bank. [`PDF <https://documents1.worldbank.org/curated/en/758171468768828889/pdf/277150PAPER0wbwp0no1022.pdf>`__]
- IUCN (2002). *Workshop Report: Land Clearing on Degraded Lands for Plantation Development*. IUCN Fire & Forest Programme. [`PDF <https://iucn.org/sites/default/files/import/downloads/ff_workshop_economics.pdf>`__]
- Nhiuane, O., Lisboa, S. N., Popat, M. & Sitoe, A. (2024). Quantifying the costs and benefits of forest conversion through slash-and-burn cultivation and conventional logging. *Trees, Forests and People*, 15, 100504. `doi:10.1016/j.tfp.2024.100504 <https://doi.org/10.1016/j.tfp.2024.100504>`__
- Sumarga, E. & Hein, L. (2014). Mapping ecosystem services for land use planning, the case of Central Kalimantan. *Environmental Management*, 54(1), 84--97. `doi:10.1007/s00267-014-0282-2 <https://doi.org/10.1007/s00267-014-0282-2>`__

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
