.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Current Diets
=============

Overview
--------

The model represents current consumption patterns by combining three
intake datasets — **GDD-IA**, **GBD**, and **NHANES** (for the USA) —
with item-level food supply data from FAOSTAT for within-group
disaggregation. The pipeline produces a single per-country, per-food
baseline diet whose mass basis is aligned with what the model's food bus
delivers after applying food loss and waste. The baseline diet serves
several roles:

* **Health impact assessment**: dietary risk exposure for the burden of
  disease attributable to current diets.
* **Optimization reference**: comparison point for optimized diets and,
  optionally, an equality constraint when ``enforce_baseline_diet`` is
  enabled.
* **Calibration**: anchors the consumer-utility piecewise blocks
  (:doc:`consumer_values`) and the production-stability L1 calibration
  (:doc:`calibration`).

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/baseline_diet_by_region.png
   :alt: Baseline diet composition by world region
   :width: 100%
   :align: center

   Population-weighted mean food group consumption (g/person/day) by UN
   M49 macro-region, showing how dietary patterns vary across world
   regions.

.. figure:: https://github.com/Sustainable-Solutions-Lab/GLADE/releases/download/doc-figures/baseline_diet_by_food.png
   :alt: Baseline diet breakdown by individual foods
   :width: 100%
   :align: center

   Global population-weighted mean consumption (g/person/day) broken
   down by individual foods within each food group.

Data Sources
------------

**Global Dietary Database — Integrated Assessment (GDD-IA)**
  * **Provider**: Marco Springmann (University of Oxford / UCL). GDD-IA
    combines the Global Dietary Database (GDD) survey-based intake
    estimates with FAOSTAT Food Balance Sheets and applies a
    multi-source caloric-intake normalisation procedure to produce
    consistent per-country food and energy intake estimates.
  * **Status**: Pending publication; available upon personal request
    from Marco Springmann. Will be re-licensed under CC-BY-NC on
    release. The public release will then become GLADE's default
    input; until then, a temporary mode that lets GLADE run without
    GDD-IA is under development.
  * **Coverage**: ~185 countries, per-country mean dietary intake at
    the reference year, reported in parallel grams/day and kcal/day for
    every food category.
  * **Role**: Primary source of per-country food-group totals for all
    food groups except the GBD-anchored risk groups (see below).

**Global Burden of Disease (GBD) 2019 dietary risk exposure**
  * **Provider**: Institute for Health Metrics and Evaluation (IHME)
    [Brauer2024]_
  * **Coverage**: country-level mean intake (g/day) for the GBD dietary
    risk factors, adults 25+.
  * **Role**: *optionally* anchors the **risk-factor** food groups
    (fruits, vegetables, whole_grains, legumes, nuts_seeds, red_meat) so
    the baseline lines up with the same exposure basis the GBD
    relative-risk functions were calibrated against. Controlled by
    ``diet.anchor_groups_to_gbd`` (see
    :ref:`current-diets-gbd-anchoring`). When anchoring is off, these
    groups use the GDD-IA/FAOSTAT estimate like every other group and
    this dataset is not needed on disk.

**NHANES — What We Eat in America / FPED**
  * **Provider**: USDA ARS / CDC NHANES
  * **Coverage**: United States; population-mean intake per food group
    derived from the FPED demographic table.
  * **Role**: USA-only override for every food group it covers.

**FAOSTAT FBS + QCL**
  * **Provider**: FAO Statistics Division
  * **Role**: Item-level supply (FBS) drives **within-group**
    disaggregation of food-group totals into per-food consumption.
    Production statistics (QCL) resolve shared FBS items (e.g. several
    millet species under one FBS code) and weight module-pool
    projections (see :ref:`current-diets-step2`). FBS supply also serves
    as the **anchor source** for the foods in
    ``diet.fbs_override_foods`` (meats, eggs, yam, coffee, cocoa).

Weight Conventions
~~~~~~~~~~~~~~~~~~

GDD-IA reports intake "as consumed" (cooked weight for cereals and
meats, fresh weight for fruits and vegetables). The pipeline derives the
food-group **mass** values that downstream rules consume in the model's
basis, so no further conversion is needed when reading
``dietary_intake.csv``:

* For most groups (cereals, vegetables, fruits, nuts/seeds, oil, sugar,
  legumes, poultry, eggs) the IA-reported grams are already close enough
  to the model basis to be passed through as-is.
* **Red meat** is inflated from cooked to raw retail mass by the
  configured ``diet.gdd_ia.cooked_to_raw`` factor (default 1.43, i.e.
  ``1/0.7``).
* **Dairy** mass is **derived from energy** at cow-milk density (0.607
  kcal/g) so the value is on a strict cow-milk-equivalent basis. All
  dairy subcategories reported by GDD-IA (fluid milk, yoghurt, cheese,
  condensed/evaporated, ice cream, butter, cream) are pooled by energy
  before the conversion.

GBD exposure is converted to the model basis at load time via
``diet.source_basis`` and ``diet.weight_conversion`` (cooked→dry for
``whole_grains`` and ``legumes`` at 0.45 and 0.40; cooked→fresh for
``red_meat`` at 1.43). NHANES values are intake-based and pass through
unchanged.

Units in the merged ``dietary_intake.csv`` distinguish ``g/day (fresh
wt)`` from ``g/day (milk equiv)`` for dairy and ``g/day (refined sugar
eq)`` for sugar.

.. _current-diets-gbd-anchoring:

GBD Anchoring of Risk-Factor Groups (optional)
----------------------------------------------

For the six GBD dietary **risk-factor** groups (``fruits``,
``vegetables``, ``whole_grains``, ``legumes``, ``nuts_seeds``,
``red_meat``) the per-country food-group total can optionally be taken
from GBD dietary-exposure intake instead of the GDD-IA/FAOSTAT estimate.
The point of anchoring is **basis consistency with the health module**:
the GBD relative-risk dose-response curves are calibrated against GBD's
own intake exposure, so a GBD-anchored baseline makes the model's
attributable-burden numbers directly comparable to GBD's.

Controlled by ``diet.anchor_groups_to_gbd``:

* ``match_health`` (default) -- follow ``health.enabled``. A health run
  anchors; a no-health run does not.
* ``true`` / ``false`` -- force anchoring on or off, decoupled from the
  health module.

When anchoring is on, the run needs the manually-downloaded IHME GBD
data (see :doc:`data_sources`); when off, it needs none of it.

When to turn it on
~~~~~~~~~~~~~~~~~~~

Turn anchoring **on** when you care about compatibility with GBD health
metrics -- i.e. whenever the health module is enabled, or when comparing
the model's diet-attributable disease burden against GBD estimates.
Otherwise the GDD-IA/FAOSTAT baseline (anchoring off) is a perfectly
reasonable diet and avoids the manual GBD download entirely; this is the
default for a no-health run.

How much does it change the baseline diet?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The switch is **not** cosmetic at the diet level. Across ~174 countries,
anchoring moves the per-country total of every risk-factor group, and in
about half of the affected country-group cells by more than 50 %
relative. The direction is systematic (values below are the mean
per-country shift, *anchored minus non-anchored*, g/day):

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Group
     - Mean shift (g/day)
     - Direction
   * - vegetables
     - -56
     - anchoring **lowers** vegetable intake (e.g. ~172 -> ~116 g/day mean)
   * - red_meat
     - -35
     - lowers in ~98 % of countries
   * - legumes
     - -15
     - lowers in ~94 % of countries
   * - nuts_seeds
     - -6
     - lowers in ~95 % of countries
   * - whole_grains
     - -4.5 (mixed)
     - large *relative* swings; GBD is often much higher where GDD-IA
       reports near-zero whole grain
   * - fruits
     - +2.4 (mixed)
     - roughly unchanged on average, country-specific either way

In words: the GBD-anchored ("health-on") baseline has systematically
**less** vegetables, red meat, legumes and nuts than the GDD-IA/FAOSTAT
("health-off") baseline, and a markedly different whole-grain profile.

Whether this matters for *physical* outcomes depends on the question.
From a global agricultural and land-use perspective the effect is
expected to be modest -- the shifts are partly redistributive within the
diet and are further absorbed by the production-stability calibration
(see :doc:`calibration`) -- though not strictly negligible. The reason
to anchor is primarily **health-metric compatibility**, not a large
expected change in land use or emissions.

