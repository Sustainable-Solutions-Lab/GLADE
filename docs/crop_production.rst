.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
  * Banana, watermelon, mango, citrus, coconut

**Oil Crops**
  * Sunflower, rapeseed, groundnut
  * Sesame, oil palm, olive

**Sugar Crops**
  * Sugarcane, sugarbeet

**Fiber Crops**
  * Cotton (ginned to produce cotton lint, cottonseed oil, and oilseed meal)

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

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/crop_yield_wheat.png
   :width: 100%
   :alt: Wheat yield potential map

   Wheat rainfed yield potential (tonnes/hectare) from GAEZ v5. Higher yields are shown in darker green. Black lines indicate region boundaries. Wheat performs best in temperate zones with adequate rainfall.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/crop_yield_wetland-rice.png
   :width: 100%
   :alt: Rice yield potential map

   Wetland rice rainfed yield potential (tonnes/hectare) from GAEZ v5. Rice shows high productivity in tropical and subtropical regions with suitable water availability, particularly in Asia.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/crop_yield_maize.png
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

   * **Potential runs**: GAEZ RES05 “yield” rasters are in kg/ha, so the default multiplier is ``0.001`` (kg → tonne). This is the behaviour in the standard (non-validation) configuration.
   * **Validation runs**: When ``validation.use_actual_yields: true`` the pipeline swaps to the GAEZ “actual yield” rasters, which are already in tonnes per hectare. In this mode the default multiplier is ``1.0`` so we do not double scale the data.
   * **Sugar & oil crops**: ``data/curated/yield_unit_conversions.csv`` stores overrides for sugarcane, sugarbeet, and oil-palm because GAEZ reports processed outputs (sugar or oil). The factors are interpreted relative to the historical kg/ha baseline, so they continue to work for both scenarios (we convert them into a scenario-agnostic multiplier inside ``build_crop_yields.py``).

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

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/crop_yield_resource_class_wheat.png
   :width: 100%
   :alt: Wheat yields by resource class

   Comparison of wheat rainfed yields (tonnes/hectare) between resource class 1 (left) and resource class 2 (right). Resource class 2 represents higher-quality land and generally shows higher yields across most regions, demonstrating how the resource class stratification captures land quality variation.

.. Note:: Yields for individual crops need not always be better in a high resource class. This is because resource classes are determined "globally" for all crops at once, so that each grid cell is assigned a resource class independent of any crop. So while resource class 2 has better *average* yields than resource class 1 in every region, that might not be true for some individual crops (e.g. rainfed wheat in the Western USA region in the above example.)

.. _yield-calibration:

GAEZ-Proxy Yield Calibration
----------------------------

A handful of model crops do not have a dedicated GAEZ yield raster and
must therefore borrow a related crop's raster as a proxy. Without
correction, country-level production under
``validation.use_actual_yields: true`` then systematically
under- or over-shoots the FBS-corrected FAOSTAT total for those crops.
The ``yield_calibration`` section of ``config/default.yaml`` lists the
affected crops and provides a per-country multiplicative correction
that rescales every per-cell yield in a country so that the model's
country-level production matches FAOSTAT by construction, while
preserving the GAEZ within-country spatial pattern.

For each crop :math:`f` listed under ``yield_calibration.crops`` the
multiplier for country :math:`c` is

.. math::

   m_{c,f} = \mathrm{clip}\!\left(
       \frac{P^{\text{FBS-corrected FAOSTAT}}_{c,f}}
            {P^{\text{model GAEZ}}_{c,f}},\
       [m_{\min},\ m_{\max}]
   \right)

with both sides on a fresh-weight basis (the dry-matter-to-fresh
conversion cancels in the ratio, but is applied explicitly so that the
intermediate diagnostics in the build log are interpretable). Clip
bounds default to ``[0.5, 3.0]``; a value outside this range is logged
and clipped rather than silently absorbed.

Initial entries (subject to change as upstream data evolves):

