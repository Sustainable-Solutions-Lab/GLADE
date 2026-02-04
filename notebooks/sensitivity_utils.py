# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for sensitivity analysis notebooks.

This module contains common functions used by yll_sensitivity, ghg_sensitivity,
and combined_sensitivity notebooks.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
import yaml

# Import constants from workflow instead of redefining
from workflow.scripts.constants import (
    DAYS_PER_YEAR,
    GRAMS_PER_MEGATONNE,
    PER_100K,
    PJ_TO_KCAL,
    USD_TO_BNUSD,
)

# GWP values (AR5 100-year)
CH4_GWP = 28.0
N2O_GWP = 265.0

# Figure styling constants
FIGURE_WIDTH_MM = 180  # Standard figure width in mm
MM_TO_INCH = 1 / 25.4  # Conversion factor

# Font sizes (in points)
FONTSIZE_TITLE = 7
FONTSIZE_AXIS_LABEL = 6
FONTSIZE_TICK_LABEL = 5
FONTSIZE_CBAR_LABEL = 6
FONTSIZE_PANEL_LABEL = 8
FONTSIZE_CONTOUR_LABEL = 5


def log_scale_zero_position(x_values: np.ndarray) -> float:
    """Calculate the position at which to plot x=0 on a log scale for even spacing.

    Given a sequence of x values that includes 0 and follows a geometric
    progression (e.g., 0, 1, 2, 4, 8, ...), this function calculates where
    to plot the 0 value to maintain even spacing on a log scale.

    The formula is: zero_pos = x1² / x2, where x1 and x2 are the first two
    non-zero values.

    Args:
        x_values: Array of x values, may include 0.

    Returns:
        The x position at which to plot 0 for even log-scale spacing.
        Returns 0.5 as fallback if calculation is not possible.
    """
    non_zero = np.sort(x_values[x_values > 0])
    if len(non_zero) >= 2:
        x1, x2 = non_zero[0], non_zero[1]
        return x1 * x1 / x2
    return 0.5  # Fallback


# Pretty names for food groups (including aggregated groups)
PRETTY_NAMES = {
    "grain": "Refined grains",
    "whole_grains": "Whole grains",
    "dairy": "Dairy",
    "eggs": "Eggs",
    "fruits": "Fruits",
    "legumes": "Legumes",
    "nuts_seeds": "Nuts & seeds",
    "oil": "Oil",
    "poultry": "Poultry",
    "red_meat": "Red meat",
    "starchy_vegetable": "Starchy veg.",
    "sugar": "Sugar",
    "vegetables": "Vegetables",
    "fruits_vegetables": "Fruits & veg.",
    "eggs_poultry": "Eggs & poultry",
}

# Health-specific labels: "Diet low in X" for protective, "Diet high in X" for harmful
PRETTY_NAMES_HEALTH = {
    "fruits": "Diet low in\nfruits",
    "vegetables": "Diet low in\nvegetables",
    "whole_grains": "Diet low in\nwhole grains",
    "legumes": "Diet low in\nlegumes",
    "nuts_seeds": "Diet low in\nnuts & seeds",
    "red_meat": "Diet high in\nred meat",
    "fruits_vegetables": "Diet low in\nfruits & veg.",
}

# Pretty names for objective categories
PRETTY_NAMES_OBJ = {
    "Crop production": "Crop production",
    "Trade": "Trade",
    "Health burden": "Health burden",
    "GHG cost": "GHG cost",
    "GHG cost (positive)": "GHG cost",
    "GHG cost (negative)": "GHG cost",
    "Fertilizer (synthetic)": "Fertilizer",
    "Consumer values": "Consumer values",
    "Biomass exports": "Biomass exports",
}


# -----------------------------------------------------------------------------
# Config and scenario loading utilities
# -----------------------------------------------------------------------------


def load_scenario_defs(project_root: Path, config_name: str) -> dict:
    """Load and expand scenario definitions from a config file.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')

    Returns:
        Dict of expanded scenario definitions
    """
    # Add workflow directory to path for importing scenario_generators
    workflow_path = project_root / "workflow"
    if str(workflow_path) not in sys.path:
        sys.path.insert(0, str(workflow_path))

    from scenario_generators import expand_scenario_defs

    # Load main config to get scenario_defs path
    config_path = project_root / "config" / f"{config_name}.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    scenario_defs_path = project_root / config["scenario_defs"]
    with open(scenario_defs_path) as f:
        raw_defs = yaml.safe_load(f)

    return expand_scenario_defs(raw_defs)


def extract_scenarios_with_param(
    project_root: Path,
    config_name: str,
    param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, str, Path]]:
    """Extract scenarios and their parameter values from config.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll')
        param_path: Path to parameter in scenario config (e.g., ['emissions', 'ghg_price'])
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg_', 'yll_')

    Returns:
        List of (param_value, scenario_name, network_path) tuples, sorted by param_value
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to parameter value
        value = scenario_config
        for key in param_path:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                value = None
                break

        if value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append((float(value), scenario_name, network_path))

    scenarios.sort(key=lambda x: x[0])
    return scenarios


def extract_combined_scenarios(
    project_root: Path,
    config_name: str,
    ghg_param_path: list[str],
    yll_param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, float, str, Path]]:
    """Extract scenarios with both GHG price and YLL value parameters.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll')
        ghg_param_path: Path to GHG price in scenario config
        yll_param_path: Path to YLL value in scenario config
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg_yll_')

    Returns:
        List of (ghg_price, yll_value, scenario_name, network_path) tuples,
        sorted by ghg_price
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to GHG price
        ghg_value = scenario_config
        for key in ghg_param_path:
            if isinstance(ghg_value, dict) and key in ghg_value:
                ghg_value = ghg_value[key]
            else:
                ghg_value = None
                break

        # Navigate to YLL value
        yll_value = scenario_config
        for key in yll_param_path:
            if isinstance(yll_value, dict) and key in yll_value:
                yll_value = yll_value[key]
            else:
                yll_value = None
                break

        if ghg_value is not None and yll_value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append(
                (float(ghg_value), float(yll_value), scenario_name, network_path)
            )

    scenarios.sort(key=lambda x: x[0])
    return scenarios


