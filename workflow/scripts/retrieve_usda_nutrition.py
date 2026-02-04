# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Retrieve nutritional data from USDA FoodData Central API.

This script fetches nutrition data for foods listed in data/curated/usda_food_mapping.csv
and outputs in the format expected by the model (data/curated/nutrition.csv).

Requires network access to call the USDA API. Credentials are sourced from
config/secrets.yaml or the USDA_API_KEY environment variable.

Adding New Foods
----------------
When adding a new food to the model, you must:

1. Add the food to data/curated/food_groups.csv with its food group classification
2. Find the appropriate USDA FoodData Central (FDC) ID from the SR Legacy database
3. Add the mapping to data/curated/usda_food_mapping.csv

To search for foods in the USDA database using the API:

.. code-block:: bash

    # Search for a food by name, filtering to SR Legacy database
    curl -X POST -H "Content-Type:application/json" \\
      -d '{"query": "brown rice raw", "dataType": ["SR Legacy"], "pageSize": 5}' \\
      "https://api.nal.usda.gov/fdc/v1/foods/search?api_key=YOUR_API_KEY"

    # The response will contain matching foods with their FDC IDs and descriptions
    # Example output:
    # {
    #   "foods": [
    #     {
    #       "fdcId": 169703,
    #       "description": "Rice, brown, long-grain, raw",
    #       "dataType": "SR Legacy",
    #       ...
    #     }
    #   ]
    # }

Search tips:
- Use descriptive terms like "raw", "dried", "fresh" to match food forms
- Prefer SR Legacy database entries for consistency (most comprehensive nutritional data)
- Check the full description to ensure it matches the intended food item
- For processed foods, look for the form closest to how it's consumed in the model

Manual search via web interface:
- Visit https://fdc.nal.usda.gov/
- Use the search box and filter by "SR Legacy" database
- Copy the FDC ID from the food detail page

Validation
----------
This script validates that all non-byproduct foods in data/curated/food_groups.csv have
corresponding entries in data/curated/usda_food_mapping.csv. The script will fail if any
foods are missing from the mapping file.
"""

import logging
from pathlib import Path
import time

import pandas as pd
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

BASE_URL = "https://api.nal.usda.gov/fdc/v1"


def get_food_nutrients(fdc_id: int, api_key: str) -> dict[str, tuple[float, str]]:
    """
    Retrieve nutrient data for a specific FDC ID.

    Parameters
    ----------
    fdc_id : int
        USDA FoodData Central food ID
    api_key : str
        USDA API key

    Returns
    -------
    dict[str, tuple[float, str]]
        Mapping from nutrient name to (amount, unit) tuple
    """
    url = f"{BASE_URL}/food/{fdc_id}"
    params = {"api_key": api_key}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    nutrients = {}
    for nutrient_entry in data.get("foodNutrients", []):
        nutrient = nutrient_entry.get("nutrient", {})
        name = nutrient.get("name")
        if not name:
            logger.warning("Skipping nutrient entry without name for FDC ID %d", fdc_id)
            continue

        amount = nutrient_entry.get("amount", 0.0)
        # In SR Legacy database, nutrients without explicit unitName are in grams
        unit = nutrient.get("unitName", "G")
        nutrients[name] = (amount, unit)

    return nutrients


def main():
    # Get inputs from Snakemake
    mapping_path = snakemake.input.mapping
    food_groups_path = snakemake.input.food_groups
    output_path = snakemake.output[0]
    api_key = snakemake.config["credentials"]["usda"]["api_key"]
    nutrient_name_map = snakemake.config["data"]["usda"]["nutrients"]

    # Read food groups file to get list of foods requiring nutrition data
    food_groups_df = pd.read_csv(food_groups_path, comment="#")
    # Exclude byproducts - they don't need USDA nutrition data
    non_byproduct_foods = set(
        food_groups_df[food_groups_df["group"] != "byproduct"]["food"]
    )

    # Read mapping file
    mapping_df = pd.read_csv(mapping_path, comment="#")
    mapped_foods = set(mapping_df["food"])

    # Validate that all non-byproduct foods have mappings
    missing_foods = non_byproduct_foods - mapped_foods
    if missing_foods:
        msg = (
            "ERROR: The following foods are in food_groups.csv but missing from "
            "usda_food_mapping.csv:\n"
        )
        for food in sorted(missing_foods):
            msg += f"  - {food}\n"
        msg += (
            "\nPlease add USDA FoodData Central IDs for these foods. "
            "See the script docstring for instructions on searching the USDA database."
        )
        raise ValueError(msg)

    # Prepare output data
    output_rows = []

    # Map USDA units to our internal units
    unit_map = {
        "G": "g/100g",
        "KCAL": "kcal/100g",
        "KJ": "kcal/100g",  # Will convert from kJ to kcal
    }

    logger.info("Retrieving nutrition data from USDA FoodData Central...")
    logger.info("Processing %d foods...", len(mapping_df))

    for idx, row in mapping_df.iterrows():
        food_name = row["food"]
        fdc_id = int(row["fdc_id"])

        logger.info(
            "  [%d/%d] %s (FDC %d)", idx + 1, len(mapping_df), food_name, fdc_id
        )

        nutrients = get_food_nutrients(fdc_id, api_key)

        # Extract requested nutrients
        for internal_name, usda_nutrient_name in nutrient_name_map.items():
            if usda_nutrient_name in nutrients:
                amount, usda_unit = nutrients[usda_nutrient_name]

                # Convert units if necessary
                if usda_unit.upper() == "KJ":
                    # Convert kilojoules to kilocalories
                    amount = amount / 4.184
                    internal_unit = "kcal/100g"
                else:
                    # Map USDA units to internal units
                    internal_unit = unit_map.get(usda_unit.upper(), usda_unit.lower())

                output_rows.append(
                    {
                        "food": food_name,
                        "nutrient": internal_name,
                        "unit": internal_unit,
                        "value": round(amount, 2),
                    }
                )
            else:
                logger.warning("    %s not found for %s", usda_nutrient_name, food_name)

        # Be polite to the API
        time.sleep(0.5)

    # Write output
    output_df = pd.DataFrame(output_rows)
    output_df = output_df.sort_values(["food", "nutrient"])

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Write output
    output_df.to_csv(output_path, index=False)

    logger.info("\nWrote %d nutrient entries to %s", len(output_rows), output_path)


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