* **plantain** — GAEZ has no plantain output, so the build pipeline
  uses the GAEZ **banana** raster as the proxy. FAOSTAT plantain
  yields run ~30 % higher than GAEZ-banana yields in African producers,
  giving a global plantain under-production of about 24 % before
  calibration.
* **coffee** — GAEZ COC yields are systematically below FAOSTAT in
  the major producers (BRA, VNM, COL), giving ~26 % global
  under-production before calibration.
* **tea** — GAEZ TEA yields overshoot in CHN; without rescaling the
  model produces about 30 % more dried tea than the FAOSTAT fresh-leaf
  statistics imply (after the standard 4:1 fresh-to-dry conversion).

The output schema matches ``fodder_yield_corrections.csv``
(``country, crop, yield_correction_factor``) so the
``build_model.crops`` module applies both corrections through the same
per-cell yield-rescaling mechanism; where they overlap they **compose
multiplicatively**. The mechanism is intentionally inert in
optimisation mode: the calibration is only applied when
``validation.use_actual_yields: true``, since the GAEZ potential
yields under the standard configuration are meant to reflect agronomic
potential rather than current realised output and so cannot be
anchored to historical FAOSTAT totals.

Rule: ``build_yield_calibration`` in ``workflow/rules/crops.smk``.
Script: ``workflow/scripts/build_yield_calibration.py``.

Seed Reservation
----------------

Per growing cycle, a fraction of harvested mass is reserved as seed for the next planting and never reaches the crop bus. The model deducts this on the production side as a yield haircut:

.. math::

   \text{post-seed yield} = \text{yield} \times \left(1 - \text{seed\_share}\right), \qquad
   \text{seed\_share} = \min\!\left(\frac{\text{seed\_kg\_per\_ha}}{1000 \cdot \text{yield\_t\_per\_ha}},\; 0.5\right).

Sowing rates are looked up per crop in :file:`data/curated/seed_rates.csv` and combined with the per-(country, region, class, water) yield. This makes the seed share country-specific by construction: low-yield countries reserve a larger fraction of the harvest. The deduction is applied uniformly to single-crop and multi-crop links (``workflow/scripts/build_model/crops.py``).

The seed share is **separate** from food-loss-and-waste accounting; FLW only handles supply-chain and consumer-side losses applied at the food bus. Coverage of every configured crop is enforced by ``workflow.validation.seed_rates`` so a missing row triggers a startup error rather than a silent zero.

Annualisation conventions encoded in the table:

* **Multi-year crops** (alfalfa, sugarcane): the establishment-year sowing rate is divided by typical stand life so that a fixed annual deduction does not over-count seed.
* **Vegetatively propagated crops** (cassava, sweet-potato, banana): zero, because cuttings come from above-ground biomass that FAOSTAT does not book under the Seed element.
* **Perennials** (oil-palm, olive, mango, citrus, coconut, cocoa, coffee, tea): zero, no annual seed reservation.

Sowing rates are drawn from agronomy literature; one row (`biomass-sorghum`) is explicitly marked ``ASSUMED`` because no global review was found at the time of compilation. The full table is reproduced below for spot-checking; each row carries its source description and (where available) a URL.

Mango uses its own GAEZ v5 RES05 yield, suitability, and water-deficit rasters,
but GAEZ v5 does not currently provide mango RES02 growing-season start/length
rasters. The workflow therefore uses citrus as an explicit RES02 calendar
fallback via ``data/curated/gaez_crop_code_mapping.csv`` while keeping mango's
own output filenames and downstream crop identity.

.. raw:: html

   <details>
     <summary><strong>Show seed_rates.csv</strong> (per-crop sowing rates and citations)</summary>

.. literalinclude:: ../data/curated/seed_rates.csv
   :language: text

.. raw:: html

   </details>

Production Constraints
----------------------

In the PyPSA model (``workflow/scripts/build_model.py``), crop production is represented as multi-bus links:

**Inputs**:
  * Land (from land bus for the region/class/water combination)
  * Water (for irrigated crops only)
  * Fertilizer (for all crops, with configurable N-P-K requirements)

**Outputs**:
  * Crop product (to crop bus)
  * Emissions (CH₄ from wetland rice, N₂O from un-collectable residue decomposition)
  * Net (feed-usable) crop residue, if any

**Efficiency Parameters**:
  * ``efficiency`` (bus0→bus1): Yield in t/ha (already net of the seed reservation described above and the per-country supply-chain loss multiplier)
  * ``efficiency2`` (bus2, negative): Water requirement in m³/ha (irrigated rows only)
  * ``efficiency3`` (bus3, negative): Fertilizer N requirement in Mt N per Mha
  * ``efficiency4`` (bus4, positive): Wetland-rice CH₄ emissions in t CH₄ per Mha (zero for non-rice rows)
  * ``efficiency5`` (bus5, positive): Net residue yield in t DM per ha — gross at-harvest biomass scaled by the per-feed-item field utilisation efficiency (FUE, per GLEAM 3.0 Supplement S1). Routed to ``residue:{item}:{country}`` and from there into feed-conversion or optional ``residue_incorporation``. Loss multipliers are *not* applied: residues stay in the field and do not share the grain's storage/transport/processing loss path.
  * ``efficiency6`` (bus6 = ``emission:n2o``, positive): Mandatory soil N₂O in t N₂O per Mha from the ``(1 - FUE)`` gross residue share that cannot be physically collected from the field. Equals ``gross_residue_per_ha * (1 - FUE) * n2o_eff_per_t_DM``. Wired here (rather than on the residue bus) so the LP cannot avoid the un-collectable-residue N₂O by re-routing dispatch through the feed link — a plain ``efficiency<1`` on a residue→feed link silently destroys the (1 - FUE) fraction at the conversion step instead of routing it to soil. See :doc:`environment` for the N₂O coefficient formula.

When crops are converted into foods, the model first rescales the dry-matter crop bus to fresh edible mass using FAO edible portion coefficients and moisture shares drawn from ``data/curated/crop_moisture_content.csv``. The scaling factor ``edible_portion_coefficient / (1 - moisture_fraction)`` is applied before product-specific extraction factors in ``data/curated/foods.csv``. Crops listed in ``data/curated/yield_unit_conversions.csv`` are the cases where GAEZ reports processed outputs (sugar or oil); the table converts those back to dry matter so that subsequent processing logic is uniform.

**Crop-specific exceptions**: For certain crops, FAO's edible portion coefficients do not match the model's yield units, requiring special handling in ``workflow/scripts/prepare_fao_edible_portion.py``:

* **Grains** (rice, barley, oat, buckwheat): FAO coefficients reflect milled/hulled conversion, but we track whole grain. Coefficient forced to 1.0; milling handled separately.
* **Sugar crops** (sugarcane, sugarbeet), **oil-palm**, and **rapeseed**: sugar and palm yields are converted back to whole-crop dry matter via ``data/curated/yield_unit_conversions.csv``; rapeseed uses FAO edible-portion type-2 coefficients that already encode processing extraction. In all three cases, edible portion coefficients are forced to 1.0 so that extraction losses are handled exactly once in ``data/curated/foods.csv``.

.. note::

   When ``validation.use_actual_yields`` is enabled, the GAEZ “actual” rasters already reflect whole-crop fresh mass for sugarcane, sugarbeet, and oil palm, so the workflow bypasses the conversion overrides above and relies directly on ``data/curated/crop_moisture_content.csv`` to compute dry-matter production. This keeps validation-era sugarcane output near observed fresh cane harvests instead of re-scaling the processed sugar or oil mass.

The model constrains:

* Total land used per (region, class, water) ≤ available land
* Total water used per region ≤ blue water availability (see water constraints)
* Total fertilizer used globally ≤ global fertilizer limit

Production Costs
----------------

