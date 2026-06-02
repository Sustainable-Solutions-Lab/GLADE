# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Food systems optimization model builder.

Modular package for constructing PyPSA networks representing global food production,
conversion, trade, and nutrition constraints.

Component Naming and Accessing Conventions
==========================================

This module follows a consistent naming and attribute scheme for all PyPSA
components. **Never parse component names to extract metadata** - always use
columns.

Naming Scheme
-------------

Names use `:` as delimiter (uncommon in data values, safe for parsing if needed):

**Pattern**: ``{type}:{specifier}:{scope}``

Buses::

    crop:{crop}:{country}           e.g., crop:wheat:USA
    food:{food}:{country}           e.g., food:bread:USA
    feed:{category}:{country}       e.g., feed:ruminant_forage:USA
    residue:{item}:{country}        e.g., residue:wheat_straw:USA
    group:{group}:{country}         e.g., group:cereals:USA
    nutrient:{nutrient}:{country}   e.g., nutrient:protein:USA
    land:cropland:{region}_c{class}_{water}            e.g., land:cropland:usa_east_c1_r
    land:pasture:{region}_c{class}                    e.g., land:pasture:usa_east_c1
    land:existing_cropland:{region}_c{class}_{water}  e.g., land:existing_cropland:usa_east_c1_r
    land:new:{region}_c{class}_{water}                e.g., land:new:usa_east_c1_r
    land:existing_grassland_convertible:{region}_c{class}  e.g., land:existing_grassland_convertible:usa_east_c1
    land:existing_grassland_marginal:{region}_c{class}     e.g., land:existing_grassland_marginal:usa_east_c1
    land:spared:{region}_c{class}_{water}              e.g., land:spared:usa_east_c1_r
    land:spared_existing_grassland_{type}:{region}_c{class}  e.g., land:spared_existing_grassland_convertible:usa_east_c1
    water:{region}                  e.g., water:usa_east
    fertilizer:supply               (global)
    fertilizer:{country}            e.g., fertilizer:USA
    emission:{type}                 e.g., emission:co2, emission:ghg
    biomass:{country}               e.g., biomass:USA
    fiber:{country}                 e.g., fiber:USA
    health:cluster:{cluster:03d}    e.g., health:cluster:001

Links::

    produce:{crop}_{water}:{region}_c{class}  e.g., produce:wheat_rainfed:usa_east_c1
    produce:multi_{combo}_{water}:{region}_c{class}
    produce:grassland:{region}_c{class}
    pathway:{pathway}:{country}               e.g., pathway:milling:USA
    convert:{item}_to_{category}:{country}    e.g., convert:wheat_to_ruminant_grain:USA
    animal:{product}_{feed}:{country}         e.g., animal:beef_grassfed:USA
    consume:{food}:{country}                  e.g., consume:bread:USA
    use:existing_land:{region}_c{class}_{water}
    use:existing_to_pasture:{region}_c{class}
    convert:new_land:{region}_c{class}_{water}
    convert:new_to_pasture:{region}_c{class}
    use:existing_grassland_{type}_to_pasture:{region}_c{class}
    spare:land:{region}_c{class}_{water}
    spare:existing_grassland_{type}:{region}_c{class}
    distribute:fertilizer:{country}
    incorporate:residue_{item}:{country}
    aggregate:{from}_to_{to}                  e.g., aggregate:ch4_to_ghg
    trade:{commodity}:{from}_{to}
    biomass:{item}:{country}
    biofuel:{item}:{country}
    fiber:{item}:{country}

Stores::

    store:group:{group}:{country}    e.g., store:group:cereals:USA
    store:nutrient:{nutrient}:{country}  e.g., store:nutrient:protein:USA
    store:water:{region}             e.g., store:water:usa_east
    store:fertilizer:{country}       e.g., store:fertilizer:USA
    store:emission:{type}            e.g., store:emission:ghg
    store:spared:{region}_c{class}_{water}  e.g., store:spared:usa_east_c1_r
    store:spared_existing_grassland_{type}:{region}_c{class}
    store:fiber:{item}:{country}
    store:yll:{cause}:cluster{cluster:03d}  e.g., store:yll:ihd:cluster001

Generators::

    supply:land_{type}:{region}_c{class}_{water}  e.g., supply:land_existing_cropland:usa_east_c1_r
    supply:fertilizer
    supply:exogenous_{category}:{country}         e.g., supply:exogenous_ruminant_forage:USA
    supply:health:cluster{cluster:03d}
    sink:biomass:{country}
    slack:{type}:{scope}             e.g., slack:water:usa_east

Carrier Column
--------------

Every bus, link, store and generator carries a ``carrier`` column; use it for type
identification. Carriers identify **component type only** -- specific items
(crops, foods, products, regions, ...) are stored in metadata columns alongside.

- Buses: ``crop_{crop}``, ``food_{food}``, ``feed_{category}``, ``residue_{item}``,
  ``group_{group}``, ``{nutrient}``, ``land_cropland``, ``land_pasture``,
  ``land_existing_cropland``, ``land_existing_grassland_convertible``,
  ``land_existing_grassland_marginal``, ``land_new``, ``spared_land``,
  ``spared_grassland``, ``multi_cropping_land_correction``,
  ``water``, ``fertilizer``, ``co2``, ``ch4``, ``n2o``, ``ghg``,
  ``biomass``, ``fiber_demand``, ``health``

