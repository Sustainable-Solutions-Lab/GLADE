# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract objective function breakdown from solved networks.

This script extracts the cost components that make up the model's objective
function, grouped into high-level categories. The output enables analysis
of how different cost drivers contribute to the total system cost.

Categories extracted:
- Crop production: Land use and yield-related costs (including grassland, spare land,
  residue incorporation, spared land stores)
- Land use: Existing land use links
- Trade: Import/export costs (crops, foods, feed)
- Fertilizer: Synthetic fertilizer supply and distribution
- Processing: Food processing/conversion costs (pathways, food conversion)
- Animal production: Livestock production costs
- Feed conversion: Feed processing costs
- Consumption: Food consumption links
- Consumer values: Utility from food group consumption (typically negative)
- Biomass exports: Revenue from biomass exports (typically negative)
- Biomass routing: Internal biomass flow costs (residue conversion)
- Health burden: Health costs from YLL stores
- GHG cost: Emissions costs from GHG stores
- Emissions aggregation: Links aggregating emissions to GHG bus
- Water: Water stores
- Slack penalties: Constraint violation penalties (ideally zero)
- Resource supply: Land and resource generators (usually zero cost)
- Nutrient tracking: Nutrient stores (usually zero cost)
- Production stability: Penalty for deviating from baseline production (L1 or quadratic)

All costs are in billion USD, matching the model's internal units.