def extract_grid_scenarios(
    project_root: Path,
    config_name: str,
    ghg_param_path: list[str],
    yll_param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, float, str, Path]]:
    """Extract grid scenarios with both GHG price and YLL value parameters.

    Unlike extract_combined_scenarios which expects co-varying parameters,
    this function extracts all combinations from a 2D grid.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll_grid')
        ghg_param_path: Path to GHG price in scenario config
        yll_param_path: Path to YLL value in scenario config
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg')

    Returns:
        List of (ghg_price, yll_value, scenario_name, network_path) tuples,
        sorted by (ghg_price, yll_value)
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to GHG price
        ghg_value = scenario_config
        for key in ghg_param_path:
            if isinstance(ghg_value, dict) and key in ghg_value:
                ghg_value = ghg_value[key]
            else:
                ghg_value = None
                break

        # Navigate to YLL value
        yll_value = scenario_config
        for key in yll_param_path:
            if isinstance(yll_value, dict) and key in yll_value:
                yll_value = yll_value[key]
            else:
                yll_value = None
                break

        if ghg_value is not None and yll_value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append(
                (float(ghg_value), float(yll_value), scenario_name, network_path)
            )

    # Sort by (ghg_price, yll_value)
    scenarios.sort(key=lambda x: (x[0], x[1]))
    return scenarios


def get_log_ticks(
    values: list[float], include_zero: bool = True
) -> tuple[list[float], list[str]]:
    """Generate tick positions and labels for a log scale with round numbers.

    Creates tick marks at powers of 10 (1, 10, 100, 1000, etc.) that fall
    within the range of the provided values.

    Args:
        values: List of parameter values (e.g., [0, 5, 14, 38, 100, 500])
        include_zero: Whether to include 0 at position 1 (for log scale)

    Returns:
        Tuple of (tick_positions, tick_labels)
    """
    # Filter out zeros for determining range
    nonzero_values = [v for v in values if v > 0]
    if not nonzero_values:
        return [1], ["0"] if include_zero else ([], [])

    min_val = min(nonzero_values)
    max_val = max(nonzero_values)

    # Determine the range of powers of 10
    min_power = int(np.floor(np.log10(max(min_val, 1))))
    max_power = int(np.ceil(np.log10(max_val)))

    ticks = []
    labels = []

    # Include 0 at position 1 if requested and 0 is in the values
    if include_zero and 0 in values:
        ticks.append(1)
        labels.append("0")

    # Add powers of 10
    for power in range(min_power, max_power + 1):
        tick_val = 10**power
        if tick_val >= min_val and tick_val <= max_val * 1.1:
            ticks.append(tick_val)
            # Format label
            if tick_val >= 1000:
                labels.append(f"{tick_val // 1000}k")
            else:
                labels.append(str(int(tick_val)))

    # Make sure we have at least the endpoints if no powers of 10 fall in range
    if len(ticks) == 0 or (include_zero and len(ticks) == 1 and ticks[0] == 1):
        # Add min and max values as ticks
        for val in [min_val, max_val]:
            if val not in ticks:
                ticks.append(val)
                if val >= 1000:
                    labels.append(f"{int(val) // 1000}k")
                else:
                    labels.append(str(int(val)))
        # Sort by tick value
        combined = sorted(zip(ticks, labels))
        ticks = [t for t, _ in combined]
        labels = [label for _, label in combined]

    return ticks, labels


# -----------------------------------------------------------------------------
# Data loading utilities
# -----------------------------------------------------------------------------


def is_cache_valid(cache_path: Path, source_files: list[Path]) -> bool:
    """Check if cache file is newer than all source files."""
    if not cache_path.exists():
        return False
    cache_mtime = cache_path.stat().st_mtime
    return all(sf.stat().st_mtime <= cache_mtime for sf in source_files)


def load_population(project_root: Path, config_name: str) -> float:
    """Load total global population from processing CSV.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')

    Returns:
        Total global population
    """
    pop_path = project_root / "processing" / config_name / "population.csv"
    if not pop_path.exists():
        raise FileNotFoundError(
            f"Population file not found: {pop_path}. "
            f"Run the workflow to generate processing files first."
        )
    pop_df = pd.read_csv(pop_path)
    return pop_df["population"].sum()


def extract_param_value(scenario_name: str, prefix: str) -> float | None:
    """Extract a numeric parameter value from a scenario name.

    Args:
        scenario_name: e.g. 'yll_5000' or 'ghg_100'
        prefix: e.g. 'yll' or 'ghg'

    Returns:
        The numeric value, or None if pattern doesn't match
    """
    if scenario_name == "baseline":
        return None
    match = re.match(rf"{prefix}_(\d+)", scenario_name)
    if match:
        return float(match.group(1))
    return None


def extract_combined_param_value(scenario_name: str) -> tuple[float, float] | None:
    """Extract GHG price and YLL value from combined scenario name.

    Args:
        scenario_name: e.g. 'ghg_yll_100' (ghg=100, yll=40000)

    Returns:
        Tuple of (ghg_price, yll_value), or None if pattern doesn't match
    """
    if scenario_name == "baseline":
        return None
    match = re.match(r"ghg_yll_(\d+)", scenario_name)
    if match:
        ghg_price = float(match.group(1))
        yll_value = ghg_price * 400  # Fixed ratio (ghg=500 → yll=200000)
        return (ghg_price, yll_value)
    return None


def load_food_to_group(project_root: Path) -> dict[str, str]:
    """Load food to group mapping from CSV."""
    food_groups_df = pd.read_csv(project_root / "data" / "curated" / "food_groups.csv")
    return dict(zip(food_groups_df["food"], food_groups_df["group"]))


# -----------------------------------------------------------------------------
# Worker functions for objective breakdown extraction
# -----------------------------------------------------------------------------


def _objective_category(n: pypsa.Network, component: str, **_) -> pd.Series:
    """Group assets into high-level categories for system cost aggregation."""
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
            elif name_str.startswith("slack:"):
                categories.append("Slack penalties")
            elif carrier == "fertilizer":
                categories.append("Fertilizer (synthetic)")
            else:
                categories.append("Other generators")
        return pd.Series(categories, index=index, name="category")

    if component == "Link":
        mapping = {
            "produce": "Crop production",
            "trade": "Trade",
            "convert": "Processing",
            "consume": "Consumption",
        }
        categories = []
        for name in index:
            name_str = str(name)
            if name_str.startswith("biomass:"):
                categories.append("Biomass routing")
                continue
            categories.append(mapping.get(name_str.split(":", 1)[0], "Other links"))
        return pd.Series(categories, index=index, name="category")

    if component == "Store":
        carriers = static["carrier"].astype(str)
        categories = []
        for _name, carrier in zip(index, carriers):
            if carrier == "ghg":
                categories.append("GHG storage")
            elif carrier.startswith("yll_"):
                categories.append("Health burden")
            elif carrier.startswith("group_"):
                categories.append("Consumer values")
            else:
                categories.append("Other stores")
        return pd.Series(categories, index=index, name="category")

    return pd.Series(component, index=index, name="category")


def _extract_objective_breakdown_worker(args):
    """Worker function for parallel objective extraction."""
    network_path, constant_health_value, constant_ghg_price, usd_to_bnusd = args

    n = pypsa.Network(network_path)

    capex = n.statistics.capex(groupby=_objective_category)
    opex = n.statistics.opex(groupby=_objective_category)

    def _to_series(df_or_series):
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

    if "Health burden" in total.index:
        total = total.drop("Health burden")
    if "GHG storage" in total.index:
        total = total.drop("GHG storage")

    snapshot = n.snapshots[-1] if len(n.snapshots) > 0 else None

    if snapshot is not None and snapshot in n.stores.dynamic.e.index:
        health_stores = n.stores.static[n.stores.static.carrier.str.startswith("yll_")]
        if not health_stores.empty:
            health_levels = n.stores.dynamic.e.loc[snapshot, health_stores.index]
            total_myll = health_levels.sum()
            health_cost_bnusd = constant_health_value * total_myll * 1e6 * usd_to_bnusd
            total["Health burden"] = health_cost_bnusd

    if snapshot is not None and snapshot in n.stores.dynamic.e.index:
        ghg_stores = n.stores.static[n.stores.static.carrier == "ghg"]
        if not ghg_stores.empty:
            ghg_levels = n.stores.dynamic.e.loc[snapshot, ghg_stores.index]
            total_mtco2eq = ghg_levels.sum()
            ghg_cost_bnusd = constant_ghg_price * total_mtco2eq * 1e6 * usd_to_bnusd
            total["GHG cost"] = ghg_cost_bnusd

    total = total[total.abs() > 1e-6]
    return total.sort_values(ascending=False)


# -----------------------------------------------------------------------------
# Data loading from workflow analysis outputs
# -----------------------------------------------------------------------------


def load_consumption_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load consumption data from extract_statistics rule outputs.

    Reads pre-computed statistics CSVs from the workflow analysis outputs.

    Requires running the Snakemake extract_statistics rule first:
        tools/smk --configfile config/{name}.yaml -- results/{name}/analysis/scen-{scenario}/food_group_consumption.csv

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and food groups as columns (kcal/person/day)
    """
    population = load_population(project_root, config_name)
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir
            / "analysis"
            / f"scen-{scenario_name}"
            / "food_group_consumption.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Statistics file not found: {csv_path}. "
                f"Run the extract_statistics rule first."
            )

        df = pd.read_csv(csv_path)

        # Sum cal_pj across countries for each food group
        total_pj = df.groupby("food_group")["cal_pj"].sum()

        # Convert to global kcal/person/day
        kcal_per_person_day = total_pj * PJ_TO_KCAL / (population * DAYS_PER_YEAR)

        data[param_value] = kcal_per_person_day

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def extract_objective_data(
    scenarios: list[tuple[float, str, Path]],
    cache_path: Path,
    param_name: str = "param_value",
    constant_health_value: float = 10000,
    constant_ghg_price: float = 100,
    n_workers: int = 8,
) -> pd.DataFrame:
    """Extract objective breakdown data for all scenarios.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        cache_path: Path to cache file
        param_name: Name for the parameter (used as index name)
        constant_health_value: USD/YLL for health burden calculation
        constant_ghg_price: USD/tCO2eq for GHG cost calculation
        n_workers: Number of parallel workers

    Returns:
        DataFrame with param_value as index and cost categories as columns
    """
    network_paths = [f for _, _, f in scenarios]

    if is_cache_valid(cache_path, network_paths):
        print(f"Loading objective data from cache: {cache_path}")
        return pd.read_csv(cache_path, index_col=param_name)

    print(f"Extracting objective data using {n_workers} workers...")

    worker_args = [
        (network_path, constant_health_value, constant_ghg_price, USD_TO_BNUSD)
        for _, _, network_path in scenarios
    ]
    param_values = [pv for pv, _, _ in scenarios]

    objective_data = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(_extract_objective_breakdown_worker, args): pv
            for args, pv in zip(worker_args, param_values)
        }

        for future in as_completed(futures):
            param_value = futures[future]
            objective_data[param_value] = future.result()
            print(f"  Loaded {param_name}={int(param_value)}")

    df = pd.DataFrame(objective_data).T.fillna(0)
    df.index.name = param_name
    df = df.sort_index()

    df.to_csv(cache_path)
    print(f"Saved objective data to cache: {cache_path}")

    return df