.. admonition:: Caveat: the option is not *exactly* "only the six risk groups"

   Anchoring ``whole_grains`` to GBD's narrow whole-grain definition can
   discard cereal energy that GDD-IA reports more broadly. To preserve
   each country's cereal energy budget, a **cereal residual fix**
   reassigns that deficit to refined ``grain`` (see
   ``apply_cereal_residual_fix`` in ``estimate_baseline_diet.py``), and
   the kcal normalisation then treats refined ``grain`` as anchored too.
   So enabling anchoring also adjusts refined ``grain`` even though it is
   not itself a GBD risk factor. The fix only runs when ``whole_grains``
   is anchored, so it is inert when anchoring is off.

.. admonition:: Calibration coupling
   :class: warning

   The calibration artefacts under ``data/curated/calibration/`` are fit
   against a *specific* baseline diet. Two sets are committed: ``default``
   (anchoring off) and ``gbd-anchored`` (anchoring on, used by the
   health-enabled configs via ``calibration.source``). Changing
   ``diet.anchor_groups_to_gbd`` (or ``health.enabled``, which drives it)
   changes the baseline diet, so point ``calibration.source`` at the
   matching set -- the provenance check errors otherwise (see
   :doc:`calibration`).

GDD-IA to Food Group Mapping
-----------------------------

GDD-IA's food categories are mapped onto the model's food groups in
``workflow/scripts/prepare_gdd_ia_dietary_intake.py``. The mapping
covers every food group the model uses (``fruits``, ``vegetables``,
``starchy_vegetable``, ``legumes``, ``nuts_seeds``, ``oil``, ``sugar``,
``grain``, ``whole_grains``, ``red_meat``, ``poultry``, ``dairy``,
``eggs``, plus ``stimulants`` for downstream tea/coffee handling).
Categories that are out of scope for the model (alcohol, seafood,
spices, rendered animal fats, miscellaneous "other") are excluded from
food-group totals but their energy is tracked separately for the
kcal-normalisation step described below. Refined and whole-grain mass
are tracked separately so cereals can be split between the model's
``grain`` and ``whole_grains`` groups; plantain is routed to
``starchy_vegetable``; and all red-meat subcategories (including
processed) are folded into ``red_meat`` so the consumption side stays
consistent with FAOSTAT slaughter-volume animal production.

A more detailed category-level mapping will be added once GDD-IA is
published.

Country Coverage
----------------

GDD-IA covers ~185 countries. For a handful of territories without
separate IA estimates the pipeline copies values from a configured
proxy. The built-in proxies live in
``workflow/scripts/prepare_gdd_ia_dietary_intake.py`` and can be
extended via ``diet.gdd_ia.country_proxies`` in the config:

.. list-table::
   :header-rows: 1
   :widths: 30 25 45

   * - Missing country
     - Proxy
     - Rationale
   * - Afghanistan (AFG)
     - Iran (IRN)
     - Persian/Pashtun dietary similarity.
   * - American Samoa (ASM)
     - Samoa (WSM)
     - Pacific islands; geographic proximity.
   * - Brunei (BRN)
     - Malaysia (MYS)
     - Regional similarity.
   * - Bhutan (BTN)
     - Nepal (NPL)
     - Himalayan diet.
   * - Eritrea (ERI)
     - Ethiopia (ETH)
     - Existing convention.
   * - Equatorial Guinea (GNQ)
     - Cameroon (CMR)
     - Central African neighbour.
   * - French Guiana (GUF)
     - France (FRA)
     - French overseas territory.
   * - Palestine (PSE)
     - Jordan (JOR)
     - Regional similarity.
   * - Puerto Rico (PRI)
     - United States (USA)
     - US territory.
   * - Somalia (SOM)
     - Ethiopia (ETH)
     - Existing convention.
   * - South Sudan (SSD)
     - Sudan (SDN)
     - Regional and historical ties.
   * - Taiwan (TWN)
     - China (CHN)
     - Regional similarity.

Data Processing
---------------

The diet pipeline runs in three preparation stages followed by the
baseline-diet estimation:

1. **Prepare GDD-IA** (``prepare_gdd_ia_dietary_intake``): reads the
   parallel grams and kcal CSVs, maps GDD-IA's food categories to the
   model's food groups, derives the per-food-group mass in model basis
   (pooling all dairy subcategories by energy, applying the cooked-to-
   raw meat inflation), and emits two files:

   * ``gdd_ia_dietary_intake.csv`` — per-(country, food group) intake
     (g/day) at age = ``All ages``.
   * ``gdd_ia_kcal_target.csv`` — per-country kcal accounting: the
     total dietary energy, the out-of-scope subtotal, the in-scope
     target (total minus out-of-scope), and the refined / whole-grain
     cereal energy split. Consumed by the cereal residual fix and the
     kcal-normalisation step in ``estimate_baseline_diet``.

2. **Prepare NHANES** (``prepare_nhanes_dietary_intake``): parses the
   USDA FPED demographic-table PDF for the configured cycle and emits
   USA-only per-food-group intake with the FAOSTAT butter top-up,
   cured-meat fold, and fruit-juice projection (see :doc:`data_sources`
   for the FPED specifics).

3. **Merge sources** (``merge_dietary_sources``): NHANES overrides
   GDD-IA for the (country, item) pairs it covers; the merged file
   ``dietary_intake.csv`` is the input to ``estimate_baseline_diet``.

The GBD risk-exposure data is processed independently by
``prepare_gbd_food_group_intake`` into
``gbd_food_group_intake.csv`` and is read directly by
``estimate_baseline_diet`` for the GBD-anchored groups.

Output Format
~~~~~~~~~~~~~

``dietary_intake.csv``:

.. code-block:: none

   unit,item,country,age,year,value
   g/day (milk equiv),dairy,USA,All ages,2018,...
   g/day (fresh wt),fruits,USA,All ages,2018,...
   ...

* ``unit``: ``g/day (fresh wt)``, ``g/day (milk equiv)`` (dairy), or
  ``g/day (refined sugar eq)`` (sugar).
* ``item``: food group name.
* ``country``: ISO 3166-1 alpha-3 code.
* ``age``: ``All ages`` for GDD-IA rows; NHANES uses the configured
  ``diet.baseline_age`` literal (the FPED single population-mean row).
* ``year``: reference year.
* ``value``: mean daily intake in grams per person, in model basis.

.. _baseline-diet-estimation:

Baseline Diet Estimation
------------------------

The dietary intake stage produces food-group-level totals. The
optimization model operates at the level of individual foods, so
``workflow/scripts/estimate_baseline_diet.py`` disaggregates the totals
into per-(country, food) consumption estimates and applies a small
number of consistency fixes:

Step 1: Food group totals
~~~~~~~~~~~~~~~~~~~~~~~~~

For groups in ``health.risk_factors`` (currently ``fruits``,
``vegetables``, ``whole_grains``, ``legumes``, ``nuts_seeds``,
``red_meat``) the per-country total is taken **from GBD when GBD reports
a value** and falls back to the merged GDD-IA/NHANES value otherwise.
GBD strictly takes precedence on these groups — no averaging — so the
baseline is on the same intake basis the GBD relative-risk functions are
calibrated against. All other groups use GDD-IA (or NHANES for the USA).

GBD exposure is converted to the model's basis at load time, per
food-group, using ``diet.source_basis`` plus per-(source, country,
food_group) overrides from ``data/curated/diet_source_basis_overrides.csv``
and the conversion tables in ``diet.weight_conversion``. The script also
logs cross-validation metrics: median and range of the GDD-IA/GBD ratio
across countries for every risk group, and GBD's milk exposure as a
cross-check on the dairy total.

Step 1b: Cereal residual fix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

GBD's ``whole_grains`` risk factor is defined narrowly (dry whole-grain
flour). GDD-IA's ``whole_grains`` is broader (any product with
substantial whole-grain content). When Step 1 anchors ``whole_grains``
to GBD, ~250 kcal/day of cereal energy can disappear from the country's
cereal budget. To preserve the cereal energy budget, the deficit is
**reassigned to refined ``grain``**:

.. math::

   \text{deficit\_kcal} = (\text{kcal}_{\text{whole\_grains}}^{\text{IA}}
                          + \text{kcal}_{\text{grain}}^{\text{IA}})
                          - g_{\text{whole, anchored}} \cdot k_{\text{whole, model}}

   \text{new}\ g_{\text{grain}} = \max(0, \text{deficit\_kcal}) / k_{\text{grain, model}}