Crop production incurs economic costs that are included in the optimization objective. Costs are derived from FAOSTAT producer prices and yields, scaled by a configurable non-endogenous cost share, producing per-(crop, country) estimates.

Crop costs are applied as marginal costs on production links. For multiple cropping systems, costs are summed across all crops in the combination.

**Cost structure**:

* **Included**: All non-endogenous production costs (approximated as a share of revenue)
* **Excluded**: Fertilizer (modeled endogenously), land rent (opportunity cost in optimization), irrigation water (resource constraint)

For comprehensive details on crop production cost data sources, processing methodology, and model application, see:

  * :doc:`costs` - Complete documentation of all production costs (crops, livestock, and grazing)

**Quick reference** for crop cost workflow:

* ``prepare_faostat_crop_costs``: Computes per-(crop, country) costs from FAOSTAT PP prices and QCL yields
* Output: ``processing/{name}/faostat_crop_costs.csv`` with columns:

  * ``crop``: Crop name
  * ``country``: ISO3 country code
  * ``cost_usd_{base_year}_per_ha``: Production cost estimate (USD/ha)
  * ``n_years``: Number of years with valid data
  * ``is_fallback``: Whether the value is a global median fallback

Water Constraints
-----------------

For irrigated crops, water availability is a key constraint. The model supports two water supply scenarios, selected via ``config.water.supply_scenario``:

* ``sustainable``: Water Footprint Network blue water availability by basin, representing sustainable extraction limits.
* ``current_use``: Huang et al. monthly irrigation withdrawals, representing present-day agricultural water use (useful for validation).

Both scenarios are processed into the same regional monthly and growing-season CSVs. ``workflow/rules/water.smk`` selects the configured scenario and writes the unified outputs under ``processing/{name}/water/`` for model building.

Sustainable Basin-Level Availability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model uses the Water Footprint Network's monthly blue water availability dataset for 405 GRDC river basins [hoekstra2011]_.

Processing steps (``workflow/scripts/process_blue_water_availability.py``):

1. **Load basin shapefile** with monthly availability (Mm³/month)
2. **Aggregate by basin and month** to get monthly water budgets

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/water_basin_availability.png
   :width: 100%
   :alt: Basin water availability map

   Annual blue water availability by GRDC river basin (mm/year). The map shows area-normalized yearly water availability across 405 major river basins globally. Higher availability is shown in darker blue, allowing direct comparison between basins of different sizes. While we normalize by area for better visualisation here, GLADE tracks total water amount availability internally.

Current-Use Irrigation Withdrawals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``water.supply_scenario`` is set to ``current_use``, the workflow uses Huang et al. (2018) gridded monthly irrigation withdrawals (0.5 degree resolution, 1971-2010) [huang2018]_. ``workflow/scripts/process_huang_irrigation_water.py`` aggregates these withdrawals to regions and computes growing-season totals using the same crop-weighted method as the sustainable dataset.

Outputs:

* ``processing/{name}/water/current_use/monthly_region_water.csv``
* ``processing/{name}/water/current_use/region_growing_season_water.csv``

Regional Water Assignment
~~~~~~~~~~~~~~~~~~~~~~~~~

Blue water availability is allocated to optimization regions using the dataset-specific processing scripts:

* ``workflow/scripts/build_region_water_availability.py`` for ``sustainable``
* ``workflow/scripts/process_huang_irrigation_water.py`` for ``current_use``

Both produce the same output schema so the model can remain unchanged.

For the sustainable dataset, the allocation steps are:

1. **Spatial join**: Intersect region polygons with basin polygons
2. **Area weighting**: Allocate basin water proportional to overlap area
3. **Growing season matching**: Assign water to regions based on when crops are growing

   * Uses growing season start/length from GAEZ
   * Sums monthly availability over the growing period
   * For now, this is done on average over all crops that can grow in the region

