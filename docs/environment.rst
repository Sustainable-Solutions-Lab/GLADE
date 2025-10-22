.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Environmental Impacts
=====================

Overview
--------

The environmental module accounts for greenhouse gas emissions, land use change and nitrogen pollution from food production. These impacts are monetized and included in the objective function via configurable prices/penalties.

This is currently a work in progress and not all relevant environmental impacts are implemented and monetized yet.

Greenhouse Gas Emissions
-------------------------

The model tracks three major greenhouse gases using 100-year global warming potentials (GWP100):

* **CO₂** (GWP = 1): From land use change, fuel combustion
* **CH₄** (GWP = 28): From enteric fermentation (ruminants), rice paddies, manure
* **N₂O** (GWP = 265): From nitrogen fertilizer application, manure

All emissions are aggregated to CO₂-equivalent (internally tracked in MtCO₂-eq; the configured price still applies per tonne) for carbon pricing.

Implementation notes (buses, stores, links)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The optimisation model represents environmental flows with three PyPSA components that are worth keeping in mind:

* **Buses** act as balance sheets. Process components report raw emissions to the ``co2`` and ``ch4`` buses, while a dedicated ``ghg`` bus tracks the combined CO₂-equivalent balance.
* **Links** move quantities between buses, applying efficiencies that encode global warming potentials. ``convert_co2_to_ghg`` has efficiency 1.0, and ``convert_ch4_to_ghg`` uses the configured ``emissions.ch4_to_co2_factor`` (27.2 by default). Every megatonne of CH₄ (after scaling from tonnes) therefore appears on the ``ghg`` bus weighted by its 100-year GWP.
* **Stores** accumulate quantities over the horizon. The extendable ``ghg`` store sits on the combined bus and is priced at ``emissions.ghg_price``. Because neither the ``co2`` nor ``ch4`` buses have stores, their flows must pass through the conversion links before the objective is charged.

With this structure the linear program keeps separate ledgers for each greenhouse gas while charging the objective using a single priced stock of CO₂-equivalent. Scenario files can tighten or relax climate policy simply by changing the configuration values—no code modifications are required.

Sources of Emissions
~~~~~~~~~~~~~~~~~~~~

**Crop Production**:
  * N₂O from fertilizer (direct and indirect)
  * CH₄ from flooded rice cultivation
  * CO₂ from machinery/fuel (if included)

**Livestock**:
  * CH₄ from enteric fermentation (ruminants)
  * N₂O and CH₄ from manure management
  * CO₂ from feed production (indirect)

**Land Use Change**:
  * CO₂ from clearing vegetation (forest, grassland → cropland)
  * Soil carbon losses

Direct N₂O emission factors
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The model uses the 2019 Refinement to the IPCC Guidelines for National Greenhouse Gas Inventories to parameterise direct N₂O emissions from managed soils. Table 11.1 (updated) is reproduced below to make the default emission factors and their uncertainty ranges readily accessible when configuring fertilizer-related pathways.