The IA cereal kcal pool comes from ``gdd_ia_kcal_target.csv`` (basis-
aware), not from nutrition.csv per-group averages.

Step 1c: Anchor-aware kcal normalisation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For each country, the **unanchored** groups are scaled by a single
multiplicative factor so that total kcal across all groups lands on
the in-scope dietary-energy target from ``gdd_ia_kcal_target.csv``
(total energy minus the out-of-scope subtotal). GBD-anchored groups
and the refined-grain residual from Step 1b are held fixed. The factor
is clipped to ``[0.1, 5.0]`` to guard against pathological values;
the mean, std, and range of the factor across countries are logged.

.. _current-diets-step2:

Step 2: Within-group food shares
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once food-group totals are set, the algorithm determines how to
distribute each total across its constituent foods using **FAOSTAT FBS
item-level supply**. The shares pipeline (in ``build_within_group_shares``)
covers four resolution patterns:

**Direct (1:1) mapping.** A food that is the unique claimant of its FBS
item receives 100 % of that item's supply.

**Shared FBS item, QCL-resolved.** When several foods share an FBS item
(e.g. ``cowpea``, ``chickpea``, ``gram``, ``phaseolus-bean`` and
``pigeon-pea`` all map to FBS 2546 "Beans"), country-level FAOSTAT QCL
production data splits the shared supply between QCL buckets, and within
a bucket the default is an equal split. Two cases use explicit
within-bucket overrides:

* ``pearl-millet`` / ``foxtail-millet`` (both QCL "Millet"): a fixed
  80 / 20 global split based on literature production shares.
* ``dairy`` / ``dairy-buffalo`` (both FBS 2848): QCL items 882 and 951
  resolve the cow / buffalo split. The split is then post-processed by
  ``cap_buffalo_share_at_production`` to cap each country's buffalo
  share at its domestic buffalo production (buffalo milk has very
  limited international trade), with any excess share reassigned to
  cow dairy. Without the cap, GBD-anchored dairy intake exceeds
  domestic milk production in buffalo-heavy importers (PAK is the
  textbook case) and the production-share split over-allocates buffalo
  demand, surfacing as unrelievable buffalo shortage at solve.

**Module-pool projection.** For food groups whose modelled foods share
a GAEZ RES06 supply-side module, the demand-side attribution pools all
module-aligned FBS codes (both explicit and "Other"-style residuals) and
splits the pool across the modelled foods. Pooling matches the
supply-side attribution by construction: each modelled food's supply
comes from FAOSTAT direct area plus a share of the module's residual
raster area, so routing the explicit FBS supply (e.g. onion FBS 2602) to
one food on the demand side while supply spreads it across the module
would produce systematic within-group slack.

Each pool sub-projection carries a ``share_method`` that decides how to
allocate the pool across its modelled foods:

* ``"blend"`` — country/global production-share blend
  :math:`s_{c,f} = w \cdot s_{c,f}^{\mathrm{country}}
  + (1-w) \cdot s_f^{\mathrm{global}}` over FAOSTAT crop production
  (currently :math:`w=0.7` for all pools using this method).
* ``"frt_attribution"`` — per-(country, crop) shares read directly from
  the supply-side ``frt_area_attribution.csv``
  (``target_production_tonnes`` column), so the demand-side within-pool
  split mirrors the supply-side FRT attribution exactly. Used for the
  fruits FRT pool, where the supply side intentionally uses area-share
  (not production-share) weighting to avoid over-attributing residual
  area to high-yield fruits; the blend method on the demand side would
  drift from that choice.