The script validates that extracted categories sum to the model's reported
objective value and raises an error if they don't match (within 1% tolerance).
It also raises errors for unrecognized component patterns to ensure the
analysis is updated when the model structure changes.
"""

import pandas as pd
import pypsa

# Relative tolerance for objective validation
OBJECTIVE_RTOL = 0.01  # 1% tolerance


def _objective_category(n: pypsa.Network, component: str, **_) -> pd.Series:
    """Group assets into high-level categories for system cost aggregation.

    Raises ValueError for unrecognized component patterns to ensure the
    analysis script is updated when the model structure changes.

    Parameters
    ----------
    n : pypsa.Network
        Network being analyzed
    component : str
        PyPSA component type (Generator, Link, Store, etc.)

    Returns
    -------
    pd.Series
        Series mapping component names to category strings

    Raises
    ------
    ValueError
        If a component name doesn't match any known pattern
    """
    static = n.components[component].static
    if static.empty:
        return pd.Series(dtype="object")

    index = static.index

    if component == "Generator":
        carriers = static.get("carrier", pd.Series(dtype=str))
        categories = []
        for name in index:
            name_str = str(name)
            carrier = str(carriers.get(name, "")) if not carriers.empty else ""
            if name_str.startswith("biomass:"):
                categories.append("Biomass exports")
            elif name_str.startswith("sink:"):
                # Sink generators (e.g., biomass sinks) - revenue from exports
                categories.append("Biomass exports")
            elif name_str.startswith("slack:"):
                categories.append("Slack penalties")
            elif carrier == "fertilizer":
                categories.append("Fertilizer")
            elif name_str.startswith("supply:"):
                # Land and resource supply generators (usually zero cost)
                categories.append("Resource supply")
            else:
                raise ValueError(
                    f"Unrecognized Generator pattern: name='{name_str}', carrier='{carrier}'. "
                    f"Update _objective_category() to handle this case."
                )
        return pd.Series(categories, index=index, name="category")

    if component == "Link":
        prefix_map = {
            "produce": "Crop production",
            "trade": "Trade",
            "trade_food": "Trade",
            "trade_feed": "Trade",
            "convert": "Processing",
            "convert_food": "Processing",
            "convert_residue": "Biomass routing",
            "consume": "Consumption",
            "animal": "Animal production",
            "pathway": "Processing",
            "biomass": "Biomass routing",
            "feed": "Feed conversion",
            "grassland": "Crop production",
            "spare": "Crop production",
            "aggregate": "Emissions aggregation",
            "distribute": "Fertilizer",
            "incorporate": "Crop production",
            "use": "Land use",
        }
        categories = []
        for name in index:
            name_str = str(name)
            prefix = name_str.split(":", 1)[0]
            if prefix in prefix_map:
                categories.append(prefix_map[prefix])
            else:
                raise ValueError(
                    f"Unrecognized Link prefix: '{prefix}' in '{name_str}'. "
                    f"Update _objective_category() to handle this case."
                )
        return pd.Series(categories, index=index, name="category")

    if component == "Store":
        carriers = static["carrier"].astype(str)
        nutrient_carriers = {"cal", "carb", "fat", "protein"}
        categories = []
        for name, carrier in zip(index, carriers):
            if carrier == "ghg":
                categories.append("GHG cost")
            elif carrier.startswith("yll_"):
                categories.append("Health burden")
            elif carrier.startswith("group_"):
                categories.append("Consumer values")
            elif carrier.startswith("nutrient_") or carrier in nutrient_carriers:
                # Nutrient stores (protein, fat, carb, cal) - usually no cost
                categories.append("Nutrient tracking")
            elif carrier == "water":
                categories.append("Water")
            elif carrier == "fertilizer":
                categories.append("Fertilizer")
            elif carrier in ("spared_land", "spared_grassland"):
                categories.append("Crop production")
            elif carrier == "production_stability":
                categories.append("Production stability")
            else:
                raise ValueError(
                    f"Unrecognized Store carrier: '{carrier}' for store '{name}'. "
                    f"Update _objective_category() to handle this case."
                )
        return pd.Series(categories, index=index, name="category")

    # For other components, fail explicitly
    raise ValueError(
        f"Unrecognized component type: '{component}'. "
        f"Update _objective_category() to handle this case."
    )


def extract_objective_breakdown(n: pypsa.Network) -> pd.DataFrame:
    """Extract objective function breakdown by cost category.

    Uses PyPSA's statistics module to compute capex and opex contributions
    grouped by high-level categories. Production stability penalties (L1 or
    quadratic) are linopy-level objective terms stored in network metadata.

    Parameters
    ----------
    n : pypsa.Network
        Solved network

    Returns
    -------
    pd.DataFrame
        Single-row DataFrame with columns for each cost category (billion USD)

    Raises
    ------
    ValueError
        If extracted costs don't sum to approximately the model objective
    """
    # Get capex and opex grouped by category
    capex = n.statistics.capex(groupby=_objective_category)
    opex = n.statistics.opex(groupby=_objective_category)

    def _to_series(df_or_series: pd.DataFrame | pd.Series) -> pd.Series:
        """Convert statistics output to a Series grouped by category."""
        if isinstance(df_or_series, pd.DataFrame):
            df_or_series = df_or_series.iloc[:, 0]
        if df_or_series.empty:
            return pd.Series(dtype=float)
        idx = df_or_series.index
        if "category" not in idx.names:
            idx = idx.set_names([*list(idx.names[:-1]), "category"])
            df_or_series.index = idx
        return df_or_series.groupby("category").sum()

    capex_series = _to_series(capex)
    opex_series = _to_series(opex)
    total = capex_series.add(opex_series, fill_value=0.0)

    # Filter out negligible categories
    total = total[total.abs() > 1e-9]

    # Food slack penalties are linopy-level variables not visible as PyPSA
    # components; their total cost is stored in n.meta by solve_model.
    food_slack_cost = n.meta.get("food_slack_cost", 0.0)
    if food_slack_cost:
        total["Slack penalties"] = total.get("Slack penalties", 0.0) + food_slack_cost

    # Production stability penalties (L1/quadratic) are linopy-level terms.
    stability_cost = n.meta.get("production_stability_cost", 0.0)
    if stability_cost:
        total["Production stability"] = (
            total.get("Production stability", 0.0) + stability_cost
        )

    # Piecewise food utility is also a linopy-level objective term.
    food_utility_cost = n.meta.get("food_utility_cost", 0.0)
    if food_utility_cost:
        total["Consumer values"] = total.get("Consumer values", 0.0) + food_utility_cost

    extracted_sum = total.sum()
    model_objective = n.objective

    # Validate against model objective
    if model_objective != 0:
        rel_error = abs(extracted_sum - model_objective) / abs(model_objective)
        if rel_error > OBJECTIVE_RTOL:
            raise ValueError(
                f"Extracted costs ({extracted_sum:.6f}) differ from model objective "
                f"({model_objective:.6f}) by {rel_error * 100:.2f}% "
                f"(tolerance: {OBJECTIVE_RTOL * 100:.2f}%). "
                f"Categories found: {total.to_dict()}"
            )
    else:
        if abs(extracted_sum) > 1e-6:
            raise ValueError(
                f"Model objective is zero but extracted costs sum to {extracted_sum:.6f}"
            )

    # Convert to single-row DataFrame
    result = total.to_frame().T
    result.index = [0]

    # Rename columns to snake_case for consistency
    column_map = {
        "Crop production": "crop_production",
        "Trade": "trade",
        "Fertilizer": "fertilizer",
        "Processing": "processing",
        "Consumption": "consumption",
        "Animal production": "animal_production",
        "Feed conversion": "feed_conversion",
        "Consumer values": "consumer_values",
        "Biomass exports": "biomass_exports",
        "Biomass routing": "biomass_routing",
        "Health burden": "health_burden",
        "GHG cost": "ghg_cost",
        "Slack penalties": "slack_penalties",
        "Resource supply": "resource_supply",
        "Nutrient tracking": "nutrient_tracking",
        "Emissions aggregation": "emissions_aggregation",
        "Land use": "land_use",
        "Water": "water",
        "Production stability": "production_stability",
    }
    result = result.rename(columns=column_map)

    return result