.. list-table:: Default emission factors to estimate direct N₂O emissions from managed soils (IPCC, 2019 Refinement - Table 11.1)
   :header-rows: 1
   :widths: 32 11 14 25 9 9

   * - Emission factor
     - Aggregated default value
     - Aggregated uncertainty range
     - Disaggregation
     - Default value
     - Uncertainty range
   * - EF\ :sub:`1` for N additions from synthetic fertilisers, organic amendments and crop residues, and N mineralised from mineral soil as a result of loss of soil carbon [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.010
     - 0.002 – 0.018
     - Synthetic fertiliser inputs in wet climates

       Other N inputs in wet climates

       All N inputs in dry climates
     - 0.016 (wet synthetic)

       0.006 (wet other)

       0.005 (dry)
     - 0.013 – 0.019

       0.001 – 0.011

       0.000 – 0.011
   * - EF\ :sub:`1FR` for flooded rice fields [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.004
     - 0.000 – 0.029
     - Continuous flooding

       Single and multiple drainage
     - 0.003

       0.005
     - 0.000 – 0.010

       0.000 – 0.016
   * - EF\ :sub:`3PRP,CPP` for cattle (dairy, non-dairy and buffalo), poultry and pigs [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.004
     - 0.000 – 0.014
     - Wet climates

       Dry climates
     - 0.006

       0.002
     - 0.000 – 0.027

       0.000 – 0.007
   * - EF\ :sub:`3PRP,SO` for sheep and “other animals” [kg N₂O-N (kg N)\ :sup:`-1`]
     - 0.003
     - 0.000 – 0.010
     - –
     - –
     - –

Carbon Pricing
~~~~~~~~~~~~~~

Emissions are priced at a configurable rate:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: emissions ---
   :end-before: # --- section: crops ---

Land Use Change
---------------

Land-use change (LUC) emissions capture the carbon consequences of converting land between natural vegetation, cropland, pasture, and spared (actively rewilded) states. The model derives annualized per-hectare coefficients for each resource class and water supply that quantify the net CO₂ flux associated with allocating an additional hectare to a specific land use.

Conceptual overview
~~~~~~~~~~~~~~~~~~~

For every grid cell on the common suitability grid, the workflow computes three main quantities:

* **Pulse emissions (:math:`P_{i,u}`)** – the one-off release (or uptake) that occurs when land transitions from its natural state to land use :math:`u` (cropland or pasture). We estimate above-ground biomass (AGB), below-ground biomass (BGB), and soil organic carbon (SOC) stocks for both the natural and agricultural equilibria, then convert the difference to CO₂ using the stoichiometric factor :math:`44/12`.
* **Annual regrowth (:math:`R_i`)** – the ongoing sequestration potential when land is spared or allowed to regrow. Regrowth is only applied where forest cover is present in the baseline land-cover map.
* **Managed flux (:math:`M_{i,u}`)** – ongoing emissions from managed systems (e.g., peat oxidation, continuous tillage). The current implementation sets :math:`M_{i,u} = 0` everywhere as a simplifying assumption.

The per-hectare land-use change factor (LEF) combines these components over the planning horizon :math:`H` (years) configured in ``config/default.yaml``:

.. math::

   \mathrm{LEF}_{i,u} = \frac{P_{i,u}}{H} + (R_i - M_{i,u})

LEFs are computed for three uses (``cropland``, ``pasture``, ``spared``). Cropland and pasture incur positive costs when they release carbon; spared land yields negative LEFs because regrowth produces a CO₂ sink. Area-weighted aggregation over resource classes produces region-level coefficients that the optimisation layer consumes.

Input datasets
~~~~~~~~~~~~~~

The LUC pipeline harmonises several global datasets to the common grid:

* Land cover fractions and forest masks from Copernicus ESA CCI land cover (:ref:`copernicus-land-cover`)
* Above-ground biomass from ESA Biomass CCI v6.0 (:ref:`esa-biomass-cci`)
* Soil organic carbon stocks (0–30 cm) from ISRIC SoilGrids 2.0 (:ref:`soilgrids-soc`), scaled to 1 m depth using IPCC Tier 1 factors
* Natural forest regrowth rates from Cook-Patton & Griscom (2020) (:ref:`cook-patton-regrowth`)
* IPCC Tier 1 below-ground biomass ratios, soil depletion factors, and agricultural equilibrium assumptions stored in ``data/luc_zone_parameters.csv``

These layers are reprojected, resampled, and combined by dedicated Snakemake rules to produce per-cell biomass/SOC stocks, forest masks, and regrowth rates ready for downstream processing. Figure :ref:`fig-luc-inputs` summarises the harmonised rasters on the common model grid.

.. _fig-luc-inputs:

.. figure:: /_static/figures/environment_luc_inputs.png
   :alt: Global maps showing forest fraction, biomass, soil carbon, and regrowth inputs
   :align: center
   :width: 95%

   Land-use change input layers harmonised to the modelling grid: forest fraction (Copernicus CCI), above-ground biomass (ESA Biomass CCI v6.0), soil organic carbon 0–30 cm (SoilGrids 2.0), and natural forest regrowth potential (Cook-Patton & Griscom, 2020).

Model integration
~~~~~~~~~~~~~~~~~

The land-use change workflow consists of two scripts:

1. ``prepare_luc_inputs.py`` aligns the raw rasters to the resource-class grid and stores intermediate masks and carbon pools under ``processing/{config}/luc/``.
2. ``build_luc_carbon_coefficients.py`` derives pulse emissions, annual LEFs, and aggregates them to ``luc_carbon_coefficients.csv``.

During model construction, ``build_model.py`` loads these coefficients, converts the LEFs to marginal CO₂ flows (MtCO₂ per Mha-year), and attaches them to:

* Crop production links (cropland LEFs)
* Grazing links that supply ruminant feed (pasture LEFs)
* Spared-land allocation links that credit regrowth sinks (filtered by current biomass—see below)

All flows connect to a single global ``co2`` bus. A flexible CO₂ store with marginal cost equal to the carbon price (``emissions.ghg_price``) accumulates the net balance, so positive flows are penalised while negative flows earn credits. This approach keeps LUC accounting endogenous to the optimisation problem while leveraging a consistent carbon price alongside other greenhouse-gas sources. The spatial pattern of the resulting LEFs is shown in :ref:`fig-luc-lef`.

Spared land filtering
~~~~~~~~~~~~~~~~~~~~~

Regrowth sequestration rates from Cook-Patton et al. (2020) represent **young regenerating forest** (0-30 years) on previously cleared or degraded land. They do not apply to mature forests, which have already accumulated most of their carbon stock and exhibit near-zero net sequestration.

To avoid incorrectly crediting sequestration on mature forest, the LEF calculation for spared land includes a conditional that sets the sequestration benefit to zero where **current above-ground biomass (AGB)** exceeds a configurable threshold (``luc.spared_land_agb_threshold_tc_per_ha``, default 20 tC/ha). Specifically:

.. math::

   \mathrm{LEF}_{\mathrm{spared}} = \begin{cases}
   -R & \text{if } \mathrm{AGB} \leq \text{threshold} \\
   0 & \text{if } \mathrm{AGB} > \text{threshold}
   \end{cases}

This ensures:

* Low-biomass areas (recently cleared or degraded land suitable for agriculture) receive negative LEFs (sequestration credits) if left unused
* High-biomass areas (mature tropical rainforest, boreal forest) receive zero spared-land LEF—their carbon value is already captured via high pulse emissions if converted, but they are not credited for additional regrowth

The threshold of 20 tC/ha is intermediate between typical agricultural land (0-10 tC/ha) and mature forest (50-200+ tC/ha). Areas above this threshold are assumed to represent established vegetation that would not exhibit the rapid early-successional regrowth rates quantified by Cook-Patton et al.

.. _fig-luc-lef:

.. figure:: /_static/figures/environment_luc_lef.png
   :alt: Global maps of cropland, pasture, and spared land LEFs
   :align: center
   :width: 95%

   Annualised land-use change emission factors (LEFs) used in the optimisation. Warm colors indicate positive emissions costs (CO₂ release), while cool colors represent sequestration credits.

Limitations and assumptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The current implementation makes several simplifying assumptions that should be considered when interpreting results:

* **Climatic zones**: Zones (tropical, temperate, boreal) are assigned by latitude only (tropical: :math:`\lvert \phi \rvert < 23.5^\circ`, boreal: :math:`\lvert \phi \rvert \ge 50^\circ`, temperate: otherwise). This does not account for altitude effects (e.g., highland tropics behave more like temperate zones) or local climate variations. A future enhancement would use actual biome or Köppen-Geiger climate classifications.

* **Agricultural biomass stocks**: Cropland and pasture equilibrium above-ground biomass is assumed to be negligible (0 tC/ha) for annual crops. This is a conservative assumption appropriate for grain crops where biomass is harvested annually, but underestimates carbon storage in perennial crops (orchards, oil palm, coffee) and improved pastures. See ``data/luc_zone_parameters.csv`` for the zone-specific parameters.

* **Forest mask threshold**: Regrowth sequestration is only applied to cells with ≥20% forest cover in the baseline land cover map. This threshold can be adjusted via ``config['luc']['forest_fraction_threshold']`` (default: 0.2). The choice of 20% is intermediate between FAO's forest definition (≥10% tree cover) and stricter definitions (≥30%).

* **Soil organic carbon depth**: SOC stocks in the 0-30 cm layer (from SoilGrids) are scaled to 1 m depth using zone-specific factors from ``data/luc_zone_parameters.csv``. **TODO**: These factors require verification against IPCC 2006/2019 Guidelines Volume 4 Chapter 2 to ensure they match the intended Tier 1 methodology.

* **Managed flux**: Set to zero everywhere (:math:`M_{i,u} = 0`), meaning ongoing emissions from agricultural management (e.g., peat oxidation, tillage-induced decomposition) are not currently modeled. Future work could incorporate organic soil maps and management-specific emission factors.

Nitrogen Pollution
------------------

Fertilizer application causes nitrogen pollution via:

* **Leaching**: NO₃⁻ contaminating groundwater
* **Runoff**: Eutrophication of rivers/lakes
* **Volatilization**: NH₃ → N₂O emissions

Global Fertilizer Limit
~~~~~~~~~~~~~~~~~~~~~~~

To prevent excessive pollution:

.. literalinclude:: ../config/default.yaml
   :language: yaml
   :start-after: # --- section: primary ---
   :end-before: # --- section: emissions ---

This caps total nitrogen-phosphorus-potassium application globally, forcing efficient use.

Nitrogen Use Efficiency (NUE)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Crop-specific fertilizer requirements (in ``data/crops.csv``) implicitly include NUE (currently mock data). More efficient crops (legumes, which fix nitrogen) require less fertilizer.
