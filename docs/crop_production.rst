.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Crop Production
===============

Overview
--------

The crop production module translates GAEZ yield potentials and land availability into production constraints for the optimization model. Each crop can be grown in multiple regions, on different resource classes, and potentially with either rainfed or irrigated water supply.

Crop Coverage
-------------

The default configuration includes over 60 crops spanning major food categories:

**Cereals**
  * Wheat, dryland rice, wetland rice, maize
  * Barley, oat, rye, sorghum
  * Buckwheat, foxtail millet, pearl millet

**Legumes and Pulses**
  * Soybean, dry pea, chickpea
  * Cowpea, gram, phaseolus bean, pigeonpea

**Roots and Tubers**
  * White potato, sweet potato, cassava, yam

**Vegetables**
  * Tomato, carrot, onion, cabbage

**Fruits**
  * Banana, citrus, coconut

**Oil Crops**
  * Sunflower, rapeseed, groundnut
  * Sesame, oil palm, olive

**Sugar Crops**
  * Sugarcane, sugarbeet

**Fodder Crops**
  * Alfalfa, biomass sorghum

The complete crop list is configured in ``config/default.yaml`` under the ``crops`` key.

.. Note:: Managed grassland is also modelled, but yields derived from the LPJmL mode; see :ref:`grassland-yields`

GAEZ Yield Data
---------------

Yield potentials come from the FAO/IIASA Global Agro-Ecological Zones (GAEZ) v5 dataset, which provides spatially-explicit crop suitability and attainable yields under various scenarios. The GAEZ documentation can be found `here <https://github.com/un-fao/gaezv5/wiki>`_. `Module II <https://github.com/un-fao/gaezv5/wiki/04.-Module-II-(Biomass-and-yield-calculation)#biomass-and-yield-calculation>`_ gives more details on biomass and yield calculations (including links to appendices with detailed calculations and parameter choices); subsequent modules apply climatic and technical constraints to arrive at potential yields in `Module V <https://github.com/un-fao/gaezv5/wiki/07.-Module-V-(Integration-of-climatic-and-edaphic-evaluation)>`_.

All RES05 yield rasters used here are provided on a 0.083333° (~5 arc-minute, ≈9 km at the equator) latitude–longitude grid, which sets the native spatial resolution before aggregation to optimization regions.

GAEZ Configuration
~~~~~~~~~~~~~~~~~~

Key GAEZ parameters in ``config/default.yaml``:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: data ---
   :end-before: # --- section: irrigation ---

**Climate Models**: Individual global circulation models (GCMs): GFDL-ESM4, IPSL-CM6A-LR, MPI-ESM1-2-HR, MRI-ESM2-0, UKESM1-0-LL; or multi-model ENSEMBLE

**Periods**:
  * Historical: HP8100 (1981-2000), HP0120 (2001-2020)
  * Future: FP2140 (2021-2040), FP4160 (2041-2060), FP6180 (2061-2080), FP8100 (2081-2100)

**Scenarios**: SSP126 (low emissions), SSP370 (medium), SSP585 (high), HIST (historical)

**Input Levels**:
  * "H" (high): Modern agricultural inputs (fertilizer, irrigation, pest management)
  * "L" (low): Subsistence farming practices

GAEZ Variables
~~~~~~~~~~~~~~

The model uses several GAEZ raster products for each crop:

* **YCX** (RES05): Attainable yield on current cropland (kg/ha or other units)
* **SX1** (RES05): Suitability index (fraction of gridcell suitable for cultivation)
* **WDC** (RES05): Net irrigation water requirement during crop cycle (mm)
* **Growing season start** (RES02): Julian day when growing season begins
* **Growing season length** (RES02): Number of days in growing cycle

.. Note:: RES05 (yields/suitability) supports ENSEMBLE, but RES02 (growing season) only has individual GCM outputs.

The following figures show yield potential maps for three major crops, illustrating the spatial variation in productivity that drives the optimization:

.. figure:: _static/figures/crop_yield_wheat.png
   :width: 100%
   :alt: Wheat yield potential map

   Wheat rainfed yield potential (tonnes/hectare) from GAEZ v5. Higher yields are shown in darker green. Black lines indicate region boundaries. Wheat performs best in temperate zones with adequate rainfall.

.. figure:: _static/figures/crop_yield_wetland-rice.png
   :width: 100%
   :alt: Rice yield potential map

   Wetland rice rainfed yield potential (tonnes/hectare) from GAEZ v5. Rice shows high productivity in tropical and subtropical regions with suitable water availability, particularly in Asia.