.. list-table::
   :header-rows: 1
   :widths: 18 18 40 24

   * - Food group
     - Pooled FBS items
     - Projection foods
     - Share method
   * - ``vegetables``
     - 2602 "Onions", 2605 "Vegetables, Other"
     - ``onion``, ``cabbage``, ``carrot``
     - ``blend``
   * - ``starchy_vegetable``
     - 2534 "Roots, Other"
     - ``potato``, ``sweet-potato``, ``yam``, ``cassava``
     - ``blend``
   * - ``nuts_seeds``
     - 2551 "Nuts and products"
     - ``groundnut``, ``sesame-seed``, ``coconut``, ``sunflower-seed``
     - ``blend``
   * - ``fruits`` (BAN sub-projection)
     - 2616 "Plantains"
     - ``banana`` only
     - ``blend``
   * - ``fruits`` (FRT sub-projection)
     - 2611–2614 (citrus), 2617 "Apples", 2618 "Pineapples",
       2619 "Dates", 2625 "Fruits, other"
     - ``citrus``, ``mango``, ``watermelon``, ``apple``
     - ``frt_attribution``

For fruits the projection is split into two sub-projections so that the
demand-side attribution mirrors the GAEZ RES06 module split on the
supply side: banana and plantain share the GAEZ BAN raster (plantain
supply is therefore projected onto banana exclusively), while citrus /
mango / watermelon / apple jointly absorb the FRT raster plus
CROPGRIDS-backed apple.

Tomato (FBS 2601) and individually-itemised starchy vegetables and nuts
retain their explicit FBS supply in addition to any pool share they
receive: each has its own GAEZ raster on the supply side and its own
FBS item, so the explicit-route is already symmetric.

**Equal split fallback.** Where total FBS supply for a food group is
zero in a country, foods within the group are assigned equal shares.

.. note::

   The within-group share computation weights each food's FBS supply
   by its **edible portion** (FAO ``edible_portion_coefficient`` looked
   up via ``data/curated/foods.csv`` -> ``fao_edible_portion.csv``)
   before normalising. The GDD-IA group totals are on an edible-mass
   basis while FBS supply is reported on a fresh-whole-commodity
   basis, so splitting an edible-mass total by fresh-mass weights
   would over-allocate intake to low-edible-portion foods (plantain
   0.59, watermelon 0.62, citrus 0.72). The weighting is applied in
   both the direct-supply branch and the pooled-projection branch of
   ``build_within_group_shares``; in pooled projections the edible
   portion follows the recipient food (e.g. plantain FBS supply in the
   BAN sub-projection is redistributed to ``banana`` using banana's
   edible portion).

Step 3: Per-food consumption
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Per-food consumption is the product of the food-group total (post Steps
1b and 1c) and the within-group share:

.. math::

   c_{i,f} = T_{i,g(f)} \cdot s_{i,f}

As a validation check, the within-group sums are verified to match the
group totals to within 0.1 g/day (excluding foods that will be replaced
by FBS overrides in Step 4).

Step 4: FBS supply overrides
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For foods listed in ``diet.fbs_override_foods`` the Step-3 estimate is
replaced with an FBS-supply-anchored intake. The override formula is

.. math::

   c_{i,f} = \frac{S_{i,f} \cdot \sigma_{i,f} \cdot r_f \cdot 1000}{365}
            \cdot (1 - w_{i,g(f)})

where

* :math:`S_{i,f}` is the FAOSTAT FBS supply (kg/capita/year) for the
  food's FBS items (carcass-weight for meat);
* :math:`\sigma_{i,f}` is the within-FBS-item share (1.0 unless several
  override foods share an FBS code, in which case the supply is split
  between them by country-level QCL production weights — e.g. dairy /
  dairy-buffalo both map to FBS 2848);
* :math:`r_f` is the carcass-to-retail factor for meat (0.67 cattle,
  0.73 pig, 0.66 sheep, 0.60 chicken; 1.0 for non-meat foods);
* :math:`w_{i,g(f)}` is the country- and group-level consumer-waste
  fraction from ``processing/{name}/food_loss_waste.csv``.

Note that the override deducts only **consumer waste**, not
supply-chain loss: the FAOSTAT FBS "Food supply" element is already net
of production-side losses (``production − feed − seed − processing −
other − losses = food``). The :math:`(1-w)` factor lands the override on
the same post-FLW intake basis the model's ``food_processing`` and
``animal_production`` links deliver after applying their FLW
multipliers, so the diet mass-balances against the food bus.

