.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Water resources
===============

Irrigation water is a primary resource in GLADE, tracked from a regional supply
through to the beneficial evapotranspiration that irrigated crops require. This
chapter is the canonical reference for the water representation: the supply
chain, the three water quantities and which constraint each sits on, the source
bands (surface, renewable groundwater, non-renewable groundwater), the scarcity
and depletion accounting, and the consumption-basis efficiency link. Rainfed
("green water") production carries no water constraint -- only blue-water
consumption is characterised.

Supply chain
------------

Water flows from a single free global source, through a tiered regional supply
that carries the scarcity and groundwater signals, into a per-region
*consumption* pool, and finally through an efficiency delivery link to a *field*
bus that irrigated crops draw from:

.. code-block:: text

   water:source
      --(tiered supply: CF -> scarcity, groundwater-band routing)-->
   water:{region}                    <- consumption pool (C)
      --(irrigate:{region}, efficiency = eta_c)-->
   water_field:{region}              <- beneficial/applied water (E)
      <--(crop production link, efficiency2 = -E)-- land

The tiered supply and the pool are on a **consumption** basis; the crop link
consumes the crop's net irrigation requirement (beneficial evapotranspiration).
The delivery link bridges the two -- see `The three water quantities`_.

The three water quantities
--------------------------

Three distinct volumes describe irrigation, and conflating them is the most
common source of error. GLADE keeps them separate:

.. csv-table::
   :header: Symbol, Quantity, Basis, Data source, Global

   :math:`E`, "Beneficial ET = net irrigation requirement", "crop demand", "GAEZ ``RES05-WDC``", "~596 km3/yr"
   :math:`C`, "Consumption (pool / scarcity / depletion)", "supply + impacts", "WaterGAP ``pirruse``", "~1223 km3/yr"
   :math:`W`, "Withdrawal (reported only)", "reporting", "Huang (corrected)", "~2334 km3/yr"

* :math:`E` is what the crop physically needs -- the water that leaves the field
  as beneficial transpiration. It is the coefficient on the crop-production
  link's water leg.
* :math:`C` is what the basin actually loses to agriculture, including
  non-beneficial consumption (canal and soil evaporation that leaves the basin
  as vapour). The regional pool, the AWARE scarcity characterisation, and the
  groundwater bands are all sized on :math:`C`. WaterGAP's ``pirruse`` (total
  irrigation consumption) is a consumption quantity and is source-agnostic (it
  already includes groundwater).
* :math:`W` is the volume physically pumped or diverted (Huang et al. 2018
  [huang2018]_, corrected). The difference :math:`W - C` is **return flow** --
  water that runs off or percolates back and is reused downstream. GLADE never
  withdraws it, so it is not modelled explicitly; :math:`W` is reported for
  comparison only (see `Return flow`_).

The two efficiencies relating them are the consumptive efficiency
:math:`\eta_c = E / C \approx 0.49` and the consumed fraction
:math:`C / W \approx 0.58`.

The efficiency delivery link
----------------------------

Because the pool is on consumption :math:`C` while the crop needs :math:`E`, a
per-region delivery link ``irrigate:{region}`` sits between them with efficiency
:math:`\eta_c`:

.. math::

   \text{(pool draw)}\ C = \frac{E}{\eta_c}, \qquad
   \text{(delivered)}\ E = \eta_c \cdot C .

Per unit :math:`E` delivered to the field bus, the link draws
:math:`C = E / \eta_c` from the pool. For :math:`\eta_c < 1` the difference is
non-beneficial consumption and simply vanishes (it is genuinely lost to the
atmosphere, not returned); for :math:`\eta_c > 1` the region deficit-irrigates
(see below) and one unit of consumption covers more than one unit of nominal
requirement. This puts every supply-side quantity on the correct consumption
basis while keeping the crop coefficient at its physical net requirement.

:math:`\eta_c` is calibrated per region at build time so the baseline
reproduces observed consumption:

.. math::

   \eta_c(r) = \operatorname{clip}\!\left(
       \frac{E_\text{baseline}(r)}{C_\text{anchor}(r)},\ \eta_\text{min},\ \eta_\text{max}
   \right),
   \qquad
   \eta_c(r) \ge \frac{E_\text{baseline}(r)}{\text{pool}(r)} ,