def load_ghg_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load GHG emissions data from extract_ghg_intensity rule outputs.

    Reads pre-computed ghg_intensity.csv files from the workflow analysis outputs.

    Requires running the Snakemake extract_ghg_intensity rule first.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and food groups as columns (in GtCO2eq)
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "ghg_intensity.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"GHG intensity file not found: {csv_path}. "
                f"Run the extract_ghg_intensity rule first."
            )

        df = pd.read_csv(csv_path)

        # Compute total emissions per food_group:
        # consumption_mt * ghg_kgco2e_per_kg = MtCO2e (since kgCO2e/kg = MtCO2e/Mt)
        df["ghg_mtco2e"] = df["consumption_mt"] * df["ghg_kgco2e_per_kg"]

        # Sum by food_group and convert to GtCO2e
        totals = df.groupby("food_group")["ghg_mtco2e"].sum() / 1000

        data[param_value] = totals

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def load_objective_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
    constant_health_value: float = 10000,
    constant_ghg_price: float = 100,
) -> pd.DataFrame:
    """Load objective breakdown from extract_objective_breakdown rule outputs.

    Reads pre-computed objective_breakdown.csv, ghg_totals.csv, and
    health_totals.csv files. Recomputes health/GHG costs at constant prices
    for comparability across scenarios with different price assumptions.

    Requires running the Snakemake analysis rules first (extract_objective_breakdown,
    extract_ghg_intensity, extract_health_impacts).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)
        constant_health_value: USD/YLL for health burden calculation
        constant_ghg_price: USD/tCO2eq for GHG cost calculation

    Returns:
        DataFrame with param_value as index and cost categories as columns (billion USD)
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        analysis_dir = results_dir / "analysis" / f"scen-{scenario_name}"

        # Load objective breakdown
        obj_path = analysis_dir / "objective_breakdown.csv"
        if not obj_path.exists():
            raise FileNotFoundError(
                f"Objective breakdown file not found: {obj_path}. "
                f"Run the extract_objective_breakdown rule first."
            )
        obj_df = pd.read_csv(obj_path)

        # Start with the breakdown categories (excluding health/GHG cost columns
        # which we recompute at constant prices)
        row = {}
        skip_cols = {"health_burden", "ghg_cost"}
        for col in obj_df.columns:
            if col not in skip_cols:
                row[col] = obj_df[col].iloc[0]

        # Load GHG totals and compute cost at constant price
        ghg_totals_path = analysis_dir / "ghg_totals.csv"
        if ghg_totals_path.exists():
            ghg_totals_df = pd.read_csv(ghg_totals_path)
            ghg_mtco2eq = ghg_totals_df["ghg_mtco2eq"].sum()
            # MtCO2eq * USD/tCO2eq * 1e6 t/Mt * 1e-9 bn/USD = MtCO2eq * USD/tCO2eq * 1e-3
            ghg_cost_bnusd = ghg_mtco2eq * constant_ghg_price * 1e-3
            row["GHG cost"] = ghg_cost_bnusd

        # Load health totals and compute cost at constant price
        health_totals_path = analysis_dir / "health_totals.csv"
        if health_totals_path.exists():
            health_totals_df = pd.read_csv(health_totals_path)
            health_myll = health_totals_df["yll_myll"].sum()
            # MYLL * USD/YLL * 1e6 YLL/MYLL * 1e-9 bn/USD = MYLL * USD/YLL * 1e-3
            health_cost_bnusd = health_myll * constant_health_value * 1e-3
            row["Health burden"] = health_cost_bnusd

        data[param_value] = pd.Series(row)

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name

    # Rename columns to human-readable format
    column_map = {
        "crop_production": "Crop production",
        "trade": "Trade",
        "fertilizer": "Fertilizer (synthetic)",
        "processing": "Processing",
        "consumption": "Consumption",
        "animal_production": "Animal production",
        "feed_conversion": "Feed conversion",
        "consumer_values": "Consumer values",
        "biomass_exports": "Biomass exports",
        "biomass_routing": "Biomass routing",
        "slack_penalties": "Slack penalties",
        "resource_supply": "Resource supply",
        "nutrient_tracking": "Nutrient tracking",
    }
    result = result.rename(columns=column_map)

    return result.sort_index()


# -----------------------------------------------------------------------------
# Health cost attribution functions
# -----------------------------------------------------------------------------


def _load_health_tables(
    processing_dir: Path,
    network: pypsa.Network | None = None,
) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame, dict]:
    """Load health data tables from processing directory.

    Args:
        processing_dir: Path to processing directory
        network: Optional network to get cluster population from embedded metadata

    Returns:
        Tuple of (cluster_lookup, cluster_population, risk_breakpoints,
                  cluster_cause_baseline, tmrel_g_per_day)
    """
    health_dir = processing_dir / "health"

    # Country to cluster mapping
    country_clusters = pd.read_csv(health_dir / "country_clusters.csv")
    cluster_lookup = dict(
        zip(country_clusters["country_iso3"], country_clusters["health_cluster"])
    )

    # Get cluster population from network metadata if available
    if network is not None:
        pop_meta = network.meta.get("population")
        if pop_meta is not None and "health_cluster" in pop_meta:
            # Convert string keys to int (JSON serialization)
            cluster_population = {
                int(k): float(v) for k, v in pop_meta["health_cluster"].items()
            }
        else:
            cluster_population = _load_cluster_population_fallback(
                health_dir, cluster_lookup
            )
    else:
        cluster_population = _load_cluster_population_fallback(
            health_dir, cluster_lookup
        )

    # Risk breakpoints for log(RR) lookup
    risk_breakpoints = pd.read_csv(health_dir / "risk_breakpoints.csv")

    # Baseline YLL and RR data per (cluster, cause)
    cluster_cause_baseline = pd.read_csv(health_dir / "cluster_cause_baseline.csv")

    # TMREL values per risk factor
    tmrel_df = pd.read_csv(health_dir / "derived_tmrel.csv")
    tmrel_g_per_day = dict(zip(tmrel_df["risk_factor"], tmrel_df["tmrel_g_per_day"]))

    return (
        cluster_lookup,
        cluster_population,
        risk_breakpoints,
        cluster_cause_baseline,
        tmrel_g_per_day,
    )


def _load_cluster_population_fallback(
    health_dir: Path, cluster_lookup: dict
) -> dict[int, float]:
    """Fallback: load cluster population from CSV files."""
    cluster_summary = pd.read_csv(health_dir / "cluster_summary.csv")
    return {
        int(k): float(v)
        for k, v in zip(
            cluster_summary["health_cluster"], cluster_summary["population_persons"]
        )
    }


def _interpolate_log_rr(
    intake: float, breakpoints: pd.DataFrame, risk_factor: str, cause: str
) -> float:
    """Interpolate log(RR) from breakpoints for given intake.

    Args:
        intake: Intake in g/day
        breakpoints: DataFrame with risk_factor, cause, intake_g_per_day, log_rr
        risk_factor: Risk factor name
        cause: Disease cause name

    Returns:
        Interpolated log(RR) value
    """
    mask = (breakpoints["risk_factor"] == risk_factor) & (breakpoints["cause"] == cause)
    bp = breakpoints.loc[mask].sort_values("intake_g_per_day")

    if bp.empty:
        return 0.0

    return float(np.interp(intake, bp["intake_g_per_day"], bp["log_rr"]))


def _extract_health_by_risk_factor_worker(args):
    """Worker function to extract health costs attributed to each risk factor.

    Steps:
    1. Load network and get food group store levels
    2. Aggregate by cluster to get per-capita intakes (g/day)
    3. Look up log(RR) for each (cluster, risk_factor, cause)
    4. Compute excess log(RR) relative to TMREL for proper attribution
    5. Proportionally allocate YLL based on excess log(RR)
    6. Sum across clusters and causes, return by risk factor
    """
    (
        network_path,
        processing_dir,
        grams_per_mt,
        days_per_year,
    ) = args

    # Load network first to access embedded population
    n = pypsa.Network(network_path)

    # Load health data tables (uses embedded cluster population from network)
    (
        cluster_lookup,
        cluster_population,
        risk_breakpoints,
        cluster_cause_baseline,
        tmrel_g_per_day,
    ) = _load_health_tables(processing_dir, network=n)

    # Get unique risk factors from breakpoints
    risk_factors = risk_breakpoints["risk_factor"].unique().tolist()

    # Build risk to causes mapping
    risk_cause_map = {}
    for rf in risk_factors:
        rf_causes = (
            risk_breakpoints.loc[risk_breakpoints["risk_factor"] == rf, "cause"]
            .unique()
            .tolist()
        )
        risk_cause_map[rf] = rf_causes

    # Precompute log(RR) at TMREL for each (risk_factor, cause) pair
    # This is the reference point for computing "excess" risk
    log_rr_at_tmrel = {}  # (rf, cause) -> log_rr
    for rf in risk_factors:
        tmrel_intake = tmrel_g_per_day.get(rf, 0.0)
        for cause in risk_cause_map.get(rf, []):
            log_rr_tmrel = _interpolate_log_rr(
                tmrel_intake, risk_breakpoints, rf, cause
            )
            log_rr_at_tmrel[(rf, cause)] = log_rr_tmrel

    # Get snapshot (network already loaded above)
    snapshot = n.snapshots[-1]

    # Get store levels at the snapshot
    if snapshot in n.stores.dynamic.e.index:
        store_levels = n.stores.dynamic.e.loc[snapshot]
    else:
        store_levels = pd.Series(dtype=float)

    # 1. Compute cluster intakes from food group stores
    # Stores with carrier group_{risk_factor} hold consumption
    cluster_intakes = {}  # (cluster, risk_factor) -> g/day per capita

    for rf in risk_factors:
        carrier = f"group_{rf}"
        fg_stores = n.stores.static[n.stores.static["carrier"] == carrier]

        if fg_stores.empty:
            continue

        for store_name in fg_stores.index:
            level_mt = store_levels.get(store_name, 0.0)
            if level_mt <= 0:
                continue

            country = fg_stores.at[store_name, "country"]
            if pd.isna(country):
                continue

            cluster = cluster_lookup.get(country)
            if cluster is None:
                continue

            key = (cluster, rf)
            if key not in cluster_intakes:
                cluster_intakes[key] = 0.0
            cluster_intakes[key] += level_mt * grams_per_mt

    # Convert to g/day per capita
    for (cluster, rf), total_grams in list(cluster_intakes.items()):
        pop = cluster_population.get(cluster, 0)
        if pop > 0:
            cluster_intakes[(cluster, rf)] = total_grams / (days_per_year * pop)
        else:
            cluster_intakes[(cluster, rf)] = 0.0

    # 2. Look up log(RR) for each (cluster, risk_factor, cause)
    log_rr_values = {}  # (cluster, rf, cause) -> log_rr

    for (cluster, rf), intake in cluster_intakes.items():
        for cause in risk_cause_map.get(rf, []):
            log_rr = _interpolate_log_rr(intake, risk_breakpoints, rf, cause)
            log_rr_values[(cluster, rf, cause)] = log_rr

    # 3. Compute attributed YLL using proportional allocation based on EXCESS log(RR)
    attributed_yll = {}  # rf -> MYLL

    for cluster in cluster_population:
        cluster_rows = cluster_cause_baseline[
            cluster_cause_baseline["health_cluster"] == cluster
        ]

        for _, row in cluster_rows.iterrows():
            cause = row["cause"]
            rr_ref = np.exp(row["log_rr_total_ref"])
            rr_baseline = np.exp(row["log_rr_total_baseline"])

            # Reconstruct absolute YLL from rate using planning-year population
            yll_rate_per_100k = row["yll_rate_per_100k"]
            pop = cluster_population[cluster]
            yll_total = (yll_rate_per_100k / PER_100K) * pop

            # Compute EXCESS log(RR) for each risk factor relative to TMREL
            # excess = log(RR(x)) - log(RR(tmrel))
            # For protective foods at TMREL: excess ≈ 0 (no contribution)
            # For protective foods below TMREL: excess > 0 (contributing to burden)
            # For harmful foods (TMREL=0): excess = log(RR(x)) - 0 = log(RR(x))
            excess_contributions = {}
            for rf in risk_factors:
                key = (cluster, rf, cause)
                log_rr_current = log_rr_values.get(key, 0.0)
                log_rr_tmrel = log_rr_at_tmrel.get((rf, cause), 0.0)
                # Excess is how much worse than optimal; should be >= 0
                excess = max(0.0, log_rr_current - log_rr_tmrel)
                excess_contributions[rf] = excess

            # Total excess log(RR) for weighting
            total_excess = sum(excess_contributions.values())

            if total_excess <= 0:
                continue

            # Compute actual RR and YLL for this (cluster, cause)
            log_rr_total = sum(
                log_rr_values.get((cluster, rf, cause), 0.0) for rf in risk_factors
            )
            rr_total = np.exp(log_rr_total)

            # YLL = (RR - RR_ref) * (yll_total / RR_baseline) * 1e-6
            # This matches the health module formula
            yll_myll = (rr_total - rr_ref) * (yll_total / rr_baseline) * 1e-6

            # Alternative: read actual YLL from health stores instead of recomputing.
            # This gives totals that match the solver exactly, but the formula-based
            # approach above is more transparent and matches the documented methodology.
            # store_name = f"yll_{cause}_cluster{cluster:03d}"
            # yll_myll = store_levels.get(store_name, 0.0)

            # Skip if negligible or negative (shouldn't happen but be safe)
            if yll_myll <= 0:
                continue

            # Proportionally allocate to risk factors based on excess log(RR)
            for rf, excess in excess_contributions.items():
                if excess > 0:
                    weight = excess / total_excess
                    if rf not in attributed_yll:
                        attributed_yll[rf] = 0.0
                    attributed_yll[rf] += weight * yll_myll

    return pd.Series(attributed_yll, dtype=float)


def extract_health_data(
    scenarios: list[tuple[float, str, Path]],
    processing_dir: Path,
    cache_path: Path,
    param_name: str = "param_value",
    n_workers: int = 8,
) -> pd.DataFrame:
    """Extract health cost attribution by risk factor for all scenarios.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        processing_dir: Path to processing directory (for health data)
        cache_path: Path to cache file
        param_name: Name for the parameter (used as index name)
        n_workers: Number of parallel workers

    Returns:
        DataFrame with param_value as index and risk_factors as columns (in MYLL)
    """
    network_paths = [f for _, _, f in scenarios]

    if is_cache_valid(cache_path, network_paths):
        print(f"Loading health data from cache: {cache_path}")
        return pd.read_csv(cache_path, index_col=param_name)

    print(f"Extracting health data using {n_workers} workers...")

    worker_args = [
        (network_path, processing_dir, GRAMS_PER_MEGATONNE, DAYS_PER_YEAR)
        for _, _, network_path in scenarios
    ]
    param_values = [pv for pv, _, _ in scenarios]

    health_data = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(_extract_health_by_risk_factor_worker, args): pv
            for args, pv in zip(worker_args, param_values)
        }

        for future in as_completed(futures):
            param_value = futures[future]
            health_data[param_value] = future.result()
            print(f"  Loaded {param_name}={int(param_value)}")

    df = pd.DataFrame(health_data).T.fillna(0)
    df.index.name = param_name
    df = df.sort_index()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path)
    print(f"Saved health data to cache: {cache_path}")

    return df


# -----------------------------------------------------------------------------
# Data preparation utilities
# -----------------------------------------------------------------------------


def aggregate_food_groups(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate food groups: combine fruits+vegetables and eggs+poultry."""
    df_plot = df.copy()

    if "fruits" in df_plot.columns and "vegetables" in df_plot.columns:
        df_plot["fruits_vegetables"] = df_plot["fruits"] + df_plot["vegetables"]
        df_plot = df_plot.drop(columns=["fruits", "vegetables"])

    if "eggs" in df_plot.columns and "poultry" in df_plot.columns:
        df_plot["eggs_poultry"] = df_plot["eggs"] + df_plot["poultry"]
        df_plot = df_plot.drop(columns=["eggs", "poultry"])

    return df_plot