4. **Output**: CSV files:

   * ``processing/{name}/water/monthly_region_water.csv``: Monthly water by region
   * ``processing/{name}/water/region_growing_season_water.csv``: Growing season totals

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/water_region_availability.png
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

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/irrigated_land_fraction.png
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

* **Global fertilizer limit**: Total synthetic nitrogen available (``fertilizer.limit`` in config, specified in kg-N and converted to Mt-N internally)
* **Global marginal cost**: Blanket fertilizer price in USD per tonne-N (``fertilizer.marginal_cost_usd_per_tonne``) converted to bnUSD/Mt-N and applied to the global fertilizer generator
* **Crop-specific requirements**: Fertilizer needed per tonne of production (from ``data/crops.csv``)
* **Emissions factors**: N₂O emissions from nitrogen application

All fertilizer quantities in the model (limits, costs, crop coefficients, emissions factors) refer to the mass of nitrogen nutrient (t-N or Mt-N). The fertilizer constraint is typically set at a realistic global scale (e.g., 200 Mt-N/year) to prevent unrealistic intensification.

At present only nitrogen nutrient flows are modeled explicitly; phosphorus and potassium application (and their GHG emissions) remain out of scope and are tracked implicitly in future work.

Growing Seasons
---------------

Temporal overlap of growing seasons within a region affects:

* **Water availability**: Multiple crops may compete for water during the same months
* **Land use**: Double-cropping potential if growing seasons don't overlap

Currently, the model uses annual time resolution, so it implicitly assumes:

* Each land parcel produces one crop per year
* Water constraints apply to the full growing season

Multiple Cropping
-----------------

Many production systems plant two or more sequential crops on the same parcel.
Multiple cropping is anchored to an observed reference-year baseline derived
from MIRCA-OS v2 (see :doc:`data_sources`), so the model starts from observed
double-cropped area -- notably India's rice-wheat and rice-rice systems -- rather
than treating every extra cropping cycle as unanchored potential. The MIRCA
release closest to ``baseline_year`` is used; the workflow supports 2010, 2015,
and 2020, ties select the earlier year, and a warning is emitted when the match
is not exact.

**Observed-baseline derivation.** The ordinary, config-specific Snakemake rule
``derive_mirca_multicropping`` (``workflow/scripts/derive_mirca_multicropping.py``)
attributes MIRCA's extra-cycle harvested area -- annual harvested area minus the
AEI-capped physical footprint -- independently for irrigated and rainfed land.
It considers only the fixed candidate sequences in
``data/curated/mirca_os_multicropping_combinations.yaml``. The catalog is tied to
the MIRCA-OS v2 crop taxonomy and records widely documented rotations and
repeated-rice systems that both datasets can represent; catalog membership is a
candidate attribution, not evidence that a rotation occurs in every cell.

The rule aggregates directly to the active config's regions and resource
classes. It writes ``baseline_area.csv``, a diagnostic
``residual_multicrop.tif``, and ``attribution_stats.csv`` under
``processing/{name}/multi_cropping/``. Keeping these products config-specific
is necessary because the aggregation and GAEZ multiple-cropping-zone gate
depend on the config's spatial and climate inputs. Extra-cycle area that cannot
be assigned to a catalog sequence remains unattributed and is handled by the
bulk land correction.

A combination is a candidate in a cell where **every crop is observed in MIRCA**
in that water supply and the GAEZ multiple-cropping zone permits the cycle count.
``sequence_feasible`` on GAEZ growing-season windows is deliberately *not* used as
a feasibility gate: GAEZ attainable season lengths overshoot the farmed cycle and
would reject nearly all observed irrigated double-cropping. MIRCA's observation is
the feasibility evidence; beyond the zone gate, GAEZ enters only for
suitability, yields and water requirements.

Within each cell and water supply, all candidate sequences receive a common
proportional fill rate subject to the extra-cycle magnitude, physical footprint,
each constituent crop's observed harvested-area budget, and each sequence's
MIRCA/GAEZ support. The shared crop budgets prevent the same observed wheat,
maize, or other crop area from being attributed independently to several
overlapping rotations. Any area that cannot be assigned under all budgets stays
in the residual.