where :math:`E_\text{baseline}(r)` is the model's own baseline irrigated area
times net requirement (summed over the region's crop-production links, single
and multi-cropping) and :math:`C_\text{anchor}(r)` is the observed irrigation
consumption (``region_agri_consumption.csv``): WaterGAP's total irrigation
consumption ``pirruse``, the same simulation, basis and reference window as
the supply envelope, so the calibrated baseline draw is consistent with the
supply split by construction. The clip to
:math:`\eta_\text{min}` guards against mistaking a data mismatch for real
inefficiency. Values **above 1 encode deficit irrigation**: in regions where
the GAEZ full requirement exceeds observed consumption (India, Pakistan,
Thailand, Sudan), real irrigation delivers less than the yield-maximising
requirement, so the delivery link stretches each unit of pool consumption
across :math:`\eta_c > 1` units of nominal requirement -- the baseline then
draws the observed consumption instead of the (unobserved) full requirement,
which would otherwise be forced into groundwater mining.
:math:`\eta_\text{max}` bounds the ratio where the consumption anchor is
unreliably small and marginal irrigation would become nearly free. Where
:math:`\eta_c > 1` each unit of pool consumption still satisfies several units
of nominal requirement, so under water pricing marginal irrigation in
deficit-irrigated regions is comparatively cheap; results that hinge on
irrigation expanding there warrant a sensitivity check on ``eta_max``.
Finally,
the floor at :math:`E_\text{baseline} / \text{pool}` guarantees the calibrated
baseline draw never exceeds the region's pool (in overexploited basins the pool
is clipped below observed consumption). Regions where the clip or floor binds
are logged as data-quality diagnostics -- they are the overexploited basins.

The :math:`\eta_\text{min}` clip has a known blind spot in the opposite
direction from deficit irrigation: **humid paddy regions** -- southern China
above all by volume, with Korea, Japan and parts of Southeast Asia -- where
the GAEZ *net* requirement of irrigated rice is near zero (rainfall covers crop
ET) while WaterGAP's paddy consumption includes ponding and percolation losses.
With :math:`E_\text{baseline} \approx 0` the baseline draws (almost) no water
regardless of :math:`\eta_c`, so the observed consumption (and its small
groundwater mining) cannot emerge there. Similarly, regions clipped at
:math:`\eta_\text{min}` draw below their consumption anchor; Spain is the
notable case where this happens in a genuinely scarce basin. Both are
requirement-basis mismatches between GAEZ and WaterGAP, accepted as residual
error rather than patched with region-specific factors. The missed volume is
a noticeable share of global irrigation consumption, but since it sits mostly
in humid, low-CF basins it is a much smaller share of baseline *scarcity*
(Spain being the exception). The flip side is conservative: water savings
from paddy water management are outside the model's reach, so
scarcity-reduction results cannot overclaim them.

Surface availability (WaterGAP envelope)
----------------------------------------

AWARE's availability is basin *river discharge*, which misstates the surface
water accessible to irrigation in two ways. Its **volume** counts through-flow
discharge as divertible: in the Texas High Plains (Ogallala), AWARE reports a
pool ~100 times the surface water WaterGAP's detailed allocation supplies, so
the model draws free "surface" water where irrigation in reality mines a fossil
aquifer. Its **timing** is unregulated discharge seasonality: rivers peak with
the monsoon or snowmelt, while real delivery is shifted into the irrigation
season by reservoirs -- WaterGAP's ``histsoc`` runs operate every GRanD
reservoir >= 0.5 km3 (Hanasaki scheme), so the monthly profile of its irrigation
surface consumption is regulated, demand-timed delivery. Keeping AWARE's
discharge timing strands that delivery in the wet months and overstates
dry-season mining (globally ~265 km3/yr).