- Links:
  - ``crop_production``: Crop production (use ``crop`` column for specific crop)
  - ``crop_production_multi``: Multi-cropping production (use ``crop`` column for combination)
  - ``grassland_production``: Grassland/pasture production (``feed_category`` is always ``ruminant_forage``)
  - ``animal_production``: Animal product production (use ``product``, ``feed_category`` columns)
  - ``food_consumption``: Food consumption (use ``food``, ``food_group`` columns)
  - ``food_processing``: Food processing pathways (use ``pathway``, ``crop`` columns)
  - ``feed_conversion``: Crop/food to feed conversion (use ``crop``, ``feed_category`` columns)
  - ``trade_crop``: Crop trade (use ``crop`` column)
  - ``trade_food``: Food trade (use ``food`` column)
  - ``trade_feed``: Feed trade (use ``feed_category`` column)
  - ``biomass_crop``: Crop to biomass (use ``crop`` column)
  - ``biomass_byproduct``: Byproduct to biomass (use ``food`` column)
  - ``biomass_disposal``: Food to biomass disposal (use ``food`` column)
  - ``biofuel``: Food/crop to biomass for biofuel demand (use ``crop`` column)
  - ``fiber_demand``: Food bus to per-country fiber bus
  - ``fertilizer_distribution``: Fertilizer distribution
  - ``emission_aggregation``: GHG emission aggregation
  - ``land_use``, ``land_conversion``, ``existing_to_pasture``, ``new_to_pasture``, ``existing_grassland_to_pasture``, ``spare_land``, ``spare_existing_grassland``, ``residue_incorporation``

- Generators: ``land_existing_cropland``, ``land_existing_grassland_convertible``,
  ``land_existing_grassland_marginal``, ``fertilizer``, ``land_slack``,
  ``exogenous_feed``, ``exogenous_forage_cal``, ``exogenous_protein_cal``,
  ``exogenous_roughage_cal``
  (the last three are added at solve time by calibration generators),
  ``slack_positive_feed``, ``slack_negative_feed``, ``biomass``, ``health``

Custom Columns
--------------

All components have consistent domain-specific columns for filtering:

- **Buses**:

  - ``country``: str | NaN - country code (NaN for global/regional)
  - ``region``: str | NaN - region name (land / water / spared-land buses)
  - ``resource_class``: int | NaN - land quality class (land buses)
  - ``water_supply``: str | NaN - "irrigated" / "rainfed" (cropland / new / existing_cropland buses)
  - ``land_type``: str | NaN - "convertible" / "marginal" (existing-grassland buses)
  - ``feed_category``: str | NaN - feed category (feed buses)
  - ``health_cluster``: int | NaN - GBD cluster id (health buses)

- **Links**:

  - ``country``: str | NaN - country code
  - ``region``: str | NaN - region name
  - ``crop``: str | NaN - crop name
  - ``food``: str | NaN - food name
  - ``food_group``: str | NaN - food group name
  - ``product``: str | NaN - animal product name
  - ``feed_category``: str | NaN - feed category
  - ``resource_class``: int | NaN - land quality class
  - ``water_supply``: str | NaN - "irrigated" or "rainfed"
  - ``land_type``: str | NaN - e.g. "convertible" or "marginal" for grassland pools

- **Stores**:

  - ``country``: str | NaN - country code
  - ``food_group``: str | NaN - food group name
  - ``nutrient``: str | NaN - nutrient name
  - ``region``: str | NaN - region name (spared-land stores)
  - ``resource_class``: int | NaN - land quality class (spared-land stores)
  - ``water_supply``: str | NaN - "irrigated" / "rainfed" (spared-land stores)
  - ``land_type``: str | NaN - grassland-pool type (spared-grassland stores)
  - ``health_cluster``: int | NaN - GBD cluster id (yll stores)
  - ``cause``: str | NaN - GBD cause name (yll stores)

- **Generators**:

  - ``country``: str | NaN - country code
  - ``region``: str | NaN - region name

- **Global Constraints**:

  - ``country``: str | NaN - country code
  - ``food_group``: str | NaN - food group name
  - ``nutrient``: str | NaN - nutrient name
  - ``product``: str | NaN - product name
  - ``crop``: str | NaN - crop name

Accessing Components
--------------------

Use regular pandas indexing with ``carrier`` and domain columns. Fail fast when
no components found::

    # Get food group stores for a specific group
    group_stores = n.stores.static[n.stores.static["carrier"] == f"group_{group}"]
    if group_stores.empty:
        raise ValueError(f"No stores found for food group '{group}'")

    # Get crop production links for a specific country
    crop_links = n.links.static[
        (n.links.static["carrier"] == "crop_production") &
        (n.links.static["crop"] == crop) &
        (n.links.static["country"] == country)
    ]
    if crop_links.empty:
        raise ValueError(f"No production links for crop '{crop}' in '{country}'")

    # Get all consumption links
    consume_links = n.links.static[n.links.static["carrier"] == "food_consumption"]
"""

# Re-export submodules for convenience
from .. import constants  # constants moved to parent package
from . import (
    animals,
    biomass,
    crops,
    food,
    health,
    infrastructure,
    nutrition,
    primary_resources,
    trade,
    utils,
)

__all__ = [
    "animals",
    "biomass",
    "constants",
    "crops",
    "food",
    "health",
    "infrastructure",
    "nutrition",
    "primary_resources",
    "trade",
    "utils",
]