**Potential derivation.** The preprocessing rule
``build_multi_cropping`` reads the GAEZ rasters for every crop in each
(combination, water supply) pair and, over pixels where the zone permits the cycle
count and every crop has suitability, positive yield and (irrigated) a water
requirement, computes the eligible potential area and per-cycle yields. The
three tables in ``processing/{name}/multi_cropping/`` are
``eligible_area.csv`` (potential area plus the summed irrigated water
requirement ``water_requirement_m3_per_ha``; zero for rainfed variants),
``cycle_yields.csv`` (per-cycle t/ha), and ``baseline_area.csv`` (observed
anchor area per region/class/water supply).

Catalog combinations are included automatically when all their crops are
modeled. The ``multiple_cropping`` config section may set a catalog name to
``null`` to disable it or add a uniquely named greenfield sequence. Greenfield
sequences have an implicit zero baseline and expose only GAEZ-constrained
optimization potential; catalog entries cannot be redefined in config.

The RES01 classes report the agro-climatic zone the pixel belongs to. We interpret the
numeric codes as:

* 0 – masked (ocean/undefined)
* 1 – no cropping (too cold/dry)
* 2 – single cropping
* 3 – limited double cropping (GAEZ permits relay; we conservatively treat it as sequential
  double cropping with at most one wetland rice cycle)
* 4 – double cropping (no wetland rice sequentially)
* 5 – double cropping with up to one wetland rice crop
* 6 – double rice cropping (limited triple in the documentation is ignored here)
* 7 – triple cropping (≤2 wetland rice crops)
* 8 – triple rice cropping (up to three wetland rice crops)

Relay cropping opportunities mentioned for the C/F zones are intentionally ignored for now; we
only construct sequential crop chains. This assumption is called out in the configuration and
model framework documentation so users know the limitation.

During ``build_model`` each (combination, region, resource class) creates a single
rainfed or irrigated multi-output link (carrier ``crop_production_multi``) that:

* draws physical land from the matching cropland bus (``_r`` or ``_i``),
* emits one crop bus per cycle with efficiencies equal to the per-cycle yield,
* charges marginal cost using the sum of crop prices across cycles,
* deducts the combined fertilizer rate (kg N per ha summed over the crops), and
* (irrigated only) withdraws the summed water requirement on the region water bus.

The link is anchored at its MIRCA baseline via ``baseline_area_mha`` with
``p_nom_max = max(GAEZ potential, baseline)`` and stays extendable, so it is
subject to the same production-stability penalty as single-crop links and the
model can add or drop a complete sequence. Every configured cycle must have a
valid yield; partial bundles are never built. If an observed local anchor lacks
a complete GAEZ row, it is relocated within the same combination, country, and
water supply. If that group has no valid full-sequence target under the active
GAEZ inputs, no partial bundle is created: the harvested-area budget remains on
the constituent single-crop baselines.

Single-crop ``crop_production`` baselines are FAOSTAT *harvested* area and
already count every cycle. The harvested cycles carried by multi links are
therefore subtracted from the corresponding single-crop baselines
(``m_k`` times the multi anchor for crop ``k``). Where joint MIRCA bundle
requirements exceed a constituent crop's national FAOSTAT budget, all affected
bundle anchors are scaled by the most restrictive crop ratio. Any cycle
subtraction that cannot be made locally is taken from other single-crop links
only within the same crop, country, and water supply. The build then asserts
that single plus multiplicity-weighted multi baselines exactly reproduce every
incoming FAOSTAT budget. The crop-agnostic
``multi_cropping_land_correction`` generator absorbs only residual,
unattributed extra-cycle area.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/multi_cropping_potential_rainfed.png
   :alt: Rain-fed multi-cropping zones and regional potential
   :width: 100%

   Rain-fed perspective: top panel shows RES01-MCR classes from GAEZ v5. Bottom panel
   reports the share of each optimisation region where the climate supports sequential
   multi-cropping (zones C–H). Zones suitable only for relay systems are counted as
   sequential double cropping, consistent with the current model assumptions.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/multi_cropping_potential_irrigated.png
   :alt: Irrigated multi-cropping zones and regional potential
   :width: 100%

   Irrigated perspective: top panel shows RES01-MCI classes. Bottom panel reports the
   share of each optimisation region where irrigated climate conditions allow sequential
   multi-cropping. Relay-only zones are again interpreted as sequential crop chains.

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