GLADE therefore keeps AWARE's scarcity structure -- the per-basin CF curve --
but sets surface volume and timing from WaterGAP's monthly climatological
irrigation surface consumption (:math:`\text{pirruse} - \text{pirrusegw}`,
ISIMIP3a WaterGAP 2.2e). The builder overlays that 0.5-degree WaterGAP field
directly with every (model-region, AWARE-basin) intersection. Each regional
total is conserved exactly, but its within-region basin split follows
WaterGAP's grid-cell delivery rather than AWARE basin area. Where WaterGAP
reports little surface (the Ogallala), the surface tiers shrink toward zero and
the residual demand draws groundwater; where irrigation is genuinely surface-fed
(California's Central Valley), the pool is largely retained but re-timed into
the irrigation season.

AWARE basin-months with no agricultural pool are already over-allocated: their
AMD is non-positive after irrigation is restored. WaterGAP delivery mapped to
such a cell remains available, but receives AWARE's maximum CF of 100 instead
of being reassigned to an unrelated lower-scarcity basin. A rare regional
WaterGAP residual with no AWARE-basin intersection is likewise retained on an
explicit CF-100 tier. The WaterGAP surface field is built by
``build_region_watergap.py``; its basin overlay and AWARE tier construction are
applied in ``build_region_water_aware.py``.

The division of labour between the two datasets is deliberate: **WaterGAP
defines every volume** -- the surface envelope, the groundwater bands, the
irrigation-consumption anchor for :math:`\eta_c` and the mining ceiling -- all
from one simulation (ISIMIP3a ``histsoc``) and one basis (consumption). Surface
delivery and its consumption anchor use the AWARE-aligned 1990-2019 reference;
the storage-decline depletion trend uses 2000-2019. **AWARE contributes only
the scarcity valuation** (the CF curve, a function of ``amd0``) and its native
basin geometry. Mixing volume sources would reintroduce cross-dataset
inconsistencies between what the baseline draws and what the envelope supplies.

Limits of the hybrid metric
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The WaterGAP surface envelope is the modelled potential irrigation consumption
allocated to surface water in the historical ``histsoc`` run. It is the best
available representation here of regulated, crop-timed delivery, but it is not
an endogenous natural-water-resource curve. The model therefore asks how the
food system can reorganize within the observed WaterGAP delivery pattern, not
how it would redesign reservoirs, canals or inter-basin transfers.

The default WaterGAP delivery window now matches AWARE2.0's 1990-2019 scarcity
reference; the groundwater-storage depletion trend remains deliberately recent
(2000-2019). Replacing AWARE capacity with WaterGAP delivery improves physical
allocation but does not recalculate AWARE's hydrology or re-anchor the CF curve
to that delivery. In addition, a native basin that crosses model regions has one
independent curve per region; the LP does not couple simultaneous drawdown across
those regions. An exact treatment would make the native basin, rather than the
model region, the shared water-supply node and would be a separate structural
model change.

Temporal resolution (intra-year periods)
----------------------------------------

Physical basin availability is not the surface water a crop can actually use:
monsoon-month runoff cannot serve a dry-season crop without storage. Summing a
year's availability lets wet-season surplus subsidise the dry season and erases
the temporal mismatch that drives real groundwater mining. Rather than baking a
seasonal cap into a scalar, the model resolves supply and demand at
``water.temporal_resolution`` (a structural divisor of 12): the year is split
into :math:`T` equal periods and each is balanced in the LP.

- ``build_region_water_aware.py`` emits the convex scarcity curve per region
  *and month*; ``compose_water_supply.py`` groups whole months into the
  :math:`T` periods (month :math:`m \to \lfloor (m-1) T / 12 \rfloor`) and
  re-merges the monthly curves into one convex curve per region-period.
- Each region-period gets its own water bus ``water:{region}:p{p}``; the tier
  capacities cap that period's surface draw.
- Every irrigated crop's net requirement is split across the periods by the
  observed crop calendar (see below), so a monsoon crop competes for wet-season
  water and a winter crop for dry-season water on the
  ``water_field:{region}:p{p}`` buses.

A period whose surface cannot meet the demand landing in it draws groundwater
(mining) endogenously; period surplus goes undispatched (there is no
inter-period surface storage link -- reservoir regulation is instead imported
exogenously through WaterGAP's monthly delivery profile, see above).
:math:`T=1` recovers the annual model (no seasonal binding); :math:`T=12` is the
faithful monthly model; :math:`T=4` (quarterly) captures wet/dry seasonality at
a fraction of the solve cost. Cost scales ~linearly in :math:`T` on the water
side of the model.

**The shipped default is** :math:`T=1`, which is cheap and adequate wherever
water is not the object of study -- but be clear about what it buys. At
:math:`T=1` a region's whole annual pool is available to every season, which is
exactly the wet-season-subsidises-dry-season averaging this design exists to
remove. The practical consequence is that **the groundwater bands go nearly
inert**: surface alone covers demand almost everywhere, so reported depletion
falls to near zero. That near-zero is an artefact of the resolution, not a
finding. Any study about water, irrigation or groundwater should raise
:math:`T` (4 is a reasonable compromise).

.. note::

   Above :math:`T=4` a crop-production link crosses ten ports (:math:`T=6`
   reaches ``bus11``, :math:`T=12` reaches ``bus17``). PyPSA resolves numeric
   ``at_port`` labels positionally against a lexicographically sorted port list,
   so at ten or more ports those labels silently select the wrong buses. Filter
   statistics by ``bus_carrier``, never by numeric ``at_port``.

The accumulated scarcity total itself grows with :math:`T`: finer resolution
exposes dry-season draws to the high monthly CFs that annual averaging smooths
away. Absolute scarcity levels are therefore only comparable between runs at
the same temporal resolution; cross-scenario comparisons should hold :math:`T`
fixed and lean on relative changes.

Demand calendar (MIRCA-OS, retimed to WaterGAP)
-----------------------------------------------

Placing demand *when* it actually occurs matters as much as placing supply. The
GAEZ growing seasons are the yield-maximising potential calendar, which
systematically disagrees with observed cropping calendars in the major irrigated
systems (the Indus, the Nile, the Gangetic plain) -- so GAEZ-timed demand lands
in months WaterGAP does not deliver surface water and is covered by groundwater
mining instead. GLADE therefore places irrigation demand by the **observed**
calendar: ``build_mirca_crop_calendar`` aggregates the MIRCA-OS 2015 monthly
irrigated growing-area grids to per-(region, crop) monthly demand shares. A
calendar-only supplement mapping (``mirca_os_calendar_supplement.csv``) adds the
MIRCA classes excluded from the multi-cropping concordance (sugar cane, pulses,
fodder) so those large irrigators are also placed by observed timing.

Growing-*area* months are still not requirement months: within a season the net
irrigation requirement follows evapotranspiration minus effective precipitation
-- it collapses during the monsoon and peaks in the dry shoulder months --
while the area profile weights every growing month equally (including dormant
winter-wheat months). The shares are therefore **retimed by iterative
proportional fitting** to WaterGAP's monthly irrigation requirement
(``pirruse``, the same simulation and basis as the supply envelope): per region,
the crop x month prior (area shares weighted by each crop's annual irrigation
water) is scaled so that region-month totals match the WaterGAP monthly shape
while each crop's annual total and the structural zeros of its observed season
are preserved exactly -- wheat shifts within its rabi window but never into the
monsoon.

Both the single-crop links (``build_model.crops``) and the multi-cropping
cycles (``build_multi_cropping``) bin the retimed shares into the :math:`T`
periods. Where MIRCA has no observation for a (region, crop) the GAEZ growing
season is the fallback. The 2015 vintage is used deliberately -- the 2020
MIRCA-OS calendar misplaces the northwest-India wheat belt into the monsoon
window (see :doc:`data_sources`).

Source bands
------------

With ``water.supply.groundwater`` two groundwater ``source`` bands expand the
supply envelope so that mining emerges endogenously wherever surface falls short
of demand (it is no longer capped at renewable availability). Unlike surface,
which is period-bound, **groundwater is an annual per-region resource**: an
aquifer integrates recharge over the year and can be pumped in any period. Each
region therefore gets a ``groundwater:{region}`` bus, fed by the two annual bands
and distributed to every period's water bus by free delivery links, so a dry
period can draw the whole year's recharge:

.. csv-table::
   :header: source, Meaning, Sizing (annual, per region), Scarcity / impact

   ``renewable`` (surface), "Surface + recharged blue water", "That period's convex surface curve (period-bound)", "AWARE CF -> ``impact:water_scarcity``"
   ``groundwater_renewable``, "Recharged groundwater abstraction", ":math:`\max(\text{pirrusegw} - \text{mined},\ 0)`", "AWARE CF + tally on ``impact:groundwater_renewable``"
   ``groundwater_nonrenewable``, "Mined (depleting) groundwater", ":math:`\text{ceiling\_factor} \times C` (generous, non-binding)", "Mined volume -> ``impact:groundwater_depletion``"

``compose_water_supply.py`` writes the surface tiers (per region-period) to
``region_water_tiers.csv`` and the annual groundwater bands (per region) to
``region_groundwater_bands.csv``. The renewable band is the WaterGAP volume,
priced at the region's scarcest surface CF so it is drawn after surface but
before mining. The non-renewable band's capacity is a deliberately generous
ceiling (``water.supply.groundwater_ceiling_factor`` times annual consumption):
the volume actually mined is set endogenously by how far surface plus renewable
groundwater fall short of demand -- the pumping cost keeps the draw minimal, so
the ceiling itself does not bind. The groundwater sizing fields come from
WaterGAP 2.2e via ``build_region_watergap.py`` (see :doc:`data_sources`): mining
is the groundwater-storage decline and renewable groundwater is the recharged
part of irrigation groundwater consumption (:math:`\text{pirrusegw}`). The
subtraction crosses sectors -- the storage decline reflects all users, so heavy
municipal or industrial mining also shrinks the irrigation renewable band (a
conservative attribution). There is
no endogenous inter-period surface storage; current reservoir operation enters
through the WaterGAP monthly surface profile, so mining reflects the deficit
under today's regulation. With ``water.supply.groundwater: false`` there are no
groundwater bands and the supply is surface only.

Scarcity accounting
-------------------

The AWARE characterisation factor (CF, m3 world-equivalent per m3 consumed)
measures how scarce a basin's water is. Both CF-carrying bands (``renewable``
and ``groundwater_renewable``) accumulate their drawn volume times the tier CF
onto the global ``impact:water_scarcity`` store:

.. math::

   \text{scarcity} = \sum_{\text{CF tiers } t} \mathrm{CF}_t \cdot \text{draw}_t
   \quad [\text{Mm}^3\ \text{world-eq}].

The convex, demand-dependent CF curve is reconstructed from AWARE's marginal CF
in ``build_region_water_aware.py`` (as the model draws down a basin's pool its
AMD falls and the CF rises), discretised into tiers, and drawn low-CF-first via
a negligible merit-order regularizer. At solve time the accumulated scarcity can
be priced (``water_scarcity.price``) or capped
(``water_scarcity.cap_mm3_world_eq``). A cap alone is porous when
``water.supply.groundwater`` is on: ``nonrenewable_cf`` applies only under
scarcity *pricing*, so with mining neither priced nor capped the LP can meet
the cap by substituting CF-free mining, deterred only by the pumping cost.
Combine the cap with a ``groundwater_depletion`` price or cap for a closed
sweep (the solve logs a warning otherwise).

Groundwater depletion accounting
--------------------------------

Non-renewable groundwater (``groundwater_nonrenewable``) does not carry a CF;
instead each unit drawn accumulates 1:1 on the ``impact:groundwater_depletion``
store (Mm3 mined), and the band carries a small real pumping cost
(``water.supply.pumping_cost_usd_per_m3``) that both adds realism and orders it
last in the merit order (drawn only once a region's renewable water is
exhausted). At solve time depletion can be priced
(``groundwater_depletion.price``) or capped (``groundwater_depletion.cap_mm3``,
e.g. down to zero to ask how the food system reorganizes without mining).

AWARE covers renewable water only and excludes fossil stocks, so under
scarcity pricing alone the CF-free mined band would become the cheapest source
wherever the scarcity charge exceeds the pumping cost, and "relief" would be
substitution into fossil groundwater rather than conservation.
``water_scarcity.nonrenewable_cf`` therefore charges each mined m3 at
``nonrenewable_cf * water_scarcity.price``. The default 100 -- AWARE's
demand-exceeds-availability cutoff plus a non-renewability premium -- is a
precautionary anchor pricing a mined m3 at least at the scarcity of the
exhausted renewable water it displaces. Set it to ``null`` to study depletion
as a separate axis via the ``groundwater_depletion`` options (enabling both
pricings together is an error).

The renewable-groundwater band additionally tallies its drawn volume on
``impact:groundwater_renewable`` (via a ``bus3`` output) purely for reporting
and as a hook for future policy; it does not affect the baseline solve.

Return flow
-----------

Return flow -- withdrawal minus consumption, reused downstream in reality -- is
handled **implicitly**. On a consumption basis it is simply never withdrawn: the
consumption pool already excludes it, and the model only ever draws the
consumption :math:`C`. Modelling it explicitly would only be necessary for
withdrawal-based accounting or explicit upstream-downstream reuse (a possible
future extension with basin topology). The consumption basis also keeps a future
drip-irrigation feature honest -- efficiency gains are credited only for
reducing consumption, not for reducing withdrawal that was returning anyway (the
irrigation-efficiency paradox).

Irrigation efficiency and technology
------------------------------------

The single ``irrigate:{region}`` delivery link generalises to parallel
per-technology links (flood, drip) with their own efficiencies and capital
costs, letting the model invest in more efficient irrigation to draw less
:math:`C` per unit :math:`E`. That technology-investment feature is a planned
extension; the current single-link formulation is the seam it slots into without
reworking the pool, scarcity, or bands.

Model components
----------------

.. csv-table::
   :header: Component, Name, Carrier, Role

   Bus, ``water:source``, ``water_source``, "Free global water source"
   Bus, ``water:{region}:p{p}``, ``water``, "Regional consumption pool (per period)"
   Bus, ``water_field:{region}:p{p}``, ``water_field``, "Beneficial/applied water for crops (per period)"
   Bus, ``groundwater:{region}``, ``groundwater``, "Annual per-region aquifer pool (when supply.groundwater)"
   Bus, ``impact:water_scarcity``, ``water_scarcity``, "Accumulated AWARE scarcity"
   Bus, ``impact:groundwater_depletion``, ``groundwater_depletion``, "Accumulated mined volume"
   Bus, ``impact:groundwater_renewable``, ``groundwater_renewable``, "Renewable-GW volume tally"
   Link, ``supply:water:{region}:p{p}:t{n}``, ``water_supply``, "Tiered surface supply (source, CF)"
   Link, ``supply:groundwater:{region}:{source}``, ``water_supply``, "Annual groundwater bands (when supply.groundwater)"
   Link, ``deliver:groundwater:{region}:p{p}``, ``groundwater_delivery``, "Annual aquifer -> period pool (free)"
   Link, ``irrigate:{region}:p{p}``, ``irrigation_delivery``, "Consumption -> field (eta_c)"
   Store, ``store:impact:water_scarcity``, ``water_scarcity``, "Priced/capped at solve time"
   Store, ``store:impact:groundwater_depletion``, ``groundwater_depletion``, "Priced/capped at solve time"
   Store, ``store:impact:groundwater_renewable``, ``groundwater_renewable``, "Reporting only"

Units: water volumes are Mm3 (10^6 m3); scarcity is Mm3 world-equivalent;
depletion is Mm3 mined. See :doc:`configuration` for the ``water`` config block
and the solve-time levers, :doc:`workflow` for the build rules, and
:doc:`analysis` for the ``water_metrics`` outputs.

References
----------

.. [huang2018] Huang, Z., Hejazi, M., Li, X., Tang, Q., Vernon, C., Leng, G., Liu, Y., Doll, P., Eisner, S., Gerten, D., Hanasaki, N., and Wada, Y. (2018). Reconstruction of global gridded monthly sectoral water withdrawals for 1971-2010 and analysis of their spatiotemporal patterns. *Hydrology and Earth System Sciences*, 22, 2117-2133. https://doi.org/10.5194/hess-22-2117-2018