.. figure:: _static/figures/crop_yield_maize.png
   :width: 100%
   :alt: Maize yield potential map

   Maize rainfed yield potential (tonnes/hectare) from GAEZ v5. Maize is adaptable across diverse climates, with strong yields in the Americas, parts of Africa, and temperate zones.

Yield Aggregation
-----------------

Yields are aggregated from the input resolution gridcells to (region, resource_class, water_supply) combinations by ``workflow/scripts/build_crop_yields.py``.

Aggregation Process
~~~~~~~~~~~~~~~~~~~

1. **Load resource classes**: Read the class assignment raster (see :doc:`land_use`)

2. **Load crop-specific rasters**:

   * Yield potential (kg/ha, converted to t/ha)
   * Suitability fraction (0-1)
   * Water requirement (mm, converted to m³/ha)
   * Growing season timing (start day, length)

3. **Unit conversions**: Apply crop-specific conversion factors

   * Default: 0.001 (kg/ha → t/ha)
   * Custom factors in ``data/yield_unit_conversions.csv`` (e.g., sugarcane in GE/ha)

4. **Mask by suitability**: Only aggregate over suitable land (SX1 > 0)

5. **Compute class averages**: Within each (region, resource_class) combination:

   * Mean yield (t/ha) weighted by suitable area
   * Mean water requirement (m³/ha)
   * Modal growing season start and length

6. **Output**: CSV file (``processing/{name}/crop_yields/{crop}_{water_supply}.csv``) with tidy columns:

   * ``region`` – Optimization region ID
   * ``resource_class`` – Class number
   * ``variable`` – One of ``yield``, ``suitable_area``, ``water_requirement_m3_per_ha``, ``growing_season_start_day``, ``growing_season_length_days``
   * ``unit`` – Physical unit for the variable (``t/ha``, ``ha``, ``m³/ha``, ``day-of-year``, ``days``)
   * ``value`` – Numeric value for the (region, class, variable) triplet

Resource Class Yields
~~~~~~~~~~~~~~~~~~~~~

Because resource classes are defined by yield quantiles (see :doc:`land_use`), yields generally increase with class number. For example, in a particular region with quantiles [0.25, 0.5, 0.75], we might see the following average yields by resource class:

* Class 0: 1.5 t/ha (bottom quartile land)
* Class 1: 2.8 t/ha (second quartile)
* Class 2: 4.2 t/ha (third quartile)
* Class 3: 6.5 t/ha (top quartile)

This allows the optimizer to preferentially allocate crops to high-quality land or expand onto marginal land as needed.

The following figure illustrates this variation, comparing rainfed wheat yields between resource classes 1 and 2 across all regions:

.. figure:: _static/figures/crop_yield_resource_class_wheat.png
   :width: 100%
   :alt: Wheat yields by resource class

   Comparison of wheat rainfed yields (tonnes/hectare) between resource class 1 (left) and resource class 2 (right). Resource class 2 represents higher-quality land and generally shows higher yields across most regions, demonstrating how the resource class stratification captures land quality variation.

.. Note:: Yields for individual crops need not always be better in a high resource class. This is because resource classes are determined "globally" for all crops at once, so that each grid cell is assigned a resource class independent of any crop. So while resource class 2 has better *average* yields than resource class 1 in every region, that might not be true for some individual crops (e.g. rainfed wheat in the Western USA region in the above example.)

Production Constraints
----------------------

In the PyPSA model (``workflow/scripts/build_model.py``), crop production is represented as multi-bus links:

**Inputs**:
  * Land (from land bus for the region/class/water combination)
  * Water (for irrigated crops only)
  * Fertilizer (for all crops, with configurable N-P-K requirements)

**Outputs**:
  * Crop product (to crop bus)
  * Emissions (CO₂, CH₄, N₂O)

**Efficiency Parameters**:
  * ``efficiency`` (bus0→bus1): Yield in t/ha
  * ``efficiency2`` (bus2, negative): Water requirement in m³/t
  * ``efficiency3`` (bus3, negative): Fertilizer requirement in kg/t
  * ``efficiency4`` (bus4, positive): Emissions in tCO₂-eq/t