**data/curated/gaez_crop_code_mapping.csv**
  Lookup table aligning GLADE crop identifiers with GAEZ resource codes. Columns: ``crop_name``, ``description``, and the RES02/RES05/RES06 codes used to locate raster layers.

**data/curated/yield_unit_conversions.csv**
  Optional per-crop overrides for converting raw GAEZ yields to tonnes of dry matter per hectare. Columns: ``code`` (crop identifier), ``factor_to_t_per_ha`` (multiplier applied to raster values), and ``note`` for context. Only sugar crops and oil-palm currently require overrides; all other crops use the default ``0.001`` factor (kg → tonne).

**data/curated/crop_moisture_content.csv**
  Moisture fractions (0-1) for each modelled crop, primarily sourced from the GAEZ v5 Module VII documentation with explicit notes where assumptions were required. Combined with edible portion coefficients to convert dry matter yields into fresh edible mass.

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

Visualization
-------------

Once a scenario has been solved, several plotting rules in
``workflow/rules/plotting.smk`` produce diagnostic figures for the crop
sector. All outputs land under
``results/{name}/plots/scen-{scenario}/``.

**Dominant-crop-group map** — gridcell-level map of cropland intensity
coloured by the locally dominant crop group::

    tools/smk -- results/{name}/plots/scen-{scenario}/crop_production_map.pdf

Produced by ``rule plot_crop_production_map``
(``workflow/scripts/plotting/plot_crop_production_map.py``). Feed crops
(pasture, forage) dominate area everywhere and are excluded from the
raster so they do not overwhelm the other groups; they remain visible in
the accompanying bar chart.

**Crop-use breakdown** — stacked bar chart of global crop production
allocated to food, animal feed, and other uses, with separate irrigated
and rainfed totals::

    tools/smk -- results/{name}/plots/scen-{scenario}/crop_use_breakdown.pdf

Produced by ``rule plot_crop_use_breakdown``
(``workflow/scripts/plotting/plot_crop_use_breakdown.py``). The companion
CSV (``crop_use_breakdown.csv``) carries the same numbers in tabular
form for downstream notebooks.

**Crop-trade map** — same gridcell map as above, overlaid with the
largest hub-to-hub trade flows (arrow colour by commodity group, width
by volume)::

    tools/smk -- results/{name}/plots/scen-{scenario}/crop_trade_map.pdf

Produced by ``rule plot_crop_trade_map``
(``workflow/scripts/plotting/plot_crop_trade_map.py``).


References
-----------

.. [hoekstra2011] Hoekstra, A.Y. and Mekonnen, M.M. (2011) *Global water scarcity: monthly blue water footprint compared to blue water availability for the world's major river basins*, Value of Water Research Report Series No. 53, UNESCO-IHE, Delft, the Netherlands. http://www.waterfootprint.org/Reports/Report53-GlobalBlueWaterScarcity.pdf
.. [huang2018] Huang, Z., Hejazi, M., Li, X., Tang, Q., Vernon, C., Leng, G., Liu, Y., Doll, P., Eisner, S., Gerten, D., Hanasaki, N., and Wada, Y. (2018). Reconstruction of global gridded monthly sectoral water withdrawals for 1971-2010 and analysis of their spatiotemporal patterns. *Hydrology and Earth System Sciences*, 22, 2117-2133. https://doi.org/10.5194/hess-22-2117-2018
