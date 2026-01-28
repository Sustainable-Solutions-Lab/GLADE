# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
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
    feed:{category}:{country}       e.g., feed:ruminant_grassland:USA
    residue:{item}:{country}        e.g., residue:wheat_straw:USA
    group:{group}:{country}         e.g., group:cereals:USA
    nutrient:{nutrient}:{country}   e.g., nutrient:protein:USA
    land:pool:{region}_c{class}_{water}     e.g., land:pool:usa_east_c1_r
    land:existing:{region}_c{class}_{water} e.g., land:existing:usa_east_c1_r
    land:new:{region}_c{class}_{water}      e.g., land:new:usa_east_c1_r
    land:marginal:{region}_c{class}         e.g., land:marginal:usa_east_c1
    water:{region}                  e.g., water:usa_east
    fertilizer:supply               (global)
    fertilizer:{country}            e.g., fertilizer:USA
    emission:{type}                 e.g., emission:co2, emission:ghg

Links::

    produce:{crop}_{water}:{region}_c{class}  e.g., produce:wheat_rainfed:usa_east_c1
    produce:multi_{combo}_{water}:{region}_c{class}
    produce:grassland:{region}_c{class}
    pathway:{pathway}:{country}               e.g., pathway:milling:USA
    convert:{item}_to_{category}:{country}    e.g., convert:wheat_to_ruminant_grain:USA
    animal:{product}_{feed}:{country}         e.g., animal:beef_grassfed:USA
    consume:{food}:{country}                  e.g., consume:bread:USA
    use:existing_land:{region}_c{class}_{water}
    convert:new_land:{region}_c{class}_{water}
    spare:land:{region}_c{class}_{water}
    distribute:fertilizer:{country}
    incorporate:residue_{item}:{country}
    aggregate:{from}_to_{to}                  e.g., aggregate:ch4_to_ghg
    trade:{commodity}:{from}_{to}
    biomass:{item}:{country}

Stores::

    store:group:{group}:{country}    e.g., store:group:cereals:USA
    store:nutrient:{nutrient}:{country}  e.g., store:nutrient:protein:USA
    store:water:{region}             e.g., store:water:usa_east
    store:fertilizer:{country}       e.g., store:fertilizer:USA
    store:emission:{type}            e.g., store:emission:ghg

Generators::

    supply:land_{type}:{region}_c{class}_{water}  e.g., supply:land_existing:usa_east_c1_r
    supply:fertilizer
    slack:{type}:{scope}             e.g., slack:water:usa_east

Carrier Column
--------------

Use the ``carrier`` column for type identification. Carriers identify **link type only**;
specific items (crops, foods, products) are stored in metadata columns.

- Buses: ``crop_{crop}``, ``food_{food}``, ``feed_{category}``, ``residue_{item}``,
  ``group_{group}``, ``{nutrient}``, ``land``, ``land_existing``, ``land_new``,
  ``water``, ``fertilizer``, ``co2``, ``ch4``, ``n2o``, ``ghg``

- Links:
  - ``crop_production``: Crop production (use ``crop`` column for specific crop)
  - ``crop_production_multi``: Multi-cropping production (use ``crop`` column for combination)
  - ``grassland_production``: Grassland/pasture production
  - ``animal_production``: Animal product production (use ``product``, ``feed_category`` columns)
  - ``food_consumption``: Food consumption (use ``food``, ``food_group`` columns)
  - ``food_processing``: Food processing pathways (use ``pathway``, ``crop`` columns)
  - ``feed_conversion``: Crop/food to feed conversion (use ``crop``, ``feed_category`` columns)
  - ``trade_crop``: Crop trade (use ``crop`` column)
  - ``trade_food``: Food trade (use ``food`` column)
  - ``trade_feed``: Feed trade (use ``feed_category`` column)
  - ``biomass_crop``: Crop to biomass (use ``crop`` column)
  - ``biomass_byproduct``: Byproduct to biomass (use ``food`` column)
  - ``fertilizer_distribution``: Fertilizer distribution
  - ``emission_aggregation``: GHG emission aggregation
  - ``land_use``, ``land_conversion``, ``spare_land``, ``residue_incorporation``: unchanged

Custom Columns
--------------

All components have consistent domain-specific columns for filtering:

**Buses**:
    - ``country``: str | NaN - country code (NaN for global/regional)
    - ``region``: str | NaN - region name (for land/water buses)

**Links**:
    - ``country``: str | NaN - country code
    - ``region``: str | NaN - region name
    - ``crop``: str | NaN - crop name
    - ``food``: str | NaN - food name
    - ``food_group``: str | NaN - food group name
    - ``product``: str | NaN - animal product name
    - ``feed_category``: str | NaN - feed category
    - ``resource_class``: int | NaN - land quality class
    - ``water_supply``: str | NaN - "irrigated" or "rainfed"

**Stores**:
    - ``country``: str | NaN - country code
    - ``food_group``: str | NaN - food group name
    - ``nutrient``: str | NaN - nutrient name

**Generators**:
    - ``country``: str | NaN - country code
    - ``region``: str | NaN - region name

**Global Constraints**:
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
    production_stability,
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
    "production_stability",
    "trade",
    "utils",
]