.. admonition:: Why yam needs an override

   GDD-IA / GBD starchy-vegetable intake for sub-Saharan Africa is
   well below FAOSTAT food supply (e.g. Nigeria: GBD ≈ 70 g/day vs.
   FBS ≈ 700 g/day for starchy vegetables). Because yam production is
   almost entirely concentrated in West Africa, the within-group
   underestimate translates directly into a ~10× underestimate of yam
   demand. The within-group shares are correct — the problem is in the
   group total — so overriding yam consumption with FBS supply ensures
   the model's demand matches observed food availability.

.. _animal-source-selection:

.. admonition:: Why animal products use FBS, not survey intake

   For meats, poultry, and eggs the per-food intake is anchored to
   FAOSTAT FBS supply rather than the survey-disaggregated group total.
   Three reasons:

   1. **Survey bias on socially significant foods.** Self-reported food
      intake systematically over-reports red meat against slaughter-
      volume supply in many populations. GDD-IA harmonises survey data
      but does not reconcile against production. The combined intake
      total for red meat sat ~24 Mt/yr above what total world supply
      (production net of feed/non-food/exports, after post-loss and
      consumer waste) can deliver — physically impossible — and
      previously inflated the calibrated ``feed`` L1 cost ninefold
      because the deviation-penalty calibration was forced to fight
      intake-derived consumer values that were structurally above supply.

   2. **Trade is handled implicitly.** FBS supply per country already
      encodes ``production + imports − exports − feed − seed − non-food
      − stock_changes``, so country-level diet automatically reflects
      observed importer/exporter patterns. The model's trade hubs then
      only have to reproduce the observed FAOSTAT trade flows at solve
      time, instead of resolving a mismatch via expensive feed-deviation
      L1 penalties.

   3. **Same FAOSTAT backbone as production.** Baseline animal
      production is built from QCL element 5510 with the shared
      ``weight_conversion.carcass_to_fresh`` table applied. FBS aggregates the same QCL
      primary commodities at carcass-weight balance level. Anchoring
      both sides to FAOSTAT removes a class of unit/source mismatches
      that otherwise surfaces as residual slack after solve.

   **Dairy is intentionally excluded** from the override list. Its
   ``food_loss_waste`` convention is non-standard — the curated dairy
   override sets ``loss_fraction=0`` and ``waste_fraction=0.30``, where
   the 30 % lumps in non-food uses of raw milk (calf feed, processing,
   industrial) plus retail and consumer waste, because the model does
   not have an explicit non-food milk outlet. Under that convention the
   GDD-IA-based dairy total happens to mass-balance against the
   production-side ``QCL × 0.7`` delivered to the food bus. Switching
   dairy to an FBS override would break that balance.

Output
~~~~~~

``processing/{name}/baseline_diet.csv`` has one row per (country, food):

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Column
     - Description
   * - ``country``
     - ISO 3166-1 alpha-3 country code.
   * - ``food``
     - Model food name (e.g. ``banana``, ``rice-white``, ``cowpea``).
   * - ``food_group``
     - Food group to which the food belongs.
   * - ``consumption_g_per_day_intake``
     - Estimated daily consumption in grams per person, on
       **post-loss, post-waste consumer-eaten intake basis** — the same
       basis the food bus delivers after the build_model FLW multiplier
       (see :ref:`weight-bases`).

Rows are sorted by (country, food_group, food).

Downstream Uses
~~~~~~~~~~~~~~~

* **Baseline diet enforcement**: when
  ``config.validation.enforce_baseline_diet`` is true, the solver adds
  per-food, per-country equality constraints on food consumption links.
* **Within-group ratio fixing**: when
  ``config.food_groups.fix_within_group_ratios`` is true, foods within
  each group are constrained to keep their baseline proportions while
  group totals may vary.
* **Piecewise consumer utility calibration**: baseline per-food
  consumption and baseline food-equality duals together calibrate
  ``results/{name}/consumer_values/utility_blocks.csv``
  (:doc:`consumer_values`).
* **Health impact assessment**: baseline consumption feeds the
  population-attributable fraction calculation (:doc:`health`).

Workflow Integration
--------------------

**Snakemake rules** (see ``workflow/rules/diet.smk``):

