# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Constants and unit conversion factors for food systems model.

This module contains all conversion factors, constants, and supported
unit definitions used throughout the model building process.
"""

# Unit conversion factors
MM3_PER_M3 = 1e-6  # convert cubic metres to million cubic metres
TONNE_TO_MEGATONNE = 1e-6  # convert tonnes to megatonnes
MEGATONNE_TO_TONNE = 1e6  # convert megatonnes to tonnes
KG_TO_MEGATONNE = 1e-9  # convert kilograms to megatonnes
GRAMS_PER_MEGATONNE = 1e12  # grams per megatonne of mass
YLL_TO_MILLION_YLL = 1e-6  # convert years of life lost to million YLL
PER_100K = 100_000  # epidemiological rate denominator (per 100,000 population)
FOOD_PORTION_TO_MASS_FRACTION = 1e-2  # convert x per 100g to mass fraction
# Energy: use petajoules throughout to keep magnitudes modest
# 1 kcal = 4.184 kJ = 4.184e-12 PJ
KCAL_TO_PJ = 4.184e-12  # convert kilocalories to petajoules
PJ_TO_KCAL = 1.0 / KCAL_TO_PJ  # convert petajoules to kilocalories
KCAL_PER_100G_TO_PJ_PER_MEGATONNE = (
    FOOD_PORTION_TO_MASS_FRACTION * GRAMS_PER_MEGATONNE * KCAL_TO_PJ
)  # kcal/100g to PJ per Mt of food
USD_TO_BNUSD = 1e-9  # convert USD to billion USD
HA_PER_MHA = 1e6  # convert million hectares to hectares
DAYS_PER_YEAR = 365
N2O_N_TO_N2O = 44.0 / 28.0  # molecular weight ratio to convert N2O-N to N2O

# Feed categories: 5 ruminant + 4 monogastric quality classes
FEED_CATEGORIES = [
    "ruminant_grassland",
    "ruminant_roughage",
    "ruminant_forage",
    "ruminant_grain",
    "ruminant_protein",
    "monogastric_low_quality",
    "monogastric_grain",
    "monogastric_energy",
    "monogastric_protein",
]

# Supported nutrition unit definitions
SUPPORTED_NUTRITION_UNITS = {
    "g/100g": {
        "kind": "mass",
        "efficiency_factor": FOOD_PORTION_TO_MASS_FRACTION,
    },
    "kcal/100g": {
        "kind": "energy",
        "efficiency_factor": KCAL_PER_100G_TO_PJ_PER_MEGATONNE,
    },
}