def prepare_objective_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare objective data: aggregate fertilizer into crop production and order categories."""
    df_obj = df.copy()

    if (
        "Fertilizer (synthetic)" in df_obj.columns
        and "Crop production" in df_obj.columns
    ):
        df_obj["Crop production"] = (
            df_obj["Crop production"] + df_obj["Fertilizer (synthetic)"]
        )
        df_obj = df_obj.drop(columns=["Fertilizer (synthetic)"])

    priority_order = ["Crop production", "Trade"]
    other_cats = [c for c in df_obj.columns if c not in priority_order]
    other_cats_sorted = (
        df_obj[other_cats].mean().sort_values(ascending=False).index.tolist()
    )
    cat_order = [c for c in priority_order if c in df_obj.columns] + other_cats_sorted

    return df_obj[cat_order]


def assign_food_colors(df: pd.DataFrame) -> dict:
    """Assign tab20 colors to food groups based on consumption at minimum x-value."""
    cmap = plt.colormaps["tab20"]
    min_val = df.index.min()
    group_order = df.loc[min_val].sort_values(ascending=False).index.tolist()
    return {group: cmap(i) for i, group in enumerate(group_order)}


# -----------------------------------------------------------------------------
# Plotting functions
# -----------------------------------------------------------------------------


def set_dual_xaxis_labels(
    ax: plt.Axes,
    x_ticks: list[float],
    ghg_values: list[float],
    yll_values: list[float],
    ghg_color: str = "darkgreen",
    yll_color: str = "darkblue",
    fontsize: int = FONTSIZE_TICK_LABEL,
):
    """Set up dual-colored x-axis tick labels showing both GHG price and YLL value.

    Args:
        ax: Matplotlib axes
        x_ticks: X-axis tick positions
        ghg_values: GHG price values for each tick
        yll_values: YLL values for each tick
        ghg_color: Color for GHG labels
        yll_color: Color for YLL labels
        fontsize: Font size for tick labels
    """
    ax.set_xticks(x_ticks)
    ax.set_xticklabels([])  # Clear default labels

    # Get axis transform for positioning
    trans = ax.get_xaxis_transform()

    # Add colored labels below axis
    for x, ghg, yll in zip(x_ticks, ghg_values, yll_values):
        # Format values nicely
        ghg_str = f"{int(ghg)}" if ghg < 1000 else f"{int(ghg / 1000)}k"
        yll_str = f"{int(yll)}" if yll < 1000 else f"{int(yll / 1000)}k"

        # GHG label (top, dark green)
        ax.text(
            x,
            -0.02,
            ghg_str,
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            color=ghg_color,
            fontweight="bold",
        )
        # YLL label (bottom, dark blue)
        ax.text(
            x,
            -0.08,
            yll_str,
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            color=yll_color,
            fontweight="bold",
        )


def set_dual_xlabel(
    ax: plt.Axes,
    ghg_color: str = "darkgreen",
    yll_color: str = "darkblue",
    fontsize: int = FONTSIZE_AXIS_LABEL,
):
    """Set dual-colored x-axis label for combined GHG/YLL sensitivity.

    Args:
        ax: Matplotlib axes
        ghg_color: Color for GHG part
        yll_color: Color for YLL part
        fontsize: Font size
    """
    # Use a two-line xlabel with colored text
    ax.set_xlabel("")  # Clear default

    ax.text(
        0.5,
        -0.18,
        "GHG price [USD/tCO2eq]",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=fontsize,
        color=ghg_color,
    )
    ax.text(
        0.5,
        -0.26,
        "Health value [USD/YLL]",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=fontsize,
        color=yll_color,
    )


def plot_stacked_sensitivity(
    df: pd.DataFrame,
    colors: dict,
    ax: plt.Axes,
    xlabel: str,
    ylabel: str,
    panel_label: str,
    x_ticks: list[float],
    x_ticklabels: list[str],
    label_x_positions: dict | None = None,
    label_skip: set | None = None,
    min_height_for_label: float = 30,
    y_max: float | None = None,
    pretty_names: dict | None = None,
):
    """Create a stacked area plot with logarithmic x-axis.

    Args:
        df: DataFrame with parameter values as index and groups as columns
        colors: Dict mapping group names to colors
        ax: Matplotlib axes to plot on
        xlabel: X-axis label
        ylabel: Y-axis label
        panel_label: Panel label (e.g., 'a', 'b', 'c')
        x_ticks: X-axis tick positions (in original scale, 0 maps to 1)
        x_ticklabels: X-axis tick labels
        label_x_positions: Manual x-positions for labels (optional)
        label_skip: Set of group names to skip labeling (optional)
        min_height_for_label: Minimum height to show a label
        y_max: Maximum y-axis value (optional)
        pretty_names: Custom pretty names dict (falls back to PRETTY_NAMES)
    """
    if label_x_positions is None:
        label_x_positions = {}
    if label_skip is None:
        label_skip = set()
    if pretty_names is None:
        pretty_names = PRETTY_NAMES

    x_values = df.index.values
    groups = df.columns.tolist()

    # Handle x=0 for log scale: calculate position for even spacing
    zero_pos = log_scale_zero_position(x_values)
    x_plot = np.where(x_values == 0, zero_pos, x_values)

    x_min, x_max = zero_pos, x_plot.max() * 1.1
    x_smooth = np.logspace(np.log10(x_min), np.log10(x_max), 200)

    y_smooth = {}
    for group in groups:
        y_smooth[group] = np.interp(
            np.log10(x_smooth), np.log10(x_plot), df[group].values
        )

    y_stacks = [np.zeros(len(x_smooth))]
    for group in groups:
        y_stacks.append(y_stacks[-1] + y_smooth[group])

    for i, group in enumerate(groups):
        y_bottom = y_stacks[i]
        y_top = y_stacks[i + 1]
        ax.fill_between(
            x_smooth,
            y_bottom,
            y_top,
            label=group,
            color=colors[group],
            alpha=0.8,
            edgecolor="none",
            linewidth=0,
        )

    # Add labels
    label_fontsize = FONTSIZE_TICK_LABEL
    bbox_style = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }

    log_x_min, log_x_max = np.log10(x_min), np.log10(x_max)
    margin_frac = 0.15
    log_margin = (log_x_max - log_x_min) * margin_frac

    for i, group in enumerate(groups):
        if group in label_skip:
            continue

        heights = y_smooth[group]
        max_height = heights.max()

        if max_height < min_height_for_label:
            continue

        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]

        if group in label_x_positions:
            x_pos = label_x_positions[group]

        log_x_pos = np.log10(x_pos)
        log_x_pos = np.clip(log_x_pos, log_x_min + log_margin, log_x_max - log_margin)
        x_pos = 10**log_x_pos

        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_bottom = y_stacks[i]
        y_top = y_stacks[i + 1]
        y_pos = (y_bottom[idx] + y_top[idx]) / 2

        label_text = pretty_names.get(group, PRETTY_NAMES.get(group, group))
        ax.text(
            x_pos,
            y_pos,
            label_text,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    ax.set_xscale("log")
    ax.set_xlabel(xlabel, fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel(ylabel, fontsize=FONTSIZE_AXIS_LABEL)

    ax.text(
        -0.10,
        1.05,
        panel_label,
        transform=ax.transAxes,
        fontsize=FONTSIZE_PANEL_LABEL,
        fontweight="bold",
        va="top",
        ha="left",
    )

    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_ticklabels)
    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)
    ax.set_xlim(x_min, x_max)
    if y_max is not None:
        ax.set_ylim(0, y_max)
    else:
        ax.set_ylim(0, None)

    ax.grid(True, alpha=0.3, which="both")
    ax.set_axisbelow(True)


def plot_objective_sensitivity(
    df: pd.DataFrame,
    ax: plt.Axes,
    xlabel: str,
    panel_label: str,
    x_ticks: list[float],
    x_ticklabels: list[str],
    health_value: float | None = None,
    ghg_price: float | None = None,
    label_x_positions: dict | None = None,
    highlight_cat: str | None = None,
):
    """Create stacked area plot for objective breakdown with positive/negative categories.

    Args:
        df: DataFrame with parameter values as index and cost categories as columns
        ax: Matplotlib axes to plot on
        xlabel: X-axis label
        panel_label: Panel label (e.g., 'c')
        x_ticks: X-axis tick positions
        x_ticklabels: X-axis tick labels
        health_value: Health value to display in note box
        ghg_price: GHG price to display in note box
        label_x_positions: Manual x-positions for labels (optional)
        highlight_cat: Category to highlight with hatching (None for no highlighting)
    """
    if label_x_positions is None:
        label_x_positions = {}

    x_values = df.index.values
    categories = df.columns.tolist()

    zero_pos = log_scale_zero_position(x_values)
    x_plot = np.where(x_values == 0, zero_pos, x_values)
    x_min, x_max = zero_pos, x_plot.max() * 1.1
    x_smooth = np.logspace(np.log10(x_min), np.log10(x_max), 200)

    y_smooth = {}
    for cat in categories:
        y_smooth[cat] = np.interp(np.log10(x_smooth), np.log10(x_plot), df[cat].values)

    min_magnitude = 1.0

    crossing_cats = []
    purely_pos_cats = []
    purely_neg_cats = []

    for cat in categories:
        y = y_smooth[cat]
        max_abs = np.max(np.abs(y))
        if max_abs < min_magnitude:
            continue

        has_pos = np.any(y > 1e-6)
        has_neg = np.any(y < -1e-6)
        if has_pos and has_neg:
            crossing_cats.append(cat)
        elif has_pos:
            purely_pos_cats.append(cat)
        elif has_neg:
            purely_neg_cats.append(cat)

    y_smooth_split = {}
    cmap_obj = plt.colormaps["tab20c"]

    for cat in purely_pos_cats:
        y_smooth_split[cat] = y_smooth[cat]
    for cat in purely_neg_cats:
        y_smooth_split[cat] = y_smooth[cat]

    for cat in crossing_cats:
        y = y_smooth[cat]
        y_pos = np.maximum(y, 0)
        y_neg = np.minimum(y, 0)

        if np.max(y_pos) > min_magnitude:
            pos_name = f"{cat} (positive)"
            y_smooth_split[pos_name] = y_pos
            purely_pos_cats.append(pos_name)

        if np.min(y_neg) < -min_magnitude:
            neg_name = f"{cat} (negative)"
            y_smooth_split[neg_name] = y_neg
            purely_neg_cats.append(neg_name)

    if highlight_cat is not None and highlight_cat in purely_pos_cats:
        purely_pos_cats.remove(highlight_cat)
        purely_pos_cats.append(highlight_cat)

    split_colors = {}
    for i, cat in enumerate(purely_pos_cats):
        if cat == highlight_cat:
            split_colors[cat] = "grey"
        else:
            split_colors[cat] = cmap_obj(4 + (i % 4))
    for i, cat in enumerate(purely_neg_cats):
        split_colors[cat] = cmap_obj(i % 4)

    y_pos_stacks = [np.zeros(len(x_smooth))]
    for cat in purely_pos_cats:
        y_pos_stacks.append(y_pos_stacks[-1] + y_smooth_split[cat])

    y_neg_stacks = [np.zeros(len(x_smooth))]
    for cat in purely_neg_cats:
        y_neg_stacks.append(y_neg_stacks[-1] + y_smooth_split[cat])

    for i, cat in enumerate(purely_pos_cats):
        y_bottom = y_pos_stacks[i]
        y_top = y_pos_stacks[i + 1]
        if cat == highlight_cat:
            ax.fill_between(
                x_smooth,
                y_bottom,
                y_top,
                label=cat,
                color=split_colors[cat],
                alpha=0.4,
                edgecolor="none",
                linewidth=0,
                hatch="///",
            )
        else:
            ax.fill_between(
                x_smooth,
                y_bottom,
                y_top,
                label=cat,
                color=split_colors[cat],
                alpha=0.8,
                edgecolor="none",
                linewidth=0,
            )

    for i, cat in enumerate(purely_neg_cats):
        y_top = y_neg_stacks[i]
        y_bottom = y_neg_stacks[i + 1]
        ax.fill_between(
            x_smooth,
            y_bottom,
            y_top,
            label=cat,
            color=split_colors[cat],
            alpha=0.8,
            edgecolor="none",
            linewidth=0,
        )

    label_fontsize = FONTSIZE_TICK_LABEL
    bbox_style = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }

    log_x_min, log_x_max = np.log10(x_min), np.log10(x_max)
    margin_frac = 0.15
    log_margin = (log_x_max - log_x_min) * margin_frac

    for i, cat in enumerate(purely_pos_cats):
        heights = y_smooth_split[cat]
        max_height = heights.max()

        if max_height < 15:
            continue

        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]

        if cat in label_x_positions:
            x_pos = label_x_positions[cat]

        log_x_pos = np.log10(x_pos)
        log_x_pos = np.clip(log_x_pos, log_x_min + log_margin, log_x_max - log_margin)
        x_pos = 10**log_x_pos

        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_bottom = y_pos_stacks[i]
        y_top = y_pos_stacks[i + 1]
        y_pos = (y_bottom[idx] + y_top[idx]) / 2

        pretty_name = PRETTY_NAMES_OBJ.get(cat, cat)
        ax.text(
            x_pos,
            y_pos,
            pretty_name,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    for i, cat in enumerate(purely_neg_cats):
        heights = np.abs(y_smooth_split[cat])
        max_height = heights.max()

        if max_height < 15:
            continue

        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]

        if cat in label_x_positions:
            x_pos = label_x_positions[cat]

        log_x_pos = np.log10(x_pos)
        log_x_pos = np.clip(log_x_pos, log_x_min + log_margin, log_x_max - log_margin)
        x_pos = 10**log_x_pos

        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_top = y_neg_stacks[i]
        y_bottom = y_neg_stacks[i + 1]
        y_pos = (y_bottom[idx] + y_top[idx]) / 2

        pretty_name = PRETTY_NAMES_OBJ.get(cat, cat)
        ax.text(
            x_pos,
            y_pos,
            pretty_name,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    if health_value is not None or ghg_price is not None:
        note_lines = ["Fixed in this plot:"]
        if health_value is not None:
            note_lines.append(f"  Health: ${health_value:,.0f}/YLL")
        if ghg_price is not None:
            note_lines.append(f"  GHG: ${ghg_price:,.0f}/tCO2eq")
        note_text = "\n".join(note_lines)
        note_bbox = {
            "boxstyle": "round,pad=0.3",
            "facecolor": "lightyellow",
            "edgecolor": "none",
            "alpha": 0.9,
        }
        ax.text(
            0.98,
            0.97,
            note_text,
            transform=ax.transAxes,
            fontsize=FONTSIZE_TICK_LABEL,
            va="top",
            ha="right",
            bbox=note_bbox,
        )

    ax.set_xscale("log")
    ax.set_xlabel(xlabel, fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("Cost [billion USD]", fontsize=FONTSIZE_AXIS_LABEL)

    ax.text(
        -0.10,
        1.05,
        panel_label,
        transform=ax.transAxes,
        fontsize=FONTSIZE_PANEL_LABEL,
        fontweight="bold",
        va="top",
        ha="left",
    )

    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_ticklabels)
    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)
    ax.set_xlim(x_min, x_max)

    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3, which="both")
    ax.set_axisbelow(True)


# -----------------------------------------------------------------------------
# Grid data loading and heatmap plotting
# -----------------------------------------------------------------------------


def load_grid_data_from_statistics(
    scenarios: list[tuple[float, float, str, Path]],
    project_root: Path,
    config_name: str,
) -> pd.DataFrame:
    """Load grid data from pre-computed analysis CSVs.

    Args:
        scenarios: List of (ghg_price, yll_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll_grid')

    Returns:
        DataFrame with MultiIndex (ghg_price, yll_value) and columns for
        cost components (billion USD), health_myll, and ghg_mtco2eq.
    """
    results_dir = project_root / "results" / config_name

    grid_data = {}
    for ghg_price, yll_value, scenario_name, _ in scenarios:
        analysis_dir = results_dir / "analysis" / f"scen-{scenario_name}"

        obj_df = pd.read_csv(analysis_dir / "objective_breakdown.csv")
        ghg_df = pd.read_csv(analysis_dir / "ghg_totals.csv")
        health_df = pd.read_csv(analysis_dir / "health_totals.csv")

        grid_data[(ghg_price, yll_value)] = {
            "crop_production": obj_df["crop_production"].iloc[0],
            "trade": obj_df["trade"].iloc[0],
            "consumer_values": obj_df.get("consumer_values", pd.Series([0.0])).iloc[0],
            "fertilizer": obj_df["fertilizer"].iloc[0],
            "ghg_mtco2eq": ghg_df["ghg_mtco2eq"].sum(),
            "health_myll": health_df["yll_myll"].sum(),
            "total_objective": obj_df.iloc[0].sum(),
        }

    df = pd.DataFrame(grid_data).T
    df.index = pd.MultiIndex.from_tuples(df.index, names=["ghg_price", "yll_value"])
    return df.sort_index()


def pivot_grid_data(
    df: pd.DataFrame,
    value_col: str,
) -> pd.DataFrame:
    """Pivot grid data into 2D matrix for heatmap plotting.

    Args:
        df: DataFrame with MultiIndex (ghg_price, yll_value)
        value_col: Column to pivot

    Returns:
        DataFrame with ghg_price as index (rows) and yll_value as columns
    """
    df_reset = df.reset_index()
    return df_reset.pivot(index="ghg_price", columns="yll_value", values=value_col)


def plot_heatmap(
    data: pd.DataFrame,
    ax: plt.Axes,
    title: str,
    cbar_label: str,
    cmap: str = "viridis",
    panel_label: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    log_scale_cbar: bool = False,
    baseline_value: float | None = None,
    baseline_label: str = "Baseline",
    cbar_orientation: str = "vertical",
):
    """Plot a heatmap with logarithmic axes.

    Args:
        data: 2D DataFrame with ghg_price as index and yll_value as columns
        ax: Matplotlib axes to plot on
        title: Plot title
        cbar_label: Colorbar label
        cmap: Colormap name
        panel_label: Panel label (e.g., 'a', 'b')
        vmin: Minimum value for colorbar
        vmax: Maximum value for colorbar
        log_scale_cbar: Whether to use log scale for colorbar
        baseline_value: Value to mark on colorbar (e.g., from (0,0) scenario)
        baseline_label: Label for the baseline marker
        cbar_orientation: Colorbar orientation ('vertical' or 'horizontal')
    """
    from matplotlib.colors import LogNorm

    ghg_values = data.index.values.astype(float)
    yll_values = data.columns.values.astype(float)

    # Create cell edges for pcolormesh in log space
    # For n data points, we need n+1 edges
    def log_edges(values):
        """Create cell edges in log space for given center values."""
        log_vals = np.log10(values)
        edges = np.zeros(len(values) + 1)
        # Interior edges are midpoints in log space
        for i in range(1, len(values)):
            edges[i] = 10 ** ((log_vals[i - 1] + log_vals[i]) / 2)
        # Exterior edges extend by half a cell width in log space
        if len(values) > 1:
            half_width_left = (log_vals[1] - log_vals[0]) / 2
            half_width_right = (log_vals[-1] - log_vals[-2]) / 2
        else:
            half_width_left = half_width_right = 0.5
        edges[0] = 10 ** (log_vals[0] - half_width_left)
        edges[-1] = 10 ** (log_vals[-1] + half_width_right)
        return edges

    yll_edges = log_edges(yll_values)
    ghg_edges = log_edges(ghg_values)

    if log_scale_cbar and vmin is not None and vmin > 0:
        norm = LogNorm(vmin=vmin, vmax=vmax)
        im = ax.pcolormesh(
            yll_edges,
            ghg_edges,
            data.values,
            cmap=cmap,
            norm=norm,
        )
    else:
        im = ax.pcolormesh(
            yll_edges,
            ghg_edges,
            data.values,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
        )

    ax.set_xscale("log")
    ax.set_yscale("log")

    # Set axis limits to match the cell edges
    ax.set_xlim(yll_edges[0], yll_edges[-1])
    ax.set_ylim(ghg_edges[0], ghg_edges[-1])

    # Set ticks at nice log positions plus extremes, no scientific notation
    from matplotlib.ticker import FuncFormatter, LogLocator

    def _format_tick(x, pos):
        """Format tick label without scientific notation."""
        if x >= 1000:
            if x == int(x):
                return f"{int(x):,}".replace(",", " ")
            return f"{x:,.0f}".replace(",", " ")
        elif x >= 1:
            return f"{int(x)}" if x == int(x) else f"{x:.1f}"
        else:
            return f"{x:.1f}"

    def _get_nice_ticks(vmin, vmax, sparse=False):
        """Get nice tick positions including extremes."""
        import math

        ticks = []
        decade_start = int(math.floor(math.log10(vmin)))
        decade_end = int(math.ceil(math.log10(vmax)))
        # Use sparser ticks (1, 5) for x-axis, denser (1, 2, 5) for y-axis
        mults = [1, 5] if sparse else [1, 2, 5]
        for decade in range(decade_start, decade_end + 1):
            base = 10**decade
            for mult in mults:
                val = base * mult
                if vmin <= val <= vmax:
                    ticks.append(val)
        # Add extremes if not close to existing ticks
        for extreme in [vmin, vmax]:
            if not any(abs(t - extreme) / extreme < 0.1 for t in ticks):
                ticks.append(extreme)
        return sorted(ticks)

    def _get_powers_of_10(vmin, vmax):
        """Get powers of 10 within range for major ticks."""
        import math

        powers = []
        start = int(math.floor(math.log10(vmin)))
        end = int(math.ceil(math.log10(vmax)))
        for exp in range(start, end + 1):
            val = 10**exp
            if vmin <= val <= vmax:
                powers.append(val)
        return powers

    # Get nice label positions and powers of 10
    x_nice = _get_nice_ticks(yll_values.min(), yll_values.max(), sparse=True)
    y_nice = _get_nice_ticks(ghg_values.min(), ghg_values.max(), sparse=False)
    x_powers = _get_powers_of_10(yll_values.min(), yll_values.max())
    y_powers = _get_powers_of_10(ghg_values.min(), ghg_values.max())

    # Major ticks at powers of 10 (longer marks)
    ax.set_xticks(x_powers)
    ax.set_yticks(y_powers)

    # Minor ticks at all integer multiples (1-9 within each decade)
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))

    # Custom formatters that show labels at nice positions
    def _make_selective_formatter(nice_ticks):
        def _formatter(x, pos):
            for t in nice_ticks:
                if abs(x - t) / t < 0.01:
                    return _format_tick(x, pos)
            return ""

        return _formatter

    ax.xaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(y_nice)))
    ax.xaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(y_nice)))

    # Rotate x-axis labels to avoid overlap and anchor at their tip
    # Use which="both" to apply to major and minor ticks
    ax.tick_params(
        axis="x", which="both", rotation=45, pad=2, labelsize=FONTSIZE_TICK_LABEL
    )
    ax.tick_params(axis="y", which="both", pad=2, labelsize=FONTSIZE_TICK_LABEL)
    plt.setp(ax.get_xticklabels(), ha="right")
    plt.setp(ax.xaxis.get_minorticklabels(), ha="right")

    ax.set_xlabel("YLL value [USD/YLL]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("GHG price [USD/tCO2eq]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_title(title, fontsize=FONTSIZE_TITLE)

    cbar = plt.colorbar(im, ax=ax, orientation=cbar_orientation, pad=0.25)
    cbar.set_label(cbar_label, fontsize=FONTSIZE_CBAR_LABEL)
    cbar.ax.tick_params(labelsize=FONTSIZE_TICK_LABEL)

    # Add baseline marker on colorbar
    if baseline_value is not None:
        cbar_vmin = vmin if vmin is not None else data.values.min()
        cbar_vmax = vmax if vmax is not None else data.values.max()
        # Only show marker if within colorbar range
        if cbar_vmin <= baseline_value <= cbar_vmax:
            if cbar_orientation == "horizontal":
                cbar.ax.axvline(x=baseline_value, color="black", linewidth=1.5)
            else:
                cbar.ax.axhline(y=baseline_value, color="black", linewidth=1.5)
                cbar.ax.plot(
                    1.0,
                    baseline_value,
                    marker="<",
                    color="black",
                    markersize=6,
                    transform=cbar.ax.get_yaxis_transform(),
                    clip_on=False,
                )

    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)

    # Make spines faint grey on plot and colorbar
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")
    for spine in cbar.ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")

    if panel_label is not None:
        ax.text(
            -0.05,
            1.12,
            panel_label,
            transform=ax.transAxes,
            fontsize=FONTSIZE_PANEL_LABEL,
            fontweight="bold",
            va="top",
            ha="left",
        )


def _nice_contour_levels(vmin: float, vmax: float, n_levels: int = 8) -> np.ndarray:
    """Generate nice round contour levels between vmin and vmax.

    Prefers values like 1, 2, 5, 10, 20, 50, 100, 200, 500, etc.
    """
    data_range = vmax - vmin
    if data_range <= 0:
        return np.array([vmin])

    # Estimate rough step size
    rough_step = data_range / n_levels

    # Find the order of magnitude
    magnitude = 10 ** np.floor(np.log10(rough_step))

    # Nice step multipliers
    nice_multipliers = [1, 2, 5, 10, 20, 50]

    # Find the best nice step
    best_step = None
    best_n = 0
    for mult in nice_multipliers:
        step = mult * magnitude
        n = int(np.floor((vmax - vmin) / step)) + 1
        if 4 <= n <= n_levels + 4 and (
            best_step is None or abs(n - n_levels) < abs(best_n - n_levels)
        ):
            best_step = step
            best_n = n

    if best_step is None:
        best_step = rough_step

    # Generate levels starting from a nice number
    start = np.ceil(vmin / best_step) * best_step
    levels = []
    level = start
    while level <= vmax:
        levels.append(level)
        level += best_step

    # Ensure we have at least the min and max in range
    if len(levels) == 0:
        levels = [vmin, vmax]

    return np.array(levels)


def plot_contour(
    data: pd.DataFrame,
    ax: plt.Axes,
    title: str,
    cbar_label: str,
    cmap: str = "viridis",
    panel_label: str | None = None,
    n_levels: int = 8,
    n_interp: int = 200,
    baseline_value: float | None = None,
    label_fmt: str = "%.0f",
    vmin: float | None = None,
    vmax: float | None = None,
    contour_levels: np.ndarray | None = None,
    cbar_orientation: str = "vertical",
    sigma: float = 0,
):
    """Plot smoothed contour lines with logarithmic axes.

    Args:
        data: 2D DataFrame with ghg_price as index and yll_value as columns
        ax: Matplotlib axes to plot on
        title: Plot title
        cbar_label: Colorbar label
        cmap: Colormap name
        panel_label: Panel label (e.g., 'a', 'b')
        n_levels: Approximate number of contour levels (used if contour_levels not provided)
        n_interp: Number of interpolation points per axis
        baseline_value: Value to highlight with a dashed contour line
        label_fmt: Format string for contour labels
        vmin: Minimum value for color scale
        vmax: Maximum value for color scale
        contour_levels: Explicit contour levels (overrides n_levels)
        cbar_orientation: Colorbar orientation ('vertical' or 'horizontal')
        sigma: Gaussian smoothing sigma (0 = no smoothing, applied on interpolated grid)
    """
    from scipy.interpolate import RegularGridInterpolator
    from scipy.ndimage import gaussian_filter

    ghg_values = data.index.values.astype(float)
    yll_values = data.columns.values.astype(float)

    # Create fine grid in log space for smooth contours
    log_ghg = np.log10(ghg_values)
    log_yll = np.log10(yll_values)

    log_ghg_fine = np.linspace(log_ghg.min(), log_ghg.max(), n_interp)
    log_yll_fine = np.linspace(log_yll.min(), log_yll.max(), n_interp)

    # Interpolate data onto fine grid using cubic interpolation
    interp = RegularGridInterpolator(
        (log_ghg, log_yll),
        data.values,
        method="cubic",
        bounds_error=False,
        fill_value=None,
    )

    log_yll_mesh, log_ghg_mesh = np.meshgrid(log_yll_fine, log_ghg_fine)
    points = np.column_stack([log_ghg_mesh.ravel(), log_yll_mesh.ravel()])
    z_fine = interp(points).reshape(log_ghg_mesh.shape)

    # Apply Gaussian smoothing if sigma > 0
    if sigma > 0:
        z_fine = gaussian_filter(z_fine, sigma=sigma)

    # Convert back to linear scale for plotting
    yll_fine = 10**log_yll_fine
    ghg_fine = 10**log_ghg_fine

    # Color scale limits
    color_vmin = vmin if vmin is not None else data.values.min()
    color_vmax = vmax if vmax is not None else data.values.max()

    # Plot continuous colors using pcolormesh
    im = ax.pcolormesh(
        yll_fine,
        ghg_fine,
        z_fine,
        cmap=cmap,
        vmin=color_vmin,
        vmax=color_vmax,
        shading="gouraud",
    )

    # Compute nice contour levels
    if contour_levels is None:
        contour_levels = _nice_contour_levels(color_vmin, color_vmax, n_levels)

    # Add contour lines at nice levels
    cs = ax.contour(
        yll_fine,
        ghg_fine,
        z_fine,
        levels=contour_levels,
        colors="black",
        linewidths=0.5,
        alpha=0.8,
    )

    # Add contour labels (horizontal to avoid rotation issues with log scales)
    label_bbox = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }
    clabels = ax.clabel(
        cs, inline=False, fontsize=FONTSIZE_CONTOUR_LABEL, fmt=label_fmt
    )
    for label in clabels:
        label.set_rotation(0)
        label.set_bbox(label_bbox)
        label.set_clip_on(True)

    # Add baseline contour if provided
    if baseline_value is not None:
        cs_baseline = ax.contour(
            yll_fine,
            ghg_fine,
            z_fine,
            levels=[baseline_value],
            colors="black",
            linewidths=2,
            linestyles="dashed",
        )
        clabels_baseline = ax.clabel(
            cs_baseline,
            inline=False,
            fontsize=FONTSIZE_CONTOUR_LABEL + 1,
            fmt=label_fmt,
        )
        for label in clabels_baseline:
            label.set_rotation(0)
            label.set_bbox(label_bbox)
            label.set_clip_on(True)

    ax.set_xscale("log")
    ax.set_yscale("log")

    # Set ticks at nice log positions plus extremes, no scientific notation
    from matplotlib.ticker import FuncFormatter, LogLocator

    def _format_tick(x, pos):
        """Format tick label without scientific notation."""
        if x >= 1000:
            if x == int(x):
                return f"{int(x):,}".replace(",", " ")
            return f"{x:,.0f}".replace(",", " ")
        elif x >= 1:
            return f"{int(x)}" if x == int(x) else f"{x:.1f}"
        else:
            return f"{x:.1f}"

    def _get_nice_ticks(vmin, vmax, sparse=False):
        """Get nice tick positions including extremes."""
        import math

        ticks = []
        decade_start = int(math.floor(math.log10(vmin)))
        decade_end = int(math.ceil(math.log10(vmax)))
        # Use sparser ticks (1, 5) for x-axis, denser (1, 2, 5) for y-axis
        mults = [1, 5] if sparse else [1, 2, 5]
        for decade in range(decade_start, decade_end + 1):
            base = 10**decade
            for mult in mults:
                val = base * mult
                if vmin <= val <= vmax:
                    ticks.append(val)
        # Add extremes if not close to existing ticks
        for extreme in [vmin, vmax]:
            if not any(abs(t - extreme) / extreme < 0.1 for t in ticks):
                ticks.append(extreme)
        return sorted(ticks)

    def _get_powers_of_10(vmin, vmax):
        """Get powers of 10 within range for major ticks."""
        import math

        powers = []
        start = int(math.floor(math.log10(vmin)))
        end = int(math.ceil(math.log10(vmax)))
        for exp in range(start, end + 1):
            val = 10**exp
            if vmin <= val <= vmax:
                powers.append(val)
        return powers

    # Get nice label positions and powers of 10
    x_nice = _get_nice_ticks(yll_values.min(), yll_values.max(), sparse=True)
    y_nice = _get_nice_ticks(ghg_values.min(), ghg_values.max(), sparse=False)
    x_powers = _get_powers_of_10(yll_values.min(), yll_values.max())
    y_powers = _get_powers_of_10(ghg_values.min(), ghg_values.max())

    # Major ticks at powers of 10 (longer marks)
    ax.set_xticks(x_powers)
    ax.set_yticks(y_powers)

    # Minor ticks at all integer multiples (1-9 within each decade)
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))

    # Custom formatters that show labels at nice positions
    def _make_selective_formatter(nice_ticks):
        def _formatter(x, pos):
            for t in nice_ticks:
                if abs(x - t) / t < 0.01:
                    return _format_tick(x, pos)
            return ""

        return _formatter

    ax.xaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(y_nice)))
    ax.xaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(y_nice)))

    # Rotate x-axis labels to avoid overlap and anchor at their tip
    # Use which="both" to apply to major and minor ticks
    ax.tick_params(
        axis="x", which="both", rotation=45, pad=2, labelsize=FONTSIZE_TICK_LABEL
    )
    ax.tick_params(axis="y", which="both", pad=2, labelsize=FONTSIZE_TICK_LABEL)
    plt.setp(ax.get_xticklabels(), ha="right")
    plt.setp(ax.xaxis.get_minorticklabels(), ha="right")

    ax.set_xlabel("YLL value [USD/YLL]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("GHG price [USD/tCO2eq]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_title(title, fontsize=FONTSIZE_TITLE)

    cbar = plt.colorbar(im, ax=ax, orientation=cbar_orientation, pad=0.25)
    cbar.set_label(cbar_label, fontsize=FONTSIZE_CBAR_LABEL)
    cbar.ax.tick_params(labelsize=FONTSIZE_TICK_LABEL)

    # Add baseline marker on colorbar
    if baseline_value is not None:
        cbar_vmin = data.values.min()
        cbar_vmax = data.values.max()
        if cbar_vmin <= baseline_value <= cbar_vmax:
            if cbar_orientation == "horizontal":
                cbar.ax.axvline(
                    x=baseline_value, color="black", linewidth=1.5, linestyle="--"
                )
            else:
                cbar.ax.axhline(
                    y=baseline_value, color="black", linewidth=1.5, linestyle="--"
                )
                cbar.ax.plot(
                    1.0,
                    baseline_value,
                    marker="<",
                    color="black",
                    markersize=6,
                    transform=cbar.ax.get_yaxis_transform(),
                    clip_on=False,
                )

    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)

    # Make spines faint grey on plot and colorbar
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")
    for spine in cbar.ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")

    if panel_label is not None:
        ax.text(
            -0.05,
            1.12,
            panel_label,
            transform=ax.transAxes,
            fontsize=FONTSIZE_PANEL_LABEL,
            fontweight="bold",
            va="top",
            ha="left",
        )