* ``prepare_gdd_ia_dietary_intake``
* ``prepare_nhanes_dietary_intake``
* ``merge_dietary_sources``
* ``prepare_gbd_food_group_intake``
* ``prepare_faostat_fbs_items``
* ``prepare_food_loss_waste``
* ``estimate_baseline_diet``
* ``validate_baseline_diet`` and ``compare_baseline_diet_to_gbd``
  (consistency checks)

**Input data**:

* ``data/manually_downloaded/GDD-IA-intake_grams_{baseline_year}.csv``
* ``data/manually_downloaded/GDD-IA-intake_kcals_{baseline_year}.csv``
* ``data/manually_downloaded/IHME_GBD_2023_RISK_EXPOSURE_DIET_{1,2}/*.CSV``
* ``data/downloads/usda_fped/Table_1_FPED_MaleFemale_{cycle}.pdf``
* FAOSTAT FBS and QCL (auto-fetched via the FAOSTAT bulk API)

**Curated data files**:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - File
     - Purpose
   * - ``data/curated/faostat_food_item_map.csv``
     - Maps model foods to FAOSTAT FBS item codes for within-group share
       calculation.
   * - ``data/curated/faostat_food_qcl_resolution.csv``
     - Maps foods sharing an FBS item to QCL production codes for
       disambiguation.
   * - ``data/curated/food_groups.csv``
     - Food → food-group mapping.
   * - ``data/curated/food_basis.csv``
     - Per-food native mass basis (dry / fresh / cooked / milk-equiv).
   * - ``data/curated/diet_source_basis_overrides.csv``
     - Per-(source, country, food_group) basis overrides for the
       cross-source conversion.
   * - ``data/curated/nhanes_fped_mapping.csv``
     - FPED column → model food-group mapping and unit-conversion
       factors.
   * - ``data/curated/food_loss_waste_overrides.csv``
     - Per-(country, food_group) loss/waste overrides feeding
       ``food_loss_waste.csv``.

**Configuration parameters**:

* ``config.countries`` — list of countries.
* ``config.food_groups.included`` — food groups to process.
* ``config.baseline_year`` — reference year for GDD-IA and GBD.
* ``config.diet.baseline_age`` — age label written to NHANES rows
  (default ``"All ages"``).
* ``config.diet.fbs_override_foods`` — foods anchored to FBS supply.
  See :ref:`Why animal products use FBS <animal-source-selection>`.
* ``config.diet.source_basis`` and ``config.diet.weight_conversion`` —
  per-source native bases and conversion tables.
* ``config.diet.gdd_ia.cooked_to_raw`` — per-group cooked→raw inflation
  factors for GDD-IA (currently ``red_meat: 1.43``).
* ``config.diet.gdd_ia.country_proxies`` — extra proxies beyond the
  defaults in ``prepare_gdd_ia_dietary_intake.py``.
* ``config.diet.nhanes.cycle`` and ``.reference_year`` — FPED release.
* ``config.health.risk_factors`` — drives which food groups are
  anchored to GBD in Step 1.
* ``config.byproducts`` — foods excluded from share calculation.

**Output**:

* ``processing/{name}/gdd_ia_dietary_intake.csv`` — GDD-IA group-level
  intake.
* ``processing/{name}/gdd_ia_kcal_target.csv`` — per-country kcal
  accounting (total dietary energy, out-of-scope subtotal, in-scope
  target, refined / whole-grain cereal energy split).
* ``processing/{name}/nhanes_dietary_intake.csv`` — USA NHANES override.
* ``processing/{name}/dietary_intake.csv`` — merged GDD-IA + NHANES.
* ``processing/{name}/gbd_food_group_intake.csv`` — GBD exposure.
* ``processing/{name}/baseline_diet.csv`` — per-food, per-country
  baseline diet.

**Scripts**:

* ``workflow/scripts/prepare_gdd_ia_dietary_intake.py``
* ``workflow/scripts/prepare_nhanes_dietary_intake.py``
* ``workflow/scripts/merge_dietary_sources.py``
* ``workflow/scripts/prepare_gbd_food_group_intake.py``
* ``workflow/scripts/estimate_baseline_diet.py``
* ``workflow/scripts/diet/food_group_projection.py`` — within-group
  pooled-projection helpers (FBS-code pools, production-share blends).

.. Reference [Brauer2024] is defined in health.rst (Sphinx citations are global).