When crops are converted into foods, the model first rescales the dry-matter crop bus to fresh edible mass using FAO edible portion coefficients and moisture shares derived from ``processing/{name}/fao_edible_portion.csv``. The scaling factor ``edible_portion_coefficient / (1 - water_fraction)`` is applied before product-specific extraction factors in ``data/foods.csv``. Crops listed in ``data/yield_unit_conversions.csv`` are assumed to already represent processed outputs and therefore skip this rescaling step.

**Crop-specific exceptions**: For certain crops, FAO's edible portion coefficients do not match the model's yield units, requiring special handling in ``workflow/scripts/prepare_fao_edible_portion.py``:

* **Grains** (rice, barley, oat, buckwheat): FAO coefficients reflect milled/hulled conversion, but we track whole grain. Coefficient forced to 1.0; milling handled separately.
* **Oil crops** (rapeseed, olive): GAEZ yields are already in kg oil/ha (see ``data/yield_unit_conversions.csv``), so no further conversion needed. Coefficient forced to 1.0.
* **Sugar crops** (sugarcane, sugarbeet): GAEZ yields are already in kg sugar/ha (see ``data/yield_unit_conversions.csv``), so no further conversion needed. Coefficient forced to 1.0.

The model constrains:

* Total land used per (region, class, water) ≤ available land
* Total water used per region ≤ blue water availability (see water constraints)
* Total fertilizer used globally ≤ global fertilizer limit

Water Constraints
-----------------

For irrigated crops, water availability is a key constraint. The model tracks blue water availability by basin and growing season.

Basin-Level Availability
~~~~~~~~~~~~~~~~~~~~~~~~

The model uses the Water Footprint Network's monthly blue water availability dataset for 405 GRDC river basins [hoekstra2011]_.

Processing steps (``workflow/scripts/process_blue_water_availability.py``):

1. **Load basin shapefile** with monthly availability (Mm³/month)
2. **Aggregate by basin and month** to get monthly water budgets

.. figure:: _static/figures/water_basin_availability.png
   :width: 100%
   :alt: Basin water availability map

   Annual blue water availability by GRDC river basin (mm/year). The map shows area-normalized yearly water availability across 405 major river basins globally. Higher availability is shown in darker blue, allowing direct comparison between basins of different sizes. While we normalize by area for better visualisation here, food-opt tracks total water amount availability internally.

Regional Water Assignment
~~~~~~~~~~~~~~~~~~~~~~~~~

Blue water availability is allocated to optimization regions based on spatial overlap with basins (``workflow/scripts/build_region_water_availability.py``):

1. **Spatial join**: Intersect region polygons with basin polygons
2. **Area weighting**: Allocate basin water proportional to overlap area
3. **Growing season matching**: Assign water to regions based on when crops are growing

   * Uses growing season start/length from GAEZ
   * Sums monthly availability over the growing period
   * For now, this is done on average over all crops that can grow in the region

4. **Output**: CSV files:

   * ``processing/{name}/water/monthly_region_water.csv``: Monthly water by region
   * ``processing/{name}/water/region_growing_season_water.csv``: Growing season totals

.. figure:: _static/figures/water_region_availability.png
   :width: 100%
   :alt: Regional water availability map

   Growing season water availability by optimization region (mm). The map shows area-normalized water available during the average growing season for each region, computed by summing monthly basin availability over the typical crop growing period. This represents the blue water constraint for irrigated crop production in the optimization model.

Irrigated Land Availability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Only a fraction of agricultural land is equipped with irrigation infrastructure. The model uses GAEZ v5's "land equipped for irrigation" dataset (LR-IRR) to determine which land can support irrigated crops.

**Key features:**

* **Spatial variation**: Irrigated land fraction varies by location based on infrastructure, water access, and historical development
* **Land competition**: Rainfed and irrigated production compete for the same physical land
* **Water coupling**: Irrigated land must have both irrigation infrastructure *and* sufficient blue water availability

The following figure shows the global distribution of land equipped for irrigation:

.. figure:: _static/figures/irrigated_land_fraction.png
   :width: 100%
   :alt: Irrigated land fraction map

   Fraction of land equipped for irrigation from GAEZ v5. Higher values (darker colors) indicate areas with more extensive irrigation infrastructure. Many agricultural regions show low irrigation fractions, limiting irrigated crop production even when water is available.

**Interaction with rainfed cropland:**

Within each optimization region and resource class, the model maintains separate variables for rainfed and irrigated land use. However, these share the same physical land base:

* **Rainfed land limit**: Total suitable cropland minus irrigated share
* **Irrigated land limit**: Total suitable cropland times irrigated share
* **Constraint**: Rainfed area + irrigated area ≤ total suitable cropland

This means that in regions with limited irrigation infrastructure, the model may:

* Prioritize irrigated production on the best land (higher resource classes) when water is available
* Fall back to rainfed production when irrigation infrastructure or water is limiting
* Trade off between high-yield irrigated crops (requiring both infrastructure and water) and lower-yield rainfed crops (requiring neither)

The irrigation infrastructure constraint is particularly important in regions where water is abundant but irrigation systems are not widely deployed, preventing the model from unrealistically converting all suitable land to high-yield irrigated production.

Fertilizer
----------

Crop production requires nitrogen (N), phosphorus (P), and potassium (K) fertilizers. The model includes:

* **Global fertilizer limit**: Total NPK available (``primary.fertilizer.limit`` in config, specified in kg and converted to Mt internally)
* **Crop-specific requirements**: Fertilizer needed per tonne of production (from ``data/crops.csv``)
* **Emissions factors**: N₂O emissions from nitrogen application

The fertilizer constraint is typically set at a realistic global scale (e.g., 200 Mt NPK/year) to prevent unrealistic intensification.

For now, N, P & K are not differentiated, and their GHG emissions are not tracked appropriately; this is work in development.

Growing Seasons
---------------

Temporal overlap of growing seasons within a region affects:

* **Water availability**: Multiple crops may compete for water during the same months
* **Land use**: Double-cropping potential if growing seasons don't overlap

Currently, the model uses annual time resolution, so it implicitly assumes:

* Each land parcel produces one crop per year
* Water constraints apply to the full growing season

Allowing for growing multiple crops a year is work in development.

Crop-Specific Data Files
-------------------------

**data/crops.csv**
  Long-form crop parameter table (mock starter data). Each row represents a ``(crop, param)`` pair:

  * ``crop``: Crop identifier matching entries used in configs and raster filenames
  * ``param``: Parameter key (currently ``fertilizer``, ``co2``, or ``ch4``)
  * ``unit``: Unit string for ``value`` (e.g., ``kg/t``)
  * ``value``: Numeric parameter value interpreted according to ``param``
  * ``description``: Free-text explanation of the assumption

  Add new parameters by appending rows; comment lines starting with ``#`` are ignored by loaders.

**data/gaez_crop_code_mapping.csv**
  Lookup table aligning food-opt crop identifiers with GAEZ resource codes. Columns: ``crop_name``, ``description``, and the RES02/RES05/RES06 codes used to locate raster layers.

**data/yield_unit_conversions.csv**
  Optional per-crop overrides for converting raw GAEZ yields to tonnes per hectare. Columns: ``code`` (GAEZ crop code used in filenames), ``factor_to_t_per_ha`` (multiplier applied to raster values), and ``note`` for context. Unlisted crops fall back to the default ``0.001`` factor.

Workflow Rules
--------------

Crop yield processing is handled by the ``build_crop_yields`` rule:

* **Input**: Resource classes, GAEZ rasters (yield, suitability, water, growing season), regions, unit conversions
* **Wildcards**: ``{crop}`` (crop name), ``{water_supply}`` ("r" or "i")
* **Output**: ``processing/{name}/crop_yields/{crop}_{water_supply}.csv``
* **Script**: ``workflow/scripts/build_crop_yields.py``

Run for a specific crop with::

    tools/smk -j1 processing/{name}/crop_yields/wheat_r.csv

Or for all crops automatically via dependencies of the ``build_model`` rule.

.. TODO: update this with reference to yield plotting rule, etc.
..
   Visualization
   -------------

   Crop production results can be visualized with several plotting rules:

   **Production totals**::

       tools/smk results/{name}/plots/crop_production.pdf

   **Spatial distribution**::

       tools/smk results/{name}/plots/crop_production_map.pdf

   **Land use by crop**::

       tools/smk results/{name}/plots/crop_land_use_map.pdf

   **Crop utilization** (food vs. feed vs. waste)::

       tools/smk results/{name}/plots/crop_use_breakdown.pdf


References
-----------

.. [hoekstra2011] Hoekstra, A.Y. and Mekonnen, M.M. (2011) *Global water scarcity: monthly blue water footprint compared to blue water availability for the world's major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, the Netherlands. http://www.waterfootprint.org/Reports/Report53-GlobalBlueWaterScarcity.pdf
